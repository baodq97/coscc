"""Taking up again every session an update paused (`0138` R7-R9, R15).

Split like the other mixins of `coscc/service/__init__.py`, whose `Service` inherits it; a
mixin with no fields. The pausing is `Sessions.suspend_all`'s and the rows are the
updater's; this reads them at the next start, whatever version that is, and hands each to
the owner of its kind, which ends it as if nothing had come between.

Nothing here runs git on a session's worktree (R9): not to read it, not to clean it.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from coscc.agent import transcript
from coscc.agent import steps as steps_mod
from coscc.data import Data
from coscc.runlog import events
from coscc.runlog.journal import BadRecord, Busy
from coscc.service.common import Invalid
from coscc.service.sessions import CHAT_TURNS

# The kinds `Sessions.stream`'s `owner` names, and which of them hold a unit.
STEP_KINDS = ("step", "opening", "closing")
KINDS = STEP_KINDS + ("integrate", "estimate", "precedent", "chat")

# Tasks begun here. asyncio keeps only a weak reference to a task.
_TASKS: set[asyncio.Task] = set()


def resume_message(dropped: list[dict[str, Any]] | None) -> str:
    """R7. What a session taken up again is told first, in English like every prompt."""
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


async def nothing() -> AsyncIterator[tuple[str, Any]]:
    """A session that is not opened: its ceiling was used up before the update (R10)."""
    return
    yield


def resume_kwargs(resume: dict[str, Any] | None, grant: Any, prompt: str) -> dict[str, Any]:
    """The prompt, the session id and the ceilings of one tool-less session, taken up again
    from `resume` or not. `used_up` is there only when nothing is left to run with."""
    if resume is None:
        return {"text": prompt, "session_id": None, "max_turns": grant.max_turns,
                "max_budget_usd": grant.max_budget_usd}
    turns, budget, used_up = transcript.ceilings_left(grant.max_turns, grant.max_budget_usd, resume)
    return {
        "text": str(resume.get("message") or ""), "session_id": resume.get("session_id") or None,
        "max_turns": turns, "max_budget_usd": budget, "resume_at": resume.get("safe_uuid"),
        **({"used_up": used_up} if used_up else {}),
    }


def check(row: dict[str, Any]) -> tuple[str, list[str]]:
    """R8. Why `row` cannot be taken up, or `""`, and the pieces before its safe point.

    The transcript must be where the CLI keeps it for that `cwd`: left to itself, the CLI
    takes a file of the same id from another project and writes into it (`spike.md ## U5`).
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
    """Review round 1, F3. Why `row`'s unit went on without its session, or `""`: a `start` or
    an `end` of that unit written after the `suspend` row, as a rerun while the row waited
    for a start leaves. A session taken up then would write over newer work."""
    unit = str(row.get("unit") or "")
    if not unit:
        return ""
    try:
        rows = journal.records(str(row.get("workspace") or ""), unit, kinds=("start", "end", "suspend"))
    except Busy:
        return "the run log was busy, so whether the unit moved on is unknown"
    after = False
    for r in rows:
        if r.get("kind") == "suspend" and r.get("suspend_id") == row.get("suspend_id"):
            after = True
        elif after and r.get("kind") in ("start", "end"):
            return f"{unit} moved on after the update paused it: its {r.get('stage')} has a later {r['kind']} row"
    return ""


def _spawn(coro: Any) -> asyncio.Task:
    task = asyncio.ensure_future(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


class ResumeMixin:

    async def resume_after_update(self) -> list[dict[str, Any]]:
        """R7. At start-up: each `suspend` row no start took up is taken up once, then the
        autopilot starts again. What happened to each row is returned.

        The `resume` row goes first, so a start that dies after it never takes the session up
        a second time. Every unit a step or an integration held is claimed again, with no
        `await` in between, before the autopilot is asked, so it cannot start a step on one.
        """
        # Review round 2, F6: the pause is over once its rows are taken up -- here after a
        # failed hand-off; a new process never had it.
        self.sessions.paused = False
        journal = self._journal()
        said: list[dict[str, Any]] = []
        starts: list[Any] = []
        try:
            rows = journal.unresumed() if journal is not None else []
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
                # Review round 1, F4: what the claim would refuse, asked before the `resume` row
                # so that row says what happened. Nothing awaits from here to the claim.
                key, unit = str(owner.get("workspace") or ""), str(owner.get("unit") or "")
                held = self.steps.get(key, unit)
                problem = self._busy(key, unit) or (
                    steps_mod.describe(unit, steps_mod.Mark("step", held.stage, "running", held.started_at))
                    if held is not None else ""
                )
            if not problem and kind in ("estimate", "precedent", "chat"):
                problem = self._owner_refuses(kind, owner)
            if not problem and kind == "chat":
                # Review round 1, F5: R10 for a chat turn, said here so its `resume` row does.
                used_up = transcript.ceilings_left(CHAT_TURNS, None, row)[2]
                if used_up:
                    problem = f"its ceiling was used up before the update: {used_up}"
            try:
                journal.resumed(
                    str(row.get("workspace") or ""), str(row.get("unit") or ""), str(row.get("stage") or ""),
                    str(row.get("suspend_id") or ""), by="app", result="failed" if problem else "resumed",
                    # `0139` R15: every resume goes through `sessions._options`, which sets
                    # `snapshot` on the system prompt once `resume_at` is given.
                    **({"detail": problem} if problem else {"system_prompt": "snapshot"}),
                )
            except (BadRecord, Busy):
                continue  # not taken: the next start sees it again
            record = {**row, "pieces": pieces, "message": resume_message(row.get("dropped"))}
            if not problem:
                self.sessions.adopt(str(row.get("session_id") or ""))
                try:
                    starts.append(self._take_up(kind, record))
                except Invalid as e:
                    problem = str(e)
            if problem:
                self._end_unresumed(journal, row, kind, problem)
            said.append({"suspend_id": row.get("suspend_id"), "kind": kind, "result": "failed" if problem else "resumed",
                         **({"detail": problem} if problem else {})})
        for start in starts:
            if start is not None:
                _spawn(start)
        # `0136` R13: a merge asked for before the app went down is recorded before the
        # autopilot could ask for it again.
        await self.reconcile_prs()
        self.autopilot_resume()
        return said

    def _owner_refuses(self, kind: str, owner: dict[str, Any]) -> str:
        """Review round 2, F7. What an estimate, Jera or a chat turn refuses before its
        session, asked before the `resume` row as F4's claim is, so that row says what
        happened. Each owner holds what it takes before its first `await`, and its task runs
        before the autopilot's, so nothing comes between this and that."""
        cwd, key, unit = str(owner.get("workspace_dir") or ""), str(owner.get("workspace") or ""), str(owner.get("unit") or "")
        try:
            self._workspace_or_refuse(cwd)
            self._refuse_while_updating()
        except Invalid as e:
            return str(e)
        if kind == "precedent":
            return self._busy(key, unit)
        held = self._active.get((key, "")) if kind == "estimate" else None
        if held is not None:
            return f"a proposal for this workspace is already running since {held.started_at}; wait for it to end"
        return ""

    def _end_unresumed(self, journal: Any, row: dict[str, Any], kind: str, problem: str) -> None:
        """R8. The step, integration, estimate or Jera ends `failed` and waits for a rerun; a
        chat turn has no `start`, and only its `resume` row says it failed."""
        if kind == "chat":
            return
        try:
            journal.finished(
                str(row.get("workspace") or ""), str(row.get("unit") or ""), str(row.get("stage") or ""),
                "failed", detail=f"not resumed after an update: {problem}",
                session_id=str(row.get("session_id") or ""),
                **({"cost_usd": row["spent_usd"]} if row.get("spent_usd") is not None else {"cost_unknown": True}),
            )
        except (BadRecord, Busy):
            pass

    def _take_up(self, kind: str, record: dict[str, Any]) -> Any:
        """Claim what the owner of `kind` holds, now, and return the coroutine that runs it."""
        owner = record["owner"]
        cwd = str(owner.get("workspace_dir") or "")
        if kind in STEP_KINDS:
            self.resume_step(record)  # its task is already made
            return None
        if kind == "integrate":
            return self.resume_integration(record)
        if kind == "estimate":
            return _drain(self.propose_estimates(cwd, resume=record))
        if kind == "precedent":
            return self.precedent(cwd, str(owner.get("unit") or ""), str(owner.get("started_by") or "person"),
                                  resume=record)
        return self._resume_chat(cwd, record)

    def resume_step(self, record: dict[str, Any]) -> steps_mod.Running:
        """A board step, as `run_step` hands one to `_drive`: the unit claimed, a new
        recorder and `run`, and `Runner.run` with the row instead of a prompt. Synchronous up
        to the task, so the unit is held when this returns."""
        from coscc.runner import Runner

        owner = record["owner"]
        key, cwd = str(owner["workspace"]), str(owner["workspace_dir"])
        unit, stage, artifact = str(owner["unit"]), str(owner["stage"]), str(owner["artifact"])
        journal = self._journal()
        directory = self._unit_dir(cwd, unit)
        mark = self._take(key, unit, "step", stage)
        try:
            running = self.steps.claim(key, unit, stage, started_at=mark.started_at)
        except steps_mod.Busy as e:
            self._release(key, unit, mark)
            raise Invalid(str(e)) from e
        mark.phase = "running"
        rid = self._mark_running(key, unit, stage, "step")
        run = uuid.uuid4().hex
        recorder = events.Recorder(run, Data(self.config.data_dir), str(journal.working_dir), key, unit, stage)
        running.run, running.handle.recorder = run, recorder
        self._recorders[run] = recorder
        self._running[rid]["run"] = run
        rounds = set(owner["rounds_before"]) if owner.get("rounds_before") is not None else None
        end_fields = None
        if rounds is not None:
            async def end_fields() -> dict[str, Any]:
                return await self._findings_added(cwd, unit, rounds)
        extra = {k: owner.get(k) for k in (
            "workspace_dir", "rounds_before", "pr_before", "tree", "watch", "scratch", "read_also")}
        kwargs: dict[str, Any] = dict(
            workspace=cwd, directory=directory, journal_key=key, unit=unit, stage=stage,
            artifact=artifact, stages=[], mode="manual", cwd=str(record.get("cwd") or cwd),
            model=record.get("model"), effort=owner.get("effort"), label=owner.get("label"),
            agent=self._agent(stage), end_fields=end_fields, pr_before=owner.get("pr_before"),
            read_also=tuple(owner.get("read_also") or ()), resume=record, owner_extra=extra,
            **({"watch": owner["watch"]} if owner.get("watch") else {}),
        )
        tree = {"path": str(record.get("cwd") or "")} if owner.get("tree") else None
        scratch = Path(owner["scratch"]) if owner.get("scratch") else None
        running.task = asyncio.create_task(self._drive(
            running, mark, Runner(self.sessions, journal, app=self._app_identity()), cwd, unit, stage,
            artifact, directory, tree, None, rounds, rid, scratch, kwargs, resumed=True,
        ))
        running.task.add_done_callback(lambda _task: self._never_driven(running, mark, rid))
        return running

    def resume_integration(self, record: dict[str, Any]) -> Any:
        """Gebo, with the lease, the grant and the press's facts its owner kept. The unit is
        claimed now; the returned coroutine runs the session and what follows it."""
        owner = record["owner"]
        key, cwd, unit = str(owner["workspace"]), str(owner["workspace_dir"]), str(owner["unit"])
        mark = self._take(key, unit, "integrate")
        rid = self._mark_running(key, unit, "integrate", "gebo")
        journal = self._journal()

        def write(rec: dict[str, Any]) -> dict[str, Any]:
            try:
                return journal.append(rec)
            except (BadRecord, Busy):
                return rec

        async def go() -> None:
            try:
                async for _ in self._integrate_gebo(
                    cwd, key, unit, None, None, None, None, int(owner["pr"]), Path(owner["tree"]),
                    str(owner["branch"]), str(owner["head_before"]), str(owner["origin_sha"]), journal,
                    write, dict(owner.get("seen") or {}), owner.get("refused_update"),
                    completion=owner.get("completion"), resume=record,
                ):
                    pass
            finally:
                self._release(key, unit, mark)
                self._running.pop(rid, None)
                self.updater.job_ended()
                self._autopilot_nudge(key)

        return go()

    async def _resume_chat(self, cwd: str, record: dict[str, Any]) -> None:
        """C11: nobody is reading this turn now; its reply is in the session, and its `chat`
        row is written as any turn's is."""
        async for _ in self.stream(cwd, str(record.get("message") or ""), str(record.get("session_id") or ""),
                                   resume=record):
            pass


async def _drain(agen: AsyncIterator[Any]) -> None:
    async for _ in agen:
        pass
