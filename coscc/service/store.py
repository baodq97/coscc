"""The workspace list, and why a bad entry cannot become a bad path.

A stored entry holds a `name` (one path segment), never an absolute path, and the table has no
column for a path, so a hand-edited store has nowhere to put `/etc`. The real path is built
from the working folder on every read; `is_under` is a second layer.

Holds workspaces and labels only; conversation content belongs to the SDK's session store.
Rows live in the app's SQLite database (`coscc/data.py`), one per `(root, name)`.

Every mutation runs inside `Data.write` (`BEGIN IMMEDIATE`) so the whole read-modify-write is
one transaction: concurrent read-then-write sequences otherwise interleave and lose entries
without any error (`scripts/verify_0004.py` proves it with four processes).
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from coscc.data import BUSY_TIMEOUT, Busy, Data, now

# The name callers pass to `transaction(timeout=...)` and tests patch; enforced by SQLite's
# `busy_timeout` (`coscc/data.py`).
LOCK_TIMEOUT = BUSY_TIMEOUT

# One path segment. No separators, no `.`/`..`, bounded length.
_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
LABEL_MAX = 200

__all__ = [
    "BadName",
    "Busy",
    "Entry",
    "LABEL_MAX",
    "LOCK_TIMEOUT",
    "Store",
    "clean_label",
    "require_name",
    "valid_name",
]


class BadName(ValueError):
    """A name that may not become a directory under the working folder."""


def valid_name(name: str) -> bool:
    return bool(_NAME.fullmatch(name or "")) and name not in {".", ".."}


def require_name(name: str) -> str:
    if not valid_name(name):
        raise BadName(
            f"invalid workspace name: {name!r} "
            "(1-64 chars of letters, digits, dot, dash or underscore; not '.' or '..')"
        )
    return name


def clean_label(label: str | None) -> str:
    return (label or "").strip()[:LABEL_MAX]


@dataclass(frozen=True)
class Entry:
    name: str
    label: str = ""


class Store:
    """The workspace list for one working folder, kept in the app's own database.

    Every method re-reads: membership is decided at read time and a cache would go stale.

    `data` is the app's data root, passed in so a test cannot reach the real `~/.cos` by
    forgetting an argument.
    """

    def __init__(
        self,
        working_dir: str | os.PathLike[str],
        data: Data | str | os.PathLike[str] | None = None,
    ):
        self.working_dir = Path(working_dir).expanduser().resolve()
        self.data = data if isinstance(data, Data) else Data(data)
        self._root = str(self.working_dir)

    @contextmanager
    def transaction(self, timeout: float | None = None):
        """Hold the list exclusively for one read-modify-write.

        Delegates to `Data.write`, the only place `BEGIN IMMEDIATE` is issued; the timeout is one
        value a test can shorten.
        """
        with self.data.write(timeout=LOCK_TIMEOUT if timeout is None else timeout) as conn:
            yield conn


    def entries(self) -> list[Entry]:
        """Entries from the database, with anything unusable dropped rather than repaired.

        A name failing `valid_name` is ignored, not corrected: the database is editable by hand and
        a repair would write back something the user did not ask for.

        Order is insertion order; re-adding moves an entry to the end.
        """
        with self.data.connect() as conn:
            rows = conn.execute(
                "SELECT name, label FROM workspaces WHERE root = ? ORDER BY rowid",
                (self._root,),
            ).fetchall()
        return [
            Entry(name=row["name"], label=clean_label(row["label"]))
            for row in rows
            if valid_name(row["name"])
        ]

    def path_of(self, name: str) -> Path:
        """The only way a stored entry becomes a path. Built, never read from the store."""
        return self.working_dir / require_name(name)

    def is_under(self, directory: str) -> bool:
        """Second layer: does this resolve to something inside the working folder?"""
        try:
            target = Path(directory).expanduser().resolve()
        except OSError:
            return False
        return target != self.working_dir and self.working_dir in target.parents

    def resolves_to_entry(self, directory: str) -> bool:
        """Membership: some entry's built path equals this directory.

        Both layers apply: `is_under` alone would accept any subdirectory of the working folder.
        """
        try:
            target = Path(directory).expanduser().resolve()
        except OSError:
            return False
        if not self.is_under(directory):
            return False
        return any(self.path_of(e.name) == target for e in self.entries())


    def add(self, name: str, label: str = "") -> Entry:
        require_name(name)
        with self.transaction() as conn:
            # Delete then insert, so the row takes a new rowid and moves to the end (`entries` orders by rowid).
            conn.execute(
                "DELETE FROM workspaces WHERE root = ? AND name = ?", (self._root, name)
            )
            conn.execute(
                "INSERT INTO workspaces (root, name, label, added_at) VALUES (?, ?, ?, ?)",
                (self._root, name, clean_label(label), now()),
            )
            return Entry(name=name, label=clean_label(label))

    def set_label(self, name: str, label: str) -> Entry:
        require_name(name)
        cleaned = clean_label(label)
        with self.transaction() as conn:
            changed = conn.execute(
                "UPDATE workspaces SET label = ? WHERE root = ? AND name = ?",
                (cleaned, self._root, name),
            ).rowcount
            if not changed:
                raise KeyError(name)
            return Entry(name=name, label=cleaned)

    def remove(self, name: str) -> None:
        """Drops the entry. Never touches the directory."""
        require_name(name)
        with self.transaction() as conn:
            changed = conn.execute(
                "DELETE FROM workspaces WHERE root = ? AND name = ?", (self._root, name)
            ).rowcount
            if not changed:
                raise KeyError(name)
