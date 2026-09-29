"""What happened to a unit: the transitions, and the projection of where it is now.

The transition is the record: no column holds a current state, and `state()` is a fold over
the log. Every field is written on every row; a caller that does not know something writes
`UNKNOWN`, never a blank. A unit has a sequence of sessions (one per step), not a session;
`sessions_of()` keeps one multi-turn session as one row.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

from coscc.units import states
from coscc.data import BUSY_TIMEOUT, Data, now as _now
from coscc.units.states import Machine

# What a field says when nobody can say: never blank, never NULL, one word so a query can ask for it.
UNKNOWN = "unknown"

# A deliverable is the unit's own artifact; a code change is a file in somebody's repository.
# One table so "how many altogether" is one query.
DELIVERABLE = "deliverable"
CODE = "code"
KINDS = (DELIVERABLE, CODE)

LOCK_TIMEOUT = BUSY_TIMEOUT

# Whose decision a transition is. `person` and `delegated` are the originator and the one they
# delegated to; `agent` is what a model inferred and never counts as either; `code` is a guard
# reading git, `gh` or the database. An older row says `UNKNOWN`.
AUTHORITIES = ("person", "delegated", "agent", "code")

_TRANSITION_COLUMNS = (
    "at", "root", "workspace", "unit", "artifact", "stage",
    "from_state", "to_state", "actor", "session", "source", "machine", "once_key",
    "guard", "authority", "run", "inputs",
)

_OUTPUT_COLUMNS = (
    "at", "root", "workspace", "unit", "stage", "kind", "path",
    "actor", "session", "source", "once_key",
)


class BadTransition(ValueError):
    """A transition this module will not store, carrying a reason a caller can show."""


def _text(value: Any) -> str:
    """Anything into a non-empty string, `UNKNOWN` when there is nothing to say."""
    out = "" if value is None else str(value).strip()
    return out or UNKNOWN


class History:
    """The transition log and the file log for one working folder.

    `data` is passed in so a test that forgets it cannot write into the real `~/.cos`.
    `machine` is the state set every write is validated against and every row records.
    """

    def __init__(
        self,
        working_dir: str | os.PathLike[str],
        data: Data | str | os.PathLike[str] | None = None,
        machine: Machine | None = None,
    ):
        self.working_dir = Path(working_dir).expanduser().resolve()
        self.data = data if isinstance(data, Data) else Data(data)
        self.machine = machine or states.default()
        self._root = str(self.working_dir)


    def record(
        self,
        workspace: str,
        unit: str,
        artifact: str,
        to_state: str,
        *,
        from_state: str | None = None,
        actor: str = UNKNOWN,
        session: str = UNKNOWN,
        source: str = UNKNOWN,
        at: str | None = None,
        once_key: str = "",
        guard: str = UNKNOWN,
        authority: str = UNKNOWN,
        run: str = UNKNOWN,
        inputs: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Append one transition. Returns the row as it was stored.

        `from_state` is derived from the log unless given: only the row before it can say
        where an artifact was. The derive and the insert are one `BEGIN IMMEDIATE`
        transaction, since a read followed by a write derived from it loses concurrent writes.
        """
        rows = self.record_many(
            [
                {
                    "workspace": workspace,
                    "unit": unit,
                    "artifact": artifact,
                    "to_state": to_state,
                    "from_state": from_state,
                    "actor": actor,
                    "session": session,
                    "source": source,
                    "at": at,
                    "once_key": once_key,
                    "guard": guard,
                    "authority": authority,
                    "run": run,
                    "inputs": inputs,
                }
            ],
            timeout=timeout,
        )
        return rows[0]

    def record_many(
        self, items: Sequence[dict[str, Any]], timeout: float | None = None
    ) -> list[dict[str, Any]]:
        """Append transitions in order, in one transaction. Returns the stored rows.

        One transaction so an interruption never leaves half a unit's history. A row whose
        `once_key` is already present is skipped and not returned, so an import is re-runnable.
        """
        prepared = [self._validate(item) for item in items]
        with self.data.write(timeout=LOCK_TIMEOUT if timeout is None else timeout) as conn:
            return self._insert(conn, prepared)

    def record_in(self, conn: sqlite3.Connection, items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """`record_many` inside a transaction the caller already holds."""
        return self._insert(conn, [self._validate(item) for item in items])

    def _insert(self, conn: sqlite3.Connection, prepared: list[dict[str, Any]]) -> list[dict[str, Any]]:
        stored: list[dict[str, Any]] = []
        latest = self._latest(conn)
        for row in prepared:
            key = (row["workspace"], row["unit"], row["artifact"])
            if row["from_state"] is None:
                row["from_state"] = latest.get(key, self.machine.absent)
            complaint = self.machine.refuse(row["artifact"], row["from_state"])
            if complaint is not None:
                raise BadTransition(complaint)
            placeholders = ", ".join("?" for _ in _TRANSITION_COLUMNS)
            cursor = conn.execute(
                f"INSERT OR IGNORE INTO transitions ({', '.join(_TRANSITION_COLUMNS)}) "
                f"VALUES ({placeholders})",
                tuple(row[name] for name in _TRANSITION_COLUMNS),
            )
            if cursor.rowcount:
                latest[key] = row["to_state"]
                stored.append({**row, "id": cursor.lastrowid})
        return stored

    def _validate(self, item: dict[str, Any]) -> dict[str, Any]:
        """One row, checked against the state set and filled out.

        Done before the transaction opens so a bad row refuses the batch without holding the database.
        """
        artifact = str(item.get("artifact") or "").strip()
        to_state = str(item.get("to_state") or "").strip()
        unit = str(item.get("unit") or "").strip()
        if not unit:
            raise BadTransition("a transition needs a unit")
        complaint = self.machine.refuse(artifact, to_state)
        if complaint is not None:
            raise BadTransition(complaint)

        authority = _text(item.get("authority"))
        if authority not in (*AUTHORITIES, UNKNOWN):
            raise BadTransition(f"authority must be one of {', '.join(AUTHORITIES)}, got {authority!r}")
        inputs = item.get("inputs") or {}
        if not isinstance(inputs, dict):
            raise BadTransition("a transition's inputs are an object: SHA, revision, PR number")

        from_state = item.get("from_state")
        return {
            "at": str(item.get("at") or "").strip() or _now(),
            "root": self._root,
            "workspace": str(item.get("workspace") or ""),
            "unit": unit,
            "artifact": artifact,
            "stage": self._stage_of(artifact),
            "from_state": None if from_state is None else str(from_state),
            "to_state": to_state,
            "actor": _text(item.get("actor")),
            "session": _text(item.get("session")),
            "source": _text(item.get("source")),
            "machine": self.machine.name,
            # Not `_text`: an absent key means "do not deduplicate", and `unknown` would make keyless rows collide.
            "once_key": str(item.get("once_key") or ""),
            "guard": _text(item.get("guard")),
            "authority": authority,
            "run": _text(item.get("run")),
            "inputs": json.dumps(inputs, ensure_ascii=False, sort_keys=True),
        }

    def _stage_of(self, artifact: str) -> str:
        stage = self.machine.for_artifact(artifact)
        return stage.name if stage is not None else UNKNOWN

    def _latest(self, conn: sqlite3.Connection) -> dict[tuple[str, str, str], str]:
        """The newest state of every artifact this working folder has a transition for, in one query."""
        rows = conn.execute(
            "SELECT workspace, unit, artifact, to_state FROM transitions "
            "WHERE root = ? AND id IN ("
            "  SELECT MAX(id) FROM transitions WHERE root = ? "
            "  GROUP BY workspace, unit, artifact)",
            (self._root, self._root),
        ).fetchall()
        return {(r["workspace"], r["unit"], r["artifact"]): r["to_state"] for r in rows}


    def transitions(
        self,
        workspace: str | None = None,
        unit: str | None = None,
        artifact: str | None = None,
        timeout: float | None = None,
    ) -> list[dict[str, Any]]:
        """Every transition, oldest first, optionally narrowed.

        Ordered by `id`: `at` is second-resolution and would leave same-second rows unordered.
        """
        sql = f"SELECT id, {', '.join(_TRANSITION_COLUMNS)} FROM transitions WHERE root = ?"
        args: list[Any] = [self._root]
        for column, value in (("workspace", workspace), ("unit", unit), ("artifact", artifact)):
            if value is not None:
                sql += f" AND {column} = ?"
                args.append(value)
        sql += " ORDER BY id"
        with self.data.connect(timeout=LOCK_TIMEOUT if timeout is None else timeout) as conn:
            return [dict(row) for row in conn.execute(sql, args).fetchall()]

    def state(
        self, workspace: str, unit: str, timeout: float | None = None
    ) -> dict[str, str]:
        """Where each artifact of this unit stands now. **A fold, never a stored value.**

        Every artifact the state set knows appears; one with no transition reads as the
        absent state. Reported in stage order.
        """
        current = {artifact: self.machine.absent for artifact in self.machine.artifacts}
        for row in self.transitions(workspace, unit, timeout=timeout):
            current[row["artifact"]] = row["to_state"]
        return current

    def machines_in(
        self, workspace: str, unit: str | None = None, timeout: float | None = None
    ) -> list[str]:
        """Which state sets the stored rows were written under, first-seen first.

        Rows read under a set they were not written under compare states that never meant the
        same thing, and every query still returns rows; a caller that finds more than its own
        name here must say so.
        """
        sql = (
            "SELECT machine, MIN(id) AS first_seen FROM transitions "
            "WHERE root = ? AND workspace = ?"
        )
        args: list[Any] = [self._root, workspace]
        if unit is not None:
            sql += " AND unit = ?"
            args.append(unit)
        sql += " GROUP BY machine ORDER BY first_seen"
        with self.data.connect(timeout=LOCK_TIMEOUT if timeout is None else timeout) as conn:
            return [row["machine"] for row in conn.execute(sql, args).fetchall()]

    def units(self, workspace: str, timeout: float | None = None) -> list[str]:
        """Every unit this working folder has any transition for, first-seen first."""
        seen: list[str] = []
        for row in self.transitions(workspace, timeout=timeout):
            if row["unit"] not in seen:
                seen.append(row["unit"])
        return seen

    def sessions_of(
        self, workspace: str, unit: str, timeout: float | None = None
    ) -> dict[str, Any]:
        """The sequence of sessions behind one unit, plus what is not known.

        One session appears once however many transitions it made, in order of first
        appearance. `unknown` is counted separately, not listed as a session, so imported
        history never reads as one session's work.
        """
        order: list[str] = []
        found: dict[str, dict[str, Any]] = {}
        unknown = 0
        for row in self.transitions(workspace, unit, timeout=timeout):
            session = row["session"]
            if session == UNKNOWN:
                unknown += 1
                continue
            if session not in found:
                order.append(session)
                found[session] = {
                    "session": session,
                    "first": row["at"],
                    "last": row["at"],
                    "actor": row["actor"],
                    "stages": [],
                    "transitions": 0,
                }
            entry = found[session]
            entry["last"] = row["at"]
            entry["transitions"] += 1
            if row["stage"] not in entry["stages"]:
                entry["stages"].append(row["stage"])
        return {
            "sessions": [found[s] for s in order],
            "unknown_transitions": unknown,
        }


    def add_output(
        self,
        workspace: str,
        unit: str,
        stage: str,
        kind: str,
        path: str,
        *,
        actor: str = UNKNOWN,
        session: str = UNKNOWN,
        source: str = UNKNOWN,
        at: str | None = None,
        once_key: str = "",
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Record one file this unit produced, and which of the two kinds it is."""
        if kind not in KINDS:
            raise BadTransition(f"kind must be one of {', '.join(KINDS)}, got {kind!r}")
        if not str(path or "").strip():
            raise BadTransition("an output needs a path")
        row = {
            "at": str(at or "").strip() or _now(),
            "root": self._root,
            "workspace": str(workspace or ""),
            "unit": str(unit or "").strip(),
            "stage": str(stage or "").strip() or UNKNOWN,
            "kind": kind,
            "path": str(path).strip(),
            "actor": _text(actor),
            "session": _text(session),
            "source": _text(source),
            "once_key": str(once_key or ""),
        }
        if not row["unit"]:
            raise BadTransition("an output needs a unit")
        placeholders = ", ".join("?" for _ in _OUTPUT_COLUMNS)
        with self.data.write(timeout=LOCK_TIMEOUT if timeout is None else timeout) as conn:
            conn.execute(
                f"INSERT OR IGNORE INTO outputs ({', '.join(_OUTPUT_COLUMNS)}) "
                f"VALUES ({placeholders})",
                tuple(row[name] for name in _OUTPUT_COLUMNS),
            )
        return row

    def outputs(
        self, workspace: str, unit: str | None = None, timeout: float | None = None
    ) -> list[dict[str, Any]]:
        sql = f"SELECT id, {', '.join(_OUTPUT_COLUMNS)} FROM outputs WHERE root = ? AND workspace = ?"
        args: list[Any] = [self._root, workspace]
        if unit is not None:
            sql += " AND unit = ?"
            args.append(unit)
        sql += " ORDER BY id"
        with self.data.connect(timeout=LOCK_TIMEOUT if timeout is None else timeout) as conn:
            return [dict(row) for row in conn.execute(sql, args).fetchall()]

    def output_counts(
        self, workspace: str, unit: str | None = None, timeout: float | None = None
    ) -> dict[str, int]:
        """How many files, by kind and altogether."""
        counts = {kind: 0 for kind in KINDS}
        rows = self.outputs(workspace, unit, timeout=timeout)
        for row in rows:
            counts[row["kind"]] = counts.get(row["kind"], 0) + 1
        counts["total"] = len(rows)
        return counts


def settled_edits(
    transitions: Iterable[dict[str, Any]], machine: Machine
) -> list[dict[str, Any]]:
    """The transitions where an artifact was touched while already settled.

    A filter over the log, not a column. It does not require that the state changed: most are
    `accepted -> accepted`, a settled artifact rewritten in place. `machine` is required, with
    no default: rows written under another set would match nothing and return an empty list.
    """
    return [row for row in transitions if machine.is_settled(row["from_state"])]
