"""The kernel: everything a feature (`coscc/features/`) sees of the core, and the only core
module it imports.

A feature ends in one `FEATURE`, a `Feature`. The core hands it a `Ctx` and asks it for its
routes, tables, schedule and agent parts. One catalog names every tool an agent may hold
(`BUILTINS`, and each feature's `Tool`); an agent's row names catalog entries, and a run gets
only what its grant holds. For each run, the kernel builds one `Facts` (its agent and grant) and
asks each part what it makes of it. A feature never writes a granted tool name: `granted`
derives `mcp__<server>__<name>`, and `Grant` refuses any other spelling. How the core hosts
features is in `coscc/http/plugin.py`.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, get_args

from claude_agent_sdk import McpServerConfig
from fastapi import Request
from fastapi.responses import StreamingResponse
from starlette.routing import BaseRoute

from coscc.agent.harness import child_env as child_env
from coscc.agent import policy
from coscc.agent.policy import Grant as Grant
from coscc.agent.policy import Row as Row
from coscc.agent.policy import bash_refused as bash_refused
from coscc.bus import Bus
from coscc.bus import Event as Event
from coscc.git import gh as gh
from coscc.git.gitops import GitError as GitError
from coscc.git.gitops import OwnTree as OwnTree
from coscc.git.gitops import commit_files as commit_files
from coscc.git.gitops import commits_between as commits_between
from coscc.git.gitops import create_branch as create_branch
from coscc.git.gitops import delete_merged_branch as delete_merged_branch
from coscc.git.gitops import detach_here as detach_here
from coscc.git.gitops import diff_u0 as diff_u0
from coscc.git.gitops import fetch_with_tags as fetch_with_tags
from coscc.git.gitops import is_ancestor as is_ancestor
from coscc.git.gitops import own_tree_remove as own_tree_remove
from coscc.git.gitops import push_branch as push_branch
from coscc.git.gitops import push_tag as push_tag
from coscc.git.gitops import remote_has_tag as remote_has_tag
from coscc.git.gitops import rev_parse as rev_parse
from coscc.git.gitops import show_file as show_file
from coscc.git.gitops import tags_merged as tags_merged
from coscc.git.gitops import worktree_add as worktree_add
from coscc.git.gitops import worktree_list as worktree_list
from coscc.loop import run as run
from coscc.store.db import Busy as Busy
from coscc.store.db import Data
from coscc.store.db import now as now
from coscc.store.journal import BELL as BELL
from coscc.store.journal import BadRecord as BadRecord
from coscc.store.journal import Intervention
from coscc.store.journal import Journal as Journal
from coscc.units import Invalid as Invalid
from coscc.units import cos_dir as cos_dir
from coscc.units.contracts import Output as Output
from coscc.units.contracts import Plan as Plan
from coscc.units.contracts import PlanStep as PlanStep
from coscc.store.journal import is_step as is_step
from coscc.units.read import Asked as Asked
from coscc.units.scratch import RAM_CAP

log = logging.getLogger(__name__)

# What one unit's ram scratch directory may hold before a write there is refused.
SCRATCH_RAM_CAP = RAM_CAP


# How long an open response may stream: the login guard asks for a live session once per request, so
# a stream ends within this and its reader asks again. Chosen, not measured.
STREAM_SECONDS = 30.0

# The word every record gets when the request names nobody. It is not an identity: the one
# password names nobody, so it says only that someone holding it or a live session acted.
OWNER = "owner"


# How a run of an agent ended (`coscc/runner/run.py`). `paused-budget`: it stopped at one of its
# two ceilings.
Status = Literal["done", "paused-budget", "failed", "refused", "cancelled"]
STATUSES: tuple[Status, ...] = get_args(Status)


@dataclass
class Run:
    """What one run of an agent left (`coscc/runner/run.py`): how it ended; its output (the object
    it handed back through `submit`, or chat's reply), `None` unless `done`; what it cost
    (`journal.COST_FIELDS` and `cost_usd`, `{}` when unknown); its turns; its session id; why, when
    it is not `done`; and its id in the run log (`run`), what `/api/runs/{run}` opens."""

    status: Status
    output: Any = None
    cost: dict[str, Any] = field(default_factory=dict)
    turns: int | None = None
    session: str = ""
    detail: str = ""
    run: str = ""


SERVER = re.compile(r"[a-z][a-z0-9-]*")
LOCAL = re.compile(r"[a-z][a-z0-9_]*")
# The kernel's own server, `submit`'s.
KERNEL_SERVER = "cos"


@dataclass(frozen=True)
class Facts:
    """One run, as the kernel knows it: its agent's key and, once issued, its grant. A part reads
    the grant, never the agent's name, to know what the run may do."""

    workspace: str
    workspace_key: str
    unit: str
    agent: str
    run: str
    # The unit's worktree; for a spike, the worktree it watches.
    tree: str
    directory: Path
    # A spike's throwaway directory, else `None`.
    scratch: str | None
    resumed: bool
    # The unit's plan record, handed to a board step's features; `None` elsewhere or with none.
    plan: Plan | None = None
    # What the run was issued (`coscc/runner/run.py`'s `issue`); the locked `Grant()` while a
    # catalog entry is asked whether it admits the run, and for a guard asked before a step that
    # pushes nothing.
    grant: Grant = field(default_factory=Grant)


def _any_run(_facts: Facts) -> bool:
    return True


# What a tool does: reads; writes the worktree; writes the app's own records; or reaches outside.
Effect = Literal["read", "write-worktree", "write-app", "external"]
# How much a call can cost when it goes wrong: `low` nothing, `medium` the run's own work, `high`
# beyond it.
Tier = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class Tool:
    """One catalog entry: a tool an agent's row may name. A built-in has no `server`; a feature's
    is one MCP server of `names`, made by `make` once per run, so a server is never shared. `when`
    is asked first, and a run it says no to gets neither the server nor its names. `uses` says
    what the run may use through it (`Grant.use`, as `(name, resource)`), asked with `when`."""

    name: str
    effect: Effect
    tier: Tier
    server: str = ""
    names: tuple[str, ...] = ()
    make: Callable[[Facts], McpServerConfig] | None = None
    when: Callable[[Facts], bool] = _any_run
    uses: Callable[[Facts], tuple[str, ...]] | None = None

    def __post_init__(self) -> None:
        if not self.server:
            return
        if not SERVER.fullmatch(self.server) or self.server == KERNEL_SERVER:
            raise ValueError(
                f"an MCP server name is [a-z][a-z0-9-]* and not 'cos': {self.server!r}"
            )
        if self.make is None or not self.names:
            raise ValueError(f"the tool {self.name!r} names its server's tools and makes it")
        for name in self.names:
            if not LOCAL.fullmatch(name) or "__" in name:
                raise ValueError(f"an MCP tool name is [a-z][a-z0-9_]* without '__': {name!r}")


# The catalog's built-in entries: Claude Code's own tools and the kernel's `submit` and `peers`,
# which the engine issues with the grant (`submit` to every agent with an output, `peers` with
# helpers) and no row names.
BUILTINS: tuple[Tool, ...] = (
    *(Tool(t, "read", "low") for t in policy.READ_TOOLS),
    *(Tool(t, "write-worktree", "medium") for t in policy.WRITE_TOOLS),
    Tool("Bash", "external", "high"),
    Tool(policy.AGENT_TOOL, "write-worktree", "medium"),
    Tool(policy.SEND_MESSAGE, "read", "low"),
    Tool("submit", "write-app", "low"),
    Tool("peers", "read", "low"),
)


@dataclass(frozen=True)
class Guard:
    """Words to deny a run, or `None` to abstain. It cannot allow."""

    name: str
    check: Callable[[Facts], str | None]


@dataclass(frozen=True)
class Block:
    """A named block of the prompt. An empty string adds nothing. A render that waits on
    something is a coroutine, awaited before the session starts. `tool` names the catalog entry
    it teaches: the block is shown only to a run whose grant holds it ("" for every run)."""

    name: str
    render: Callable[[Facts], str | Awaitable[str]]
    tool: str = ""


@dataclass(frozen=True)
class Parts:
    tools: tuple[Tool, ...] = ()
    guards: tuple[Guard, ...] = ()
    blocks: tuple[Block, ...] = ()


def _always(_feature: str, _workspace: str) -> bool:
    return True


@dataclass(frozen=True)
class Hooks:
    """The parts, each tagged with its feature, and whether a feature is on for a workspace."""

    parts: tuple[tuple[str, Parts], ...] = ()
    enabled: Callable[[str, str], bool] = _always

    def catalog(self) -> dict[str, Tool]:
        """Every tool a row may name: `BUILTINS` and each feature's, on or off."""
        return {t.name: t for t in (*BUILTINS, *(t for _, p in self.parts for t in p.tools))}

    def reads(self) -> tuple[str, ...]:
        """The catalog names whose effect is `read`."""
        return tuple(n for n, t in self.catalog().items() if t.effect == "read")

    def on(self, workspace: str) -> Parts:
        """Only what is on for this workspace."""
        on = [p for feature, p in self.parts if self.enabled(feature, workspace)]
        return Parts(
            tools=tuple(t for p in on for t in p.tools),
            guards=tuple(g for p in on for g in p.guards),
            blocks=tuple(b for p in on for b in p.blocks),
        )

    def held(self, row: Row, workspace: str) -> tuple[Tool, ...]:
        """The features' tools `row` names that are on for this workspace."""
        return tuple(t for t in self.on(workspace).tools if t.name in row.tools)

    def tools_for(self, facts: Facts, row: Row) -> tuple[Tool, ...]:
        """`held`'s tools that this run's `when` lets through."""
        return tuple(t for t in self.held(row, facts.workspace) if t.when(facts))

    def refusal(self, facts: Facts) -> str:
        """The words of the first guard on for the run's workspace that denies it, or `""` when
        all abstain. A guard that raises denies: a run is never let through by a check that could
        not be made."""
        for guard in self.on(facts.workspace).guards:
            try:
                words = guard.check(facts)
            except Exception as e:
                log.exception("guard %s of a feature failed", guard.name)
                return f"{guard.name}: failed ({type(e).__name__})"
            if words is not None:
                return f"{guard.name}: {words}"
        return ""

    def blocks_for(self, facts: Facts) -> tuple[Block, ...]:
        """The blocks on for the run's workspace whose tool its grant holds."""
        held = (*facts.grant.tools, *facts.grant.held)
        return tuple(b for b in self.on(facts.workspace).blocks if not b.tool or b.tool in held)


def granted(tools: tuple[Tool, ...]) -> tuple[str, ...]:
    """The names the session sees, in order."""
    return tuple(f"mcp__{t.server}__{n}" for t in tools for n in t.names)


def facts(
    *,
    workspace: str,
    workspace_key: str,
    unit: str,
    agent: str,
    run: str,
    cwd: str,
    watch: str | None,
    directory: Path,
    resumed: bool,
    plan: Plan | None = None,
    grant: Grant | None = None,
) -> Facts:
    """`watch` is set when the run is a spike, whose `cwd` is its throwaway directory."""
    return Facts(
        workspace=workspace,
        workspace_key=workspace_key,
        unit=unit,
        agent=agent,
        run=run,
        tree=watch or cwd,
        directory=directory,
        scratch=cwd if watch else None,
        resumed=resumed,
        plan=plan,
        grant=grant if grant is not None else Grant(),
    )


State = Literal["off", "pilot", "on"]
STATES: tuple[State, ...] = get_args(State)
Arm = Literal["on", "off"]


def arm_of(state: State, unit: str) -> Arm | None:
    """The branch a run belongs to: none when `off`; under `pilot` a unit whose `NNNN` is even is
    `on` and an odd one `off`; under `on` every unit is `on`."""
    if state == "off":
        return None
    if state == "on":
        return "on"
    number = unit[:4]
    return "on" if number.isdigit() and int(number) % 2 == 0 else "off"


@dataclass(frozen=True)
class Units:
    """What a feature reads and makes of a workspace's units."""

    # Checks the workspace, raising `Invalid`, and returns how the run log names it.
    key: Callable[[str], str]
    # `(workspace path, slug, brief)`: a new unit, made as `POST /api/units` makes one, and its
    # name. It touches no shortlist.
    create_unit: Callable[[str, str, str], Awaitable[str]]
    # `(workspace path)`: the workspace's own detached tree, made or moved to the fetched
    # `origin/main`, as `(path, sha)`. Raises `GitError` when git refuses, `Invalid` when the
    # workspace is not known.
    main_tree: Callable[[str], Awaitable[tuple[str, str]]]
    # `(workspace path, fresh)`: the board's units. Not `fresh`: the board held, as the page reads
    # it; `fresh`: the loop asked again now. `Invalid` when the loop cannot be read.
    units: Callable[[str, bool], Awaitable[list[dict[str, Any]]]]
    # `(workspace path, fresh)`: the open pull requests as the board holds `gh pr list`'s answer,
    # asked only when none is held or `fresh`; `gh`'s error as a string.
    open_prs: Callable[[str, bool], Awaitable[list[dict[str, Any]] | str]]
    # `(workspace path, name)`: the feature's own worktree `name`, beside the units' trees, worked
    # out again on every call; the `OwnTree.path` the writers check. `GitError` when unsafe.
    own_tree: Callable[[str, str], Path]


@dataclass(frozen=True)
class Runs:
    """The run log."""

    journal: Callable[[], Journal | None]
    # `(workspace path, after, limit)`: every time a person stepped in there past the time
    # `after` (`""`: from the first), oldest first (`coscc/runner/interventions.py`). Blocking.
    interventions: Callable[[str, str, int], list[Intervention]]


@dataclass(frozen=True)
class Agents:
    # `(workspace path, kind, prompt)`: one run of the feature's `Session` `kind`
    # (`coscc/runner/run.py`), which hands its object back through `submit`, as `Run.output`.
    # `Invalid` while another such session of the workspace runs or an update is under way. One an
    # update paused is `cancelled`, with no cost: the run log's `end` holds it.
    session: Callable[[str, str, str], Awaitable[Run]]


@dataclass(frozen=True)
class Settings:
    """What a person chose for this feature, per workspace path."""

    # What a person chose there, else the feature's `default`; a workspace the app does not know
    # counts as the default.
    state: Callable[[str], State]
    # `state` is not `off`.
    enabled: Callable[[str], bool]
    # `(workspace path, unit)`: `arm_of` the state now.
    arm: Callable[[str, str], Arm | None]
    # The hours of its `schedule` there, `0` for off.
    schedule: Callable[[str], int]
    # `(workspace path, hours)`: set them, one of its `schedule.hours`.
    set_schedule: Callable[[str, int], None]


@dataclass(frozen=True)
class Ctx:
    """What one feature sees of the core; the core builds one per feature."""

    units: Units
    runs: Runs
    agents: Agents
    store: Data
    bus: Bus
    settings: Settings
    # Raises `Invalid` while an update is under way.
    refuse_updating: Callable[[], None]
    # The feature's slow reads (`gh`) held and asked again in the background; the core cancels and
    # waits for each one when it goes down, and an answer that changed reads the board again.
    asks: Asked
    # `(tree, pull request)`: its required checks as `gh pr checks --required` says, or `gh`'s
    # error as a string.
    required_checks: Callable[[str, int], Awaitable[list[dict[str, Any]] | str]]


@dataclass(frozen=True)
class Schedule:
    """How often the core asks a feature to run on its own in a workspace where it is not `off`."""

    # What Settings offers, in hours, `0` being off. Each workspace's choice is the pref
    # `features.schedule`, `{feature: {workspace key: hours}}`.
    hours: tuple[int, ...]
    # The choice of a workspace nobody chose one for.
    default: int
    # `(ctx, workspace path, hours)`: asked every `TICK_SECONDS` while the choice is not `0`;
    # the feature decides whether that many hours passed since its last run.
    tick: Callable[[Ctx, str, int], Awaitable[None]]


@dataclass(frozen=True)
class Session:
    """A paid session a feature runs through `Ctx.agents.session`: no stage, and it hands one object
    back through `submit`. `kind` names its row, its attempts and their bus events
    (`<kind>.queued`, `.running`, `.ended`, `.refused`)."""

    kind: str
    row: Row
    # The declaration of the object it hands back, `kind: session` (`coscc/units/contracts.py`).
    output: Output
    # What the `submit` tool tells the session it is for.
    purpose: str
    # Its own row: the model and effort it runs on unless the Agents page overrides them under
    # `kind`; `None` is the app's default.
    model: str | None = None
    effort: str | None = None
    # Whether `row.max_turns` holds below the floor a session that submits gets, because one
    # more turn could pass its budget; a refused object is then not submitted again.
    own_turns: bool = False


@dataclass(frozen=True)
class Feature:
    name: str
    routes: Callable[[Ctx], Sequence[BaseRoute]]
    # `CREATE TABLE IF NOT EXISTS ...` statements, run once at build through `Data.write()`.
    tables: tuple[str, ...] = ()
    # What the feature hands the agent's steps, called once at build like `routes`.
    agent: Callable[[Ctx], Parts] | None = None
    # The state of a workspace nobody chose one for: `on` or `off`.
    default: State = "on"
    # Whether `pilot` may be chosen: half the units get the feature (`arm_of`).
    pilot: bool = False
    # `(ctx, workspace path)`: one sentence for Settings, and whether `pilot` or `on` may be
    # chosen there now. Quick: it is asked on every read of the panel.
    status: Callable[[Ctx, str], tuple[str, bool]] | None = None
    # `(ctx, workspace path, state)`: told once a person set a state. Quick: it schedules long
    # work and returns.
    on_set: Callable[[Ctx, str, State], None] | None = None
    schedule: Schedule | None = None
    sessions: tuple[Session, ...] = ()
    # One fixed sentence, 100 characters at most: what the feature does, shown under its name.
    summary: str = ""


async def body(request: Request) -> dict[str, Any]:
    """The request's JSON object; anything else is the caller's mistake."""
    try:
        parsed = await request.json()
    except json.JSONDecodeError, ValueError:
        parsed = None
    if not isinstance(parsed, dict):
        raise Invalid("send a JSON object")
    return parsed


def line(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj).encode() + b"\n"


async def ndjson(stream: AsyncIterator[tuple[str, Any]], what: str) -> StreamingResponse:
    """`(kind, payload)` items as NDJSON: a `chunk` line per text, then one `done`.

    The first item is pulled here, so a refusal before any output is still a status code; a
    failure after it arrives as an `error` line, and the caller must read to the last line.
    """
    try:
        first = await anext(stream)
    except StopAsyncIteration:
        raise Invalid(f"{what} produced nothing") from None

    def out(kind: str, payload: Any) -> bytes:
        return line({"type": kind, **({"text": payload} if kind == "chunk" else payload)})

    async def lines() -> AsyncIterator[bytes]:
        try:
            yield out(*first)
            async for kind, payload in stream:
                yield out(kind, payload)
        except Exception as e:
            log.exception("the stream of %s failed", what)
            yield line({"type": "error", "error": f"{type(e).__name__}: {e}"})

    return StreamingResponse(lines(), media_type="application/x-ndjson")
