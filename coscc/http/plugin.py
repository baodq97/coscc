"""How the core hosts features: their state per workspace, the `Ctx` each gets, their tables,
agent parts, and the Settings panel's view of them. A feature itself sees only
`coscc/kernel.py`.

A feature's state per workspace, `off`, `pilot` or `on`, is the pref `features.state`,
`{feature: {workspace key: state}}`; a feature with no entry there has its `default`. An entry
of the older pref `features.off`, `{feature: [workspace keys]}`, still reads as `off` until the
next write for that pair moves it.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from coscc.agent import pack
from coscc.git.gitops import GitError
from coscc.github import integrate
from coscc.store.db import Data
from coscc.kernel import (
    BUILTINS,
    STATES,
    Arm,
    Ctx,
    Feature,
    Hooks,
    Invalid,
    Parts,
    Runs,
    Settings,
    State,
    Units,
    arm_of,
)
from coscc.update.updater import refuse_while_updating
from coscc.units.workspaces import Workspaces
from coscc.units import BadUnit, contracts, worktrees
from coscc.units import board as board_reader
from coscc.units.board import Unavailable
from coscc.units.read import Asked

if TYPE_CHECKING:
    from coscc.http.app import Core

OFF_PREF = "features.off"
STATE_PREF = "features.state"
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


def check_declarations() -> None:
    """Every declaration and every agent's input, the shipped ones: a broken one stops the build
    with its `ContractError`."""
    contracts.declarations()
    contracts.input_of("")


def hooks_of(features: Sequence[Feature], ctxs: dict[str, Ctx]) -> Hooks:
    """Every feature's agent parts, tagged with its name; a clash is a `ValueError` naming the
    feature, and so is an agent's row (`pack.check`) the catalog makes unusable: a tool it does not
    hold, a tool beyond reading on a row whose artifact the app writes."""
    parts: list[tuple[str, Parts]] = []
    servers: dict[str, str] = {}
    builtin = {t.name for t in BUILTINS} | {"cos"}
    names: dict[str, dict[str, str]] = {"guard": {}, "block": {}}
    for f in features:
        if f.agent is None:
            continue
        made = f.agent(ctxs[f.name])
        for tool in made.tools:
            for taken in (tool.server, tool.name):
                if taken in servers or taken in builtin:
                    raise ValueError(
                        f"{f.name}: the tool {taken!r} is also "
                        f"{servers.get(taken, 'the kernel')}'s; rename it"
                    )
            if not tool.server:
                raise ValueError(f"{f.name}: the tool {tool.name!r} names no MCP server")
            servers[tool.server] = servers[tool.name] = f.name
        for kind, named in (("guard", made.guards), ("block", made.blocks)):
            for part in named:
                if part.name in names[kind]:
                    raise ValueError(
                        f"{f.name}: the {kind} {part.name!r} is also {names[kind][part.name]}'s; "
                        "rename it"
                    )
                names[kind][part.name] = f.name
        parts.append((f.name, made))
    hooks = Hooks(
        parts=tuple(parts), enabled=lambda feature, cwd: ctxs[feature].settings.enabled(cwd)
    )
    # The built-in rows: a bad one stops the build. A bad owner's file refuses its agent's runs.
    effects = {n: t.effect for n, t in hooks.catalog().items()}
    shipped = pack.manifest()["name"]
    builtin = {k: r["builtin"] for k, r in pack.rows().items() if r["pack"] == shipped}
    for key, row in builtin.items():
        bad = pack.check(row, effects, builtin)
        if bad:
            raise ValueError(f"the row {key!r} cannot be used: {'; '.join(bad)}")
    return hooks


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

    async def create_unit(workspace: str, slug: str, brief: str) -> str:
        return str((await core.answers.create_unit(workspace, slug, brief))["unit"])

    return Ctx(
        Units(
            workspace_key,
            create_unit,
            main_tree,
            lambda workspace, fresh: _units(core, workspace, fresh),
            lambda workspace, fresh: _open_prs(core, workspace, fresh),
            lambda workspace, name: _own_tree(core, workspace, name),
        ),
        Runs(core.ws.journal),
        data,
        core.bus,
        Settings(state, enabled, arm),
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


def shown(ctxs: dict[str, Ctx], features: Sequence[Feature], cwd: str) -> list[Shown]:
    """Each feature for the workspace `cwd`; one whose `status` forbids choosing shows `off`."""
    out = []
    for f in features:
        ctx = ctxs[f.name]
        state = ctx.settings.state(cwd)
        sentence, may = f.status(ctx, cwd) if f.status else ("", True)
        if not sentence:
            sentence = "Off in this workspace." if state == "off" else "On in this workspace."
        out.append(Shown(f.name, state if may else "off", f.pilot, sentence, not may, f.summary))
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
