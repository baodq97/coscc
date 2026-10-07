"""Pausing every session for an update, and taking them up again. The pausing is `Sessions.suspend_all`'s and
the rows are the updater's; `Resume.resume_after_update` reads them at the next start, whatever version that is, and hands each to
the owner of its kind, which ends it as if nothing had come between.

Nothing here runs git on a session's worktree: not to read it, not to clean it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypedDict

from coscc.agent import transcript
from coscc.agent.sessions import Sessions
from coscc.config import Config
from coscc.kernel import Invalid, facts as facts_of
from coscc.runner.queue import Attempt, Holds, describe
from coscc.runner.steps import Steps
from coscc.store.db import Busy
from coscc.store.journal import BadRecord
from coscc.units import worktrees
from coscc.units.workspaces import Workspaces

# The kinds `Sessions.stream`'s `owner` names, and which of them hold a unit.
STEP_KINDS = ("step", "opening")
KINDS = STEP_KINDS + ("integrate", "estimate", "chat")

# How often `settle_after_suspend` looks again.
SETTLE_POLL = 0.1

# Tasks begun here. asyncio keeps only a weak reference to a task.
_TASKS: set[asyncio.Task] = set()


class Job(TypedDict):
    """What an Apply waits for: `kind` `integration`, `id`, where, the `stage`, and since when."""

    kind: str
    id: str
    workspace: str
    unit: str
    stage: str
    started: str


def resume_message(dropped: list[dict[str, Any]] | None) -> str:
    """What a session taken up again is told first, in English like every prompt."""
    if not dropped:
        return (
            "The app was just restarted to install an update. Nothing you were running was "
            "cut. Carry on with the work you were doing."
        )
    listed = "\n".join(f"- {d.get('name', 'tool')}: {d.get('input', '')}" for d in dropped)
    return (
        "The app was just restarted to install an update, and these tool calls were "
        "interrupted by that restart. The user did not refuse them, and none of them has a "
        f"result:\n{listed}\n\nCheck the state of the worktree first -- a command cut halfway "
        "may have left files half written -- then run again whichever of them still needs to "
        "run, and carry on with the work."
    )


def check(row: dict[str, Any]) -> tuple[str, list[str]]:
    """Why `row` cannot be taken up, or `""`, and the pieces before its safe point.

    The transcript must be where the CLI keeps it for that `cwd`: left to itself, the CLI
    takes a file of the same id from another project and writes into it.
    """
    if row.get("unresumable"):
        return str(row["unresumable"]), []
    cwd, sid = str(row.get("cwd") or ""), str(row.get("session_id") or "")
    if not cwd or not Path(cwd).is_dir():
        return f"the directory it ran in is gone: {cwd}", []
    path = transcript.path_for(cwd, sid)
    if not path.is_file():
        return f"its transcript is not in {path.parent}", []
    try:
        found = transcript.cut(path, int(row.get("boundary") or 0))
    except (transcript.Unreadable, OSError) as e:
        return f"its transcript could not be read: {e}", []
    if not row.get("safe_uuid"):
        return "its transcript holds no point to go on from", []
    return "", list(found["pieces"])


def moved_on(journal: Any, row: dict[str, Any]) -> str:
    """Why `row`'s unit went on without its session, or `""`: a `start` or
    an `end` of that unit written after the `suspend` row, as a rerun while the row waited
    for a start leaves. A session taken up then would write over newer work."""
    unit = str(row.get("unit") or "")
    if not unit:
        return ""
    try:
        rows = journal.records(
            str(row.get("workspace") or ""), unit, kinds=("start", "end", "suspend")
        )
    except Busy:
        return "the run log was busy, so whether the unit moved on is unknown"
    after = False
    for r in rows:
        if r.get("kind") == "suspend" and r.get("suspend_id") == row.get("suspend_id"):
            after = True
        elif after and r.get("kind") in ("start", "end"):
            return f"{unit} moved on after the update paused it: its {r.get('stage')} has a later {r['kind']} row"
    return ""


log = logging.getLogger(__name__)


def _spawn(coro: Any) -> asyncio.Task:
    task = asyncio.ensure_future(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


class Resume:
    """What is not a board step is taken up by its owner, handed in by kind: `takers` gives, of
    `integrate`, `estimate` and `chat`, what claims the owner's hold and returns the coroutine
    that runs it. `owner_refuses` is what an estimate or a chat turn refuses before its session,
    and `finish` what follows once every row is taken up: the merges left open, the autopilot."""

    def __init__(
        self,
        config: Config,
        ws: Workspaces,
        holds: Holds,
        sessions: Sessions,
        steps: Steps,
        *,
        takers: dict[str, Callable[[str, dict[str, Any]], Any]],
        refuse_updating: Callable[[], None],
        finish: Callable[[], Awaitable[None]],
    ) -> None:
        self.config = config
        self.ws = ws
        self.holds = holds
        self.sessions = sessions
        self.steps = steps
        self.takers = takers
        self.refuse_updating = refuse_updating
        self.finish = finish

    # -- pausing for an update ------------------------------------------------

    def update_waited(self) -> list[Job]:
        """What an Apply waits for: a mechanical integration and a screenshot retake.
        Gebo sessions, steps, estimates and chat are paused by `suspend_sessions`; what of
        them had no session open gets `settle_after_suspend`'s bounded wait."""
        jobs: list[Job] = []
        for row in self.holds.attempts.unfinished():
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
        return [r for r in self.holds.attempts.unfinished() if r["state"] != "queued"]

    # -- taking up again --------------------------------------------------------

    def _feature_refuses(self, owner: dict[str, Any]) -> str:
        """What the guards of the features say to a step taken up again, from its owner; `""` when
        none denies."""
        workspace, unit = str(owner.get("workspace_dir") or ""), str(owner.get("unit") or "")
        tree, scratch = str(owner.get("tree") or ""), owner.get("scratch")
        try:
            directory = self.ws.unit_dir(workspace, unit)
        except Invalid as e:
            return str(e)
        return self.steps.feature_refusal(
            facts_of(
                workspace=workspace,
                workspace_key=str(owner.get("workspace") or ""),
                unit=unit,
                agent=str(owner.get("stage") or ""),
                run=str(owner.get("run") or ""),
                cwd=str(scratch or tree),
                watch=tree if scratch else None,
                directory=directory,
                resumed=True,
            )
        )

    async def resume_after_update(self) -> list[dict[str, Any]]:
        """At start-up: each `suspend` row no start took up is taken up once, then the
        autopilot starts again. What happened to each row is returned.

        The `resume` row goes first, so a start that dies after it never takes the session up
        a second time. Every unit a step or an integration held is claimed again, with no
        `await` in between, before the autopilot is asked, so it cannot start a step on one.
        """
        # The pause is over once its rows are taken up, here after a failed hand-off; a new
        # process never had it.
        handoff = self.sessions.paused
        self.sessions.paused = False
        # `shutdown` closed the queue before a hand-off that failed; this process goes on.
        self.holds.attempts.closed = False
        await self._recover(handoff)
        journal = self.ws.journal()
        if journal is None:
            # No working folder: `suspend_sessions` wrote no row, so there is none to take up.
            self._end_unclaimed(handoff)
            await self.finish()
            return []
        said: list[dict[str, Any]] = []
        starts: list[Any] = []
        try:
            rows = journal.unresumed()
        except Busy:
            rows = []
        # Judged before any row is written, so the `end` of one that fails is not read as the
        # unit of another moving on.
        gone = {id(row): moved_on(journal, row) for row in rows}
        for row in rows:
            owner = row.get("owner") or {}
            kind = str(owner.get("kind") or "")
            problem, pieces = check(row)
            if not problem and kind not in KINDS:
                problem = f"no owner takes up a session of kind {kind!r}"
            problem = problem or gone[id(row)]
            if not problem and kind in STEP_KINDS + ("integrate",):
                # What the claim would refuse, asked before the `resume` row
                # so that row says what happened. Nothing awaits from here to the claim.
                key, unit = str(owner.get("workspace") or ""), str(owner.get("unit") or "")
                problem = self.steps.unadoptable(
                    "integration" if kind == "integrate" else "step", key, unit
                )
                if not problem and kind in STEP_KINDS:
                    problem = self._feature_refuses(owner)
            if not problem and kind in ("estimate", "chat"):
                problem = self._owner_refuses(kind, owner)
            try:
                journal.resumed(
                    str(row.get("workspace") or ""),
                    str(row.get("unit") or ""),
                    str(row.get("stage") or ""),
                    str(row.get("suspend_id") or ""),
                    by="app",
                    result="failed" if problem else "resumed",
                    # Every resume goes through `sessions._options`, which sets
                    # `snapshot` on the system prompt once `resume_at` is given.
                    **({"detail": problem} if problem else {"system_prompt": "snapshot"}),
                )
            except BadRecord, Busy:
                continue  # not taken: the next start sees it again
            record = {**row, "pieces": pieces, "message": resume_message(row.get("dropped"))}
            if not problem:
                # Its `suspend` row names the session, so `Sessions.known` lets it be resumed.
                try:
                    starts.append(self._take_up(kind, record))
                except Invalid as e:
                    problem = str(e)
            if problem:
                self._end_unresumed(journal, row, kind, problem)
            said.append(
                {
                    "suspend_id": row.get("suspend_id"),
                    "kind": kind,
                    "result": "failed" if problem else "resumed",
                    **({"detail": problem} if problem else {}),
                }
            )
        for start in starts:
            if start is not None:
                _spawn(start)
        self._end_unclaimed(handoff)
        # A merge asked for before the app went down is recorded before the
        # autopilot could ask for it again.
        await self.finish()
        return said

    def _owner_refuses(self, kind: str, owner: dict[str, Any]) -> str:
        """What an estimate or a chat turn refuses before its session, asked before the
        `resume` row as the step claim is, so that row says what happened. Each owner holds what it takes before its first `await`, and its task runs
        before the autopilot's, so nothing comes between this and that."""
        cwd, key = str(owner.get("workspace_dir") or ""), str(owner.get("workspace") or "")
        try:
            self.ws.check(cwd)
            self.refuse_updating()
        except Invalid as e:
            return str(e)
        held = self.holds.attempts.holding(key, "") if kind == "estimate" else None
        return describe("", held) if held is not None else ""

    def _live_here(self, row: Attempt, handoff: bool) -> bool:
        """Whether a task of this process still holds `row`, after a hand-off that failed: a
        step or integration in `Steps.tasks`, or any hold, review round or estimate, whose
        coroutine ends its own attempt."""
        return row["id"] in self.steps.tasks or (
            handoff and row["machine"] not in ("step", "integration")
        )

    async def _recover(self, handoff: bool = False) -> None:
        """At start-up, before Resume: what a process that went down left unfinished.
        `queued` stays queued; one a Stop reached ends `stopped`; `preparing` and `ending`
        end `interrupted`, the tree `preparing` left half made removed first; a hold, a
        review round or an estimate ends `interrupted`. A `running` step or integration
        waits for Resume, and `_end_unclaimed` ends it once Resume did not take it up."""
        for row in self.holds.attempts.unfinished():
            if row["state"] == "queued" or self._live_here(row, handoff):
                continue
            if row["stop_asked_at"]:
                outcome = "stopped"
            elif row["state"] == "running" and row["machine"] in ("step", "integration"):
                continue
            else:
                outcome = "interrupted"
            if row["state"] == "preparing":
                try:
                    await worktrees.discard_half(
                        row["workspace"], row["unit"], self.config.data_dir
                    )
                except Exception:
                    # A start-up is never stopped by this; the next click's `ensure` asks again.
                    log.exception("the half-made tree of %s was not removed", row["unit"])
            self.holds.attempts.move(row["id"], "ended", outcome)

    def _end_unclaimed(self, handoff: bool = False) -> None:
        """After Resume: a `running` attempt no task of this process holds ends `interrupted`,
        and the queue moves on."""
        for row in self.holds.attempts.unfinished():
            if row["state"] != "queued" and not self._live_here(row, handoff):
                self.holds.attempts.move(row["id"], "ended", "interrupted")
        self.holds.attempts.wake_all()

    def _end_unresumed(self, journal: Any, row: dict[str, Any], kind: str, problem: str) -> None:
        """The run ends `failed`: a step or integration waits for a rerun."""
        try:
            journal.finished(
                str(row.get("workspace") or ""),
                str(row.get("unit") or ""),
                str(row.get("stage") or ""),
                "failed",
                # The fields every run's `end` has (`run.ended`) that a run not taken up knows.
                agent=kind if kind not in STEP_KINDS else str(row.get("stage") or ""),
                status="failed",
                detail=f"not resumed after an update: {problem}",
                session_id=str(row.get("session_id") or ""),
                model=row.get("model"),
                **(
                    {"cost_usd": row["spent_usd"]}
                    if row.get("spent_usd") is not None
                    else {"cost_unknown": True}
                ),
            )
        except BadRecord, Busy:
            pass

    def _take_up(self, kind: str, record: dict[str, Any]) -> Any:
        """Claim what the owner of `kind` holds, now, and return the coroutine that runs it."""
        if kind in STEP_KINDS:
            self.steps.resume_step(record)  # its task is already made
            return None
        return self.takers[kind](str(record["owner"].get("workspace_dir") or ""), record)
