"""The only place business logic lives.

The page and the JSON API translate requests into calls here and never decide anything.
Nothing here imports a web framework; `Invalid` is how this layer refuses.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Literal

from coscc.bus import Event
from coscc.config import Config
from coscc.agent.sessions import Sessions
from coscc.update import updater as updater_mod

from coscc.service.activity import Activity
from coscc.service.agents import Agents
from coscc.service.answers import Answers
from coscc.service.attempts import Attempt, Attempts
from coscc.service.autopilot import Autopilot, autopilot_values
from coscc.service.backlog import Backlog
from coscc.service.board import Board
from coscc.service.common import Holds
from coscc.service.ideas import Ideas
from coscc.service.models import Models
from coscc.service.release import Release
from coscc.service.resume import Resume
from coscc.service.sessions import Chat
from coscc.service.steps import Steps
from coscc.runlog.journal import BadRecord
from coscc.data import Busy
from coscc.kernel import OWNER
from coscc.service.update import SETTLE_POLL, as_invalid, update_words
from coscc.service.watch import Watch
from coscc.service.workspaces import Workspaces

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
        self.chat = Chat(self.config, self.ws, self.sessions, self.updater, self.models)
        self.ideas = Ideas(self.config, self.ws)
        self.backlog = Backlog(
            self.config, self.ws, self.holds, self.sessions, self.updater, self.models, self.bus
        )
        self.release = Release(self.config, self.ws, self.updater)

        # An answer written and a step or an integration ended each schedule an autopilot
        # pass; the autopilot, built after them, is looked up when the event comes.
        self.answers = Answers(
            self.config, self.ws, self.holds, self.agents, self.backlog, self.ideas, self.bus
        )
        self.steps = Steps(
            self.config,
            self.ws,
            self.holds,
            self.sessions,
            self.updater,
            self.agents,
            self.models,
            self.ideas,
            self.answers,
            self.bus,
        )
        self.watch = Watch(self.config, self.ws, self.steps.recorders)
        self.boards = Board(self.config, self.ws, self.holds, self.agents, self.release, self.steps)
        self.autopilot = Autopilot(
            self.config, self.ws, self.holds, self.agents, self.steps, self.boards
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
        for name in (
            "step.ended",
            "step.refused",
            "integration.ended",
            "integration.refused",
            "answer.written",
            "hold.moved",
            "mode.set",
        ):
            self.bus.subscribe(name, self._read_board_again)
        self.resume = Resume(
            self.config,
            self.ws,
            self.holds,
            self.sessions,
            self.updater,
            self.agents,
            self.models,
            self.backlog,
            self.chat,
            self.steps,
            self.autopilot,
            self.bus,
        )

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

    def _read_board_again(self, event: Event) -> None:
        """A change the app made reads that workspace's board again, once one was read."""
        if event.going_down or event.workspace not in self.boards.held:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self.boards.refresh(self.boards.held[event.workspace]["cwd"], again=True)

    async def board(
        self, cwd: str, which: Literal["new", "held", "next"] = "new"
    ) -> dict[str, Any]:
        """The board of `cwd` as `boards.read` returns it, with what the autopilot shows on it.

        `new` waits for a read begun after this call, which asks `gh` anew. `held` answers with the last read and
        starts the next, so it waits only while nothing was read yet: what the page and
        `/api/board` ask. `next` waits for the next read to end and starts none: what a tab
        that shows the board waits on. Every workspace has one read running at most.
        """
        self.ws.check(cwd)
        key = self.ws.key(cwd)
        if which == "next":
            await self.boards.next_read(cwd)
            data = self.boards.held[key]["data"]
        else:
            kept = self.boards.held.get(key) if which == "held" else None
            task = self.boards.refresh(cwd, again=which == "new", fresh=which == "new")
            data = kept["data"] if kept is not None else await asyncio.shield(task)
        self.autopilot.show(key, data)
        return data

    async def warm_boards(self) -> None:
        """Every listed workspace's board read once, so the first page opened finds it held. A
        first read waits on its workspace's open pull requests, so the board it holds has them."""
        cwds = [w["path"] for w in self.ws.all()["workspaces"] if not w.get("missing")]
        await asyncio.gather(*(self.boards.refresh(c) for c in cwds), return_exceptions=True)

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
            *((f"CI ask of {u} in {ws}", t) for (ws, u), t in self.steps.ci_asks.items()),
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
