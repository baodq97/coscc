"""Running, driving, stopping and taking up again one step of a unit, and the attempts every
machine shares: its click, its readers, its task and its end."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, NotRequired, TypedDict
from collections.abc import Awaitable, Callable, Coroutine

from coscc import units
from coscc.agent import modeltrial
from coscc.agent import steps as steps_mod
from coscc.agent.sessions import Sessions, Suspended
from coscc.bus import Bus, Event
from coscc.config import Config
from coscc.git import drift, fetches, gitops
from coscc.kernel import OWNER, Facts, Hooks, Invalid, facts as facts_of
from coscc.runlog import events
from coscc.runner.attempt import describe_attempt
from coscc.runner.prompt import answers_section
from coscc.runner.queue import MACHINES, STOPPABLE, Attempt, Holds, Refused, describe
from coscc.runner.reply import RunError
from coscc.runner.step import Runner, check_started_by
from coscc.git.gitops import GitError
from coscc.store.db import Busy, Data, now as _now
from coscc.store.journal import NOT_STEPS, BadRecord, Journal
from coscc.units import backlog, planmap, retake, worktrees
from coscc.units import board as board_reader
from coscc.units import BadUnit, CannotCreate
from coscc.units.board import Unavailable
from coscc.units.ideas import Ideas
from coscc.units.read import HoldView
from coscc.units.workspaces import Workspaces
from coscc.units.worktrees import BRANCH_REMOTE, BRANCH_TRUNK, describe_base

log = logging.getLogger(__name__)


# The stages the app does itself with no session, through the PR machine (`prmachine.STAGES`).
MECHANICAL = ("pr", "ship")

# The longest note a rerun takes, in characters. Chosen, not measured.
RERUN_NOTE_MAX = 4000

# What the page says when a retake of the screenshots refuses `review`: one
# sentence, no commit, path or log line. The rest is in the `screens` record.
RETAKE_REFUSED = "The screenshots could not be taken again after the branch was rewritten, so review did not start."


class Cut(TypedDict):
    """A branch `cut_branch` cut: its name, the commit and tree it was cut in, and the prepare."""

    cwd: str
    unit: str
    branch: str
    base: str
    sha: str
    output: str
    worktree: str
    switched: bool
    prepare: dict[str, Any]


async def cut_branch(
    cwd: str, unit: str, data_dir: str | os.PathLike[str] | None, state: Any
) -> Cut:
    """Cut the unit's branch in its worktree from the freshly fetched trunk, and prepare it.

    One path for the "Cut this unit's branch" button and for an `impl` that starts on a
    detached tree. It raises `Invalid` with the words of what failed; nothing is cut then."""
    try:
        name = units.branch_name(cwd, unit, data_dir, state)
    except (CannotCreate, BadUnit) as e:
        raise Invalid(str(e)) from e
    # Cut in the unit's own worktree, never in the workspace: cutting there took one unit's
    # branch away from another. The workspace stays on `main`.
    try:
        tree = await worktrees.ensure(cwd, unit, None, data_dir)
    except (GitError, BadUnit) as e:
        raise Invalid(f"Could not open {unit}'s worktree, so no branch was cut. {e}") from e
    repo = Path(tree["path"])
    # Through the coordinator, so a step starting beside this does not race it for
    # `refs/remotes/origin/main`; a fetch under 30s old is reused here too.
    try:
        await fetches.fetch(repo, BRANCH_REMOTE, BRANCH_TRUNK)
    except GitError as e:
        raise Invalid(
            f"Could not update {BRANCH_TRUNK} from {BRANCH_REMOTE}, so no branch was cut. "
            f"Nothing in the repository changed. git said: {e}"
        ) from e
    try:
        sha = await gitops.rev_parse(repo, f"refs/remotes/{BRANCH_REMOTE}/{BRANCH_TRUNK}")
        output = await gitops.create_branch(repo, name, sha)
    except GitError as e:
        raise Invalid(str(e)) from e
    # Prepared here rather than when the tree was made: the lockfiles an `impl` works with are
    # the ones at the commit just cut from. A failure is returned, not raised (the branch is cut
    # either way), and `run_step` refuses `impl` until preparing succeeds.
    prepared = await worktrees.prepare(repo, cwd, data_dir=data_dir)
    return {
        "cwd": cwd,
        "unit": unit,
        "branch": name,
        "base": f"{BRANCH_REMOTE}/{BRANCH_TRUNK}",
        "sha": sha[:7],
        "output": output,
        "worktree": str(repo),
        "switched": bool(tree.get("switched")),
        "prepare": prepared,
    }


async def _unset(*_: Any, **__: Any) -> dict[str, Any]:
    raise RuntimeError("the PR side was not hung on the steps")


def step_cwd(stage: str, work: str, directory: Path, spike_dir: str | None = None) -> str:
    """Where a step's session runs: the unit's worktree, except for `ship` and `spike`.

    `spike` runs in `spike_dir`, a throwaway directory under the data root, so its probe code
    never lands in the worktree whose branch it would ride.

    `ship` runs `gh pr merge --squash --delete-branch`, which inside a worktree merges and then
    fails (gh tries to switch the worktree to `main`, git refuses, exit 1, branches left
    behind). Run from a non-git directory with the PR URL it merges and deletes the remote
    branch; the unit's store directory is such a directory. `worktrees.remove_if_finished`
    removes the worktree and local branch once GitHub says `MERGED`. The gates still read `work`.
    """
    if stage == "spike" and spike_dir:
        return spike_dir
    return str(directory) if stage == "ship" else work


def _answers_kept(path: Path, before: bytes) -> bool:
    """Whether `path` still ends with the `## Answers` section it had, `before`,
    byte for byte. A `pr` step writes `pr.md` itself, and an `impl` step
    `impl.md`, so nothing else guards that section."""
    try:
        return path.read_bytes().endswith(before)
    except OSError:
        return False


def _gate_reasons(answer: board_reader.Gate) -> tuple[str, ...]:
    """The codes that go with the gate's words, so no reader downstream parses these. A stand-in
    gate that answers a plain pair has none."""
    return tuple(getattr(answer, "reasons", ()))


def _rounds_before(found: dict[str, Any], row: dict[str, Any]) -> set[Any] | None:
    """The rounds `review.md` held before this step, so that the ones it adds can be told apart
    afterwards. Taken from the board already read; None for any other artifact."""
    if row["file"] != "review.md":
        return None
    return {r.get("n") for r in found.get("rounds") or []}


def _round_kwargs(
    found: dict[str, Any], row: dict[str, Any], stage: str, rounds_before: set[Any] | None
) -> dict[str, Any]:
    """From the same board: a last round the loop read as unfinished, and the ids it dropped,
    for the review that runs again. Whether it counts is not asked here."""
    last_round = (found.get("rounds") or [None])[-1] if row["file"] == "review.md" else None
    kw: dict[str, Any] = (
        {"unfinished_round": {"n": last_round["n"], "dropped": list(last_round["dropped"])}}
        if last_round and last_round.get("unfinished")
        else {}
    )
    # The findings the last round left open, which an `impl` may claim only a
    # person can close: guard `impl-claim` reads them when its object arrives.
    if rounds_before:
        kw["rounds_known"] = tuple(sorted(n for n in rounds_before if isinstance(n, int)))
    if stage == "impl" and found.get("rounds"):
        last = found["rounds"][-1]
        kw.update(open_findings=tuple(last.get("open_ids") or ()), claims_round=last.get("n"))
    return kw


async def _plan_drift(
    journal: Journal,
    key: str,
    unit: str,
    stage: str,
    directory: Path,
    tree: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Which files the plan names `main` changed since the plan ran, for `impl`
    only. Unlike `failed_attempts`, nothing here may refuse the step: a busy run log, an
    unreadable `plan.md` or a bug in `drift.py` is "could not check"."""
    if stage != "impl":
        return None
    try:
        return await drift.compute(
            journal.records(key, unit),
            (directory / "plan.md").read_text(encoding="utf-8"),
            tree["path"] if tree else None,
        )
    except Exception as e:
        # Recorded as the reason.
        log.exception("the plan drift of %s could not be read", unit)
        return {
            "plan_sha": None,
            "main_sha": None,
            "files": None,
            "checked": False,
            "reason": str(e) or type(e).__name__,
        }


def _shortlist(journal: Journal, key: str, unit: str) -> dict[str, Any]:
    """Where the unit stood in the shortlist in effect as it started, for the
    outcome's measurement. Like the plan drift, nothing here may refuse the step."""
    try:
        return backlog.stamp(journal.records(key, kind="shortlist"), unit)
    except Exception as e:
        # Recorded as the reason.
        log.exception("the shortlist rank of %s could not be read", unit)
        return {"rank": None, "of": None, "record": None, "error": str(e) or type(e).__name__}


def _answers_before(stage: str, directory: Path, row: dict[str, Any]) -> bytes | None:
    """The `## Answers` an `impl` step finds in its artifact. Read after the last refusal
    that reads nothing more, before any money is spent: every `impl` step writes `impl.md`
    itself, so only a comparison afterwards can tell whether its `## Answers` survived."""
    if stage != "impl":
        return None
    try:
        return answers_section((directory / row["file"]).read_bytes())
    except OSError:
        return None


# The one stage the run button may offer for a unit, as `coscc.loop next` answered it.
NextStep = TypedDict(
    "NextStep",
    {
        "cwd": str,
        "unit": str,
        "stage": str | None,
        "action": str,
        "blocked": bool,
        # Finding ids a person is awaited on.
        "waiting": list[str],
        # Finding ids the last review round left out.
        "dropped": list[str],
        "hold": NotRequired[HoldView],
        # The stage a fully answered draft would run again; only the autopilot reads it.
        "rerun": NotRequired[str],
        # `impl` when it left its file a draft asking nothing; only the autopilot reads it.
        "continue": NotRequired[str],
        # The codes the autopilot branches on.
        "reasons": list[str],
    },
)


class Steps:
    """One step of a unit, from its click to its end, and what every attempt shares.

    What sits above `runner` is handed in: the stage's agent row and configuration, the
    answers' writes, the update's refusals, the app's identity. The PR side (`github`) hangs
    `mechanical` and `integration_note` on this once built, and runs its own attempts through
    `stream`, `enqueue`, `launch`, `tell`, `close` and `end_attempt`.
    """

    def __init__(
        self,
        config: Config,
        ws: Workspaces,
        holds: Holds,
        sessions: Sessions,
        ideas: Ideas,
        bus: Bus,
        *,
        agent_of: Callable[[str], dict[str, Any] | None],
        stage_config: Callable[..., dict[str, Any]],
        ci_red: Callable[[str, str, str], Awaitable[bool | None]],
        findings_added: Callable[[str, str, set[Any]], Awaitable[dict[str, Any]]],
        worktree: Callable[..., Awaitable[dict[str, Any] | None]],
        append_to_answers: Callable[[Path, str, str], Awaitable[Any]],
        ingest: Callable[[str, str, dict[str, Any], str], Awaitable[dict[str, Any]]],
        post_new_rounds: Callable[[str, str, set[Any]], Awaitable[Any]],
        sync_pr: Callable[..., Awaitable[Any]],
        refuse_updating: Callable[[], None],
        refuse_mechanical: Callable[[], None],
        identity: Callable[[], dict[str, str]],
    ) -> None:
        self.config = config
        self.ws = ws
        self.holds = holds
        self.sessions = sessions
        self.ideas = ideas
        self.bus = bus
        self.agent_of = agent_of
        self.stage_config = stage_config
        self.ci_red = ci_red
        self.findings_added = findings_added
        self.worktree = worktree
        self.append_to_answers = append_to_answers
        self.ingest = ingest
        self.post_new_rounds = post_new_rounds
        self.sync_pr = sync_pr
        self.refuse_updating = refuse_updating
        self.refuse_mechanical = refuse_mechanical
        self.identity = identity
        self.hooks = Hooks()
        # Hung on this by the PR side: a `pr` or `ship` run through the PR machine, and the
        # section a `review` carries about an integration pushed since its last round.
        self.mechanical: Callable[..., Awaitable[dict[str, Any]]] = _unset
        self.integration_note: Callable[[Journal, str, str], str] = lambda *_: ""
        # Held across one retake of a unit's screenshots, app-wide: every capture binds
        # `127.0.0.1:18783`, so two at once fail. A capture a session runs does not take it.
        self._screens_lock = asyncio.Lock()
        # The retake running now, if any, `{workspace, unit, started}`; read only by `_update_waited`.
        self.retakes: dict[str, dict[str, Any]] = {}
        # The live part of each launched or queued attempt, by attempt id: what a Stop closes
        # and cancels, and the readers its items go to. Decides nothing, shows nothing.
        self.tasks: dict[int, steps_mod.Running] = {}
        # The click's own `Running`, while `open` may hand its attempt to the scheduler.
        self._opening: steps_mod.Running | None = None
        holds.attempts.launchers["step"] = lambda row: self.launch(row, self._prepare)
        # The recorder of every running board step, by `run`: what `events_page` reads and
        # `follow_events` subscribes to. A step leaves it when `drive` ends; then the tables answer.
        self.recorders: dict[str, events.Recorder] = {}

    async def after_end(self, cwd: str, unit: str, stage: str, key: str) -> None:
        """After a step's `done` and its `end`: a `questions` record when the unit is left with
        open questions, and after `ship` a `ship` record saying whether it merged. Never raises,
        like `cleanup`: a record that cannot be written changes nothing about the step.

        `why` is `decide`'s, read off the files by `coscc.loop status` as `board.read` copies it,
        without asking `gh` as `next` would, so `ship-refused` can also be a merge whose branch
        deletion failed. `ship-merging` (a merge asked for and not recorded yet) writes no
        `ship` record: it is no refusal, and the `shipped` follows once the merge is recorded."""
        try:
            journal = self.ws.journal()
            if journal is None:
                return
            data = await board_reader.read(self.ws.units_root(cwd), state=self.ws.snapshot(cwd))
            found = next((u for u in data["units"] if u["name"] == unit), None)
            if found is None:
                return
            asked = board_reader.open_questions(found)
            if asked:
                journal.append(
                    {
                        "kind": "questions",
                        "workspace": key,
                        "unit": unit,
                        "stage": stage,
                        "questions": [
                            {"artifact": q.get("artifact"), "n": q.get("n")} for q in asked
                        ],
                    }
                )
            result = {"finished": "shipped", "ship-refused": "refused"}.get(
                str(found.get("why") or "")
            )
            if stage == "ship" and result:
                journal.append(
                    {
                        "kind": "ship",
                        "workspace": key,
                        "unit": unit,
                        "stage": "ship",
                        "result": result,
                    }
                )
        except Exception:
            # `Unavailable`, `BadRecord`, `Busy` included.
            log.exception("what follows the %s of %s was not done", stage, unit)
            return

    async def next_step(self, cwd: str, unit: str) -> NextStep:
        """The one stage the run button may offer, and why -- `coscc.loop next`'s answer.

        Read with the same store and the same `repo=cwd` that `run_step` hands the gate, so
        the stage offered and the gate that will be asked read one checkout. Nothing here chooses a stage.
        """
        self.ws.check(cwd)
        if not unit:
            raise Invalid("name a work unit")
        self.ws.unit_dir(cwd, unit)
        # Asked first with no `--repo`, which reads files only: a held unit is
        # answered here, before `worktree` could reopen the tree a drop just removed.
        try:
            held = await board_reader.next_step(
                self.ws.units_root(cwd), unit, repo=None, state=self.ws.snapshot(cwd, [unit])
            )
        except Unavailable as e:
            raise Refused(str(e), ("unavailable",)) from e
        if held.get("hold"):
            return {
                "cwd": cwd,
                "unit": unit,
                "stage": held["stage"],
                "action": str(held["action"]),
                "blocked": bool(held["blocked"]),
                "waiting": [],
                "dropped": [],
                "hold": held["hold"],
                "reasons": list(held.get("reasons") or []),
            }
        # The unit's worktree is the checkout its branch and pull request are read
        # from. None when there is none to open, and the loop then keeps `review` and
        # `ship` closed rather than read the workspace's branch, which is not this unit's.
        # A workspace that is not a git repository has no worktrees, and is read as it
        # always was — the same fallback `run_step` takes, so the two read one checkout.
        if (Path(cwd).expanduser().resolve() / ".git").exists():
            tree = await self.worktree(cwd, unit)
            repo = tree["path"] if tree else None
        else:
            repo = cwd
        try:
            found = await board_reader.next_step(
                self.ws.units_root(cwd), unit, repo=repo, state=self.ws.snapshot(cwd, [unit])
            )
        except Unavailable as e:
            raise Refused(str(e), ("unavailable",)) from e
        return {
            "cwd": cwd,
            "unit": unit,
            "stage": found["stage"],
            "action": str(found["action"]),
            "blocked": bool(found["blocked"]),
            # The findings a person is awaited on, copied from `coscc.loop next`.
            "waiting": list(found.get("waiting") or []),
            # The ids the last review round left out, copied from `coscc.loop next`.
            "dropped": list(found.get("dropped") or []),
            # The stage a fully answered draft would run again; only the autopilot
            # reads it.
            "rerun": str(found.get("rerun") or ""),
            # `impl` when it left its file a draft asking nothing; only the autopilot reads it.
            "continue": str(found.get("continue") or ""),
            # The codes the autopilot branches on, copied from `coscc.loop next`.
            "reasons": list(found.get("reasons") or []),
        }

    async def rerun_offers(self, cwd: str, unit: str) -> dict[str, Any]:
        """The accepted stages `unit` may run again, each with the stages that
        then run again after it -- `coscc.loop rerun`'s answer, copied: `{unit, offers: [{stage,
        later}], why}`. Files only: no worktree is opened and no `gh` is asked. Nothing here
        chooses a stage."""
        self.ws.check(cwd)
        if not unit:
            raise Invalid("name a work unit")
        self.ws.unit_dir(cwd, unit)
        try:
            found = await board_reader.rerun(
                self.ws.units_root(cwd), unit, state=self.ws.snapshot(cwd, [unit])
            )
        except Unavailable as e:
            raise Invalid(str(e)) from e
        if "error" in found:
            raise Invalid(str(found["error"]))
        return found

    async def set_mode(self, cwd: str, unit: str, stage: str, mode: str) -> dict[str, Any]:
        """Choose how one step runs. Validated against the board, not against a second list."""
        self.ws.check(cwd)
        journal = self.ws.journal()
        if journal is None:
            raise Invalid(
                "no working folder is set, so a mode cannot be recorded — set COS_WORKING_DIR"
            )

        try:
            data = await board_reader.read(self.ws.units_root(cwd), state=self.ws.snapshot(cwd))
        except Unavailable as e:
            raise Invalid(str(e)) from e

        found = next((u for u in data["units"] if u["name"] == unit), None)
        if found is None:
            raise Invalid(f"no such work unit in this workspace: {unit}")
        if stage not in data["stages"]:
            raise Invalid(f"no such stage: {stage} (use one of {', '.join(data['stages'])})")

        try:
            journal.set_mode(self.ws.key(cwd), unit, stage, mode)
        except BadRecord as e:
            raise Invalid(str(e)) from e
        except Busy as e:
            raise Invalid(str(e)) from e
        self.bus.publish(Event("mode.set", self.ws.key(cwd), unit))
        return {"cwd": cwd, "unit": unit, "stage": stage, "mode": mode}

    async def run_step(
        self,
        cwd: str,
        unit: str,
        stage: str,
        started_by: str = "person",
        rerun: bool = False,
        note: str = "",
    ) -> AsyncIterator[tuple[str, Any]]:
        """Run one step of one unit, streaming the reply as it arrives.

        Everything this needs — the stage order, the artifact filename, the mode — comes
        from one board read, so a step cannot run against a different idea of the unit
        than the one the page is showing.

        `started_by` is `autopilot` only when the autopilot calls this; no route
        passes it, so a request cannot say it is the autopilot. The autopilot queues with
        `enqueue_step`: a note sent here is a person's, and one that says it is the
        autopilot's is refused.

        `rerun` runs an accepted stage again, with a person's `note`. Whether the
        stage may, and the `### Rerun` block appended to `intent.md` before the session
        starts, are `coscc.loop rerun`'s. Refused for the autopilot and for a note over
        `RERUN_NOTE_MAX`; an empty note is not refused.
        """
        try:
            check_started_by(started_by)
        except ValueError as e:
            raise Invalid(str(e)) from e
        if started_by != "person" and str(note or "").strip():
            raise Invalid("the app's note reaches a step only through the autopilot's queue")
        self.ws.check(cwd)
        self.refuse_updating()
        if self.ws.journal() is None:
            raise Refused(
                "no working folder is set, so a run cannot be recorded — set COS_WORKING_DIR",
                ("no-run-log",),
            )
        # The click is an attempt in `queued`, or `unit-busy` when the unit has one already.
        # The scheduler launches `_prepare` once a slot is free; this frame only reads. A
        # reader that goes away -- a closed tab, a dropped NDJSON client -- takes its queue
        # with it and nothing else; stopping it is `stop_step`, and only that.
        async for item in self.stream(
            "step",
            cwd,
            unit,
            stage,
            started_by=started_by,
            rerun=rerun,
            note=str(note or "").strip(),
        ):
            yield item

    def enqueue_step(self, cwd: str, unit: str, stage: str, note: str = "") -> int:
        """The autopilot's step: `run_step`'s first half with no reader. An attempt in `queued`,
        `started_by=autopilot`, its id returned at once; the scheduler launches `_prepare`,
        which asks the gate, and `drive` ends it and runs `after_end` with nobody reading, as
        it does for one queued before a restart. `note` is the app's own (`note_by=app`): the
        prompt puts it apart from a person's. `unit-busy` and the update are refused here."""
        note = note.strip()
        return self.enqueue(
            cwd, unit, "step", stage, note=note, note_by="app" if note else "person"
        )

    def enqueue(self, cwd: str, unit: str, machine: str, stage: str, **kw: Any) -> int:
        """An attempt of the autopilot's in `queued`, with no reader; its id."""
        self.ws.check(cwd)
        self.refuse_updating()
        if self.ws.journal() is None:
            raise Refused(
                "no working folder is set, so a run cannot be recorded — set COS_WORKING_DIR",
                ("no-run-log",),
            )
        running = self._open(machine, cwd, unit, stage, started_by="autopilot", **kw)
        return running.attempt

    async def stream(
        self, machine: str, cwd: str, unit: str, stage: str, **kw: Any
    ) -> AsyncIterator[tuple[str, Any]]:
        """A click's attempt: opened (`unit-busy` when the unit holds one) and followed by one
        reader, to its `done` or what it raised."""
        queue: asyncio.Queue = asyncio.Queue()
        running = self._open(machine, cwd, unit, stage, queue=queue, **kw)
        async for item in self._follow(running, queue):
            yield item

    def _open(
        self,
        machine: str,
        cwd: str,
        unit: str,
        stage: str,
        queue: asyncio.Queue | None = None,
        **kw: Any,
    ) -> steps_mod.Running:
        key = self.ws.key(cwd)
        running = steps_mod.Running(workspace=key, unit=unit, stage=stage, started_at=_now())
        running.cwd = cwd
        if queue is not None:
            running.listeners.add(queue)
        self._opening = running
        try:
            row = self.holds.attempts.open(machine, key, unit, stage, **kw)
        finally:
            self._opening = None
        if not running.attempt:
            # Still queued: the scheduler finds it here once a slot is free.
            running.attempt, running.started_at = row["id"], row["since"]
            self.tasks[row["id"]] = running
        return running

    async def _follow(
        self, running: steps_mod.Running, queue: asyncio.Queue
    ) -> AsyncIterator[tuple[str, Any]]:
        """What one reader of an attempt streams, to its `done` or what it raised."""
        try:
            while True:
                kind, payload = await queue.get()
                if kind == "raise":
                    raise payload
                yield (kind, payload)
                if kind == "done":
                    return
        finally:
            running.listeners.discard(queue)

    @staticmethod
    def tell(running: steps_mod.Running, item: tuple[str, Any]) -> None:
        for q in list(running.listeners):
            q.put_nowait(item)

    def _live(self, row: Attempt) -> steps_mod.Running:
        """The `Running` of an attempt the scheduler moved on: the one its click made, or, for
        one queued before a restart, a new one with no reader."""
        running = self._opening if self._opening is not None and not self._opening.attempt else None
        if running is None or (running.workspace, running.unit) != (row["workspace"], row["unit"]):
            running = self.tasks.get(row["id"]) or steps_mod.Running(
                workspace=row["workspace"],
                unit=row["unit"],
                stage=row["stage"],
                started_at=row["since"],
            )
        running.attempt, running.started_at = row["id"], row["since"]
        self.tasks[row["id"]] = running
        return running

    def launch(
        self,
        row: Attempt,
        work: Callable[[steps_mod.Running, Attempt], Coroutine[Any, Any, None]],
    ) -> None:
        """The scheduler's launcher of an attempt: `work` as its task."""
        running = self._live(row)
        running.task = asyncio.get_running_loop().create_task(work(running, row))

    def close(self, running: steps_mod.Running, error: BaseException | None, outcome: str) -> None:
        """End an attempt that was not handed to `drive`: `refused` with the refusal's code where
        its machine allows it, else `ended(outcome)`. Tells its readers, and forgets it."""
        attempts = self.holds.attempts
        row = attempts.get(running.attempt)
        if self.tasks.get(running.attempt) is running:
            del self.tasks[running.attempt]
        if error is not None:
            self.tell(running, ("raise", error))
        if row is None or row["state"] in ("ended", "refused"):
            return
        code = ""
        if isinstance(error, Refused):
            code = error.reasons[0] if error.reasons else "refused"
        elif isinstance(error, Invalid):
            code = "invalid"
        if code and outcome != "stopped" and "refused" in MACHINES[row["machine"]][row["state"]]:
            attempts.move(running.attempt, "refused", code)
        else:
            attempts.move(
                running.attempt,
                "ended",
                "stop_late" if row["stop_asked_at"] and outcome != "stopped" else outcome,
            )

    async def _prepare(self, running: steps_mod.Running, asked: Attempt) -> None:
        """A step from `preparing` to `drive`: the board read, the tree, the gate and the inputs,
        nothing spent until the gate is open. A Stop cancels it; `gitops` kills the `git` it
        was in and `worktrees.ensure` removes the tree it left half made."""
        key, unit, stage = asked["workspace"], asked["unit"], asked["stage"]
        cwd = running.cwd or key
        started_by, rerun, note = asked["started_by"], bool(asked["rerun"]), asked["note"]
        # The autopilot's own note: never a rerun's, never a person's.
        app_note = note if asked.get("note_by") == "app" and not rerun else ""
        handed = False
        try:
            journal = self.ws.journal()
            if journal is None:
                raise Refused(
                    "no working folder is set, so a run cannot be recorded — set COS_WORKING_DIR",
                    ("no-run-log",),
                )
            # Refuse, or find what the step runs on: nothing is spent until the gate is open.
            data, found, row = await self._find_stage(cwd, unit, stage)
            rerun_block = await self._ask_rerun(cwd, unit, stage, started_by, note) if rerun else ""
            tree, work = await self._open_tree(cwd, unit, stage)
            base = await self._tree_base(cwd, unit, tree)
            answer = await self._ask_gate(cwd, unit, stage, work)
            refusal = self.feature_refusal(
                facts_of(
                    workspace=cwd,
                    workspace_key=key,
                    unit=unit,
                    stage=stage,
                    run="",
                    cwd=work,
                    watch=None,
                    directory=self.ws.unit_dir(cwd, unit),
                    commands=(),
                    resumed=False,
                )
            )
            if refusal:
                raise Refused(refusal, ("feature-refused",))

            # `pr` and `ship` run no session: the PR machine pushes, opens or merges, and
            # records each move through its guard. `ending` from the start: a Stop after
            # this point never cuts a push halfway.
            if stage in MECHANICAL:
                self.holds.attempts.move(running.attempt, "running")
                self.holds.attempts.move(running.attempt, "ending")
                done = await self._run_mechanical(
                    cwd, key, unit, stage, tree, started_by, rerun, rerun_block, answer
                )
                self.tell(running, ("done", done))
                handed = True
                outcome = done.get("outcome") if isinstance(done, dict) else None
                self.close(running, None, str(outcome or "done"))
                return

            # What the step is handed.
            screens_note = await self._ready_tree(
                cwd, key, journal, unit, stage, tree, work, started_by
            )
            directory = self.ws.unit_dir(cwd, unit)
            rounds_before = _rounds_before(found, row)
            inputs, answers_before = await self._gather_inputs(
                cwd=cwd,
                key=key,
                journal=journal,
                unit=unit,
                stage=stage,
                stages=data["stages"],
                found=found,
                row=row,
                directory=directory,
                tree=tree,
                work=work,
                rounds_before=rounds_before,
            )
            if rerun:
                await self.append_to_answers(directory / "intent.md", "\n" + rerun_block, "a rerun")
            inputs.update(await self._link_kwargs(cwd, unit, stage))
            # Emptied before the step, whatever an earlier one left, and removed
            # after it however it ends -- in `drive`, so a client that drops the stream does
            # not decide when.
            scratch = units.spike_dir(cwd, unit, self.config.data_dir) if stage == "spike" else None
            kwargs = self._step_kwargs(
                cwd=cwd,
                key=key,
                unit=unit,
                stage=stage,
                stages=data["stages"],
                artifact=row["file"],
                directory=directory,
                answer=answer,
                tree=tree,
                work=work,
                base=base,
                scratch=scratch,
                started_by=started_by,
                rerun=rerun,
                note=note,
                app_note=app_note,
                screens_note=screens_note,
                rounds_before=rounds_before,
                inputs=inputs,
            )

            # Launch.
            runner = Runner(self.sessions, journal, app=self.identity(), hooks=self.hooks)
            self._start(
                running=running,
                runner=runner,
                cwd=cwd,
                key=key,
                journal=journal,
                unit=unit,
                stage=stage,
                artifact=row["file"],
                directory=directory,
                base=base,
                rounds_before=rounds_before,
                scratch=scratch,
                kwargs=kwargs,
                answers_before=answers_before,
            )
            handed = True
        except asyncio.CancelledError:
            if not running.stop_requested:
                # The app going down: the attempt stays `preparing`, and the next start
                # removes what this left.
                if self.tasks.get(running.attempt) is running:
                    del self.tasks[running.attempt]
                raise
            task = asyncio.current_task()
            if task is not None:
                task.uncancel()
            self.close(
                running,
                Invalid(f"{unit}'s {stage} step was stopped before it began; nothing ran"),
                "stopped",
            )
        except Exception as e:
            if not handed:
                self.close(running, e, "failed")
            else:
                raise

    async def _find_stage(
        self, cwd: str, unit: str, stage: str
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """The board read, the unit in it and its row for `stage`. Refuses a unit or a stage
        that is not there, and a unit that is held."""
        try:
            data = await board_reader.read(self.ws.units_root(cwd), state=self.ws.snapshot(cwd))
        except Unavailable as e:
            raise Refused(str(e), ("unavailable",)) from e

        found = next((u for u in data["units"] if u["name"] == unit), None)
        if found is None:
            raise Refused(f"no such work unit in this workspace: {unit}", ("no-unit",))
        row = next((r for r in found["stages"] if r["stage"] == stage), None)
        if row is None:
            raise Refused(
                f"no such stage: {stage} (use one of {', '.join(data['stages'])})", ("no-stage",)
            )
        # the loop's own field, read before any worktree is opened — the gate
        # below would refuse too, but only after `worktree` had reopened a dropped tree.
        held = found.get("hold")
        if held:
            raise Refused(
                f"{unit} is {held.get('state')}: {held.get('reason')} — nothing runs on it",
                ("held",),
            )
        return data, found, row

    async def _ask_rerun(self, cwd: str, unit: str, stage: str, started_by: str, note: str) -> str:
        """The `### Rerun` block for running `stage` again, asked before a worktree is opened or
        the gate asked. Whether `stage` may run again, and the block that says so, are
        the loop's; its refusal is passed on."""
        if started_by != "person":
            raise Refused(
                "a stage is run again only by a person, from the board, never by the autopilot",
                ("rerun-by-person",),
            )
        if len(note) > RERUN_NOTE_MAX:
            raise Invalid(
                f"the note is {len(note)} characters, over the {RERUN_NOTE_MAX} a rerun takes"
            )
        try:
            asked = await board_reader.rerun(
                self.ws.units_root(cwd), unit, stage, state=self.ws.snapshot(cwd, [unit])
            )
        except Unavailable as e:
            raise Refused(str(e), ("unavailable",)) from e
        if "error" in asked:
            raise Invalid(str(asked["error"]))
        return str(asked.get("block") or "")

    async def _open_tree(
        self, cwd: str, unit: str, stage: str
    ) -> tuple[dict[str, Any] | None, str]:
        """The unit's worktree, and the directory the step runs in.

        Every step runs in the unit's own worktree. A workspace that is not a git
        repository has none, and its steps run where they always did — there is no
        branch there for another unit to take away.
        """
        is_repo = (Path(cwd).expanduser().resolve() / ".git").exists()
        tree = await self.worktree(cwd, unit, strict=True) if is_repo else None
        if is_repo and tree is None:
            try:
                tree = {
                    "path": (await worktrees.ensure(cwd, unit, None, self.config.data_dir))["path"]
                }
            except (GitError, BadUnit) as e:
                raise Refused(
                    f"{unit} has no worktree and one could not be opened: {e}", ("no-worktree",)
                ) from e
        work = tree["path"] if tree else cwd
        # A spike is watched through the worktree's `HEAD` and `git status`;
        # with no git there is nothing to watch, so it does not run at all.
        if stage == "spike" and tree is None:
            raise Refused(
                "spike needs a git worktree to watch, and this workspace is not a git repository",
                ("no-git",),
            )
        return tree, work

    async def _tree_base(
        self, cwd: str, unit: str, tree: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """A tree already on its branch carries whatever `worktree` read when it was opened onto it (or nothing,
        when it was already there before this call); a tree still detached is refreshed
        now, on the spot, because a session about to run on it is about to read it."""
        if tree is None:
            return None
        if tree.get("branch"):
            return tree.get("base")
        return await worktrees.refresh_base(cwd, unit, self.config.data_dir)

    def feature_refusal(self, facts: Facts) -> str:
        """The words of the first feature guard that denies this run, or `""` when all abstain. A
        guard that raises denies: a run is never let through by a check that could not be made."""
        for guard in self.hooks.for_step(facts.stage, facts.workspace).guards:
            try:
                words = guard.check(facts)
            except Exception as e:
                log.exception("guard %s of a feature failed", guard.name)
                return f"{guard.name}: failed ({type(e).__name__})"
            if words is not None:
                return f"{guard.name}: {words}"
        return ""

    async def _ask_gate(self, cwd: str, unit: str, stage: str, work: str) -> board_reader.Gate:
        """`coscc.loop gate` is asked here, not left to the skill: a session often cannot run
        a command. Here rather than in `Runner` because a refusal must arrive before any
        money is spent, and `run_step` is the last place that is still true."""
        # `pr.md`'s title and body go up before the `ship` gate compares the
        # title, so one a person changed on GitHub, or a `pr` step left behind, does not
        # close it. Never raises; when it fails, the gate decides.
        if stage == "ship":
            await self.sync_pr(cwd, unit, None, stage="ship")
        try:
            # `work` is the checkout the `review` and `ship` gates read git and the pull
            # request from. The store has no git to read.
            answer = await board_reader.gate(
                self.ws.units_root(cwd),
                unit,
                stage,
                repo=work,
                state=self.ws.snapshot(cwd, [unit]),
            )
        except Unavailable as e:
            raise Refused(str(e), ("unavailable",)) from e
        allowed, said = answer
        if not allowed:
            raise Refused(said, _gate_reasons(answer))
        return answer

    async def _run_mechanical(
        self,
        cwd: str,
        key: str,
        unit: str,
        stage: str,
        tree: dict[str, Any] | None,
        started_by: str,
        rerun: bool,
        rerun_block: str,
        answer: board_reader.Gate,
    ) -> dict[str, Any]:
        """One `pr` or `ship` through the PR machine, and the `done` item it ends with.
        A `pr` run again has its block appended as any rerun, and the note reaches
        no prompt: the app writes `pr.md` again from the unit's metadata."""
        if rerun:
            await self.append_to_answers(
                self.ws.unit_dir(cwd, unit) / "intent.md", "\n" + rerun_block, "a rerun"
            )
        return await self.mechanical(
            cwd,
            key,
            unit,
            stage,
            tree,
            started_by,
            again=rerun,
            rebased=getattr(answer, "rebased", None),
        )

    async def _ready_tree(
        self,
        cwd: str,
        key: str,
        journal: Journal,
        unit: str,
        stage: str,
        tree: dict[str, Any] | None,
        work: str,
        started_by: str,
    ) -> str:
        """Refuse a tree the step cannot start on, before any money is spent. Returns the
        section the `review` prompt carries about its screenshots, `""` for any other step."""
        if tree is None:
            return ""
        if stage == "impl":
            # A tree still detached gets its branch here, before the session opens, by the
            # path of the "Cut this unit's branch" button; a tree already on one is left alone.
            if not tree.get("branch"):
                try:
                    await cut_branch(cwd, unit, self.config.data_dir, self.ws.snapshot(cwd, [unit]))
                except (GitError, Invalid) as e:
                    raise Refused(f"no branch could be cut for {unit}: {e}", ("no-branch",)) from e
            # A tree that cannot run its tests turns every `impl` red from the start, so
            # the step is not started on one. Tried once more first: a network blip is the
            # ordinary reason, and the page has nothing better to offer than *try again*.
            prepared = worktrees.read_prepare(Path(work)) or {}
            if not prepared.get("ok"):
                prepared = await worktrees.prepare(Path(work), cwd, data_dir=self.config.data_dir)
            if not prepared.get("ok"):
                raise Refused(worktrees.describe_failure(prepared), ("no-worktree",))
        # A UI unit whose branch was rewritten since `impl` took its
        # screenshots has them taken again, here, before any money is spent; a retake that
        # fails refuses the step, and no round is spent on a stale manifest.
        if stage == "review":
            return await self.retake_screens(cwd, key, journal, unit, work, started_by)
        return ""

    async def _gather_inputs(
        self,
        *,
        cwd: str,
        key: str,
        journal: Journal,
        unit: str,
        stage: str,
        stages: list[str],
        found: dict[str, Any],
        row: dict[str, Any],
        directory: Path,
        tree: dict[str, Any] | None,
        work: str,
        rounds_before: set[Any] | None,
    ) -> tuple[dict[str, Any], bytes | None]:
        """What the stage is handed beyond who runs it and where: keyword arguments for
        `Runner.run`, and `## Answers` as the artifact had it. Only a busy run log refuses
        the step here; every other read that fails is recorded as the reason."""
        mode = journal.modes(key).get((unit, stage), "manual")
        round_kw = _round_kwargs(found, row, stage, rounds_before)
        config, failed = await self._stage_config(
            cwd, key, journal, unit, stage, stages, directory, work
        )
        integration_note = self.integration_note(journal, key, unit) if stage == "review" else ""
        plan_drift = await _plan_drift(journal, key, unit, stage, directory, tree)
        # The files the plan names, as they stand in the tree the step runs
        # on, for `impl` only. The same again: nothing in `for_step` may refuse the step.
        plan_kw = planmap.for_step(directory / "plan.md", work) if stage == "impl" else {}
        shortlist = _shortlist(journal, key, unit)
        answers_before = _answers_before(stage, directory, row)
        # `dict(...)`, not a literal: two sources naming one key is a `TypeError`, not an override.
        return dict(
            mode=mode,
            **round_kw,
            **config,
            last_attempt=describe_attempt(failed) if failed else "",
            integration_note=integration_note,
            plan_drift=plan_drift,
            drift_note=drift.describe(plan_drift) if plan_drift is not None else "",
            shortlist=shortlist,
            end_fields=self._end_fields(
                cwd, unit, rounds_before, answers_before, directory / row["file"]
            ),
            **plan_kw,
        ), answers_before

    async def _stage_config(
        self,
        cwd: str,
        key: str,
        journal: Journal,
        unit: str,
        stage: str,
        stages: list[str],
        directory: Path,
        work: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """The stage's configuration and the failed attempts before this run. Resolved after
        the gate, so a refused step reads nothing more: the plan's label, the effort and, for
        `impl`, which run this is, read before any money is spent. `Runner` does not read the
        run log itself; `build_prompt` only places what it is handed, the same as `base_note`."""
        try:
            config = self.stage_config(stage, list(stages), directory, journal, key, unit)
            failed = journal.failed_attempts(key, unit, stage)
        except Busy as e:
            raise Refused(str(e), ("unavailable",)) from e
        # A return to `impl` in the model trial asks `next` once whether CI sent it back;
        # `ci_red` never raises, so nothing here refuses the step.
        if (
            modeltrial.FIELD in (config.get("trial_record") or {})
            and (config.get("impl_run") or 0) > 1
        ):
            config.setdefault("trial_record", {})[modeltrial.CI_RED] = await self.ci_red(
                cwd, unit, work
            )
        return config, failed

    def _end_fields(
        self,
        cwd: str,
        unit: str,
        rounds_before: set[Any] | None,
        answers_before: bytes | None,
        artifact: Path,
    ) -> Callable[[], Awaitable[dict[str, Any]]] | None:
        """What a step that ends `done` adds to its record, asked only then: whether `impl.md`
        kept its `## Answers`, else the findings a `review` added to `review.md`."""
        if answers_before is not None:

            async def answers_kept() -> dict[str, Any]:
                return {"answers_kept": _answers_kept(artifact, answers_before)}

            return answers_kept
        if rounds_before is not None:

            async def findings_added() -> dict[str, Any]:
                return await self.findings_added(cwd, unit, rounds_before)

            return findings_added
        return None

    async def _link_kwargs(self, cwd: str, unit: str, stage: str) -> dict[str, Any]:
        """The keyword arguments the database and the unit's idea add to the prompt."""
        # The answers and holds the prompt renders, from the database.
        link_kw: dict[str, Any] = {"meta": self.ws.meta_of(cwd, unit)}
        # The snapshot a step that runs the loop itself hands `--state` (the `pr` step's
        # `pr-text`, the `ship` step's gate), which refuse to decide without one. Written as the
        # step begins, under the data root beside `spikes/`, and replaced by the next step of
        # the unit. Left out when it could not be written: the step still runs.
        path = Data(self.config.data_dir).root / "state" / units.slot(cwd) / f"{unit}.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(self.ws.snapshot(cwd, [unit]), ensure_ascii=False), encoding="utf-8"
            )
            link_kw["state_file"] = str(path)
        except OSError, Invalid:
            pass
        # Only for a unit an idea lists, and only for `intent` and `impl`;
        # every other step is handed no key.
        if stage == "intent":
            idea_note = self.ideas.idea_note(cwd, unit)
            if idea_note:
                link_kw["idea_note"] = idea_note
        if stage == "impl":
            sibling_paths, siblings_note = await self.ideas.siblings(cwd, unit)
            if siblings_note:
                link_kw.update(siblings_note=siblings_note, read_also=sibling_paths)
        return link_kw

    def _step_kwargs(
        self,
        *,
        cwd: str,
        key: str,
        unit: str,
        stage: str,
        stages: list[str],
        artifact: str,
        directory: Path,
        answer: board_reader.Gate,
        tree: dict[str, Any] | None,
        work: str,
        base: dict[str, Any] | None,
        scratch: Path | None,
        started_by: str,
        rerun: bool,
        note: str,
        screens_note: str,
        rounds_before: set[Any] | None,
        inputs: dict[str, Any],
        app_note: str = "",
    ) -> dict[str, Any]:
        """The keyword arguments `Runner.run` is called with: who runs what, where, what the
        gate said, and the `inputs` gathered."""
        return dict(
            workspace=cwd,
            directory=directory,
            journal_key=key,
            unit=unit,
            stage=stage,
            artifact=artifact,
            stages=list(stages),
            gate_said=answer[1],
            gate_reasons=_gate_reasons(answer),
            cwd=step_cwd(stage, work, directory, str(scratch) if scratch else None),
            base=base,
            base_note=describe_base(base),
            screens_note=screens_note,
            # The stage's row with today's overrides, read once
            # as the step starts: a rename later reaches the next step, not this one.
            agent=self.agent_of(stage),
            **inputs,
            # Only named for a spike, so a stand-in `run` without it keeps working.
            **({"watch": work} if scratch is not None else {}),
            # The same: `Runner.run` writes `person` when it is not named.
            **({"started_by": started_by} if started_by != "person" else {}),
            # The same again: only a rerun names them.
            **({"rerun": True, "rerun_note": note} if rerun else {}),
            # And the autopilot's note, only when it wrote one.
            **({"app_note": app_note} if app_note else {}),
            # What `resume_step` needs of this step, in its `suspend` row.
            owner_extra={
                "workspace_dir": cwd,
                "rounds_before": sorted(rounds_before) if rounds_before is not None else None,
                "tree": tree is not None,
                "watch": work if scratch is not None else None,
                "scratch": str(scratch) if scratch is not None else None,
                "read_also": list(inputs.get("read_also") or ()),
            },
        )

    def _start(
        self,
        *,
        running: steps_mod.Running,
        runner: Runner,
        cwd: str,
        key: str,
        journal: Journal,
        unit: str,
        stage: str,
        artifact: str,
        directory: Path,
        base: dict[str, Any] | None,
        rounds_before: set[Any] | None,
        scratch: Path | None,
        kwargs: dict[str, Any],
        answers_before: bytes | None,
    ) -> None:
        """Start `drive` as the step's own task, its attempt `running`."""
        # The step's `run` and recorder, from here to the task with no `await`
        # between, so every list that names the step names its `run` too.
        run = uuid.uuid4().hex
        recorder = events.Recorder(
            run,
            Data(self.config.data_dir),
            str(journal.working_dir),
            key,
            unit,
            stage,
        )
        running.run = run
        running.handle.recorder = recorder
        self.recorders[run] = recorder
        self.holds.attempts.move(running.attempt, "running", run=run)
        self.seal_attempt(running)
        running.task = asyncio.create_task(
            self.drive(
                running,
                runner,
                cwd,
                unit,
                stage,
                artifact,
                directory,
                base,
                rounds_before,
                scratch,
                kwargs,
                answers_before=answers_before,
            )
        )
        running.task.add_done_callback(lambda _task: self.never_driven(running))

    def seal_attempt(self, running: steps_mod.Running) -> None:
        """What `steps_mod.seal` calls: the attempt is `ending` from the artifact's first byte."""

        def on_seal() -> None:
            self.holds.attempts.move(running.attempt, "ending")

        running.on_seal = on_seal

    async def retake_screens(
        self,
        cwd: str,
        key: str,
        journal: Journal,
        unit: str,
        work: str,
        started_by: str,
    ) -> str:
        """Ask `coscc.loop screens`; when it says to, take the screenshots again under
        `_screens_lock`, judge the result and record it. Returns the section for the `review`
        prompt, `""` when nothing was taken. A retake that fails raises `Invalid` with
        `RETAKE_REFUSED`; what went wrong is only in its record. No tracked file is put back;
        `.screens/` is, by `retake.take`."""
        try:
            asked = await board_reader.screens(
                self.ws.units_root(cwd), unit, work, state=self.ws.snapshot(cwd, [unit])
            )
        except Unavailable as e:
            raise Invalid(str(e)) from e
        if not asked.get("retake"):
            return ""
        old = asked.get("manifest") or {}
        addresses = [str(a) for a in old.get("addresses") or []]
        async with self._screens_lock:
            # Asked again past the lock, which another retake may have held
            # for minutes; from here to the end of `take` a pending update waits for it.
            # And none begins once Apply is pressed.
            self.refuse_mechanical()
            rid = uuid.uuid4().hex
            self.retakes[rid] = {"workspace": key, "unit": unit, "started": _now()}
            started = datetime.now().timestamp()
            try:
                result = await retake.take(Path(work), addresses, data_dir=self.config.data_dir)
            except asyncio.CancelledError:
                # A client that went away, or the app going down: the group is killed, and
                # the record still says a retake was begun and did not finish.
                gone = {"code": None, "seconds": round(datetime.now().timestamp() - started, 1)}
                try:
                    journal.append(
                        retake.record(
                            key, unit, old, gone, False, "cancelled before it finished", started_by
                        )
                    )
                except BadRecord, Busy:
                    pass
                raise
            finally:
                self.retakes.pop(rid, None)
                self.bus.publish(Event("retake.ended", key, unit))
        ok, detail = retake.judge(result)
        try:
            journal.append(retake.record(key, unit, old, result, ok, detail, started_by))
        except (BadRecord, Busy) as e:
            # Only a retake that was taken may say it was.
            if not ok:
                raise Invalid(RETAKE_REFUSED) from e
            raise Invalid(
                f"the screenshots were taken again, but the run log could not record it: {e}"
            ) from e
        if not ok:
            raise Invalid(RETAKE_REFUSED)
        return retake.describe_for_review(old, result.get("manifest_after") or {})

    def never_driven(self, running: steps_mod.Running) -> None:
        """A task cancelled before its first turn -- a Stop queued ahead of it, or an update's
        `shutdown` -- never enters `drive`, so its `finally` never runs. That `finally` is what
        forgets the step, so one still in `tasks` when the task is done never ran: a Stop's
        attempt ends `stopped`, the app going down leaves it to the next start, and the
        reader is told instead of left waiting."""
        if self.tasks.get(running.attempt) is not running:
            return
        del self.tasks[running.attempt]
        # Never started, so it wrote nothing and has nothing to say.
        self.recorders.pop(running.run, None)
        if running.stop_requested:
            self.holds.attempts.move(running.attempt, "ended", "stopped")
        self.tell(
            running,
            (
                "raise",
                Invalid(
                    f"{running.unit}'s {running.stage} step was cancelled before it began; nothing ran"
                ),
            ),
        )

    async def drive(  # noqa: C901, PLR0915 - still to split
        self,
        running: steps_mod.Running,
        runner: Runner,
        cwd: str,
        unit: str,
        stage: str,
        artifact: str,
        directory: Path,
        base: dict[str, Any] | None,
        rounds_before: set[Any] | None,
        scratch: Path | None,
        kwargs: dict[str, Any],
        answers_before: bytes | None = None,
        resumed: bool = False,
    ) -> None:
        """One board step, start to end, as its own task.

        Every item goes to the step's listeners with `put_nowait` -- this never waits on a
        reader -- and a `stopped` step records no transition, cleans nothing and posts nothing.

        `answers_before` is `pr.md`'s `## Answers` as a `pr` rerun found it, or `impl.md`'s
        as an `impl` step found it; a `done` that no longer ends with
        it says `answers_lost`.
        """

        def tell(item: tuple[str, Any]) -> None:
            self.tell(running, item)

        told_done = False
        # What the attempt ends with.
        outcome = "failed"
        # `after_end` runs last, only on this.
        ended_done = False
        recorder = running.handle.recorder
        # A cancel with no Stop behind it is the app going down.
        going_down = False
        # Paused by an update: the next start takes the step up in the directory it had.
        suspended = False
        try:
            if recorder is not None:
                recorder.start()
            if scratch is not None and not resumed:
                # A spike taken up again goes on in the directory it had.
                shutil.rmtree(scratch, ignore_errors=True)
                scratch.mkdir(parents=True)
            async for item in runner.run(**kwargs, running=running):
                if item[0] == "done":
                    item = ("done", {**item[1], "base": base})
                    if item[1].get("outcome") != "stopped":
                        item = (
                            "done",
                            {**item[1], **await self.ingest(cwd, unit, item[1], artifact)},
                        )
                    if rounds_before is not None and item[1].get("outcome") == "done":
                        # After `Runner` has written `review.md`, never
                        # before: the artifact does not wait on GitHub.
                        item = (
                            "done",
                            {
                                **item[1],
                                "comments": await self.post_new_rounds(cwd, unit, rounds_before),
                            },
                        )
                    if (
                        answers_before is not None
                        and item[1].get("outcome") == "done"
                        and not _answers_kept(directory / artifact, answers_before)
                    ):
                        item = ("done", {**item[1], "answers_lost": True})
                    told_done = True
                    outcome = str(item[1].get("outcome") or "done")
                tell(item)
                if item[0] == "done" and item[1].get("outcome") == "done":
                    ended_done = True
        except RunError as e:
            tell(("raise", Invalid(str(e))))
            told_done = True
        except Suspended:
            # An update paused the step and wrote its `suspend` row; like the app
            # going down, nothing is ended, nudged or recorded as a transition here.
            going_down = suspended = True
        except asyncio.CancelledError:
            if not running.stop_requested:
                going_down = True
                raise
            # A Stop's cancel that arrived after the runner had already ended.
            task = asyncio.current_task()
            if task is not None:
                task.uncancel()
        except Exception as e:  # noqa: BLE001 - the reader raises it, as it always did
            tell(("raise", e))
            told_done = True
        finally:
            if not told_done:
                tell(
                    (
                        "raise",
                        Invalid(
                            f"{unit}'s {stage} step ended without an outcome; the app may be shutting down"
                        ),
                    )
                )
            if self.tasks.get(running.attempt) is running:
                del self.tasks[running.attempt]
            task = asyncio.current_task()
            if task is not None:
                # Until this task ends -- its recorder, `after_end` -- an Apply's settle and
                # `shutdown` wait for it.
                self.holds.finishing[running.attempt] = (
                    {
                        "workspace": running.workspace,
                        "unit": running.unit,
                        "stage": stage,
                        "started": running.started_at,
                    },
                    task,
                )
            try:
                if scratch is not None and not suspended:
                    shutil.rmtree(scratch, ignore_errors=True)
                if not going_down:
                    # Ended before the board read `after_end` costs, so the pass it wakes and
                    # the reader's `done` find the unit free. The app going down leaves the
                    # attempt as it is, for the next start to take up or end.
                    self.end_attempt(running.attempt, outcome)
                if recorder is not None and not recorder.closed:
                    # The runner closes it on every road that writes an `end`. Left open means the
                    # app is going down -- what can be written is, with no `end` -- or the
                    # runner raised before its own `finally`, which is an ending like any other.
                    if going_down:
                        await recorder.abandon()
                    else:
                        await recorder.close("failed", "the step ended without an outcome")
                if recorder is not None:
                    self.recorders.pop(recorder.run, None)
                if ended_done and not going_down and stage not in NOT_STEPS:
                    # After the runner's `end`, which it writes before it yields `done`, and
                    # after the attempt ended: the board read it costs holds neither the
                    # reader's `done` nor the unit.
                    await self.after_end(cwd, unit, stage, running.workspace)
            finally:
                self.holds.finishing.pop(running.attempt, None)

    def end_attempt(self, attempt: int, outcome: str) -> None:
        """`ended(outcome)`; `stop_late` for a step that a Stop reached once it was `ending`."""
        row = self.holds.attempts.get(attempt)
        if row is None or row["state"] in ("ended", "refused"):
            return
        if row["stop_asked_at"] and row["state"] == "ending" and outcome != "stopped":
            outcome = "stop_late"
        self.holds.attempts.move(attempt, "ended", outcome)

    async def stop_step(self, cwd: str, unit: str, by: str) -> dict[str, Any]:
        """Stop one running board step. The route and the page's
        button both call this, and nothing else.

        `by` is a name the person typed, not an identity: the password names nobody. What it
        leaves is an `end` record with `outcome: stopped` and `stopped_by` -- or nothing at
        all when the cancel lands before the step's first turn (`never_driven`). It opens
        and closes no gate, and starts nothing.
        """
        self.ws.check(cwd)
        name = (by or "").strip() or OWNER
        return await self.stop_running(self.ws.key(cwd), unit, name)

    async def stop_running(self, key: str, unit: str, by: str) -> dict[str, Any]:
        """The Stop itself, recorded on the unit's attempt whatever its state:

        - `queued`: `ended(stopped)` now, with no git and no session touched;
        - `preparing`: its task is cancelled, which kills the `git` it was in;
        - a step `running`: its session is closed and its task cancelled, an `end` record
          with `stopped` and `stopped_by` -- or none for one cancelled before its first turn;
        - a step `ending`: it runs to its end, which records `stop_late`;
        - an integration `running`: it stops between two mechanical steps, never in one.
        """
        attempts = self.holds.attempts
        row = attempts.holding(key, unit)
        if row is None:
            raise Invalid(f"{unit} has no step running")
        if row["machine"] not in STOPPABLE:
            raise Invalid(describe(unit, row))
        row = attempts.ask_stop(row["id"], by)
        running = self.tasks.get(row["id"])
        said = {"unit": unit, "stage": row["stage"], "stopped_by": row["stop_asked_by"]}
        state = row["state"]
        if state == "queued":
            if running is not None:
                del self.tasks[row["id"]]
                self.tell(
                    running,
                    (
                        "raise",
                        Invalid(
                            f"{unit}'s {row['stage']} step was stopped before it began; nothing ran"
                        ),
                    ),
                )
            attempts.move(row["id"], "ended", "stopped")
            return said
        if running is None or row["machine"] == "integration" or state == "ending":
            # Read at the next safe point, or recorded as `stop_late` when it ends.
            return said
        if not running.stop_requested:
            # The first name stays: two presses are one stop, with one person behind it.
            running.stop_requested = True
            running.stopped_by = row["stop_asked_by"] or by
        await running.handle.close()
        if running.task is not None:
            running.task.cancel()
        return said

    def running_steps(self, cwd: str) -> list[dict[str, Any]]:
        """The board steps and integrations of this workspace not yet ended, read from their
        attempts: `state` is `queued`, `preparing`, `running` or `ending`, and `stopping` a
        Stop recorded on it. A step is `kind: "step"`, an integration `kind: "integration"`
        with no `run`."""
        self.ws.check(cwd)
        return [
            {
                "unit": r["unit"],
                "stage": r["stage"],
                "started_at": r["since"],
                "state": r["state"],
                "stopping": bool(r["stop_asked_at"]),
                "run": r["run"] if r["machine"] == "step" else None,
                "kind": r["machine"],
            }
            for r in self.holds.attempts.unfinished(self.ws.key(cwd))
            if r["machine"] in STOPPABLE
        ]

    def unadoptable(self, machine: str, key: str, unit: str) -> str:
        """Why the unit's attempt cannot be taken up by a resumed `machine`, or `""`: none, or
        the one the last process left `running`, is what Resume takes up."""
        row = self.holds.attempts.holding(key, unit)
        if row is None or (
            row["machine"] == machine and row["state"] == "running" and row["id"] not in self.tasks
        ):
            return ""
        return describe(unit, row)

    def adopt(self, machine: str, key: str, unit: str, stage: str) -> int:
        """The attempt a resumed step or integration goes on in: the one left `running`, or a
        new one in `running` when the process that went down had none (an older build)."""
        problem = self.unadoptable(machine, key, unit)
        if problem:
            raise Invalid(problem)
        row = self.holds.attempts.holding(key, unit)
        if row is not None:
            return int(row["id"])
        return int(self.holds.attempts.open(machine, key, unit, stage, state="running")["id"])

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
        attempt = self.adopt("step", key, unit, stage)
        running = steps_mod.Running(
            workspace=key, unit=unit, stage=stage, started_at=_now(), attempt=attempt, cwd=cwd
        )
        self.tasks[attempt] = running
        self.seal_attempt(running)
        run = uuid.uuid4().hex
        recorder = events.Recorder(
            run, Data(self.config.data_dir), str(journal.working_dir), key, unit, stage
        )
        running.run, running.handle.recorder = run, recorder
        self.recorders[run] = recorder
        self.holds.attempts.set_run(attempt, run)
        rounds = set(owner["rounds_before"]) if owner.get("rounds_before") is not None else None
        end_fields = None
        if rounds is not None:

            async def end_fields() -> dict[str, Any]:
                return await self.findings_added(cwd, unit, rounds)

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
            agent=self.agent_of(stage),
            end_fields=end_fields,
            read_also=tuple(owner.get("read_also") or ()),
            resume=record,
            owner_extra=extra,
            **({"watch": owner["watch"]} if owner.get("watch") else {}),
        )
        scratch = Path(owner["scratch"]) if owner.get("scratch") else None
        running.task = asyncio.create_task(
            self.drive(
                running,
                Runner(self.sessions, journal, app=self.identity(), hooks=self.hooks),
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
        running.task.add_done_callback(lambda _task: self.never_driven(running))
        return running
