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
from typing import Any, Literal, Sequence, get_args

from coscc.units import states
from coscc.store.db import BUSY_TIMEOUT, Data, now as _now
from coscc.units.states import Machine

# What a field says when nobody can say: never blank, never NULL, one word so a query can ask for it.
UNKNOWN = "unknown"

LOCK_TIMEOUT = BUSY_TIMEOUT

# Whose decision a transition is. `person` is the originator; `agent` is what a model inferred
# and never counts as one; `code` is a guard reading git, `gh` or the database. An older row
# says `UNKNOWN`.
Authority = Literal["person", "agent", "code"]
AUTHORITIES: tuple[Authority, ...] = get_args(Authority)

_TRANSITION_COLUMNS = (
    "at",
    "root",
    "workspace",
    "unit",
    "artifact",
    "stage",
    "from_state",
    "to_state",
    "actor",
    "session",
    "source",
    "machine",
    "once_key",
    "guard",
    "authority",
    "run",
    "inputs",
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

    def record_in(
        self, conn: sqlite3.Connection, items: Sequence[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """`record_many` inside a transaction the caller already holds."""
        return self._insert(conn, [self._validate(item) for item in items])

    def _insert(
        self, conn: sqlite3.Connection, prepared: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        stored: list[dict[str, Any]] = []
        latest = self._latest(conn)
        for row in prepared:
            key = (row["workspace"], row["unit"], row["artifact"])
            if row["from_state"] is None:
                row["from_state"] = latest.get(key, self.machine.absent)
            complaint = self.machine.refuse(row["artifact"], row["from_state"])
            if complaint is None:
                ref = self._process_of(conn, row)
                complaint = self.machine.refuse(row["artifact"], row["to_state"], ref)
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

    def _process_of(self, conn: sqlite3.Connection, row: dict[str, Any]) -> str | None:
        """The process the unit records, `None` while it has no `unit_meta` row yet."""
        found = conn.execute(
            "SELECT process FROM unit_meta WHERE root = ? AND workspace = ? AND unit = ?",
            (self._root, row["workspace"], row["unit"]),
        ).fetchone()
        return found[0] if found else None

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
            raise BadTransition(
                f"authority must be one of {', '.join(AUTHORITIES)}, got {authority!r}"
            )
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
