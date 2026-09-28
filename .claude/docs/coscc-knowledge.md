# The knowledge store: what no test here catches

Unit 0090, agents relearn what earlier units already knew; unit 0131, the store goes stale
after one gather. `COS_KNOWLEDGE=1` hands `spec`, `spike`, `plan` and `impl` of half the units
the entries of `<COS_DATA_DIR>/knowledge/knowledge.md` that apply to the workspace
(`coscc/knowledge/__init__.py`); a gather writes that file (`coscc/knowledge/gather.py`), by
itself after every `ship` that ends `done`, or at a terminal; `coscc knowledge measure` says
whether the store paid (`coscc/knowledge/measure.py`).

## The app spends quota by itself

- With the flag on, every `ship` step the board runs that ends `done` starts one gather of
  that unit's `spec.md`, `plan.md`, `spike.md` and `review.md` in the background
  (`coscc/service/knowledge.py`). Nobody is asked. It costs up to batches × $2.00 per ship,
  and one session's excess over that. A ship merged from a terminal starts none.
- A gather after a ship is one `knowledge` row in `cos.db`, `mode: unit`, with `outcome`
  `saved`, `unchanged`, `refused` or `failed`, its `sessions` and its `cost_usd`. More than
  one such row for one unit is a bug that multiplies the cost.
- A unit of one batch is never repaired: after its first session one more could pass the
  ceiling of $2.00, so a refused reply is `failed`, and its sources are not sent again
  automatically. `coscc knowledge gather` at a terminal sends them, and anything else the
  manifest does not hold.
- A gather waits for another to end, one at a time for the whole store. A terminal
  `gather` is refused while one runs. An update of the app waits for a gather too; the money
  of one cut short is spent and the store is as it was.
- A failed gather after a ship changes no byte of the store, the manifest or `health.json`.
  A terminal `gather` still saves every batch that passes, which `--all` resumes from.
- The session runs in the store's directory with its own `Sessions`, whose membership is
  narrowed to that directory. It has no tool; give the grant one and the session reads
  everything there.
- The way back: unset `COS_KNOWLEDGE` and restart. The store stays where it is, nothing
  reads it, and no ship gathers.

## A terminal gather

- Each batch is one paid session under the grant `knowledge` (1 turn, $2.00, chosen, not
  measured). The CLI compares the session's cost after the turn has run (`0085`
  `spike.md ## U2`), so one batch can pass $2.00; the figure `gather` prints before `--yes`
  is batches × $2.00, not a bound.
- `gather --all` reads every unit's four files, `spec.md` and `plan.md` included, so it
  costs more than it did before `0131`. It saves the store after every batch that passes and
  records which sources passed in `<COS_DATA_DIR>/knowledge/gather-all.json`; running it again
  goes on from the batch that failed. Deleting that file by hand is the only way to start
  over. While it is there, `gather`, and every gather after a ship, is refused.
- A reply the check refuses is sent back to be repaired, at most twice, but only while what
  was spent plus one more $2.00 session stays under the figure printed.
- A store edited by hand into a block `parse` cannot read, a line outside every entry, or
  entries under no header refuse every gather until fixed by hand.

## Every read of `Ref:` is of a fetched `origin/main`

A gather, `show` and `measure` fetch `origin/main` of every workspace they read first, and a
fetch that fails stops them: a gather opens no session, the commands exit 1. In the app the
fetch goes through `coscc/git/fetches.py`; at a terminal through a table of that process
alone, so it may race the app for the ref, and only one retry covers it.

`coscc knowledge show` checks every entry on that `origin/main`, writes
`<COS_DATA_DIR>/knowledge/health.json` (`{sha: {slot: sha}, at, entries: {K<n>: "" | why}}`),
and prints for each workspace how many entries apply, pass, and are carried. It exits 1 when
an entry is broken and 2 when the store, the run log or git cannot be read. `check` is gone.

- A check reads what an entry points at — a pinned version, a path, a name in a file —
  never whether its sentence is still true.
- A tool no workspace pins in `.python-version` or `uv.lock` — `gh`, `git`, the Claude Code
  CLI, a model id — cannot enter the store: every such entry is dropped `unpinned`.
- No unit may be the only source of more than two entries; the older ones are dropped
  `one-unit`. The share of `tool:` entries is no longer a rule, so nothing but the prompt
  stops the store filling with `workspace:` entries the code already says.

## What a step is handed

Each step of the `on` arm checks the entries on the `HEAD` of its own worktree and withholds
the ones no longer true there: a `Ref:` that is gone, a `tool:` version that is not the pin.
Its `start` row's `knowledge` says which it carried (`ids`), which it withheld and why, and
the `HEAD` it read. When git cannot answer, every `workspace:` entry is withheld and every
`tool:` one carried. An entry true on `main` but not yet on the branch is withheld too, so
the Knowledge page may call an entry passing that some steps did not receive.

## The measure

Every step of every unit, with the flag on, carries `knowledge_trial: {arm}`: `on` when the
first byte of the SHA-256 of `"knowledge:" + <unit>` is even. The `off` arm never reads the
store. `coscc knowledge measure [--workspace SLOT]` compares the arms over every stage of the
units shipped by 2026-10-16 (UTC), with no cut at ten, adds what gathering cost in their
window to the `on` arm, and prints the verdict with the `origin_main` it fetched. `baseline`
and `baseline.json` are gone. A unit that started before the flag is in no arm; a unit whose
steps carry two arms, or none on some, is excluded and listed.

The Knowledge page shows the same measure without fetching, the entries with what the last
`health.json` said of each, the last gather, and the last 20 steps that carried an arm. It
has no button.

`measure` reads `knowledge_trial`, `knowledge`, `ci_red` and the `mode` and `cost_usd` of
`knowledge` rows by name: rename one and it reads nothing, silently
(`.claude/rules/coscc-data.md`).
