-- The schema of a database at 12 (0.15, the last release), as the real one has it: the start of `_from_12`.

CREATE TABLE attempt_moves (
    attempt  INTEGER NOT NULL REFERENCES attempts (id),
    seq      INTEGER NOT NULL,
    moved_to TEXT NOT NULL,
    outcome  TEXT NOT NULL DEFAULT '',
    at       TEXT NOT NULL,
    PRIMARY KEY (attempt, seq)
);

CREATE TABLE attempts (
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
, note_by TEXT NOT NULL DEFAULT 'person');

CREATE TABLE auth (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    password_hash TEXT NOT NULL,
    set_at        INTEGER NOT NULL
);

CREATE TABLE auth_sessions (
    token_sha256 TEXT PRIMARY KEY,
    created_at   INTEGER NOT NULL,
    last_used_at INTEGER NOT NULL,
    expires_at   INTEGER NOT NULL
);

CREATE TABLE codegraph_index (workspace TEXT PRIMARY KEY, path TEXT NOT NULL, state TEXT NOT NULL, sha TEXT NOT NULL DEFAULT '', at TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', root TEXT NOT NULL DEFAULT '');

CREATE TABLE codegraph_runs (run TEXT PRIMARY KEY, workspace TEXT NOT NULL, unit TEXT NOT NULL, stage TEXT NOT NULL, arm TEXT NOT NULL, sha TEXT NOT NULL, map_chars INTEGER NOT NULL, wait_ms INTEGER NOT NULL, error TEXT NOT NULL, at TEXT NOT NULL);

CREATE TABLE idea_meta (
    root      TEXT NOT NULL,
    workspace TEXT NOT NULL,
    idea      TEXT NOT NULL,
    read      TEXT NOT NULL,
    PRIMARY KEY (root, workspace, idea)
);

CREATE TABLE impl_claims (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL,
    root      TEXT NOT NULL,
    workspace TEXT NOT NULL,
    unit      TEXT NOT NULL,
    run       TEXT NOT NULL,
    round     INTEGER NOT NULL,
    finding   TEXT NOT NULL
);

CREATE TABLE migrations (
    key TEXT PRIMARY KEY,
    at  TEXT NOT NULL
);

CREATE TABLE prefs (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE pull_requests (
    root         TEXT NOT NULL,
    workspace    TEXT NOT NULL,
    unit         TEXT NOT NULL,
    number       INTEGER NOT NULL,
    head         TEXT NOT NULL,
    files        TEXT,
    merge_commit TEXT NOT NULL DEFAULT '',
    at           TEXT NOT NULL, ci TEXT NOT NULL DEFAULT 'pending', ci_head TEXT NOT NULL DEFAULT '', ci_checks TEXT, ci_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (root, workspace, number, head)
);

CREATE TABLE review_findings (
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
);

CREATE TABLE review_rounds (
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
);

CREATE TABLE runs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL,
    root      TEXT NOT NULL,
    workspace TEXT NOT NULL DEFAULT '',
    unit      TEXT NOT NULL DEFAULT '',
    stage     TEXT NOT NULL DEFAULT '',
    kind      TEXT NOT NULL,
    record    TEXT NOT NULL
);

CREATE TABLE scan_cursor (
    workspace TEXT PRIMARY KEY,
    after     TEXT NOT NULL,
    seen      TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE scan_proposals (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace TEXT NOT NULL,
    run       INTEGER NOT NULL,
    type      TEXT NOT NULL,
    slug      TEXT NOT NULL,
    title     TEXT NOT NULL,
    problem   TEXT NOT NULL,
    sources   TEXT NOT NULL,
    state     TEXT NOT NULL DEFAULT 'pending',
    unit      TEXT NOT NULL DEFAULT '',
    by        TEXT NOT NULL DEFAULT '',
    at        TEXT NOT NULL,
    decided   TEXT NOT NULL DEFAULT '',
    reason    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE scan_runs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace TEXT NOT NULL,
    at        TEXT NOT NULL,
    by        TEXT NOT NULL,
    outcome   TEXT NOT NULL,
    cost_usd  REAL NOT NULL DEFAULT 0,
    session   TEXT NOT NULL DEFAULT '',
    taken     INTEGER NOT NULL DEFAULT 0,
    cut       INTEGER NOT NULL DEFAULT 0,
    rejected  TEXT NOT NULL DEFAULT '[]',
    stopped   INTEGER NOT NULL DEFAULT 0,
    detail    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE stage_results (
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
);

CREATE TABLE step_events (
    run   TEXT NOT NULL,
    seq   INTEGER NOT NULL,
    at    INTEGER NOT NULL,
    kind  TEXT NOT NULL,
    event TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    PRIMARY KEY (run, seq)
);

CREATE TABLE step_runs (
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
, head TEXT NOT NULL DEFAULT '', revisions TEXT NOT NULL DEFAULT '{}');

CREATE TABLE transitions (
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
, guard TEXT NOT NULL DEFAULT 'unknown', authority TEXT NOT NULL DEFAULT 'unknown', run TEXT NOT NULL DEFAULT 'unknown', inputs TEXT NOT NULL DEFAULT '{}');

CREATE TABLE unit_answers (
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
, authority TEXT NOT NULL DEFAULT 'unknown');

CREATE TABLE unit_decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace   TEXT NOT NULL,
    unit        TEXT NOT NULL,
    text        TEXT NOT NULL,
    authority   TEXT NOT NULL,
    recorded_by TEXT NOT NULL,
    date        TEXT NOT NULL,
    delegation  TEXT NOT NULL DEFAULT '',
    reverses    TEXT NOT NULL DEFAULT '',
    withdrawn   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE unit_holds (
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
);

CREATE TABLE unit_links (
    root      TEXT NOT NULL,
    workspace TEXT NOT NULL,
    unit      TEXT NOT NULL,
    kind      TEXT NOT NULL CHECK (kind IN ('idea', 'repo', 'depends')),
    ref       TEXT NOT NULL,
    pos       INTEGER NOT NULL
);

CREATE TABLE unit_meta (
    root        TEXT NOT NULL,
    workspace   TEXT NOT NULL,
    unit        TEXT NOT NULL,
    type        TEXT NOT NULL,
    lane        TEXT NOT NULL DEFAULT 'full',
    number      INTEGER,
    slug        TEXT,
    imported_at TEXT NOT NULL,
    PRIMARY KEY (root, workspace, unit)
);

CREATE TABLE unit_questions (
    root      TEXT NOT NULL,
    workspace TEXT NOT NULL,
    unit      TEXT NOT NULL,
    artifact  TEXT NOT NULL,
    n         INTEGER NOT NULL,
    text      TEXT NOT NULL
);

CREATE TABLE unit_seen (
    root      TEXT NOT NULL,
    workspace TEXT NOT NULL,
    unit      TEXT NOT NULL,
    artifact  TEXT NOT NULL,
    sha256    TEXT NOT NULL,
    questions INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (root, workspace, unit, artifact)
);

CREATE TABLE unit_unknowns (
    root      TEXT NOT NULL,
    workspace TEXT NOT NULL,
    unit      TEXT NOT NULL,
    artifact  TEXT NOT NULL,
    field     TEXT NOT NULL,
    reason    TEXT NOT NULL,
    raw       TEXT,
    at        TEXT NOT NULL
);

CREATE TABLE vault_grants (
    name      TEXT NOT NULL,
    workspace TEXT NOT NULL,
    PRIMARY KEY (name, workspace)
);

CREATE TABLE vault_secrets (
    name        TEXT NOT NULL,
    workspace   TEXT NOT NULL,
    description TEXT NOT NULL,
    stages      TEXT NOT NULL,
    modes       TEXT NOT NULL,
    broker      INTEGER NOT NULL,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    has_value   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (name, workspace)
);

CREATE TABLE workspaces (
    root     TEXT NOT NULL,
    name     TEXT NOT NULL,
    label    TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL,
    PRIMARY KEY (root, name)
);

CREATE INDEX attempt_moves_to ON attempt_moves (moved_to, attempt);

CREATE INDEX attempts_unit ON attempts (workspace, unit);

CREATE INDEX impl_claims_scope ON impl_claims (root, workspace, unit, id);

CREATE INDEX pull_requests_unit ON pull_requests (root, workspace, unit);

CREATE UNIQUE INDEX review_rounds_n ON review_rounds (root, workspace, unit, n);

CREATE INDEX runs_scope ON runs (root, workspace, unit, id);

CREATE INDEX stage_results_scope ON stage_results (root, workspace, unit, id);

CREATE INDEX step_runs_scope ON step_runs (root, workspace, unit, started_at);

CREATE UNIQUE INDEX transitions_once
    ON transitions (once_key) WHERE once_key <> '';

CREATE INDEX transitions_scope ON transitions (root, workspace, unit, id);

CREATE UNIQUE INDEX unit_answers_once
    ON unit_answers (once_key) WHERE once_key <> '';

CREATE INDEX unit_answers_scope ON unit_answers (root, workspace, unit, id);

CREATE UNIQUE INDEX unit_holds_once
    ON unit_holds (once_key) WHERE once_key <> '';

CREATE INDEX unit_holds_scope ON unit_holds (root, workspace, unit, id);

CREATE INDEX unit_links_scope ON unit_links (root, workspace, unit);

CREATE INDEX unit_questions_scope ON unit_questions (root, workspace, unit);

CREATE INDEX unit_unknowns_scope ON unit_unknowns (root, workspace, unit);
