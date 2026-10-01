"""The core's door for a feature: what it gets of the running app, and what it hands back.

A feature (`coscc/features/<name>.py`) ends in one `PLUGIN` and reaches the app only through a
`Ctx`. It is turned off per workspace by the pref `features.off`, `{feature: [workspace keys]}`.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse
from starlette.routing import BaseRoute

from coscc.agent.policy import is_prose_stage
from coscc.bus import Bus
from coscc.data import Data
from coscc.hooks import Hooks, Parts
from coscc.runlog.journal import Journal
from coscc.service import Service
from coscc.service.common import Invalid
from coscc.service.workspaces import Workspaces

OFF_PREF = "features.off"
CREATE_TABLE = re.compile(r"\s*CREATE TABLE IF NOT EXISTS\s+\w+", re.IGNORECASE)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Ctx:
    journal: Callable[[], Journal | None]
    # Checks the workspace, raising `Invalid`, and returns how the run log names it.
    workspace_key: Callable[[str], str]
    # `(feature, workspace path)`; a workspace the app does not know counts as on.
    enabled: Callable[[str, str], bool]
    bus: Bus
    data: Data


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


def _off(data: Data) -> dict[str, list[str]]:
    stored = data.pref(OFF_PREF, {})
    return stored if isinstance(stored, dict) else {}


def ctx_of(service: Service) -> Ctx:
    data = Data(service.config.data_dir)

    def workspace_key(cwd: str) -> str:
        service.ws.check(cwd)
        return Workspaces.key(cwd)

    def enabled(feature: str, workspace: str) -> bool:
        return Workspaces.key(workspace) not in _off(data).get(feature, [])

    return Ctx(service.ws.journal, workspace_key, enabled, service.bus, data)


def set_enabled(service: Service, known: Sequence[str], feature: str, cwd: str, on: bool) -> None:
    """Turn `feature` on or off for the workspace `cwd`; a feature or workspace not known is `Invalid`."""
    if feature not in known:
        raise Invalid(f"not a feature: {feature}")
    key = ctx_of(service).workspace_key(cwd)
    data = Data(service.config.data_dir)
    off = _off(data)
    keys = [k for k in off.get(feature, []) if k != key]
    if not on:
        keys.append(key)
    off[feature] = keys
    data.set_pref(OFF_PREF, off)
