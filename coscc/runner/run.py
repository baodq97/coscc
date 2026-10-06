"""One way to run an agent: `run(agent, given, ctx=...)`, for every session the app opens.

A run resolves nothing itself: the caller hands the `Agent` (its key, row with both ceilings,
model, effort) and the `Input` (where, the prompt, whose, its own `start` and owner fields). The
run writes `start`, records its events (`step_runs`/`step_events`, unit `""` allowed), opens the
session under the gate of the grant it issues (`issue`), collects `submit`, maps how it ended to a `kernel.Status`, writes `end`
with the same fields for every agent (`ended`), and goes on from a `suspend` row under what is
left of its ceilings. A caller keeps only its own work: the estimate its records, a feature its
object, Gebo its rebase, chat its reply.

`Runner` (a board step) writes its artifact between the session and the `end`, so it composes the
same parts (`open_session`, `ended`, `status_of`) around that work instead of calling `run`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from coscc.agent import pack, policy
from coscc.agent.helpers import Denials, Gate
from coscc.agent.policy import Grant, Row
from coscc.agent.sessions import Refused, StepHandle, Suspended, secrets_of
from coscc.agent.transcript import ceilings_left
from coscc.config import Config
from coscc.kernel import Run, Status
from coscc.runlog.events import Recorder
from coscc.runner.attempt import CLAUDE_CODE_PRESET
from coscc.store.db import Busy, Data
from coscc.store.journal import BadRecord, Journal, Outcome
from coscc.units import submit as submit_mod
from coscc.units.scratch import RAM_CAP

log = logging.getLogger(__name__)

# The runs this process runs now, by their `run`: what `/api/runs/{run}` follows live.
LIVE: dict[str, Recorder] = {}

# How the detail of a run whose `submit` stayed empty begins.
NO_SUBMISSION = "no-submission"

# How each `Status` is written as the `end`'s `outcome` (`journal.OUTCOMES`), which the board and the
# loop's readers read.
OUTCOME: dict[Status, Outcome] = {
    "done": "done",
    "paused-budget": "paused-budget",
    "failed": "failed",
    "refused": "failed",
    "cancelled": "cancelled",
}


@dataclass(frozen=True)
class Agent:
    """Who runs: its key (a stage, `estimate`, `integrate`, `chat`, a feature's session), its row
    with both ceilings resolved, and what it runs on, with where each came from (`sources`:
    `model_source`, `effort_source`, `max_turns_source`, `max_budget_source`). `name` is the
    agent's name for its `start`; `settings` its commit attribution; `preset` whether the session
    gets Claude Code's system prompt; `system` the row's body, appended to it, or the whole system
    prompt without it."""

    key: str
    row: Row
    model: str | None = None
    effort: str | None = None
    sources: Mapping[str, str] = field(default_factory=dict)
    name: str = ""
    settings: str | None = None
    preset: bool = False
    system: str = ""


@dataclass(frozen=True)
class Input:
    """What one run is asked: `prompt` in `cwd`, for the workspace `workspace` (the run log's key,
    whose path `workspace_dir` is asked about membership), of `unit` (`""` for none), recorded
    under `stage` (the agent's key when empty). `start` and `owner` are the caller's own fields of
    the `start` record and of the owner a `suspend` row keeps. `branch` is the branch its grant may
    push and `lease` the head each push must carry (Gebo's), "" for none; `channel` the `submit` it hands its object
    back through (`None`: its output is its reply). `session_id` continues a session (chat);
    `keep` keeps its client for the next turn (chat). `resume` is a `suspend` row to go on from."""

    cwd: str
    prompt: str
    workspace: str
    workspace_dir: str = ""
    unit: str = ""
    stage: str = ""
    started_by: str = "person"
    start: Mapping[str, Any] = field(default_factory=dict)
    owner: Mapping[str, Any] = field(default_factory=dict)
    branch: str = ""
    lease: str = ""
    channel: Any = None
    session_id: str | None = None
    keep: bool = False
    resume: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class Ctx:
    """What a run is run with: the sessions, the run log (`None`: nothing is recorded), the data
    root its events go to (`None`: no events), and the build that runs it."""

    sessions: Any
    journal: Journal | None
    data_dir: str | Path | None = None
    app: Mapping[str, str] | None = None


# Told the run as it ended, before its `end`: may set its `status` and `detail`, and returns the
# fields the caller adds to the `end`.
Finish = Callable[[Run], Awaitable[Mapping[str, Any]]]


def issue(
    row: Row,
    sessions: Any,
    *,
    cwd: str,
    unit_dir: str = "",
    scratch: tuple[str, str] | None = None,
    branch: str = "",
    lease: str = "",
    features: Collection[str] = (),
    held: tuple[str, ...] = (),
    mcp: tuple[str, ...] = (),
    use: tuple[tuple[str, str], ...] = (),
) -> Grant:
    """The grant of one run, issued as it opens and held by its session's gate only: no grant, no
    action (`policy.critical`).

    - `write`: its `cwd` and `unit_dir` (none for a spike), only when `row` holds a write tool;
      the unit's `scratch` only when it holds Bash too, under `scratch.RAM_CAP`.
    - `branch` and `lease` as the caller found them: the branch the worktree stands on as the
      session opens (none on the trunk, detached, or for a spike) and Gebo's lease.
    - `helpers`: the row's helper rows, only when `row` holds `Agent`.
    - `mcp`: `submit` when `row` submits, `peers` with helpers, and `mcp`, the full names of the
      catalog tools `row` lists, on for the workspace and admitted (`kernel.Hooks.tools_for`).
    - `use`: what those tools may use (`kernel.Tool.uses`).
    - `tools`: the names in `row` that are no feature's (`features`, the catalog's feature
      entries), Claude Code's own; `held`: the features' entries `row` names that are on, whether
      or not their `when` admitted the run.
    - `secrets`: this app's (`sessions.secrets_of`), the deny list no grant lifts; never empty.
    """
    config = getattr(sessions, "config", None)
    secrets = secrets_of(config if config is not None else Config())
    if not secrets:
        raise ValueError("a grant needs the secrets it denies")
    writes = any(t in policy.WRITE_TOOLS for t in row.tools)
    bash = writes and any(t in policy.EXEC_TOOLS for t in row.tools)
    helpers = row.helpers if policy.AGENT_TOOL in row.tools else ()
    return Grant(
        cwd=cwd,
        write=tuple(dict.fromkeys(p for p in (cwd, unit_dir) if p)) if writes else (),
        scratch=scratch if bash else None,
        ram_cap=RAM_CAP if bash and scratch else 0,
        branch=branch,
        lease=lease if branch else "",
        helpers=helpers,
        mcp=(
            *((policy.SUBMIT_TOOL,) if row.submits else ()),
            *((policy.PEERS_TOOL,) if helpers else ()),
            *mcp,
        ),
        use=use,
        secrets=secrets,
        home=config.home if config is not None else "",
        tools=tuple(t for t in row.tools if t not in features),
        held=held,
    )


def ceiling_of(terminal: str) -> str:
    """Which ceiling the session said it stopped at, `turns` or `usd`; `""` when at neither. The
    SDK puts it in `terminal_reason` (`max_turns`), or in `subtype` (`error_max_turns`,
    `error_max_budget_usd`) for an older CLI."""
    text = (terminal or "").lower()
    if "max_turns" in text:
        return "turns"
    return "usd" if "budget" in text else ""


def status_of(outcome: str) -> Status:
    """The `Status` a board step's `outcome` is: a person's Stop is `cancelled`, any other that is
    not `done` or `paused-budget` is `failed`."""
    return _STATUS.get(outcome, "failed")


_STATUS: dict[str, Status] = {
    "done": "done",
    "paused-budget": "paused-budget",
    "stopped": "cancelled",
    "cancelled": "cancelled",
}


def ended(
    journal: Journal,
    workspace: str,
    unit: str,
    stage: str,
    status: Status,
    *,
    agent: str,
    session_id: str,
    model: str | None,
    denials: Denials,
    cost: Mapping[str, Any],
    outcome: Outcome | None = None,
    **extra: Any,
) -> None:
    """The `end` of any run, with the fields every agent's has: `agent`, `status`, `session_id`,
    `model`, the refusals (`denials`, `denied`, `background`, `classified`) and the cost fields
    (`cost_usd` absent when unknown). `outcome` is the stage's own word when it has one (`stopped`),
    else `status`'s."""
    journal.finished(
        workspace,
        unit,
        stage,
        outcome or OUTCOME[status],
        agent=agent,
        status=status,
        session_id=session_id,
        model=model,
        denials=denials.count,
        denied=denials.reasons or None,
        background=denials.background,
        classified=denials.classified,
        **cost,
        **extra,
    )


def recorder_for(ctx: Ctx, workspace: str, unit: str, stage: str) -> Recorder | None:
    """A new run's recorder, in `LIVE` until the run ends; `None` with no data root or run log."""
    if ctx.data_dir is None or ctx.journal is None:
        return None
    recorder = Recorder(
        uuid.uuid4().hex,
        Data(ctx.data_dir),
        str(ctx.journal.working_dir),
        workspace,
        unit,
        stage,
    )
    LIVE[recorder.run] = recorder
    return recorder


def tell_config(
    recorder: Any,
    model: str | None,
    effort: str | None,
    sources: Mapping[str, str],
    turns: int,
    budget: float | None,
    grant: Grant | None = None,
) -> None:
    """The recorder's `config` event: the values the session about to open is handed, with where
    each came from, and what its grant holds (`policy.granted`). Nothing here may change the run."""
    try:
        recorder.config(
            granted=policy.granted(grant) if grant is not None else [],
            model=model,
            model_source=str(sources.get("model_source") or ""),
            effort=effort,
            effort_source=str(sources.get("effort_source") or ""),
            max_turns=turns,
            max_turns_source=str(sources.get("max_turns_source") or ""),
            max_budget_usd=budget,
            max_budget_source=str(sources.get("max_budget_source") or ""),
        )
    except Exception:
        log.exception("the config of a session was not recorded")


def session_kw(agent: Agent, cwd: str, workspace: str = "") -> Mapping[str, Any]:
    """What every session of a run is given beyond its prompt, gate and ceilings, each named only
    when it has one: the workspace (only when it is not `cwd`), the model, the effort, Claude
    Code's system prompt and the commit attribution beside it."""
    return {
        **({"workspace": workspace} if workspace and workspace != cwd else {}),
        **({"model": agent.model} if agent.model is not None else {}),
        **({"effort": agent.effort} if agent.effort is not None else {}),
        **({"system_prompt": system_prompt(agent)} if agent.preset or agent.system else {}),
        **({"settings": agent.settings} if agent.settings is not None and agent.preset else {}),
    }


def system_prompt(agent: Agent) -> dict[str, Any]:
    """Claude Code's preset with the row's body appended, or the body as the whole prompt."""
    if not agent.preset:
        return {"type": "custom", "prompt": agent.system}
    return {**CLAUDE_CODE_PRESET, **({"append": agent.system} if agent.system else {})}


def open_session(
    sessions: Any,
    agent: Agent,
    cwd: str,
    prompt: str,
    session_id: str | None,
    *,
    turns: int,
    budget: float | None,
    gate: Gate,
    workspace: str = "",
    **extra: Any,
) -> Any:
    """The one way a run's session opens: `session_kw`, the built-in tools of the gate's grant,
    under `gate` and the two ceilings. `extra` is passed as it is (`step`, `owner`, `resume_at`, `mcp_servers`,
    `agents`, `unit_scratch`, `recorder`)."""
    return sessions.stream(
        cwd,
        prompt,
        session_id or None,
        max_turns=turns,
        gate=gate,
        # The grant's own tools, `[]` when none: `None` would fall back to `COS_TOOLS`.
        tools=list(gate.grant.tools),
        max_budget_usd=budget,
        **session_kw(agent, cwd, workspace),
        **extra,
    )


def model_of(ctx: Ctx, agent: Agent) -> str | None:
    """The model the run's session opens on, as its `start` and `end` say: the agent's, else this
    app's (`Sessions.stream`'s own fallback); `None` leaves it to the CLI."""
    if agent.model is not None:
        return agent.model
    config = getattr(ctx.sessions, "config", None)
    return getattr(config, "model", None)


def _started(ctx: Ctx, agent: Agent, given: Input, stage: str, run: str, grant: Grant) -> Any:
    """The run's `start`, and its `at`; `None` when the run log refused it."""
    if ctx.journal is None:
        return None
    row = agent.row
    try:
        return ctx.journal.started(
            given.workspace,
            given.unit,
            stage,
            "manual",
            started_by=given.started_by,
            prompt_chars=len(given.prompt),
            grants=policy.record(grant),
            max_turns=row.max_turns,
            max_budget_usd=row.max_budget_usd or None,
            model=model_of(ctx, agent),
            model_source=str(agent.sources.get("model_source") or ""),
            effort=agent.effort,
            effort_source=str(agent.sources.get("effort_source") or ""),
            **(
                {"app_version": ctx.app.get("version", ""), "app_commit": ctx.app.get("commit", "")}
                if ctx.app is not None
                else {}
            ),
            agent=agent.key,
            **({"agent_name": agent.name} if agent.name else {}),
            **pack.stamp(agent.key),
            run=run,
            pid=os.getpid(),
            **given.start,
        ).get("at")
    except BadRecord, Busy:
        log.exception("the start of a %s run was not recorded", agent.key)
        return None


async def _close(recorder: Recorder | None, run: Run) -> dict[str, Any]:
    """The recorder closed, everything on disk, and what the `end` says of it."""
    if recorder is None:
        return {}
    try:
        lost = await recorder.close(OUTCOME[run.status], run.detail)
    except Exception:
        log.exception("the events of run %s were not closed", recorder.run)
        lost = max(1, int(recorder.seq or 0))
    finally:
        LIVE.pop(recorder.run, None)
    return {"run": recorder.run, "events_lost": lost}


async def run(
    agent: Agent, given: Input, *, ctx: Ctx, finish: Finish | None = None
) -> AsyncGenerator[tuple[str, Any], None]:
    """Yield `("chunk", text)` and `("tool", name)` as the reply arrives, then one `("done", Run)`.

    A session at a ceiling is `paused-budget`; one refused before it opened `refused`; a channel
    left empty `failed`. `finish` hears the run before its `end` (the estimate writes its records
    there, Gebo reads GitHub). An update that pauses the session raises `Suspended` and writes no
    `end`: its `suspend` row is the end, and `given.resume` takes it up under what is left of its
    two ceilings, writing no second `start`. A cancel writes a `cancelled` `end` and goes on.
    """
    stage = given.stage or agent.key
    recorder = recorder_for(ctx, given.workspace, given.unit, stage)
    out = Run("failed", run=recorder.run if recorder is not None else "")
    grant = issue(agent.row, ctx.sessions, cwd=given.cwd, branch=given.branch, lease=given.lease)
    text, turns, budget, used_up, start_at = _begin(ctx, agent, given, stage, out, grant)
    owner = _owner(agent, given, stage, start_at, out.run)
    denials = Denials()
    if recorder is not None:
        denials.listener = recorder.denied
        recorder.start()
        tell_config(recorder, agent.model, agent.effort, agent.sources, turns, budget, grant)
    reply, terminal, models_used = "", used_up, []
    channel = given.channel
    try:
        bad = pack.problems(agent.key) if given.resume is None and pack.row(agent.key) else []
        if bad:
            raise Refused(f"agent-invalid: {agent.key}'s row cannot run: {'; '.join(bad)}")
        if not used_up:
            stream = _stream(
                ctx, agent, given, text, out.session, turns, budget, Gate(grant, denials), owner
            )
            async for kind, payload in stream:
                if kind == "chunk":
                    reply += payload
                    yield ("chunk", payload)
                elif kind == "tool":
                    yield ("tool", payload)
                elif kind == "session":
                    out.session = str(payload)
                elif kind == "done":
                    out.session = str(payload.get("session_id") or out.session)
                    out.cost = dict(payload.get("cost") or {})
                    terminal = str(payload.get("terminal_reason") or "")
                    models_used = list(payload.get("models_used") or [])
        _judge(out, agent.key, terminal, channel, reply)
    except Suspended:
        # An update paused it and wrote its `suspend` row; the next start goes on from there.
        await _abandon(recorder)
        raise
    except asyncio.CancelledError, GeneratorExit:
        await _cancelled(ctx, agent, given, stage, out, denials, models_used, terminal, recorder)
        raise
    except Refused as e:
        out.status, out.detail = "refused", str(e)
    except Exception as e:
        log.exception("the %s session failed", agent.key)
        out.detail = f"the session failed: {e}"
    out.turns = out.cost.get("turns")
    try:
        extra = await _finished(finish, out, agent.key)
    except asyncio.CancelledError:
        await _cancelled(ctx, agent, given, stage, out, denials, models_used, terminal, recorder)
        raise
    if out.status != "done":
        out.output = None
    closed = await _close(recorder, out)
    if channel is not None:
        closed.update(guard=submit_mod.RUN_SUBMITTED, submitted=submit_mod.submitted(channel))
    _end(ctx, agent, given, stage, out, denials, models_used, terminal, {**closed, **extra})
    yield ("done", out)


async def _finished(finish: Finish | None, out: Run, key: str) -> dict[str, Any]:
    """What the run's `finish` adds to its `end`; a `finish` that fails fails the run, and the
    `end` is still written."""
    if finish is None:
        return {}
    try:
        return dict(await finish(out))
    except Exception as e:
        log.exception("the %s run's finish failed", key)
        out.status, out.detail = "failed", f"the run's finish failed: {e}"
        return {}


async def _cancelled(
    ctx: Ctx,
    agent: Agent,
    given: Input,
    stage: str,
    out: Run,
    denials: Denials,
    models_used: list[str],
    terminal: str,
    recorder: Recorder | None,
) -> None:
    """A cancel during the session or its `finish`: the run ends `cancelled` and leaves `LIVE`."""
    out.status, out.detail = "cancelled", "the run was cancelled before it ended"
    await _abandon(recorder)
    _end(ctx, agent, given, stage, out, denials, models_used, terminal, {"run": out.run})


def _stream(
    ctx: Ctx,
    agent: Agent,
    given: Input,
    text: str,
    session_id: str,
    turns: int,
    budget: float | None,
    gate: Gate,
    owner: dict[str, Any],
) -> Any:
    """The run's session: under the gate of its grant, with its `submit`, going on from a `suspend` row's safe
    point when there is one."""
    channel, resume = given.channel, given.resume
    recorder = LIVE.get(str(owner["run"]))
    return open_session(
        ctx.sessions,
        agent,
        given.cwd,
        text,
        session_id,
        turns=turns,
        budget=budget,
        gate=gate,
        workspace=given.workspace_dir,
        owner=owner,
        **({"resume_at": resume.get("safe_uuid")} if resume is not None else {}),
        **({"mcp_servers": {submit_mod.SERVER: channel.server()}} if channel is not None else {}),
        # A kept client is the session layer's (chat); any other is closed when it ends.
        **({"recorder": recorder} if given.keep else {"step": StepHandle(recorder=recorder)}),
    )


def _begin(
    ctx: Ctx, agent: Agent, given: Input, stage: str, out: Run, grant: Grant
) -> tuple[str, int, float | None, str, Any]:
    """`(text, turns, budget, used_up, start_at)` the session opens with: the prompt under the
    row's two ceilings, after its `start`; or a `suspend` row's message under what is left of
    them, with the `start` it had. `out` gets the session id it goes on from."""
    row, resume = agent.row, given.resume
    if resume is None:
        out.session = given.session_id or ""
        start_at = _started(ctx, agent, given, stage, out.run, grant)
        return given.prompt, row.max_turns, row.max_budget_usd or None, "", start_at
    out.session = str(resume.get("session_id") or "")
    turns, budget, used_up = ceilings_left(row.max_turns, row.max_budget_usd, dict(resume))
    start_at = (resume.get("owner") or {}).get("start_at")
    return str(resume.get("message") or ""), turns, budget, used_up, start_at


async def _abandon(recorder: Recorder | None) -> None:
    """What can be written of the run is, with no `end` event."""
    if recorder is not None:
        await recorder.abandon()
        LIVE.pop(recorder.run, None)


def _owner(agent: Agent, given: Input, stage: str, start_at: Any, run: str) -> dict[str, Any]:
    """Whose session this is, for a `suspend` row: all the next start needs to take it up again."""
    return {
        "kind": stage,
        "workspace": given.workspace,
        "workspace_dir": given.workspace_dir or given.cwd,
        "unit": given.unit,
        "stage": stage,
        "start_at": start_at,
        "max_turns": agent.row.max_turns,
        "max_budget_usd": agent.row.max_budget_usd,
        "effort": agent.effort,
        **agent.sources,
        "run": run,
        **given.owner,
    }


def _judge(out: Run, key: str, terminal: str, channel: Any, reply: str) -> None:
    """How a session that ended by itself ended: at a ceiling, with its channel empty, or done."""
    if ceiling_of(terminal):
        out.status, out.detail = "paused-budget", f"stopped at its ceiling: {terminal}"
    elif channel is not None and not submit_mod.submitted(channel):
        out.detail = f"{NO_SUBMISSION}: the session handed back no {key} through submit"
    else:
        out.status = "done"
        out.output = channel.object() if channel is not None else reply


def _end(
    ctx: Ctx,
    agent: Agent,
    given: Input,
    stage: str,
    out: Run,
    denials: Denials,
    models_used: list[str],
    terminal: str,
    extra: Mapping[str, Any],
) -> None:
    if ctx.journal is None:
        return
    try:
        ended(
            ctx.journal,
            given.workspace,
            given.unit,
            stage,
            out.status,
            agent=agent.key,
            session_id=out.session,
            model=model_of(ctx, agent),
            denials=denials,
            cost=out.cost if out.cost else {"cost_unknown": True},
            detail=out.detail or None,
            models_used=models_used or None,
            terminal=terminal or None,
            **extra,
        )
    except BadRecord, Busy:
        log.exception("the end of a %s run was not recorded", agent.key)
