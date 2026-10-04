"""Taking up again every session an update paused. The pausing is `Sessions.suspend_all`'s and the rows are the
updater's; this reads them at the next start, whatever version that is, and hands each to
the owner of its kind, which ends it as if nothing had come between.

Nothing here runs git on a session's worktree: not to read it, not to clean it.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

from coscc.agent import transcript
from coscc.agent import steps as steps_mod
from coscc.data import Data, now as _now
from coscc.kernel import Invalid, facts as facts_of
from coscc.runlog import events
from coscc.runlog.journal import BadRecord
from coscc.runner.step import Runner
from coscc.data import Busy
from coscc.service.update import refuse_while_updating
from coscc.service.attempts import Attempt, describe
from coscc.units import worktrees
from coscc.service.sessions import CHAT_TURNS
from coscc.bus import Bus
from coscc.config import Config
from coscc.agent.sessions import Sessions
from coscc.update.updater import Updater

if TYPE_CHECKING:
    # `backlog` takes estimates up again with the functions below, so the parts are types only.
    from coscc.service.agents import Agents
    from coscc.service.autopilot import Autopilot
    from coscc.service.backlog import Backlog
    from coscc.service.common import Holds
    from coscc.service.models import Models
    from coscc.service.sessions import Chat
    from coscc.service.steps import Steps
    from coscc.service.workspaces import Workspaces

# The kinds `Sessions.stream`'s `owner` names, and which of them hold a unit.
STEP_KINDS = ("step", "opening", "closing")
KINDS = STEP_KINDS + ("integrate", "estimate", "chat")

# Tasks begun here. asyncio keeps only a weak reference to a task.
_TASKS: set[asyncio.Task] = set()


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


async def nothing() -> AsyncIterator[tuple[str, Any]]:
    """A session that is not opened: its ceiling was used up before the update."""
    return
    yield


def resume_kwargs(resume: dict[str, Any] | None, grant: Any, prompt: str) -> dict[str, Any]:
    """The prompt, the session id and the ceilings of one tool-less session, taken up again
    from `resume` or not. `used_up` is there only when nothing is left to run with."""
    if resume is None:
        return {
            "text": prompt,
            "session_id": None,
            "max_turns": grant.max_turns,
            "max_budget_usd": grant.max_budget_usd,
        }
    turns, budget, used_up = transcript.ceilings_left(grant.max_turns, grant.max_budget_usd, resume)
    return {
        "text": str(resume.get("message") or ""),
        "session_id": resume.get("session_id") or None,
        "max_turns": turns,
        "max_budget_usd": budget,
        "resume_at": resume.get("safe_uuid"),
        **({"used_up": used_up} if used_up else {}),
    }


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
    def __init__(
        self,
        config: Config,
        ws: Workspaces,
        holds: Holds,
        sessions: Sessions,
        updater: Updater,
        agents: Agents,
        models: Models,
        backlog: Backlog,
        chat: Chat,
        steps: Steps,
        autopilot: Autopilot,
        bus: Bus,
    ) -> None:
        self.config = config
        self.ws = ws
        self.holds = holds
        self.sessions = sessions
        self.updater = updater
        self.agents = agents
        self.models = models
        self.backlog = backlog
        self.chat = chat
        self.steps = steps
        self.autopilot = autopilot
        self.bus = bus

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
                stage=str(owner.get("stage") or ""),
                run=str(owner.get("run") or ""),
                cwd=str(scratch or tree),
                watch=tree if scratch else None,
                directory=directory,
                commands=(),
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
            await self.steps.reconcile_prs()
            self.autopilot.resume()
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
                problem = self._unadoptable(
                    "integration" if kind == "integrate" else "step", key, unit
                )
                if not problem and kind in STEP_KINDS:
                    problem = self._feature_refuses(owner)
            if not problem and kind in ("estimate", "chat"):
                problem = self._owner_refuses(kind, owner)
            if not problem and kind == "chat":
                # A chat turn's used-up ceiling, said here so its `resume` row does.
                used_up = transcript.ceilings_left(CHAT_TURNS, None, row)[2]
                if used_up:
                    problem = f"its ceiling was used up before the update: {used_up}"
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
                self.sessions.adopt(str(row.get("session_id") or ""))
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
        await self.steps.reconcile_prs()
        self.autopilot.resume()
        return said

    def _owner_refuses(self, kind: str, owner: dict[str, Any]) -> str:
        """What an estimate or a chat turn refuses before its session, asked before the
        `resume` row as the step claim is, so that row says what happened. Each owner holds what it takes before its first `await`, and its task runs
        before the autopilot's, so nothing comes between this and that."""
        cwd, key = str(owner.get("workspace_dir") or ""), str(owner.get("workspace") or "")
        try:
            self.ws.check(cwd)
            refuse_while_updating(self.updater)
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

    def _unadoptable(self, machine: str, key: str, unit: str) -> str:
        """Why the unit's attempt cannot be taken up by a resumed `machine`, or `""`: none, or
        the one the last process left `running`, is what Resume takes up."""
        row = self.holds.attempts.holding(key, unit)
        if row is None or (
            row["machine"] == machine
            and row["state"] == "running"
            and row["id"] not in self.steps.tasks
        ):
            return ""
        return describe(unit, row)

    def _adopt(self, machine: str, key: str, unit: str, stage: str) -> int:
        """The attempt a resumed step or integration goes on in: the one left `running`, or a
        new one in `running` when the process that went down had none (an older build)."""
        problem = self._unadoptable(machine, key, unit)
        if problem:
            raise Invalid(problem)
        row = self.holds.attempts.holding(key, unit)
        if row is not None:
            return int(row["id"])
        return int(self.holds.attempts.open(machine, key, unit, stage, state="running")["id"])

    def _end_unresumed(self, journal: Any, row: dict[str, Any], kind: str, problem: str) -> None:
        """The step, integration or estimate ends `failed` and waits for a rerun; a
        chat turn has no `start`, and only its `resume` row says it failed."""
        if kind == "chat":
            return
        try:
            journal.finished(
                str(row.get("workspace") or ""),
                str(row.get("unit") or ""),
                str(row.get("stage") or ""),
                "failed",
                detail=f"not resumed after an update: {problem}",
                session_id=str(row.get("session_id") or ""),
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
        owner = record["owner"]
        cwd = str(owner.get("workspace_dir") or "")
        if kind in STEP_KINDS:
            self.resume_step(record)  # its task is already made
            return None
        if kind == "integrate":
            return self.resume_integration(record)
        if kind == "estimate":
            return _drain(self.backlog.propose_estimates(cwd, resume=record))
        return self.resume_chat(cwd, record)

    def resume_step(self, record: dict[str, Any]) -> steps_mod.Running:
        """A board step, as `run_step` hands one to `drive`: the unit claimed, a new
        recorder and `run`, and `Runner.run` with the row instead of a prompt. Synchronous up
        to the task, so the unit is held when this returns."""
        owner = record["owner"]
        key, cwd = str(owner["workspace"]), str(owner["workspace_dir"])
        unit, stage, artifact = str(owner["unit"]), str(owner["stage"]), str(owner["artifact"])
        journal = self.ws.journal()
        if journal is None:
            raise Invalid("no working folder is set, so nothing can be taken up")
        directory = self.ws.unit_dir(cwd, unit)
        attempt = self._adopt("step", key, unit, stage)
        running = steps_mod.Running(
            workspace=key, unit=unit, stage=stage, started_at=_now(), attempt=attempt, cwd=cwd
        )
        self.steps.tasks[attempt] = running
        self.steps.seal_attempt(running)
        run = uuid.uuid4().hex
        recorder = events.Recorder(
            run, Data(self.config.data_dir), str(journal.working_dir), key, unit, stage
        )
        running.run, running.handle.recorder = run, recorder
        self.steps.recorders[run] = recorder
        self.holds.attempts.set_run(attempt, run)
        rounds = set(owner["rounds_before"]) if owner.get("rounds_before") is not None else None
        end_fields = None
        if rounds is not None:

            async def end_fields() -> dict[str, Any]:
                return await self.models.findings_added(cwd, unit, rounds)

        extra = {
            k: owner.get(k)
            for k in (
                "workspace_dir",
                "rounds_before",
                "tree",
                "watch",
                "scratch",
                "read_also",
            )
        }
        kwargs: dict[str, Any] = dict(
            workspace=cwd,
            directory=directory,
            journal_key=key,
            unit=unit,
            stage=stage,
            artifact=artifact,
            stages=[],
            mode="manual",
            cwd=str(record.get("cwd") or cwd),
            model=record.get("model"),
            effort=owner.get("effort"),
            label=owner.get("label"),
            agent=self.agents.agent(stage),
            end_fields=end_fields,
            read_also=tuple(owner.get("read_also") or ()),
            resume=record,
            owner_extra=extra,
            **({"watch": owner["watch"]} if owner.get("watch") else {}),
        )
        scratch = Path(owner["scratch"]) if owner.get("scratch") else None
        running.task = asyncio.create_task(
            self.steps.drive(
                running,
                Runner(
                    self.sessions,
                    journal,
                    app=self.steps.app_identity(),
                    hooks=self.steps.hooks,
                ),
                cwd,
                unit,
                stage,
                artifact,
                directory,
                None,
                rounds,
                scratch,
                kwargs,
                resumed=True,
            )
        )
        running.task.add_done_callback(lambda _task: self.steps.never_driven(running))
        return running

    def resume_integration(self, record: dict[str, Any]) -> Any:
        """Gebo, with the lease, the grant and the press's facts its owner kept. The unit is
        claimed now; the returned coroutine runs the session and what follows it."""
        owner = record["owner"]
        key, cwd, unit = str(owner["workspace"]), str(owner["workspace_dir"]), str(owner["unit"])
        journal = self.ws.journal()
        if journal is None:
            raise Invalid("no working folder is set, so nothing can be taken up")
        attempt = self._adopt("integration", key, unit, "integrate")
        self.holds.attempts.set_road(attempt, "gebo")
        running = steps_mod.Running(
            workspace=key, unit=unit, stage="integrate", started_at=_now(), attempt=attempt, cwd=cwd
        )
        self.steps.tasks[attempt] = running

        def write(rec: dict[str, Any]) -> dict[str, Any]:
            try:
                return journal.append(rec)
            except BadRecord, Busy:
                return rec

        async def go() -> None:
            running.task = asyncio.current_task()
            outcome = "failed"
            try:
                async for _ in self.steps.integrate_gebo(
                    cwd,
                    key,
                    unit,
                    None,
                    None,
                    None,
                    int(owner["pr"]),
                    Path(owner["tree"]),
                    str(owner["branch"]),
                    str(owner["head_before"]),
                    str(owner["origin_sha"]),
                    journal,
                    write,
                    dict(owner.get("seen") or {}),
                    owner.get("refused_update"),
                    completion=owner.get("completion"),
                    resume=record,
                ):
                    pass
                outcome = "done"
            except asyncio.CancelledError:
                # The app going down: the next start ends the attempt.
                self.steps.tasks.pop(attempt, None)
                raise
            finally:
                if self.steps.tasks.pop(attempt, None) is not None:
                    self.steps.end_attempt(attempt, outcome)

        return go()

    async def resume_chat(self, cwd: str, record: dict[str, Any]) -> None:
        """Nobody is reading this turn now; its reply is in the session, and its `chat`
        row is written as any turn's is."""
        async for _ in self.chat.stream(
            cwd,
            str(record.get("message") or ""),
            str(record.get("session_id") or ""),
            resume=record,
        ):
            pass


async def _drain(agen: AsyncIterator[Any]) -> None:
    async for _ in agen:
        pass
