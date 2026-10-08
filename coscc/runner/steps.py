"""Running, driving, stopping and taking up again one step of a unit, and the attempts every
machine shares: its click, its readers, its task and its end."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, AsyncIterator, NotRequired, TypedDict
from collections.abc import Awaitable, Callable, Coroutine

from coscc import units
from coscc.agent import models, modeltrial, pack, transcript
from coscc.agent import steps as steps_mod
from coscc.agent.sessions import Sessions, Suspended
from coscc.bus import Bus
from coscc.config import Config
from coscc.git import drift, fetches, gitops
from coscc.kernel import OWNER, Facts, Grant, Hooks, Invalid, facts as facts_of
from coscc.runlog import events
from coscc.runner.attempt import describe_attempt
from coscc.runner.queue import MACHINES, STOPPABLE, Attempt, Holds, Refused, describe
from coscc.runner.reply import RunError
from coscc.runner.run import LIVE
from coscc.runner.step import Runner, check_started_by
from coscc.git.gitops import GitError
from coscc.store.db import Busy, Data, in_thread, now as _now
from coscc.store.journal import NOT_STEPS, MERGE_RECORD, BadRecord, Journal, paused_stage
from coscc.units import backlog, mentions, planmap, retake, states, worktrees
from coscc.units import board as board_reader
from coscc.units import BadUnit, CannotCreate
from coscc.units.board import Unavailable
from coscc.units.contracts import ContractError, Plan, missing
from coscc.units.ideas import Ideas
from coscc.units.read import BUDGET_REACHED, HoldView
from coscc.units.workspaces import Workspaces
from coscc.units.worktrees import BRANCH_REMOTE, BRANCH_TRUNK, describe_base

log = logging.getLogger(__name__)


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


def step_cwd(
    stage: str,
    work: str,
    directory: Path,
    scratch: str | None = None,
    process: str | None = None,
) -> str:
    """Where a step's session runs: the unit's worktree, except for a `merge` and a `scratch` one.

    A state whose output is written `by: scratch` runs in `scratch`, a throwaway directory under
    the data root, so its probe code never lands in the worktree whose branch it would ride.

    A `merge` runs `gh pr merge --squash --delete-branch`, which inside a worktree merges and then
    fails (gh tries to switch the worktree to `main`, git refuses, exit 1, branches left
    behind). Run from a non-git directory with the PR URL it merges and deletes the remote
    branch; the unit's store directory is such a directory. `worktrees.remove_if_finished`
    removes the worktree and local branch once GitHub says `MERGED`. The gates still read `work`.
    """
    if scratch and states.by_of(process, stage) == "scratch":
        return scratch
    return str(directory) if states.action_of(process, stage) == "merge" else work


def _agent_key(process: str, stage: str) -> str:
    """The one agent row the process binds `stage` to, `""` for an engine action; a state that is
    neither is gone: `Refused` `no-stage`."""
    key = pack.agent_for(process or pack.DEFAULT_PROCESS, stage) or ""
    if not key and not states.action_of(process, stage):
        raise Refused(
            f"{stage} is no state of {process or pack.DEFAULT_PROCESS} that runs an agent",
            ("no-stage",),
        )
    return key


def _gate_reasons(answer: board_reader.Gate) -> tuple[str, ...]:
    """The codes that go with the gate's words, so no reader downstream parses these. A stand-in
    gate that answers a plain pair has none."""
    return tuple(getattr(answer, "reasons", ()))


def _rounds_before(
    found: dict[str, Any], stage: str, process: str | None = None
) -> set[Any] | None:
    """The rounds a review state's artifact held before this step, so that the ones it adds can be
    told apart afterwards. Taken from the board already read; None for any other state."""
    if states.kind_of(process, stage) != "review":
        return None
    return {r.get("n") for r in found.get("rounds") or []}


def _round_kwargs(
    found: dict[str, Any],
    stage: str,
    rounds_before: set[Any] | None,
    process: str | None = None,
) -> dict[str, Any]:
    """From the same board: a last round the loop read as unfinished, and the ids it dropped, for
    the review that runs again. Whether it counts is not asked here."""
    last_round = (
        (found.get("rounds") or [None])[-1] if states.kind_of(process, stage) == "review" else None
    )
    kw: dict[str, Any] = {}
    if last_round and last_round.get("unfinished"):
        kw["unfinished_round"] = {"n": last_round["n"], "dropped": list(last_round["dropped"])}
    # The findings the last round left open, which an `impl` may claim only a
    # person can close: guard `impl-claim` reads them when its object arrives.
    if rounds_before:
        kw["rounds_known"] = tuple(sorted(n for n in rounds_before if isinstance(n, int)))
    if states.by_of(process, stage) == "session" and found.get("rounds"):
        last = found["rounds"][-1]
        kw.update(open_ids=tuple(last.get("open_ids") or ()), claims_round=last.get("n"))
    return kw


async def _plan_drift(
    journal: Journal,
    key: str,
    unit: str,
    stage: str,
    plan: Plan | None,
    tree: dict[str, Any] | None,
    process: str | None = None,
) -> dict[str, Any] | None:
    """Which files the plan's record names `main` changed since the plan ran, for a state whose
    agent reads `drift` only. Unlike `failed_attempts`, nothing here may refuse the step: a busy run log or a bug
    in `drift.py` is "could not check"."""
    if "drift" not in states.data_of(process, stage):
        return None
    try:
        return await drift.compute(
            journal.records(key, unit),
            plan["files"] if plan else None,
            tree["path"] if tree else None,
            states.states_with_field("files"),
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


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _answers_of(meta: dict[str, Any]) -> list[tuple[str, str]]:
    """`(artifact, answer)` for each answer `cos.db` holds for the unit."""
    return [(str(a.get("artifact")), str(a.get("text") or "")) for a in meta.get("answers") or []]


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
        # What the gate says of the offered stage when it is closed (asked only for a page).
        "gate": NotRequired[str],
    },
)


RAISED = (
    "A person raised your ceiling, to {ceiling}. You stopped at it with the work unfinished; "
    "carry on with it."
)


def _raised(end: dict[str, Any], ceilings: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """`(record, raised)` to take up the session a `paused-budget` `end` kept under `ceilings`
    (`{usd?, turns?}`), shaped like a `suspend` row so `resume_step` takes it up; `raised` is
    what the `raise` row says. The ceiling the run hit must be higher than it was."""
    given = ceilings if isinstance(ceilings, dict) else {}
    old = {"usd": end.get("max_budget_usd"), "turns": end.get("max_turns")}
    new = dict(old)
    for field, name in (("usd", "budget"), ("turns", "turns")):
        if given.get(field) is not None:
            new[field], why = models.check(name, given[field])
            if why:
                raise Invalid(why)
    hit = str(end.get("ceiling") or "")
    if hit not in new or float(new[hit] or 0) <= float(old[hit] or 0):
        word = "$ ceiling" if hit == "usd" else "turn ceiling"
        raise Invalid(f"raise the {word} it hit: it paused at {old.get(hit)}")
    sid, cwd = str(end.get("session_id") or ""), str(end.get("cwd") or "")
    path = transcript.path_for(cwd, sid)
    if not sid or not path.is_file():
        raise Invalid(f"its session cannot be taken up: its transcript is not in {path.parent}")
    try:
        edge = transcript.boundary(path)
        found = transcript.cut(path, edge)
    except (transcript.Unreadable, OSError) as e:
        raise Invalid(
            f"its session cannot be taken up: its transcript could not be read: {e}"
        ) from e
    if not found["safe_uuid"]:
        raise Invalid("its session cannot be taken up: its transcript holds no point to go on from")
    if hit == "turns" and int(new["turns"] or 0) <= found["api_calls"]:
        raise Invalid(f"raise the turn ceiling above the {found['api_calls']} turns it used")
    spent = end.get("session_cost_usd", end.get("cost_usd"))
    if hit == "usd" and spent is not None and float(new["usd"] or 0) <= float(spent):
        raise Invalid(f"raise the $ ceiling above the ${float(spent):g} it spent")
    sources = {
        f"max_{name}_source": "raise"
        for name, field in (("budget", "usd"), ("turns", "turns"))
        if given.get(field) is not None
    }
    owner = {
        **(end.get("owner") or {}),
        "max_turns": new["turns"],
        "max_budget_usd": new["usd"],
        **sources,
    }
    said = ", ".join(
        f"${new['usd']:g}" if k == "usd" else f"{new['turns']} turns"
        for k in ("usd", "turns")
        if given.get(k) is not None
    )
    record = {
        "owner": owner,
        "cwd": cwd,
        "session_id": sid,
        "run": end.get("run"),
        "model": end.get("model"),
        "start_at": owner.get("start_at"),
        "boundary": edge,
        "safe_uuid": found["safe_uuid"],
        "dropped": [],
        "api_calls": found["api_calls"],
        "pieces": found["pieces"],
        "message": RAISED.format(ceiling=said),
        "raised": True,
        **({"spent_usd": float(spent)} if spent is not None else {"cost_unknown": True}),
    }
    return record, {
        "from_usd": old["usd"],
        "from_turns": old["turns"],
        "max_budget_usd": new["usd"],
        "max_turns": new["turns"],
    }


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
        worktree: Callable[..., Awaitable[dict[str, Any] | None]],
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
        self.worktree = worktree
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
            if states.action_of(str(found.get("process") or ""), stage) == "merge" and result:
                journal.append(
                    {
                        "kind": MERGE_RECORD,
                        "workspace": key,
                        "unit": unit,
                        "stage": stage,
                        "result": result,
                    }
                )
        except Exception:
            # `Unavailable`, `BadRecord`, `Busy` included.
            log.exception("what follows the %s of %s was not done", stage, unit)
            return

    async def next_step(self, cwd: str, unit: str, with_gate: bool = False) -> NextStep:
        """The one stage the run button may offer, and why -- `coscc.loop next`'s answer.

        Read with the same store and the same `repo=cwd` that `run_step` hands the gate, so
        the stage offered and the gate that will be asked read one checkout. Nothing here chooses a stage.
        `with_gate` also asks the gate of the stage offered and adds what it says when closed.
        """
        self.ws.check(cwd)
        if not unit:
            raise Invalid("name a work unit")
        self.ws.unit_dir(cwd, unit)
        state = await in_thread(self.ws.snapshot, cwd, [unit])
        own = state["units"].get(f"{state['workspace']}/{unit}") or {}
        # A unit with a hold row is asked first with no `--repo`, which reads files only: a held
        # unit is answered here, before `worktree` could reopen the tree a drop just removed.
        # One with none cannot be held, and is not asked twice.
        held: dict[str, Any] = {}
        if own.get("holds"):
            try:
                held = await board_reader.next_step(
                    self.ws.units_root(cwd), unit, repo=None, state=state
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
                self.ws.units_root(cwd), unit, repo=repo, state=state
            )
        except Unavailable as e:
            raise Refused(str(e), ("unavailable",)) from e
        closed = ""
        if with_gate and found["stage"] and not found["blocked"]:
            try:
                answer = await board_reader.gate(
                    self.ws.units_root(cwd),
                    unit,
                    found["stage"],
                    repo=repo,
                    state=state,
                )
            except Unavailable as e:
                closed = str(e)
            else:
                closed = "" if answer[0] else str(answer[1])
        return {
            "cwd": cwd,
            "unit": unit,
            "stage": found["stage"],
            "action": str(found["action"]),
            "blocked": bool(found["blocked"]),
            "gate": closed,
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
        names = [r["stage"] for r in found["stages"]]
        if stage not in names:
            raise Invalid(f"no such stage: {stage} (use one of {', '.join(names)})")

        try:
            journal.set_mode(self.ws.key(cwd), unit, stage, mode)
        except BadRecord as e:
            raise Invalid(str(e)) from e
        except Busy as e:
            raise Invalid(str(e)) from e
        self.bus.publish("mode.set", {"workspace": self.ws.key(cwd), "unit": unit})
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
        stage may, and the records it makes stale (a `rerun` row written before the session
        starts), are `coscc.loop rerun`'s. Refused for the autopilot and for a note over
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

    async def raise_step(
        self,
        cwd: str,
        unit: str,
        stage: str,
        ceilings: Any,
        started_by: str = "person",
    ) -> AsyncIterator[tuple[str, Any]]:
        """Go on with the session `unit`'s `stage` paused at a ceiling, under the raised ceilings
        `{usd?, turns?}`: the same session, with the new ceiling less what it spent. Only a
        person raises one; the autopilot never does. The ceiling it hit must be raised."""
        try:
            check_started_by(started_by)
        except ValueError as e:
            raise Invalid(str(e)) from e
        if started_by != "person":
            raise Refused("a ceiling is raised only by a person", ("rerun-by-person",))
        self.ws.check(cwd)
        self.refuse_updating()
        journal = self.ws.journal()
        if journal is None:
            raise Refused(
                "no working folder is set, so a run cannot be recorded — set COS_WORKING_DIR",
                ("no-run-log",),
            )
        await self._find_stage(cwd, unit, stage)  # refuses a held unit
        key = self.ws.key(cwd)
        rows = await in_thread(journal.records, key, unit, kinds=("start", "end", "raise"))
        last = next((r for r in reversed(rows) if r.get("stage") == stage), None)
        if last is None or last.get("kind") != "end" or last.get("outcome") != "paused-budget":
            raise Invalid(
                f"{unit}'s {stage} is not paused at a ceiling, so there is nothing to raise"
            )
        record, raised = _raised(last, ceilings)
        directory = self.ws.unit_dir(cwd, unit)
        owner = record["owner"]
        refusal = self.feature_refusal(
            facts_of(
                workspace=cwd,
                workspace_key=key,
                unit=unit,
                agent=stage,
                run="",
                cwd=str(owner.get("scratch") or owner.get("tree") or cwd),
                watch=owner.get("watch"),
                directory=directory,
                resumed=True,
            )
        )
        if refusal:
            raise Refused(refusal, ("feature-refused",))
        running = self.resume_step(record, {**raised, "by": OWNER})
        queue: asyncio.Queue = asyncio.Queue()
        running.listeners.add(queue)
        async for item in self._follow(running, queue):
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
            proc = str(found.get("process") or "")
            agent_key = _agent_key(proc, stage)
            # A stage held at a ceiling runs from scratch only on a person's rerun; there is no
            # accepted artifact to make stale, so the loop is not asked.
            paused = paused_stage(await in_thread(journal.timeline, key, unit), stage)
            if paused is not None and not rerun:
                raise Refused(
                    f"{unit}'s {stage} paused at its ceiling: raise it to go on, or rerun it "
                    "from scratch",
                    (BUDGET_REACHED,),
                )
            asked_rerun = rerun and paused is None
            stale = (
                await self._ask_rerun(cwd, unit, stage, started_by, note, paused is None)
                if rerun
                else {}
            )
            tree, work = await self._open_tree(cwd, unit, stage, proc)
            base = await self._tree_base(cwd, unit, tree)
            answer = await self._ask_gate(cwd, unit, stage, work, proc)
            refusal = self.feature_refusal(
                facts_of(
                    workspace=cwd,
                    workspace_key=key,
                    unit=unit,
                    agent=agent_key,
                    run="",
                    cwd=work,
                    watch=None,
                    directory=self.ws.unit_dir(cwd, unit),
                    resumed=False,
                    # A step the PR machine does itself pushes the unit's branch: the guards see
                    # that push. A session's own grant is issued once it opens (`Runner`).
                    grant=Grant(branch=str((tree or {}).get("branch") or "HEAD"))
                    if states.action_of(proc, stage)
                    else None,
                    action=states.action_of(proc, stage),
                )
            )
            if refusal:
                raise Refused(refusal, ("feature-refused",))

            # `pr` and `ship` run no session: the PR machine pushes, opens or merges, and
            # records each move through its guard. `ending` from the start: a Stop after
            # this point never cuts a push halfway.
            if states.action_of(proc, stage):
                self.holds.attempts.move(running.attempt, "running")
                self.holds.attempts.move(running.attempt, "ending")
                done = await self._run_mechanical(
                    cwd, key, unit, stage, tree, started_by, rerun, stale, answer, proc
                )
                self.tell(running, ("done", done))
                handed = True
                outcome = done.get("outcome") if isinstance(done, dict) else None
                self.close(running, None, str(outcome or "done"))
                return

            # What the step is handed.
            screens_note = await self._ready_tree(
                cwd, key, journal, unit, stage, tree, work, started_by, proc
            )
            directory = self.ws.unit_dir(cwd, unit)
            rounds_before = _rounds_before(found, stage, proc)
            inputs = await self._gather_inputs(
                cwd=cwd,
                key=key,
                journal=journal,
                unit=unit,
                stage=stage,
                found=found,
                tree=tree,
                work=work,
                rounds_before=rounds_before,
                process=proc,
                agent_key=agent_key,
            )
            inputs.update(await self._link_kwargs(cwd, unit, stage, proc))
            self.refuse_unready(agent_key, directory, inputs.get("meta"))
            if asked_rerun:
                self._record_rerun(cwd, unit, stage, stale)
            # Emptied before the step, whatever an earlier one left, and removed
            # after it however it ends -- in `drive`, so a client that drops the stream does
            # not decide when.
            scratch = (
                units.spike_dir(cwd, unit, self.config.data_dir)
                if states.by_of(proc, stage) == "scratch"
                else None
            )
            kwargs = self._step_kwargs(
                cwd=cwd,
                key=key,
                unit=unit,
                stage=stage,
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
                process=proc or pack.DEFAULT_PROCESS,
                agent_key=agent_key,
                branch=await self._unit_branch(cwd, unit)
                if states.by_of(proc, stage) == "session" and tree
                else "",
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
                base=base,
                rounds_before=rounds_before,
                scratch=scratch,
                kwargs=kwargs,
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
                f"no such stage: {stage} (use one of {', '.join(r['stage'] for r in found['stages'])})",
                ("no-stage",),
            )
        # the loop's own field, read before any worktree is opened — the gate
        # below would refuse too, but only after `worktree` had reopened a dropped tree.
        held = found.get("hold")
        if held:
            raise Refused(
                f"{unit} is {held.get('state')}: {held.get('reason')} — nothing runs on it",
                (str(held.get("code") or "held"),),
            )
        return data, found, row

    @staticmethod
    def _check_rerun(started_by: str, note: str) -> None:
        """A rerun is a person's, and its note is short."""
        if started_by != "person":
            raise Refused(
                "a stage is run again only by a person, from the board, never by the autopilot",
                ("rerun-by-person",),
            )
        if len(note) > RERUN_NOTE_MAX:
            raise Invalid(
                f"the note is {len(note)} characters, over the {RERUN_NOTE_MAX} a rerun takes"
            )

    async def _ask_rerun(
        self, cwd: str, unit: str, stage: str, started_by: str, note: str, ask: bool = True
    ) -> dict[str, int]:
        """`{file: record}` running `stage` again makes stale, asked before a worktree is opened
        or the gate asked. Whether `stage` may run again, and what it makes stale, are the
        loop's; its refusal is passed on. A stage held at a ceiling has no accepted artifact to make
        stale, so with `ask` false only the person and the note are checked."""
        self._check_rerun(started_by, note)
        if not ask:
            return {}
        try:
            asked = await board_reader.rerun(
                self.ws.units_root(cwd), unit, stage, state=self.ws.snapshot(cwd, [unit])
            )
        except Unavailable as e:
            raise Refused(str(e), ("unavailable",)) from e
        if "error" in asked:
            raise Invalid(str(asked["error"]))
        return dict(asked.get("stale") or {})

    def _record_rerun(self, cwd: str, unit: str, stage: str, stale: dict[str, int]) -> None:
        """The person's rerun, one `rerun` row, written where the step starts: the loop reads
        each artifact it names as stale while it holds the same record."""
        self.ws.unit_meta().add_decision(
            self.ws.key(cwd),
            unit,
            "rerun",
            {"stage": stage, "stale": stale},
            OWNER,
            date.today().isoformat(),
            "product",
        )

    async def _open_tree(
        self, cwd: str, unit: str, stage: str, process: str | None = None
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
        # A scratch step is watched through the worktree's `HEAD` and `git status`;
        # with no git there is nothing to watch, so it does not run at all.
        if states.by_of(process, stage) == "scratch" and tree is None:
            raise Refused(
                f"{stage} needs a git worktree to watch, and this workspace is not a git repository",
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

    def refuse_unready(self, stage: str, directory: Path, meta: Any) -> None:
        """`Refused` before any spend: `agent-invalid` when the stage's row cannot run (a bad
        owner's file; an edited row `pack.check` refuses with the catalog, as a built-in one
        would have stopped the build; a stage with no row opens no session), `input-missing`
        when its declared input is not there yet."""
        found = pack.row(stage)
        effects = {n: t.effect for n, t in self.hooks.catalog().items()}
        bad = pack.problems(stage, effects if pack.needs_catalog(found) else None) if found else []
        if bad:
            raise Refused(
                f"{stage} cannot start: its agent's row cannot run: {'; '.join(bad)}",
                ("agent-invalid",),
            )
        lacks = missing(stage, directory, meta)
        if lacks:
            raise Refused(
                f"{stage} cannot start: it needs {' and '.join(lacks)}, and this unit has "
                "none yet.",
                ("input-missing",),
            )

    def feature_refusal(self, facts: Facts) -> str:
        """`Hooks.refusal`: the first feature guard that denies the run, or `""`."""
        return self.hooks.refusal(facts)

    async def _ask_gate(
        self, cwd: str, unit: str, stage: str, work: str, process: str | None = None
    ) -> board_reader.Gate:
        """`coscc.loop gate` is asked here, not left to the skill: a session often cannot run
        a command. Here rather than in `Runner` because a refusal must arrive before any
        money is spent, and `run_step` is the last place that is still true."""
        # `pr.md`'s title and body go up before the `ship` gate compares the
        # title, so one a person changed on GitHub, or a `pr` step left behind, does not
        # close it. Never raises; when it fails, the gate decides.
        if states.action_of(process, stage) == "merge":
            await self.sync_pr(cwd, unit, None, stage=stage)
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
        stale: dict[str, int],
        answer: board_reader.Gate,
        process: str | None = None,
    ) -> dict[str, Any]:
        """One `pr` or `ship` through the PR machine, and the `done` item it ends with.
        A `pr` run again records its rerun as any other, and the note reaches no prompt: the
        PR machine records `open` again and writes `pr.md` from the unit's metadata."""
        if rerun:
            self._record_rerun(cwd, unit, stage, stale)
        return await self.mechanical(
            cwd,
            key,
            unit,
            stage,
            tree,
            started_by,
            again=rerun,
            rebased=getattr(answer, "rebased", None),
            process=process,
            passed=getattr(answer, "passed", ()),
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
        process: str | None = None,
    ) -> str:
        """Refuse a tree the step cannot start on, before any money is spent. Returns the
        section the `review` prompt carries about its screenshots, `""` for any other step."""
        if tree is None:
            return ""
        if states.by_of(process, stage) == "session":
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
        if "screens" in states.data_of(process, stage):
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
        found: dict[str, Any],
        tree: dict[str, Any] | None,
        work: str,
        rounds_before: set[Any] | None,
        process: str | None = None,
        agent_key: str = "",
    ) -> dict[str, Any]:
        """What the stage is handed beyond who runs it and where: keyword arguments for
        `Runner.run`. Only a busy run log refuses the step here; every other read that fails is
        recorded as the reason."""
        mode = journal.modes(key).get((unit, stage), "manual")
        round_kw = _round_kwargs(found, stage, rounds_before, process)
        try:
            plan = self.ws.unit_meta().plan(key, unit)
        except (Busy, ContractError) as e:
            raise Refused(str(e), ("unavailable",)) from e
        config, failed = await self._stage_config(
            cwd, key, journal, unit, stage, str(found.get("process") or ""), plan, work, agent_key
        )
        integration_note = (
            self.integration_note(journal, key, unit)
            if "integration" in states.data_of(process, stage)
            else ""
        )
        plan_drift = await _plan_drift(journal, key, unit, stage, plan, tree, process)
        # The files the plan's record names, as they stand in the tree the step runs
        # on, for `impl` only. The same again: nothing in `for_step` may refuse the step.
        plan_kw = (
            planmap.for_step(plan["files"] if plan else [], work)
            if "plan-map" in states.data_of(process, stage)
            else {}
        )
        shortlist = _shortlist(journal, key, unit)
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
            **plan_kw,
            plan=plan,
        )

    async def _stage_config(
        self,
        cwd: str,
        key: str,
        journal: Journal,
        unit: str,
        stage: str,
        process: str,
        plan: Plan | None,
        work: str,
        agent_key: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """The stage's configuration and the failed attempts before this run. Resolved after
        the gate, so a refused step reads nothing more: the plan's label, the effort and, for
        `impl`, which run this is, read before any money is spent. `Runner` does not read the
        run log itself; `build_prompt` only places what it is handed, the same as `base_note`."""
        try:
            config = self.stage_config(stage, process, plan, journal, key, unit, agent_key or None)
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

    async def _link_kwargs(
        self, cwd: str, unit: str, stage: str, process: str | None = None
    ) -> dict[str, Any]:
        """The keyword arguments the database and the unit's idea add to the prompt."""
        # The answers and holds the prompt renders, from the database.
        link_kw: dict[str, Any] = {"meta": self.ws.meta_of(cwd, unit)}
        # The snapshot a step that runs the loop itself hands `--state` (the `ship` step's
        # gate), which refuses to decide without one. Written as the
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
        # Only for a unit opened from an idea, and only for a state whose agent reads `idea` or
        # `siblings`; every other step is handed no key.
        data = states.data_of(process, stage)
        if "idea" in data:
            idea_note = self.ideas.idea_note(cwd, unit)
            if idea_note:
                link_kw["idea_note"] = idea_note
        if "siblings" in data:
            siblings_note = await self.ideas.siblings(cwd, unit)
            if siblings_note:
                link_kw["siblings_note"] = siblings_note
        # Every stage is told where `idea.md` and `intent.md` of the units its unit names are.
        directory = self.ws.unit_dir(cwd, unit)
        mentions_note = mentions.for_step(
            cwd,
            unit,
            _read_text(directory / states.brief_file()),
            _answers_of(link_kw["meta"]),
            link_kw["meta"],
            self.ws.name(cwd),
            self.ws.all()["workspaces"],
            self.config.data_dir,
        )
        if mentions_note:
            link_kw["mentions_note"] = mentions_note
        return link_kw

    async def _unit_branch(self, cwd: str, unit: str) -> str:
        """The unit's branch as the loop names it (`unit-branch`): the one push an impl's grant
        holds, whatever its worktree's `HEAD` says. `""` when the loop cannot name it: no push."""
        try:
            name = await in_thread(
                units.branch_name, cwd, unit, self.config.data_dir, self.ws.snapshot(cwd, [unit])
            )
        except CannotCreate, BadUnit:
            return ""
        return name if gitops.unit_branch(name) else ""

    def _step_kwargs(
        self,
        *,
        cwd: str,
        key: str,
        unit: str,
        stage: str,
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
        process: str,
        agent_key: str,
        app_note: str = "",
        branch: str = "",
    ) -> dict[str, Any]:
        """The keyword arguments `Runner.run` is called with: who runs what, where, what the
        gate said, the `inputs` gathered, and the unit's `branch`, the one push its grant holds."""
        return dict(
            workspace=cwd,
            directory=directory,
            journal_key=key,
            unit=unit,
            stage=stage,
            artifact=artifact,
            gate_said=answer[1],
            gate_reasons=_gate_reasons(answer),
            lane=getattr(answer, "lane", "full"),
            cwd=step_cwd(stage, work, directory, str(scratch) if scratch else None, process),
            base=base,
            base_note=describe_base(base),
            screens_note=screens_note,
            # The row the unit's process binds the state to, as it stands, read once as the step
            # starts: a rename later reaches the next step, not this one.
            agent=self.agent_of(agent_key),
            agent_key=agent_key,
            process=process,
            **inputs,
            # Only named for a spike, so a stand-in `run` without it keeps working.
            **({"watch": work} if scratch is not None else {}),
            # The same: `Runner.run` writes `person` when it is not named.
            **({"started_by": started_by} if started_by != "person" else {}),
            # The same again: only a rerun names them.
            **({"rerun": True, "rerun_note": note} if rerun else {}),
            # And the autopilot's note, only when it wrote one.
            **({"app_note": app_note} if app_note else {}),
            # And the unit's branch, only for an impl on its tree.
            **({"branch": branch} if branch else {}),
            # What `resume_step` needs of this step, in its `suspend` row.
            owner_extra={
                "workspace_dir": cwd,
                "rounds_before": sorted(rounds_before) if rounds_before is not None else None,
                "tree": tree is not None,
                "watch": work if scratch is not None else None,
                "scratch": str(scratch) if scratch is not None else None,
                "branch": branch,
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
        base: dict[str, Any] | None,
        rounds_before: set[Any] | None,
        scratch: Path | None,
        kwargs: dict[str, Any],
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
        LIVE[run] = recorder
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
                base,
                rounds_before,
                scratch,
                kwargs,
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
                self.bus.publish("retake.ended", {"workspace": key, "unit": unit})
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
        LIVE.pop(running.run, None)
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
        base: dict[str, Any] | None,
        rounds_before: set[Any] | None,
        scratch: Path | None,
        kwargs: dict[str, Any],
        resumed: bool = False,
    ) -> None:
        """One board step, start to end, as its own task.

        Every item goes to the step's listeners with `put_nowait` -- this never waits on a
        reader -- and a `stopped` step records no transition, cleans nothing and posts nothing.
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
                if scratch is not None and not suspended and outcome != "paused-budget":
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
                    LIVE.pop(recorder.run, None)
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

    def resume_step(
        self, record: dict[str, Any], raised: dict[str, Any] | None = None
    ) -> steps_mod.Running:
        """A board step, as `run_step` hands one to `drive`: the unit claimed, a new
        recorder and `run`, and `Runner.run` with the row instead of a prompt. Synchronous up
        to the task, so the unit is held when this returns. `raised` is what a person's raise
        of a ceiling says (`by`, the old and the new ceilings): one `raise` row names the new
        `run`, so the run log shows one session in two parts."""
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
        LIVE[run] = recorder
        self.holds.attempts.set_run(attempt, run)
        if raised is not None:
            session_id = str(record.get("session_id") or "")
            journal.raised(key, unit, stage, run=run, session_id=session_id, **raised)
            # The new run's own `start`, so every run has one start and one end; `continues` tells
            # the run log's fold that the `raise` already reopened the row.
            journal.started(
                key,
                unit,
                stage,
                "manual",
                started_by="person",
                raised_by=raised["by"],
                continues=str(record.get("run") or ""),
                session_id=session_id,
                agent=stage,
                model=record.get("model"),
                head=owner.get("head"),
                run=run,
                pid=os.getpid(),
            )
        rounds = set(owner["rounds_before"]) if owner.get("rounds_before") is not None else None
        # The process the first start recorded; one from before units recorded theirs is the default.
        process = str(record.get("process") or pack.DEFAULT_PROCESS)
        agent_key = pack.agent_for(process, stage) or ""
        extra = {
            k: owner.get(k)
            for k in (
                "workspace_dir",
                "rounds_before",
                "tree",
                "watch",
                "scratch",
                "branch",
            )
        }
        kwargs: dict[str, Any] = dict(
            workspace=cwd,
            directory=directory,
            journal_key=key,
            unit=unit,
            stage=stage,
            artifact=artifact,
            mode="manual",
            cwd=str(record.get("cwd") or cwd),
            model=record.get("model"),
            effort=owner.get("effort"),
            label=owner.get("label"),
            agent=self.agent_of(agent_key),
            agent_key=agent_key,
            process=process,
            resume=record,
            owner_extra=extra,
            **({"watch": owner["watch"]} if owner.get("watch") else {}),
            # The branch the first start was granted, never the worktree's `HEAD` now.
            **({"branch": owner["branch"]} if owner.get("branch") else {}),
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
                None,
                rounds,
                scratch,
                kwargs,
                resumed=True,
            )
        )
        running.task.add_done_callback(lambda _task: self.never_driven(running))
        return running
