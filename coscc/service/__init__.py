"""The only place business logic lives.

The page and the JSON API translate requests into calls here and never decide anything.
Nothing here imports a web framework; `Invalid` is how this layer refuses.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from collections.abc import AsyncIterator, Callable
from typing import Any, Literal

from coscc.bus import Event
from coscc.config import Config
from coscc.agent.sessions import Sessions
from coscc.github.integrate import open_prs_once
from coscc.github.integration import Integration
from coscc.runner.queue import Attempt, Attempts, Holds
from coscc.runner.resume import Resume
from coscc.runner.steps import Steps
from coscc.update import updater as updater_mod

from coscc.service.activity import Activity
from coscc.service.agents import Agents
from coscc.leif.answers import Answers
from coscc.leif.autopilot import Autopilot, autopilot_values
from coscc.leif.chat import CHAT_TURNS, Chat
from coscc.service.backlog import Backlog
from coscc.service.models import Models
from coscc.service.release import Release
from coscc.store.journal import BadRecord
from coscc.store.db import Busy
from coscc.kernel import OWNER
from coscc.units.ideas import Ideas
from coscc.units.read import Board
from coscc.units.workspaces import Workspaces
from coscc.service.update import (
    SETTLE_POLL,
    as_invalid,
    refuse_mechanical_while_updating,
    refuse_while_updating,
    update_words,
)
from coscc.service.watch import Watch

log = logging.getLogger(__name__)

# How long `shutdown` waits for what it cancelled. Chosen, not measured: past it a step's
# thread is left running rather than an update held up.
SHUTDOWN_WITHIN = 10.0


@dataclass
class Service:
    config: Config
    sessions: Sessions

    def __post_init__(self) -> None:
        # Which workspaces there are, and where each keeps its units.
        self.ws = Workspaces(self.config, self.sessions)
        # One question, asked in two places. See `Sessions.membership`.
        self.sessions.membership = self.ws.is_member
        # Told of every step, integration and chat turn that ends.
        self.updater = updater_mod.Updater(self.config, self)
        self.bus = self.sessions.bus
        # What holds each unit now: its attempt, and the scheduler that launches them.
        self.attempts = Attempts(self.config.data_dir, self.bus, self._capacity)
        self.attempts.admitting = self._admitting
        self.holds = Holds(self.attempts)
        # The parts below `Service`, each given what it reads.
        self.agents = Agents(self.config, self.ws)
        self.models = Models(self.config, self.ws)
        self.activity = Activity(self.config, self.ws)
        self.chat = Chat(
            self.config,
            self.ws,
            self.sessions,
            lambda: refuse_while_updating(self.updater),
            self.models.model_for,
        )
        self.ideas = Ideas(self.config, self.ws)
        self.backlog = Backlog(
            self.config, self.ws, self.holds, self.sessions, self.updater, self.models, self.bus
        )
        self.release = Release(self.config, self.ws, self.updater)

        # An answer written and a step or an integration ended each schedule an autopilot
        # pass; the autopilot, built after them, is looked up when the event comes.
        self.answers = Answers(
            self.config, self.ws, self.holds, self.agents.agent, self.ideas, self.bus
        )
        self.steps = Steps(
            self.config,
            self.ws,
            self.holds,
            self.sessions,
            self.ideas,
            self.bus,
            agent_of=self.agents.agent,
            stage_config=self.models.stage_config,
            ci_red=self.models.ci_red,
            findings_added=self.models.findings_added,
            worktree=self.answers.worktree,
            append_to_answers=self.answers.append_to_answers,
            ingest=self.answers.ingest,
            post_new_rounds=self.answers.post_new_rounds,
            sync_pr=self.answers.sync_pr,
            refuse_updating=lambda: refuse_while_updating(self.updater),
            refuse_mechanical=lambda: refuse_mechanical_while_updating(self.updater),
            identity=self._identity,
        )
        # Above `runner`: a unit's pull request, which runs its attempts on the steps.
        self.integration = Integration(
            self.config,
            self.ws,
            self.holds,
            self.sessions,
            self.steps,
            self.bus,
            agent_overrides=lambda: self.agents.agent_overrides()[0],
            config_overrides=self.agents.config_overrides,
            config_for=self.models.config_for,
        )
        self.watch = Watch(self.config, self.ws, self.steps.recorders)
        self.boards = Board(
            self.config,
            self.ws,
            self.bus,
            self.attempts.unfinished,
            lambda: self.agents.agent_overrides()[0],
            lambda cwd: open_prs_once(cwd)(),
            self._attach,
        )
        self.release.details.changed = self.boards.changed
        self.autopilot = Autopilot(
            self.config,
            self.ws,
            self.holds,
            lambda: self.agents.config_overrides()[0]["budget"],
            lambda: self.agents.agent_overrides()[0],
            self.steps,
            self.integration,
            self.boards,
        )
        # Who listens to what. The updater hears every ending; the autopilot hears those that
        # free a unit or leave a person's answer, and not a step that ended because the app
        # is going down.
        for name in (
            "step.ended",
            "step.refused",
            "integration.ended",
            "integration.refused",
            "integration.escalated",
            "retake.ended",
            "estimate.ended",
            "estimate.refused",
            "chat-turn.ended",
        ):
            self.bus.subscribe(name, lambda _: self.updater.job_ended())
        for name in (
            "step.ended",
            "step.refused",
            "integration.ended",
            "integration.refused",
            "answer.written",
            "shortlist.saved",
            "hold.moved",
        ):
            self.bus.subscribe(name, self._wake_autopilot)
        self.resume = Resume(
            self.config,
            self.ws,
            self.holds,
            self.sessions,
            self.steps,
            takers={
                "integrate": lambda _cwd, record: self.integration.resume(record),
                "estimate": lambda cwd, record: _drain(
                    self.backlog.propose_estimates(cwd, resume=record)
                ),
                "chat": lambda cwd, record: self._resume_chat(cwd, record),
            },
            refuse_updating=lambda: refuse_while_updating(self.updater),
            chat_turns=CHAT_TURNS,
            finish=self._resumed,
        )

    def _identity(self) -> dict[str, str]:
        """The running build's version and commit, for a step's `start` row.

        `Updater.me` is `update.identity`, computed once and kept. Anything failing is two
        empty strings; it never stops a step.
        """
        try:
            me = self.updater.me()
            return {"version": str(me.get("version") or ""), "commit": str(me.get("commit") or "")}
        except Exception:
            # A record field, never a reason to refuse a step.
            log.exception("the version of the app could not be read")
            return {"version": "", "commit": ""}

    async def _resumed(self) -> None:
        """Once every row is taken up: a merge asked for before the app went down is recorded
        before the autopilot could ask for it again."""
        await self.integration.reconcile_prs()
        self.autopilot.resume()

    async def _resume_chat(self, cwd: str, record: dict[str, Any]) -> None:
        """Nobody is reading this turn now; its reply is in the session, and its `chat`
        row is written as any turn's is."""
        async for _ in self.chat.stream(
            cwd,
            str(record.get("message") or ""),
            str(record.get("session_id") or ""),
            resume=record,
        ):
            pass

    def _capacity(self, workspace: str, slot: str) -> int:
        """Agent sessions at once: the workspace's `max_parallel`. Heavy work: one."""
        if slot == "agent":
            return int(autopilot_values(self.config, workspace)["max_parallel"])
        return 1

    def _admitting(self) -> bool:
        """No queued attempt is moved on from the press of Apply until the update goes no
        further: what began then would be refused `updating`, or cut by the hand-off."""
        return not self.updater.window and self.updater.state not in ("pending", "applying")

    def update_over(self) -> None:
        """Told by the updater once a cancel or a failure left it idle: the queue moves on."""
        self.attempts.wake_all()

    def _wake_autopilot(self, event: Event) -> None:
        if not event.going_down:
            self.autopilot.nudge(event.workspace)

    async def _attach(self, cwd, data, journal, key, prs, fresh) -> Callable[[], None]:
        """What the board read adds from above `units`: each unit's integration and the release
        block. Returns what starts the CI asks that found no answer, which the read calls last."""
        asks = await self.integration.attach_integration(
            cwd, data["units"], journal, key, prs, fresh
        )
        data["release"] = await self.release.attach_release(
            cwd, data["units"], journal, key, prs, fresh
        )
        return lambda: self.integration.ask_ci(
            asks, ended=lambda tree: self.boards.changed((tree,))
        )

    async def board(
        self, cwd: str, which: Literal["new", "held", "next"] = "new"
    ) -> dict[str, Any]:
        """The board of `cwd` (`Board.get`) with what the autopilot shows on it."""
        data = await self.boards.get(cwd, which)
        self.autopilot.show(self.ws.key(cwd), data)
        return data

    # -- updating the app -----------------------------------------------------
    #
    # Every decision is `Updater`'s; these translate its refusals into `Invalid`, so a route
    # maps one exception type.

    def _update_waited(self) -> list[dict[str, Any]]:
        """What an Apply waits for: a mechanical integration and a screenshot retake.
        Gebo sessions, steps, estimates and chat are paused by `suspend_sessions`; what of
        them had no session open gets `settle_after_suspend`'s bounded wait."""
        jobs: list[dict[str, Any]] = []
        for row in self.attempts.unfinished():
            if (
                row["machine"] == "integration"
                and row["state"] != "queued"
                and row["road"] != "gebo"
            ):
                jobs.append(
                    {
                        "kind": "integration",
                        "id": f"integration:{row['workspace']}:{row['unit']}",
                        "workspace": row["workspace"],
                        "unit": row["unit"],
                        "stage": "integrate",
                        "started": row["since"],
                    }
                )
        for entry in self.steps.retakes.values():
            # No Stop reaches it, and `retake.take` puts `.screens/` back only if it gets to.
            jobs.append(
                {
                    "kind": "integration",
                    "id": f"screens:{entry['workspace']}:{entry['unit']}",
                    "workspace": entry["workspace"],
                    "unit": entry["unit"],
                    "stage": "screens",
                    "started": entry["started"],
                }
            )
        return jobs

    async def suspend_sessions(self, by: str) -> list[dict[str, Any]]:
        """Every session paused, and one `suspend` row written for each, before this process
        hands off. With no working folder there is nowhere to write one, and the sessions end
        as a restart ends them."""
        records = await self.sessions.suspend_all()
        journal = self.ws.journal()
        written: list[dict[str, Any]] = []
        for record in records:
            owner = record.get("owner") or {}
            if journal is None:
                break
            if not owner.get("kind"):
                continue  # a caller that named no owner: nothing could take it up again
            try:
                written.append(
                    journal.suspended(
                        str(owner.get("workspace") or ""),
                        str(owner.get("unit") or ""),
                        str(owner.get("stage") or ""),
                        by=by,
                        **record,
                    )
                )
            except BadRecord, Busy:
                continue
        return written

    async def settle_after_suspend(self, within: float) -> list[dict[str, Any]]:
        """`suspend_sessions` pauses only what had a session open: a step writing its round to
        the PR or syncing `pr.md` after its `end`, or a Gebo reading the PR's head after its
        session, had none. Each gets `within` seconds to finish (no new session may open
        meanwhile) and what still runs is returned, for the updater to name in a `cut` row
        before `shutdown` cancels it. A step in its attempt's `ending`, writing its `questions`
        or `ship` record, counts as `after-end` until its task ends.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + within
        while (self._launched() or self.holds.finishing) and loop.time() < deadline:
            await asyncio.sleep(SETTLE_POLL)
        left = [
            {
                "kind": (row["road"] or "rebase")
                if row["machine"] == "integration"
                else row["machine"],
                "workspace": row["workspace"],
                "unit": row["unit"],
                "stage": row["stage"],
                "started": row["since"],
            }
            for row in self._launched()
            if row["id"] not in self.holds.finishing
        ]
        left += [{**entry, "kind": "after-end"} for entry, _task in self.holds.finishing.values()]
        return left

    def _launched(self) -> list[Attempt]:
        """The attempts past `queued` and not ended, of every workspace."""
        return [r for r in self.attempts.unfinished() if r["state"] != "queued"]

    def update_status(self) -> dict[str, Any]:
        """`Updater.status`, unchanged, with `line`, `local_line` and `actions`."""
        status = self.updater.status()
        return {**status, **update_words(status)}

    async def update_apply(self, channel: str, by: str) -> dict[str, Any]:
        try:
            return await self.updater.apply(channel, (by or "").strip() or OWNER)
        except updater_mod.Refused as e:
            raise as_invalid(e) from e

    def update_cancel(self, by: str) -> dict[str, Any]:
        try:
            return self.updater.cancel((by or "").strip() or OWNER)
        except updater_mod.Refused as e:
            raise as_invalid(e) from e

    def update_build_local(self, by: str) -> dict[str, Any]:
        try:
            return self.updater.build_local((by or "").strip() or OWNER)
        except updater_mod.Refused as e:
            raise as_invalid(e) from e

    def _asks(self) -> list[tuple[str, asyncio.Task]]:
        """The background `gh` asks running now: CI, the board's and the release panel's."""
        return [
            *((f"CI ask of {u} in {ws}", t) for (ws, u), t in self.integration.ci_asks.items()),
            *((f"gh ask for {' '.join(k)}", t) for k, t in self.boards.prs.asks.items()),
            *((f"release ask for {' '.join(k)}", t) for k, t in self.release.details.asks.items()),
        ]

    async def shutdown(self) -> None:
        """Cancel every autopilot pass, step, board read and background `gh` ask still running,
        let every tree removal end as it would, and wait for all of them, `SHUTDOWN_WITHIN`
        seconds at most from the call. An ask a request begins meanwhile is cancelled and
        waited for too. Once this returns nothing they started still writes, unless it
        outlived the deadline: each such one is logged by name.

        No `end` is written: a step with no `end` is what an app that went down mid-step looks like.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SHUTDOWN_WITHIN
        # The queue first: an integration waited for below frees its slot, and what is queued
        # stays queued for the next start rather than begin in a process going down.
        self.attempts.closed = True
        # The autopilot next, so no pass starts a step while the rest go down. A pass may be
        # in a board read's thread, so it is waited for too; taken before `stop` drops it.
        autopilot = [
            *((f"autopilot of {k}", t) for k, t in self.autopilot.tasks.items()),
            *((f"pull request reader of {k}", t) for k, t in self.autopilot.pr_readers.items()),
            *(("autopilot pass", t) for t in self.autopilot.pending),
        ]
        for key in list(self.autopilot.tasks):
            self.autopilot.stop(key)
        for t in list(self.autopilot.pending):
            t.cancel()
        # A CI ask and a held `gh` answer hold nothing worth keeping, but their `gh` is reaped.
        cancelled = [*autopilot, *self._asks()]
        # An integration is waited for, not cancelled: it stops between two `git`s or not at all.
        integrations = [
            (f"integration of {r.unit} in {r.workspace}", r.task)
            for r in self.steps.tasks.values()
            if r.stage == "integrate" and r.task is not None and not r.task.done()
        ]
        steps = [
            (f"{r.stage} step of {r.unit} in {r.workspace}", r.task)
            for r in self.steps.tasks.values()
            if r.stage != "integrate" and r.task is not None and not r.task.done()
        ]
        # A step's task past `steps.release`, still in its `after_end`.
        steps += [
            (f"after-end of {entry['unit']} in {entry['workspace']}", t)
            for entry, t in self.holds.finishing.values()
            if not t.done() and t not in [s for _label, s in steps]
        ]
        cancelled += steps
        for _label, t in cancelled:
            t.cancel()
        # Board reads are cancelled there, and tree removals left to end.
        waited = cancelled + integrations + await self.boards.stop()
        while True:
            left = {t for _label, t in waited if not t.done()}
            if left:
                await asyncio.wait(left, timeout=max(0.0, deadline - loop.time()))
            # Only the board has a door: an ask a request began meanwhile is cancelled here.
            seen = {t for _label, t in waited}
            late = [(label, t) for label, t in self._asks() if t not in seen and not t.done()]
            for _label, t in late:
                t.cancel()
            waited += late
            if not late or loop.time() >= deadline:
                break
        late_integrations = [t for _label, t in integrations if not t.done()]
        for t in late_integrations:
            # Past the deadline: its `git` is killed rather than an update held up.
            t.cancel()
        if late_integrations:
            await asyncio.wait(late_integrations, timeout=1.0)
        for label, t in waited:
            if not t.done():
                log.warning(
                    "shutdown returns with the %s still running after %gs", label, SHUTDOWN_WITHIN
                )


async def _drain(agen: AsyncIterator[Any]) -> None:
    async for _ in agen:
        pass
