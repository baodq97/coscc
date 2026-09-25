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
- The session runs in the store's directory and `Sessions.membership` is narrowed to it.
  It has no tool; give the grant one and the session reads everything there.

## What the check does not check

`knowledge.validate` holds the byte caps, the ids, the scopes and that every `Source:` is a
source the session was given. It cannot tell a wrong entry from a right one (`spec.md` C2):
a wrong entry now reaches every `spec`, `spike` and `plan`. The way back is to unset
`COS_KNOWLEDGE` and restart; the store stays where it is and nothing reads it.

## The order, after the unit ships

1. `coscc knowledge baseline` — before the flag. `measure` refuses a baseline written after
   the first step that carried the store.
2. `coscc knowledge gather --all`, which spends nothing and prints batches and the ceiling.
3. `coscc knowledge gather --all --yes`.
4. `COS_KNOWLEDGE=1` in the service's env file, then restart.

The later the flag goes on, the more units ran `spec` without it and are left out as mixed
(`spec.md` C8). `measure` reads the deadline, 2026-10-16, as a UTC day.

`measure` reads the `knowledge` field of `start` rows and the `mode` of `knowledge` rows by
name: rename either and it reads nothing, silently (`.claude/rules/coscc-data.md`).
