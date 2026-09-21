"""Everything about a step that is not the artifact.

`spec.md` draws the line this module sits on: what a stage *says* lives in its artifact, on
disk, in git, in one copy. Who ran it, when, in which mode, what it cost and how often it
was told no — none of that is an artifact, none of it belongs in a file someone reads to
understand the work, and committing a token count on every event would make the history
useless. So it comes here instead.

**The status of a stage is never read from this file.** It is always read from the
artifact's `Status:` line via `board.py`. That is what keeps the journal unable to lie
about progress: it can only say who was there and what it cost.

**Append-only, and that is a safety property rather than a style.** `0005` measured four
processes losing 12 of 20 workspace entries to interleaved read-modify-write
(`cos_baodo/store.py:16-18`). An append has no read step, so that entire class of loss is
structurally absent here rather than defended against. The `flock` is still taken — it
frames a record so two writers cannot interleave halves of a line, and it gives a reader a
consistent snapshot — but it is the second line of defence, not the first.

Records are stamped and never edited. A mode change is a new record; the latest one wins.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

VERSION = 1
JOURNAL_FILENAME = ".cos-journal.jsonl"
LOCK_FILENAME = ".cos-journal.lock"

# Same reasoning, and the same numbers, as `store.LOCK_TIMEOUT`: the work under the lock is
# one line of a few hundred bytes, so ten seconds is enormous. It turns an indefinite hang
# into an error that names the folder. Chosen, not measured.
LOCK_TIMEOUT = 10.0
LOCK_POLL = 0.01

MODES = ("manual", "autonomous")

# How a run ended. `cancelled` and `exhausted` exist so that "no end record" can keep
# meaning the one thing it should: the app stopped while the step was still running.
OUTCOMES = ("done", "failed", "exhausted", "cancelled")

# The fields a caller may report about what a turn cost. Anything else in a record is
# carried through untouched; these are the ones `totals` knows how to add up.
COST_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "turns",
    "duration_ms",
)

# Money is the one field that is not a whole number. A turn can cost less than a cent, so
# truncating it to an integer would report most of them as free.
COST_USD = "cost_usd"
USD_PLACES = 6


def _add_cost(into: dict[str, Any], values: dict[str, Any]) -> None:
    """Add one cost record into a running total, keeping USD a float."""
    for field_name in COST_FIELDS:
        into[field_name] = int(into.get(field_name, 0)) + int(values.get(field_name) or 0)
    into[COST_USD] = round(
        float(into.get(COST_USD, 0.0)) + float(values.get(COST_USD) or 0.0), USD_PLACES
    )


def _zero_cost() -> dict[str, Any]:
    out: dict[str, Any] = {name: 0 for name in COST_FIELDS}
    out[COST_USD] = 0.0
    return out


class Busy(RuntimeError):
    """Another process held the journal for too long. Raised rather than waited out."""


class BadRecord(ValueError):
    """A record this module will not store, carrying a reason a caller can show."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Journal:
    """The run log for one working folder, covering every workspace under it.

    It lives beside the workspace store rather than inside any repository: a workspace is
    somebody's git checkout, and dropping a growing log into it would show up in their
    `git status` forever.
    """

    def __init__(self, working_dir: str | os.PathLike[str]):
        self.working_dir = Path(working_dir).expanduser().resolve()
        self.path = self.working_dir / JOURNAL_FILENAME
        self.lock_path = self.working_dir / LOCK_FILENAME
        self._lock = threading.Lock()

    # -- locking ------------------------------------------------------------

    @contextmanager
    def transaction(self, timeout: float | None = None):
        """Hold the journal exclusively. Lifted from `store.Store.transaction`.

        The lock lives on its own file for the same reason it does there: `flock` is
        released by the kernel when the holder dies, so a crash cannot wedge the folder.
        """
        timeout = LOCK_TIMEOUT if timeout is None else timeout
        self.working_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            try:
                deadline = time.monotonic() + timeout
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError as e:
                        if e.errno not in (errno.EACCES, errno.EAGAIN):
                            raise
                        if time.monotonic() >= deadline:
                            raise Busy(
                                f"another process is holding the journal in "
                                f"{self.working_dir} (waited {timeout:.0f}s) — try again"
                            ) from e
                        time.sleep(LOCK_POLL)
                try:
                    yield
                finally:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    # -- writing ------------------------------------------------------------

    def append(self, record: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        """Stamp one record and add it to the end. Never rewrites what is already there."""
        if not isinstance(record, dict):
            raise BadRecord("a journal record must be a dict")
        kind = str(record.get("kind") or "")
        if not kind:
            raise BadRecord("a journal record needs a 'kind'")

        stamped = {"v": VERSION, "at": _now(), **record}
        try:
            line = json.dumps(stamped, ensure_ascii=False, sort_keys=False)
        except (TypeError, ValueError) as e:
            raise BadRecord(f"record is not JSON-serialisable: {e}") from e
        if "\n" in line:
            raise BadRecord("a record may not contain a newline")

        with self.transaction(timeout):
            # O_APPEND puts the write at the end as one operation, so the lock is framing
            # rather than the thing that keeps two writers apart.
            fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
            try:
                os.write(fd, (line + "\n").encode("utf-8"))
            finally:
                os.close(fd)
        return stamped

    def set_mode(self, workspace: str, unit: str, stage: str, mode: str) -> dict[str, Any]:
        """Record which way a step should run. The latest record for a step wins."""
        if mode not in MODES:
            raise BadRecord(f"mode must be one of {', '.join(MODES)}, got {mode!r}")
        return self.append(
            {"kind": "mode", "workspace": workspace, "unit": unit, "stage": stage, "mode": mode}
        )

    def started(self, workspace: str, unit: str, stage: str, mode: str, **extra: Any) -> dict[str, Any]:
        if mode not in MODES:
            raise BadRecord(f"mode must be one of {', '.join(MODES)}, got {mode!r}")
        return self.append(
            {"kind": "start", "workspace": workspace, "unit": unit, "stage": stage, "mode": mode, **extra}
        )

    def finished(
        self, workspace: str, unit: str, stage: str, outcome: str, **extra: Any
    ) -> dict[str, Any]:
        if outcome not in OUTCOMES:
            raise BadRecord(f"outcome must be one of {', '.join(OUTCOMES)}, got {outcome!r}")
        return self.append(
            {"kind": "end", "workspace": workspace, "unit": unit, "stage": stage, "outcome": outcome, **extra}
        )

    # -- reading ------------------------------------------------------------

    def records(
        self, workspace: str | None = None, unit: str | None = None, timeout: float | None = None
    ) -> list[dict[str, Any]]:
        """Every record, oldest first, optionally narrowed to one workspace or unit.

        A line that will not parse is skipped rather than repaired. The file is plain text
        beside a user's own folder, and rewriting it to fix someone else's edit would lose
        whatever they meant by it.
        """
        if not self.path.exists():
            return []
        with self.transaction(timeout):
            raw = self.path.read_text(encoding="utf-8", errors="replace")

        out: list[dict[str, Any]] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(item, dict):
                continue
            if workspace is not None and item.get("workspace") != workspace:
                continue
            if unit is not None and item.get("unit") != unit:
                continue
            out.append(item)
        return out

    def modes(self, workspace: str, timeout: float | None = None) -> dict[tuple[str, str], str]:
        """Current mode of every step that has ever had one set. Latest record wins."""
        found: dict[tuple[str, str], str] = {}
        for item in self.records(workspace, timeout=timeout):
            if item.get("kind") != "mode":
                continue
            mode = item.get("mode")
            if mode in MODES:
                found[(str(item.get("unit")), str(item.get("stage")))] = mode
        return found

    def timeline(self, workspace: str, unit: str, timeout: float | None = None) -> list[dict[str, Any]]:
        """One row per run of a step, oldest first (`spec.md` R15).

        A `start` with no `end` is a run that is still going — or one the app was killed
        during. Both look the same from here, and the row says so by leaving `ended` unset
        rather than guessing.
        """
        rows: list[dict[str, Any]] = []
        open_runs: dict[str, dict[str, Any]] = {}
        for item in self.records(workspace, unit, timeout=timeout):
            kind = item.get("kind")
            stage = str(item.get("stage") or "")
            if kind == "start":
                row = {
                    "stage": stage,
                    "mode": item.get("mode"),
                    "started": item.get("at"),
                    "ended": None,
                    "outcome": None,
                    "session_id": item.get("session_id"),
                    "artifact": None,
                    "cost": {},
                    "denials": 0,
                }
                rows.append(row)
                open_runs[stage] = row
            elif kind == "end":
                row = open_runs.pop(stage, None)
                if row is None:
                    # An end with no start: keep it rather than drop it, so a half-written
                    # history still shows that something happened.
                    row = {"stage": stage, "mode": item.get("mode"), "started": None}
                    rows.append(row)
                row["ended"] = item.get("at")
                row["outcome"] = item.get("outcome")
                row["artifact"] = item.get("artifact")
                row["denials"] = int(item.get("denials") or 0)
                if item.get("session_id"):
                    row["session_id"] = item.get("session_id")
                cost = _zero_cost()
                _add_cost(cost, item)
                row["cost"] = cost
        return rows

    def totals(self, workspace: str, unit: str, timeout: float | None = None) -> dict[str, Any]:
        """What one unit has cost, added up from its steps (`spec.md` R17).

        Added rather than stored. A stored total is a second number that can disagree with
        the first, and the point of the requirement is that it cannot.
        """
        per_stage: dict[str, dict[str, Any]] = {}
        for row in self.timeline(workspace, unit, timeout=timeout):
            stage = row.get("stage") or ""
            _add_cost(per_stage.setdefault(stage, _zero_cost()), row.get("cost") or {})

        total = _zero_cost()
        for bucket in per_stage.values():
            _add_cost(total, bucket)
        return {"per_stage": per_stage, "total": total}


def totals_of(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Add the cost of some timeline rows. Exposed so a caller can total a subset."""
    out = _zero_cost()
    for row in rows:
        _add_cost(out, row.get("cost") or {})
    return out
