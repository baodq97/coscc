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

import coscc
from coscc import config

if TYPE_CHECKING:
    from collections.abc import Callable
from typing import Any, Iterator

# The shape below. `_open` refuses a database numbered higher than this rather than guessing, and
# **an older build answers `500` on a database a newer one has touched**, so rolling the app back
# means rolling the database back with it. 19 is idea 0006 whole: an empty database is created at
# it, one at `FROM` (0.14, the last release) takes `_from_12` in one step, any other is refused.
SCHEMA_VERSION = 19
FROM = 12

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
-- `guard` is which guard decided it, `authority` on whose word, `run` in which run, `inputs` what
-- it read (JSON: SHA, revision, PR number); `unknown` for a row older than the guards.
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
    once_key   TEXT NOT NULL DEFAULT '',
    guard      TEXT NOT NULL DEFAULT 'unknown',
    authority  TEXT NOT NULL DEFAULT 'unknown',
    run        TEXT NOT NULL DEFAULT 'unknown',
    inputs     TEXT NOT NULL DEFAULT '{}'
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
-- `events` and `bytes` count what was stored, `lost` what never was. `head` and `revisions` are the
-- head and the artifacts' revisions the run was handed when it opened.
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
    purged_at  TEXT,
    head       TEXT NOT NULL DEFAULT '',
    revisions  TEXT NOT NULL DEFAULT '{}'
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
    """-- One row per directory under a store's `.cos/`, whatever its name --
-- `number` and `slug` are NULL for one that does not match NNNN_<slug>. `type` is the
-- intent's record hands over (the word `intent.md` declares for a unit with no record),
-- or `unknown` until one is read. Status is not here: it is the fold over `transitions`. `process`
-- is the one the unit walks, `<pack>/<process>`, fixed when it is created.
CREATE TABLE IF NOT EXISTS unit_meta (
    root        TEXT NOT NULL,
    workspace   TEXT NOT NULL,
    unit        TEXT NOT NULL,
    type        TEXT NOT NULL,
    number      INTEGER,
    slug        TEXT,
    imported_at TEXT NOT NULL,
    process     TEXT NOT NULL DEFAULT 'coscc-sdlc/full',
    PRIMARY KEY (root, workspace, unit)
)""",
    """-- The idea a unit was opened from and each unit it depends on, in the order given. Written
-- once, by the press that creates the unit (`UnitMeta.link`); an idea's units are the rows
-- that name it.
CREATE TABLE IF NOT EXISTS unit_links (
    root      TEXT NOT NULL,
    workspace TEXT NOT NULL,
    unit      TEXT NOT NULL,
    kind      TEXT NOT NULL CHECK (kind IN ('idea', 'depends')),
    ref       TEXT NOT NULL,
    pos       INTEGER NOT NULL
)""",
    """CREATE INDEX IF NOT EXISTS unit_links_scope ON unit_links (root, workspace, unit)""",
    """-- The questions of an artifact's last record, replaced per artifact on each
-- record. An artifact with a record and no rows here has no question.
CREATE TABLE IF NOT EXISTS unit_questions (
    root      TEXT NOT NULL,
    workspace TEXT NOT NULL,
    unit      TEXT NOT NULL,
    artifact  TEXT NOT NULL,
    n         INTEGER NOT NULL,
    text      TEXT NOT NULL,
    recommendation TEXT NOT NULL DEFAULT ''
)""",
    """CREATE INDEX IF NOT EXISTS unit_questions_scope ON unit_questions (root, workspace, unit)""",
    """-- An answer, to a question (`ref` its number) or to a review finding (`ref` `F<k>`).
-- Appended and never edited; the last for a `ref` is the one in force. `by` is whose decision
-- it is, as sent: `person` (a person's press) or `delegated` (decided for them); `name` is the
-- name the caller gave. The default is `delegated`: a writer that forgot `by` never writes a
-- person's word.
CREATE TABLE IF NOT EXISTS unit_answers (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    root      TEXT NOT NULL,
    workspace TEXT NOT NULL,
    unit      TEXT NOT NULL,
    artifact  TEXT NOT NULL,
    ref       TEXT NOT NULL,
    text      TEXT NOT NULL,
    name      TEXT NOT NULL,
    date      TEXT NOT NULL,
    via       TEXT NOT NULL,
    once_key  TEXT NOT NULL DEFAULT '',
    "by"      TEXT NOT NULL DEFAULT 'delegated' CHECK ("by" IN ('person', 'delegated'))
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
    """-- A person's decision on a unit that is not an answer or a hold: run a stage again
-- (`rerun`, `fields` `{stage, stale: {file: record}}`), allow review more rounds
-- (`more-rounds`, `{rounds}`), or what the unit's outcome was (`outcome`, `{result,
-- measured_by, source, reason, note}`). Appended and never edited.
CREATE TABLE IF NOT EXISTS unit_decisions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    root       TEXT NOT NULL,
    workspace  TEXT NOT NULL,
    unit       TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('rerun', 'more-rounds', 'outcome')),
    fields     TEXT NOT NULL,
    decided_by TEXT NOT NULL,
    date       TEXT NOT NULL,
    via        TEXT NOT NULL
)""",
    """CREATE INDEX IF NOT EXISTS unit_decisions_scope ON unit_decisions (root, workspace, unit, id)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS unit_holds_once
    ON unit_holds (once_key) WHERE once_key <> ''""",
    """-- A record that could not be applied, and why: `field` is `ingest`. The board shows it.
CREATE TABLE IF NOT EXISTS unit_unknowns (
    root      TEXT NOT NULL,
    workspace TEXT NOT NULL,
    unit      TEXT NOT NULL,
    artifact  TEXT NOT NULL,
    field     TEXT NOT NULL,
    reason    TEXT NOT NULL,
    at        TEXT NOT NULL
)""",
    """CREATE INDEX IF NOT EXISTS unit_unknowns_scope ON unit_unknowns (root, workspace, unit)""",
    """-- An agent's output as the `submit` tool received it, under the contract version it was written to. `object` is the whole
-- object as JSON; `judgement` is beside it so a guard can narrow without parsing. `revision`
-- is the SHA-256 the app took of the artifact when the object arrived. Where the
-- artifact stands is still the fold over `transitions`, never a column here.
CREATE TABLE IF NOT EXISTS outputs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL,
    root      TEXT NOT NULL,
    workspace TEXT NOT NULL,
    unit      TEXT NOT NULL,
    agent     TEXT NOT NULL,
    run       TEXT NOT NULL,
    revision  TEXT NOT NULL,
    judgement TEXT NOT NULL,
    object    TEXT NOT NULL,
    version   INTEGER NOT NULL DEFAULT 1
)""",
    """CREATE INDEX IF NOT EXISTS outputs_scope ON outputs (root, workspace, unit, id)""",
    """-- Work an agent proposed for the Backlog (`coscc/units/proposals.py`): `unit` the unit it is
-- about ('' for none), `run` the run that made it, `sources` JSON, `decision` pending, accepted
-- or dismissed, `made` the unit accepting it made. Only the owner's press moves `decision` on.
CREATE TABLE IF NOT EXISTS proposals (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace TEXT NOT NULL,
    agent     TEXT NOT NULL,
    unit      TEXT NOT NULL DEFAULT '',
    run       TEXT NOT NULL DEFAULT '',
    type      TEXT NOT NULL,
    slug      TEXT NOT NULL,
    title     TEXT NOT NULL,
    problem   TEXT NOT NULL,
    sources   TEXT NOT NULL,
    decision  TEXT NOT NULL DEFAULT 'pending',
    made      TEXT NOT NULL DEFAULT '',
    by        TEXT NOT NULL DEFAULT '',
    at        TEXT NOT NULL,
    decided   TEXT NOT NULL DEFAULT '',
    reason    TEXT NOT NULL DEFAULT ''
)""",
    """CREATE INDEX IF NOT EXISTS proposals_scope ON proposals (workspace, id)""",
    """-- A run an event asked for after a delay (`coscc/runner/triggers.py`): due at `due_at`,
-- kept across a restart until the tick runs it.
CREATE TABLE IF NOT EXISTS trigger_due (
    workspace TEXT NOT NULL,
    agent     TEXT NOT NULL,
    unit      TEXT NOT NULL DEFAULT '',
    due_at    TEXT NOT NULL,
    event     TEXT NOT NULL,
    PRIMARY KEY (workspace, agent, unit)
)""",
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
    screens   TEXT NOT NULL DEFAULT '[]',
    criteria  TEXT NOT NULL DEFAULT '[]'
)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS review_rounds_n ON review_rounds (root, workspace, unit, n)""",
    """-- The findings of one round. `finding` is `F<k>`; `open` is 1 while the finding
-- is `[open]`, and `label` the word the round gave it (`open`, `fixed`, `needs-person`,
-- `claim-rejected`, `answered`), what a reader of that one round sees, not a status that
-- moves (`tests/units/test_history.py`). `criterion` is the one the round graded it under.
CREATE TABLE IF NOT EXISTS review_findings (
    round    INTEGER NOT NULL,
    finding  TEXT NOT NULL,
    open     INTEGER NOT NULL,
    label    TEXT NOT NULL,
    fixed_in TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL,
    criterion TEXT NOT NULL DEFAULT '',
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
-- PR/CI machine, folded like any other; `ci` is the answer read at `ci_head`, written only with a
-- `ci-at-head` transition, from the checks `ci_checks` (JSON) at `ci_at`.
CREATE TABLE IF NOT EXISTS pull_requests (
    root         TEXT NOT NULL,
    workspace    TEXT NOT NULL,
    unit         TEXT NOT NULL,
    number       INTEGER NOT NULL,
    head         TEXT NOT NULL,
    files        TEXT,
    merge_commit TEXT NOT NULL DEFAULT '',
    at           TEXT NOT NULL,
    ci           TEXT NOT NULL DEFAULT 'pending',
    ci_head      TEXT NOT NULL DEFAULT '',
    ci_checks    TEXT,
    ci_at        TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (root, workspace, number, head)
)""",
    """CREATE INDEX IF NOT EXISTS pull_requests_unit ON pull_requests (root, workspace, unit)""",
    """-- One try at a step, an integration, a hold, a review round or an estimate, from the
-- click to its end (`coscc/runner/queue.py`). What it is now is its last move, never a
-- column here; `stop_asked_at` is a Stop recorded, not a state. `workspace` is the journal
-- key; `run` the step's events once it launched; `road` an integration's, `rebase` or `gebo`.
-- `started_by`, `rerun` and `note` are what a queued one is launched with, by this process or
-- the next; `note_by` who wrote `note`: `person` (a rerun's) or `app` (what the autopilot hands a
-- step it queued).
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
    road          TEXT NOT NULL DEFAULT '',
    note_by       TEXT NOT NULL DEFAULT 'person'
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


# The prefs `_from_12` moves into the pack, and the one row renamed on the way.
_MOVED = ("agent", "model", "effort", "turns", "budget")
_RENAMED = {"chat": "leif"}
# Where each moved pref lands in a row: `(top key, sub key)`; `agent`'s fields are their own.
_LANDS = {
    "model": ("model", "id"),
    "effort": ("model", "effort"),
    "turns": ("ceilings", "turns"),
    "budget": ("ceilings", "usd"),
}
_IDENTITY = {"glyph": "glyph", "name": "name", "meaning": "description", "role": "body"}


def _front(text: str) -> dict[str, Any]:
    """A built-in row's frontmatter: its `key: <JSON>` lines between the two `---`."""
    head = text.split("\n---", 1)[0].removeprefix("---\n")
    out: dict[str, Any] = {}
    for line in head.splitlines():
        key, sep, value = line.partition(":")
        if sep and key and not key.startswith("#"):
            out[key] = json.loads(value)
    return out


def _owner_row(
    built: dict[str, Any], given: dict[tuple[str, str], Any]
) -> tuple[dict[str, Any], str]:
    """What the owner's layer of one row holds for the prefs `given` (`{(prefix, variant):
    value}`): each top-level key whose value then differs from `built`, and the body."""
    after = json.loads(json.dumps(built))
    body = ""
    for (prefix, variant), value in given.items():
        if prefix == "agent":
            for field, text in (value if isinstance(value, dict) else {}).items():
                if field == "role":
                    body = str(text)
                elif field in _IDENTITY:
                    after[_IDENTITY[field]] = text
            continue
        top, sub = _LANDS[prefix]
        if variant:
            holder = after.setdefault("variants", {}).setdefault(variant, {})
        else:
            holder = after
        holder.setdefault(top, {})[sub] = value
    return {k: v for k, v in after.items() if built.get(k) != v}, body


def _builtin() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """The built-in pack as `_from_12` reads it (this layer sits below `coscc.agent.pack`): the
    states of its default process and each row's frontmatter."""
    root = Path(coscc.__file__).resolve().parent / "packs" / "coscc-sdlc"
    process = json.loads((root / "process.json").read_text(encoding="utf-8"))
    rows = {
        p.stem: _front(p.read_text(encoding="utf-8"))
        for p in sorted((root / "agents").glob("*.md"))
    }
    return process["processes"]["full"]["states"], rows


def _output(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("output") or {}


def _with(rows: dict[str, dict[str, Any]], field: str) -> list[str]:
    """The rows whose output declares `field`."""
    return [
        k
        for k, r in rows.items()
        if field in {f.rstrip("?") for f in _output(r).get("fields") or {}}
    ]


def _coders(rows: dict[str, dict[str, Any]]) -> list[str]:
    """The rows that write in the unit's branch: an artifact by the session."""
    return [
        k
        for k, r in rows.items()
        if _output(r).get("by") == "session" and _output(r).get("kind") == "artifact"
    ]


def _marks(keys: list[str]) -> str:
    return ", ".join("?" * len(keys))


def _records(conn: sqlite3.Connection, rows: dict[str, dict[str, Any]]) -> None:
    """Each stored record at its row's current contract (`_from_12`), the keys in the order the
    steps it replaces left them."""
    asking, measured = _with(rows, "questions"), _with(rows, "unmeasured")
    for key in _with(rows, "type"):
        conn.execute(
            "UPDATE outputs SET object = json_set(object, '$.type', "
            "COALESCE((SELECT type FROM unit_meta m WHERE m.root = outputs.root "
            "AND m.workspace = outputs.workspace AND m.unit = outputs.unit), 'unknown')) "
            "WHERE agent = ?",
            (key,),
        )
    for key in _with(rows, "rests_on"):
        conn.execute(
            "UPDATE outputs SET object = json_remove(json_set(json_set(object, "
            "'$.impl', 'novel', '$.files', json('[]'), '$.steps', json('[]'), '$.rests_on', "
            "json(COALESCE((SELECT json_extract(s.object, '$.unmeasured') FROM outputs s "
            "WHERE s.root = outputs.root AND s.workspace = outputs.workspace "
            f"AND s.unit = outputs.unit AND s.agent IN ({_marks(measured)}) "
            "ORDER BY s.id DESC LIMIT 1), '[]'))), '$.variant', 'novel'), '$.impl') "
            "WHERE agent = ?",
            (*measured, key),
        )
    conn.execute(
        "UPDATE outputs SET object = json_set(object, '$.questions', "
        "(SELECT json_group_array(json_set(value, '$.recommendation', '')) "
        "FROM json_each(outputs.object, '$.questions'))) "
        f"WHERE json_type(object, '$.questions') = 'array' AND agent IN ({_marks(asking)})",
        asking,
    )
    for key in asking:
        conn.execute(
            "UPDATE outputs SET version = ? WHERE agent = ?",
            (int(_output(rows[key]).get("version") or 1), key),
        )


def _scan_moves(
    conn: sqlite3.Connection, rows: dict[str, dict[str, Any]], tables: set[str]
) -> None:
    """The scan feature is a row of the pack, the one proposing on a schedule. Its proposals move
    into `proposals` under that row; whether it was on in a workspace moves from `features.state`
    (a schedule of `0` hours is off) into `agents.state`; its cursor lands as `data_until` on its
    last `end`; `features.schedule` and its tables go. The feature had the row's key as its name."""
    scan = next(
        (
            k
            for k, r in rows.items()
            if _output(r).get("kind") == "proposal" and (r.get("trigger") or {}).get("schedule")
        ),
        "",
    )
    if f"{scan}_proposals" in tables:
        conn.execute(
            "INSERT INTO proposals (workspace, agent, unit, run, type, slug, title, problem, "
            "sources, decision, made, by, at, decided, reason) SELECT workspace, ?, '', '', "
            "type, slug, title, problem, sources, state, unit, by, at, decided, reason "
            f"FROM {scan}_proposals ORDER BY id",
            (scan,),
        )
    if f"{scan}_cursor" in tables:
        for workspace, after in conn.execute(f"SELECT workspace, after FROM {scan}_cursor"):
            conn.execute(
                "UPDATE runs SET record = json_set(record, '$.data_until', ?) WHERE id = "
                "(SELECT id FROM runs WHERE workspace = ? AND kind = 'end' AND stage = ? "
                "ORDER BY id DESC LIMIT 1)",
                (after, workspace, scan),
            )
    prefs = dict(conn.execute("SELECT key, value FROM prefs").fetchall())

    def _read(key: str) -> dict[str, Any]:
        try:
            got = json.loads(prefs.get(key) or "{}")
        except ValueError:
            return {}
        return got if isinstance(got, dict) else {}

    states, hours = _read("features.state"), _read("features.schedule")
    chosen = states.pop(scan, None)
    moved = {
        ws: "off" if state == "off" or hours.get(scan, {}).get(ws) == 0 else "on"
        for ws, state in (chosen if isinstance(chosen, dict) else {}).items()
    }
    if moved:
        agents = _read("agents.state")
        agents[scan] = {**(agents.get(scan) or {}), **moved}
        conn.execute(
            "INSERT INTO prefs (key, value) VALUES ('agents.state', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (json.dumps(agents),),
        )
    if chosen is not None:
        conn.execute(
            "UPDATE prefs SET value = ? WHERE key = 'features.state'", (json.dumps(states),)
        )
    conn.execute("DELETE FROM prefs WHERE key = 'features.schedule'")
    for table in ("proposals", "runs", "cursor"):
        conn.execute(f"DROP TABLE IF EXISTS {scan}_{table}")


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
                if found not in (0, FROM):
                    raise Incompatible(
                        f"{self.db_path} is at schema {found}, and this build migrates only "
                        f"schema {FROM}: upgrade through 0.14 first, or start from a copy made "
                        f"at {FROM}"
                    )
                if found == FROM:
                    self._from_12(conn)
                else:
                    for statement in _SCHEMA:
                        conn.execute(statement)
                # Not parameterisable; `SCHEMA_VERSION` is this module's own integer.
                conn.execute(f"PRAGMA user_version={int(SCHEMA_VERSION)}")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    def _from_12(self, conn: sqlite3.Connection) -> None:
        """12 (0.14) to this schema in one step: idea 0006 whole.

        - `stage_results` is `outputs` (column `agent`, a `version`); the removed decisions
          feature's `unit_decisions`, `idea_meta`, `unit_seen`, `unit_meta.lane`, the `repo` links,
          the `raw` unknowns and the import's `unit-meta:` marks go; a review finding's `rule` is
          its `criterion`; a unit walks the default process.
        - Records move to their current contract: the intent's gains the unit's `type`, the plan's
          a `novel` `variant`, empty `files` and `steps` and `rests_on` (the unit's latest spec
          record's `unmeasured`, so no stored gate changes), every question a `recommendation`.
        - A stored `done` is `accepted`; an `exhausted` run is `failed`. An answer's `answered_by`
          is `name`, and `by` replaces `authority`: `delegated` where `authority` was not
          `person` or the name opens with Leif, Claude or agent, else `person`.
        - The agent prefs are the owner's layer of the pack (`_owner_row`); the scan feature's
          proposals, state and cursor are its row's; a merge's record is `merge`; the vault's
          per-secret list, in its column and its `policy` records, is `agents`.

        Every agent and state is found by what the built-in pack says it is, never by its name.
        """
        states, rows = _builtin()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.execute("ALTER TABLE stage_results RENAME TO outputs")
        conn.execute("ALTER TABLE outputs RENAME COLUMN stage TO agent")
        conn.execute("ALTER TABLE outputs ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
        conn.execute("DROP INDEX IF EXISTS stage_results_scope")
        for table in ("unit_decisions", "idea_meta", "unit_seen"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        links = conn.execute(
            "SELECT root, workspace, unit, kind, ref, pos FROM unit_links WHERE kind != 'repo'"
        ).fetchall()
        conn.execute("DROP TABLE unit_links")
        conn.execute("ALTER TABLE review_findings RENAME COLUMN rule TO criterion")
        conn.execute("ALTER TABLE review_rounds ADD COLUMN criteria TEXT NOT NULL DEFAULT '[]'")
        conn.execute("ALTER TABLE unit_meta DROP COLUMN lane")
        conn.execute(
            "ALTER TABLE unit_meta ADD COLUMN process TEXT NOT NULL DEFAULT 'coscc-sdlc/full'"
        )
        conn.execute("DELETE FROM unit_unknowns WHERE field <> 'ingest'")
        conn.execute("ALTER TABLE unit_unknowns DROP COLUMN raw")
        conn.execute(
            "ALTER TABLE unit_questions ADD COLUMN recommendation TEXT NOT NULL DEFAULT ''"
        )
        conn.execute("ALTER TABLE unit_answers RENAME COLUMN answered_by TO name")
        conn.execute(
            "ALTER TABLE unit_answers ADD COLUMN \"by\" TEXT NOT NULL DEFAULT 'delegated' "
            "CHECK (\"by\" IN ('person', 'delegated'))"
        )
        agent_named = " OR ".join(
            f"(lower(trim(name)) = '{n}' OR (lower(trim(name)) LIKE '{n}%' AND "
            f"substr(lower(trim(name)), {len(n) + 1}, 1) NOT BETWEEN 'a' AND 'z'))"
            for n in ("leif", "claude", "agent")
        )
        conn.execute(
            "UPDATE unit_answers SET \"by\" = 'person' "
            f"WHERE authority IN ('person', '') AND NOT ({agent_named})"
        )
        conn.execute("ALTER TABLE unit_answers DROP COLUMN authority")
        if "vault_secrets" in tables:
            conn.execute("ALTER TABLE vault_secrets RENAME COLUMN stages TO agents")
        conn.execute(
            "UPDATE runs SET record = json_remove(json_set(record, '$.agents', "
            "json(json_extract(record, '$.stages'))), '$.stages') "
            "WHERE kind = 'vault' AND json_type(record, '$.stages') IS NOT NULL"
        )
        for statement in _SCHEMA:
            conn.execute(statement)
        conn.executemany("INSERT INTO unit_links VALUES (?, ?, ?, ?, ?, ?)", links)
        conn.execute("DELETE FROM migrations WHERE key LIKE 'unit-meta:%'")
        conn.execute("UPDATE transitions SET to_state = 'accepted' WHERE to_state = 'done'")
        conn.execute("UPDATE transitions SET from_state = 'accepted' WHERE from_state = 'done'")
        conn.execute(
            "UPDATE runs SET record = json_set(record, '$.outcome', 'failed', '$.status', 'failed') "
            "WHERE json_extract(record, '$.outcome') = 'exhausted'"
        )
        conn.execute(
            "UPDATE step_events SET event = json_set(event, '$.outcome', 'failed') "
            "WHERE kind = 'end' AND json_extract(event, '$.outcome') = 'exhausted'"
        )
        conn.execute("UPDATE attempt_moves SET outcome = 'failed' WHERE outcome = 'exhausted'")
        _records(conn, rows)
        # A merge's run-log record was named after the state that merges; it is the action's now.
        for state, found in states.items():
            if found.get("action") == "merge":
                conn.execute(
                    "UPDATE runs SET kind = ?, record = json_set(record, '$.kind', ?) "
                    "WHERE kind = ?",
                    (found["action"], found["action"], state),
                )
        self._agent_prefs(conn, rows)
        _scan_moves(conn, rows, tables)

    def _agent_prefs(self, conn: sqlite3.Connection, rows: dict[str, dict[str, Any]]) -> None:
        """Every `agent:`, `model:`, `effort:`, `turns:` and `budget:` pref becomes the owner's layer
        of the pack, `<root>/packs/local/agents/<key>.md`, holding only what differs from the
        built-in row, and the prefs go. `chat` is `leif`, `<key>:novel` the row's `novel` variant;
        Gebo, which ran the coder's model and effort when it had none of its own, keeps them. A key
        no built-in row has changed nothing and is dropped."""
        prefs = conn.execute("SELECT key, value FROM prefs").fetchall()
        found: dict[str, dict[tuple[str, str], Any]] = {}
        for key, raw in prefs:
            prefix, _, name = str(key).partition(":")
            if prefix not in _MOVED or not name:
                continue
            try:
                value = json.loads(raw)
            except ValueError:
                continue
            base, _, variant = name.partition(":")
            found.setdefault(_RENAMED.get(base, base), {})[(prefix, variant)] = value
        gebo = found.setdefault("integrate", {})
        for coder in _coders(rows):
            for field in ("model", "effort"):
                if (field, "") in found.get(coder, {}) and (field, "") not in gebo:
                    gebo[(field, "")] = found[coder][(field, "")]
        for key, given in found.items():
            if not given or key not in rows:
                continue
            fields, body = _owner_row(rows[key], given)
            if fields or body:
                path = self.root / "packs" / "local" / "agents" / f"{key}.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                lines = [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in fields.items()]
                path.write_text(
                    "---\n" + "\n".join(lines) + "\n---\n" + (f"{body}\n" if body else ""),
                    encoding="utf-8",
                )
        for key, _ in prefs:
            if str(key).partition(":")[0] in _MOVED:
                conn.execute("DELETE FROM prefs WHERE key = ?", (key,))

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
    # Only `coscc/http/auth.py` calls these. `prefs()` never reads these tables, so the hash has
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
