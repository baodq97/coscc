# The knowledge store: what no test here catches

`.cos/0090_agents-relearn-what-earlier-units-already-knew`. `COS_KNOWLEDGE=1` hands `spec`,
`spike` and `plan` the entries of `<COS_DATA_DIR>/knowledge/knowledge.md` that apply to the
workspace (`coscc/knowledge.py`); `coscc knowledge gather` writes that file
(`coscc/gather.py`); `coscc knowledge baseline` and `measure` decide whether it paid
(`coscc/measure.py`).

## Gathering spends quota, and its ceiling is not a ceiling

- Each batch is one paid session under the grant `knowledge` (1 turn, $2.00, chosen, not
  measured). The CLI compares the session's cost after the turn has run (`0085`
  `spike.md ## U2`), so one batch can pass $2.00; the figure `gather` prints before
  `--yes` is batches × $2.00, not a bound.
- Nobody has counted the `spike.md` and `review.md` files the history holds, so what
  `gather --all` costs is unknown until it prints its batch count.
- `gather --all` writes nothing until every batch has passed. A batch that fails at the end
  has spent every batch before it for a store that did not change.
- A batch whose reply the check refuses is sent again by every later `gather`, since the
  manifest records only what was written. Nothing skips it; read its `reason` in the
  `knowledge` rows of `cos.db` and decide.
- A store edited by hand into a block `parse` cannot read — no `Scope:`, no `Source:`, no
  statement, or a `## ` heading that is not `## K<n>` — refuses every `gather`, dry run
  included, until the block is fixed or removed: a save renders only what was read. So
  does a line outside every entry but the title and the header, such as a note under it.
- Entries under no header refuse `gather` too: the header's `Max id` is the only record of
  an id given out and since dropped. New ids also start above every id a `done`
  `knowledge` row of the run log dropped, but only rows under this `COS_WORKING_DIR`; a
  header edited below an id deleted by hand is caught by nothing.
- The session runs in the store's directory and `Sessions.membership` is narrowed to it.
  It has no tool; give the grant one and the session reads everything there.

## What the check does not check

`knowledge.validate` holds the byte caps, the ids, the scopes and that every `Source:` is a
source the session was given. It cannot tell a wrong entry from a right one (`spec.md` C2):
a wrong entry now reaches every `spec`, `spike` and `plan`. The way back is to unset
`COS_KNOWLEDGE` and restart; the store stays where it is and nothing reads it.

Since `0108`, `coscc/admit.py` writes each entry's date and version and drops the rest into
the batch's `dropped`; `coscc knowledge check` reads the store against `main`.

- `check` checks that what an entry points at exists — a pinned version, a path, a name in
  a file — never that its sentence is still true. An entry about a file that still exists
  passes whatever it says of it.
- A tool no workspace pins in `.python-version` or `uv.lock` — `gh`, `git`, the Claude Code
  CLI, a model id — cannot enter the store: every such entry is dropped `unpinned`.
- The version is what `main` declared on the day of the newest source, not what ran; a unit
  that raised a dependency on its own branch is recorded at the old one.
- The "not measured" markers are eleven fixed phrases (`admit.MARKERS`); a source saying the
  same in other words is not caught.
- `check` reads the run log of every `COS_WORKING_DIR`, `gather` only its own, and both read
  the local `main`, which may be behind `origin/main`; `check` prints the sha it read.

## The order, after the unit ships

1. `coscc knowledge baseline` — before the flag. `measure` refuses a baseline written after
   the first step that carried the store.
2. `coscc knowledge gather --all`, which spends nothing and prints batches and the ceiling.
3. `coscc knowledge gather --all --yes`.
4. `COS_KNOWLEDGE=1` in the service's env file, then restart.
5. `coscc knowledge check`: 0 every entry passes and at least half are `tool:`. Run it again
   before 2026-10-02 and after every raise of `reflex` or `claude-agent-sdk`: an entry pinned
   at the old version fails, and the way back is another `gather`.

The later the flag goes on, the more units ran `spec` without it and are left out as mixed
(`spec.md` C8). `measure` reads the deadline, 2026-10-16, as a UTC day.

`measure` reads the `knowledge` field of `start` rows and the `mode` of `knowledge` rows by
name: rename either and it reads nothing, silently (`.claude/rules/coscc-data.md`).
