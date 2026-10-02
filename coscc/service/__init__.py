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
from coscc.service.autopilot import Autopilot
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
from coscc.service.common import OWNER
from coscc.service.update import SETTLE_POLL, as_invalid, update_words
from coscc.service.watch import Watch
from coscc.service.workspaces import Workspaces

log = logging.getLogger(__name__)


@dataclass
class Service:
    config: Config
    sessions: Sessions

    def __post_init__(self) -> None:
        # Which workspaces there are, and where each keeps its units.
        self.ws = Workspaces(self.config, self.sessions)
        # What holds each unit now, and what the board lists as running.
        self.holds = Holds()
        # One question, asked in two places. See `Sessions.membership`.
        self.sessions.membership = self.ws.is_member
        # Told of every step, integration and chat turn that ends.
        self.updater = updater_mod.Updater(self.config, self)
        self.bus = self.sessions.bus
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
            "step.released",
            "integration.ended",
            "integration.escalated",
            "retake.ended",
            "estimate.ended",
            "chat-turn.ended",
        ):
            self.bus.subscribe(name, lambda _: self.updater.job_ended())
        for name in (
            "step.ended",
            "integration.ended",
            "answer.written",
            "shortlist.saved",
            "hold.moved",
        ):
            self.bus.subscribe(name, self._wake_autopilot)
        for name in ("step.ended", "integration.ended", "answer.written", "hold.moved", "mode.set"):
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
        for entry in self.holds.running.values():
            if entry["stage"] == "integrate" and entry.get("kind") != "gebo":
                jobs.append(
                    {
                        "kind": "integration",
                        "id": f"integration:{entry['workspace']}:{entry['unit']}",
                        "workspace": entry["workspace"],
                        "unit": entry["unit"],
                        "stage": "integrate",
                        "started": entry["started"],
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
        before `shutdown` cancels it. A step past its `holds.running` entry, writing its `questions`
        or `ship` record, counts as `after-end` until its task ends.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + within
        while (self.holds.running or self.holds.finishing) and loop.time() < deadline:
            await asyncio.sleep(SETTLE_POLL)
        left = list(self.holds.running.values())
        left += [{**entry, "kind": "after-end"} for entry, _task in self.holds.finishing.values()]
        return [
            {k: entry.get(k) for k in ("kind", "workspace", "unit", "stage", "started")}
            for entry in left
        ]

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

    async def shutdown(self) -> None:
        """Cancel every step, board read and background `gh` ask still running, let every tree
        removal end as it would, and wait for all of them, 10 seconds at most from the call.
        Once this returns nothing they started still writes, unless it outlived the 10
        seconds: each such one is logged by name.

        No `end` is written: a step with no `end` is what an app that went down mid-step looks like.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 10
        # The autopilot first, so no pass starts a step while the rest go down.
        for key in list(self.autopilot.tasks):
            self.autopilot.stop(key)
        for t in list(self.autopilot.pending):
            t.cancel()
        # A CI ask and a held `gh` answer hold nothing worth keeping, but their `gh` is reaped.
        cancelled = [
            *((f"CI ask of {u} in {ws}", t) for (ws, u), t in self.steps.ci_asks.items()),
            *((f"gh ask for {' '.join(k)}", t) for k, t in self.boards.prs.asks.items()),
            *((f"release ask for {' '.join(k)}", t) for k, t in self.release.details.asks.items()),
        ]
        steps = [
            (f"{r.stage} step of {r.unit} in {r.workspace}", r.task)
            for r in self.steps.registry.all()
            if r.task is not None and not r.task.done()
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
        waited = cancelled + await self.boards.stop()
        if not waited:
            return
        _done, pending = await asyncio.wait(
            {t for _label, t in waited}, timeout=max(0.0, deadline - loop.time())
        )
        for label, t in waited:
            if t in pending:
                log.warning("shutdown returns with the %s still running after 10s", label)
