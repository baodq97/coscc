---
paths:
  - "coscc/**"
  - "coscc/**/*"
  - "rxconfig.py"
  - "scripts/*.py"
---

# The coscc app

## Commands

```
uv run coscc-build                          # build the page; never `reflex export`
COS_WORKING_DIR=~/projects uv run coscc     # http://127.0.0.1:8790; never `reflex run`
uv run coscc reset-password                 # clears the master password and every session
uv run python scripts/capture_screens.py /board /settings   # into .screens/
```

`npm test` never builds: editing the page needs a rebuild.

## Architecture

One shell under static routes; `coscc/state/place.py` reads and writes the address and
`StudioState.arrive` is the only handler that sets `screen`, `cwd`, `unit_id` and `detail_tab`
(a navigation button only returns `rx.redirect`). Components in `screens/`, state in `state/`,
logic behind `Service` (`service/`): a handler that decides anything is a bug in `Service`.
`policy.py` is the grant table keyed by stage, outside `Config`; the default grants nothing.
A part that ends something publishes `<subject>.<past-tense verb>` on `coscc/bus.py`; who
hears what is one table in `Service.__post_init__`, never a callback wired down.

## Gotchas

- Sessions spend account quota: nothing that talks to the app goes in an unattended loop except
  the app's own autopilot. Every `--paid` flag under `scripts/` spends money.
- SQLite: `busy_timeout` is the first statement on every connection, before
  `PRAGMA journal_mode=WAL`; every read-modify-write is in `BEGIN IMMEDIATE`. Reversed, it goes
  flaky with `database is locked`.
- A card's state never uses the harness's `blocked` flag (true for every unfinished unit): read
  `next.why`, and the column is `cos.mjs`'s `at`.
- The five prose stages have no write tools and no commands, in any mode; the app writes their
  artifact from the reply.
- A `coscc/_harness/` or `coscc/_web/` left in a checkout shadows `.claude/` and the page, and
  `git status` stays clean: `rm -rf` it after a hand-built wheel.
- A database newer than the app is a 500 on every page while `/api/health` says `ok`
  (`data.Incompatible`); match the app to the database.

## Docs (`.claude/docs/`, read when editing what the line names)

- `.claude/docs/coscc-notices.md` — `coscc/service/notices.py`, the notice script.
- `.claude/docs/coscc-steps.md` — `Backlog.timeline`, `/api/board/stop`, `/api/board/running`, `Steps.run_step`, `journal.failed_attempts`.
- `.claude/docs/coscc-answers.md` — answer, outcome, hold and more-rounds routes, `cos.mjs rerun`, `coscc/units/hold.py`, `coscc/runner/prompt.py` answers helpers.
- `.claude/docs/coscc-settings.md` — `/api/settings/*`, `coscc/agent/models.py`, `/api/backlog/*`.
- `.claude/docs/coscc-spike.md` — the `spike` grant, its scratch directory.
- `.claude/docs/coscc-page-text.md` — adding or removing words on a screen.
- `.claude/docs/coscc-proofs.md` — `npm run e2e`, `capture_screens.py` (overwrites `.web`).
