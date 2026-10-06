"""How the core hosts features: their state per workspace, the `Ctx` each gets, their tables,
agent parts and schedules, and the Settings panel's view of them. A feature itself sees only
`coscc/kernel.py`.

A feature's state per workspace, `off`, `pilot` or `on`, is the pref `features.state`,
`{feature: {workspace key: state}}`; a feature with no entry there has its `default`. An entry
of the older pref `features.off`, `{feature: [workspace keys]}`, still reads as `off` until the
next write for that pair moves it.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from coscc.agent import policy
from coscc.agent.policy import is_prose_stage
from coscc.bus import Name
from coscc.agent.sessions import Suspended
from coscc.git.gitops import GitError
from coscc.github import integrate
from coscc.store.db import Data
from coscc.store.journal import Intervention
from coscc.kernel import (
    STATES,
    Agents,
    Arm,
    Ctx,
    Feature,
    Hooks,
    Invalid,
    Parts,
    Runs,
    Settings,
    State,
    Run,
    Units,
    arm_of,
)
from coscc.runner import queue
from coscc.runner import run as run_mod
from coscc.runner.interventions import interventions
from coscc.update.updater import refuse_while_updating
from coscc.units.workspaces import Workspaces
from coscc.units import BadUnit, contracts, submit, worktrees
from coscc.units import board as board_reader
from coscc.units.board import Unavailable
from coscc.units.read import Asked

if TYPE_CHECKING:
    from coscc.http.app import Core

OFF_PREF = "features.off"
STATE_PREF = "features.state"
SCHEDULE_PREF = "features.schedule"
# How often the core asks each scheduled feature whether it is due. Chosen, not measured.
TICK_SECONDS = 300.0
CREATE_TABLE = re.compile(r"\s*CREATE TABLE IF NOT EXISTS\s+\w+", re.IGNORECASE)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Shown:
    """A feature as Settings and `GET /api/features` show it for one workspace."""

    name: str
    # `off` whenever `pilot` and `on` may not be chosen.
    state: State
    pilot: bool
    sentence: str
    locked: bool
    # The hours of its schedule here and the choices, both empty for a feature with none.
    schedule: int | None = None
    hours: tuple[int, ...] = ()
    summary: str = ""


def tables_of(features: Sequence[Feature]) -> tuple[str, ...]:
    """Every feature's `tables`; a statement that is not `CREATE TABLE IF NOT EXISTS` is refused."""
    for f in features:
        for statement in f.tables:
            if not CREATE_TABLE.match(statement):
                raise ValueError(f"{f.name}: a table is a CREATE TABLE IF NOT EXISTS statement")
    return tuple(statement for f in features for statement in f.tables)


def create_tables(data: Data, tables: Sequence[str]) -> None:
    """Run the statements once the app starts; with none, the database is not opened."""
    if not tables:
        return
    with data.write() as conn:
        for statement in tables:
            conn.execute(statement)


def add_sessions(core: Core, features: Sequence[Feature]) -> None:
    """Every feature's `sessions` into the core's tables: its output declaration (`submit`), its
    grant (`policy`), its attempt machine; and the updater hears each one end, as it hears the
    core's. Every declaration, the shipped ones first, is checked here: a broken one stops the
    build with its `ContractError`; so is every agent's input."""
    contracts.declarations()
    contracts.input_of("")
    for f in features:
        for s in f.sessions:
            submit.add_session(s.kind, s.output, s.purpose)
            policy.add_session(s.kind, s.grant, s.own_turns)
            queue.add_session(s.kind)
            for end in ("ended", "refused"):
                core.bus.subscribe(
                    cast(Name, f"{s.kind}.{end}"), lambda _: core.updater.job_ended()
                )


def hooks_of(features: Sequence[Feature], ctxs: dict[str, Ctx]) -> Hooks:
    """Every feature's agent parts, tagged with its name; a clash or a tool on a prose stage is
    a `ValueError` naming the feature."""
    parts: list[tuple[str, Parts]] = []
    servers: dict[str, str] = {}
    names: dict[str, dict[str, str]] = {"guard": {}, "block": {}}
    for f in features:
        if f.agent is None:
            continue
        made = f.agent(ctxs[f.name])
        for tool in made.tools:
            if tool.server in servers:
                raise ValueError(
                    f"{f.name}: the MCP server {tool.server!r} is also {servers[tool.server]}'s; "
                    "rename it"
                )
            servers[tool.server] = f.name
            prose = sorted(s for s in tool.stages if is_prose_stage(s))
            if prose:
                raise ValueError(
                    f"{f.name}: the tool {tool.server!r} names prose stages "
                    f"({', '.join(prose)}); a prose stage cannot carry a tool, so drop them"
                )
        for kind, named in (("guard", made.guards), ("block", made.blocks)):
            for part in named:
                if part.name in names[kind]:
                    raise ValueError(
                        f"{f.name}: the {kind} {part.name!r} is also {names[kind][part.name]}'s; "
                        "rename it"
                    )
                names[kind][part.name] = f.name
        parts.append((f.name, made))
    return Hooks(
        parts=tuple(parts), enabled=lambda feature, cwd: ctxs[feature].settings.enabled(cwd)
    )


def _pref(data: Data, name: str) -> dict[str, Any]:
    stored = data.pref(name, {})
    return stored if isinstance(stored, dict) else {}


def state_of(data: Data, feature: str, key: str, default: State) -> State:
    """The state chosen for `(feature, workspace key)`; an entry of `features.off` is `off`."""
    chosen = _pref(data, STATE_PREF).get(feature)
    got = chosen.get(key) if isinstance(chosen, dict) else None
    for state in STATES:
        if got == state:
            return state
    off = _pref(data, OFF_PREF).get(feature)
    return "off" if isinstance(off, list) and key in off else default


def ctx_of(core: Core, feature: Feature) -> Ctx:
    """The `Ctx` of one feature: its own settings, and the core's parts."""
    data = Data(core.config.data_dir)

    def workspace_key(cwd: str) -> str:
        core.ws.check(cwd)
        return Workspaces.key(cwd)

    def state(workspace: str) -> State:
        return state_of(data, feature.name, Workspaces.key(workspace), feature.default)

    def enabled(workspace: str) -> bool:
        return state(workspace) != "off"

    def arm(workspace: str, unit: str) -> Arm | None:
        return arm_of(state(workspace), unit)

    async def main_tree(workspace: str) -> tuple[str, str]:
        tree, sha = await worktrees.main_tree(core.ws.check(workspace), data.root)
        return str(tree), sha

    def found(workspace: str, after: str, limit: int) -> list[Intervention]:
        return interventions(
            core.ws.journal(),
            core.ws.unit_meta(),
            core.holds.attempts,
            workspace_key(workspace),
            after,
            limit,
        )

    async def session(workspace: str, kind: str, prompt: str) -> Run:
        """One run of the feature's session `kind` (`run_mod.run`), held by an attempt of `kind`."""
        key = workspace_key(workspace)
        refuse_while_updating(core.updater)
        journal = core.ws.journal()
        if journal is None:
            raise Invalid("no working folder is set, so a session cannot be recorded")
        own = next((s for s in feature.sessions if s.kind == kind), None)
        if own is None:
            raise Invalid(f"{feature.name} has no session {kind!r}")
        attempt = core.holds.attempts.open(kind, key, "", kind)["id"]
        core.holds.attempts.move(attempt, "running")
        outcome = "failed"
        got = Run("cancelled", detail="the session ended before it handed anything back")
        try:
            stream = run_mod.run(
                core.models.agent(kind, policy.grant_for(kind), own.model, own.effort),
                run_mod.Input(workspace, prompt, key, channel=submit.Collector(kind)),
                ctx=run_mod.Ctx(core.sessions, journal, core.config.data_dir),
            )
            try:
                async for item, payload in stream:
                    if item == "done":
                        got = payload
            finally:
                await stream.aclose()
            outcome = "done"
        except Suspended:
            # What it spent is read off its transcript only after this, and lands in the run
            # log's `end` once the app is back (`Resume._end_unresumed`).
            got = Run(
                "cancelled", detail="an update paused the session; what it spent is in the run log"
            )
        except asyncio.CancelledError:
            outcome = "interrupted"
            raise
        finally:
            core.holds.attempts.move(attempt, "ended", outcome)
        return got

    async def create_unit(workspace: str, slug: str, brief: str) -> str:
        return str((await core.answers.create_unit(workspace, slug, brief))["unit"])

    def schedule(workspace: str) -> int:
        return schedule_of(data, feature, Workspaces.key(workspace))

    def set_schedule(workspace: str, hours: int) -> None:
        set_schedule_of(core, (feature,), feature.name, workspace, hours)

    return Ctx(
        Units(
            workspace_key,
            create_unit,
            main_tree,
            lambda workspace, fresh: _units(core, workspace, fresh),
            lambda workspace, fresh: _open_prs(core, workspace, fresh),
            lambda workspace, name: _own_tree(core, workspace, name),
        ),
        Runs(core.ws.journal, found),
        Agents(session),
        data,
        core.bus,
        Settings(state, enabled, arm, schedule, set_schedule),
        lambda: refuse_while_updating(core.updater),
        core.asks.setdefault(feature.name, Asked(core.boards.changed)),
        _required_checks,
    )


async def _units(core: Core, workspace: str, fresh: bool) -> list[dict[str, Any]]:
    """`Units.units`: the board held, read only when none is, or the loop asked again when
    `fresh`."""
    if not fresh:
        held = core.boards.held.get(core.ws.key(core.ws.check(workspace)))
        board = held["data"] if held else await core.boards.get(workspace, "held")
        return board["units"]
    core.ws.check(workspace)
    try:
        read = await board_reader.read(
            core.ws.units_root(workspace), state=core.ws.snapshot(workspace)
        )
    except Unavailable as e:
        raise Invalid(str(e)) from e
    return read["units"]


async def _open_prs(core: Core, workspace: str, fresh: bool) -> list[dict[str, Any]] | str:
    """`Units.open_prs`: the board's held `gh pr list`, under the key its reads use; asked only
    when none is held or `fresh`."""
    held = core.boards.prs.held.get((workspace, "prs"))
    if held is not None and not fresh:
        return held[0]
    return await core.boards.prs.get(
        (workspace, "prs"), lambda: core.boards.open_prs(workspace), fresh
    )


def _own_tree(core: Core, workspace: str, name: str) -> Path:
    try:
        return worktrees.own_tree(core.ws.check(workspace), name, core.config.data_dir)
    except BadUnit as e:
        raise GitError(str(e)) from e


async def _required_checks(tree: str, number: int) -> list[dict[str, Any]] | str:
    try:
        return await integrate.required_checks(tree, number)
    except integrate.IntegrateError as e:
        return str(e)


def schedule_of(data: Data, plugin: Feature, key: str) -> int:
    """The hours chosen for `(plugin, workspace key)`, else its schedule's `default`."""
    if plugin.schedule is None:
        return 0
    got = _pref(data, SCHEDULE_PREF).get(plugin.name)
    hours = got.get(key) if isinstance(got, dict) else None
    if isinstance(hours, int) and hours in plugin.schedule.hours:
        return hours
    return plugin.schedule.default


def set_schedule_of(
    core: Core, features: Sequence[Feature], feature: str, cwd: str, hours: object
) -> int:
    """Set `feature`'s schedule for the workspace `cwd`. `Invalid`: a feature or workspace not
    known, one without a schedule, or hours it does not offer."""
    plugin = next((f for f in features if f.name == feature), None)
    if plugin is None:
        raise Invalid(f"not a feature: {feature}")
    if plugin.schedule is None:
        raise Invalid(f"{feature} has no schedule")
    core.ws.check(cwd)
    if not isinstance(hours, int) or isinstance(hours, bool) or hours not in plugin.schedule.hours:
        offered = ", ".join(str(h) for h in plugin.schedule.hours)
        raise Invalid(f"schedule must be one of {offered} hours")
    data = Data(core.config.data_dir)
    chosen = _pref(data, SCHEDULE_PREF)
    mine = chosen.get(feature)
    chosen[feature] = {**(mine if isinstance(mine, dict) else {}), Workspaces.key(cwd): hours}
    data.set_pref(SCHEDULE_PREF, chosen)
    return hours


async def tick(core: Core, ctxs: dict[str, Ctx], features: Sequence[Feature]) -> None:
    """One round of every scheduled feature, in each listed workspace where it is not `off` and
    its schedule is not `0`. A tick that fails is logged and the next still runs."""
    for f in features:
        if f.schedule is None:
            continue
        for cwd in core.ws.all()["paths"]:
            ctx = ctxs[f.name]
            hours = ctx.settings.schedule(cwd)
            if not hours or not ctx.settings.enabled(cwd):
                continue
            try:
                await f.schedule.tick(ctx, cwd, hours)
            except Exception:
                log.exception("the scheduled run of %s in %s failed", f.name, cwd)


def shown(ctxs: dict[str, Ctx], features: Sequence[Feature], cwd: str) -> list[Shown]:
    """Each feature for the workspace `cwd`; one whose `status` forbids choosing shows `off`."""
    out = []
    for f in features:
        ctx = ctxs[f.name]
        state = ctx.settings.state(cwd)
        sentence, may = f.status(ctx, cwd) if f.status else ("", True)
        if not sentence:
            sentence = "Off in this workspace." if state == "off" else "On in this workspace."
        hours = f.schedule.hours if f.schedule else ()
        schedule = ctx.settings.schedule(cwd) if f.schedule else None
        out.append(
            Shown(
                f.name,
                state if may else "off",
                f.pilot,
                sentence,
                not may,
                schedule,
                hours,
                f.summary,
            )
        )
    return out


def set_state(
    core: Core,
    ctxs: dict[str, Ctx],
    features: Sequence[Feature],
    feature: str,
    cwd: str,
    state: str,
) -> State:
    """Set `feature`'s state for the workspace `cwd`, then tell the feature. `Invalid`: a feature,
    workspace or state not known, `pilot` for a feature without it, or `pilot`/`on` while its
    `status` forbids them."""
    plugin = next((f for f in features if f.name == feature), None)
    if plugin is None:
        raise Invalid(f"not a feature: {feature}")
    ctx = ctxs[feature]
    key = ctx.units.key(cwd)
    chosen = next((s for s in STATES if s == state), None)
    if chosen is None:
        raise Invalid(f"state must be one of {', '.join(STATES)}")
    if chosen == "pilot" and not plugin.pilot:
        raise Invalid(f"{feature} has no pilot: choose on or off")
    if chosen != "off" and plugin.status:
        sentence, may = plugin.status(ctx, cwd)
        if not may:
            raise Invalid(sentence or f"{feature} cannot be turned on here")
    data = Data(core.config.data_dir)
    states = _pref(data, STATE_PREF)
    mine = states.get(feature)
    states[feature] = {**(mine if isinstance(mine, dict) else {}), key: chosen}
    data.set_pref(STATE_PREF, states)
    off = _pref(data, OFF_PREF)
    if key in (off.get(feature) or []):
        off[feature] = [k for k in off[feature] if k != key]
        data.set_pref(OFF_PREF, off)
    if plugin.on_set:
        plugin.on_set(ctx, cwd, chosen)
    return chosen
