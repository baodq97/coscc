"""Codegraph: a code index of each workspace's `main`, for the agent's impl and review runs.

The engine is `@colbymchenry/codegraph`, installed once under the app's data directory and run
only through its bundled Node (`graph.py`). Here: the index's life on the workspace's `_main`
tree, one record per run with its arm, the map block, the impl tools, the Settings sentence and
two routes (status, A/B report). What a person should know is in `coscc/features/codegraph/README.md`.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import shutil
import sqlite3
import threading
import time
from claude_agent_sdk import McpServerConfig, SdkMcpTool, create_sdk_mcp_server, tool
from collections.abc import Callable, Iterable, Mapping
from coscc.features.codegraph.graph import (
    BridgeError,
    GitError,
    ago,
    call,
    callers,
    changed_files,
    failure_sentence,
    find,
    impact,
    impl_map,
    install,
    installed,
    old_hunks,
    review_map,
)
from coscc.kernel import (
    Block,
    Ctx,
    Event,
    Facts,
    Feature,
    Invalid,
    Parts,
    State,
    Tool,
    cos_dir,
    files_of,
    now,
)
from coscc.units.turnstats import changes_requested, impl_ends, read_chars, shipped_units
from coscc.units.turnstats import pairs as turn_pairs
from dataclasses import dataclass
from datetime import date
from fastapi import APIRouter, Request
from pathlib import Path
from starlette.routing import BaseRoute
from typing import Any, Literal, NamedTuple, TypedDict, get_args


log = logging.getLogger(__name__)

# `root` is the tree the index lives in, which only the kernel's `main_tree` names; `path` is the
# workspace, the one thing the bus's key cannot give back. `sha` is the `main` the index is at and
# stays when a later sync fails, so a reader can still use what is there.
INDEX_TABLE = (
    "CREATE TABLE IF NOT EXISTS codegraph_index (workspace TEXT PRIMARY KEY, path TEXT NOT NULL, "
    "state TEXT NOT NULL, sha TEXT NOT NULL DEFAULT '', at TEXT NOT NULL, "
    "reason TEXT NOT NULL DEFAULT '', root TEXT NOT NULL DEFAULT '')"
)
IndexState = Literal["installing", "building", "ready", "failed"]
STATES: tuple[IndexState, ...] = get_args(IndexState)

DB_FILE = Path(".codegraph") / "codegraph.db"
# Seconds a run may wait for the index to catch up with `main`, and the ceiling of one engine
# call. Chosen, not measured: a sync is seconds, a first build of a large tree minutes.
WAIT_S = 60.0
CALL_S = 1800.0


class Ready(NamedTuple):
    """An index a reader may use: its tree, the `main` it is at, and how long the run waited."""

    root: str
    sha: str
    wait_ms: int


class Status(NamedTuple):
    """What a page shows: `state` is empty when nothing was ever built."""

    state: IndexState | Literal[""]
    sha: str
    at: str
    reason: str


class IndexRow(NamedTuple):
    path: str
    root: str
    state: IndexState
    sha: str
    at: str
    reason: str


class Indexes:
    """`ensure` for a run, `schedule` for the bus, `status` for a page, `start_install` for a
    person's click; `install(home)`, `installed(home)` and `call(...)` are the engine's."""

    def __init__(
        self,
        ctx: Ctx,
        home: Path,
        install: Callable[[Path], Path | str],
        installed: Callable[[Path], Path | str],
        call: Callable[[Path, Path, str, str, Mapping[str, object], float], object],
        *,
        wait_s: float = WAIT_S,
        call_s: float = CALL_S,
        clock: Callable[[], str] = now,
    ) -> None:
        self.ctx = ctx
        self.home = home
        self._install = install
        self._installed = installed
        self._call = call
        self.wait_s = wait_s
        self.call_s = call_s
        self._clock = clock
        # Held by an install, a build and a sync, never by a query.
        self._lock = threading.Lock()
        self._binary: Path | None = None
        # `_broken`: the install there is cannot run (its bundled Node), kept until the app restarts, so the
        # feature stays off and no run installs again. `_failure`: the last install failed; a run
        # does not retry it, a person's pick (`retry`) does.
        self._broken = ""
        self._failure = self._failed_at = ""
        self._installing = False
        self._install_done = threading.Event()
        self._install_done.set()
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def _engine(self) -> Path | str:
        """The checked binary, asked once and then remembered. A failure is remembered only for
        an install that is there, so one not there yet is seen once it finishes."""
        if self._binary is not None:
            return self._binary
        if self._installing:
            return "The code index engine is being installed."
        if self._broken:
            return self._broken
        found = await asyncio.to_thread(self._installed, self.home)
        if isinstance(found, Path):
            self._binary = found
        elif self._present():
            self._broken = found
        return found

    def _present(self) -> bool:
        return (self.home / "node_modules").is_dir() and (self.home / "package-lock.json").is_file()

    def lock(self) -> str:
        """Why `pilot` and `on` cannot be chosen, or "": an install found unable to run, or no
        npm and nothing installed. Reads what is known, runs no process."""
        if self._binary is not None or self._installing:
            return ""
        if self._broken:
            return self._broken
        return "" if (self.home / "node_modules").is_dir() or shutil.which("npm") else NO_NPM

    def retry(self) -> None:
        """A person picked `pilot` or `on`: a failed install may be tried again."""
        self._failure = ""

    def start_install(self) -> None:
        """Install the engine in the background unless it is there or on its way. A daemon thread,
        not the loop's executor, so a slow download never holds the process open at exit."""
        if self._installing or self._binary is not None:
            return
        self._installing = True
        self._failure = ""
        self._install_done.clear()
        threading.Thread(target=self._install_run, name="codegraph-install", daemon=True).start()

    def _install_run(self) -> None:
        try:
            with self._lock:
                result = self._install(self.home)
            if isinstance(result, Path):
                self._binary = result
            else:
                self._failure, self._failed_at = result, self._clock()
                log.warning("codegraph install: %s", result)
        except Exception:
            log.exception("codegraph install failed")
            self._failure = "The code index engine could not be installed; the log has the cause."
            self._failed_at = self._clock()
        finally:
            self._installing = False
            self._install_done.set()

    def _row(self, key: str) -> IndexRow | None:
        with self.ctx.store.connect() as conn:
            found = conn.execute(
                "SELECT path, root, state, sha, at, reason FROM codegraph_index WHERE workspace = ?",
                (key,),
            ).fetchone()
        return IndexRow(*tuple(found)) if found else None

    def _put(
        self,
        key: str,
        path: str,
        state: IndexState,
        *,
        root: str | None = None,
        sha: str | None = None,
        reason: str = "",
    ) -> None:
        """A change of state; the tree and the SHA it names are kept unless given."""
        with self.ctx.store.write() as conn:
            conn.execute(
                "INSERT INTO codegraph_index (workspace, path, state, sha, at, reason, root) "
                "VALUES (?, ?, ?, COALESCE(?, ''), ?, ?, COALESCE(?, '')) "
                "ON CONFLICT(workspace) DO UPDATE SET path = excluded.path, state = excluded.state, "
                "at = excluded.at, reason = excluded.reason, sha = COALESCE(?, sha), "
                "root = COALESCE(?, root)",
                (key, path, state, sha, self._clock(), reason, root, sha, root),
            )

    def status(self, workspace: str) -> Status:
        """The install if it is running or failed, else the workspace's row. `workspace` is the
        key. Reads the table, starts nothing."""
        if self._installing:
            return Status("installing", "", "", "")
        if self._binary is None and self._failure:
            return Status("failed", "", self._failed_at, self._failure)
        row = self._row(workspace)
        return Status(row.state, row.sha, row.at, row.reason) if row else Status("", "", "", "")

    def _bridge(self, binary: Path, op: str, root: str) -> None:
        """One build or sync; waits for any other, and for an install."""
        with self._lock:
            self._call(self.home, binary, op, root, {}, self.call_s)

    async def _refresh(self, key: str, path: str) -> None:
        """Bring the index to the fetched `main`. Every failure lands in the row, never raised."""
        root = ""
        try:
            binary = await self._engine()
            if not isinstance(binary, Path):
                return
            root, sha = await self.ctx.units.main_tree(path)
            row = self._row(key)
            exists = (Path(root) / DB_FILE).exists()
            if row and row.sha == sha and exists:
                if row.state != "ready":
                    self._put(key, path, "ready", root=root)
                return
            self._put(key, path, "building", root=root)
            await asyncio.to_thread(self._bridge, binary, "sync" if exists else "index", root)
            self._put(key, path, "ready", root=root, sha=sha)
        except Exception as error:
            log.exception("codegraph index of %s failed", key)
            self._put(key, path, "failed", reason=failure_sentence(error, root, path))

    def _task(self, key: str, path: str) -> asyncio.Task[None]:
        """The refresh of this workspace, started if none is running: two askers share one."""
        running = self._tasks.get(key)
        if running is not None and not running.done():
            return running
        task = asyncio.get_running_loop().create_task(self._refresh(key, path))
        self._tasks[key] = task  # held: a task nothing refers to may be collected mid-run
        task.add_done_callback(
            lambda t: self._tasks.pop(key, None) if self._tasks.get(key) is t else None
        )
        return task

    def schedule(self, key: str) -> None:
        """For a bus handler: refresh a workspace that was indexed once. One never indexed is not
        started by a merge, only by a run that reads."""
        try:
            asyncio.get_running_loop()
            row = self._row(key)
        except RuntimeError:
            return
        if row is not None:
            self._task(key, row.path)

    async def ensure(self, workspace: str) -> Ready | str:
        """The index to read now, or why there is none. Waits at most `wait_s` for it to catch up
        with `main`; past that, or when the refresh failed, what is already built is used."""
        started = time.monotonic()
        try:
            key = self.ctx.units.key(workspace)
        except Invalid as error:
            return str(error)
        engine = await self._engine()
        if not isinstance(engine, Path):
            if not self._broken and not self._failure:
                self.start_install()
            return engine
        task = self._task(key, workspace)
        try:
            await asyncio.wait_for(
                asyncio.shield(task), max(self.wait_s - (time.monotonic() - started), 0.0)
            )
        except TimeoutError:
            pass
        row = self._row(key)
        waited = round((time.monotonic() - started) * 1000)
        if row and row.sha and row.root and (Path(row.root) / DB_FILE).exists():
            return Ready(row.root, row.sha, waited)
        if row and row.state == "failed":
            return row.reason
        return "The code index is still being built." if row else "There is no code index yet."

    async def settle(self) -> None:
        """Wait for every install and refresh now running: for a test and for a clean stop."""
        await asyncio.to_thread(self._install_done.wait)
        if self._tasks:
            await asyncio.gather(*self._tasks.values())


Arm = Literal["on", "off"]
ARMS: tuple[str, ...] = get_args(Arm)

# The owner's target, chosen not measured: at least this many units per arm, and a drop in
# characters read per impl step of at least this share against the off arm.
MIN_UNITS = 5
READ_DROP = 0.30
SCORING_DAY = date(2026, 11, 15)  # the owner scores the pilot then; no committed file says it


@dataclass(frozen=True)
class Row:  # one record row a run of the feature wrote
    run: str
    workspace: str
    unit: str
    stage: str
    arm: Arm
    sha: str
    map_chars: int
    wait_ms: int
    error: str
    at: str


@dataclass(frozen=True)
class Step:
    """One impl step of the run log; `read` and `served` are None once its events were purged."""

    run: str
    unit: str
    cost: float
    read: int | None
    served: int | None


class Window(TypedDict):
    since: str | None
    until: str | None


class ArmStats(TypedDict):
    steps: int
    excluded: int
    units: int
    read_mean: int | None
    served_mean: int | None
    cost_mean: float | None
    changes_requested_mean: float | None


class Arms(TypedDict):
    on: ArmStats
    off: ArmStats


class Report(TypedDict):
    window: Window
    arms: Arms
    excluded_units: int
    verdict: Literal["pass", "fail"]
    missed: list[str]


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def _stats(kept: list[Step], dropped: int, rounds: Mapping[str, int]) -> ArmStats:
    units = {s.unit for s in kept}
    # Only a unit that shipped has review rounds to count.
    shipped = [float(rounds[u]) for u in sorted(units) if u in rounds]
    read = _mean([float(s.read) for s in kept if s.read is not None])
    served = _mean([float(s.served) for s in kept if s.served is not None])
    return ArmStats(
        steps=len(kept),
        excluded=dropped,
        units=len(units),
        read_mean=None if read is None else round(read),
        served_mean=None if served is None else round(served),
        cost_mean=_round(_mean([s.cost for s in kept]), 4),
        changes_requested_mean=_round(_mean(shipped), 2),
    )


def _missed(on: ArmStats, off: ArmStats) -> list[str]:
    """One sentence per condition of the target that the numbers do not meet."""
    out = [
        f"the {name} arm has {a['units']} units, under {MIN_UNITS}"
        for name, a in (("on", on), ("off", off))
        if a["units"] < MIN_UNITS
    ]
    # A comparison needs a number on both sides; a side with none counts as not met.
    on_read, off_read = on["read_mean"], off["read_mean"]
    if on_read is None or off_read is None:
        out.append("read per impl step cannot be compared: an arm has no counted step")
    elif on_read > (1 - READ_DROP) * off_read:
        out.append(
            f"read per impl step went from {off_read} to {on_read}, short of a {READ_DROP:.0%} drop"
        )
    on_rounds, off_rounds = on["changes_requested_mean"], off["changes_requested_mean"]
    if on_rounds is None or off_rounds is None:
        out.append("review rounds cannot be compared: an arm has no shipped unit")
    elif on_rounds > off_rounds:
        out.append(f"review rounds rose from {off_rounds} to {on_rounds}")
    on_cost, off_cost = on["cost_mean"], off["cost_mean"]
    if on_cost is None or off_cost is None:
        out.append("cost per impl step cannot be compared: an arm has no counted step")
    elif on_cost > off_cost:
        out.append(f"cost per impl step rose from {off_cost} to {on_cost}")
    return out


def units_by_arm(pairs: Iterable[tuple[str, str]]) -> dict[str, set[str]]:
    """The units of `(unit, arm)` records per arm; one in both says nothing, so is in neither."""
    seen: dict[str, set[str]] = {}
    for unit, arm in pairs:
        seen.setdefault(unit, set()).add(arm)
    return {a: {u for u, arms in seen.items() if arms == {a}} for a in ARMS}


def report(
    rows: list[Row],
    steps: list[Step],
    rounds: Mapping[str, int],
    window: tuple[str | None, str | None],
) -> Report:
    since, until = window
    inside = [
        r for r in rows if (since is None or r.at >= since) and (until is None or r.at < until)
    ]
    alone = units_by_arm((r.unit, r.arm) for r in inside)
    mixed = {r.unit for r in inside} - alone["on"] - alone["off"]
    impl = {r.run: r for r in inside if r.stage == "impl"}

    kept: dict[str, list[Step]] = {a: [] for a in ARMS}
    dropped = dict.fromkeys(ARMS, 0)
    for s in steps:
        row = impl.get(s.run)
        if row is None:
            continue
        if s.unit in mixed or row.error or s.read is None:
            dropped[row.arm] += 1
        else:
            kept[row.arm].append(s)
    on, off = (_stats(kept[a], dropped[a], rounds) for a in ARMS)
    missed = _missed(on, off)
    return Report(
        window=Window(since=since, until=until),
        arms=Arms(on=on, off=off),
        excluded_units=len(mixed),
        verdict="fail" if missed else "pass",
        missed=missed,
    )


NAME = "codegraph"
# Each impl or review run of a workspace not `off`: which arm it was in and what it got.
RUNS_TABLE = (
    "CREATE TABLE IF NOT EXISTS codegraph_runs (run TEXT PRIMARY KEY, workspace TEXT NOT NULL, "
    "unit TEXT NOT NULL, stage TEXT NOT NULL, arm TEXT NOT NULL, sha TEXT NOT NULL, "
    "map_chars INTEGER NOT NULL, wait_ms INTEGER NOT NULL, error TEXT NOT NULL, at TEXT NOT NULL)"
)
MAP_STAGES = ("impl", "review")
TOOL_STAGES = ("impl",)
TOOL_NAMES = ("find", "callers", "impact")
# Seconds one query may take. State, not measured: the spike's queries took well under one.
QUERY_S = 60.0
NO_NPM = "npm is not installed, so codegraph cannot be turned on."
# Tasks started from a synchronous hook, kept so they are not collected mid-way.
_running: set[asyncio.Task[Any]] = set()


@functools.cache
def _indexes(ctx: Ctx) -> Indexes:
    return Indexes(ctx, ctx.store.root / NAME, install, installed, call)


def _where(ctx: Ctx, key: str) -> tuple[str, str] | None:
    """`(root, sha)` of the index there is, ready, or `None`."""
    with ctx.store.connect() as conn:
        found = conn.execute(
            "SELECT root, sha FROM codegraph_index WHERE workspace = ? AND state = 'ready'",
            (key,),
        ).fetchone()
    return (found[0], found[1]) if found and found[0] and found[1] else None


def _rows(conn: sqlite3.Connection, key: str) -> list[Row]:
    found = conn.execute(
        "SELECT run, workspace, unit, stage, arm, sha, map_chars, wait_ms, error, at "
        "FROM codegraph_runs WHERE workspace = ?",
        (key,),
    ).fetchall()
    return [Row(*r) for r in found if r[4] in ARMS]


def _record(ctx: Ctx, row: Row) -> None:
    with ctx.store.write() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO codegraph_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row.run, row.workspace, row.unit, row.stage, row.arm, row.sha,
                row.map_chars, row.wait_ms, row.error, row.at,
            ),
        )  # fmt: skip


def _map(ctx: Ctx, facts: Facts, binary: Path, ready: Ready) -> str:
    """The map for this run: blocking, run in a thread."""
    home = ctx.store.root / NAME

    def ask(op: str, args: Mapping[str, object]) -> object:
        return call(home, binary, op, ready.root, args, QUERY_S)

    changed = changed_files(facts.tree, ready.sha)
    if facts.stage == "review":
        return review_map(ask, ready.sha, changed, old_hunks(facts.tree, ready.sha))
    plan = facts.directory / "plan.md"
    named = files_of(plan.read_text(encoding="utf-8")) if plan.is_file() else None
    return impl_map(ask, ready.sha, changed, sorted(named or ()))


async def _render(ctx: Ctx, facts: Facts) -> str:
    """The map for the `on` arm, nothing for the `off` one; either way one record of the run."""
    arm = ctx.settings.arm(facts.workspace, facts.unit)
    idx = _indexes(ctx)
    if facts.stage not in MAP_STAGES or arm is None or idx.lock():
        return ""
    text = error = sha = ""
    wait_ms = 0
    if arm == "on":
        got = await idx.ensure(facts.workspace)
        if isinstance(got, str):
            error = got
        else:
            sha, wait_ms = got.sha, got.wait_ms
            binary = await idx._engine()
            try:
                if isinstance(binary, str):
                    raise BridgeError(binary)
                text = await asyncio.to_thread(_map, ctx, facts, binary, got)
            except (BridgeError, GitError, OSError) as e:
                error = str(e) or type(e).__name__
    else:
        sha = idx.status(facts.workspace_key).sha
    row = Row(
        facts.run, facts.workspace_key, facts.unit, facts.stage, arm, sha, len(text), wait_ms,
        error, now(),
    )  # fmt: skip
    await asyncio.to_thread(_record, ctx, row)
    return text


def _ready_for(ctx: Ctx, facts: Facts) -> bool:
    """The tools go to an `on`-arm impl run while the index is ready and the engine not found
    broken. The engine is checked at the first call, so a resumed run gets them too."""
    if ctx.settings.arm(facts.workspace, facts.unit) != "on" or _indexes(ctx).lock():
        return False
    return _where(ctx, facts.workspace_key) is not None


# What an SDK tool handler hands back: `{"content": [{"type": "text", "text"}], "is_error"}`. Not
# a TypedDict: the SDK's `tool` wants a plain `dict`.
Reply = dict[str, object]


def _text(text: str, error: bool = False) -> Reply:
    return {"content": [{"type": "text", "text": text}], "is_error": error}


def build_tools(ctx: Ctx, facts: Facts) -> list[SdkMcpTool[Any]]:
    """The run's three tools; each reads the index there is now and never waits for a sync."""
    home = ctx.store.root / NAME

    async def answer(make: Callable[[Callable[..., object], str, set[str]], str]) -> Reply:
        where, binary = _where(ctx, facts.workspace_key), await _indexes(ctx)._engine()
        if where is None or not isinstance(binary, Path):
            return _text("The code index is not ready; use Read and Grep.", True)
        root, sha = where

        def ask(op: str, args: Mapping[str, object]) -> object:
            return call(home, binary, op, root, args, QUERY_S)

        def run() -> str:
            return make(ask, sha, changed_files(facts.tree, sha))

        try:
            return _text(await asyncio.to_thread(run))
        except (BridgeError, GitError, OSError) as e:
            return _text(f"The code index could not answer: {e}", True)

    def arg(args: Mapping[str, object], name: str) -> str:
        got = args.get(name)
        return got.strip() if isinstance(got, str) else ""

    async def _find(args: Mapping[str, object]) -> Reply:
        q = arg(args, "query")
        return await answer(lambda a, s, c: find(a, s, c, facts.tree, q))

    async def _callers(args: Mapping[str, object]) -> Reply:
        name = arg(args, "symbol")
        return await answer(lambda a, s, c: callers(a, s, c, name))

    async def _impact(args: Mapping[str, object]) -> Reply:
        path = arg(args, "path")
        return await answer(lambda a, s, c: impact(a, s, c, facts.tree, path))

    def one(name: str, said: str) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {name: {"type": "string", "description": said}},
            "required": [name],
        }

    return [
        tool(
            "find",
            "Where code is on main: entry points for words, or a file's symbols for a path.",
            one("query", "words, or a repository path for its outline"),
        )(_find),
        tool(
            "callers",
            "The direct call sites of a symbol on main.",
            one("symbol", "a function, method or class name"),
        )(_callers),
        tool(
            "impact",
            "The files on main that import a file directly, tests apart.",
            one("path", "a repository path"),
        )(_impact),
    ]


def agent(ctx: Ctx) -> Parts:
    def make(facts: Facts) -> McpServerConfig:
        return create_sdk_mcp_server(NAME, "1.0.0", build_tools(ctx, facts))

    def ended(event: Event) -> None:
        with ctx.store.connect() as conn:
            found = conn.execute(
                "SELECT path FROM codegraph_index WHERE workspace = ?", (event.workspace,)
            ).fetchone()
        if found and ctx.settings.enabled(found[0]):
            _indexes(ctx).schedule(event.workspace)

    ctx.bus.subscribe("integration.ended", ended)
    return Parts(
        tools=(Tool(NAME, TOOL_NAMES, TOOL_STAGES, make, when=lambda f: _ready_for(ctx, f)),),
        blocks=(Block("codegraph-map", lambda facts: _render(ctx, facts)),),
    )


def status(ctx: Ctx, workspace: str) -> tuple[str, bool]:
    """One sentence for the Settings row, and whether `pilot` and `on` may be chosen."""
    idx = _indexes(ctx)
    why = idx.lock()
    if why:
        return why, False
    state = ctx.settings.state(workspace)
    if state == "off":
        return "Off in this workspace.", True
    key = ctx.units.key(workspace)
    s = idx.status(key)
    index = {
        "installing": "Installing the code index engine, about 290 MB, once.",
        "building": "Building the index of main.",
        "ready": f"Ready: the index of main is from {ago(s.at)}.",
        "failed": f"Failed: {s.reason}",
    }.get(s.state)
    if state != "pilot":
        return index or "Waiting: the index of main is built at the next impl or review.", True
    # The split, the units the report counts in each arm so far and the scoring day.
    # Quick: whether a step's events remain is all `report` needs to count units, so none is read.
    with ctx.store.connect() as conn:
        rows, ended = _rows(conn, key), impl_ends(conn, key)
    steps = [Step(run, unit, 0.0, None if gone else 0, None) for run, unit, gone in ended]
    arms = report(rows, steps, {}, (None, None))["arms"]
    split = (
        f"even-numbered units use it, odd ones do not: {arms['on']['units']} on, "
        f"{arms['off']['units']} off so far, scored {SCORING_DAY:%b %-d}."
    )
    return (f"{index.rstrip('.')}; {split}" if index else split[0].upper() + split[1:]), True


def on_set(ctx: Ctx, workspace: str, state: State) -> None:
    """`pilot` or `on`: install the engine if it is missing, then build the index."""
    if state == "off":
        return
    idx = _indexes(ctx)
    idx.retry()
    try:
        task = asyncio.get_running_loop().create_task(idx.ensure(workspace))
    except RuntimeError:
        idx.start_install()
        return
    _running.add(task)
    task.add_done_callback(_running.discard)


def _rounds(
    conn: sqlite3.Connection, ctx: Ctx, key: str, window: tuple[str | None, str | None]
) -> dict[str, int]:
    """The changes-requested rounds of each unit shipped in the window whose review is there."""
    out: dict[str, int] = {}
    for unit in shipped_units(conn, key, *window):
        review = cos_dir(key, ctx.store.root) / unit / "review.md"
        if review.is_file():
            out[unit] = changes_requested(review.read_text(encoding="utf-8"))
    return out


def measured(ctx: Ctx, key: str, window: tuple[str | None, str | None]) -> Report:
    """The report over the run log: blocking, run in a thread."""
    with ctx.store.connect() as conn:
        rows = _rows(conn, key)
        pairs = turn_pairs(conn, key, None, None)
        runs = [str(p["end"].get("run") or "") for p in pairs]
        chars = read_chars(conn, runs, NAME)
        rounds = _rounds(conn, ctx, key, window)
    steps = [
        Step(
            run, str(p["unit"]), float(p["end"].get("cost_usd") or 0), *chars.get(run, (None, None))
        )
        for run, p in zip(runs, pairs)
        if run
    ]
    return report(rows, steps, rounds, window)


def routes(ctx: Ctx) -> list[BaseRoute]:
    router = APIRouter()

    @router.get("/api/codegraph/report")
    async def get_report(request: Request) -> Report:
        """The A/B report over the runs recorded in `[since, until)`; either may be left out."""
        q = request.query_params
        key = ctx.units.key(q.get("cwd", ""))
        window = (q.get("since") or None, q.get("until") or None)
        return await asyncio.to_thread(measured, ctx, key, window)

    return router.routes


FEATURE = Feature(
    NAME,
    routes,
    tables=(INDEX_TABLE, RUNS_TABLE),
    agent=agent,
    default="off",
    pilot=True,
    status=status,
    on_set=on_set,
    summary="Indexes the code of main so impl and review find where things are without reading files.",
)
