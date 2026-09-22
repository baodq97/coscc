"""What happened to a unit: the transitions, and the projection of where it is now.

`.cos/0013_board-cannot-say-what-happened/intent.md` measured what the old arrangement
cost. A stage's state was read out of the `Status:` line of a file on disk, so rewriting
that file destroyed every state it had held before — 42 such rewrites had already happened
in this repository by 2026-09-22, and the board could show none of them.

**R1 is the whole design: the transition is the record.** There is no column anywhere
holding a current state, and `state()` below is a fold over the log rather than a read of
a stored value. The test that proves it deletes the last row of a unit by hand and watches
the projection fall back on its own. A design carrying both a log and a state column has
two truths, and the one people edit is never the one they read.

**R3 is why nothing here is optional.** Every field is written on every row, and a caller
that does not know something writes `UNKNOWN` rather than leaving it out. `intent.md`
exists because "nobody recorded this" currently looks exactly like "this did not happen";
a blank column would be that confusion moved into the database.

The first use of this is the git import, and it can supply neither an actor nor a session
— git knows who authored a commit, not which session produced it. So the imported history
arrives with both fields `UNKNOWN`, deliberately and visibly. `spec.md` C1 says so: the
provenance this unit builds is true of work done **after** it, not of work already in git.

**A unit has a sequence of sessions, not a session.** `coscc/runner.py:271-274` passes
`session_id=None` on every step, so eight stages make eight sessions; `coscc/sessions.py:184`
sets `fork_session=False`, so one chat keeps one id across many turns. Those are different
mechanisms and `sessions_of()` keeps them apart: one multi-turn session is one row, and a
unit that ran eight steps has eight rows. Anything holding a singular `session_id` for a
unit is wrong from the second step onward (`intent.md` constraint 2).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

from coscc import states
from coscc.data import BUSY_TIMEOUT, Data, now as _now
from coscc.states import Machine

# What a field says when nobody can say. R3: never a blank, never a NULL, never absent
# from the row. One word, used everywhere, so a query can ask for it.
UNKNOWN = "unknown"

# R5's classifier. A deliverable is the unit's own artifact; a code change is a file in
# somebody's repository. Two kinds in one table so that "how many altogether" is one query
# rather than a union every caller has to remember to write.
DELIVERABLE = "deliverable"
CODE = "code"
KINDS = (DELIVERABLE, CODE)

LOCK_TIMEOUT = BUSY_TIMEOUT

_TRANSITION_COLUMNS = (
    "at", "root", "workspace", "unit", "artifact", "stage",
    "from_state", "to_state", "actor", "session", "source", "machine", "once_key",
)

_OUTPUT_COLUMNS = (
    "at", "root", "workspace", "unit", "stage", "kind", "path",
    "actor", "session", "source", "once_key",
)


class BadTransition(ValueError):
    """A transition this module will not store, carrying a reason a caller can show."""


def _text(value: Any) -> str:
    """Anything into a non-empty string, `UNKNOWN` when there is nothing to say.

    One function because R3 has one rule, and applying it in eleven places by hand is how
    ten of them stay right.
    """
    out = "" if value is None else str(value).strip()
    return out or UNKNOWN


class History:
    """The transition log and the file log for one working folder.

    `data` is passed in for the same reason `Journal` takes it: a test that forgets it
    writes into the real `~/.cos` (`coscc/journal.py:108-109`). The two new tables inherit
    that hazard unchanged.

    `machine` is the state set every write is validated against and every row records. It
    is a constructor argument rather than a module lookup because `spec.md` R6 has to be
    demonstrable, and a set that can only be changed by editing Python is not configuration.
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

    # -- writing ------------------------------------------------------------

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
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Append one transition. Returns the row as it was stored.

        `from_state` is **derived from the log** unless given. That is not a convenience:
        deriving it is what keeps the chain internally consistent under R1, because the
        only thing that can say where an artifact was is the row before it. A caller
        passing one is asserting something the log can contradict.

        The derive and the insert are one `BEGIN IMMEDIATE` transaction
        (`coscc/data.py` module docstring): a read followed by a write derived from it is
        the exact shape that lost 12 of 20 workspaces before SQLite was here.
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
                }
            ],
            timeout=timeout,
        )
        return rows[0]

    def record_many(
        self, items: Sequence[dict[str, Any]], timeout: float | None = None
    ) -> list[dict[str, Any]]:
        """Append transitions in order, in one transaction. Returns the stored rows.

        The import needs this: 42 separate transactions is 42 chances to be interrupted
        halfway, and a log with half a unit's history in it is worse than one with none —
        the projection would be confidently wrong rather than empty.

        A row whose `once_key` is already present is skipped and **not** returned, which is
        what makes an import re-runnable (`coscc/data.py` `transitions_once`).
        """
        prepared = [self._validate(item) for item in items]
        stored: list[dict[str, Any]] = []
        with self.data.write(timeout=LOCK_TIMEOUT if timeout is None else timeout) as conn:
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
                    stored.append(dict(row))
        return stored

    def _validate(self, item: dict[str, Any]) -> dict[str, Any]:
        """One row, checked against the state set and filled out under R3.

        Done before the transaction opens so that a bad row in a batch refuses the batch
        without having held the database.
        """
        artifact = str(item.get("artifact") or "").strip()
        to_state = str(item.get("to_state") or "").strip()
        unit = str(item.get("unit") or "").strip()
        if not unit:
            raise BadTransition("a transition needs a unit")
        complaint = self.machine.refuse(artifact, to_state)
        if complaint is not None:
            raise BadTransition(complaint)

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
            # Not `_text`: an absent key means "do not deduplicate this", and turning that
            # into the word `unknown` would make every keyless row collide with the first.
            "once_key": str(item.get("once_key") or ""),
        }

    def _stage_of(self, artifact: str) -> str:
        stage = self.machine.for_artifact(artifact)
        return stage.name if stage is not None else UNKNOWN

    def _latest(self, conn: sqlite3.Connection) -> dict[tuple[str, str, str], str]:
        """The newest state of every artifact this working folder has a transition for.

        One query rather than one per row: an import of a whole repository would otherwise
        be quadratic in the number of artifacts, and it is the only caller that matters.
        """
        rows = conn.execute(
            "SELECT workspace, unit, artifact, to_state FROM transitions "
            "WHERE root = ? AND id IN ("
            "  SELECT MAX(id) FROM transitions WHERE root = ? "
            "  GROUP BY workspace, unit, artifact)",
            (self._root, self._root),
        ).fetchall()
        return {(r["workspace"], r["unit"], r["artifact"]): r["to_state"] for r in rows}

    # -- reading ------------------------------------------------------------

    def transitions(
        self,
        workspace: str | None = None,
        unit: str | None = None,
        artifact: str | None = None,
        timeout: float | None = None,
    ) -> list[dict[str, Any]]:
        """Every transition, oldest first, optionally narrowed.

        Ordered by `id`. `at` is second-resolution, so two transitions recorded in the
        same second would have no order at all if it were the key — the same reasoning as
        `coscc/journal.py`.
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

        Every artifact the state set knows appears, including ones with no transition at
        all: those read as the absent state, which is what `coscc/board.py:63-81` derives
        from a missing file today. Reported in stage order so a caller never has to know
        the order itself.

        Folded in Python rather than asked for in SQL. The volume is one unit's history —
        42 rows for this entire repository — and a fold is a thing a reader can check
        against R1 by looking at it.
        """
        current = {artifact: self.machine.absent for artifact in self.machine.artifacts}
        for row in self.transitions(workspace, unit, timeout=timeout):
            current[row["artifact"]] = row["to_state"]
        return current

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
        """R4. The sequence of sessions behind one unit, plus what is not known.

        One session appears once however many transitions it made — that is what keeps a
        multi-turn chat from reading as several sessions. Ordered by first appearance,
        because the order the stages ran in is the thing a reader is looking for.

        `unknown` is counted separately rather than listed as a session. `UNKNOWN` is not
        an id, and a row called "unknown" sitting in the sequence would let the imported
        history read as though one session did all of it — precisely the misreading
        `spec.md` C1 warns about.
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

    # -- what a unit produced -----------------------------------------------

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
        """R5. Record one file this unit produced, and which of the two kinds it is."""
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
        """How many files, by kind and altogether. `total` is here rather than at the
        call site because R5 exists so that nobody has to add the two up themselves."""
        counts = {kind: 0 for kind in KINDS}
        rows = self.outputs(workspace, unit, timeout=timeout)
        for row in rows:
            counts[row["kind"]] = counts.get(row["kind"], 0) + 1
        counts["total"] = len(rows)
        return counts


def settled_edits(
    transitions: Iterable[dict[str, Any]], machine: Machine | None = None
) -> list[dict[str, Any]]:
    """The transitions `0013`'s outcome counts: an artifact touched while already settled.

    `intent.md` measured 39 of these on 2026-09-22 and the board could show none of them.
    It is a filter over the log rather than a column on it — the log records what happened
    and this decides what to call interesting, which is the split that lets the definition
    change without a migration.

    Note what it does **not** require: that the state changed. Most of these are
    `accepted → accepted`, a settled artifact rewritten in place, which is exactly the
    event the old arrangement destroyed.
    """
    machine = machine or states.default()
    return [row for row in transitions if machine.is_settled(row["from_state"])]
