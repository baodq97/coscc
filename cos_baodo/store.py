"""The workspace list, and the reason a bad entry cannot become a bad path.

The safety property here is a data shape, not a check. A stored entry holds a `name` —
**one path segment** — and never an absolute path, so there is no field in which a
hand-edited store could put `/etc`. The real path is built from the working folder on every
read (`spec.md` R12). `is_under` stays as a second layer, but the first layer is that
the dangerous value has nowhere to live. Since the move to SQLite that is stronger: the
table has **no column** for a path at all.

This is the first state the app owns. It holds workspaces and labels, and nothing else:
conversation content belongs to the SDK's session store, which stays the one source of
truth for anything said.

**Where it lives, and why that changed.** This was once a JSON file inside the
working folder, replaced by `rename` on every write, with a `flock` beside it. It is now
rows in the app's own SQLite database under the data root (`cos_baodo/data.py`), because
`intent.md` asked for one durable place that exists whether or not a working folder
does. One row per `(root, name)`, so one database serves every working folder on the
machine and a workspace still cannot be named outside its own root.

**Concurrency, and why the transaction is where it is.** Measurement of the old
arrangement: four processes adding five workspaces each to one working folder left 8 of 20,
with no error anywhere. The loss was never two writes colliding — it was two
read-then-write sequences interleaving, each reading the old list and each writing back
what it computed. That is why every mutation below runs inside `Data.write`, which opens
`BEGIN IMMEDIATE`: the transaction covers the whole read-modify-write, exactly as the file
lock did. `spec.md` C2 is explicit that swapping the mechanism does not carry the
proof across — `scripts/verify_0004.py` is what decides it, and it still measures 20.
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from cos_baodo.data import BUSY_TIMEOUT, Busy, Data, now

# Kept as the name callers already pass to `transaction(timeout=...)` and patch in tests.
# The value is the same 10 seconds the file lock waited, now enforced by SQLite's
# `busy_timeout` (`cos_baodo/data.py`).
LOCK_TIMEOUT = BUSY_TIMEOUT

# One path segment. No separators, no `.`/`..`, bounded length. `spec.md` R12 lists
# the inputs this has to turn away.
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

    Every method re-reads. That is deliberate: `spec.md` R21 wants membership decided
    at read time, and a cached list is exactly the thing that made an earlier gate safe for a
    reason that no longer holds.

    `data` is the app's data root. It is passed in rather than defaulted at the call sites
    so that a test, or a proof driving four processes at a temporary root, cannot reach the
    real `~/.cos` by forgetting an argument.
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

        Delegates to `Data.write`, which is the only place `BEGIN IMMEDIATE` is issued.
        Kept as a method because callers already frame multi-step work with it, and
        because the timeout has to be one value a test can shorten — a test that had to
        wait the real deadline would not be run.
        """
        with self.data.write(timeout=LOCK_TIMEOUT if timeout is None else timeout) as conn:
            yield conn

    # -- reading ------------------------------------------------------------

    def entries(self) -> list[Entry]:
        """Entries from the database, with anything unusable dropped rather than repaired.

        A name that does not pass `valid_name` is ignored, not corrected: the database is
        editable by hand like the file before it, and a repair would write back something
        the user did not ask for. Dropping it keeps the invariant without touching their
        data.

        Order is insertion order. `add` re-inserts an existing name, so re-adding moves an
        entry to the end — the behaviour the JSON list had.
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

        Both layers apply. `is_under` alone would accept any subdirectory of the working
        folder, including ones nobody added.
        """
        try:
            target = Path(directory).expanduser().resolve()
        except OSError:
            return False
        if not self.is_under(directory):
            return False
        return any(self.path_of(e.name) == target for e in self.entries())

    # -- writing ------------------------------------------------------------

    def add(self, name: str, label: str = "") -> Entry:
        require_name(name)
        with self.transaction() as conn:
            # Delete then insert rather than upsert, so the row takes a new rowid and the
            # entry moves to the end of the list. That is what the JSON version did, and
            # `entries` orders by rowid.
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
        """Drops the entry. Never touches the directory — `spec.md` R18 and C6."""
        require_name(name)
        with self.transaction() as conn:
            changed = conn.execute(
                "DELETE FROM workspaces WHERE root = ? AND name = ?", (self._root, name)
            ).rowcount
            if not changed:
                raise KeyError(name)
