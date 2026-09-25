---
paths:
  - "coscc/**"
  - "coscc/**/*"
  - "rxconfig.py"
  - "scripts/verify_*.py"
---

# The coscc app

Everything here is a hazard or a measurement that no command in this repository will
catch. Anything a script, a test or a refusal already enforces is left to that script. The
hazards of one area live in their own rule or document; `## Index` says which, and when to
read it.

## Commands

```
uv sync                                     # after a fresh clone
uv run coscc-build                          # build the page; never `reflex export`
COS_WORKING_DIR=~/projects uv run coscc     # then http://127.0.0.1:8790
uv run coscc reset-password                 # forgot the master password: clears it and every session
uv run python scripts/capture_screens.py /board /settings   # a UI unit's screenshots, into .screens/
```

**Start it with `coscc`, not `reflex run`.** Reflex's dev mode serves the page from a vite
server binding every interface, with no setting for its host. `coscc` mounts the compiled
frontend into the API's own ASGI app and binds one port, on `0.0.0.0` by default, behind
a master password (`coscc/auth.py`); `COS_HOST=127.0.0.1` is the loopback posture.

`npm test` never builds. `verify_0001` and `verify_0002` drive the ASGI app in-process, so
the test command needs no JavaScript toolchain — that is deliberate, and it is why editing
the page and forgetting to rebuild is possible at all.

## Shape

One shell under nine static routes (`/cost` since `0093`): `/` Overview, `/workspaces`,
`/board`, `/backlog`, `/sessions`, `/activity`, `/cost`, `/settings`, and `/unit` — the Board with a unit's dialog open,
`?ws=<workspace name>&id=<unit>&tab=<tab>`. `coscc/place.py` reads and writes the address;
`StudioState.arrive`, every route's `on_load`, is the only handler that sets `screen`,
`cwd`, `unit_id` and `detail_tab` — a navigation button only returns `rx.redirect`. A new
socket `session_id` is a new page and reads everything; a move inside the app reads only
what changed. Reflex's `on_load_internal` supersedes, so a navigation cancels the older
arrival and what it chained; `arrive` records a read only once it is done. The one thing a
navigation does not cancel is the `cos.mjs next` ask `load_next` waits on: it runs in its
own task (`_ASKING`), and the next arrival at that unit waits for it instead of asking
again. A proof that drives the state in-process has no browser to
follow a redirect: it arrives where the button would have sent it (`arrive_at`).
Components in `screens.py`, state in `state.py`, logic behind `service.py`.
**A handler that decides anything is a bug in `service.py`, not in the page.**

`policy.py` is the grant table, keyed by stage; the mode is recorded but grants nothing. It
sits **outside `Config`** so the four knobs keep meaning what they meant. Default is the
locked position: no tools, no commands, one turn, no budget.

## Hazards every change can meet

- **Sessions spend account quota.** Nothing that talks to the app belongs in an unattended
  loop. These proofs spend money, push, or need another machine: `verify_0001`, `0002`,
  `0004`, `0005`, `0011`, `0014`, and every `--paid` flag
  (`.claude/docs/coscc-proofs.md`).
- **SQLite settings are ordered.** `busy_timeout` must be the **first statement on every
  connection**, before `PRAGMA journal_mode=WAL`; reversed, it fails now and then with
  `database is locked`. Every read-modify-write is wrapped in `BEGIN IMMEDIATE`. No test
  catches the ordering; it only goes flaky.
- **The board's lanes must not use the harness's `blocked` flag.** `cos.mjs` returns
  `blocked: true` for every unfinished unit, so that mapping puts all of them in *Needs
  review* and empties the other three. `state.py` reads lanes off artifact statuses.
- **The five prose stages get no write tools and no commands, in any mode.** `plan`,
  `review` and `spec` may read, in every mode, inside the read boundary. A session that
  cannot write a file needs the app to write its artifact from the reply. Since `0082` the
  page no longer says so (`spec.md ## Answers, câu 5`); this bullet is where it is said,
  because otherwise it looks like the agent wrote the file.
  `.cos/0005_hand-driven-invisible-loop/plan.md` Risk 1 records why.
- **A `coscc/_harness/` left in a checkout shadows `.claude/`.** Both are gitignored and
  built, not committed, so `git status` stays clean while the app reads the stale copy —
  edit a skill, and the step still runs the old text. `coscc/harness.py` prefers the
  packaged tree on purpose (a wheel has no checkout to fall back to). `rm -rf
  coscc/_harness` after building a wheel by hand. The same is true of `coscc/_web/`, where
  it costs a stale page instead of stale rules.
- **A database newer than the app is a 500 on every page, and `/api/health` still says
  `ok`.** Nothing catches `data.Incompatible`, and the login guard reads the database on
  every request, so it leaves as `Internal Server Error` while `systemctl --user is-active`
  reports `active`. A checkout's `npm test` can raise the schema of `~/.cos/cos.db` under an
  installed build. `curl /api/workspaces` sees it; health checks do not. The way out is to
  match the app to the database or delete the database — a downgrade does not remove it.

## Index

Read the file named before touching what its line names. A rule under `.claude/rules/` is
also listed in every session's system prompt; a document under `.claude/docs/` is reached
only from here.

| Hazard | File | Read it when |
|---|---|---|
| `ship` merges, `pr` does not; `pr`/`ship` reach every repository the login does; the mode grants nothing; the read boundary is not a sandbox; a redirect may write under `/tmp` | `.claude/rules/coscc-policy.md` | editing `coscc/policy.py`, or any grant, tool list or `decide` call |
| the login door, `EXEMPT`, proxies, hashing limits | `.claude/rules/coscc-auth.md` | editing `coscc/auth.py`, `coscc/run.py`, or adding any route |
| scratch `COS_DATA_DIR`, `COSCC_PROTECTED_DB`; what a session loads of `~/.claude/` and the project | `.claude/rules/coscc-sessions.md` | editing `coscc/sessions.py`, `coscc/steps.py`, `coscc/instructions.py`, or adding a rule |
| a worktree per unit, `switch main`, fetches before a step; names from `main` in `impl`'s prompt | `.claude/rules/coscc-worktrees.md` | editing `coscc/worktrees.py`, `gitops.py`, `drift.py`, `fetches.py`, or `run_step`'s preparation |
| `POST /api/units/integrate` force-pushes; `gh pr list` per board read and per `pr` step | `.claude/rules/coscc-integrate.md` | editing `coscc/integrate.py`, the `integrate` or `pr` grant, or the board's integration read |
| `POST /api/units/review-comment` posts; a `pr` step rewrites its pull request | `.claude/rules/coscc-github.md` | editing `coscc/prcomment.py`, `coscc/prsync.py`, `/review-comment`, or `_sync_pr` |
| `POST /api/update/*` stops work and restarts | `.claude/rules/coscc-update.md` | editing `coscc/update.py`, `coscc/updater.py`, `scripts/build_wheel.sh`, `/api/update/*` |
| `GET /api/board/events` hands out everything a step saw | `.claude/rules/coscc-events.md` | editing `coscc/events.py`, `/api/board/events`, `/follow`, or the watch pane |
| the `review`/`ship` gates call `gh`; the run button follows `next` | `.claude/rules/coscc-board.md` | editing `coscc/board.py` or the run button |
| two roots, and what `--measure` reads | `.claude/rules/coscc-data.md` | editing `coscc/config.py`, `units.py`, `data.py`, `journal.py`, or a run-log field |
| `/api/timeline` returns a failed reply; `pull` within one process; a failed step's tail; `POST /api/board/stop`; units at the same time; `GET /api/board/running` | `.claude/docs/coscc-steps.md` | editing `/api/timeline`, `/api/board/stop`, `/api/board/running`, `Service.run_step`, `runner.describe_attempt` or `journal.failed_attempts` |
| `POST /api/units/answer`, `/precedent`, `/outcome`, `/hold`; re-running keeps `## Answers` | `.claude/docs/coscc-answers.md` | editing those routes, `coscc/hold.py`, or `answers_section`/`strip_answers`/`with_answers` in `coscc/runner.py` |
| `POST /api/settings/models` and `/efforts`; `POST /api/backlog/*` | `.claude/docs/coscc-settings.md` | editing `/api/settings/*`, `coscc/models.py`, `/api/backlog/*` or `coscc/backlog.py` |
| `spike` runs arbitrary code | `.claude/docs/coscc-spike.md` | editing the `spike` grant, its scratch directory, or its progress-file write |
| what the page stopped explaining in `0082` | `.claude/docs/coscc-page-text.md` | adding words to a screen, or removing a sentence the page says beside a button |
| every proof's cost; `capture_screens.py` overwrites `.web` | `.claude/docs/coscc-proofs.md` | running any `scripts/verify_*.py` or `scripts/capture_screens.py`, or writing a proof |
