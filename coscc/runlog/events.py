"""The recorder each board step carries: what the step does, event by event, for anyone watching.

Every SDK message `Sessions._stream` gets, every refusal the session's gate records, the `config`
a session opened with and the runner's outcome become numbered events, kept in memory for the life of the step, pushed to
followers, and written to two tables of `cos.db` (never into the run log).

Nothing here may change the step. `message` and `denied` run on the SDK's read loop, so they
are synchronous, never wait for a reader or the database, and swallow every error into `lost`.
A reader `SUB_LIMIT` events behind is cut, not waited for. Writes happen in a task of their
own. `close` waits at most `2 * CLOSE_WAIT` for the last write and as long again for the
index row (20 s in all, which the runner's `end` record waits for).

Everything a step saw is kept and handed out unfiltered (commands, paths, thinking, tool
output), up to `KEEP_DAYS` and `KEEP_BYTES`, to whoever holds the password or a live session.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping
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

from coscc.agent.sessions import cumulative
from coscc.store.db import Data, in_thread
from coscc.store.db import now as iso_now
from coscc.store.journal import TOKEN_FIELDS, Journal

log = logging.getLogger(__name__)

# Characters a text field keeps, and an `input` keeps once it is JSON. Chosen, not measured.
FIELD_MAX = 64_000

# Seconds between two writes of what has been recorded. Chosen.
FLUSH_EVERY = 1.0

# Seconds the periodic write waits for `cos.db`. Chosen: short, so a busy database delays
# the next write rather than holding the task, and far under `CLOSE_WAIT`.
WRITE_WAIT = 2.0

# Seconds `close` gives `cos.db` for the last write before counting what is left as lost, and
# again for closing the index row. Each is awaited up to twice this, since a write the periodic
# task began still holds `Data`'s lock, so the `end` record can wait up to `4 * CLOSE_WAIT`.
CLOSE_WAIT = 5.0

# Events a follower may leave unread before it is cut. Chosen.
SUB_LIMIT = 5_000

# `PAGE_DEFAULT` is an example, not a threshold.
PAGE_DEFAULT = 200
PAGE_MAX = 500

# Keep 30 days and no more than 200 MB in total. MB is 10**6 bytes of stored event JSON, not
# the size of the file, which never shrinks without a `VACUUM`.
KEEP_DAYS = 30
KEEP_BYTES = 200_000_000

# Seconds a follower with nothing new waits before it is handed an empty batch, so the page's
# loop can notice it was closed. Chosen.
IDLE_WAKE = 5.0

# The fields every event carries; everything else is the kind's own, and is what gets cut.
COMMON = ("run", "seq", "at", "kind")

# What a step's helpers did, for measuring only: a start, an end with its tokens, and each write.
HELPER_KINDS = ("worker_start", "worker_end", "worker_write")


def now_ms() -> int:
    return int(time.time() * 1000)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _cut(event: dict[str, Any]) -> dict[str, Any]:
    """Every field longer than `FIELD_MAX` once it is text is kept as its first `FIELD_MAX`
    characters. A dict or a list is measured as JSON and, when too long, kept as the cut JSON
    string. `length` is the longest original length among the cut fields.
    """
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
    """`result`: turns, cost, the four token fields `journal.TOKEN_FIELDS` adds, time."""
    total = cumulative(message)
    return {
        "num_turns": int(getattr(message, "num_turns", 0) or 0),
        "cost_usd": getattr(message, "total_cost_usd", None),
        **{name: int(total.get(name) or 0) for name in TOKEN_FIELDS},
        "duration_ms": int(getattr(message, "duration_ms", 0) or 0),
        "terminal_reason": getattr(message, "terminal_reason", None)
        or getattr(message, "subtype", None),
    }


def _system_fields(thing: Any) -> dict[str, Any]:
    """`system`: anything else the SDK hands over, by class name, with its data as JSON."""
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
    """The events of one board step's `run`. One per step, made by `Steps.run_step`.

    `events` holds every event of the run until it ends; `pending` what is not on disk yet;
    `subscribers` the followers' queues. All three are touched only on the event loop, and never
    across an `await`.
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
        # Each helper's `agent_id` by the id of the `Agent` call that started it, and the helper
        # whose message is being recorded now: every event of it carries that `agent_id`.
        self._helpers: dict[str, str] = {}
        self._from = ""
        self._task: asyncio.Task | None = None
        self._opened = False

    def _emit(self, kind: str, **fields: Any) -> None:
        try:
            self.seq += 1
            mark = {"agent_id": self._from} if self._from else {}
            event = _cut(
                {"run": self.run, "seq": self.seq, "at": now_ms(), "kind": kind, **mark, **fields}
            )
            self.events.append(event)
            self.pending.append(event)
            for q in list(self.subscribers):
                if q.qsize() >= SUB_LIMIT:
                    # Cut, and told where to read again from. The step does not wait.
                    self.subscribers.discard(q)
                    q.put_nowait(("cut", self.seq))
                else:
                    q.put_nowait(("event", event))
        except Exception:  # noqa: BLE001 - `_lose` logs the first; nothing here may reach the step
            self._lose()

    def config(
        self,
        *,
        model: str | None,
        model_source: str,
        effort: str | None,
        effort_source: str,
        max_turns: int,
        max_turns_source: str,
        max_budget_usd: float | None,
        max_budget_source: str,
        granted: list[str] | None = None,
    ) -> None:
        """`config`: what the session was handed, once, as it opens and before any SDK event, and
        again for the segment of a step taken up after an update: the values, with where each came
        from, and what its grant holds (`policy.granted`). The runner hands them in; nothing is
        resolved here."""
        self._emit(
            "config",
            granted=list(granted or ()),
            model=model,
            model_source=model_source,
            effort=effort,
            effort_source=effort_source,
            max_turns=max_turns,
            max_turns_source=max_turns_source,
            max_budget_usd=max_budget_usd,
            max_budget_source=max_budget_source,
        )

    def message(self, msg: Any) -> None:
        """One SDK message, as one or more events; a helper's carry its `agent_id`. Synchronous: no
        `await` on this path."""
        parent = str(getattr(msg, "parent_tool_use_id", None) or "")
        self._from = self._helpers.get(parent, parent)
        try:
            data = getattr(msg, "data", None)
            if getattr(msg, "subtype", "") == "task_started" and isinstance(data, dict):
                if data.get("tool_use_id") and data.get("task_id"):
                    self._helpers[str(data["tool_use_id"])] = str(data["task_id"])
            before = self.seq
            if isinstance(msg, AssistantMessage):
                mid = getattr(msg, "message_id", None)
                if mid and mid not in self._message_ids:
                    # A turn is a message id not seen before.
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
                # Every message yields at least one event, whatever it is.
                self._emit("system", **_system_fields(msg))
        except Exception:  # noqa: BLE001 - `_lose` logs the first
            self._lose()
        finally:
            self._from = ""

    def _lose(self) -> None:
        """One event lost, counted in the `end` row; the first of a run is logged with its
        traceback, so a message the recorder cannot read logs once, not once per message."""
        if not self.lost:
            log.exception("run %s lost an event", self.run)
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
                # The CLI's own file for an output it did not stream whole. Named, never read.
                extra = {
                    "persisted_path": str(persisted.get("persistedOutputPath")),
                    "persisted_size": persisted.get("persistedOutputSize"),
                }
            self._emit(
                "tool_result",
                tool_use_id=block.tool_use_id,
                is_error=bool(block.is_error),
                content=block.content,
                **extra,
            )
        else:
            self._emit("system", **_system_fields(block))

    def denied(self, tool: str, tool_input: Any, reason: str, lacked: str = "") -> None:
        """`denied`: one refusal of the run's gate, with the grant it lacked when the hook's rule
        names one (`policy.lacked`). Every one, not the first five."""
        self._emit("denied", tool=tool, input=tool_input, reason=reason, lacked=lacked)

    def helper(self, kind: str, fields: Mapping[str, object]) -> None:
        """One of `HELPER_KINDS`, as `coscc/agent/helpers.py` tells it; any other kind is dropped."""
        if kind in HELPER_KINDS:
            self._emit(kind, **fields)

    def subscribe(self, after: int) -> tuple[asyncio.Queue, list[dict[str, Any]]]:
        """Register first, then read what is there: nothing falls between."""
        q: asyncio.Queue = asyncio.Queue()
        self.subscribers.add(q)
        return q, [e for e in self.events if e["seq"] > after]

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    def start(self) -> None:
        """Begin writing. The index row goes first, so a step cut off early still has one."""
        if self._task is None and not self.closed:
            self._task = asyncio.ensure_future(self._flusher())

    def _write(self, rows: list[dict[str, Any]], timeout: float) -> None:
        """In a thread. Rows already written are ignored, so a write repeated after a cancel
        stores nothing twice."""
        if not self._opened:
            self.data.step_run_open(
                self.run,
                self.root,
                self.workspace,
                self.unit,
                self.stage,
                self.started_at,
                timeout=timeout,
            )
            self._opened = True
        if rows:
            self.data.step_events_add(self.run, rows, timeout=timeout)

    async def _flush(self, timeout: float) -> bool:
        batch = list(self.pending)
        try:
            await in_thread(self._write, batch, timeout)
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
        """`end`, then everything left to disk, before the runner writes its `end` record.
        Returns how many events never reached disk. Never raises, bar a cancel.
        """
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
            await asyncio.wait_for(
                in_thread(
                    self.data.step_run_close,
                    self.run,
                    now_ms(),
                    self.lost,
                    timeout=CLOSE_WAIT,
                ),
                CLOSE_WAIT * 2,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("the run row of a step was not closed")
        return self.lost

    async def stored_turns(self) -> tuple[int | None, str]:
        """After `close`: the turns of this run as stored, and where the number came from --
        `"events"`, or `"memory"` when `cos.db` could not be read and the count held here stands in.
        Never raises, bar a cancel.
        """
        try:
            n = await asyncio.wait_for(
                in_thread(
                    self.data.step_turns,
                    self.run,
                    timeout=CLOSE_WAIT,
                ),
                CLOSE_WAIT * 2,
            )
            return int(n), "events"
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - `Busy`, a timeout: the count in memory stands in
            return (self.turns if isinstance(self.turns, int) else None), "memory"

    async def abandon(self) -> None:
        """The app is going down with the step running: what can be written is, and no `end` -- the
        index row keeps `ended_at` empty, which reads `ended-unknown`.
        """
        if self.closed:
            return
        self.closed = True
        try:
            await self._stop_flusher()
            await asyncio.wait_for(self._flush(CLOSE_WAIT), CLOSE_WAIT * 2)
        except BaseException:  # noqa: BLE001, S110 - best effort, on the way out
            pass


async def purge(
    data: Any,
    journal: Any,
    now: int | None = None,
    keep_days: int = KEEP_DAYS,
    keep_bytes: int = KEEP_BYTES,
) -> tuple[int, int]:
    """At startup only. Whole runs, oldest first: past `keep_days`, then while the total is over
    `keep_bytes`. Index rows stay, with `purged_at`. One `events-purge` row in the run log when
    anything went, and only when there is a run log.
    """
    at = now_ms() if now is None else int(now)
    older_than = at - keep_days * 24 * 3600 * 1000
    runs, freed = await in_thread(data.step_events_purge, older_than, keep_bytes, iso_now())
    if runs and journal is not None:
        await in_thread(
            journal.append,
            {"kind": "events-purge", "workspace": "", "runs": runs, "bytes": freed},
        )
    return runs, freed


def purge_on_start(config: Any) -> tuple[int, int]:
    """What `coscc/run.py` calls before the server is built: a `Data` and a `Journal` built from
    `config` the way `Core` builds them, nothing else of the app.
    """
    data = Data(config.data_dir)
    journal = Journal(config.working_dir, data) if config.working_dir else None
    return asyncio.run(purge(data, journal))
