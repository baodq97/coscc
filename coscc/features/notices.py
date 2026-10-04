"""Notices: which run-log records a listener is told of, what each says, and following them.

`notice_of` is the one place a record becomes a notice: its kind and the sentence shown. Only
tells; nothing here writes and no sentence reads as approval. A sentence names the unit and
the workspace's folder name and never carries a `reason`, `detail`, path, SHA or `run` (a
reason can hold any of them, or `gh`'s words). The whole record goes out beside it as `record`.

`Notices` holds only the `Ctx`; it reads and writes nothing but the lines it hands out. The file
ends in `PLUGIN`: the route, and the script that shows the notices on the page.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from typing import Any, Literal, get_args

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from starlette.routing import BaseRoute

from coscc.auth import WS_RECHECK
from coscc.data import Busy
from coscc.plugin import Ctx, Plugin, line
from coscc.runlog.journal import BELL
from coscc.service.common import Invalid
from coscc.units import autopilot

# The run-log kinds a notice can come from; `Journal.notice_rows` narrows on them.
SOURCE_KINDS = ("autopilot-stop", "questions", "end", "ship")
# The five kinds, in order.
Kind = Literal["autopilot-stop", "questions", "step-ended", "ship-refused", "shipped"]
KINDS: tuple[Kind, ...] = get_args(Kind)
# Seconds between two `beat` lines of a quiet stream. Chosen, not measured.
BEAT_SECONDS = 15.0
# Seconds one stream lasts before it ends and its listener reconnects with `after`.
# `auth.Guard` checks the session once per request, so without an end a logged-out listener
# would keep hearing.
LIFETIME_SECONDS = WS_RECHECK
# How many rows one read of the stream takes at most. Chosen, not measured.
PAGE = 500

# What each stop of `autopilot.STOP_KINDS` means, said of a unit. `full` is no autopilot stop
# and never reaches here.
STOPS = {
    "a": "it has open questions",
    "b": "it is waiting for a person",
    "c": "shipping waits for a person",
    "d": "its last integration needs a person",
    "e": "its last step did not finish",
    "f": "there is nothing it may run",
    "cap": "the daily spending cap is reached",
    "shortlist": "nothing is on the shortlist",
    "reruns": "a draft was run again as often as it may",
}
# The same, said of a whole workspace: a failed look at it, `COS_HOST` off loopback, a busy
# run log. The reason says which; the sentence does not.
WORKSPACE_F = "it could not look at the workspace"

# How an `end` that is not `done` is said (`journal.OUTCOMES`).
ENDED = {
    "failed": "failed",
    "exhausted": "ran out of turns",
    "stopped": "was stopped",
    "cancelled": "was cancelled",
}


def _where(record: dict[str, Any]) -> str:
    """The workspace's folder name, never its path."""
    return Path(str(record.get("workspace") or "")).name or "this workspace"


def _stop_text(record: dict[str, Any]) -> str:
    unit = str(record.get("unit") or "")
    stop = str(record.get("stop") or "")
    where = _where(record)
    if not unit:
        said = WORKSPACE_F if stop == "f" else STOPS.get(stop)
        if said is None:
            return f"The autopilot stopped in {where}."
        return f"The autopilot stopped in {where}: {said}."
    said = STOPS.get(stop)
    if said is None:
        return f"The autopilot stopped on {unit} in {where}."
    return f"The autopilot stopped on {unit} in {where}: {said}."


def _kind_and_text(record: dict[str, Any]) -> tuple[str, str] | None:
    kind = record.get("kind")
    unit = str(record.get("unit") or "")
    stage = str(record.get("stage") or "")
    where = _where(record)
    if kind == "autopilot-stop":
        stop = str(record.get("stop") or "")
        if not stop or stop == "full":
            return None
        return "autopilot-stop", _stop_text(record)
    if kind == "questions":
        n = len(record.get("questions") or [])
        asked = f"{n} open question{'' if n == 1 else 's'}" if n else "open questions"
        after = f" after its {stage} step" if stage else ""
        return "questions", f"{unit or 'A unit'} in {where} has {asked}{after}."
    if kind == "end":
        outcome = str(record.get("outcome") or "")
        if not unit or not autopilot.is_step(record) or outcome == "done":
            return None
        said = ENDED.get(outcome, "ended without finishing")
        return "step-ended", f"The {stage or 'last'} step of {unit} in {where} {said}."
    if kind == "ship":
        result = record.get("result")
        if result == "shipped":
            return "shipped", f"{unit} in {where} was merged."
        if result == "refused":
            return "ship-refused", f"{unit} in {where} did not merge."
    return None


def notice_of(id: int, record: dict[str, Any]) -> dict[str, Any] | None:
    """The notice line for the run-log row `id`, or `None` when the row makes no notice.

    The keys stay in this order: `type` then `id` open every line, and the terminal command
    in `coscc/features/notices.md` reads `id` off that prefix.
    """
    found = _kind_and_text(record)
    if found is None:
        return None
    kind, text = found
    return {
        "type": "notice",
        "id": int(id),
        "at": str(record.get("at") or ""),
        "workspace": str(record.get("workspace") or ""),
        "unit": str(record.get("unit") or ""),
        "stage": str(record.get("stage") or ""),
        "kind": kind,
        "text": text,
        "record": record,
    }


class Notices:
    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx

    # -- notices -------------------------------------------------------------
    #
    # One stream for every listener. A connection lasts `LIFETIME_SECONDS` at most, then
    # ends and the listener comes back with `after`; a peer that vanished holds it until then or
    # until a `beat` fails to write.

    def notice_scope(self, workspace: str) -> str | None:
        """The journal key to narrow to, `None` for every workspace; an unknown workspace or a stream with no run log is refused."""
        if self.ctx.journal() is None:
            raise Invalid("there is no working folder, so there is no run log to follow")
        if not workspace:
            return None
        return self.ctx.workspace_key(workspace)

    async def follow_notices(
        self,
        scope: str | None,
        after: int | None,
        beat: float = BEAT_SECONDS,
        lifetime: float | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """With no `after`, or one past every row, a `head` line first; with one, every notice
        past it first. Then each notice as it lands, in `id` order and none twice, and a `beat`
        after `beat` seconds without a line. Ends `lifetime` seconds in.

        A record of a workspace with notices off is passed over. A record this process appends rings `BELL` and is read at once; one another process
        appends is read at the next wake. The ticket is armed before each read, so a ring
        during the read is not missed."""
        journal = self.ctx.journal()
        if journal is None:
            raise Invalid("there is no working folder, so there is no run log to follow")
        loop = asyncio.get_running_loop()
        ends = loop.time() + (LIFETIME_SECONDS if lifetime is None else lifetime)
        head = await asyncio.to_thread(journal.last_id)
        if after is None or after > head:
            # An `after` past every row is a cursor from a run log since replaced, whose ids start
            # again at 1: kept, it would hide every notice. The `head` line sets the listener's cursor.
            last = head
            yield {"type": "head", "id": head}
        else:
            last = after
        said = loop.time()
        while True:  # noqa: PLR1702 - still to split
            ticket = BELL.arm()
            try:
                while True:
                    try:
                        rows = await asyncio.to_thread(
                            journal.notice_rows,
                            last,
                            SOURCE_KINDS,
                            scope,
                            PAGE,
                        )
                    except Busy:
                        # Read again at the next wake; nothing is skipped past.
                        rows = []
                    on: dict[str, bool] = {}
                    for rid, record in rows:
                        last = rid
                        where = str(record.get("workspace") or "")
                        if where not in on:
                            on[where] = self.ctx.enabled("notices", where)
                        found = notice_of(rid, record) if on[where] else None
                        if found is not None:
                            yield found
                            said = loop.time()
                    if len(rows) < PAGE:
                        break
                if loop.time() >= ends:
                    return
                left = said + beat - loop.time()
                if left <= 0:
                    try:
                        head = await asyncio.to_thread(journal.last_id)
                    except Busy:
                        head = last
                    yield {"type": "beat", "id": head}
                    said = loop.time()
                    left = beat
                await BELL.wait(ticket, min(left, ends - loop.time()))
            finally:
                BELL.disarm(ticket)


# The colours are the Radix variables of `screens/studio.py`'s roles: INK gray 12, MUTED gray 11,
# SURFACE gray 2, LINE gray 5. A feature may not import `screens`, so they are written out.
# One stream of `/api/notices/follow` per tab, over the page kit (`plugin.KIT_JS`) and outside
# Reflex, so no hydration or route change touches the stack appended to `document.body`. The
# cursor moves only once the notice's node is in the DOM, and never down; a `head` line sets it.
_NOTICE_JS = """
(function () {
  if (window.__coscc_notices) return;
  window.__coscc_notices = true;
  window.__coscc_notice_opens = 0;
  var KEY = "coscc_notice_after", SHOWN = 5, stack = null, more = null;
  var ago = window.coscc.ago;
  function cursor() { var v = localStorage.getItem(KEY); return v === null ? null : +v; }
  function advance(id) {
    var cur = cursor();
    if (cur === null || id > cur) localStorage.setItem(KEY, String(id));
  }
  function box() {
    if (!stack) {
      stack = document.createElement("div");
      stack.id = "notice-stack";
      stack.setAttribute("role", "region");
      stack.setAttribute("aria-label", "Notices");
      stack.setAttribute("aria-live", "polite");
      stack.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:9998;display:flex;" +
        "flex-direction:column;gap:8px;width:min(360px,calc(100vw - 32px));" +
        "font:14px/1.4 var(--default-font-family,system-ui,sans-serif)";
      more = document.createElement("div");
      more.id = "notice-more";
      more.style.cssText = "color:var(--gray-11);font-size:12px;text-align:right;padding:0 4px";
      stack.appendChild(more);
    }
    if (!stack.isConnected) document.body.appendChild(stack);
    return stack;
  }
  function layout() {
    var all = stack.querySelectorAll(".notice"), hidden = Math.max(0, all.length - SHOWN);
    for (var i = 0; i < all.length; i++) all[i].style.display = i < SHOWN ? "flex" : "none";
    more.textContent = hidden ? hidden + (hidden === 1 ? " more notice" : " more notices") : "";
    more.style.display = hidden ? "block" : "none";
  }
  function refresh() {
    if (!stack) return;
    var all = stack.querySelectorAll(".notice-time");
    for (var i = 0; i < all.length; i++) all[i].textContent = ago(all[i].getAttribute("data-at"));
  }
  function show(n) {
    var s = box();
    if (s.querySelector('[data-notice-id="' + n.id + '"]')) return;
    var el = document.createElement("div");
    el.className = "notice";
    el.setAttribute("data-notice-id", String(n.id));
    el.style.cssText = "display:flex;gap:10px;align-items:flex-start;background:var(--gray-2);" +
      "color:var(--gray-12);border:1px solid var(--gray-5);border-radius:10px;padding:10px 12px;" +
      "box-shadow:0 4px 16px rgba(0,0,0,.12)";
    var body = document.createElement("div");
    body.style.cssText = "flex:1;min-width:0;overflow-wrap:anywhere";
    var text = document.createElement("div");
    text.textContent = n.text;
    var time = document.createElement("div");
    time.className = "notice-time";
    time.setAttribute("data-at", n.at || "");
    time.style.cssText = "color:var(--gray-11);font-size:12px;margin-top:2px";
    time.textContent = ago(n.at);
    body.appendChild(text);
    body.appendChild(time);
    var x = document.createElement("button");
    x.type = "button";
    x.textContent = "\\u00d7";
    x.setAttribute("aria-label", "Dismiss");
    x.style.cssText = "background:none;border:0;color:var(--gray-11);font-size:18px;line-height:1;" +
      "padding:0 2px;cursor:pointer";
    x.onclick = function () { el.remove(); layout(); };
    el.appendChild(body);
    el.appendChild(x);
    s.insertBefore(el, s.firstChild);
    layout();
  }
  window.coscc.every(60000, refresh);
  window.coscc.stream("/api/notices/follow", function (m) {
    if (m.type === "head") localStorage.setItem(KEY, String(m.id));
    else if (m.type === "notice") { show(m); advance(m.id); }
  }, {after: function () { window.__coscc_notice_opens += 1; return cursor(); }});
})();
"""


def routes(ctx: Ctx) -> Sequence[BaseRoute]:
    notices = Notices(ctx)
    router = APIRouter()

    @router.get("/api/notices/follow")
    async def follow_notices_route(request: Request) -> Any:
        """NDJSON: a `head` line when there is no `after`, a `notice` line per run-log record
        past it that is one, and a `beat` line after `BEAT_SECONDS` without one.
        `workspace` narrows to one. Reads only. It ends after `LIFETIME_SECONDS`, so a
        listener comes back through the login door.

        Holds a connection per listener (`coscc/features/notices.md`). A refusal is a 400
        before the stream starts; the first line is not waited for, since with `after` it may
        be a `beat` 15 s away."""
        raw = request.query_params.get("after") or None
        after = int(raw) if raw is not None and raw.isdigit() else None
        if raw is not None and after is None:
            raise Invalid("after must be a whole number")
        scope = notices.notice_scope(request.query_params.get("workspace", ""))
        stream = notices.follow_notices(scope, after)

        async def lines() -> AsyncIterator[bytes]:
            try:
                async for item in stream:
                    yield line(item)
            finally:
                await stream.aclose()

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    return router.routes


PLUGIN = Plugin(
    "notices",
    routes=routes,
    scripts=(_NOTICE_JS,),
    summary="Pops up a notice when a step fails, a unit has questions, the autopilot stops or a PR ships.",
)
