"""The one directory this app keeps its own state in, and the one connection into it.

`spec.md` R1: there is exactly one data directory, it defaults to `~/.cos`, and its
location is read from the environment in `config.from_env` and nowhere else. This module
is the only place that turns that setting into a path, opens the database, or knows the
schema. Everything above it asks for a `Data` and gets handed something already correct.

**Why SQLite here at all.** The previous arrangement was measured, and it cost: four
processes adding five workspaces each to one JSON file left 8 of 20, silently. The fix
then was `flock` around the whole read-modify-write. SQLite is a different mechanism for
the same property, and `spec.md` C2 is explicit that it does not inherit the proof —
`scripts/verify_0004.py` has to go green again on this code before R9 is a fact.

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
that measurement found, moved into a different mechanism, which is why it gets a paragraph instead
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

from coscc import config

if TYPE_CHECKING:
    from collections.abc import Callable
from typing import Any, Iterator

# Bumped when a migration changes the shape below. `_open` refuses a database numbered
# higher than this rather than guessing what the extra columns mean (`spec.md` R5).
#
# 2 added `transitions` and `outputs` for `.cos/0013_board-cannot-say-what-happened`. That
# refusal now has a cost worth stating out loud, because it is the way back from this
# unit: **a v0.2.3 or older build will not open a database this one has touched.** Rolling
# the app back means rolling the database back with it, and `~/.cos/cos.db` is not
# something a downgrade removes. `plan.md` Risk 3 records the decision.
#
# 3 added `auth` and `auth_sessions` for `.cos/0070_anyone-who-reaches-the-port-can-run-anything`.
# The same refusal applies one version on: **a build from before `0070` answers `500` on a
# database this one has touched** (that unit's `spec.md` C6), and since `0070` the login
# guard reads the database on every request, so it is every page, not only the routes
# that read data.
#
# 4 added `step_runs` and `step_events` for `.cos/0073_nobody-can-watch-what-a-running-agent-is-doing`.
# The number had to move: `_prepare` runs `_SCHEMA` only below it, so two tables added at 3
# would never reach a `cos.db` already at 3. The refusal applies once more (that unit's
# `spec.md` C6): **a build from before `0073` answers `500` on a database this one has
# touched.** Rolling the app back means rolling the database back with it.
SCHEMA_VERSION = 4

DEFAULT_DIR = "~/.cos"
DB_FILENAME = "cos.db"

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
    """-- `.cos/0013_board-cannot-say-what-happened` R1: **the transition is the record, and
-- "where is this unit now" is a query over this table.** There is deliberately no column
-- anywhere holding a current state. A design with both would have two truths, and the one
-- edited by hand would be the other one.
--
-- R3 decides the columns: a transition that cannot say who or which session must say so
-- in a value, not by leaving a column empty. So every column is NOT NULL with no default,
-- which pushes the decision onto the writer -- `coscc/history.py` substitutes its
-- `UNKNOWN` and nothing here can quietly accept a blank. `intent.md` exists because
-- "not known" already looks exactly like "did not happen"; a NULL here would be that
-- mistake written into the schema.
--
-- `machine` names the state set the row was written under. Without it, a database written
-- under one configuration and read under another compares states that never meant the
-- same thing, and `spec.md` C5 says that failure runs rather than stops.
CREATE TABLE IF NOT EXISTS transitions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    root       TEXT NOT NULL,
    workspace  TEXT NOT NULL,
    unit       TEXT NOT NULL,
    artifact   TEXT NOT NULL,
    stage      TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state   TEXT NOT NULL,
    actor      TEXT NOT NULL,
    session    TEXT NOT NULL,
    source     TEXT NOT NULL,
    machine    TEXT NOT NULL,
    once_key   TEXT NOT NULL DEFAULT ''
)""",
    """-- Oldest-first within a unit is every read this table has; `id` is monotonic where `at`
-- is only second-resolution, the same reasoning as `runs_scope`.
CREATE INDEX IF NOT EXISTS transitions_scope ON transitions (root, workspace, unit, id)""",
    """-- What makes an import re-runnable instead of doubling (`spec.md` open question 4).
-- The key is the writer's: the git import derives one per commit and artifact, so running
-- it twice inserts nothing the second time. It is **partial** so that live transitions,
-- which pass no key, are never deduplicated -- two identical moves a minute apart are two
-- events, and an append-only log that silently dropped the second would be lying by
-- omission. Empty string rather than NULL keeps R3's "no implicit blanks" true of every
-- column in the table.
CREATE UNIQUE INDEX IF NOT EXISTS transitions_once
    ON transitions (once_key) WHERE once_key <> ''""",
    """-- R5: "how many files did this produce, and where". One table with a `kind` column
-- rather than two tables, because every "how many in total" question would otherwise have
-- to union them at the call site, and one of the call sites would forget.
--
-- `path` is recorded as given. This table never resolves a path against a repository:
-- a deliverable and a code change live in different trees, and a column that sometimes
-- meant one and sometimes the other would need a reader to know which before it could be
-- read.
CREATE TABLE IF NOT EXISTS outputs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL,
    root      TEXT NOT NULL,
    workspace TEXT NOT NULL,
    unit      TEXT NOT NULL,
    stage     TEXT NOT NULL,
    kind      TEXT NOT NULL,
    path      TEXT NOT NULL,
    actor     TEXT NOT NULL,
    session   TEXT NOT NULL,
    source    TEXT NOT NULL,
    once_key  TEXT NOT NULL DEFAULT ''
)""",
    """CREATE INDEX IF NOT EXISTS outputs_scope ON outputs (root, workspace, unit, id)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS outputs_once
    ON outputs (once_key) WHERE once_key <> ''""",
    """-- `0070` R5: the master password, as an argon2id hash and nothing else. One row at
-- most, which the CHECK makes a property of the table rather than of every writer. Not a
-- `prefs` row: `prefs()` returns every row, and a Settings route that read widely would
-- hand the hash out. Times here are epoch seconds, unlike the ISO text elsewhere, because
-- every read of them is a comparison with "now".
CREATE TABLE IF NOT EXISTS auth (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    password_hash TEXT NOT NULL,
    set_at        INTEGER NOT NULL
)""",
    """-- `0070` R6: one row per live login. Only the SHA-256 of the cookie's value is kept, so
-- a copy of this file is not a copy of anyone's session.
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_sha256 TEXT PRIMARY KEY,
    created_at   INTEGER NOT NULL,
    last_used_at INTEGER NOT NULL,
    expires_at   INTEGER NOT NULL
)""",
    """-- `0073` R6: one row per board step's `run`, written when its recorder starts and kept
-- after its events are purged (R14). Times are epoch milliseconds, the unit of an event's
-- `at`. `ended_at` stays NULL for a step the app went down under: `ended-unknown`.
-- `events` and `bytes` count what was stored, `lost` what never was.
CREATE TABLE IF NOT EXISTS step_runs (
    run        TEXT PRIMARY KEY,
    root       TEXT NOT NULL,
    workspace  TEXT NOT NULL,
    unit       TEXT NOT NULL,
    stage      TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    ended_at   INTEGER,
    events     INTEGER NOT NULL DEFAULT 0,
    bytes      INTEGER NOT NULL DEFAULT 0,
    lost       INTEGER NOT NULL DEFAULT 0,
    purged_at  TEXT
)""",
    """CREATE INDEX IF NOT EXISTS step_runs_scope ON step_runs (root, workspace, unit, started_at)""",
    """-- `0073` R2, R6: every event of a `run`, whole, as the JSON the recorder composed. Not
-- rows of `runs`: the board folds every row of that table on every read, and the run log
-- is append-only where R14 has to delete (spec Design 2).
CREATE TABLE IF NOT EXISTS step_events (
    run   TEXT NOT NULL,
    seq   INTEGER NOT NULL,
    at    INTEGER NOT NULL,
    kind  TEXT NOT NULL,
    event TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    PRIMARY KEY (run, seq)
)""",
)


class Incompatible(RuntimeError):
    """The database on disk was written by a newer version of this app.

    Raised rather than worked around. A newer schema may have moved something this code
    still writes, and the failure mode of guessing is a corrupted history that looks fine.
    """


class Protected(RuntimeError):
    """This database belongs to the app that started this process, which must not open it.

    `.cos/0076_a-step-can-migrate-the-running-apps-database` R5. A step's code once
    migrated the running app's `cos.db` to a schema the app could not read, and every page
    answered `500` until somebody fixed the file by hand. The app now names its database
    in `config.PROTECTED_DB_VAR` for every child, and this is raised before a connection
    exists. It is a tripwire, not a lock: code that opens the file without `Data`, or a
    branch cut before this check, walks past it (that unit's `spec.md` C1).
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

        Before any of that, and before the directory is made, a database listed in
        `config.PROTECTED_DB_VAR` is refused (`0076` R5). Asked on every call, reads
        included: a step must not read the running app's data either.
        """
        if self.db_path.resolve() in config.protected_databases():
            raise Protected(
                f"{self.db_path} belongs to the app that started this process; "
                f"{config.PROTECTED_DB_VAR} lists it, so it is not opened here"
            )
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
        # A lower number re-runs `_create`, and that is the whole migration mechanism:
        # every statement in `_SCHEMA` is `IF NOT EXISTS`, so a v1 database meets the
        # tables 2, 3 and 4 added and keeps every row it already had. This works for *adding*. A
        # version that has to change or drop a column will need a real migration here, and
        # will not be able to reuse this path.

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

        `Store` and `Journal` both bring a pre-SQLite file in, and both did the same three
        things around the part that differs. `spec.md` R8 is the requirement; these
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

    def delete_pref(self, key: str) -> bool:
        """Remove one preference. True when there was one to remove."""
        with self.write() as conn:
            cur = conn.execute("DELETE FROM prefs WHERE key = ?", (key,))
            return cur.rowcount > 0

    def pref_rows(self, prefix: str) -> dict[str, str]:
        """Every preference whose key starts with `prefix`, **unparsed**.

        `prefs()` drops a row whose JSON will not parse, silently. That is fine for a
        screen density, and wrong for the model a stage runs on: a hand-edited
        `model:plan` that does not parse would make `plan` fall back with nobody told why
        (`0004_no-setting-says-which-model-runs-a-stage` spec R11). The caller parses, and
        reports what it could not.

        Matched in Python, not with `LIKE`, so a `_` or `%` in the prefix means itself.
        """
        with self.connect() as conn:
            rows = conn.execute("SELECT key, value FROM prefs").fetchall()
        return {
            str(row["key"]): row["value"]
            for row in rows
            if str(row["key"]).startswith(prefix)
        }

    # -- the login (`0070`) -------------------------------------------------
    #
    # Only `coscc/auth.py` calls these. None of them reads `prefs`, and `prefs()` never
    # reads these tables, so the hash has no road out through Settings.

    def auth_password_hash(self) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT password_hash FROM auth WHERE id = 1").fetchone()
        return None if row is None else str(row["password_hash"])

    def auth_set_password(self, password_hash: str, now: int) -> bool:
        """Store the first password. False when one is already there.

        Checked and inserted under one `BEGIN IMMEDIATE`, so of two `POST /setup` racing
        each other exactly one wins, and the loser is a refusal rather than an overwrite.
        """
        with self.write() as conn:
            if conn.execute("SELECT 1 FROM auth WHERE id = 1").fetchone() is not None:
                return False
            conn.execute(
                "INSERT INTO auth (id, password_hash, set_at) VALUES (1, ?, ?)",
                (password_hash, int(now)),
            )
            return True

    def auth_clear(self) -> None:
        """`coscc reset-password`: the password and every session, in one transaction."""
        with self.write() as conn:
            conn.execute("DELETE FROM auth")
            conn.execute("DELETE FROM auth_sessions")

    def auth_session_add(self, token_sha256: str, now: int, expires_at: int) -> None:
        """A new login. Sessions already past their expiry go in the same write."""
        with self.write() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (int(now),))
            conn.execute(
                "INSERT OR REPLACE INTO auth_sessions "
                "(token_sha256, created_at, last_used_at, expires_at) VALUES (?, ?, ?, ?)",
                (token_sha256, int(now), int(now), int(expires_at)),
            )

    def auth_state(self, token_sha256: str) -> tuple[bool, sqlite3.Row | None]:
        """Whether a password is set, and this session's row — one connection, one read.

        The guard asks this on every request it decides, which is what lets `coscc
        reset-password` take effect on the next request without a restart. The row is
        returned whatever its expiry; judging it is the caller's.
        """
        with self.connect() as conn:
            has_password = conn.execute("SELECT 1 FROM auth WHERE id = 1").fetchone() is not None
            row = conn.execute(
                "SELECT token_sha256, created_at, last_used_at, expires_at "
                "FROM auth_sessions WHERE token_sha256 = ?",
                (token_sha256,),
            ).fetchone() if token_sha256 else None
        return has_password, row

    def auth_session_touch(self, token_sha256: str, now: int, expires_at: int) -> None:
        with self.write() as conn:
            conn.execute(
                "UPDATE auth_sessions SET last_used_at = ?, expires_at = ? WHERE token_sha256 = ?",
                (int(now), int(expires_at), token_sha256),
            )

    def auth_session_delete(self, token_sha256: str) -> None:
        with self.write() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE token_sha256 = ?", (token_sha256,))

    # -- a step's events (`0073`) -------------------------------------------
    #
    # Written by `coscc/events.py`'s recorder from a thread, read by `Service.events_page`.
    # Nothing here reads `runs`, and no route writes through these.

    def step_run_open(
        self, run: str, root: str, workspace: str, unit: str, stage: str, started_at: int,
        timeout: float | None = None,
    ) -> None:
        with self.write(timeout=timeout) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO step_runs (run, root, workspace, unit, stage, started_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (run, root, workspace, unit, stage, int(started_at)),
            )

    def step_events_add(
        self, run: str, rows: list[dict[str, Any]], timeout: float | None = None,
    ) -> int:
        """Store events and count them into the index row, in one transaction. A `(run, seq)`
        already stored is ignored and not counted twice. Returns how many were new."""
        added = 0
        stored = 0
        with self.write(timeout=timeout) as conn:
            for event in rows:
                text = json.dumps(event, ensure_ascii=False, default=str)
                size = len(text.encode("utf-8"))
                cur = conn.execute(
                    "INSERT OR IGNORE INTO step_events (run, seq, at, kind, event, bytes) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (run, int(event["seq"]), int(event["at"]), str(event["kind"]), text, size),
                )
                if cur.rowcount > 0:
                    added += 1
                    stored += size
            if added:
                conn.execute(
                    "UPDATE step_runs SET events = events + ?, bytes = bytes + ? WHERE run = ?",
                    (added, stored, run),
                )
        return added

    def step_run_close(self, run: str, ended_at: int, lost: int, timeout: float | None = None) -> None:
        with self.write(timeout=timeout) as conn:
            conn.execute(
                "UPDATE step_runs SET ended_at = ?, lost = ? WHERE run = ?",
                (int(ended_at), int(lost), run),
            )

    def step_run(self, run: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM step_runs WHERE run = ?", (run,)).fetchone()
            if row is None:
                return None
            out = dict(row)
            out["last_at"] = conn.execute(
                "SELECT MAX(at) FROM step_events WHERE run = ?", (run,)
            ).fetchone()[0]
        return out

    def step_turns(self, run: str, timeout: float | None = None) -> int:
        """`0092` R1. How many `turn` events of `run` are stored: the step's turns, counted
        from what reached disk rather than from what the recorder held in memory."""
        with self.connect(timeout=timeout) as conn:
            return int(conn.execute(
                "SELECT COUNT(*) FROM step_events WHERE run = ? AND kind = 'turn'", (run,)
            ).fetchone()[0])

    def step_tool_uses(self, run: str, timeout: float | None = None) -> list[dict[str, Any]]:
        """`0085` R11. Every stored `tool_use` event of `run`, oldest first: what a step that
        wrote nothing had opened, for the next run's prompt."""
        with self.connect(timeout=timeout) as conn:
            rows = conn.execute(
                "SELECT event FROM step_events WHERE run = ? AND kind = 'tool_use' ORDER BY seq",
                (run,),
            ).fetchall()
        return [json.loads(r["event"]) for r in rows]

    def step_runs_open(self) -> list[dict[str, Any]]:
        """`0092` R5. Every index row nobody closed and nobody purged, each with the `at` of
        its last stored event as `last_at` (None when it has none), oldest first."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT r.*, (SELECT MAX(e.at) FROM step_events e WHERE e.run = r.run) AS last_at "
                "FROM step_runs r WHERE r.ended_at IS NULL AND r.purged_at IS NULL "
                "ORDER BY r.started_at, r.run"
            ).fetchall()
        return [dict(row) for row in rows]

    def step_events_page(self, run: str, before: int | None, limit: int) -> tuple[list[dict[str, Any]], bool]:
        """The last `limit` events with `seq < before` (all of them when `before` is None),
        oldest first, and whether any older one is stored."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT event FROM step_events WHERE run = ? AND seq < ? ORDER BY seq DESC LIMIT ?",
                (run, int(before) if before is not None else 2**62, int(limit)),
            ).fetchall()
            events = [json.loads(r["event"]) for r in reversed(rows)]
            older = bool(events) and conn.execute(
                "SELECT 1 FROM step_events WHERE run = ? AND seq < ? LIMIT 1",
                (run, int(events[0]["seq"])),
            ).fetchone() is not None
        return events, older

    def step_event(self, run: str, seq: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT event FROM step_events WHERE run = ? AND seq = ?", (run, int(seq))
            ).fetchone()
        return None if row is None else json.loads(row["event"])

    def step_events_purge(self, older_than_ms: int, max_bytes: int, now_iso: str) -> tuple[int, int]:
        """`0073` R14. Whole runs only, and their index rows kept with `purged_at`: first every
        run begun before `older_than_ms`, then the oldest while the stored total is over
        `max_bytes`. Returns `(runs, bytes)` purged. One transaction."""
        runs = 0
        freed = 0
        with self.write() as conn:
            def drop(run: str, size: int) -> None:
                nonlocal runs, freed
                conn.execute("DELETE FROM step_events WHERE run = ?", (run,))
                conn.execute("UPDATE step_runs SET purged_at = ? WHERE run = ?", (now_iso, run))
                runs += 1
                freed += int(size)

            for row in conn.execute(
                "SELECT run, bytes FROM step_runs WHERE purged_at IS NULL AND started_at < ? "
                "ORDER BY started_at, run",
                (int(older_than_ms),),
            ).fetchall():
                drop(row["run"], row["bytes"])
            kept = conn.execute(
                "SELECT run, bytes FROM step_runs WHERE purged_at IS NULL ORDER BY started_at, run"
            ).fetchall()
            total = sum(int(r["bytes"]) for r in kept)
            for row in kept:
                if total <= max_bytes:
                    break
                drop(row["run"], row["bytes"])
                total -= int(row["bytes"])
        return runs, freed
