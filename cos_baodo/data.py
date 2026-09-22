"""The one directory this app keeps its own state in, and the one connection into it.

`spec.md` R1: there is exactly one data directory, it defaults to `~/.cos`, and its
location is read from the environment in `config.from_env` and nowhere else. This module
is the only place that turns that setting into a path, opens the database, or knows the
schema. Everything above it asks for a `Data` and gets handed something already correct.

**Why SQLite here at all.** `0005` measured what the previous arrangement cost: four
processes adding five workspaces each to one JSON file left 8 of 20, silently. The fix
then was `flock` around the whole read-modify-write. SQLite is a different mechanism for
the same property, and `spec.md` C2 is explicit that it does not inherit the proof —
`scripts/verify_0005.py` has to go green again on this code before R9 is a fact.

Three settings below are what make that property hold, and none of them is a default:

`journal_mode=WAL` — readers do not block the writer and the writer does not block
readers. Without it, the board reading while a step writes is a lock conflict rather than
a read.

`busy_timeout` — the kernel-side wait. Set to the same **10 seconds** the file lock used
(`store.LOCK_TIMEOUT` before this unit), because the number was chosen for the same
reason: turn an indefinite block into an error, not wait for anyone.

`BEGIN IMMEDIATE` in `write()` — the one that actually matters. SQLite's default
transaction takes a read lock first and tries to upgrade on the first write, and an
upgrade that loses the race is aborted rather than retried. Every read-modify-write in
this app therefore declares itself a writer up front. This is the exact shape of the bug
`0005` found, moved into a different mechanism, which is why it gets a paragraph instead
of a line.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
from typing import Any, Iterator

# Bumped when a migration changes the shape below. `_open` refuses a database numbered
# higher than this rather than guessing what the extra columns mean (`spec.md` R5).
SCHEMA_VERSION = 1

DEFAULT_DIR = "~/.cos"
DB_FILENAME = "cos.db"
OBJECTS_DIRNAME = "objects"

# Seconds. Matches the file-lock timeout this replaced; see the module docstring.
BUSY_TIMEOUT = 10.0

# How often `_retry` looks again. Same value the file lock polled at, for the same
# reason: short enough not to be felt, long enough not to spin.
RETRY_POLL = 0.01

# `0o700`, from `spec.md` R2. The directory holds a record of every workspace on this
# machine and every prompt-shaped thing the app has run, so it is not world-readable even
# though the app is single-user.
DIR_MODE = 0o700

# The schema, one statement per entry. Not a single script: `executescript` issues a COMMIT
# before it runs, so it cannot be used inside the transaction that creates the schema — and
# creating the schema outside a transaction is how four processes starting at once end up
# racing each other through it.
#
# The version lives in SQLite's own `PRAGMA user_version` rather than in a table. Reading it
# costs nothing and needs no lock, which is what lets every later connection skip all of
# this with one read. A version table would be a second place to look and a write to reach.
_SCHEMA = (
    """-- One row per migration that has already run, so a migration cannot run twice. Keyed by
-- a caller-chosen string rather than a number: the imports are per working folder, and
-- there is no ordering between them.
CREATE TABLE IF NOT EXISTS migrations (
    key TEXT PRIMARY KEY,
    at  TEXT NOT NULL
)""",
    """-- `spec.md` R6. `name` is one path segment and there is deliberately **no column for a
-- workspace path**. The path is rebuilt from `root` on every read, so a hand-edited
-- database has nowhere to put `/etc`.
CREATE TABLE IF NOT EXISTS workspaces (
    root     TEXT NOT NULL,
    name     TEXT NOT NULL,
    label    TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL,
    PRIMARY KEY (root, name)
)""",
    """-- The journal. `record` holds the whole record as JSON exactly as the journal composed
-- it; the columns beside it exist only so a query can narrow without parsing every row.
-- Storing the record twice would be two truths, so the columns are read *from* the JSON
-- at insert time and never written independently.
CREATE TABLE IF NOT EXISTS runs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL,
    root      TEXT NOT NULL,
    workspace TEXT NOT NULL DEFAULT '',
    unit      TEXT NOT NULL DEFAULT '',
    stage     TEXT NOT NULL DEFAULT '',
    kind      TEXT NOT NULL,
    record    TEXT NOT NULL
)""",
    """-- Oldest-first within a scope is every read this table has, and `id` is monotonic where
-- `at` is only second-resolution. The index is shaped after the query, not after a
-- measurement -- `spec.md` open question 2 says so plainly.
CREATE INDEX IF NOT EXISTS runs_scope ON runs (root, workspace, unit, id)""",
    """-- Appearance and the other things the Settings screen remembers (`spec.md` R14). Machine
-- wide rather than per browser; `spec.md` C6 records why that is right here and would be
-- wrong with two users.
CREATE TABLE IF NOT EXISTS prefs (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
)""",
)


class Incompatible(RuntimeError):
    """The database on disk was written by a newer version of this app.

    Raised rather than worked around. A newer schema may have moved something this code
    still writes, and the failure mode of guessing is a corrupted history that looks fine.
    """


class Busy(RuntimeError):
    """Something else held the database past the timeout.

    Same contract the file lock had: an error that names the file, never a hang.
    """


def now() -> str:
    """UTC, second resolution. Shared so every table stamps time the same way."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Data:
    """One data directory: the database, the object folder, and the schema in between.

    Cheap to construct and safe to construct repeatedly — it opens no connection until
    asked. Connections are per call rather than pooled, because the processes that share
    this directory are separate OS processes and a pool would only help within one of them.
    """

    def __init__(self, root: str | os.PathLike[str] | None = None):
        self.root = Path(root or DEFAULT_DIR).expanduser().resolve()
        self.db_path = self.root / DB_FILENAME
        self.objects_dir = self.root / OBJECTS_DIRNAME
        # Threads inside one process still serialise here. It is not what keeps two
        # processes apart -- SQLite does that -- but it is cheap and it keeps a single
        # process from spending its busy timeout fighting itself.
        self._lock = threading.Lock()

    # -- the directory ------------------------------------------------------

    def ensure_dir(self) -> Path:
        """Create the directory if it is not there. `spec.md` R2: nobody sets this up."""
        self.root.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
        # `mkdir(mode=...)` is a no-op when the directory already exists, and an existing
        # directory made by an older build may be `0o755`. Tightening on every open is
        # what makes R2 true of a directory that already exists, not only a new one.
        try:
            os.chmod(self.root, DIR_MODE)
        except OSError:
            # A directory we cannot chmod is still usable; refusing to start over a
            # permission bit would be worse than the bit.
            pass
        return self.root

    # -- connections --------------------------------------------------------

    @contextmanager
    def connect(self, timeout: float | None = None) -> Iterator[sqlite3.Connection]:
        """A connection with the schema present, WAL on, and a bounded wait.

        `isolation_level=None` turns off the driver's implicit transaction handling, which
        is the only way `write()` below can say `BEGIN IMMEDIATE` and have it mean what it
        says.

        **`busy_timeout` is the first statement, and that is not a style choice.** It was
        measured on 2026-09-22: with `PRAGMA journal_mode=WAL` issued first, the journal's
        four-writer test failed roughly one run in ten with `database is locked` raised out
        of the pragma itself. Changing the journal mode wants an exclusive lock, and a
        connection that has not yet been told how long to wait does not wait at all.
        """
        self.ensure_dir()
        wait = BUSY_TIMEOUT if timeout is None else timeout
        conn = sqlite3.connect(self.db_path, timeout=wait, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(f"PRAGMA busy_timeout={int(max(wait, 0.0) * 1000)}")
            self._prepare(conn, wait)
            yield conn
        except sqlite3.OperationalError as e:
            raise self._busy(e, wait) from e
        finally:
            conn.close()

    @contextmanager
    def write(self, timeout: float | None = None) -> Iterator[sqlite3.Connection]:
        """One read-modify-write, declared as a writer from the first statement.

        Every caller that reads a value and writes something derived from it must use
        this rather than `connect`. See the module docstring for why the distinction is
        the whole point of this file.
        """
        wait = BUSY_TIMEOUT if timeout is None else timeout
        with self._lock, self.connect(timeout=wait) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as e:
                raise self._busy(e, wait) from e
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    def _busy(self, error: sqlite3.OperationalError, wait: float) -> Exception:
        """Map SQLite's lock errors onto the vocabulary the rest of the app already uses."""
        text = str(error).lower()
        if "locked" in text or "busy" in text:
            return Busy(
                f"another process is holding {self.db_path} "
                f"(waited {wait:.0f}s) — try again in a moment"
            )
        return error

    def _retry(self, work, wait: float) -> None:
        """Run something that SQLite may answer with `SQLITE_BUSY`, until the deadline.

        `busy_timeout` covers ordinary statements. It does not reliably cover changing the
        journal mode, which is why this exists at all and why it is used in exactly two
        places below.
        """
        deadline = time.monotonic() + max(wait, 0.0)
        while True:
            try:
                work()
                return
            except sqlite3.OperationalError as e:
                text = str(e).lower()
                if "locked" not in text and "busy" not in text:
                    raise
                if time.monotonic() >= deadline:
                    raise
                time.sleep(RETRY_POLL)

    # -- schema -------------------------------------------------------------

    def _prepare(self, conn: sqlite3.Connection, wait: float) -> None:
        """Make this connection usable. Every step here is a read in the common case."""
        # WAL is a property of the database file, not of the connection, so it is set once
        # in the life of the database and read on every open. Reading it needs no lock;
        # setting it does, which is why the two are not the same statement.
        if str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
            self._retry(lambda: conn.execute("PRAGMA journal_mode=WAL"), wait)
        # Armed rather than load-bearing: `_SCHEMA` declares no foreign key today, so this
        # enforces nothing. Kept because SQLite defaults it *off* per connection, and a
        # table added later would otherwise get no enforcement and no warning.
        conn.execute("PRAGMA foreign_keys=ON")

        found = self._user_version(conn)
        if found > SCHEMA_VERSION:
            raise Incompatible(
                f"{self.db_path} was written by a newer version of this app "
                f"(database schema {found}, this build understands {SCHEMA_VERSION}) — "
                "upgrade the app rather than running this one against it"
            )
        if found < SCHEMA_VERSION:
            self._retry(lambda: self._create(conn), wait)
        # An equal number is the whole common path: one pragma read, and nothing else.
        # A lower number is where a migration would run. There is only one version so far,
        # and writing a migration for a shape that has never shipped would be writing it
        # against a guess.

    @staticmethod
    def _user_version(conn: sqlite3.Connection) -> int:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])

    def _create(self, conn: sqlite3.Connection) -> None:
        """Create the schema inside one transaction, re-checking under the lock.

        Four processes can reach this at the same moment on a fresh data root. The first
        one through sets `user_version`; the rest re-read it here, inside `BEGIN
        IMMEDIATE`, and find there is nothing left to do.
        """
        conn.execute("BEGIN IMMEDIATE")
        try:
            found = self._user_version(conn)
            if found > SCHEMA_VERSION:
                raise Incompatible(
                    f"{self.db_path} was written by a newer version of this app "
                    f"(database schema {found}, this build understands {SCHEMA_VERSION})"
                )
            if found < SCHEMA_VERSION:
                for statement in _SCHEMA:
                    conn.execute(statement)
                # Not parameterisable; `SCHEMA_VERSION` is this module's own integer.
                conn.execute(f"PRAGMA user_version={int(SCHEMA_VERSION)}")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    def version(self) -> int:
        with self.connect() as conn:
            return self._user_version(conn)

    # -- one-shot migrations ------------------------------------------------

    def has_run(self, key: str, conn: sqlite3.Connection | None = None) -> bool:
        """Whether a one-shot migration under this key has already happened."""
        if conn is not None:
            return conn.execute("SELECT 1 FROM migrations WHERE key = ?", (key,)).fetchone() is not None
        with self.connect() as c:
            return c.execute("SELECT 1 FROM migrations WHERE key = ?", (key,)).fetchone() is not None

    def import_once(
        self,
        conn: sqlite3.Connection,
        key: str,
        source: Path,
        load: "Callable[[sqlite3.Connection], None]",
    ) -> None:
        """Run a one-shot import of `source`, inside the caller's transaction.

        `Store` and `Journal` both bring a pre-`0011` file in, and both did the same three
        things around the part that differs. `0011 spec.md` R8 is the requirement; these
        are the properties that make it safe to call on every path in:

        - the cheapest possible exit for the common case is one `stat` and no query, which
          is what any machine set up after this unit takes;
        - the `migrations` row is written **in the caller's transaction**, so an import
          that rolls back is not recorded as done;
        - the file is never deleted. An import that turns out wrong is recoverable only
          while the thing it read from still exists.

        Only `load` differs between the two callers: what the file says, and what rows it
        becomes.
        """
        if not source.is_file():
            return
        if self.has_run(key, conn):
            return
        load(conn)
        Data.mark_run(conn, key)

    @staticmethod
    def mark_run(conn: sqlite3.Connection, key: str) -> None:
        """Record a migration inside the same transaction that performed it.

        Taking the connection rather than opening one is deliberate: a mark written in a
        second transaction could survive a rollback of the first, and then the import it
        claims to record would never run.
        """
        conn.execute(
            "INSERT OR IGNORE INTO migrations (key, at) VALUES (?, ?)", (key, now())
        )

    # -- preferences --------------------------------------------------------

    def pref(self, key: str, default: Any = None) -> Any:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM prefs WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            # Hand-edited into something unreadable. Same posture as the old store: drop
            # it back to the default rather than repair a file somebody wrote themselves.
            return default

    def set_pref(self, key: str, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        with self.write() as conn:
            conn.execute(
                "INSERT INTO prefs (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, payload),
            )

    def prefs(self) -> dict[str, Any]:
        with self.connect() as conn:
            rows = conn.execute("SELECT key, value FROM prefs").fetchall()
        out: dict[str, Any] = {}
        for row in rows:
            try:
                out[row["key"]] = json.loads(row["value"])
            except (TypeError, ValueError):
                continue
        return out
