"""The one directory this app keeps its own state in, and the one connection into it.

The location is read in `config.from_env` (default `~/.cos`); this module is the only place
that turns it into a path, opens the database, or knows the schema.

Three settings make concurrent writers from several processes safe, and none is a default:
`journal_mode=WAL` (readers and the writer do not block each other), `busy_timeout` (10 s,
turning an indefinite block into an error), and `BEGIN IMMEDIATE` in `write()`. SQLite's
default transaction takes a read lock and upgrades on the first write, and an upgrade that
loses the race is aborted rather than retried, so every read-modify-write declares itself a
writer up front.
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

# Bumped when a migration changes the shape below. `_open` refuses a database numbered higher
# than this rather than guessing. The refusal runs the other way too: **an older build answers
# `500` on a database a newer one has touched**, so rolling the app back means rolling the
# database back with it. Version 7 added *columns* (`_COLUMNS`). A new `_COLUMNS` entry moves the
# number too: a database already at this one never runs `_create` again (8: the `ci` columns;
# 9: `attempts` and `attempt_moves`; 10: `attempts.note_by`; 11: `decisions` dropped).
SCHEMA_VERSION = 11

DEFAULT_DIR = "~/.cos"
DB_FILENAME = "cos.db"

# Seconds.
BUSY_TIMEOUT = 10.0

# How often `_retry` looks again: short enough not to be felt, long enough not to spin.
RETRY_POLL = 0.01

# `0o700`: the directory holds a record of every workspace and prompt-shaped thing the app has run.
DIR_MODE = 0o700

# The schema, one statement per entry. Not a single script: `executescript` issues a COMMIT
# first, so it cannot run inside the transaction that creates the schema, and creating it
# outside one lets concurrent processes race through it. The version lives in SQLite's
# `PRAGMA user_version`: reading it needs no lock, so every later connection skips all of this.
_SCHEMA = (
    """-- One row per migration that has already run, so a migration cannot run twice. Keyed by
-- a caller-chosen string rather than a number: the imports are per working folder, and
-- there is no ordering between them.
CREATE TABLE IF NOT EXISTS migrations (
    key TEXT PRIMARY KEY,
    at  TEXT NOT NULL
)""",
    """-- `name` is one path segment and there is deliberately **no column for a
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
-- measurement.
CREATE INDEX IF NOT EXISTS runs_scope ON runs (root, workspace, unit, id)""",
    """-- Appearance and the other things the Settings screen remembers. Machine
-- wide rather than per browser; that is right here and would be
-- wrong with two users.
CREATE TABLE IF NOT EXISTS prefs (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
)""",
    """-- **The transition is the record, and
-- "where is this unit now" is a query over this table.** There is deliberately no column
-- anywhere holding a current state. A design with both would have two truths, and the one
-- edited by hand would be the other one.
--
-- The columns follow from this: a transition that cannot say who or which session must say so
-- in a value, not by leaving a column empty. So every column is NOT NULL with no default,
-- which pushes the decision onto the writer -- `coscc/units/history.py` substitutes its
-- `UNKNOWN` and nothing here can quietly accept a blank. `intent.md` exists because
-- "not known" already looks exactly like "did not happen"; a NULL here would be that
-- mistake written into the schema.
--
-- `machine` names the state set the row was written under. Without it, a database written
-- under one configuration and read under another compares states that never meant the
-- same thing, and that failure runs rather than stops.
--
-- `guard`, `authority`, `run` and `inputs` are added by `_COLUMNS`.
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
    """-- What makes an import re-runnable instead of doubling.
-- The key is the writer's: the git import derives one per commit and artifact, so running
-- it twice inserts nothing the second time. It is **partial** so that live transitions,
-- which pass no key, are never deduplicated -- two identical moves a minute apart are two
-- events, and an append-only log that silently dropped the second would be lying by
-- omission. Empty string rather than NULL keeps "no implicit blanks" true of every
-- column in the table.
CREATE UNIQUE INDEX IF NOT EXISTS transitions_once
    ON transitions (once_key) WHERE once_key <> ''""",
    """-- "How many files did this produce, and where". One table with a `kind` column
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
    """-- The master password, as an argon2id hash and nothing else. One row at
-- most, which the CHECK makes a property of the table rather than of every writer. Not a
-- `prefs` row: `prefs()` returns every row, and a Settings route that read widely would
-- hand the hash out. Times here are epoch seconds, unlike the ISO text elsewhere, because
-- every read of them is a comparison with "now".
CREATE TABLE IF NOT EXISTS auth (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    password_hash TEXT NOT NULL,
    set_at        INTEGER NOT NULL
)""",
    """-- One row per live login. Only the SHA-256 of the cookie's value is kept, so
-- a copy of this file is not a copy of anyone's session.
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_sha256 TEXT PRIMARY KEY,
    created_at   INTEGER NOT NULL,
    last_used_at INTEGER NOT NULL,
    expires_at   INTEGER NOT NULL
)""",
    """-- One row per board step's `run`, written when its recorder starts and kept
-- after its events are purged. Times are epoch milliseconds, the unit of an event's
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
    """-- Every event of a `run`, whole, as the JSON the recorder composed. Not
-- rows of `runs`: the board folds every row of that table on every read, and the run log
-- is append-only where a purge has to delete.
CREATE TABLE IF NOT EXISTS step_events (
    run   TEXT NOT NULL,
    seq   INTEGER NOT NULL,
    at    INTEGER NOT NULL,
    kind  TEXT NOT NULL,
    event TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    PRIMARY KEY (run, seq)
)""",
    # The owner's typed decisions and delegations, never used; Leif's knowledge replaces them.
    "DROP TABLE IF EXISTS decisions",
    """-- One row per directory under a store's `.cos/`, whatever its name --
-- `number` and `slug` are NULL for one that does not match NNNN_<slug>. `type` is the
-- word `intent.md` declares, or `unknown` until one is read. `lane` is `full` for every
-- unit and nothing reads it to decide. Status is not here: it is the fold over
-- `transitions`.
CREATE TABLE IF NOT EXISTS unit_meta (
    root        TEXT NOT NULL,
    workspace   TEXT NOT NULL,
    unit        TEXT NOT NULL,
    type        TEXT NOT NULL,
    lane        TEXT NOT NULL DEFAULT 'full',
    number      INTEGER,
    slug        TEXT,
    imported_at TEXT NOT NULL,
    PRIMARY KEY (root, workspace, unit)
)""",
    """-- `intent.md`'s `Idea:`, `Repo:` and each `Depends on:`, in the order written. Replaced
-- whole each time the intent is read. `repo` is here because the unit's line under its
-- idea's `## Units` is found by it.
CREATE TABLE IF NOT EXISTS unit_links (
    root      TEXT NOT NULL,
    workspace TEXT NOT NULL,
    unit      TEXT NOT NULL,
    kind      TEXT NOT NULL CHECK (kind IN ('idea', 'repo', 'depends')),
    ref       TEXT NOT NULL,
    pos       INTEGER NOT NULL
)""",
    """CREATE INDEX IF NOT EXISTS unit_links_scope ON unit_links (root, workspace, unit)""",
    """-- One row per file under `.cos/ideas/`: `read` is what `coscc.loop meta` read of it, as
-- JSON (`{title, status, units, problems}`), replaced whole on the next read. Not a column
-- per field: an idea has no transitions, and a `status` column here would be the current
-- state the transitions keep out of every table (`tests/units/test_history.py`).
CREATE TABLE IF NOT EXISTS idea_meta (
    root      TEXT NOT NULL,
    workspace TEXT NOT NULL,
    idea      TEXT NOT NULL,
    read      TEXT NOT NULL,
    PRIMARY KEY (root, workspace, idea)
)""",
    """-- The questions under an artifact's `## Open questions`, replaced per artifact on each
-- read. Whether the section is there at all is `unit_seen.questions`.
CREATE TABLE IF NOT EXISTS unit_questions (
    root      TEXT NOT NULL,
    workspace TEXT NOT NULL,
    unit      TEXT NOT NULL,
    artifact  TEXT NOT NULL,
    n         INTEGER NOT NULL,
    text      TEXT NOT NULL
)""",
    """CREATE INDEX IF NOT EXISTS unit_questions_scope ON unit_questions (root, workspace, unit)""",
    """-- A person's answer, to a question (`ref` its number) or to a review finding
-- (`ref` `F<k>`). Appended and never edited; the last for a `ref` is the one in force.
-- `once_key` is what makes the import re-runnable, as `transitions_once`.
CREATE TABLE IF NOT EXISTS unit_answers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    root        TEXT NOT NULL,
    workspace   TEXT NOT NULL,
    unit        TEXT NOT NULL,
    artifact    TEXT NOT NULL,
    ref         TEXT NOT NULL,
    text        TEXT NOT NULL,
    answered_by TEXT NOT NULL,
    date        TEXT NOT NULL,
    via         TEXT NOT NULL,
    once_key    TEXT NOT NULL DEFAULT ''
)""",
    """CREATE INDEX IF NOT EXISTS unit_answers_scope ON unit_answers (root, workspace, unit, id)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS unit_answers_once
    ON unit_answers (once_key) WHERE once_key <> ''""",
    """-- A hold decision, the `move` to `paused`, `dropped` or `active`. Appended; the
-- hold in force is the fold the loop makes over the rows by `HOLD_MOVES`.
CREATE TABLE IF NOT EXISTS unit_holds (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    root       TEXT NOT NULL,
    workspace  TEXT NOT NULL,
    unit       TEXT NOT NULL,
    move       TEXT NOT NULL,
    reason     TEXT NOT NULL,
    decided_by TEXT NOT NULL,
    date       TEXT NOT NULL,
    via        TEXT NOT NULL,
    once_key   TEXT NOT NULL DEFAULT ''
)""",
    """CREATE INDEX IF NOT EXISTS unit_holds_scope ON unit_holds (root, workspace, unit, id)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS unit_holds_once
    ON unit_holds (once_key) WHERE once_key <> ''""",
    """-- A field that could not be read, and why. `raw` is the word read, when there
-- was one (a status outside the artifact's set). `field` `ingest` is an ingest that failed;
-- the board shows it, `/settings` does not.
CREATE TABLE IF NOT EXISTS unit_unknowns (
    root      TEXT NOT NULL,
    workspace TEXT NOT NULL,
    unit      TEXT NOT NULL,
    artifact  TEXT NOT NULL,
    field     TEXT NOT NULL,
    reason    TEXT NOT NULL,
    raw       TEXT,
    at        TEXT NOT NULL
)""",
    """CREATE INDEX IF NOT EXISTS unit_unknowns_scope ON unit_unknowns (root, workspace, unit)""",
    """-- The text of each artifact as last read, by its SHA-256, so an ingest reads
-- only what changed. `questions` is 1 when it had a `## Open questions` section.
CREATE TABLE IF NOT EXISTS unit_seen (
    root      TEXT NOT NULL,
    workspace TEXT NOT NULL,
    unit      TEXT NOT NULL,
    artifact  TEXT NOT NULL,
    sha256    TEXT NOT NULL,
    questions INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (root, workspace, unit, artifact)
)""",
    """-- A stage's result as the `submit` tool received it. `object` is the whole
-- object as JSON; `judgement` is beside it so a guard can narrow without parsing. `revision`
-- is the SHA-256 the app took of the artifact when the object arrived. Where the
-- artifact stands is still the fold over `transitions`, never a column here.
CREATE TABLE IF NOT EXISTS stage_results (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL,
    root      TEXT NOT NULL,
    workspace TEXT NOT NULL,
    unit      TEXT NOT NULL,
    stage     TEXT NOT NULL,
    run       TEXT NOT NULL,
    revision  TEXT NOT NULL,
    judgement TEXT NOT NULL,
    object    TEXT NOT NULL
)""",
    """CREATE INDEX IF NOT EXISTS stage_results_scope ON stage_results (root, workspace, unit, id)""",
    """-- One review round. `head` is the SHA the app recorded when the run opened, never
-- one the model wrote; `screens` is the JSON list of images the round looked at.
CREATE TABLE IF NOT EXISTS review_rounds (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL,
    root      TEXT NOT NULL,
    workspace TEXT NOT NULL,
    unit      TEXT NOT NULL,
    n         INTEGER NOT NULL,
    run       TEXT NOT NULL,
    head      TEXT NOT NULL,
    verdict   TEXT NOT NULL,
    screens   TEXT NOT NULL DEFAULT '[]'
)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS review_rounds_n ON review_rounds (root, workspace, unit, n)""",
    """-- The findings of one round. `finding` is `F<k>`; `open` is 1 while the finding
-- is `[open]`, and `label` the word the round gave it (`open`, `fixed`, `needs-person`,
-- `claim-rejected`, `answered`), what a reader of that one round sees, not a status that
-- moves (`tests/units/test_history.py`). `rule` is `S<n>` or ''.
CREATE TABLE IF NOT EXISTS review_findings (
    round    INTEGER NOT NULL,
    finding  TEXT NOT NULL,
    open     INTEGER NOT NULL,
    label    TEXT NOT NULL,
    fixed_in TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL,
    rule     TEXT NOT NULL DEFAULT '',
    path     TEXT NOT NULL DEFAULT '',
    lines    TEXT NOT NULL DEFAULT '',
    text     TEXT NOT NULL,
    PRIMARY KEY (round, finding)
)""",
    """-- A finding `impl` claims only a person can close, by its round and id. The guard
-- checks each against the open findings of the last round before a row is written.
CREATE TABLE IF NOT EXISTS impl_claims (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL,
    root      TEXT NOT NULL,
    workspace TEXT NOT NULL,
    unit      TEXT NOT NULL,
    run       TEXT NOT NULL,
    round     INTEGER NOT NULL,
    finding   TEXT NOT NULL
)""",
    """CREATE INDEX IF NOT EXISTS impl_claims_scope ON impl_claims (root, workspace, unit, id)""",
    """-- What one read of a pull request found at one head -- the files its diff
-- names from the merge-base with `origin/main` (JSON, or NULL when they could not be read,
-- which counts as every file) and, once merged, the merge commit. Where the pull request
-- stands (`open`, `merge-requested`, `merged`, `closed`) and its CI are transitions of the
-- PR/CI machine, folded like any other.
CREATE TABLE IF NOT EXISTS pull_requests (
    root         TEXT NOT NULL,
    workspace    TEXT NOT NULL,
    unit         TEXT NOT NULL,
    number       INTEGER NOT NULL,
    head         TEXT NOT NULL,
    files        TEXT,
    merge_commit TEXT NOT NULL DEFAULT '',
    at           TEXT NOT NULL,
    PRIMARY KEY (root, workspace, number, head)
)""",
    """CREATE INDEX IF NOT EXISTS pull_requests_unit ON pull_requests (root, workspace, unit)""",
    """-- One try at a step, an integration, a hold, a review round or an estimate, from the
-- click to its end (`coscc/service/attempts.py`). What it is now is its last move, never a
-- column here; `stop_asked_at` is a Stop recorded, not a state. `workspace` is the journal
-- key; `run` the step's events once it launched; `road` an integration's, `rebase` or `gebo`.
-- `started_by`, `rerun` and `note` are what a queued one is launched with, by this process or
-- the next.
CREATE TABLE IF NOT EXISTS attempts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    machine       TEXT NOT NULL,
    workspace     TEXT NOT NULL,
    unit          TEXT NOT NULL,
    stage         TEXT NOT NULL DEFAULT '',
    slot          TEXT NOT NULL DEFAULT '',
    started_by    TEXT NOT NULL DEFAULT 'person',
    rerun         INTEGER NOT NULL DEFAULT 0,
    note          TEXT NOT NULL DEFAULT '',
    stop_asked_at TEXT,
    stop_asked_by TEXT,
    run           TEXT NOT NULL DEFAULT '',
    road          TEXT NOT NULL DEFAULT ''
)""",
    """CREATE INDEX IF NOT EXISTS attempts_unit ON attempts (workspace, unit)""",
    """-- Every move of an attempt, in order: the state it `moved_to`. `outcome` is an `ended` move's outcome or a
-- `refused` move's reason code, `''` for any other.
CREATE TABLE IF NOT EXISTS attempt_moves (
    attempt  INTEGER NOT NULL REFERENCES attempts (id),
    seq      INTEGER NOT NULL,
    moved_to TEXT NOT NULL,
    outcome  TEXT NOT NULL DEFAULT '',
    at       TEXT NOT NULL,
    PRIMARY KEY (attempt, seq)
)""",
    """CREATE INDEX IF NOT EXISTS attempt_moves_to ON attempt_moves (moved_to, attempt)""",
)

# Columns added to a table that already existed, as `(table, column, declaration)`. `_SCHEMA`
# cannot carry them: `CREATE TABLE IF NOT EXISTS` leaves an old table as it was. `_create`
# adds each one a table lacks. SQLite will not add a `NOT NULL` column without a default, so
# the default is the word a row that predates the column carries.
_COLUMNS = (
    # Which guard decided a transition, on whose authority, in which run, reading what (JSON: SHA, revision, PR number).
    ("transitions", "guard", "TEXT NOT NULL DEFAULT 'unknown'"),
    ("transitions", "authority", "TEXT NOT NULL DEFAULT 'unknown'"),
    ("transitions", "run", "TEXT NOT NULL DEFAULT 'unknown'"),
    ("transitions", "inputs", "TEXT NOT NULL DEFAULT '{}'"),
    # The head and the artifacts' revisions a run was handed when it opened.
    ("step_runs", "head", "TEXT NOT NULL DEFAULT ''"),
    ("step_runs", "revisions", "TEXT NOT NULL DEFAULT '{}'"),
    # Whose answer a row is: `person` or `agent` (an earlier version's precedent answers).
    ("unit_answers", "authority", "TEXT NOT NULL DEFAULT 'unknown'"),
    # The CI answer read at `ci_head`, written only with a `ci-at-head` transition; the checks
    # it was read from (JSON, for the names of the red ones) and when.
    ("pull_requests", "ci", "TEXT NOT NULL DEFAULT 'pending'"),
    ("pull_requests", "ci_head", "TEXT NOT NULL DEFAULT ''"),
    ("pull_requests", "ci_checks", "TEXT"),
    ("pull_requests", "ci_at", "TEXT NOT NULL DEFAULT ''"),
    # Who wrote an attempt's `note`: `person` (a rerun's) or `app` (what the autopilot hands a
    # step it queued: a draft to go on with, the red checks of a head).
    ("attempts", "note_by", "TEXT NOT NULL DEFAULT 'person'"),
)


class Unusable(RuntimeError):
    """`cos.db` cannot be used now: one of the three below."""


class Incompatible(Unusable):
    """The database on disk was written by a newer version of this app.

    Raised rather than worked around: guessing risks a corrupted history that looks fine.
    """


class Protected(Unusable):
    """This database belongs to the app that started this process, which must not open it.

    A step's code once migrated the running app's `cos.db` to a schema the app could not
    read. The app names its database in `config.PROTECTED_DB_VAR` for every child, and this
    is raised before a connection exists. A tripwire, not a lock: code that opens the file
    without `Data` walks past it.
    """


class Busy(Unusable):
    """Something else held the database past the timeout: an error that names the file, never a hang."""


def now() -> str:
    """UTC, second resolution. Shared so every table stamps time the same way."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Data:
    """One data directory: the database, the object folder, and the schema in between.

    Cheap to construct: it opens no connection until asked. Connections are per call, not
    pooled, because the sharing processes are separate OS processes.
    """

    def __init__(self, root: str | os.PathLike[str] | None = None):
        self.root = Path(root or DEFAULT_DIR).expanduser().resolve()
        self.db_path = self.root / DB_FILENAME
        # Threads inside one process still serialise here; SQLite keeps processes apart. It stops a
        # single process from spending its busy timeout fighting itself.
        self._lock = threading.Lock()

    # -- the directory ------------------------------------------------------

    def ensure_dir(self) -> Path:
        """Create the directory if it is not there."""
        self.root.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
        # `mkdir(mode=...)` is a no-op on an existing directory, which an older build may
        # have made `0o755`; tightening on every open covers it.
        try:
            os.chmod(self.root, DIR_MODE)
        except OSError:
            # A directory we cannot chmod is still usable.
            pass
        return self.root

    # -- connections --------------------------------------------------------

    @contextmanager
    def connect(self, timeout: float | None = None) -> Iterator[sqlite3.Connection]:
        """A connection with the schema present, WAL on, and a bounded wait.

        `isolation_level=None` turns off the driver's implicit transactions, so `write()` can
        say `BEGIN IMMEDIATE` and have it mean what it says.

        **`busy_timeout` is the first statement**: with `PRAGMA journal_mode=WAL` first, a
        four-writer test failed about one run in ten with `database is locked` out of the
        pragma itself, since changing the journal mode wants an exclusive lock and a
        connection not yet told to wait does not wait.

        Before any of that, a database listed in `config.PROTECTED_DB_VAR` is refused, reads
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

        Every caller that writes something derived from a read must use this, not `connect`.
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

        `busy_timeout` does not reliably cover changing the journal mode, which is what this is for.
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
        # WAL is a property of the database file: set once, read on every open. Reading it
        # needs no lock; setting it does.
        if str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
            self._retry(lambda: conn.execute("PRAGMA journal_mode=WAL"), wait)
        # Enforces nothing today (`_SCHEMA` declares no foreign key), but SQLite defaults it
        # off per connection, so a table added later would get no enforcement and no warning.
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
        # Equal is the whole common path: one pragma read. A lower number re-runs `_create`,
        # which is the whole migration mechanism: every `_SCHEMA` statement is `IF NOT EXISTS`
        # and `_COLUMNS` adds the columns. This works for *adding*; changing or dropping a
        # column needs a real migration.

    @staticmethod
    def _user_version(conn: sqlite3.Connection) -> int:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])

    def _create(self, conn: sqlite3.Connection) -> None:
        """Create the schema inside one transaction, re-checking under the lock.

        Several processes can reach this at once on a fresh data root: the first sets
        `user_version`, the rest re-read it inside `BEGIN IMMEDIATE` and find nothing to do.
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
                for table, column, declaration in _COLUMNS:
                    have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                    if column not in have:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
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
            return (
                conn.execute("SELECT 1 FROM migrations WHERE key = ?", (key,)).fetchone()
                is not None
            )
        with self.connect() as c:
            return (
                c.execute("SELECT 1 FROM migrations WHERE key = ?", (key,)).fetchone() is not None
            )

    def import_once(
        self,
        conn: sqlite3.Connection,
        key: str,
        source: Path,
        load: "Callable[[sqlite3.Connection], None]",
    ) -> None:
        """Run a one-shot import of `source`, inside the caller's transaction.

        Safe to call on every path in:

        - the common case exits after one `stat` and no query;
        - the `migrations` row is written **in the caller's transaction**, so an import that
          rolls back is not recorded as done;
        - the file is never deleted, so a wrong import stays recoverable.

        Only `load` differs between callers: what the file says, and what rows it becomes.
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

        A mark written in a second transaction could survive a rollback of the first.
        """
        conn.execute("INSERT OR IGNORE INTO migrations (key, at) VALUES (?, ?)", (key, now()))

    # -- preferences --------------------------------------------------------

    def pref(self, key: str, default: Any = None) -> Any:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM prefs WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except TypeError, ValueError:
            # Hand-edited into something unreadable: back to the default, not repaired.
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
            except TypeError, ValueError:
                continue
        return out

    def delete_pref(self, key: str) -> bool:
        """Remove one preference. True when there was one to remove."""
        with self.write() as conn:
            cur = conn.execute("DELETE FROM prefs WHERE key = ?", (key,))
            return cur.rowcount > 0

    def pref_rows(self, prefix: str) -> dict[str, str]:
        """Every preference whose key starts with `prefix`, **unparsed**.

        `prefs()` silently drops a row whose JSON will not parse, which is wrong for the model
        a stage runs on: `plan` would fall back with nobody told why. The caller parses and
        reports what it could not.

        Matched in Python, not with `LIKE`, so a `_` or `%` in the prefix means itself.
        """
        with self.connect() as conn:
            rows = conn.execute("SELECT key, value FROM prefs").fetchall()
        return {str(row["key"]): row["value"] for row in rows if str(row["key"]).startswith(prefix)}

    # -- the login ---------------------------------------------------------
    #
    # Only `coscc/auth.py` calls these. `prefs()` never reads these tables, so the hash has
    # no road out through Settings.

    def auth_password_hash(self) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT password_hash FROM auth WHERE id = 1").fetchone()
        return None if row is None else str(row["password_hash"])

    def auth_set_password(self, password_hash: str, now: int) -> bool:
        """Store the first password. False when one is already there.

        Checked and inserted under one `BEGIN IMMEDIATE`, so of two racing `POST /setup`
        exactly one wins, and the loser is a refusal rather than an overwrite.
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
        """Whether a password is set, and this session's row: one connection, one read.

        The guard asks this on every request, so `coscc reset-password` takes effect on the
        next request. The row is returned whatever its expiry; judging it is the caller's.
        """
        with self.connect() as conn:
            has_password = conn.execute("SELECT 1 FROM auth WHERE id = 1").fetchone() is not None
            row = (
                conn.execute(
                    "SELECT token_sha256, created_at, last_used_at, expires_at "
                    "FROM auth_sessions WHERE token_sha256 = ?",
                    (token_sha256,),
                ).fetchone()
                if token_sha256
                else None
            )
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

    # -- a step's events ----------------------------------------------------
    #
    # Written by `coscc/runlog/events.py`'s recorder from a thread, read by `Watch.events_page`.

    def step_run_open(
        self,
        run: str,
        root: str,
        workspace: str,
        unit: str,
        stage: str,
        started_at: int,
        timeout: float | None = None,
    ) -> None:
        with self.write(timeout=timeout) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO step_runs (run, root, workspace, unit, stage, started_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (run, root, workspace, unit, stage, int(started_at)),
            )

    def step_events_add(
        self,
        run: str,
        rows: list[dict[str, Any]],
        timeout: float | None = None,
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

    def step_run_close(
        self, run: str, ended_at: int, lost: int, timeout: float | None = None
    ) -> None:
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
        """How many `turn` events of `run` are stored: counted from what reached disk, not from memory."""
        with self.connect(timeout=timeout) as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM step_events WHERE run = ? AND kind = 'turn'", (run,)
                ).fetchone()[0]
            )

    def step_tool_uses(self, run: str, timeout: float | None = None) -> list[dict[str, Any]]:
        """Every stored `tool_use` event of `run`, oldest first: what a step that wrote nothing had opened."""
        with self.connect(timeout=timeout) as conn:
            rows = conn.execute(
                "SELECT event FROM step_events WHERE run = ? AND kind = 'tool_use' ORDER BY seq",
                (run,),
            ).fetchall()
        return [json.loads(r["event"]) for r in rows]

    def step_runs_open(self) -> list[dict[str, Any]]:
        """Every index row nobody closed and nobody purged, each with the `at` of its last stored event as `last_at` (None when it has none), oldest first."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT r.*, (SELECT MAX(e.at) FROM step_events e WHERE e.run = r.run) AS last_at "
                "FROM step_runs r WHERE r.ended_at IS NULL AND r.purged_at IS NULL "
                "ORDER BY r.started_at, r.run"
            ).fetchall()
        return [dict(row) for row in rows]

    def step_events_page(
        self, run: str, before: int | None, limit: int
    ) -> tuple[list[dict[str, Any]], bool]:
        """The last `limit` events with `seq < before` (all of them when `before` is None),
        oldest first, and whether any older one is stored."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT event FROM step_events WHERE run = ? AND seq < ? ORDER BY seq DESC LIMIT ?",
                (run, int(before) if before is not None else 2**62, int(limit)),
            ).fetchall()
            events = [json.loads(r["event"]) for r in reversed(rows)]
            older = (
                bool(events)
                and conn.execute(
                    "SELECT 1 FROM step_events WHERE run = ? AND seq < ? LIMIT 1",
                    (run, int(events[0]["seq"])),
                ).fetchone()
                is not None
            )
        return events, older

    def step_event(self, run: str, seq: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT event FROM step_events WHERE run = ? AND seq = ?", (run, int(seq))
            ).fetchone()
        return None if row is None else json.loads(row["event"])

    def step_events_purge(
        self, older_than_ms: int, max_bytes: int, now_iso: str
    ) -> tuple[int, int]:
        """Whole runs only, index rows kept with `purged_at`: first every run begun before
        `older_than_ms`, then the oldest while the stored total is over `max_bytes`. Returns
        `(runs, bytes)` purged. One transaction."""
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
