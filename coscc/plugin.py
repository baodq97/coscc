"""The core's door for a feature: what it gets of the running app, and what it hands back.

A feature (`coscc/features/<name>.py`) ends in one `PLUGIN` and reaches the app only through a
`Ctx`. Its state per workspace, `off`, `pilot` or `on`, is the pref `features.state`,
`{feature: {workspace key: state}}`; a feature with no entry there has its `default`. An entry
of the older pref `features.off`, `{feature: [workspace keys]}`, still reads as `off` until the
next write for that pair moves it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, get_args

from fastapi import Request
from fastapi.responses import StreamingResponse
from starlette.routing import BaseRoute

from coscc.agent.policy import is_prose_stage
from coscc.agent.sessions import Suspended
from coscc.bus import Bus
from coscc.data import Data
from coscc.hooks import Hooks, Parts
from coscc.runlog.journal import Intervention, Journal
from coscc.service import Service
from coscc.service.common import Invalid, Submitted
from coscc.service.interventions import interventions
from coscc.service.update import refuse_while_updating
from coscc.service.workspaces import Workspaces
from coscc.units import worktrees

OFF_PREF = "features.off"
STATE_PREF = "features.state"
SCHEDULE_PREF = "features.schedule"
# How often the core asks each scheduled feature whether it is due. Chosen, not measured.
TICK_SECONDS = 300.0
CREATE_TABLE = re.compile(r"\s*CREATE TABLE IF NOT EXISTS\s+\w+", re.IGNORECASE)

State = Literal["off", "pilot", "on"]
STATES: tuple[State, ...] = get_args(State)
Arm = Literal["on", "off"]

log = logging.getLogger(__name__)


def arm_of(state: State, unit: str) -> Arm | None:
    """The branch a run belongs to: none when `off`; under `pilot` a unit whose `NNNN` is even is
    `on` and an odd one `off`; under `on` every unit is `on`."""
    if state == "off":
        return None
    if state == "on":
        return "on"
    number = unit[:4]
    return "on" if number.isdigit() and int(number) % 2 == 0 else "off"


def _on(_feature: str, _workspace: str) -> State:
    return "on"


def _arm(_feature: str, _workspace: str, unit: str) -> Arm | None:
    return arm_of("on", unit)


async def _no_main_tree(workspace: str) -> tuple[str, str]:
    raise Invalid(f"no main tree for {workspace} here")


def _no_interventions(_workspace: str, _after: str, _limit: int) -> list[Intervention]:
    return []


async def _no_session(_workspace: str, kind: str, _prompt: str) -> Submitted:
    raise Invalid(f"no {kind} session here")


async def _no_unit(_workspace: str, slug: str, _brief: str) -> str:
    raise Invalid(f"no unit {slug} can be made here")


def _no_schedule(_feature: str, _workspace: str) -> int:
    return 0


def _set_no_schedule(feature: str, _workspace: str, _hours: int) -> None:
    raise Invalid(f"{feature} has no schedule here")


@dataclass(frozen=True)
class Ctx:
    journal: Callable[[], Journal | None]
    # Checks the workspace, raising `Invalid`, and returns how the run log names it.
    workspace_key: Callable[[str], str]
    # `(feature, workspace path)`: `state` is not `off`.
    enabled: Callable[[str, str], bool]
    bus: Bus
    data: Data
    # `(feature, workspace path)`: what a person chose there, else the feature's `default`; a
    # workspace the app does not know counts as the default.
    state: Callable[[str, str], State] = _on
    # `(feature, workspace path, unit)`: `arm_of` the state now.
    arm: Callable[[str, str, str], Arm | None] = _arm
    # `(workspace path)`: the workspace's own detached tree, made or moved to the fetched
    # `origin/main`, as `(path, sha)`. Raises `GitError` when git refuses, `Invalid` when the
    # workspace is not known.
    main_tree: Callable[[str], Awaitable[tuple[str, str]]] = _no_main_tree
    # `(workspace path, after, limit)`: every time a person stepped in there past the time
    # `after` (`""`: from the first), oldest first (`coscc/service/interventions.py`). Blocking.
    interventions: Callable[[str, str, int], list[Intervention]] = _no_interventions
    # `(workspace path, kind, prompt)`: one paid session under the grant `kind` that hands its
    # object back through `submit`, recorded in the run log as the estimate is. `Invalid` while
    # another such session of the workspace runs or an update is under way. One an update
    # paused hands back no cost: the run log's `end` holds it.
    session: Callable[[str, str, str], Awaitable[Submitted]] = _no_session
    # `(workspace path, slug, brief)`: a new unit, made as `POST /api/units` makes one, and its
    # name. It touches no shortlist.
    create_unit: Callable[[str, str, str], Awaitable[str]] = _no_unit
    # `(feature, workspace path)`: the hours of its `schedule` there, `0` for off.
    schedule: Callable[[str, str], int] = _no_schedule
    # `(feature, workspace path, hours)`: set them, one of its `schedule.hours`.
    set_schedule: Callable[[str, str, int], None] = _set_no_schedule


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


# The page kit: one script the shell injects once, before every feature script. `window.coscc`:
# `api(url, opts)` a `fetch` with the session cookie and no cache, a 401 going to `/login` and
# never resolving; `stream(url, onLine, {after})` NDJSON a line at a time, aborted after 40 s
# with no byte and reconnected after 1 s doubling to 30 s (`after` is a function giving the
# cursor, or null, at each connect); `every(ms, fn)` now, then each `ms`; `ago(t)` a relative
# time; `slot(id, render)` renders into the element with that id, again whenever a route change
# puts a new one in the page.
KIT_JS = r"""
(function () {
  if (window.coscc) return;
  function api(url, opts) {
    var init = Object.assign({credentials: "same-origin", cache: "no-store"}, opts || {});
    return fetch(url, init).then(function (r) {
      if (r.status === 401) { window.location.replace("/login"); return new Promise(function () {}); }
      return r;
    });
  }
  function stream(url, onLine, opts) {
    var after = (opts && opts.after) || function () { return null; };
    var DEAD = 40000, wait = 1000;
    function connect() {
      var cur = after();
      var ctl = new AbortController(), timer = null;
      function arm() { clearTimeout(timer); timer = setTimeout(function () { ctl.abort(); }, DEAD); }
      arm();
      api(url + (cur === null || cur === undefined ? "" : (url.indexOf("?") < 0 ? "?" : "&") + "after=" + cur),
          {signal: ctl.signal}).then(function (r) {
        if (!r.ok || !r.body) throw new Error(String(r.status));
        wait = 1000;
        var reader = r.body.getReader(), dec = new TextDecoder(), buf = "";
        function pump() {
          return reader.read().then(function (res) {
            if (res.done) throw new Error("ended");
            arm();
            buf += dec.decode(res.value, {stream: true});
            var i;
            while ((i = buf.indexOf("\n")) >= 0) {
              var line = buf.slice(0, i);
              buf = buf.slice(i + 1);
              if (line) onLine(JSON.parse(line));
            }
            return pump();
          });
        }
        return pump();
      }).catch(function () {}).then(function () {
        clearTimeout(timer);
        var w = wait;
        wait = Math.min(wait * 2, 30000);
        setTimeout(connect, w);
      });
    }
    connect();
  }
  function every(ms, fn) { fn(); return setInterval(fn, ms); }
  function ago(at) {
    var t = Date.parse(at);
    if (isNaN(t)) return "";
    var s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + " min ago";
    if (s < 86400) return Math.floor(s / 3600) + " h ago";
    return new Date(t).toLocaleString([], {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"});
  }
  function slot(id, render) {
    var last = null;
    function check() {
      var el = document.getElementById(id);
      if (el && el !== last) render(el);
      last = el;
    }
    new MutationObserver(check).observe(document.body, {childList: true, subtree: true});
    check();
  }
  window.coscc = {api: api, stream: stream, every: every, ago: ago, slot: slot};
})();
"""


@dataclass(frozen=True)
class Page:
    """A sidebar entry whose screen frames the feature's own `GET path?cwd=<workspace>`."""

    label: str
    # A lucide icon name, as the sidebar's own entries use.
    icon: str
    path: str


@dataclass(frozen=True)
class Plugin:
    name: str
    routes: Callable[[Ctx], Sequence[BaseRoute]]
    scripts: tuple[str, ...] = ()
    # `CREATE TABLE IF NOT EXISTS ...` statements, run once at build through `Data.write()`.
    tables: tuple[str, ...] = ()
    # What the feature hands the agent's steps, called once at build like `routes`.
    agent: Callable[[Ctx], Parts] | None = None
    page: Page | None = None
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
    # One fixed sentence, 100 characters at most: what the feature does, shown under its name.
    summary: str = ""


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


def tables_of(features: Sequence[Plugin]) -> tuple[str, ...]:
    """Every feature's `tables`; a statement that is not `CREATE TABLE IF NOT EXISTS` is refused."""
    for f in features:
        for statement in f.tables:
            if not CREATE_TABLE.match(statement):
                raise ValueError(f"{f.name}: a table is a CREATE TABLE IF NOT EXISTS statement")
    return tuple(statement for f in features for statement in f.tables)


def create_tables(ctx: Ctx, tables: Sequence[str]) -> None:
    """Run the statements once the app starts; with none, the database is not opened."""
    if not tables:
        return
    with ctx.data.write() as conn:
        for statement in tables:
            conn.execute(statement)


def hooks_of(features: Sequence[Plugin], ctx: Ctx) -> Hooks:
    """Every feature's agent parts, tagged with its name; a clash or a tool on a prose stage is
    a `ValueError` naming the feature."""
    parts: list[tuple[str, Parts]] = []
    servers: dict[str, str] = {}
    names: dict[str, dict[str, str]] = {"guard": {}, "block": {}}
    for f in features:
        if f.agent is None:
            continue
        made = f.agent(ctx)
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
    return Hooks(parts=tuple(parts), enabled=ctx.enabled)


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


def ctx_of(service: Service, features: Sequence[Plugin] = ()) -> Ctx:
    """The `Ctx` every feature gets; `features` gives their `default` states."""
    data = Data(service.config.data_dir)
    defaults: dict[str, State] = {f.name: f.default for f in features}

    def workspace_key(cwd: str) -> str:
        service.ws.check(cwd)
        return Workspaces.key(cwd)

    def state(feature: str, workspace: str) -> State:
        return state_of(data, feature, Workspaces.key(workspace), defaults.get(feature, "on"))

    def enabled(feature: str, workspace: str) -> bool:
        return state(feature, workspace) != "off"

    def arm(feature: str, workspace: str, unit: str) -> Arm | None:
        return arm_of(state(feature, workspace), unit)

    async def main_tree(workspace: str) -> tuple[str, str]:
        tree, sha = await worktrees.main_tree(service.ws.check(workspace), data.root)
        return str(tree), sha

    def found(workspace: str, after: str, limit: int) -> list[Intervention]:
        return interventions(
            service.ws.journal(),
            service.ws.unit_meta(),
            service.holds.attempts,
            workspace_key(workspace),
            after,
            limit,
        )

    async def session(workspace: str, kind: str, prompt: str) -> Submitted:
        """The estimate's session (`Backlog.submitting`), held by an attempt of `kind`."""
        key = workspace_key(workspace)
        refuse_while_updating(service.updater)
        journal = service.ws.journal()
        if journal is None:
            raise Invalid("no working folder is set, so a session cannot be recorded")
        attempt = service.holds.attempts.open(kind, key, "", kind)["id"]
        service.holds.attempts.move(attempt, "running")
        outcome = "failed"
        got = Submitted(None, {}, "", "the session ended before it handed anything back")
        try:
            stream = service.backlog.submitting(workspace, key, journal, kind, prompt)
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
            got = Submitted(
                None, {}, "", "an update paused the session; what it spent is in the run log"
            )
        except asyncio.CancelledError:
            outcome = "interrupted"
            raise
        finally:
            service.holds.attempts.move(attempt, "ended", outcome)
        return got

    async def create_unit(workspace: str, slug: str, brief: str) -> str:
        return str((await service.answers.create_unit(workspace, slug, brief))["unit"])

    def schedule(feature: str, workspace: str) -> int:
        plugin = next((f for f in features if f.name == feature), None)
        if plugin is None or plugin.schedule is None:
            return 0
        return schedule_of(data, plugin, Workspaces.key(workspace))

    def set_schedule(feature: str, workspace: str, hours: int) -> None:
        set_schedule_of(service, features, feature, workspace, hours)

    return Ctx(
        service.ws.journal,
        workspace_key,
        enabled,
        service.bus,
        data,
        state,
        arm,
        main_tree,
        found,
        session,
        create_unit,
        schedule,
        set_schedule,
    )


def schedule_of(data: Data, plugin: Plugin, key: str) -> int:
    """The hours chosen for `(plugin, workspace key)`, else its schedule's `default`."""
    if plugin.schedule is None:
        return 0
    got = _pref(data, SCHEDULE_PREF).get(plugin.name)
    hours = got.get(key) if isinstance(got, dict) else None
    if isinstance(hours, int) and hours in plugin.schedule.hours:
        return hours
    return plugin.schedule.default


def set_schedule_of(
    service: Service, features: Sequence[Plugin], feature: str, cwd: str, hours: object
) -> int:
    """Set `feature`'s schedule for the workspace `cwd`. `Invalid`: a feature or workspace not
    known, one without a schedule, or hours it does not offer."""
    plugin = next((f for f in features if f.name == feature), None)
    if plugin is None:
        raise Invalid(f"not a feature: {feature}")
    if plugin.schedule is None:
        raise Invalid(f"{feature} has no schedule")
    service.ws.check(cwd)
    if not isinstance(hours, int) or isinstance(hours, bool) or hours not in plugin.schedule.hours:
        offered = ", ".join(str(h) for h in plugin.schedule.hours)
        raise Invalid(f"schedule must be one of {offered} hours")
    data = Data(service.config.data_dir)
    chosen = _pref(data, SCHEDULE_PREF)
    mine = chosen.get(feature)
    chosen[feature] = {**(mine if isinstance(mine, dict) else {}), Workspaces.key(cwd): hours}
    data.set_pref(SCHEDULE_PREF, chosen)
    return hours


async def tick(service: Service, ctx: Ctx, features: Sequence[Plugin]) -> None:
    """One round of every scheduled feature, in each listed workspace where it is not `off` and
    its schedule is not `0`. A tick that fails is logged and the next still runs."""
    for f in features:
        if f.schedule is None:
            continue
        for cwd in service.ws.all()["paths"]:
            hours = ctx.schedule(f.name, cwd)
            if not hours or not ctx.enabled(f.name, cwd):
                continue
            try:
                await f.schedule.tick(ctx, cwd, hours)
            except Exception:
                log.exception("the scheduled run of %s in %s failed", f.name, cwd)


def shown(ctx: Ctx, features: Sequence[Plugin], cwd: str) -> list[Shown]:
    """Each feature for the workspace `cwd`; one whose `status` forbids choosing shows `off`."""
    out = []
    for f in features:
        state = ctx.state(f.name, cwd)
        sentence, may = f.status(ctx, cwd) if f.status else ("", True)
        if not sentence:
            sentence = "Off in this workspace." if state == "off" else "On in this workspace."
        hours = f.schedule.hours if f.schedule else ()
        schedule = ctx.schedule(f.name, cwd) if f.schedule else None
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
    service: Service, ctx: Ctx, features: Sequence[Plugin], feature: str, cwd: str, state: str
) -> State:
    """Set `feature`'s state for the workspace `cwd`, then tell the feature. `Invalid`: a feature,
    workspace or state not known, `pilot` for a feature without it, or `pilot`/`on` while its
    `status` forbids them."""
    plugin = next((f for f in features if f.name == feature), None)
    if plugin is None:
        raise Invalid(f"not a feature: {feature}")
    key = ctx.workspace_key(cwd)
    chosen = next((s for s in STATES if s == state), None)
    if chosen is None:
        raise Invalid(f"state must be one of {', '.join(STATES)}")
    if chosen == "pilot" and not plugin.pilot:
        raise Invalid(f"{feature} has no pilot: choose on or off")
    if chosen != "off" and plugin.status:
        sentence, may = plugin.status(ctx, cwd)
        if not may:
            raise Invalid(sentence or f"{feature} cannot be turned on here")
    data = Data(service.config.data_dir)
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
