# Codegraph: a code index of `main` for impl and review

Read this before changing `coscc/features/codegraph/`: `__init__.py` holds the index's life,
the run records, the map, the tools, the Settings sentence and the routes; `graph.py` holds the
install, the Node bridge and the text the agent reads.

## States

`off` by default in every workspace. Settings offers `off`, `pilot` and `on`:

- `pilot` gives the feature to **half the units**: an even unit number is in the `on` arm, an
  odd one in the `off` arm. A unit's runs share its arm while the state stays.
- `on` puts every unit in the `on` arm.
- `off` stops the use: no tool, no block, no record, no Node process. It removes neither the
  install nor the index.

Picking `pilot` or `on` installs the engine once if it is missing, then builds the index. The
row is locked at `off`, its sentence saying why, when `npm` is missing and nothing is installed,
or when the installed engine's bundled Node is missing or outside `>=22.16.0 <25.0.0`. That
check runs once per app process, at the first run or pick that needs the engine; until the app
restarts, an install found broken keeps the feature off for every unit and is not installed
again. A failed install is tried again only when a person picks `pilot` or `on`.

The row says what the feature does (`summary`), then one status sentence. At `pilot` that
sentence tells the split (even-numbered units use it, odd ones do not), the units with records
in each arm so far, and the scoring day, `SCORING_DAY` in `__init__.py` (2026-11-15, shown as
`Nov 15`); it comes after the index state while the engine installs, the index builds, is ready
or has failed, and alone before the first index. The counts are the report's rule, a unit in both
arms counted in neither (`units_by_arm`), over all records, not a window.

## What the agent sees

- `impl`, `on` arm, index ready: the MCP server `codegraph` with `find` (entry points for
  words, or a file's symbols for a path), `callers` (direct call sites) and `impact` (files
  importing a file directly, tests apart). Each answer is at most 4 000 characters and says how
  many items it left out.
- `impl` and `review`, `on` arm, index ready: the block `codegraph-map`, at most 6 000
  characters. At impl, the outline and callers of the plan's files; at review, the symbols the
  diff touches and their callers outside the changed files. Review gets no tool.
- Every answer opens with the `main` commit the index is at, and marks each file this unit
  changed against it `changed in this unit: Read for current lines`. Guessed (`fuzzy`) edges,
  file-level nodes and dependents of dependents are left out.
- The `off` arm sees nothing.

## Hazards

- The index is of `main`, in a detached tree `worktrees/<slot>/_main` beside the units' trees;
  it shows in `git worktree list` of the workspace. `.codegraph/` there is untracked (its
`.gitignore` ignores all but itself); the tree moves past untracked files, and is left where it
is when a tracked file there changed or a commit was made on it. Nothing goes in
`info/exclude`. A read
  leaves `codegraph.db-shm` and `codegraph.db-wal` beside the db: a reader writes the shared
  memory index, never the db.
- Symbols a unit adds are not in the index, and lines of a changed file are `main`'s.
- The install is about 290 MB under the data directory, shared by every workspace; a build
  peaks near 900 MB of memory, so one build or sync runs at a time in the app. Queries take no
  lock.
- `npm ci --ignore-scripts` from the lockfile in `graph.py` is the only network call: it checks
  each package's sha512 against the lockfile. The engine ships its own Node, a third-party binary
  the app runs; review the lockfile like any new dependency.
- The bridge never runs on the `node` from `PATH`: Node 22.5 to 22.15 lacks `node:sqlite` or
  FTS5, or fails in a sync.
- A run waits at most 60 s for its index to follow `main`, then reads it as it is. A failed
  install, build or sync is on the Settings row and in the log.
- The record of each run (its arm, the index commit, the map size, the wait, any error) feeds
  `GET /api/codegraph/report?cwd=&since=&until=`. A unit whose runs fall in both arms is left
  out of both; a window wholly in `on` has no `off` arm and says `fail`.
