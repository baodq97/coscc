"""What a board step does, event by event, for anyone watching it (`0073`).

A step's session used to reach one place: the request that pressed *Run*, as text. The
tool calls, the refusals, the turns and the cost stopped in the runner, and a browser that
opened the board later saw nothing until the step was over. This module is the recorder
each board step carries (`spec.md` Design 1): every message the SDK hands `Sessions._stream`,
every refusal `permission_gate` makes and the runner's outcome become numbered events, kept
in memory for the life of the step, pushed to whoever follows it, and written to two tables
of `cos.db` beside the run log -- never into it (Design 2).

**Nothing here may change the step.** `message` and `denied` run on the SDK's read loop, so
they are synchronous, never wait for a reader or for the database, and swallow every error
into `lost` (R5). A reader that falls `SUB_LIMIT` events behind is cut, not waited for (R8).
The writes happen in a task of their own, off that loop. `close` waits at most
`2 * CLOSE_WAIT` for the last write and as long again to close the index row -- 20 s in all,
which the runner's `end` record waits for.

**Everything a step saw is kept and handed out.** Commands, paths, thinking and tool output,
unfiltered (`intent.md ## Answers, câu 6`), for up to `KEEP_DAYS` and `KEEP_BYTES`, to
whoever holds the password or a live session. `.claude/CLAUDE.md` says so.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    ServerToolUseBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from coscc.journal import TOKEN_FIELDS

# R4. Characters a text field keeps, and an `input` keeps once it is JSON. Chosen, not
# measured.
FIELD_MAX = 64_000

# R6. Seconds between two writes of what has been recorded. Chosen.
FLUSH_EVERY = 1.0

# Seconds the periodic write waits for `cos.db`. Chosen: short, so a busy database delays
# the next write rather than holding the task, and far under `CLOSE_WAIT`.
WRITE_WAIT = 2.0

# Seconds `close` gives `cos.db` for the last write before counting what is left as lost, and
# again for closing the index row. Chosen. Each is awaited up to twice this, since a write the
# periodic task began still holds `Data`'s lock, so the `end` record can wait up to
# `4 * CLOSE_WAIT` (`plan.md` Risk 9, unmeasured).
CLOSE_WAIT = 5.0

# R8. Events a follower may leave unread before it is cut. Chosen.
SUB_LIMIT = 5_000

# R7. `PAGE_DEFAULT` is the example in `intent.md ## Answers, câu 3`, not a threshold.
PAGE_DEFAULT = 200
PAGE_MAX = 500

# R12. What a collapsed field shows: this many lines, and no more than this many characters.
# Chosen.
COLLAPSE_LINES = 20
COLLAPSE_CHARS = 2_000

# R14, `spec.md ## Answers, câu 1`: "Giữ 30 ngày, và tổng không quá 200 MB". MB is read as
# 10**6 bytes of stored event JSON -- not the size of the file, which never shrinks without
# a `VACUUM` (spec C5).
KEEP_DAYS = 30
KEEP_BYTES = 200_000_000

# Seconds a follower with nothing new waits before it is handed an empty batch, so the page's
# loop can notice it was closed. Chosen.
IDLE_WAKE = 5.0

# The fields every event carries; everything else is the kind's own, and is what R4 cuts.
COMMON = ("run", "seq", "at", "kind")


def now_ms() -> int:
    return int(time.time() * 1000)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _cut(event: dict[str, Any]) -> dict[str, Any]:
    """R4. Every field longer than `FIELD_MAX` once it is text is kept as its first
    `FIELD_MAX` characters. A dict or a list is measured as JSON and, when too long, kept as
    the cut JSON string. `length` is the longest original length among the cut fields."""
    cut: list[str] = []
    longest = 0
    for name, value in list(event.items()):
        if name in COMMON or value is None or isinstance(value, (bool, int, float)):
            continue
        text = value if isinstance(value, str) else _json(value)
        if len(text) > FIELD_MAX:
            event[name] = text[:FIELD_MAX]
            cut.append(name)
            longest = max(longest, len(text))
    if cut:
        event["truncated"] = True
        event["length"] = longest
        event["truncated_fields"] = cut
    return event


def _result_fields(message: Any) -> dict[str, Any]:
    """R3 `result`: turns, cost, the four token fields `journal.TOKEN_FIELDS` adds, time."""
    from coscc.sessions import _cumulative  # the one reading of `model_usage`

    total = _cumulative(message)
    return {
        "num_turns": int(getattr(message, "num_turns", 0) or 0),
        "cost_usd": getattr(message, "total_cost_usd", None),
        **{name: int(total.get(name) or 0) for name in TOKEN_FIELDS},
        "duration_ms": int(getattr(message, "duration_ms", 0) or 0),
        "terminal_reason": getattr(message, "terminal_reason", None) or getattr(message, "subtype", None),
    }


def _system_fields(thing: Any) -> dict[str, Any]:
    """R3 `system`: anything else the SDK hands over, by class name, with its data as JSON."""
    try:
        data = vars(thing)
    except TypeError:
        data = {"repr": repr(thing)}
    return {
        # Not `type`: the follow route's NDJSON lines carry their own `type`, and an event's
        # field of that name would replace it.
        "class": type(thing).__name__,
        "subtype": getattr(thing, "subtype", None),
        "data": _json(data),
    }


class Recorder:
    """The events of one board step's `run`. One per step, made by `Service.run_step`.

    `events` holds every event of the run until it ends (spec C7, unmeasured); `pending` what
    is not on disk yet; `subscribers` the followers' queues. All three are touched only on
    the event loop, and never across an `await`.
    """

    def __init__(self, run: str, data: Any, root: str, workspace: str, unit: str, stage: str):
        self.run = run
        self.data = data
        self.root = root
        self.workspace = workspace
        self.unit = unit
        self.stage = stage
        self.started_at = now_ms()
        self.seq = 0
        self.turns = 0
        self.lost = 0
        self.closed = False
        self.events: list[dict[str, Any]] = []
        self.pending: list[dict[str, Any]] = []
        self.subscribers: set[asyncio.Queue] = set()
        self._message_ids: set[str] = set()
        self._task: asyncio.Task | None = None
        self._opened = False

    # -- what goes in (the SDK's read loop: synchronous, never raises) ---------------------

    def _emit(self, kind: str, **fields: Any) -> None:
        try:
            self.seq += 1
            event = _cut({"run": self.run, "seq": self.seq, "at": now_ms(), "kind": kind, **fields})
            self.events.append(event)
            self.pending.append(event)
            for q in list(self.subscribers):
                if q.qsize() >= SUB_LIMIT:
                    # R8: cut, and told where to read again from. The step does not wait.
                    self.subscribers.discard(q)
                    q.put_nowait(("cut", self.seq))
                else:
                    q.put_nowait(("event", event))
        except Exception:  # noqa: BLE001 - R5: nothing here may reach the step
            self.lost += 1

    def message(self, msg: Any) -> None:
        """R3. One SDK message, as one or more events. Synchronous: no `await` on this path."""
        try:
            before = self.seq
            if isinstance(msg, AssistantMessage):
                mid = getattr(msg, "message_id", None)
                if mid and mid not in self._message_ids:
                    # A turn is a message id not seen before (`spike.md ## U1`, C11).
                    self._message_ids.add(mid)
                    self.turns += 1
                    self._emit("turn", n=self.turns)
                for block in msg.content or []:
                    self._block(block)
            elif isinstance(msg, UserMessage):
                persisted = getattr(msg, "tool_use_result", None)
                content = msg.content if isinstance(msg.content, list) else []
                if isinstance(msg.content, str):
                    self._emit("text", role="user", text=msg.content)
                for block in content:
                    self._block(block, persisted, role="user")
            elif isinstance(msg, ResultMessage):
                self._emit("result", **_result_fields(msg))
            if self.seq == before:
                # R3: every message yields at least one event, whatever it is.
                self._emit("system", **_system_fields(msg))
        except Exception:  # noqa: BLE001 - R5
            self.lost += 1

    def _block(self, block: Any, persisted: Any = None, role: str = "") -> None:
        if isinstance(block, TextBlock):
            self._emit("text", **({"role": role} if role else {}), text=block.text)
        elif isinstance(block, ThinkingBlock):
            self._emit("thinking", thinking=block.thinking)
        elif isinstance(block, (ToolUseBlock, ServerToolUseBlock)):
            self._emit("tool_use", id=block.id, name=block.name, input=block.input)
        elif isinstance(block, ToolResultBlock):
            extra: dict[str, Any] = {}
            if isinstance(persisted, dict) and persisted.get("persistedOutputPath"):
                # The CLI's own file for an output it did not stream whole. Named, never read
                # (spec C3).
                extra = {
                    "persisted_path": str(persisted.get("persistedOutputPath")),
                    "persisted_size": persisted.get("persistedOutputSize"),
                }
            self._emit(
                "tool_result", tool_use_id=block.tool_use_id, is_error=bool(block.is_error),
                content=block.content, **extra,
            )
        else:
            self._emit("system", **_system_fields(block))

    def denied(self, tool: str, tool_input: Any, reason: str) -> None:
        """R3 `denied`: one refusal of the grant's gate. Every one, not the first five."""
        self._emit("denied", tool=tool, input=tool_input, reason=reason)

    # -- followers ---------------------------------------------------------------------

    def subscribe(self, after: int) -> tuple[asyncio.Queue, list[dict[str, Any]]]:
        """Register first, then read what is there (`spike.md ## U3`): nothing falls between."""
        q: asyncio.Queue = asyncio.Queue()
        self.subscribers.add(q)
        return q, [e for e in self.events if e["seq"] > after]

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    # -- the disk (a task of its own) --------------------------------------------------

    def start(self) -> None:
        """Begin writing. The index row goes first, so a step cut off early still has one."""
        if self._task is None and not self.closed:
            self._task = asyncio.ensure_future(self._flusher())

    def _write(self, rows: list[dict[str, Any]], timeout: float) -> None:
        """In a thread. Rows already written are ignored, so a write repeated after a cancel
        stores nothing twice."""
        if not self._opened:
            self.data.step_run_open(
                self.run, self.root, self.workspace, self.unit, self.stage, self.started_at,
                timeout=timeout,
            )
            self._opened = True
        if rows:
            self.data.step_events_add(self.run, rows, timeout=timeout)

    async def _flush(self, timeout: float) -> bool:
        batch = list(self.pending)
        try:
            await asyncio.to_thread(self._write, batch, timeout)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - `Busy` or anything else: kept for the next write
            return False
        # Only what was written leaves; what arrived during the write stays.
        del self.pending[: len(batch)]
        return True

    async def _flusher(self) -> None:
        while True:
            await self._flush(WRITE_WAIT)
            await asyncio.sleep(FLUSH_EVERY)

    async def _stop_flusher(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def close(self, outcome: str, detail: str | None) -> int:
        """R3 `end`, then everything left to disk, before the runner writes its `end` record
        (R6). Returns how many events never reached disk. Never raises, bar a cancel."""
        if self.closed:
            return self.lost
        self._emit("end", outcome=outcome, detail=detail or "")
        self.closed = True
        try:
            await self._stop_flusher()
            written = await asyncio.wait_for(self._flush(CLOSE_WAIT), CLOSE_WAIT * 2)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a timeout: what is left is lost
            written = False
        if not written:
            self.lost += len(self.pending)
            self.pending.clear()
        try:
            await asyncio.wait_for(asyncio.to_thread(
                self.data.step_run_close, self.run, now_ms(), self.lost, timeout=CLOSE_WAIT,
            ), CLOSE_WAIT * 2)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            pass
        return self.lost

    async def abandon(self) -> None:
        """The app is going down with the step running (spec C9): what can be written is, and
        no `end` -- the index row keeps `ended_at` empty, which reads `ended-unknown`."""
        if self.closed:
            return
        self.closed = True
        try:
            await self._stop_flusher()
            await asyncio.wait_for(self._flush(CLOSE_WAIT), CLOSE_WAIT * 2)
        except BaseException:  # noqa: BLE001 - best effort, on the way out
            pass


# -- what the page shows (R12) -------------------------------------------------------


def _clip(text: str) -> tuple[str, bool]:
    lines = text.split("\n")
    short = "\n".join(lines[:COLLAPSE_LINES])
    if len(short) > COLLAPSE_CHARS:
        short = short[:COLLAPSE_CHARS]
    return short, short != text


def _body(event: dict[str, Any]) -> str:
    kind = event.get("kind")
    if kind == "text":
        value = event.get("text")
    elif kind == "thinking":
        value = event.get("thinking")
    elif kind in ("tool_use", "denied"):
        value = event.get("input")
    elif kind == "tool_result":
        value = event.get("content")
    elif kind == "system":
        value = event.get("data")
    elif kind == "end":
        value = event.get("detail")
    else:
        value = ""
    if value is None:
        return ""
    return value if isinstance(value, str) else _json(value)


def _label(event: dict[str, Any]) -> str:
    kind = event.get("kind")
    if kind == "text":
        return "text" + (" (user)" if event.get("role") == "user" else "")
    if kind == "turn":
        return f"lượt {event.get('n')}"
    if kind == "tool_use":
        return f"{event.get('name')}"
    if kind == "tool_result":
        return f"kết quả của {str(event.get('tool_use_id') or '')[-8:]}" + (
            " (lỗi)" if event.get("is_error") else ""
        )
    if kind == "denied":
        return f"{event.get('tool')} bị từ chối: {event.get('reason')}"
    if kind == "result":
        cost = event.get("cost_usd")
        tokens = sum(int(event.get(name) or 0) for name in TOKEN_FIELDS)
        return (
            f"{event.get('num_turns')} lượt · "
            + (f"${float(cost):.4f}" if cost is not None else "chi phí không rõ")
            + f" · {tokens} token · {event.get('terminal_reason') or ''}"
        ).rstrip(" ·")
    if kind == "system":
        return " ".join(str(p) for p in (event.get("class"), event.get("subtype")) if p)
    if kind == "end":
        return f"kết thúc: {event.get('outcome')}"
    return str(kind or "")


def when(at_ms: Any) -> str:
    """`at` as the page prints it: UTC, to the millisecond."""
    try:
        moment = datetime.fromtimestamp(int(at_ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return ""
    return moment.strftime("%H:%M:%S.") + f"{moment.microsecond // 1000:03d}"


def collapse(event: dict[str, Any]) -> dict[str, Any]:
    """R12. An event as the page holds it: a label, a body at most `COLLAPSE_LINES` lines or
    `COLLAPSE_CHARS` characters, and whether that is all of it. The page never holds more."""
    body, collapsed = _clip(_body(event))
    persisted = ""
    if event.get("persisted_path"):
        size = event.get("persisted_size")
        persisted = (
            f"đầu ra đầy đủ ({size if size is not None else '?'} ký tự) nằm ở "
            f"{event['persisted_path']} trên máy chạy app; board không đọc file này"
        )
    return {
        "seq": int(event.get("seq") or 0),
        "at": int(event.get("at") or 0),
        "when": when(event.get("at")),
        "kind": str(event.get("kind") or ""),
        "label": _label(event),
        "body": body,
        "collapsed": collapsed,
        "truncated": bool(event.get("truncated")),
        # Not `length`: on the page that name is a list's own method.
        "original_length": int(event.get("length") or 0),
        "persisted": persisted,
    }


def full_text(event: dict[str, Any]) -> str:
    """R12 *Mở*: the body as stored, whole (R4's cut is all that was ever kept)."""
    return _body(event)


# -- R14 -----------------------------------------------------------------------------


async def purge(
    data: Any, journal: Any, now: int | None = None,
    keep_days: int = KEEP_DAYS, keep_bytes: int = KEEP_BYTES,
) -> tuple[int, int]:
    """R14, at startup only. Whole runs, oldest first: past `keep_days`, then while the total
    is over `keep_bytes`. Index rows stay, with `purged_at`. One `events-purge` row in the run
    log when anything went, and only when there is a run log."""
    from coscc.data import now as iso_now

    at = now_ms() if now is None else int(now)
    older_than = at - keep_days * 24 * 3600 * 1000
    runs, freed = await asyncio.to_thread(data.step_events_purge, older_than, keep_bytes, iso_now())
    if runs and journal is not None:
        await asyncio.to_thread(
            journal.append, {"kind": "events-purge", "workspace": "", "runs": runs, "bytes": freed},
        )
    return runs, freed


def purge_on_start(config: Any) -> tuple[int, int]:
    """What `coscc/run.py` calls before the server is built: a `Data` and a `Journal` built
    from `config` the way `Service` builds them, and nothing else of the app."""
    from coscc.data import Data
    from coscc.journal import Journal

    data = Data(config.data_dir)
    journal = Journal(config.working_dir, data) if config.working_dir else None
    return asyncio.run(purge(data, journal))
