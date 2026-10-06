"""Running one step of one unit, and recording what it cost.

A step reads the step before it: the prompt is `intent.md` plus the previous stage's
artifact verbatim, and the names of what went in are recorded. The six prose stages get no
tools, so the session cannot write its own artifact: **the app holds the pen for `.cos/`,
and the session only returns text.**

A stage's rules come from **this app's own** skills, never the workspace's (a workspace is
a repository somebody cloned). `coscc/agent/harness.py` answers where they are, and a step
whose rules it cannot find does not run.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import uuid
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, AsyncIterator


from coscc import kernel
from coscc.agent import agents, instructions, models, modeltrial, pack, steps, transcript
from coscc.agent import sessions as sessions_mod
from coscc.agent.helpers import PROTOCOL, Denials, Gate, Helpers, definitions
from coscc.store.journal import Journal, Outcome
from coscc.units import scratch as scratch_mod
from coscc.units import submit as submit_mod
from coscc.agent.policy import (
    AGENT_TOOL,
    Grant,
    Row,
    record,
    row_for_step,
)
from coscc.units import guards
from coscc.agent.sessions import Refused, Sessions, Suspended

from coscc.runner import run as run_mod
from coscc.runner.prompt import _read, compose_prompt, submit_prompt, PROGRESS_FILE
from coscc.runner.review import (
    _round_number,
    _rounds,
    render_round,
    replace_new_rounds,
    UI_STANDARD,
)
from coscc.runner.reply import (
    RunError,
    OpeningError,
    opening_prompt,
    _Stopped,
    _with_reply,
    _title,
    _after_tool,
    _joined,
)
from coscc.runner.attempt import (
    snapshot,
    _head_of,
    _tree_state,
    describe_tree_change,
    _write_artifact,
)

log = logging.getLogger(__name__)

# Who started a step or an integration: the autopilot, or a request to a route (a person on
# the board, `curl`, or an agent at a terminal, which the app cannot tell apart).
STARTED_BY = ("person", "autopilot")


def check_started_by(value: str) -> str:
    """`value` when it is one of `STARTED_BY`; `ValueError` otherwise."""
    if value not in STARTED_BY:
        raise ValueError(f"started_by must be one of {', '.join(STARTED_BY)}, got {value!r}")
    return value


# # How many agent sessions one step starts. `Runner.run` makes exactly one `stream` call, so
# # this describes the code below rather than being a setting; Settings shows it per stage.
SESSIONS_PER_STEP = 1


# The kernel's own catalog names (`kernel.BUILTINS`).
_BUILTIN = frozenset(t.name for t in kernel.BUILTINS)

# # How long the repair turn may take. Rewriting a 26,535-character plan took about 94 s and
# # a spec 79 s; the text comes at once at the end of the turn, so a turn cut here leaves
# # nothing to write.
OPENING_TIMEOUT = 180.0


async def _opening_turn(
    sessions: Sessions,
    cwd: str,
    prompt: str,
    session_id: str,
    denials: Denials,
    **kw: Any,
) -> tuple[str, int, dict[str, Any] | None]:
    """The reply of one more turn on a prose step's own session, how many pieces of text it said,
    and its `done`.

    Same session id, a new handle with no recorder, no tools and a gate granting none, one turn.
    """
    text, blocks, done = "", 0, None
    async for kind, payload in sessions.stream(
        cwd,
        prompt,
        session_id,
        max_turns=1,
        gate=Gate(run_mod.issue(Row(), sessions, cwd=cwd), denials),
        tools=[],
        step=sessions_mod.StepHandle(),
        **kw,
    ):
        if kind == "chunk":
            text += payload
            if payload.strip():
                blocks += 1
        elif kind == "tool":
            text = _after_tool(text)
        elif kind == "done":
            done = payload
    return text, blocks, done


async def _submit_turn(
    sessions: Sessions,
    cwd: str,
    prompt: str,
    session_id: str,
    denials: Denials,
    channel: submit_mod.Channel,
    **kw: Any,
) -> dict[str, Any] | None:
    """One more turn on a step's own session, holding `submit` and nothing else, and its `done`.
    What it says is not kept: the artifact was written before it.
    """
    done = None
    async for kind, payload in sessions.stream(
        cwd,
        prompt,
        session_id,
        max_turns=SUBMIT_TURNS,
        gate=Gate(run_mod.issue(Row(submits=True), sessions, cwd=cwd), denials),
        tools=[],
        step=sessions_mod.StepHandle(),
        mcp_servers={submit_mod.SERVER: channel.server()},
        **kw,
    ):
        if kind == "done":
            done = payload
    return done


# # The turns `_submit_turn` gets: a call, a refusal, a call again and the reply. Chosen, not
# # measured.
SUBMIT_TURNS = 4


def _manifest_head(cwd: str) -> str | None:
    """The head `.screens/manifest.json` in the step's tree says its screenshots were taken at:
    the app's read, so no model copies it. `None` when there is none.
    """
    try:
        head = json.loads(
            (Path(cwd) / ".screens" / "manifest.json").read_text(encoding="utf-8")
        ).get("head")
    except OSError, ValueError, AttributeError:
        return None
    return str(head) if head else None


def _titled(pieces: list[str], artifact: str) -> bool:
    title = _title(artifact)
    return any(line.startswith(title) for piece in pieces for line in piece.splitlines())


def _unsubmitted(channel: submit_mod.Channel) -> str:
    """`""` once the run holds an object its channel's guard still opens on, with the app's hash
    taken now; else why not, the object dropped when it went stale. The run itself ends `done`
    only as the `run-submitted` guard says.
    """
    got = channel.received
    if got is not None:
        verdict = channel.verdict(got["object"], got["revision"])
        if not verdict.open:
            channel.received = None
            return (
                f"guard {channel.guard_id} refused the object it had accepted ({', '.join(verdict.reasons)}): "
                "the unit's files changed after it was submitted"
            )
    ran = guards.guard(guards.TRANSITIONS["run"]["submitted"]).check(
        {"submitted": channel.received is not None}
    )
    return "" if ran.open else f"{', '.join(ran.reasons)}: no object reached submit"


async def nothing() -> AsyncIterator[tuple[str, Any]]:
    """The main reply of an `opening` or `closing` turn taken up again: already said."""
    return
    yield


def _turn_cost(
    done: dict[str, Any] | None, cost: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The `closing` field of one more turn on a step's session, and the step's cost once that
    turn is counted in. The repair turn is costed the same way.

    A `ResultMessage` came back when `terminal_reason` is set. Its cost is the whole session's,
    so it replaces the main one rather than adding to it; the turn's own share is the difference.
    """
    after = str((done or {}).get("terminal_reason") or "")
    if not after:
        return {"cost_unknown": True}, cost
    total = ((done or {}).get("cost") or {}).get("cost_usd")
    before_usd = cost.get("cost_usd")
    closing = {
        "terminal": after,
        "turns": ((done or {}).get("cost") or {}).get("turns"),
        "cost_usd": (
            round(total - before_usd, 6) if total is not None and before_usd is not None else None
        ),
    }
    return closing, ({**cost, "cost_usd": total} if total is not None else cost)


async def _from_progress(
    cwd: str,
    watch: str,
    before: tuple[str, str] | None,
    running: steps.Running | None,
    tree_changed: bool,
    directory: Path,
    artifact: str,
) -> tuple[str, str]:
    """Write a spike's artifact from its progress file, when nothing forbids it.

    Returns one of `withheld`, `unchecked`, `none`, `unusable` or `progress` and a sentence for
    `detail` (`""` for none). Called only for a spike whose reply was not written; `outcome` is
    not this function's to change.
    """
    # A Stop, or a worktree the spike changed, writes nothing from any source.
    if (running is not None and running.stop_requested) or tree_changed:
        return "withheld", ""
    # The same check the reply's road makes, made again: the first reading may have failed. A
    # worktree the app could not read is not proven unchanged, so nothing is written; but nothing
    # stopped it, so it is `unchecked` rather than `withheld`.
    if before is None:
        return (
            "unchecked",
            "spike.md not written from the progress file: the worktree's state was not read before the step",
        )
    try:
        changed = describe_tree_change(before, await _tree_state(watch))
    except RunError as e:
        return "unchecked", f"spike.md not written from the progress file: {e}"
    if changed:
        return (
            "withheld",
            f"spike.md not written from the progress file: the worktree changed during spike: {changed}",
        )
    # From here a Stop is refused, as on the reply's road.
    if not steps.seal(running):
        return "withheld", ""
    progress = Path(cwd) / PROGRESS_FILE
    try:
        text = progress.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return "none", ""
    try:
        # No `await` from the seal to the write, as on the reply's road.
        _write_artifact(directory, artifact, text)
    except RunError as e:
        return "unusable", f"the progress file was not an artifact: {e}"
    return "progress", "spike.md written from the progress file"


def _stop_asked(running: steps.Running | None) -> bool:
    return running is not None and running.stop_requested


def _uncancel() -> None:
    task = asyncio.current_task()
    if task is not None:
        task.uncancel()


def _taken_back(
    running: steps.Running | None, e: asyncio.CancelledError
) -> asyncio.CancelledError | None:
    """`None` when the cancel was a Stop's, taken back here; else `e`, the app going down."""
    if not _stop_asked(running):
        return e
    _uncancel()
    return None


def _noted(detail: str, said: str) -> str:
    """`detail` with `said` under it."""
    if not said:
        return detail
    return f"{detail}\n--- {said} ---" if detail else said


def _admitted(
    started_by: str,
    stage: str,
    label: str | None,
    directory: str | Path,
    workspace: str,
    unit: str,
) -> Row:
    """The stage's row, once the step may run at all: else `ValueError` or `RunError`. Whether the
    row itself may run (a prose stage holding only reading tools among them) is `pack.check`'s,
    asked before the step (`agent-invalid`)."""
    check_started_by(started_by)
    row = row_for_step(stage, label)
    if not Path(directory).exists():
        raise RunError(f"no such work unit for {workspace}: {unit}")
    return row


async def _compose(
    cwd: str,
    directory: Path,
    unit: str,
    stage: str,
    artifact: str,
    row: Row,
    resume: dict[str, Any] | None,
    was: dict[str, Any],
    watch: str | None,
    *,
    branch: str,
    blocks: tuple[tuple[str, str], ...],
    gate_said: str,
    base_note: str,
    last_attempt: str,
    integration_note: str,
    screens_note: str,
    drift_note: str,
    lane: str,
    rerun: bool,
    rerun_note: str,
    app_note: str,
    plan_map: str,
    unfinished_round: dict[str, Any] | None,
    idea_note: str,
    siblings_note: str,
    mentions_note: str,
    agent: dict[str, Any] | None,
    meta: dict[str, Any] | None,
    state_file: str | None,
) -> tuple[str, str, list]:
    """`(head, prompt, envelope)` of the step: the message of a step taken up again, or the
    prompt composed from the unit's files."""
    if resume is not None:
        # Nothing of git is read or run on a step taken up again; what the first start read is in its
        # owner.
        return str(was.get("head") or ""), str(resume.get("message") or ""), []
    head = await _head_of(watch or cwd)
    branch = branch if stage == "impl" and not watch else ""
    prompt, envelope = compose_prompt(
        cwd,
        directory,
        unit,
        stage,
        artifact,
        writes_own=not row.app_writes_artifact,
        gate_said=gate_said,
        head=head,
        base_note=base_note,
        last_attempt=last_attempt,
        integration_note=integration_note,
        screens_note=screens_note,
        drift_note=drift_note,
        lane=lane,
        worktree=watch or "",
        ceilings=(row.max_turns, row.max_budget_usd or None),
        rerun=rerun,
        rerun_note=rerun_note,
        app_note=app_note,
        plan_map=plan_map,
        unfinished_round=unfinished_round,
        idea_note=idea_note,
        siblings_note=siblings_note,
        mentions_note=mentions_note,
        runs_commands="Bash" in row.tools and "Bash" not in row.asks,
        agent=agent,
        unit_meta=meta,
        state_file=state_file,
        blocks=blocks,
        branch=branch,
    )
    return head, prompt, envelope


def _runs_as(
    stage: str,
    key: str,
    row: Row,
    model: str | None,
    effort: str | None,
    agent: dict[str, Any] | None,
) -> run_mod.Agent:
    """The step as `run_mod` opens its session: Claude Code's system prompt for a row that opens
    anything, and the commit attribution only beside it, so a tool-less session's argv is
    unchanged."""
    return run_mod.Agent(
        stage,
        row,
        model=model,
        effort=effort,
        settings=agents.settings_json(agent) if agent is not None else None,
        preset=row.opens_anything,
        system=str((pack.row(key) or {}).get(pack.BODY) or ""),
    )


def _turn_kw(
    kw: dict[str, Any],
    owner: dict[str, Any],
    kind: str,
    running: steps.Running | None,
    turn_budget: float | None,
    take_up: bool = False,
    resume: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`kw` and what one more turn on the step's session is given: its budget, its owner (a board
    step's only) and, for a turn taken up again, where to go on from."""
    return {
        **kw,
        "max_budget_usd": turn_budget,
        **({"owner": {**owner, "kind": kind}} if running is not None else {}),
        **({"resume_at": (resume or {}).get("safe_uuid")} if take_up else {}),
    }


def _channel_for(
    row: Row,
    recorder: Any,
    stage: str,
    directory: Path,
    artifact: str,
    head: str,
    open_ids: tuple[str, ...],
    claims_round: int | None,
) -> submit_mod.Channel | None:
    """This run's `submit`, bound to it: a stage that hands back a stage result ends `done` only
    once the channel holds an object its guard still opens on."""
    if not row.submits:
        return None
    return submit_mod.Channel(
        run=str(recorder.run) if recorder is not None else uuid.uuid4().hex,
        stage=stage,
        directory=directory,
        artifact=artifact,
        own=not row.app_writes_artifact,
        head=head,
        open_ids=tuple(open_ids),
        claims_round=claims_round,
    )


def with_ceilings(
    row: Row, stage: str, label: str | None, was: Mapping[str, Any]
) -> tuple[Row, models.Ceilings]:
    """`row` with its two ceilings as `models.ceilings` resolves them from the agent's row, and
    those ceilings with where each came from. Gebo's row is given the same way.

    `was` is the owner of a step taken up again (`{}` for a first segment): it goes on under what
    its first segment ran under, as its owner kept it. An owner with none (an older build's)
    leaves them as resolved now."""
    ceilings = models.ceilings(stage, label)
    if was.get("max_turns") is not None:
        ceilings = models.Ceilings(
            max_turns=int(was["max_turns"]),
            max_turns_source=str(was.get("max_turns_source") or ""),
            max_budget_usd=float(was.get("max_budget_usd") or 0.0) or None,
            max_budget_source=str(was.get("max_budget_source") or ""),
        )
    return (
        replace(
            row,
            max_turns=int(ceilings["max_turns"] or row.max_turns),
            max_budget_usd=ceilings["max_budget_usd"] or 0.0,
        ),
        ceilings,
    )


def config_sources(
    ceilings: models.Ceilings,
    model_source: str,
    effort_source: str,
    was: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Where the model, the effort and the two ceilings came from, as the owner keeps them for the
    `config` of a segment that takes the step up again. A source not named is the owner's (`was`)."""
    kept = was or {}
    return {
        "model_source": model_source or str(kept.get("model_source") or ""),
        "effort_source": effort_source or str(kept.get("effort_source") or ""),
        "max_turns_source": ceilings["max_turns_source"],
        "max_budget_source": ceilings["max_budget_source"],
    }


def _owner(
    journal_key: str,
    unit: str,
    stage: str,
    start_at: Any,
    row: Row,
    head: str,
    label: str | None,
    effort: str | None,
    artifact: str,
    was: dict[str, Any],
    recorder: Any,
    resume: dict[str, Any] | None,
    owner_extra: dict[str, Any] | None,
    sources: dict[str, str],
) -> dict[str, Any]:
    """Whose session this is, for a `suspend` row, and all an update's next start needs to take it
    up again without reading git. `sources` is where the model, the effort and the two ceilings
    came from, for the `config` of the segment that takes it up."""
    segments = list(was.get("segments") or [])
    if resume is not None:
        # This segment, filled in when its first `done` comes.
        segments.append(
            {
                "suspend_id": resume.get("suspend_id"),
                **({"cost_unknown": True} if resume.get("cost_unknown") else {}),
            }
        )
    return {
        "kind": "step",
        "workspace": journal_key,
        "unit": unit,
        "stage": stage,
        "start_at": start_at,
        "max_turns": row.max_turns,
        "max_budget_usd": row.max_budget_usd,
        "head": head,
        "label": label,
        "effort": effort,
        **sources,
        "artifact": artifact,
        "segments": segments,
        **({"run": recorder.run} if recorder is not None else {}),
        **(owner_extra or {}),
    }


def _carried(
    resume: dict[str, Any] | None, was: dict[str, Any], turn_kind: str
) -> tuple[list[str], int, str, str, dict[str, Any]]:
    """`(pieces, blocks, terminal, session_id, cost)` a step starts from: nothing, or what it had
    before the update that paused it."""
    # What the session said, one entry per stretch between two tool calls. A step taken up again
    # starts from what it had said before its safe point.
    pieces = [*((resume or {}).get("pieces") or []), ""] if resume is not None else [""]
    # How many pieces of text, blank ones aside, the session said.
    blocks = sum(1 for p in pieces if p.strip())
    terminal = ""
    session_id = str((resume or {}).get("session_id") or "")
    cost: dict[str, Any] = {}
    if turn_kind == "opening":
        # The main reply ended before the update; what it ended with is in the owner.
        terminal = str(was.get("main_terminal") or "")
        cost = dict(was.get("main_cost") or {})
    elif resume is not None and resume.get("spent_usd") is not None:
        cost = {"cost_usd": float(resume["spent_usd"])}
    return pieces, blocks, terminal, session_id, cost


async def _tree_before(
    resume: dict[str, Any] | None,
    was: dict[str, Any],
    watch: str | None,
    owner: dict[str, Any],
) -> tuple[str, str] | None:
    """The spike's worktree as it stood before the session, or as the first start read it."""
    if resume is not None:
        return tuple(was["before"]) if watch and was.get("before") else None
    before = await _tree_state(watch) if watch else None
    if before is not None:
        owner["before"] = list(before)
    return before


def _helpers_of(
    row: Row,
    recorder: Any,
    blocks: tuple[tuple[str, str], ...],
    resumed: bool,
) -> tuple[Helpers | None, tuple[tuple[str, str], ...]]:
    """This run's helpers, for a row that may start them (its hooks hold `Agent` and
    `SendMessage`), and the prompt blocks with `PROTOCOL` added. A step taken up again composes no
    prompt."""
    if AGENT_TOOL not in row.tools or AGENT_TOOL in row.asks:
        return None, blocks
    ledger = Helpers(recorder.helper if recorder is not None else None)
    return ledger, blocks if resumed else (*blocks, ("helpers", PROTOCOL))


def _denials(recorder: Any) -> Denials:
    """What a step was refused, told to its recorder when it has one."""
    denials = Denials()
    if recorder is not None:
        denials.listener = recorder.denied
    return denials


def _after_call(pieces: list[str], tool: str, after_submit: int | None) -> int | None:
    """Starts the piece after a tool call; the index of the one after a `submit` call, else
    `after_submit`.

    Kept: what comes after a tool call is the next piece, and `_joined` puts it on a line of its
    own. Dropping all text before the last call lost the head of any artifact written in pieces;
    `_write_artifact` drops what comes before the artifact's last title line instead.
    """
    pieces.append("")
    return len(pieces) - 1 if tool == submit_mod.NAME else after_submit


def _reply_done(
    payload: dict[str, Any], segment_done: dict[str, Any] | None, resume: dict[str, Any] | None
) -> tuple[str, dict[str, Any], str, list[str], dict[str, Any] | None]:
    """`(session_id, cost, terminal, models_used, segment_done)` once the main reply's `done` came."""
    return (
        payload.get("session_id", ""),
        payload.get("cost", {}) or {},
        str(payload.get("terminal_reason") or ""),
        list(payload.get("models_used") or []),
        payload if resume is not None and segment_done is None else segment_done,
    )


def _rounds_before(
    row: Row,
    channel: submit_mod.Channel | None,
    stage: str,
    directory: Path,
    artifact: str,
) -> set[int] | None:
    """The rounds `review.md` held before this step's reply is written, for a review that hands
    its round back through `submit`; else `None`.

    Taken before the write and kept when it is refused: the round is numbered past these.
    """
    if not (row.app_writes_artifact and channel is not None and stage == submit_mod.ROUND):
        return None
    return {_round_number(r) for r in _rounds(_read(directory / artifact))}


def _write_reply(
    row: Row,
    directory: Path,
    artifact: str,
    stage: str,
    watch: str | None,
    pieces: list[str],
    after_submit: int | None,
    channel: submit_mod.Channel | None,
    blocks: int,
    spike_md: str | None,
    review_md: str | None,
) -> tuple[str | None, str | None]:
    """The artifact, written from the reply or found on disk: `(spike_md, review_md)`."""
    if not row.app_writes_artifact:
        # The session had the tools to write it. Believing it did, rather than looking, is how a step
        # reports success for a file that is not there.
        if not (directory / artifact).exists():
            raise RunError(f"the step did not write {artifact}")
        # What decides is the object.
        return spike_md, review_md
    # Synchronous, so nothing yields between reading the `## Answers` already on disk and writing
    # the artifact over it.
    #
    # A session stopped at its ceiling was cut off, so a title it wrote before its last tool call
    # is a draft or a first piece whose header may say `accepted`. Only what it said after that
    # call is taken.
    taken = pieces
    if (
        channel is not None
        and after_submit is not None
        and not _titled(pieces[after_submit:], artifact)
    ):
        # What came after the last `submit` call, with no title in it, is the session saying it
        # called the tool: never part of the artifact.
        taken = pieces[:after_submit]
    _write_artifact(directory, artifact, _joined(taken, artifact), blocks=blocks)
    if watch:
        # The reply was written, so the progress file is never read.
        spike_md = "reply"
    if stage == "review":
        review_md = "round"
    return spike_md, review_md


def _judged(
    e: BaseException,
    running: steps.Running | None,
    pieces: list[str],
    terminal: str,
    unit: str,
    stage: str,
) -> tuple[Outcome, str, dict[str, str] | None, OpeningError | None] | None:
    """How a step that raised ends: `(outcome, detail, error, unopened)`; `None` when the app is
    going down or an update paused the session, and the caller lets `e` through with no `end`.

    `error` is set only here, so a step that finished, or paused at a ceiling, carries no error.
    """
    if isinstance(e, asyncio.CancelledError):
        if _taken_back(running, e) is not None:
            return None
        # The cancel was `Steps.stop_step`'s own. Taken back, so the `end` is written and the
        # step's reader still gets its `done` row.
        return "stopped", "", None, None
    if isinstance(e, Suspended):
        # An update paused the session and wrote its `suspend` row: like an app going down, no
        # `attempt` and no `end`. The next start takes it up.
        return None
    if run_mod.ceiling_of(terminal):
        # Bounded, not failed: the session is kept and a raise goes on from it.
        return "paused-budget", f"stopped at the ceiling: {terminal}", None, None
    error = {"type": type(e).__name__, "message": str(e)}
    if isinstance(e, (RunError, Refused)):
        # "The step did not write impl.md" is a real reason to stop, so it goes into the attempt
        # record's `error` like any other.
        unopened = e if isinstance(e, OpeningError) else None
        # What the session said, kept: the money was spent, and a reply with a preamble is
        # often a good artifact that a person can judge in a second.
        detail = _with_reply(str(e), _joined(pieces))
    else:
        log.exception("the session of %s %s failed", unit, stage)
        unopened = None
        detail = _with_reply(f"{type(e).__name__}: {e}", _joined(pieces))
    return "failed", detail, error, unopened


async def _spike_progress(
    cwd: str,
    watch: str,
    before: tuple[str, str] | None,
    running: steps.Running | None,
    tree_changed: bool,
    directory: Path,
    artifact: str,
    detail: str,
    unit: str,
) -> tuple[str | None, str, BaseException | None]:
    """A spike's `spike.md` from its progress file, when its reply was not written:
    `(spike_md, detail, held)`.

    Read here, before `runner.steps.Steps.run_step` removes `cwd`, and not in the `except` branches: an
    exception raised inside one (a Stop's cancel landing on an `await`) is not caught by its
    siblings and would leave with no `end`. Wrapped like `snapshot`; `outcome` is never changed.
    `held` is the cancel of an app going down, which the caller raises once the `end` is written.
    """
    try:
        spike_md, said = await _from_progress(
            cwd, watch, before, running, tree_changed, directory, artifact
        )
        return spike_md, _noted(detail, said), None
    except asyncio.CancelledError as e:
        held = _taken_back(running, e)
        return ("withheld" if held is None else None), detail, held
    except Exception as e:
        # The `end` row never depends on it.
        log.exception("the progress file of %s was not read", unit)
        said = f"the progress file was not read: {type(e).__name__}: {e}"
        return "unusable", _noted(detail, said), None


async def _repair_opening(
    sessions: Sessions,
    cwd: str,
    artifact: str,
    session_id: str,
    denials: Denials,
    unopened: OpeningError,
    turn_kw: dict[str, Any],
    take_up: bool,
    turn_spent: bool,
    running: steps.Running | None,
    cost: dict[str, Any],
    directory: Path,
    segment_done: dict[str, Any] | None,
    detail: str,
    unit: str,
    stage: str,
    message: str,
) -> tuple[
    str | None,
    str,
    dict[str, Any] | None,
    dict[str, Any],
    bool,
    dict[str, Any] | None,
    BaseException | None,
]:
    """A prose step whose reply was refused for its opening alone gets one more turn on its own
    session, with no tools, asking for the artifact again: `(opening, detail, closing, cost,
    taken, segment_done, held)`, `opening` being `repaired`, `none` or `withheld`, and `detail`
    `""` once repaired.

    Not a spike (progress file), and not a step at its ceiling (closing turn). Wrapped, for the
    closing turn's reasons: the `end` row never depends on it. Sealed first, so a Stop is refused
    until the turn is over; `OPENING_TIMEOUT` bounds it, and the budget does not. `taken` is
    whether the turn an update paused was reached again.
    """
    opening: str | None = None
    closing: dict[str, Any] | None = None
    held: BaseException | None = None
    taken = False
    said = ""
    try:
        if not session_id:
            opening, said = (
                "none",
                f"{artifact}: the opening was not repaired: the session has no id",
            )
        elif not steps.seal(running):
            opening = "withheld"
        elif turn_spent:
            # The turn would stop at the ceiling, and be refused.
            taken = take_up
            opening, said = (
                "none",
                f"{artifact}: the repair turn's reply was not written: it stopped at the ceiling: error_max_budget_usd",
            )
        else:
            reply, again, done = await asyncio.wait_for(
                _opening_turn(
                    sessions,
                    cwd,
                    message if take_up else opening_prompt(artifact, unopened.problem),
                    session_id,
                    denials,
                    **turn_kw,
                ),
                OPENING_TIMEOUT,
            )
            if take_up:
                taken, segment_done = True, segment_done or done
            closing, cost = _turn_cost(done, cost)
            after = str((done or {}).get("terminal_reason") or "")
            # A turn that stopped at a ceiling writes nothing. At `max_turns` it was cut off: an MCP tool
            # still reaches a session with `tools=[]`, and one call its gate refuses ends the only
            # turn, so what came before may be a draft whose header says `accepted`. At the budget the
            # turn ran whole and the CLI compared the cost after, so its reply may be complete; it is
            # refused all the same, because the reply's road never ends a step past its ceiling `done`.
            if run_mod.ceiling_of(after):
                opening, said = (
                    "none",
                    f"{artifact}: the repair turn's reply was not written: it stopped at the ceiling: {after}",
                )
            else:
                # The road every reply takes, and nothing of the first reply joined to it. No `await` from
                # here to the write.
                try:
                    _write_artifact(directory, artifact, reply, blocks=again)
                except RunError as e:
                    opening, said = (
                        "none",
                        f"{artifact}: the repair turn's reply was not written: {e}",
                    )
                else:
                    opening, detail = "repaired", ""
    except asyncio.CancelledError as e:
        held = _taken_back(running, e)
        if held is None:
            opening = "withheld"
    except Suspended as e:
        held = e
    except Exception as e:
        # The `end` row never depends on it.
        log.exception("the opening turn of %s %s failed", unit, stage)
        closing = {"cost_unknown": True, "error": f"{type(e).__name__}: {e}"}
        opening, said = (
            "none",
            f"{artifact}: the repair turn failed: {type(e).__name__}: {e}",
        )
    # Under the first refusal and what the session first replied.
    return opening, _noted(detail, said), closing, cost, taken, segment_done, held


async def _repair_submit(
    sessions: Sessions,
    cwd: str,
    stage: str,
    artifact: str,
    session_id: str,
    denials: Denials,
    channel: submit_mod.Channel,
    turn_kw: dict[str, Any],
    turn_spent: bool,
    running: steps.Running | None,
    cost: dict[str, Any],
    outcome: Outcome,
    error: dict[str, str] | None,
    detail: str,
    unit: str,
) -> tuple[
    Outcome,
    dict[str, str] | None,
    str,
    dict[str, Any] | None,
    dict[str, Any],
    BaseException | None,
]:
    """A step that wrote its artifact with no object the guard still opens on gets one more turn on
    its own session, holding `submit` and nothing else, as the opening's repair is one: `(outcome,
    error, detail, submit_turn, cost, held)`.

    Still none, and it ends `failed`, whatever its file says. Sealed and wrapped as that turn is:
    the `end` row never depends on it.
    """
    submit_turn: dict[str, Any] | None = None
    held: BaseException | None = None
    why = _unsubmitted(channel)
    said = ""
    if not why:
        return outcome, error, detail, submit_turn, cost, held
    try:
        if not session_id:
            said = "the session has no id"
        elif not steps.seal(running):
            said = "a Stop came first"
        elif turn_spent:
            said = "the budget was spent"
        else:
            done = await asyncio.wait_for(
                _submit_turn(
                    sessions,
                    cwd,
                    submit_prompt(stage, artifact, why),
                    session_id,
                    denials,
                    channel,
                    **turn_kw,
                ),
                OPENING_TIMEOUT,
            )
            submit_turn, cost = _turn_cost(done, cost)
            if run_mod.ceiling_of(str((done or {}).get("terminal_reason") or "")):
                channel.received = None
                said = "the repair turn stopped at its ceiling"
            else:
                why = _unsubmitted(channel)
    except asyncio.CancelledError as e:
        held = _taken_back(running, e)
        if held is None:
            said = "a Stop came during the repair turn"
    except Suspended as e:
        held = e
    except Exception as e:
        # The `end` row never depends on it.
        log.exception("the repair turn of %s %s failed", unit, stage)
        submit_turn = {"cost_unknown": True, "error": f"{type(e).__name__}: {e}"}
        said = f"the repair turn failed: {type(e).__name__}: {e}"
    if why and held is None:
        outcome = "failed"
        error = {"type": "NoSubmission", "message": why}
        detail = f"no-submission: {why}" + (f" ({said})" if said else "")
    return outcome, error, detail, submit_turn, cost, held


def _write_round(
    cwd: str,
    directory: Path,
    artifact: str,
    head: str,
    agent: dict[str, Any] | None,
    channel: submit_mod.Channel,
    rounds_before: set[int] | None,
    rounds_known: tuple[int, ...],
    unit: str,
    outcome: Outcome,
    error: dict[str, str] | None,
    detail: str,
) -> tuple[Outcome, dict[str, str] | None, str]:
    """The round the review handed back, written into `review.md` by the app: its number, the head
    the app read, the verdict, the findings and the screenshots. `(outcome, error, detail)`, the
    step failed if it could not be."""
    try:
        # Past every round the file held and every one the app has a row for, so a number is never
        # given twice (`review_rounds_n`).
        number = max({*(rounds_before or ()), *rounds_known}, default=0) + 1
        screens = {
            "taken": _manifest_head(cwd),
            "standard": UI_STANDARD,
            "by": f"{(agent or {}).get('name') or 'the review session'} (agent, review)",
        }
        text = _read(directory / artifact)
        new = [r for r in _rounds(text) if _round_number(r) not in (rounds_before or set())]
        received = channel.received or {}
        rendered = render_round(
            new[-1] if new else "## Round", number, head, received["object"], screens
        )
        (directory / artifact).write_text(
            replace_new_rounds(text, rounds_before or set(), rendered),
            encoding="utf-8",
        )
        channel.extra = {"n": number, "screens": screens}
    except Exception as e:
        # The `end` row never depends on it.
        log.exception("the review round of %s was not written", unit)
        return (
            "failed",
            {"type": type(e).__name__, "message": str(e)},
            f"review.md: the round was not written from its object: {type(e).__name__}: {e}",
        )
    return outcome, error, detail


async def _close_recorder(
    recorder: Any, outcome: Outcome, detail: str, unit: str, stage: str
) -> tuple[dict[str, Any], int | None, str]:
    """`(run_fields, stored, stored_from)`: the recorder closed, everything on disk, before the
    attempt record, so the turns it counts go into both."""
    try:
        lost = await recorder.close(outcome, detail)
    except Exception:
        # How many is unknown, so all of them.
        log.exception("the events of %s %s were not closed", unit, stage)
        lost = max(1, int(getattr(recorder, "seq", 0) or 0))
    run_fields = {"run": recorder.run, "events_lost": lost}
    stored: int | None = None
    stored_from = ""
    if outcome != "done":
        try:
            stored, stored_from = await recorder.stored_turns()
        except Exception:
            # The `end` row never depends on it.
            log.exception("the stored turns of %s %s were not read", unit, stage)
            stored = None
    return run_fields, stored, stored_from


def _unwritten(mark: str | None, applies: bool, outcome: Outcome) -> str | None:
    """`mark` as it was, or for one nothing wrote (`None`) where it applies: `withheld` for a Stop,
    else `none`."""
    if not applies or mark is not None:
        return mark
    return "withheld" if outcome == "stopped" else "none"


def _cost_fields(
    cost: dict[str, Any], counted: bool, outcome: Outcome, stored: int | None, stored_from: str
) -> dict[str, Any]:
    """A step that did not finish counts its turns from its events, and keeps the CLI's own count,
    when one came, as `cli_turns`. With no `ResultMessage` there is no `cost_usd` at all, never
    a zero. `done` is written as it always was."""
    fields: dict[str, Any] = dict(cost)
    if counted and outcome != "done":
        if isinstance(stored, int) and stored > 0:
            if cost:
                fields["cli_turns"] = fields.pop("turns", None)
            fields["turns"] = stored
            if stored_from == "memory":
                fields["turns_from"] = "memory"
        if not cost and outcome != "stopped":
            # A Stop's `end` says this below, beside `stopped_by`.
            fields["cost_unknown"] = True
    return fields


def _segment_fields(
    owner: dict[str, Any],
    resume: dict[str, Any] | None,
    segment_done: dict[str, Any] | None,
) -> dict[str, Any]:
    """Each segment an update's resume began, with the tokens of its first call and what it cost;
    `cost_partial` when one before it left no cost."""
    fields: dict[str, Any] = {}
    if owner["segments"]:
        if resume is not None and segment_done is not None:
            segment = owner["segments"][-1]
            segment["first_call"] = segment_done.get("first_call")
            total = (segment_done.get("cost") or {}).get("cost_usd")
            if total is not None:
                # A CLI killed without its `cost-state` leaves the next one counting from zero, so its total
                # is this segment's alone.
                spent = 0.0 if resume.get("cost_unknown") else float(resume.get("spent_usd") or 0.0)
                segment["cost_usd"] = round(float(total) - spent, 6)
        fields["segments"] = owner["segments"]
        if any(s.get("cost_unknown") for s in owner["segments"]):
            fields["cost_partial"] = True
    return fields


async def _attempt(
    journal: Journal,
    journal_key: str,
    unit: str,
    stage: str,
    cwd: str,
    session_id: str,
    outcome: Outcome,
    terminal: str,
    error: dict[str, str] | None,
    cost: dict[str, Any],
    turns: Any,
) -> BaseException | None:
    """A stopped step's tree and transcript, captured *before* `end` is written: the attempt record,
    or the cancel the capture met (`pending`).

    Only for a run that is not `done`: a step that wrote its artifact needs no attempt record, and
    this must not touch anything a `done` run left behind.
    """
    pending: BaseException | None = None
    try:
        fields, pending = await snapshot(cwd, session_id)
        journal.attempted(
            journal_key,
            unit,
            stage,
            outcome=outcome,
            terminal=terminal or None,
            error=error,
            turns=turns,
            cost_usd=cost.get("cost_usd"),
            session_id=session_id or None,
            **fields,
        )
    except Exception:
        # A failure here must not change the outcome or the `end` record that follows. The attempt
        # record is best-effort; the run log's `end` row is the one thing never put at risk.
        log.exception("the attempt record of %s %s was not written", unit, stage)
    return pending


def _round_counts(channel: submit_mod.Channel | None) -> dict[str, Any]:
    """The findings of the round a `review` step handed back, for its `end`: the count, the open
    ones and its verdict. Nothing for a step that handed back no round."""
    obj = (channel.received or {}).get("object") if channel is not None else None
    if not isinstance(obj, dict) or "verdict" not in obj:
        return {}
    findings = [f for f in obj.get("findings") or () if isinstance(f, dict)]
    return {
        "findings": len(findings),
        "findings_open": sum(1 for f in findings if f.get("state") == "open"),
        "verdicts": [str(obj["verdict"])],
    }


class Runner:
    """Runs one step. Owns no state beyond what it was handed."""

    def __init__(
        self,
        sessions: Sessions,
        journal: Journal | None,
        app: dict | None = None,
        hooks: kernel.Hooks = kernel.Hooks(),
    ):
        self.sessions = sessions
        self.hooks = hooks
        self.journal = journal
        # `{"version", "commit"}` of the app running this step, from `update.identity`; `None` leaves
        # `start` without them.
        self.app = app

    def _configured(
        self, row: Row, stage: str, label: str | None, was: Mapping[str, Any]
    ) -> tuple[Row, models.Ceilings]:
        """`row` and its two ceilings with their sources, as `with_ceilings` resolves them."""
        return with_ceilings(row, stage, label, was)

    def _scratch(self, workspace: str, unit: str) -> tuple[Path, Path] | None:
        """The unit's `(ram, disk)` scratch directories, made if missing, or `None` for a stand-in
        `Sessions` with no config (a test's), which has no data root to put them in. Raises
        `RunError` saying why when `scratch.ensure` would not use them."""
        config = getattr(self.sessions, "config", None)
        if config is None:
            return None
        try:
            return scratch_mod.ensure(workspace, unit, config.data_dir)
        except scratch_mod.Unsafe as e:
            raise RunError(
                f"the unit's scratch directory cannot be used, so the step did not start: {e}"
            ) from e

    async def _features(
        self,
        row: Row,
        running: Any,
        *,
        workspace: str,
        journal_key: str,
        unit: str,
        stage: str,
        cwd: str,
        watch: str | None,
        directory: Path,
        resumed: bool,
        plan: kernel.Plan | None,
        branch: str,
    ) -> tuple[
        Any,
        kernel.Facts,
        tuple[tuple[str, str], ...],
        Helpers | None,
        tuple[kernel.Tool, ...],
        tuple[str, str] | None,
    ]:
        """The step's recorder, when `Steps.run_step` gave it one (its `run` goes into `start` and
        `end`, and it is closed, everything on disk, before `end` is written); this run as the
        features see it, its grant issued; the prompt blocks they add; the run's helpers
        (`_helpers_of`); the catalog tools it was granted; and the unit's `(ram, disk)` scratch
        directories, made here. A step taken up again composes no prompt, so it has no block.

        The grant (`run_mod.issue`) holds its `cwd` and, unless it is a spike, the unit's folder to
        write, and `branch`, the unit's own as the app recorded it, the one it may push (a spike
        has none): never the worktree's `HEAD`, which the session can move."""
        recorder = getattr(running.handle, "recorder", None) if running is not None else None
        asked = kernel.facts(
            workspace=workspace,
            workspace_key=journal_key,
            unit=unit,
            agent=stage,
            run=str(recorder.run) if recorder is not None else uuid.uuid4().hex,
            cwd=cwd,
            watch=watch,
            directory=directory,
            resumed=resumed,
            plan=plan,
        )
        tools = self.hooks.tools_for(asked, row)
        made = self._scratch(workspace, unit)
        # A step with nothing to name passes nothing, so a stand-in `stream` keeps working.
        own = (str(made[0]), str(made[1])) if made is not None else None
        grant = run_mod.issue(
            row,
            self.sessions,
            cwd=cwd,
            unit_dir="" if watch else str(directory),
            scratch=own,
            branch="" if watch else branch,
            # What the row names beyond the kernel's own tools is a feature's: never `--tools`.
            features=tuple(t for t in row.tools if t not in _BUILTIN),
            held=tuple(t.name for t in self.hooks.held(row, workspace)),
            mcp=kernel.granted(tools),
            use=tuple((t.name, r) for t in tools if t.uses is not None for r in t.uses(asked)),
            asks=kernel.granted(tuple(t for t in tools if t.name in row.asks)),
        )
        facts = replace(asked, grant=grant)
        ledger, blocks = _helpers_of(
            row, recorder, () if resumed else await self._blocks(facts), resumed
        )
        return recorder, facts, blocks, ledger, tools, own

    def _with_tools(
        self,
        channel: submit_mod.Channel | None,
        facts: kernel.Facts,
        tools: tuple[kernel.Tool, ...],
        ledger: Helpers | None = None,
    ) -> dict[str, Any]:
        """The `mcp_servers` argument holding `submit` (when there is a channel) and one server per
        granted catalog tool, each made from this run's facts. `ledger`'s `peers` rides on
        `submit`'s server: a row holding `Agent` (impl) submits."""
        extra = (ledger.tool(),) if ledger is not None else ()
        servers: dict[str, Any] = (
            {submit_mod.SERVER: channel.server(*extra)} if channel is not None else {}
        )
        servers.update({t.server: t.make(facts) for t in tools if t.make is not None})
        return {"mcp_servers": servers} if servers else {}

    async def _blocks(self, facts: kernel.Facts) -> tuple[tuple[str, str], ...]:
        """The prompt blocks features add to this run, in order, those with words only."""
        out = []
        for b in self.hooks.blocks_for(facts):
            text = b.render(facts)
            if inspect.isawaitable(text):
                text = await text
            if text:
                out.append((b.name, text))
        return tuple(out)

    async def run(
        self,
        workspace: str,
        directory: str | Path,
        journal_key: str,
        unit: str,
        stage: str,
        artifact: str,
        mode: str,
        gate_said: str = "",
        gate_reasons: tuple[str, ...] = (),
        lane: str = "full",
        process: str = pack.DEFAULT_PROCESS,
        cwd: str | None = None,
        model: str | None = None,
        model_source: str = "",
        base: dict[str, Any] | None = None,
        base_note: str = "",
        last_attempt: str = "",
        integration_note: str = "",
        screens_note: str = "",
        plan_drift: dict[str, Any] | None = None,
        drift_note: str = "",
        shortlist: dict[str, Any] | None = None,
        effort: str | None = None,
        effort_source: str = "",
        label_declared: str | None = None,
        label: str | None = None,
        label_source: str | None = None,
        impl_run: int | None = None,
        watch: str | None = None,
        running: steps.Running | None = None,
        started_by: str = "person",
        trial_record: dict[str, Any] | None = None,
        rerun: bool = False,
        rerun_note: str = "",
        app_note: str = "",
        plan_map: str = "",
        plan_map_record: dict[str, Any] | None = None,
        plan: kernel.Plan | None = None,
        unfinished_round: dict[str, Any] | None = None,
        open_ids: tuple[str, ...] = (),
        claims_round: int | None = None,
        rounds_known: tuple[int, ...] = (),
        idea_note: str = "",
        siblings_note: str = "",
        mentions_note: str = "",
        agent: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        state_file: str | None = None,
        resume: dict[str, Any] | None = None,
        owner_extra: dict[str, Any] | None = None,
        branch: str = "",
    ) -> AsyncIterator[tuple[str, Any]]:
        """Yield `("chunk", text)` while the reply arrives, then one `("done", {...})`.

        The same shape `Sessions.stream` uses, so the page and a proof command consume one stream.
        Optional arguments left `None`/empty leave the prompt, argv and records as they were.

        `cwd` is where the step works (the unit's worktree): the session's directory, the write
        boundary and the repository the prompt names. `workspace` stays the membership question
        and the journal's subject; unset, the two are the same.

        `model`, `model_source`, `effort`, the label fields, `impl_run`, `base`, `plan_drift`,
        `shortlist`, `started_by` (`person` or `autopilot`, else `ValueError`),
        `trial_record`, `plan_map*`, `rerun*`, `app_note` (the autopilot's note), `agent`
        (the stage's resolved agent-table row; a preset session gets its commit attribution as
        `settings`) and `meta` (the unit's snapshot entry) are carried into the prompt or the
        `start` record and nowhere else; this module reads no git and decides no meaning.
        `base_note` is `service.describe_base(base)`, already worked out, so `build_prompt` does not
        import the app's assembly. A `done` review's `end` carries the
        findings of the round it handed back. `model_trial.model` is filled in once
        the session's `init` names it. `ci_red` may come alone.

        `watch` is the unit's worktree when `cwd` is a spike's throwaway directory: writing is held
        to `cwd`, and the spike has no branch to push. Its `HEAD` and `git status --porcelain`
        are read before and after the session and a difference fails the step before any artifact
        is written; nothing is restored, the difference goes in `detail`.

        `idea_note`, `siblings_note` and `mentions_note` are the shared idea, the sibling
        checkouts `impl` may read, and the units this unit names. `unfinished_round` is
        `{n, dropped}` for a `review` prompt only.

        `running` is the step's row in `Core.steps`. With it the client is closed when the step
        ends, and a person's Stop ends it as `stopped`, decided by `running.stop_requested`, never
        by the kind of exception. A stop is honoured only before `steps.seal`, which is called
        before anything of the artifact is written or read, so a stopped step leaves no artifact
        and a sealed one cannot be stopped halfway. A cancellation nobody asked for is the app
        shutting down and writes no `end`.

        `resume` is a `suspend` row, with the `message` to send and the `pieces` the transcript held
        before its safe point. The step goes on in the same session under what is left of the
        row's two ceilings and writes no `start`; one whose ceiling is used up opens no session and
        ends `paused-budget`. A row whose `owner.kind` is `opening` goes through the main reply again
        from those pieces and takes up that one turn. A person's raise of a ceiling is such a row. `owner_extra` is what `Core`
        adds to the owner a `suspend` row carries.
        """
        was = dict((resume or {}).get("owner") or {})
        # A step taken up again goes on under the ceilings its owner kept.
        row, ceilings = self._configured(
            _admitted(started_by, stage, label, directory, workspace, unit),
            stage,
            label,
            was,
        )
        directory = Path(directory)
        cwd = cwd or workspace
        turn_kind = str(was.get("kind") or "step") if resume is not None else ""
        recorder, facts, blocks, ledger, tools, own = await self._features(
            row,
            running,
            workspace=workspace,
            journal_key=journal_key,
            unit=unit,
            stage=stage,
            cwd=cwd,
            watch=watch,
            directory=directory,
            resumed=resume is not None,
            plan=plan,
            branch=branch,
        )
        head, prompt, envelope = await _compose(
            cwd,
            directory,
            unit,
            stage,
            artifact,
            row,
            resume,
            was,
            watch,
            branch=branch,
            blocks=blocks,
            gate_said=gate_said,
            base_note=base_note,
            last_attempt=last_attempt,
            integration_note=integration_note,
            screens_note=screens_note,
            drift_note=drift_note,
            lane=lane,
            rerun=rerun,
            rerun_note=rerun_note,
            app_note=app_note,
            plan_map=plan_map,
            unfinished_round=unfinished_round,
            idea_note=idea_note,
            siblings_note=siblings_note,
            mentions_note=mentions_note,
            agent=agent,
            meta=meta,
            state_file=state_file,
        )
        channel = _channel_for(
            row, recorder, stage, directory, artifact, head, open_ids, claims_round
        )
        servers = self._with_tools(channel, facts, tools, ledger)
        runs_as = _runs_as(stage, pack.agent_for(process, stage) or "", row, model, effort, agent)
        start_at = self._write_start(
            was.get("start_at"),
            resume,
            journal_key,
            unit,
            stage,
            mode,
            started_by,
            prompt,
            envelope,
            row,
            facts.grant,
            head,
            cwd,
            recorder,
            gate_reasons,
            model=model,
            model_source=model_source,
            effort=effort,
            effort_source=effort_source,
            label_declared=label_declared,
            label=label,
            label_source=label_source,
            impl_run=impl_run,
            base=base,
            plan_drift=plan_drift,
            shortlist=shortlist,
            trial_record=trial_record,
            plan_map_record=plan_map_record,
            rerun=rerun,
            rerun_note=rerun_note,
            agent=agent,
            blocks=[name for name, _ in blocks],
            process=process,
        )
        owner = _owner(
            journal_key,
            unit,
            stage,
            start_at,
            row,
            head,
            label,
            effort,
            artifact,
            was,
            recorder,
            resume,
            owner_extra,
            config_sources(ceilings, model_source, effort_source, was),
        )
        # A routine `impl` in the model trial has its `start` told the model the session's `init`
        # named, once, when it comes, or `never-started` at the end.
        trial_at = start_at if (trial_record or {}).get(modeltrial.FIELD) and start_at else None
        # What is left of the two ceilings after the part of the session before the cut.
        turns_left, budget_left, used_up = (
            transcript.ceilings_left(row.max_turns, row.max_budget_usd, resume)
            if resume is not None
            else (row.max_turns, row.max_budget_usd or None, "")
        )
        denials = _denials(recorder)
        pieces, blocks, terminal, session_id, cost = _carried(resume, was, turn_kind)
        # `models_used` is what the session says it was billed to; the `start` record says what was
        # asked for. `shutting_down` is set when the task is cancelled with no Stop behind it.
        outcome, detail, error, shutting_down, tree_changed, models_used = (
            "failed", "", None, False, False, []
        )  # fmt: skip
        # The index of the piece that began after the last `submit` call, `None` before one; the
        # rounds `review.md` held before this step's reply was written; the `done` of the first call on
        # a resumed session, which fills that segment in; the worktree's state before the session; the
        # refusal of a reply that lacked its opening, which a repair turn may follow; and where a
        # spike's `spike.md` and a review's `review.md` came from, for their `end` row.
        after_submit = rounds_before = segment_done = before = unopened = None
        spike_md = review_md = None
        try:
            before = await _tree_before(resume, was, watch, owner)
            if used_up and turn_kind != "opening":
                # Nothing left to resume with, so no session is opened. Not for a closing or repair turn:
                # that is one turn of its own, bounded by its timeout.
                terminal = used_up
                raise RunError(
                    "the ceiling was used up before the update, so the step was not resumed"
                )
            async for kind, payload in await self._open_main(
                cwd,
                prompt,
                session_id,
                turns_left,
                budget_left,
                runs_as,
                # A session's own `git push` asks the guards again first (`vault-leak`).
                Gate(facts.grant, denials, ledger, before_push=lambda: self.hooks.refusal(facts)),
                own,
                running,
                owner,
                resume,
                servers,
                turn_kind,
                workspace,
            ):
                if kind == "chunk":
                    pieces[-1] += payload
                    blocks += bool(payload.strip())
                    yield ("chunk", payload)
                elif kind == "session":
                    # The one place this app learns a session id before the step is over. Not forwarded: only
                    # `chunk` may cross this boundary as itself.
                    session_id = str(payload)
                    trial_at = self._trial_model(
                        trial_at,
                        journal_key,
                        unit,
                        stage,
                        running.handle.init_model if running is not None else "",
                    )
                elif kind == "tool":
                    # Not forwarded: `coscc/http/routes.py` treats every kind that is not `chunk` as the terminal
                    # `done` row, so a third kind would arrive at the client as a malformed `done`.
                    after_submit = _after_call(pieces, payload, after_submit)
                else:
                    session_id, cost, terminal, models_used, segment_done = _reply_done(
                        payload, segment_done, resume
                    )
            # What the main reply ended with, for an `opening` turn an update pauses: its
            # next start goes through this reply again without a session.
            owner.update(main_terminal=terminal, main_cost=dict(cost))
            if run_mod.ceiling_of(terminal):
                # Nothing is written of what it had not finished: the session is kept.
                raise RunError(f"stopped at the ceiling: {terminal}")

            # Before anything is written: a spike that touched the branch it was meant only to read must
            # leave no `spike.md` saying it measured.
            changed = (
                describe_tree_change(before, await _tree_state(watch))
                if watch and before is not None
                else ""
            )
            if changed:
                spike_md, tree_changed = "withheld", True
                raise RunError(f"the worktree changed during spike: {changed}")

            # Nothing of the artifact has been read or written yet. From here on a Stop is refused;
            # before here, one that already came ends the step unwritten.
            if not steps.seal(running):
                spike_md, review_md = (
                    "withheld" if watch else spike_md,
                    "withheld" if stage == "review" else review_md,
                )
                raise _Stopped()
            rounds_before = _rounds_before(row, channel, stage, directory, artifact)
            spike_md, review_md = _write_reply(
                row,
                directory,
                artifact,
                stage,
                watch,
                pieces,
                after_submit,
                channel,
                blocks,
                spike_md,
                review_md,
            )
            outcome = "done"
        except (asyncio.CancelledError, Exception) as e:
            judged = _judged(e, running, pieces, terminal, unit, stage)
            if judged is None:
                shutting_down = True
                raise
            outcome, detail, error, unopened = judged
        finally:
            outcome, detail, cost, stopped_by, review_md = await self._conclude(
                ledger=ledger,
                journal_key=journal_key,
                unit=unit,
                stage=stage,
                artifact=artifact,
                cwd=cwd,
                watch=watch,
                directory=directory,
                row=row,
                head=head,
                agent=agent,
                kw=dict(run_mod.session_kw(runs_as, cwd, workspace)),
                owner=owner,
                resume=resume,
                turn_kind=turn_kind,
                running=running,
                recorder=recorder,
                channel=channel,
                denials=denials,
                rounds_known=rounds_known,
                budget_left=budget_left,
                before=before,
                tree_changed=tree_changed,
                rounds_before=rounds_before,
                trial_at=trial_at,
                terminal=terminal,
                session_id=session_id,
                models_used=models_used,
                shutting_down=shutting_down,
                spike_md=spike_md,
                review_md=review_md,
                unopened=unopened,
                segment_done=segment_done,
                outcome=outcome,
                detail=detail,
                error=error,
                cost=cost,
            )

        yield (
            "done",
            {
                "unit": unit,
                "stage": stage,
                "outcome": outcome,
                "artifact": artifact if outcome == "done" else None,
                "session_id": session_id,
                "envelope": envelope,
                "error": detail,
                "cost": cost,
                "model": model,
                "model_source": model_source,
                **({"stopped_by": stopped_by} if outcome == "stopped" else {}),
                # What guard `stage-result` read, for `Answers.ingest` to apply.
                **(
                    {
                        "submitted": channel.inputs(
                            channel.received["object"], channel.received["revision"]
                        )
                    }
                    if channel is not None and channel.received is not None and outcome == "done"
                    else {}
                ),
            },
        )

    def _write_start(
        self,
        start_at: Any,
        resume: dict[str, Any] | None,
        journal_key: str,
        unit: str,
        stage: str,
        mode: str,
        started_by: str,
        prompt: str,
        envelope: list,
        row: Row,
        grant: Grant,
        head: str,
        cwd: str,
        recorder: Any,
        gate_reasons: tuple[str, ...],
        *,
        model: str | None,
        model_source: str,
        effort: str | None,
        effort_source: str,
        label_declared: str | None,
        label: str | None,
        label_source: str | None,
        impl_run: int | None,
        base: dict[str, Any] | None,
        plan_drift: dict[str, Any] | None,
        shortlist: dict[str, Any] | None,
        trial_record: dict[str, Any] | None,
        plan_map_record: dict[str, Any] | None,
        rerun: bool,
        rerun_note: str,
        agent: dict[str, Any] | None,
        blocks: list[str],
        process: str,
    ) -> Any:
        """The step's `start` record, and its `at`; `start_at` as it was for a step taken up again or
        with no journal."""
        if self.journal is None or resume is not None:
            return start_at
        # The autopilot tells a recording `ship` that ran out from a merging one by this field. Only
        # a `ship` whose gate named the merge already made carries it, by the code `recording-ship`.
        ship_extra = (
            {"ship_mode": "record"} if stage == "ship" and "recording-ship" in gate_reasons else {}
        )
        return self.journal.started(
            journal_key,
            unit,
            stage,
            mode,
            started_by=started_by,
            prompt_chars=len(prompt),
            # The parts the prompt was handed: the artifacts, records, answers, findings and data its
            # row declares, and the app's own sections; `[]` for a step taken up again.
            envelope=envelope,
            # Which build ran the step, so a measurement splits by what ran rather than by a date.
            **(
                {
                    "app_version": self.app.get("version", ""),
                    "app_commit": self.app.get("commit", ""),
                }
                if self.app is not None
                else {}
            ),
            grants=record(grant),
            blocks=blocks,
            max_turns=row.max_turns,
            # Which row ran: its pack, its hash, the keys the owner's layer set; the unit's process.
            **pack.stamp(pack.agent_for(process, stage) or "", process),
            head=head,
            model=model,
            model_source=model_source,
            effort=effort,
            effort_source=effort_source,
            label_declared=label_declared,
            label=label,
            label_source=label_source,
            **({"impl_run": impl_run} if impl_run is not None else {}),
            agents=SESSIONS_PER_STEP,
            base=base,
            # Which system prompt the step ran on. `""` means no preset, and nothing more: a step
            # without one whose `cwd` holds project instructions runs on those as its whole system
            # prompt, and only `instructions` below says whether it did.
            system_prompt="claude_code" if row.opens_anything else "",
            # Which project files `_options` puts into the system prompt, whole or as a line of contents.
            # Read again there, so a file edited in between is not seen here.
            instructions=instructions.read(cwd).record(),
            **({"plan_drift": plan_drift} if plan_drift is not None else {}),
            **({"shortlist": shortlist} if shortlist is not None else {}),
            # Every routine `impl`'s `model_trial`, and `ci_red`.
            **(trial_record or {}),
            # Every `impl` start, `bytes: 0` when the plan names no file.
            **({"plan_map": plan_map_record} if plan_map_record is not None else {}),
            # Only on a stage run again from the board.
            **({"rerun": True, "rerun_note": rerun_note} if rerun else {}),
            **ship_extra,
            # The agent's key, as its `end` names it, and its name when the step began (none for a
            # stage the agent table has no row for).
            agent=stage,
            **({"agent_name": agent["name"]} if agent is not None else {}),
            # Whose step this is, so the next start can tell one this process still runs from one the app
            # went down under.
            **({"run": recorder.run, "pid": os.getpid()} if recorder is not None else {}),
        ).get("at")

    def _trial_model(
        self, trial_at: Any, journal_key: str, unit: str, stage: str, said: str
    ) -> Any:
        """Tells a routine `impl`'s `start` the model the session's `init` named, once, when it
        comes, or `never-started` at the end. `trial_at` once told, or as it was when there is
        nothing to tell."""
        if not said or trial_at is None or self.journal is None:
            return trial_at
        try:
            self.journal.set_trial_model(journal_key, unit, stage, trial_at, said)
        except Exception:
            # A measurement, never a reason to fail the step.
            log.exception("the trial model of %s %s was not recorded", unit, stage)
        return None

    async def _open_main(
        self,
        cwd: str,
        prompt: str,
        session_id: str,
        turns_left: int,
        budget_left: float | None,
        runs_as: run_mod.Agent,
        gate: Gate,
        own: tuple[str, str] | None,
        running: steps.Running | None,
        owner: dict[str, Any],
        resume: dict[str, Any] | None,
        servers: dict[str, Any],
        turn_kind: str,
        workspace: str = "",
    ) -> AsyncIterator[tuple[str, Any]]:
        """The main reply's stream. An `opening` or `closing` turn taken up again has its main reply
        already. `gate` holds the grant issued for the step (`_features`), and the session's
        environment names the unit's scratch directories `own`.

        The recorder is handed the segment's `config` first, so it comes before any SDK event: the
        model and effort it runs on, the two ceilings as the session gets them, and the sources
        the owner keeps."""
        recorder = getattr(running.handle, "recorder", None) if running is not None else None
        if recorder is not None:
            run_mod.tell_config(
                recorder,
                runs_as.model,
                runs_as.effort,
                owner,
                turns_left,
                budget_left,
                gate.grant,
            )
        if turn_kind == "opening":
            return nothing()
        return run_mod.open_session(
            self.sessions,
            runs_as,
            cwd,
            prompt,
            session_id,
            turns=turns_left,
            budget=budget_left,
            gate=gate,
            workspace=workspace,
            # Only a board step has a row.
            **({"step": running.handle, "owner": owner} if running is not None else {}),
            # Only on a step taken up again.
            **({"resume_at": resume.get("safe_uuid")} if resume is not None else {}),
            # Only a step with a channel.
            **servers,
            # Only a grant holding helpers gets them (impl); the gate holds their ledger.
            **({"agents": definitions(gate.grant.helpers)} if gate.helpers is not None else {}),
            **({"unit_scratch": own} if own is not None else {}),
        )

    async def _conclude(
        self,
        *,
        journal_key: str,
        unit: str,
        stage: str,
        artifact: str,
        cwd: str,
        watch: str | None,
        directory: Path,
        row: Row,
        head: str,
        agent: dict[str, Any] | None,
        kw: dict[str, Any],
        owner: dict[str, Any],
        resume: dict[str, Any] | None,
        turn_kind: str,
        running: steps.Running | None,
        recorder: Any,
        channel: submit_mod.Channel | None,
        denials: Denials,
        rounds_known: tuple[int, ...],
        budget_left: float | None,
        before: tuple[str, str] | None,
        tree_changed: bool,
        rounds_before: set[int] | None,
        trial_at: Any,
        terminal: str,
        session_id: str,
        models_used: list[str],
        shutting_down: bool,
        spike_md: str | None,
        review_md: str | None,
        unopened: OpeningError | None,
        segment_done: dict[str, Any] | None,
        outcome: Outcome,
        detail: str,
        error: dict[str, str] | None,
        cost: dict[str, Any],
        ledger: Helpers | None = None,
    ) -> tuple[Outcome, str, dict[str, Any], str | None, str | None]:
        """The end of a step, whatever ended it: the turns that repair a reply, the attempt record and
        the `end` row. `(outcome, detail, cost, stopped_by, review_md)`. `ledger` tells what its helpers left
        untold first, while the recorder is open.

        `shutting_down` is set when the task is cancelled with no Stop behind it: the app is going
        down, and no `end` is what says so. What must be raised once the rest has done what it does
        for one is raised at the end.
        """
        if ledger is not None:
            ledger.close()
        # The budget a closing or repair turn is given. The CLI compares it only after the turn has
        # run, so on one turn it bounds nothing; one already spent is not passed at all, since `0`
        # reaches `_options` as no ceiling. `turn_spent` is what that turn would then have ended with.
        turn_spent = budget_left is not None and budget_left <= 0
        turn_budget = None if turn_spent else budget_left
        # Whether the `opening` or `closing` turn the update paused was reached again.
        turn_taken = False
        held: BaseException | None = None
        closing = opening = submit_turn = None
        message = str((resume or {}).get("message") or "")
        if watch and not shutting_down and outcome == "failed" and spike_md is None:
            spike_md, detail, held = await _spike_progress(
                cwd, watch, before, running, tree_changed, directory, artifact, detail, unit
            )
            shutting_down = held is not None
        if (
            unopened is not None
            and row.prose
            and not watch
            and outcome == "failed"
            and not shutting_down
        ):
            take_up = turn_kind == "opening"
            opening, detail, closing, cost, turn_taken, segment_done, held = await _repair_opening(
                self.sessions,
                cwd,
                artifact,
                session_id,
                denials,
                unopened,
                _turn_kw(kw, owner, "opening", running, turn_budget, take_up, resume),
                take_up,
                turn_spent,
                running,
                cost,
                directory,
                segment_done,
                detail,
                unit,
                stage,
                message,
            )
            shutting_down = held is not None
            if opening == "repaired":
                outcome, error = "done", None
                review_md = "round" if stage == "review" else review_md
        if channel is not None and outcome == "done" and not shutting_down:
            outcome, error, detail, submit_turn, cost, held = await _repair_submit(
                self.sessions,
                cwd,
                stage,
                artifact,
                session_id,
                denials,
                channel,
                _turn_kw(kw, owner, "submit", running, turn_budget),
                turn_spent,
                running,
                cost,
                outcome,
                error,
                detail,
                unit,
            )
            shutting_down = held is not None
        if (
            channel is not None
            and stage == submit_mod.ROUND
            and outcome == "done"
            and channel.received is not None
            and not shutting_down
        ):
            outcome, error, detail = _write_round(
                cwd,
                directory,
                artifact,
                head,
                agent,
                channel,
                rounds_before,
                rounds_known,
                unit,
                outcome,
                error,
                detail,
            )
        # The outcome is decided here, so the door closes here: a Stop that arrives while the attempt
        # record is captured below is refused (`Finishing`) rather than told "stopped" and logged as
        # something else. `seal` is False only when a Stop already came, and that one is honoured.
        stop_came = not steps.seal(running)
        # Only a step with a `running` entry can be stopped; after the seal no stop changes it.
        stopped_by = running.stopped_by if running is not None else None
        if not shutting_down and stop_came and outcome != "done":
            # Whatever the stop raised on its way in (a closed stream, a cancel, `_Stopped` at the seal),
            # a person asked, and that is the outcome. No `ResultMessage` came back, so nothing was
            # billed that this app saw: absent, not zero.
            outcome, error, detail = "stopped", None, f"stopped by {stopped_by}"
            cost = cost if terminal else {}
        # Nothing wrote a spike's or a review's mark: a Stop withheld it, or there was no file.
        spike_md = _unwritten(spike_md, bool(watch) and not shutting_down, outcome)
        review_md = _unwritten(review_md, stage == "review" and not shutting_down, outcome)
        # Not for an app going down: `drive` writes what it can, and no `end`.
        run_fields, stored, stored_from = (
            await _close_recorder(recorder, outcome, detail, unit, stage)
            if recorder is not None and not shutting_down
            else ({}, None, "")
        )
        cost_fields = _cost_fields(
            cost, recorder is not None and not shutting_down, outcome, stored, stored_from
        )
        if resume is not None and resume.get("raised") and cost_fields.get("cost_usd") is not None:
            # The CLI's total on a raised session holds the part before it: this `end` keeps its own
            # share, so a sum over ends counts each dollar once, and the session's total beside it.
            total = float(cost_fields["cost_usd"])
            cost_fields["session_cost_usd"] = total
            cost_fields["cost_usd"] = round(total - float(resume.get("spent_usd") or 0.0), 6)
        segment_fields = _segment_fields(owner, resume, segment_done)
        dropped = turn_kind == "opening" and not turn_taken and not shutting_down
        detail = _noted(
            detail,
            f"the {turn_kind} turn an update paused was not reached again, so it was dropped"
            if dropped
            else "",
        )
        # An app going down writes neither record. No `end` is what an interrupted step looks like.
        journal = self.journal
        record = journal is not None and not shutting_down
        pending: BaseException | None = None
        if journal is not None and record and outcome != "done":
            pending = await _attempt(
                journal,
                journal_key,
                unit,
                stage,
                cwd,
                session_id,
                outcome,
                terminal,
                error,
                cost,
                cost_fields.get("turns"),
            )
        extra = _round_counts(channel) if record and outcome == "done" else {}
        if record and trial_at is not None:
            self._trial_model(
                trial_at,
                journal_key,
                unit,
                stage,
                (running.handle.init_model if running is not None else "")
                or modeltrial.NEVER_STARTED,
            )
        if journal is not None and record:
            run_mod.ended(
                journal,
                journal_key,
                unit,
                stage,
                run_mod.status_of(outcome),
                outcome=outcome,
                agent=stage,
                session_id=session_id,
                model=kw.get("model"),
                denials=denials,
                cost=cost_fields,
                artifact=artifact if outcome == "done" else None,
                detail=detail or None,
                models_used=models_used or None,
                terminal=terminal or None,
                **(
                    {
                        "stopped_by": stopped_by,
                        **({} if cost else {"cost_unknown": True}),
                    }
                    if outcome == "stopped"
                    else {}
                ),
                # The `Author:` the artifact carries, as written: `""` for none, never checked against the
                # table and never a reason to refuse.
                **(
                    {"author": agents.author_of(_read(directory / artifact))}
                    if outcome == "done"
                    else {}
                ),
                **extra,
                **run_fields,
                # What a raise needs to go on from this end: both ceilings, the one hit, and whose
                # session it is.
                **(
                    {
                        "ceiling": run_mod.ceiling_of(terminal),
                        "max_turns": row.max_turns,
                        "max_budget_usd": row.max_budget_usd or None,
                        "cwd": cwd,
                        "owner": owner,
                    }
                    if outcome == "paused-budget"
                    else {}
                ),
                # Only a spike's `end` carries it.
                **({"spike_md": spike_md} if watch else {}),
                # Only a review's; `closing` only when that turn ran.
                **({"review_md": review_md} if stage == "review" else {}),
                **({"closing": closing} if closing is not None else {}),
                # Only once a reply lacked its opening.
                **({"opening": opening} if opening is not None else {}),
                **({"opening_reason": str(unopened)} if opening == "repaired" else {}),
                # Whether the run's `submit` held an object at the end, and the repair turn's cost when one
                # ran. Only a step with a channel carries either.
                **({"submitted": channel.received is not None} if channel is not None else {}),
                **({"submit_turn": submit_turn} if submit_turn is not None else {}),
                **segment_fields,
            )
        if held is not None:
            raise held
        if pending is not None:
            if not stop_came:
                raise pending
            # A Stop's cancel that landed in the capture rather than the session.
            _uncancel()
        return outcome, detail, cost, stopped_by, review_md
