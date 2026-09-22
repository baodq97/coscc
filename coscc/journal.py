"""Everything about a step that is not the artifact.

`spec.md` draws the line this module sits on: what a stage *says* lives in its artifact, on
disk, in git, in one copy. Who ran it, when, in which mode, what it cost and how often it
was told no — none of that is an artifact, none of it belongs in a file someone reads to
understand the work, and committing a token count on every event would make the history
useless. So it comes here instead.

**The status of a stage is never read from this file.** It is always read from the
artifact's `Status:` line via `board.py`. That is what keeps the journal unable to lie
about progress: it can only say who was there and what it cost.

**Append-only, and that is a safety property rather than a style.** Four processes adding
five workspace entries each to one working folder left 8 of the 20 behind, with no error
anywhere (`.cos/0004_silent-concurrent-loss/plan.md:115`) — the loss was interleaved
read-modify-write, not colliding writes. An append has
no read step, so that entire class of loss is structurally absent here rather than defended
against. The transaction is still taken — it frames a record so a reader gets a consistent
snapshot — but it is the second line of defence, not the first.

Records are stamped and never edited. A mode change is a new record; the latest one wins.

**Where it lives now.** It was a JSONL file beside the workspace store, one line
per record, `O_APPEND` under a `flock`. It is now rows in the app's SQLite database under
the data root. The record itself is still stored whole, as the JSON the caller composed —
the columns beside it (`root`, `workspace`, `unit`, `stage`, `kind`) are read out of that
JSON at insert time so a query can narrow without parsing every row. They are a second
copy, so they are never written independently of it; the JSON is the record.

A JSONL journal written before the move to SQLite is imported once, on first use, and the
file is not deleted. That mirrors the workspace store, for the same reason: an import that turns
out wrong is recoverable only while its source still exists.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from coscc.data import BUSY_TIMEOUT, Busy, Data, now as _now

VERSION = 1

# The file this used to be. Named here only so `_import_legacy` can read it once
# (`spec.md` R8, extended to the journal). Nothing writes it any more.
JOURNAL_FILENAME = ".cos-journal.jsonl"

# Same reasoning, and the same number, as `store.LOCK_TIMEOUT`: ten seconds turns an
# indefinite block into an error that names the file. Chosen, not measured.
LOCK_TIMEOUT = BUSY_TIMEOUT

MODES = ("manual", "autonomous")

# How a run ended. `cancelled` and `exhausted` exist so that "no end record" can keep
# meaning the one thing it should: the app stopped while the step was still running.
OUTCOMES = ("done", "failed", "exhausted", "cancelled")

# The fields a caller may report about what a turn cost. Anything else in a record is
# carried through untouched; these are the ones `totals` knows how to add up.
# The four a turn is billed for. Named apart from the other two because a cost display
# sums exactly these — cache reads and writes included, or a cache-heavy session reads as
# nearly free — and `state._tokens` should not retype them.
TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
)

COST_FIELDS = TOKEN_FIELDS + ("turns", "duration_ms")

# Money is the one field that is not a whole number. A turn can cost less than a cent, so
# truncating it to an integer would report most of them as free.
COST_USD = "cost_usd"
USD_PLACES = 6


def add_cost(into: dict[str, Any], values: dict[str, Any]) -> None:
    """Add one cost record into a running total, keeping USD a float."""
    for field_name in COST_FIELDS:
        into[field_name] = int(into.get(field_name, 0)) + int(values.get(field_name) or 0)
    into[COST_USD] = round(
        float(into.get(COST_USD, 0.0)) + float(values.get(COST_USD) or 0.0), USD_PLACES
    )


def zero_cost() -> dict[str, Any]:
    out: dict[str, Any] = {name: 0 for name in COST_FIELDS}
    out[COST_USD] = 0.0
    return out


class BadRecord(ValueError):
    """A record this module will not store, carrying a reason a caller can show."""


class Journal:
    """The run log for one working folder, covering every workspace under it.

    It lives in the app's data root rather than inside any repository: a workspace is
    somebody's git checkout, and dropping a growing log into it would show up in their
    `git status` forever. Before the data root existed, "not inside a repository" meant
    the working folder; now it means `~/.cos`, which is also true when there is no
    working folder at all.

    `data` is passed in for the same reason it is on `Store`: a test that forgets it would
    write to the real `~/.cos`.
    """

    def __init__(
        self,
        working_dir: str | os.PathLike[str],
        data: Data | str | os.PathLike[str] | None = None,
    ):
        self.working_dir = Path(working_dir).expanduser().resolve()
        self.data = data if isinstance(data, Data) else Data(data)
        self.legacy_path = self.working_dir / JOURNAL_FILENAME
        self._root = str(self.working_dir)
        self._imported = False

    # -- locking ------------------------------------------------------------

    @contextmanager
    def transaction(self, timeout: float | None = None):
        """Hold the journal exclusively. Delegates to `Data.write`.

        Kept as a method because `store.Store` has one and callers frame work with it the
        same way, and because the timeout has to be a value a test can shorten.
        """
        with self.data.write(timeout=LOCK_TIMEOUT if timeout is None else timeout) as conn:
            self._import_legacy(conn)
            yield conn

    # -- migration ----------------------------------------------------------

    def _migration_key(self) -> str:
        return f"import-jsonl:{self._root}"

    def _import_legacy(self, conn) -> None:
        """Bring a pre-SQLite JSONL log in, once, for this working folder.

        `Data.import_once` owns the guard, the mark and the not-deleting, exactly as it
        does for `store.Store`. This supplies only the parse: lines that will not parse are
        skipped rather than repaired, as `records` used to skip them on every read.
        """
        if self._imported:
            return
        self.data.import_once(conn, self._migration_key(), self.legacy_path, self._load_legacy)
        self._imported = True

    def _load_legacy(self, conn) -> None:
        try:
            raw = self.legacy_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            raw = ""
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(item, dict) and item.get("kind"):
                self._insert(conn, item)

    def _needs_import(self) -> bool:
        return self.legacy_path.is_file() and not self._imported

    # -- writing ------------------------------------------------------------

    def _insert(self, conn, record: dict[str, Any]) -> None:
        """One row. The columns are read out of the record, never supplied beside it."""
        conn.execute(
            "INSERT INTO runs (at, root, workspace, unit, stage, kind, record) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(record.get("at") or ""),
                self._root,
                str(record.get("workspace") or ""),
                str(record.get("unit") or ""),
                str(record.get("stage") or ""),
                str(record.get("kind") or ""),
                json.dumps(record, ensure_ascii=False, sort_keys=False),
            ),
        )

    def append(self, record: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        """Stamp one record and add it to the end. Never rewrites what is already there."""
        if not isinstance(record, dict):
            raise BadRecord("a journal record must be a dict")
        kind = str(record.get("kind") or "")
        if not kind:
            raise BadRecord("a journal record needs a 'kind'")

        stamped = {"v": VERSION, "at": _now(), **record}
        try:
            # Serialised here rather than at insert time so an unstorable record is
            # refused before anything is written, as it was when this was a text file.
            json.dumps(stamped, ensure_ascii=False, sort_keys=False)
        except (TypeError, ValueError) as e:
            raise BadRecord(f"record is not JSON-serialisable: {e}") from e

        with self.transaction(timeout) as conn:
            self._insert(conn, stamped)
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
        self,
        workspace: str | None = None,
        unit: str | None = None,
        timeout: float | None = None,
        kind: str | None = None,
    ) -> list[dict[str, Any]]:
        """Every record for this working folder, oldest first, optionally narrowed.

        A row whose JSON will not parse is skipped rather than repaired. The database is
        editable by hand like the file before it, and rewriting somebody's edit would lose
        whatever they meant by it.

        Ordered by `id`. `at` is only second-resolution, so two records written in the same
        second would have no order at all if it were the key.
        """
        if self._needs_import():
            with self.transaction(timeout):
                pass

        sql = "SELECT record FROM runs WHERE root = ?"
        args: list[Any] = [self._root]
        if workspace is not None:
            sql += " AND workspace = ?"
            args.append(workspace)
        if unit is not None:
            sql += " AND unit = ?"
            args.append(unit)
        if kind is not None:
            sql += " AND kind = ?"
            args.append(kind)
        sql += " ORDER BY id"

        with self.data.connect(timeout=LOCK_TIMEOUT if timeout is None else timeout) as conn:
            rows = conn.execute(sql, args).fetchall()

        out: list[dict[str, Any]] = []
        for row in rows:
            try:
                item = json.loads(row["record"])
            except (json.JSONDecodeError, ValueError, TypeError):
                continue
            if isinstance(item, dict):
                out.append(item)
        return out

    def modes(self, workspace: str, timeout: float | None = None) -> dict[tuple[str, str], str]:
        """Current mode of every step that has ever had one set. Latest record wins.

        Narrowed in SQL: the `kind` column exists so this does not fetch and parse every
        start, end and denial in the working folder to keep the handful that are modes.
        """
        found: dict[tuple[str, str], str] = {}
        for item in self.records(workspace, timeout=timeout, kind="mode"):
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
        return _fold(self.records(workspace, unit, timeout=timeout))

    def timelines(
        self, workspace: str, timeout: float | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        """`timeline` for every unit at once, from a single read.

        The board needs one of these per unit. Asking `timeline` for each would open a
        connection and re-scan the working folder per unit — the same rows, N times.
        """
        by_unit: dict[str, list[dict[str, Any]]] = {}
        for item in self.records(workspace, timeout=timeout):
            by_unit.setdefault(str(item.get("unit") or ""), []).append(item)
        return {unit: _fold(items) for unit, items in by_unit.items()}
    def totals(self, workspace: str, unit: str, timeout: float | None = None) -> dict[str, Any]:
        """What one unit has cost, added up from its steps (`spec.md` R17).

        Added rather than stored. A stored total is a second number that can disagree with
        the first, and the point of the requirement is that it cannot.
        """
        per_stage: dict[str, dict[str, Any]] = {}
        for row in self.timeline(workspace, unit, timeout=timeout):
            stage = row.get("stage") or ""
            add_cost(per_stage.setdefault(stage, zero_cost()), row.get("cost") or {})

        total = zero_cost()
        for bucket in per_stage.values():
            add_cost(total, bucket)
        return {"per_stage": per_stage, "total": total}


def _fold(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Records in order, folded into one row per run. Shared by `timeline`/`timelines`."""
    rows: list[dict[str, Any]] = []
    open_runs: dict[str, dict[str, Any]] = {}
    for item in items:
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
                "detail": None,
            }
            rows.append(row)
            open_runs[stage] = row
        elif kind == "end":
            row = open_runs.pop(stage, None)
            if row is None:
                # An end with no start: keep it rather than drop it, so a half-written
                # history still shows that something happened.
                row = {"stage": stage, "mode": item.get("mode"), "started": None, "detail": None}
                rows.append(row)
            row["ended"] = item.get("at")
            row["outcome"] = item.get("outcome")
            row["artifact"] = item.get("artifact")
            row["denials"] = int(item.get("denials") or 0)
            # Why it ended this way, carried through to the row the page reads. `finished`
            # has stored this since `0005` and `_fold` dropped it, so every failure arrived
            # at the board as an outcome with no reason -- and `0014` found out the
            # expensive way, when a paid `spec` step failed inside a proof run and the only
            # account of it was the word "failed".
            row["detail"] = item.get("detail")
            if item.get("session_id"):
                row["session_id"] = item.get("session_id")
            cost = zero_cost()
            add_cost(cost, item)
            row["cost"] = cost
    return rows


def totals_of(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Add the cost of some timeline rows. Exposed so a caller can total a subset."""
    out = zero_cost()
    for row in rows:
        add_cost(out, row.get("cost") or {})
    return out
