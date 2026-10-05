"""Everything about a step that is not the artifact: who ran it, when, in which mode, what it
cost and how often it was told no.

The status of a stage is never read from here; it comes from the artifact's `Status:` line via
`board.py`, so the journal can say only who was there and what it cost.

Append-only, as a safety property: concurrent read-modify-write of a list loses entries
silently, and an append has no read step. The transaction still frames a record so a reader
gets a consistent snapshot, but it is the second line of defence.

Records are stamped and never edited; a mode change is a new record and the latest wins. The
one exception is `set_trial_model`, which reads and writes one `start` field inside one
exclusive transaction.

Rows live in the app's SQLite database. The record is stored whole as the JSON the caller
composed; the columns beside it (`root`, `workspace`, `unit`, `stage`, `kind`) are read out of
that JSON at insert time so a query can narrow without parsing every row, and are never
written independently. A legacy JSONL journal is imported once, on first use, and not deleted.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, NamedTuple, get_args

from coscc.store.db import BUSY_TIMEOUT, Data, now as _now

log = logging.getLogger(__name__)

VERSION = 1

# The old JSONL file. Named here only so `_import_legacy` can read it once.
JOURNAL_FILENAME = ".cos-journal.jsonl"

# Same as `store.LOCK_TIMEOUT`: turns an indefinite block into an error. Chosen, not measured.
LOCK_TIMEOUT = BUSY_TIMEOUT

MODES = ("manual", "autonomous")

# How a run ended. `cancelled` and `exhausted` exist so that "no end record" keeps meaning one
# thing: the app stopped while the step was still running. `stopped` is a person pressing Stop
# and carries `stopped_by`, the name they typed. `cancelled` is written by nothing.
Outcome = Literal["done", "failed", "exhausted", "cancelled", "stopped"]
OUTCOMES: tuple[Outcome, ...] = get_args(Outcome)

# The fields a caller may report about what a turn cost. Anything else in a record is carried
# through untouched; these are the ones `totals` adds up.
# The four a turn is billed for, cache reads and writes included (or a cache-heavy session reads
# as nearly free); `state._tokens` should not retype them.
TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
)

COST_FIELDS = TOKEN_FIELDS + ("turns", "duration_ms")

# Money is not a whole number: a turn can cost less than a cent, and truncating would report
# most as free.
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


class Intervention(NamedTuple):
    """One time a person had to step in, as the owner of the table that saw it reads it.
    `id` is `<kind>:<table>:<row id>`, the same on every read; `detail` is the row's own words,
    uncut (`coscc/service/interventions.py` cuts it)."""

    id: str
    kind: str
    at: str
    unit: str
    stage: str
    detail: str


# A row whose JSON will not parse is read as having no field at all, never as an error.
_RERUN = "CASE WHEN json_valid(record) THEN json_extract(record, '$.rerun') END = 1"


class Bell:
    """Rung after every append this process commits, from any thread.

    A reader `arm`s a ticket *before* it reads, then `wait`s on it, so a ring between its read and
    its wait is not missed. A ring never fails the append that rang it: a ticket whose loop has
    closed is dropped. Another process's appends ring nothing here; a reader sees those only when
    its wait times out.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tickets: set[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = set()

    def arm(self) -> tuple[asyncio.AbstractEventLoop, asyncio.Event]:
        ticket = (asyncio.get_running_loop(), asyncio.Event())
        with self._lock:
            self._tickets.add(ticket)
        return ticket

    def disarm(self, ticket: tuple[asyncio.AbstractEventLoop, asyncio.Event]) -> None:
        with self._lock:
            self._tickets.discard(ticket)

    def ring(self) -> None:
        with self._lock:
            tickets = list(self._tickets)
        for ticket in tickets:
            loop, event = ticket
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                self.disarm(ticket)

    async def wait(
        self, ticket: tuple[asyncio.AbstractEventLoop, asyncio.Event], timeout: float
    ) -> bool:
        """True when rung, False when `timeout` seconds passed first. Disarms either way."""
        try:
            await asyncio.wait_for(ticket[1].wait(), max(0.0, timeout))
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            self.disarm(ticket)

    def __len__(self) -> int:
        with self._lock:
            return len(self._tickets)


BELL = Bell()


class Journal:
    """The run log for one working folder, covering every workspace under it.

    It lives in the app's data root, not inside any repository, so a growing log never shows up in
    a checkout's `git status`.

    `data` is passed in so a test that forgets it cannot write to the real `~/.cos`.
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

    @contextmanager
    def transaction(self, timeout: float | None = None):
        """Hold the journal exclusively. Delegates to `Data.write`; a method so callers frame work as
        with `store.Store`, and so a test can shorten the timeout.
        """
        with self.data.write(timeout=LOCK_TIMEOUT if timeout is None else timeout) as conn:
            self._import_legacy(conn)
            yield conn

    def _migration_key(self) -> str:
        return f"import-jsonl:{self._root}"

    def _import_legacy(self, conn) -> None:
        """Bring a pre-SQLite JSONL log in, once, for this working folder.

        `Data.import_once` owns the guard, the mark and the not-deleting. This supplies only the
        parse: lines that will not parse are skipped rather than repaired.
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
            except json.JSONDecodeError, ValueError:
                continue
            if isinstance(item, dict) and item.get("kind"):
                self._insert(conn, item)

    def _needs_import(self) -> bool:
        return self.legacy_path.is_file() and not self._imported

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
            # Serialised here so an unstorable record is refused before anything is written.
            json.dumps(stamped, ensure_ascii=False, sort_keys=False)
        except (TypeError, ValueError) as e:
            raise BadRecord(f"record is not JSON-serialisable: {e}") from e

        with self.transaction(timeout) as conn:
            self._insert(conn, stamped)
        # After the commit, never inside it: a reader woken here finds the row.
        BELL.ring()
        return stamped

    def append_with(
        self,
        records: list[dict[str, Any]],
        also: Callable[[Any], None],
        timeout: float | None = None,
    ) -> list[dict[str, Any]]:
        """`append` of every record, with `also(conn)` written in the same transaction: a hold's row in
        `unit_holds` and its `hold` record here, or each answer's row in `unit_answers` and its
        `answer` record, are all written or none is.
        """
        stamped = []
        for record in records:
            if not isinstance(record, dict) or not record.get("kind"):
                raise BadRecord("a journal record needs a 'kind'")
            one = {"v": VERSION, "at": _now(), **record}
            try:
                json.dumps(one, ensure_ascii=False, sort_keys=False)
            except (TypeError, ValueError) as e:
                raise BadRecord(f"record is not JSON-serialisable: {e}") from e
            stamped.append(one)
        with self.transaction(timeout) as conn:
            also(conn)
            for one in stamped:
                self._insert(conn, one)
        BELL.ring()
        return stamped

    def set_mode(self, workspace: str, unit: str, stage: str, mode: str) -> dict[str, Any]:
        """Record which way a step should run. The latest record for a step wins."""
        if mode not in MODES:
            raise BadRecord(f"mode must be one of {', '.join(MODES)}, got {mode!r}")
        return self.append(
            {"kind": "mode", "workspace": workspace, "unit": unit, "stage": stage, "mode": mode}
        )

    def started(
        self, workspace: str, unit: str, stage: str, mode: str, **extra: Any
    ) -> dict[str, Any]:
        if mode not in MODES:
            raise BadRecord(f"mode must be one of {', '.join(MODES)}, got {mode!r}")
        return self.append(
            {
                "kind": "start",
                "workspace": workspace,
                "unit": unit,
                "stage": stage,
                "mode": mode,
                **extra,
            }
        )

    def set_trial_model(
        self,
        workspace: str,
        unit: str,
        stage: str,
        at: str,
        model: str,
        timeout: float | None = None,
    ) -> bool:
        """The one field of a written `start` this log ever fills in afterwards: `model_trial.model`,
        the model the session's `init` named, which the `start` could not know when it was written.

        Only one row is touched: the latest `start` of `workspace`, `unit` and `stage` stamped at `at`
        (the `at` of the record `started` returned), and only while it carries a `model_trial` with no
        `model` yet. Whether a row was written is returned.
        """
        with self.transaction(timeout) as conn:
            row = conn.execute(
                "SELECT id, record FROM runs WHERE root = ? AND workspace = ? AND unit = ? AND stage = ? "
                "AND kind = 'start' AND json_extract(record, '$.at') = ? ORDER BY id DESC LIMIT 1",
                (self._root, workspace, unit, stage, at),
            ).fetchone()
            if row is None:
                return False
            try:
                record = json.loads(row[1])
            except json.JSONDecodeError, ValueError:
                return False
            trial = record.get("model_trial")
            if not isinstance(trial, dict) or trial.get("model"):
                return False
            record["model_trial"] = {**trial, "model": str(model)}
            conn.execute(
                "UPDATE runs SET record = ? WHERE id = ?",
                (json.dumps(record, ensure_ascii=False, sort_keys=False), row[0]),
            )
        BELL.ring()
        return True

    def finished(
        self, workspace: str, unit: str, stage: str, outcome: Outcome, **extra: Any
    ) -> dict[str, Any]:
        if outcome not in OUTCOMES:
            raise BadRecord(f"outcome must be one of {', '.join(OUTCOMES)}, got {outcome!r}")
        return self.append(
            {
                "kind": "end",
                "workspace": workspace,
                "unit": unit,
                "stage": stage,
                "outcome": outcome,
                **extra,
            }
        )

    def attempted(self, workspace: str, unit: str, stage: str, **extra: Any) -> dict[str, Any]:
        """What a stopped step left behind, written just before its `end` record.

        Never read for a stage's status; `board.py` reads only `Status:` in the artifact. Only read
        back by `failed_attempts`, to build the next run's prompt.
        """
        return self.append(
            {"kind": "attempt", "workspace": workspace, "unit": unit, "stage": stage, **extra}
        )

    def suspended(self, workspace: str, unit: str, stage: str, **fields: Any) -> dict[str, Any]:
        """One session an update paused, with a `suspend_id` of its own. Written between a step's
        `start` and its `end`, and closing neither.
        """
        return self.append(
            {
                "kind": "suspend",
                "workspace": workspace,
                "unit": unit,
                "stage": stage,
                "suspend_id": uuid.uuid4().hex,
                **fields,
            }
        )

    def resumed(
        self, workspace: str, unit: str, stage: str, suspend_id: str, **fields: Any
    ) -> dict[str, Any]:
        """The next start took `suspend_id` up; written before it runs anything, so a start after this
        one never takes it up again.
        """
        return self.append(
            {
                "kind": "resume",
                "workspace": workspace,
                "unit": unit,
                "stage": stage,
                "suspend_id": suspend_id,
                **fields,
            }
        )

    def unresumed(self, timeout: float | None = None) -> list[dict[str, Any]]:
        """Every `suspend` row, in every workspace, with no `resume` naming it."""
        rows = self.records(timeout=timeout, kinds=("suspend", "resume"))
        taken = {r.get("suspend_id") for r in rows if r.get("kind") == "resume"}
        return [r for r in rows if r.get("kind") == "suspend" and r.get("suspend_id") not in taken]

    def records(
        self,
        workspace: str | None = None,
        unit: str | None = None,
        timeout: float | None = None,
        kind: str | None = None,
        kinds: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Every record for this working folder, oldest first, optionally narrowed.

        A row whose JSON will not parse is skipped rather than repaired (the database is editable by
        hand). Ordered by `id`: `at` is only second-resolution.
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
        if kinds is not None:
            wanted = list(kinds)
            sql += f" AND kind IN ({', '.join('?' for _ in wanted)})" if wanted else " AND 0"
            args.extend(wanted)
        sql += " ORDER BY id"

        with self.data.connect(timeout=LOCK_TIMEOUT if timeout is None else timeout) as conn:
            rows = conn.execute(sql, args).fetchall()

        out: list[dict[str, Any]] = []
        for row in rows:
            try:
                item = json.loads(row["record"])
            except json.JSONDecodeError, ValueError, TypeError:
                continue
            if isinstance(item, dict):
                out.append(item)
        return out

    def last_id(self, timeout: float | None = None) -> int:
        """The largest `runs.id` in the database, 0 when there is none.

        Every root's, not this one's: ids are one sequence.
        """
        if self._needs_import():
            with self.transaction(timeout):
                pass
        with self.data.connect(timeout=LOCK_TIMEOUT if timeout is None else timeout) as conn:
            row = conn.execute("SELECT MAX(id) FROM runs").fetchone()
        return int(row[0] or 0)

    def notice_rows(
        self,
        after: int,
        kinds: Iterable[str],
        workspace: str | None = None,
        limit: int = 500,
        timeout: float | None = None,
    ) -> list[tuple[int, dict[str, Any]]]:
        """`(id, record)` for this root's rows of `kinds` past `after`, by `id`, at most `limit`,
        optionally in one workspace. A row whose JSON will not parse is skipped, as `records` skips
        it; its id is then never handed out.
        """
        if self._needs_import():
            with self.transaction(timeout):
                pass
        wanted = list(kinds)
        sql = "SELECT id, record FROM runs WHERE root = ? AND id > ?" + (
            f" AND kind IN ({', '.join('?' for _ in wanted)})" if wanted else " AND 0"
        )
        args: list[Any] = [self._root, int(after), *wanted]
        if workspace is not None:
            sql += " AND workspace = ?"
            args.append(workspace)
        sql += " ORDER BY id LIMIT ?"
        args.append(int(limit))
        with self.data.connect(timeout=LOCK_TIMEOUT if timeout is None else timeout) as conn:
            rows = conn.execute(sql, args).fetchall()
        out: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            try:
                item = json.loads(row["record"])
            except json.JSONDecodeError, ValueError, TypeError:
                continue
            if isinstance(item, dict):
                out.append((int(row["id"]), item))
        return out

    def interventions(
        self, workspace: str, after: str, limit: int, timeout: float | None = None
    ) -> list[Intervention]:
        """The runs a person started again (`start` with `rerun`, its note as the detail) and
        every integration (`integration`, its outcome and detail) of `workspace` whose `at` is
        past `after`, oldest first, at most `limit` of each."""
        if self._needs_import():
            with self.transaction(timeout):
                pass
        out: list[Intervention] = []
        with self.data.connect(timeout=LOCK_TIMEOUT if timeout is None else timeout) as conn:
            for kind, where in (
                ("rerun", f"kind = 'start' AND {_RERUN}"),
                ("integrate", "kind = 'integration'"),
            ):
                rows = conn.execute(
                    "SELECT id, at, unit, stage, record FROM runs WHERE root = ? AND workspace = ? "
                    f"AND at > ? AND {where} ORDER BY at, id LIMIT ?",
                    (self._root, workspace, after, int(limit)),
                ).fetchall()
                for row in rows:
                    try:
                        item = json.loads(row["record"])
                    except json.JSONDecodeError, ValueError, TypeError:
                        item = {}
                    item = item if isinstance(item, dict) else {}
                    if kind == "rerun":
                        detail = str(item.get("rerun_note") or "")
                    else:
                        said = str(item.get("detail") or "")
                        detail = str(item.get("outcome") or "") + (f": {said}" if said else "")
                    out.append(
                        Intervention(
                            f"{kind}:runs:{row['id']}",
                            kind,
                            row["at"],
                            row["unit"],
                            row["stage"],
                            detail,
                        )
                    )
        return out

    def modes(self, workspace: str, timeout: float | None = None) -> dict[tuple[str, str], str]:
        """Current mode of every step that has ever had one set. Latest record wins.

        Narrowed in SQL by the `kind` column rather than parsing every row.
        """
        found: dict[tuple[str, str], str] = {}
        for item in self.records(workspace, timeout=timeout, kind="mode"):
            mode = item.get("mode")
            if mode in MODES:
                found[(str(item.get("unit")), str(item.get("stage")))] = mode
        return found

    def timeline(
        self, workspace: str, unit: str, timeout: float | None = None
    ) -> list[dict[str, Any]]:
        """One row per run of a step, oldest first.

        A `start` with no `end` is a run still going or one the app was killed during; the row leaves
        `ended` unset rather than guessing.
        """
        return _fold(self.records(workspace, unit, timeout=timeout))

    def timelines(
        self, workspace: str, timeout: float | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        """`timeline` for every unit at once, from a single read, so the board does not open a
        connection and re-scan the folder per unit.
        """
        return timelines_of(self.records(workspace, timeout=timeout))

    def append_checked(
        self,
        record: dict[str, Any],
        kinds: Iterable[str],
        check: Any,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Read the records of `kinds`, let `check` refuse, and append, in one transaction.

        `check(rows)` raises `BadRecord` to refuse; nothing is written then. The read happens on the
        transaction's own connection after `BEGIN IMMEDIATE`, so two writers checking against each
        other's rows run one after the other.
        """
        if not isinstance(record, dict) or not record.get("kind"):
            raise BadRecord("a journal record needs a 'kind'")
        stamped = {"v": VERSION, "at": _now(), **record}
        try:
            json.dumps(stamped, ensure_ascii=False, sort_keys=False)
        except (TypeError, ValueError) as e:
            raise BadRecord(f"record is not JSON-serialisable: {e}") from e
        wanted = list(kinds)
        with self.transaction(timeout) as conn:
            sql = (
                "SELECT record FROM runs WHERE root = ? AND workspace = ?"
                + (f" AND kind IN ({', '.join('?' for _ in wanted)})" if wanted else " AND 0")
                + " ORDER BY id"
            )
            rows = []
            for row in conn.execute(
                sql, [self._root, str(record.get("workspace") or ""), *wanted]
            ).fetchall():
                try:
                    item = json.loads(row["record"])
                except json.JSONDecodeError, ValueError, TypeError:
                    continue
                if isinstance(item, dict):
                    rows.append(item)
            check(rows)
            self._insert(conn, stamped)
        BELL.ring()
        return stamped

    def open_starts(
        self, workspace: str, timeout: float | None = None
    ) -> dict[str, dict[str, Any]]:
        """Per unit, the runs with a `start` and no `end`, and the unit's last `start`.

        `{unit: {"open": [row...], "last_start": at}}`, only for units with an open row. A row is
        `_fold`'s, so this cannot disagree with `timeline` about what is open. Narrowed in SQL to the
        two kinds that decide it, since the board asks every few seconds. Writes nothing.
        """
        by_unit: dict[str, list[dict[str, Any]]] = {}
        for item in self.records(workspace, timeout=timeout, kinds=("start", "end")):
            by_unit.setdefault(str(item.get("unit") or ""), []).append(item)
        out: dict[str, dict[str, Any]] = {}
        for unit, items in by_unit.items():
            open_rows = [r for r in _fold(items) if r.get("ended") is None]
            if not open_rows:
                continue
            starts = [i.get("at") for i in items if i.get("kind") == "start"]
            out[unit] = {"open": open_rows, "last_start": starts[-1] if starts else None}
        return out

    def failed_attempts(
        self, workspace: str, unit: str, stage: str, timeout: float | None = None
    ) -> dict[str, Any] | None:
        """What the runs of `stage` before this one left behind, or `None` when there is nothing to
        tell: no run yet, or the most recent one is `done`.
        """
        seq = [
            r
            for r in self.records(workspace, unit, timeout=timeout)
            if str(r.get("stage") or "") == stage and r.get("kind") in ("end", "attempt")
        ]
        end_positions = [i for i, r in enumerate(seq) if r.get("kind") == "end"]
        if not end_positions:
            return None
        last = end_positions[-1]
        if seq[last].get("outcome") == "done":
            return None

        attempt = None
        for i in range(last - 1, -1, -1):
            # Only the attempt written by *this* run: an earlier run's `end` ends the search, so a run
            # whose capture failed is described as having none, not with the tree of a run before it.
            if seq[i].get("kind") == "end":
                break
            if seq[i].get("kind") == "attempt":
                attempt = seq[i]
                break

        def _brief(rec: dict[str, Any]) -> dict[str, Any]:
            return {
                "at": rec.get("at"),
                "outcome": rec.get("outcome"),
                "turns": rec.get("turns"),
                "cost_usd": rec.get("cost_usd"),
            }

        earlier: list[dict[str, Any]] = []
        for i in range(last - 1, -1, -1):
            r = seq[i]
            if r.get("kind") != "end":
                continue
            if r.get("outcome") == "done":
                break
            earlier.append(_brief(r))
        earlier.reverse()

        found = {"attempt": attempt, "latest": _brief(seq[last]), "earlier": earlier}
        latest = seq[last]
        # A review that ran out of turns and whose closing turn wrote nothing left no round; what it
        # had opened is kept in its events, never in `review.md`. A round the app wrote later leaves a
        # newer `end`, so this stops being the latest. `closing` says whether a closing turn ran at
        # all: none does with no session id or no head.
        if (
            stage == "review"
            and latest.get("outcome") == "exhausted"
            and latest.get("review_md") == "none"
            and latest.get("run")
        ):
            found["opened"] = {
                **self._opened(str(latest["run"]), timeout),
                "closing": "closing" in latest,
            }
        return found

    def _opened(self, run: str, timeout: float | None) -> dict[str, Any]:
        """The paths `run`'s `tool_use` events named, `{"purged": True}` when its events are gone, or
        `{"error": ...}`: a reason to show, never one to refuse the step for.
        """
        try:
            row = self.data.step_run(run)
            if row is None or row.get("purged_at"):
                return {"purged": True}
            paths: list[str] = []
            for event in self.data.step_tool_uses(run, timeout=timeout):
                given = event.get("input")
                if isinstance(given, str):
                    # `events._cut` keeps a long `input` as the start of its JSON.
                    try:
                        given = json.loads(given)
                    except ValueError:
                        continue
                if not isinstance(given, dict):
                    continue
                path = given.get("file_path") or given.get("path") or given.get("pattern")
                if isinstance(path, str) and path and path not in paths:
                    paths.append(path)
            return {"paths": paths}
        except Exception as e:
            # `Busy` included: the step still runs.
            log.exception("the paths a step touched could not be read")
            return {"error": f"{type(e).__name__}: {e}"}


def _fold(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Records in order, folded into one row per run. Shared by `timeline`/`timelines`."""
    rows: list[dict[str, Any]] = []
    open_runs: dict[str, dict[str, Any]] = {}
    # The same open rows by their `run`, so an `end` that names one closes that one: two runs of a
    # stage can be open at once, and the latest is not always the one that ended.
    open_by_run: dict[str, dict[str, Any]] = {}
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
                # Which model the step was started on, and why that one (`override`, `default` or
                # `COS_MODEL`). An older start has neither: None.
                "model": item.get("model"),
                "model_source": item.get("model_source"),
                # The id of the step's events, and how many never reached disk. A run not from the board has
                # neither: None.
                "run": item.get("run"),
                "events_lost": None,
                # What state opened an `integrate` session. Any other stage has none: None.
                "integrate_state": item.get("integrate_state"),
                # The agent's name when the step began. A session no agent row names has none: None.
                "agent": item.get("agent"),
            }
            rows.append(row)
            open_runs[stage] = row
            if row["run"]:
                open_by_run[str(row["run"])] = row
        elif kind == "end":
            row = open_by_run.pop(str(item.get("run") or ""), None) if item.get("run") else None
            if row is not None:
                if open_runs.get(stage) is row:
                    del open_runs[stage]
            else:
                # An `end` with no `run`, or one naming no open row: by stage.
                row = open_runs.pop(stage, None)
                if row is not None and row.get("run"):
                    open_by_run.pop(str(row["run"]), None)
            if row is None:
                # An end with no start: keep it, so a half-written history still shows that something happened.
                row = {"stage": stage, "mode": item.get("mode"), "started": None, "detail": None}
                rows.append(row)
            row["ended"] = item.get("at")
            row["outcome"] = item.get("outcome")
            row["artifact"] = item.get("artifact")
            row["denials"] = int(item.get("denials") or 0)
            # Why it ended this way, carried through to the row the page reads, so a failure reaches the
            # board with its reason.
            row["detail"] = item.get("detail")
            if item.get("session_id"):
                row["session_id"] = item.get("session_id")
            cost = zero_cost()
            add_cost(cost, item)
            row["cost"] = cost
            # Whether this `end` actually carried a cost, as opposed to one `add_cost` filled in as zero
            # because the session died before reporting any. Without it a step that failed before its first
            # billed turn reads as a run that cost nothing, not one nobody measured.
            row["reported"] = "cost_usd" in item
            # Whether it carried `turns`, known apart from its cost: a step that died after three turns
            # knows them, and not what they cost.
            row["turns_reported"] = "turns" in item
            if "events_lost" in item:
                row["events_lost"] = item.get("events_lost")
    return rows


def timelines_of(items: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """`Journal.timelines` over records already read, so the board reads the run log once."""
    by_unit: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_unit.setdefault(str(item.get("unit") or ""), []).append(item)
    return {unit: _fold(rows) for unit, rows in by_unit.items()}


def _cost_unknown(row: dict[str, Any]) -> bool:
    """A run that ended with no `cost_usd`: what it cost is not known, not zero."""
    return row.get("ended") is not None and not row.get("reported", True)


def totals_of(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Add the cost of some timeline rows. Exposed so a caller can total a subset.

    Only known costs are added; `unknown` counts the ended runs whose cost is not known, so the
    sum is never shown as the whole of it.
    """
    out = zero_cost()
    out["unknown"] = 0
    for row in rows:
        add_cost(out, row.get("cost") or {})
        out["unknown"] += int(_cost_unknown(row))
    return out


def last_runs(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """For each stage, its most recently *ended* timeline row, keyed by stage.

    A run still in progress (`ended` unset) is skipped. `rows` is `timeline`/`timelines`'s output,
    oldest first, so the last assignment to a stage is the most recent one.
    """
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("ended") is None:
            continue
        stage = str(row.get("stage") or "")
        cost = row.get("cost") or {}
        # Each known or not on its own: a step can report its turns and not what they cost.
        out[stage] = {
            "outcome": row.get("outcome"),
            "ended": row.get("ended"),
            "turns": cost.get("turns") if row.get("turns_reported", True) else None,
            "cost_usd": cost.get("cost_usd") if row.get("reported", True) else None,
        }
    return out
