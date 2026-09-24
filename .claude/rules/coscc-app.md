---
paths:
  - "coscc/**"
  - "coscc/**/*"
  - "rxconfig.py"
  - "scripts/verify_*.py"
---

# The coscc app

Everything here is a hazard or a measurement that no command in this repository will
catch. Anything a script, a test or a refusal already enforces is left to that script.

## Commands

```
uv sync                                     # after a fresh clone
uv run coscc-build                          # build the page; never `reflex export`
COS_WORKING_DIR=~/projects uv run coscc     # then http://127.0.0.1:8790
```

**Start it with `coscc`, not `reflex run`.** Reflex's dev mode serves the page from a vite
server binding every interface, and 0.9.11 has no setting for its host — measured
2026-09-21, `ss -ltn` showed `*:3000`. `coscc` mounts the compiled frontend into the API's
own ASGI app and binds one port. Since `0011` that port is on `0.0.0.0` by default and
the app has no authentication — `COS_HOST=127.0.0.1` is the loopback posture `0001` built.

`npm test` never builds. `verify_0001` and `verify_0002` drive the ASGI app in-process, so
the test command needs no JavaScript toolchain — that is deliberate, and it is why editing
the page and forgetting to rebuild is possible at all.

## Shape

One page at `/`, six screens: Overview, Workspaces, Board, Sessions, Activity & usage,
Settings. Components in `screens.py`, state in `state.py`, logic behind `service.py`.
**A handler that decides anything is a bug in `service.py`, not in the page.**

`policy.py` is the grant table, keyed by stage; the mode is recorded but grants nothing
(since `0020`). It sits **outside `Config`**
so the four knobs keep meaning what they meant. Default is the locked position: no tools,
no commands, one turn, no budget.

## Hazards

- **Sessions spend account quota.** Nothing that talks to the app belongs in an unattended
  loop.
- **Stage `ship` merges; `pr` no longer does.** Since `0015` `pr` stops at an open pull
  request and its grant refuses the merge by the command's words with flags removed —
  `gh -R o/r pr merge`, the merge endpoint through `gh api` and `gh alias set` included.
  An alias defined before the step, or `node -e` spawning `gh`, still walks past
  (`coscc/policy_test.py`, `test_the_known_limit_of_the_deny_list`).
  `ship`'s grant holds `git` and `gh` with this machine's login and lands the
  change on `main` after the `ship` gate opens. Read the next bullet for how far that
  reaches.
- **`/api/timeline` returns what a failed paid step replied.** Since `0014` a step whose
  reply could not be used keeps the last 2000 characters of it (`coscc/runner.py:150`), and
  that text reaches the board as `detail`. No route has a login and the default bind is
  `0.0.0.0`.
- **The `pr` and `ship` grants reach further than this
  repository.** Their capability comes from this machine's `gh` login, so they reach every
  repository that login reaches. The page shows a warning string before the button is
  pressed; do not remove either.
- **The `review` and `ship` gates call `gh` and `git` in the workspace.** `board.gate`
  passes `--repo` and waits 30s (chosen). `child_env` carries `PATH`, `HOME` and
  `COS_REVIEW_ROUNDS` only, so a machine logged in through `GH_TOKEN` alone sees the
  `review` gate closed with gh's own error. Offline, `review` cannot start.
- **The run button offers the stage `cos.mjs next` names, even one that already has an
  artifact.** Since `0024` the fix → review-again loop is driven from the board: after a
  review asks for changes it offers `impl`, then `review` once a fix is on the pull request
  and CI is green. Re-running `impl` overwrites `impl.md`; `review.md`'s rounds are guarded
  on top of that: the reply carries only its new round, the runner writes the earlier ones
  back from the file, and it refuses a reply that rewrites one. Since `0025` every prose
  stage's `## Answers` section is guarded the same way, `review.md` included — see the
  hazard below. Opening a unit, finishing a step and
  pressing *Ask again* each ask `gh` in the workspace under this machine's login, up to
  60s; nothing re-asks on a timer, so a pending CI shows no button until someone asks.
  An `impl` that commits and does not push leaves the button on `impl`. Since `0028` the
  button offers nothing when `next` returns `waiting`: the last review round confirmed
  those findings need a person, and the frame names them and links to *Questions* instead.
  An `impl.md ## Needs a person` that claims every open finding sends the button to
  `review`, not `impl`, even with no new commit — nothing but the review checks the claim.
- **Two roots, and backing up one does not back up the other.** `COS_DATA_DIR` (default
  `~/.cos`) holds `cos.db`; `COS_WORKING_DIR` holds somebody else's git checkouts. A stored
  workspace is a *name*, never a path — the path is rebuilt from the root on every read,
  which is why a hand-edited store cannot point the app at `/etc`.
- **SQLite settings are ordered, and the order was measured.** `busy_timeout` must be the
  **first statement on every connection**, before `PRAGMA journal_mode=WAL`. Reversed, it
  failed about one run in ten with `database is locked` — measured 2026-09-22. Every
  read-modify-write is wrapped in `BEGIN IMMEDIATE`. No test catches the ordering; it only
  goes flaky.
- **The board's lanes must not use the harness's `blocked` flag.** `cos.mjs` returns
  `blocked: true` for every unfinished unit, so that mapping puts all of them in *Needs
  review* and empties the other three. `state.py` reads lanes off artifact statuses.
- **The five prose stages get no write tools and no commands, in any mode.** `plan`,
  `review` (since `0015`) and `spec` (since `0020`) may read, in every mode, inside the
  read boundary below; `ship` is no longer prose. A session that cannot write a file
  needs the app to write its artifact from the reply.
  Settings says so on the page, because otherwise it looks like the agent wrote the file.
  `.cos/0005_hand-driven-invisible-loop/plan.md` Risk 1 records why.
- **The mode no longer grants anything, so the default button hands `pr` and `ship`
  their full grant.** Before `0020` a step started in `manual` had no tools and failed
  harmlessly; now `pr` pushes and `ship` merges with this machine's `gh` login whatever
  mode is set. What still stands in front is the gate `run_step` asks and the warning
  string shown before the button.
- **The read boundary is not a sandbox.** Since `0020` `Read`, `Glob` and `Grep` are held
  to the unit's worktree and its own folder in the store, for every grant. It binds only
  the stages without `Bash`: `impl`, `pr` and `ship` still have `cat` and `head`, and
  `check_command` reads no paths. A `Glob` pattern is checked only up to its first
  wildcard. `ship` runs in the store's unit folder, so it can no longer `Read` the
  worktree. `coscc/policy_test.py`, `TheReadBoundaryIsNotASandbox`, pins the gaps. A
  worktree's `.git` is a file pointing outside both roots, so no read-only stage can read
  a commit out of `.git/`: `review` is handed the head in its prompt instead
  (`build_prompt`, *The commit you are reviewing*; `0020` review round 1, F1).
- **A `coscc/_harness/` left in a checkout shadows `.claude/`.** Both are gitignored and
  both are built, not committed, so `git status` stays clean while the app reads the stale
  copy — edit a skill, and the step still runs the old text. `coscc/harness.py` prefers the
  packaged tree on purpose (a wheel has no checkout to fall back to); the cost is this.
  `rm -rf coscc/_harness` after building a wheel by hand. The same is true of
  `coscc/_web/`, where it costs a stale page instead of stale rules.
- **A database newer than the app is a 500 on every route that reads it, and `/api/health`
  still says `ok`.** Nothing catches `data.Incompatible` — not `service.py`, not `api.py` —
  so it leaves as `Internal Server Error` while `systemctl --user is-active` reports
  `active`. Measured 2026-09-22 when a checkout's `npm test` upgraded `~/.cos/cos.db` to
  schema 2 under an installed `v0.2.3`. Health checks do not see this; `curl /api/workspaces`
  does. The way out is to match the app to the database or delete the database — a downgrade
  does not remove it.
- **`POST /api/units/answer` writes a stranger's words into a paid prompt.** Since `0016`
  it appends an answer under a typed name to an artifact, and the next stage embeds that
  file. No login, `0.0.0.0` by default: anyone on the network can put text there that a
  stage will read as a person's decision. The only trace is the file and an `outputs` row
  with `actor = human:<name>` — watch for a name nobody recognises. `COS_HOST=127.0.0.1`
  is the mitigation that exists. Since `0028` it also takes `question: "F<n>"` with
  `artifact: "review.md"` for a finding `cos.mjs` lists in `personFindings`, and that block
  does more than reach a prompt: `next` offers `review` once every such finding has one,
  and the `ship` gate counts a finding the review then marks `[answered]` as closed only
  when its block exists. A stranger's answer plus one agent's round is part of what opens
  a merge.
- **`POST /api/units/review-comment` writes to GitHub under this machine's `gh` login.**
  Since `0021` it posts a round of `review.md` to the unit's pull request, verbatim and
  unfiltered: a finding that quotes a token or a local path goes up with it, and a public
  repository's pull request is public. No login, `0.0.0.0` by default, so anyone who
  reaches the port can press it. The body is only ever the round's own text, and the
  marker on its last line stops a second copy. The trace is a `pr-comment` row in Activity
  and the comment itself. `run_step` also posts on its own after writing a review round,
  which can hold the `done` row up to 60s on a slow network (two `gh` calls, 30s each).
- **Re-running a prose stage keeps `## Answers` byte for byte; a reply's own attempt at
  one is dropped, silently.** Since `0025` the runner (`coscc/runner.py`: `answers_section`,
  `strip_answers`, `with_answers`) reads the section already on disk right before it
  writes — not at the step's start — and writes it back after the stage's own text, on
  all five prose stages: `idea`, `intent`, `spec`, `plan` and `review`. Whatever a reply
  says under its own `## Answers` heading — copied from the artifact, forged, or a model
  answering its own question — never reaches disk, and nothing records that a reply tried.
  A window remains between the answer route's read and the runner's: the two hold no lock
  in common (`_answer_lock` is `Service`'s, `coscc/service.py:140`, and `Runner` carries
  no reference to it). Byte-identical is not meaning-identical: a re-run that renumbers
  `## Open questions` leaves `### Câu N` on disk pointing at whichever question now
  carries that number, not the one a person answered
  (`.cos/0025_rerunning-a-stage-erases-what-was-added-to-it/spec.md` C1). `impl.md` is
  still overwritten whole — a session writes it with its own tools, and this unit never
  covered it.
- **Each unit works in its own `git worktree`, and the app may move the workspace to
  `main`.** Since `0017` a unit's tree is `<COS_DATA_DIR>/worktrees/<slot>/<unit>`; a step
  runs there, and `gate`/`next` get `--repo <that tree>`. When a unit's branch is checked
  out in the workspace itself (cut at a terminal), the app runs `git switch main` there,
  only if that tree is clean. A person standing on that branch finds themselves on
  `main`, and when coscc works on itself the harness it reads changes with it. Every tree
  costs its own `.venv`, `node_modules` and `.web` (unmeasured), prepared by `uv sync
  --frozen`, `npm ci` and `uv run coscc-build`, running the repository's own install
  scripts under this process's user. After `ship`, or on a board read of a `finished`
  unit, the app removes the tree and `branch -D`s the local branch, but only when `gh`
  says merged at the local head. A tree that cannot be removed that way (clean, but `gh`
  fails or the branch is off the merged head) costs a `gh pr view`, up to 30s, on
  **every** board read until someone removes it by hand; nothing remembers the refusal.
  Since `0030_a-unit-branch-starts-from-a-stale-main` the app also moves a still-detached
  tree's HEAD to `origin/main` as a fetch just brought it, before every step that runs
  there — so a unit opened after a merge no longer starts its branch on a stale trunk. Each
  such step therefore costs one more fetch, up to `FETCH_TIMEOUT` = 20s (chosen, not
  measured) when the remote does not answer; the step still runs on whatever the tree
  already had, and says so in its prompt and in the run log. The same `0030` change also
  reaches a plain board read: when a unit's branch already exists but its tree is still
  detached, or never existed, `next_step`'s call into `worktrees.ensure` opens the tree
  onto that branch there and then, which fetches first and costs the same up to
  `FETCH_TIMEOUT` — on **every** such read, not only when a step runs. Offline, that fetch
  fails, `ensure` raises, and `next_step` swallows it into a plain `None`: the read still
  answers, but with no worktree and no reason shown for why the run button has nothing to
  offer.
- **`POST /api/settings/models` decides what every step spends, and has no login.** Since
  the store's `0004_no-setting-says-which-model-runs-a-stage` each stage, and chat, runs
  on the model Settings names: an override in the `prefs` table (`model:<name>`), else
  `coscc/models.json`, else `COS_MODEL`. Anyone who reaches the port can move `review` to
  a weak model or every stage to a dear one, `0.0.0.0` by default. The trace is a
  `setting` record in the run log (workspace `""`, with `old` and `new` — it shows on no
  workspace's Activity) and the `override` badge on Settings. A model id is not checked
  when saved; a wrong one fails the stage's next step with the CLI's error. A person who
  had `COS_MODEL` set before this lost it for all eight stages: it now answers only chat.
  `COS_HOST=127.0.0.1` is the mitigation that exists.
- **`pull` refuses only within this process.** Two copies of the app on one working folder
  still see past each other for sessions. `.cos/0004_silent-concurrent-loss/spec.md` C2.
- **A failed step's transcript tail is stored in `cos.db` and put into the next prompt.**
  Since `0019` a stage that ends without `done` — a ceiling hit, an exception, a reply with
  no `Status:` line — has `Runner.run` capture the tree (`HEAD`, branch, the commits since
  the trunk, `git status --porcelain`) and the last `runner.ATTEMPT_EXCERPT` (8000, chosen;
  measured 2026-09-24 as too short to hold `0032`'s own measurements, which sat 87656 and
  101788 characters from the end — the unit's `impl.md` says why it was left) characters of what the session's own
  turns produced, and append it to the run log as one `kind: "attempt"` row, read back only
  by `journal.failed_attempts` and placed in the *next* run's prompt
  (`runner.describe_attempt`), never in an artifact. No route returns it — `/api/timeline`,
  `Service.board`, `.activity`, `.usage` and `.activity_and_usage` all project a fixed set
  of fields that does not include it — but it still sits in `cos.db` under the data root,
  and a tool's own output can carry a token or a local path. Capturing is best-effort:
  `Runner.run`'s `finally` swallows every exception around it, so a step's outcome and its
  `end` record never depend on the capture succeeding.

## The proofs, and what each one costs

| | |
|---|---|
| `verify_0001.py` | creates real sessions |
| `verify_0002.py` | clones, creates sessions |
| `verify_0003.py` | browser, needs `COS_PORT` free; no session, no quota |
| `verify_0004.py` | 4 processes at once, creates a session |
| `verify_0005.py` | **pushes a branch and opens a PR.** Needs `COS_PROOF_REPO`; unset is exit 2 with claims 2, 3, 4, 6 skipped. Eight sessions, one with a $5 ceiling |
| `verify_0006.py` | browser, needs `COS_PORT` free; sends one short prompt |
| `verify_0011.py` | **needs another machine.** `COS_PROOF_TARGET`, an SSH destination it reboots twice; unset is exit 2 |
| `verify_0012.py` | measures the **installed** copy, not this checkout. Needs `node`, a running service at `COS_URL` and one workspace; no session, no quota |
| `verify_0013.py` | reads git history into a **temporary** data root, never `~/.cos`. No session, no quota, no network. Run it plain and it is exit 1 by design — `--import` is what fills the log and makes it exit 0 |

| `verify_0014.py` | **spends real money and merges a real pull request.** Needs `COS_PROOF_REPO`, a throwaway repo; unset is exit 2. Five sessions — measured $3.28 and 11m49s end to end, 2026-09-22. `--dry` stops before the first paid step |
| `verify_0016.py` | no session, no quota, no network; temporary data root. Needs `node` and `uv`; either missing is exit 2 |
| `verify_0017.py` | no session, no quota, no network; temporary data root, bare-directory remote. `--this-repo` prepares a worktree of this checkout (`uv sync`, `npm ci`, a build: 20s measured 2026-09-23) and runs `npm test` twice. `--paid` **spends real money**: two real `impl` sessions on a clone of `COS_PROOF_REPO`; unset is exit 2 |
| `verify_0019.py` | no session, no quota, no network; temporary data root, bare-directory remote, a fake `gh` first on `PATH`. Needs `node`, `uv` and `git`; any missing is exit 2. The session and its transcript are stand-ins, so it cannot see a real budget stop's `terminal_reason` or a transcript read before it is flushed |
| `verify_0021.py` | no session, no quota, no network; temporary data root and a fake `gh` first on `PATH`. Needs `node`, `uv` and `git`; any missing is exit 2 |
| `verify_0024.py` | no session, no quota, no network; temporary data root and a fake `gh` first on `PATH`. Needs `node`, `uv` and `git`; any missing is exit 2. Drives `StudioState`'s own handlers through Reflex's event processor, in-process; no browser, so the compiled page is not exercised |
| `verify_0025.py` | no session, no quota, no network; temporary data root. Needs `node` and `uv`; either missing is exit 2 |
| `verify_stage_models.py` | no session, no quota, no network; temporary data root, `COS_MODEL` removed, a fake `gh` first on `PATH`. Needs `node`, `uv` and `git`; any missing is exit 2. Drives `StudioState`'s handlers as `verify_0024` does. `--paid` **spends real money**: since `0031_shipped-model-defaults-cap-every-stage-at-200k` it calls `claude -p` once per distinct id `coscc/models.json` ships (currently two: `claude-opus-5-5[1m]` and `claude-sonnet-5[1m]`) and requires `modelUsage[...].contextWindow` to read 1000000 for each. No `claude` on `PATH`, or a login that does not work, is exit 2; the CLI reporting an error for that model id is exit 1 |
| `verify_state_it_describes.py` | browser, needs `COS_PORT` free; no session, no quota, no network. The remote is a bare directory in a temp folder. Proof of the store's `0001_product-describes-a-state-it-is-not-in`, not of `.cos/0001_*` — hence the name |

Exit codes: `0` pass, `1` the page is broken, `2` the environment is not ready.

`verify_0003` and `verify_0006` are the two that open a real browser. **In a checkout** the
bundle hardcodes its own address, so neither can move to a spare port: stop the app first,
and never run them at the same time. A wheel installed by `install.sh` behaves the other
way — `coscc/frontend.py` rewrites the address at startup, because a packaged install has
no Node to rebuild with. Both sentences are true; which one applies depends on which of the
two shapes you are looking at, and `coscc/run.py` is where they part.

A figure carries across a rewrite only if the mechanism did not change. `0006 spec.md` C2
says this about swapping the store; it holds the same way for swapping the interpreter.
