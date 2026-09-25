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
uv run coscc reset-password                 # forgot the master password: clears it and every session
```

**Start it with `coscc`, not `reflex run`.** Reflex's dev mode serves the page from a vite
server binding every interface, and 0.9.11 has no setting for its host — measured
2026-09-21, `ss -ltn` showed `*:3000`. `coscc` mounts the compiled frontend into the API's
own ASGI app and binds one port. Since `0011` that port is on `0.0.0.0` by default; since
`0070` a master password stands in front of all of it (`coscc/auth.py`) —
`COS_HOST=127.0.0.1` is the loopback posture `0001` built.

`npm test` never builds. `verify_0001` and `verify_0002` drive the ASGI app in-process, so
the test command needs no JavaScript toolchain — that is deliberate, and it is why editing
the page and forgetting to rebuild is possible at all.

## Shape

One shell under seven static routes (since `0056`): `/` Overview, `/workspaces`, `/board`,
`/sessions`, `/activity`, `/settings`, and `/unit` — the Board with a unit's dialog open,
`?ws=<workspace name>&id=<unit>&tab=<tab>`. `coscc/place.py` reads and writes the address;
`StudioState.arrive`, every route's `on_load`, is the only handler that sets `screen`,
`cwd`, `unit_id` and `detail_tab` — a navigation button only returns `rx.redirect`. A new
socket `session_id` is a new page and reads everything; a move inside the app reads only
what changed. Reflex's `on_load_internal` supersedes, so a navigation cancels the older
arrival and what it chained; `arrive` records a read only once it is done. The one thing
a navigation does not cancel is the `cos.mjs next` ask `load_next` waits on: it runs in its
own task (`_ASKING`), and the next arrival at that unit waits for it instead of asking again. A proof that
drives the state in-process has no browser to follow a redirect: it arrives where the
button would have sent it (`arrive_at` in `verify_0024`, `0051`, `stage_models`).
Components in `screens.py`, state in `state.py`, logic behind `service.py`.
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
  that text reaches the board as `detail`, for whoever holds the password or a live
  session.
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
  `check_command` reads only the target of a redirect that writes (since `0060`), never
  the paths `cat` or `head` are given. A `Glob` pattern is checked only up to its first
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
  does not remove it. Since `0070` the login guard reads the database on every request, so a
  build meeting a newer database fails every page, not only the routes that read data. Since
  `0073` `SCHEMA_VERSION` is 4: a build from before `0073` is a `500` on every page of a
  database this one has touched.
- **`coscc/auth.py` is the only door, and it opens on one password.** Since `0070` uvicorn
  serves `coscc.coscc:served`, the composed app wrapped by `auth.Guard` — the one position
  `.cos/0070_*/spike.md ## U1` measured to see every scope, CORS preflight included; moving
  the guard into `api_transformer` lets Reflex answer `OPTIONS` without it. `auth.EXEMPT` is
  the whole list of what answers without a session (`/api/health`, `/login`, and `/setup`
  while no password is stored); anything else, a route added later included, is refused,
  and `coscc/auth_test.py` plus `scripts/verify_0070.py` count that. Things no test sees:
  `proxy_headers=False` in `coscc/run.py`, so behind a proxy every client shares one
  failure count and a stranger can lock the owner out for up to an hour (spec C2); a
  state-changing request or websocket handshake whose `Origin` does not match `Host` is
  `403`, so a proxy that rewrites `Host` breaks the page (C3); `HASH_CONCURRENCY` = 2
  argon2 hashes at once (about 64 MiB each, `spike.md ## U2`), a third waits `HASH_WAIT`
  = 5 s and gets `429` — many addresses trying at once can refuse the owner too. The
  failure count and the setup token live in memory: a restart clears the one and mints the
  other. A setup token sits in the journal until the password is set. The guard opens a
  SQLite connection per request (unmeasured cost); a `Busy` there is a `500`, still a
  refusal. A page it lets through goes out `Cache-Control: no-cache`: without it chromium
  reused a cached `index.html` after logout, a board whose socket the guard refused, and
  never reached `/login`. `verify_0070.py --browser` is the one proof that opens chromium
  behind the guard, on loopback and off it — not behind a proxy, and not on the installed
  service.
- **`POST /api/units/answer` writes a stranger's words into a paid prompt.** Since `0016`
  it appends an answer under a typed name to an artifact, and the next stage embeds that
  file. Whoever holds the password or a live session can put text there, under any name,
  that a stage will read as a person's decision. The only trace is the file and an
  `outputs` row with `actor = human:<name>` — watch for a name nobody recognises.
  The password is what stands in front; `COS_HOST=127.0.0.1` still narrows who can try it. Since `0028` it also takes `question: "F<n>"` with
  `artifact: "review.md"` for a finding `cos.mjs` lists in `personFindings`, and that block
  does more than reach a prompt: `next` offers `review` once every such finding has one,
  and the `ship` gate counts a finding the review then marks `[answered]` as closed only
  when its block exists. A stranger's answer plus one agent's round is part of what opens
  a merge.
- **`POST /api/units/review-comment` writes to GitHub under this machine's `gh` login.**
  Since `0021` it posts a round of `review.md` to the unit's pull request, verbatim and
  unfiltered: a finding that quotes a token or a local path goes up with it, and a public
  repository's pull request is public. Whoever holds the password or a live session can
  press it. The body is only ever the round's own text, and the
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
  measured) when the remote does not answer — unless, since `0048`, a fetch of the same
  clone is already running (the step waits for it) or one succeeded under 30s before (the
  step fetches nothing, and a commit pushed in those 30s is not in its base, cutting a
  branch included); the step still runs on whatever the tree
  already had, and says so in its prompt and in the run log. The same `0030` change also
  reaches a plain board read: when a unit's branch already exists but its tree is still
  detached, or never existed, `next_step`'s call into `worktrees.ensure` opens the tree
  onto that branch there and then, which fetches first and costs the same up to
  `FETCH_TIMEOUT` — on **every** such read, not only when a step runs. Offline, that fetch
  fails, `ensure` raises, and `next_step` swallows it into a plain `None`: the read still
  answers, but with no worktree and no reason shown for why the run button has nothing to
  offer.
- **`POST /api/settings/models` decides what every step spends, for whoever holds the
  password.** Since
  the store's `0004_no-setting-says-which-model-runs-a-stage` each stage, and chat, runs
  on the model Settings names: an override in the `prefs` table (`model:<name>`), else
  `coscc/models.json`, else `COS_MODEL`. Anyone holding the password can move `review` to
  a weak model or every stage to a dear one, `0.0.0.0` by default. The trace is a
  `setting` record in the run log (workspace `""`, with `old` and `new` — it shows on no
  workspace's Activity) and the `override` badge on Settings. A model id is not checked
  when saved; a wrong one fails the stage's next step with the CLI's error. A person who
  had `COS_MODEL` set before this lost it for every stage: it now answers only chat.
  Since `0033` every row also has an effort (`effort:<name>`, `POST /api/settings/efforts`,
  the same `setting` trace), and each stage after `plan` has a `<stage>:novel` row used when
  the plan's label is `novel`: declared, forced by a file in `coscc/labels.py`
  `SECURITY_SURFACE`, missing (every plan written before `0033`), or escalated because an
  earlier `impl` of the unit stopped at `max_turns`. So a routine `impl` that runs out of
  turns reruns on the dearer row with nobody pressing anything different. Since `0062`
  that rerun, and every `impl` labelled `novel` — a `missing` plan written before `0033`
  included — also gets 250 turns / $16.0 instead of 120 / $8.0 (`policy.NOVEL_CEILINGS`,
  shown on Settings as `impl:novel`), so one press can spend twice as much. `max` is refused
  from `models.json` and taken from an override, so anyone holding the password can set it.
  The password is what stands in front; `COS_HOST=127.0.0.1` still narrows who can try it.
- **`spike` runs arbitrary code, and nothing is a sandbox.** Since `0039` a spec that marks
  a concern `[unmeasured] U<n>` sends its unit to a `spike` step holding `Bash` with
  `python`, `node`, `npm` and `uv` (`impl`'s commands without `git`,
  `policy.SPIKE_COMMANDS`), run under this process's user. Its `cwd` is
  `<COS_DATA_DIR>/spikes/<slot>/<unit>`, emptied before the step and removed after it
  (`service.run_step`); the write tools are held to that directory, and the worktree and
  the unit are read only. `check_command` is not a sandbox: `python -c` writes anywhere the
  user can, `~/.ssh` and other units' stores included, and none of that is seen. What is
  seen is the worktree: its `HEAD` and `git status --porcelain` are read before and after
  (`gitops.tree_state`), and a difference fails the step with no `spike.md` and the files
  named in the run log — detected, not undone, and a write to a path `.gitignore` covers is
  not in `status`. A client that drops the stream leaves the scratch until the generator
  is collected or the next spike clears it. The `spec ↔ spike` loop stops for a person at
  `Round: 2` (`cos.mjs` `SPIKE_ROUNDS`), and `Round:` is the agent's own word: a spike
  that writes `Round: 1` every time loops until the money runs out.
- **A redirect may write under `/tmp`, outside the write boundary.** Since `0060`
  `check_command` reads a line as bash does and lets a redirect write to `/dev/null`, to
  another descriptor, or below `/tmp/<a directory whose name carries the unit's
  NNNN_slug>/` — for `impl`, `pr` and `ship`, whose `decide` gets a `unit_dir`. `spike`
  and `integrate` get none, so they have `/dev/null` and descriptors only. That write is
  outside the boundary `decide` holds the write tools to. The target is resolved,
  symlinks included, when `decide` runs, not when bash opens it, so a directory swapped
  for a symlink in between is not seen. Nothing creates or removes that directory, and
  `/tmp` is shared: anyone on the machine can make one carrying a unit's name first. The
  rest is still words, not capability — `python -c` writes anywhere, as before.
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

- **`POST /api/units/integrate` force-pushes under this machine's `gh` login.** Since
  `0035`. On a `behind` unit it runs `gh pr update-branch --rebase` and then moves the
  unit's local branch with `reset --keep`; on a `conflicting` or `red-after-integration`
  unit it opens Gebo, a paid session (ceilings 120 turns / $8, chosen, not measured) whose
  grant allows exactly one push: `--force-with-lease=<branch>:<head at start>` to the
  unit's own branch. The roads to the branch the grant's own commands hold are refused by
  their words: `gh api` (it reaches `git/refs` with `force=true`), `gh repo sync`,
  `gh extension`, `git send-pack`, `git http-push`, and an alias, include or
  `GIT_CONFIG_*` made during the step. The grant still reads tokens, so any program it may
  start can push past the lease itself — `node -e`, `python -c`, or a script the step
  wrote and then runs through `npm test` — and so can an alias already in a git config
  before the step (`coscc/policy_test.py`, `test_the_known_limit_c6`). What stops a force
  on `main` is the GitHub ruleset, not this grant. Gebo may read the
  intent, spec and plan of the units the app lists as related — a widening of the read
  boundary, and not a sandbox while it has `cat`. Behind the password like every route. Every
  attempt, refused ones included, is one `integration` row in the run log.
  Since `0052` a `behind` or `current` unit can open a paid session too: a non-zero exit of
  `update-branch` after which `gh pr view` still reads the pull request's head unmoved opens
  Gebo with gh's code and words in its prompt — a missing permission, or a network that
  failed only the first call, included, and that session will likely fail the same way
  (`.cos/0052_*/plan.md` Risk 1). A lapsed login fails that read too and opens none. A
  timeout, and a head GitHub has not moved yet, stay `failed` with no session. A non-zero
  exit after which the head has moved is taken as GitHub's rebase (`pushed`, the tree
  follows it); one after which it cannot be read is `failed`. The head is read once, not
  polled, so a rebase GitHub finishes after that read still races the session: the lease
  refuses Gebo's push, and since review round 2 of `0052` the row is `failed` rather than
  Gebo's `pushed` whenever Gebo's tree does not end on the moved head, and the tree is moved
  to it — the session is paid for either way. Every press inside the window costs one fetch through the
  `0048` coordinator (up to `FETCH_TIMEOUT` 20s, and one reused under 30s old) and one
  `gh pr view` for `mergeStateStatus` (up to 30s), both before the lock and the answer. The
  fetch moves `refs/remotes/origin/main` for every worktree of the workspace. `merge_state`
  is written to the row and decides nothing.
- **A session reads a scratch `COS_DATA_DIR`, and `cos.db` is a tripwire, not a lock.**
  Since `0076` every session `Sessions` opens — each stage's step, Gebo, chat — gets
  `COS_DATA_DIR` pointed at a fresh `/tmp/coscc-session-*` (`sessions.scratch_dir`), and it
  and the commands `worktrees.prepare` runs carry `COSCC_PROTECTED_DB`, this app's `cos.db`
  appended to whatever list the app itself was given; `Data.connect` raises `Protected`
  before opening a listed file, reads included. What it does not stop: a branch cut before
  `0076`, or one that edits the check, has only the scratch directory (spec C1); `python
  -c`, `sqlite3` or anything opening `~/.cos/cos.db` by its literal path walks past both.
  A board step's directory is removed after its CLI is closed. Gebo's and a chat's live
  with their client in `Sessions._live` — Gebo streams with no `step` — and nothing in the
  app closes one but `Sessions.close_all`, which the installed service runs only on an
  update, and `cut_turn`, only on "áp dụng ngay": so every Gebo run and every new chat
  adds one that stays until then. A SIGKILL of the app, or any restart without that
  update, leaves every `/tmp/coscc-session-*` that exists at that moment behind for good,
  and nothing sweeps them (spec C4, size unmeasured). Chat no longer falls back to `~/.cos`
  (C6). `verify_0037/0041/0060/0061 --measure` run inside a step read an empty database and
  exit 2: run them at a terminal. If the app itself is started with its own `cos.db` in
  `COSCC_PROTECTED_DB`, every route that reads it is a `500` while `/api/health` says `ok`
  — the incident this unit fixed, from the other side (`plan.md` Risk 2).
- **`POST /api/units/outcome` writes the ground for keeping or dropping a unit.** Since
  `0047`. On a `finished` unit it appends a `### Outcome` block (`Result:`
  `đạt` | `trượt` | `không đo được`, `Measured by:`, `Source:` or `Reason:`) under
  `intent.md ## Answers`, and the board labels the unit from the last valid one. Anyone
  holding the password can record `đạt` under any name; `Measured
  by:` is a word they typed too, and `Source:` is checked against nothing. No gate reads
  the block. The trace is the block in the file and an `outputs` row with `source =
  outcome`. The password is what stands in front; `COS_HOST=127.0.0.1` still narrows who can try it.
- **`POST /api/board/stop` ends anyone's step, under any name.** Since
  `0034`. It closes the step's CLI client and cancels the step's task; a CLI still running
  `sessions.DISCONNECT_TIMEOUT` (5s, chosen) after the close began gets SIGTERM from the
  app, and SIGKILL `KILL_AFTER` (3s, chosen) later — through the SDK's private
  `_transport._process`, so an SDK that renames it loses this silently; the step ends `stopped`, writes no artifact and records no
  transition, and whatever it already committed or pushed stays. `stopped_by` is a name
  the person typed, not an identity, and the trace is that one `end` record. A Stop whose
  cancel reaches the step's task before its first turn leaves no trace at all: the runner
  never ran, so there is neither `start` nor `end`, and only the Stop's own reply names
  `stopped_by` (`Service._never_driven`, since `0050`). A step that
  has begun writing its artifact refuses the stop. A step stopped before its session
  reported a cost records `cost_unknown` and no cost at all, so Activity reads it as free.
  Stopping a `pr` or `ship` midway can leave a pushed branch or a merged pull request with
  no `pr.md` or an unremoved worktree. Whoever holds the password or a live session can
  press it.
- **Units run their steps at the same time.** Since `0034` each board step is its own
  task, one per unit (a second is refused before it spends anything) and any number of
  units at once; a reader that goes away no longer ends the step, and every step's CLI
  process is closed when it ends. Since `0048` a fetch of the same clone waits for one
  already running; the rest of two steps' `git` in one workspace — `switch main` among it —
  can still collide on a lock, and nothing here serialises it (unmeasured). The list
  of running steps is in memory: a restart forgets it, and a step cut off by a restart has
  no `end` record. Since `0050` a unit is held from before `run_step`'s first board read:
  a second request for any stage of it is refused before it runs `cos.mjs`, `git` or `gh`,
  with a reason naming the stage, the phase (`preparing` or `running`) and when it began
  (`steps.describe`). Still one process only: a second copy of the app, a chat or a
  terminal is not seen.
- **Every board read with a unit between `pr` and `ship` costs one `gh pr list`.** Since
  `0035`, up to 30s (chosen), plus a `gh pr checks` for a unit whose head is the one its
  last integration pushed. Offline, every such unit reads `unknown` and the board waits
  out the timeout. Unmeasured. The counts use the `origin/main` of the last fetch; the
  read does not fetch. Since `0052` a `current` unit has the *Integrate* button too, since
  only a press fetches; pressed on a unit that is really current, it leaves a `refused` row.
- **Every `pr` step costs one `gh pr list` before the session starts.** Since `0041`,
  under this machine's `gh` login, up to 30s (`integrate.GH_TIMEOUT`, chosen, not
  measured). Offline or logged out, the step still runs and its prompt says the lookup
  failed. The `pr` grant refuses `git rebase`, `git merge`, `git pull`,
  `gh pr update-branch` and a forced push (`--force`, `-f`, `--force-with-lease`,
  `--force-if-includes`, a `+` refspec) by their words. It also refuses a git alias,
  include or `GIT_CONFIG_*` made during the step, and `gh api` naming the update-branch
  endpoint (`pulls/<n>/update-branch`, `updatePullRequestBranch`). It does not refuse
  `gh api` as a whole, as the `integrate` grant does. `node -e`, or an alias defined before
  the step, still walks past (`coscc/policy_test.py`, `IntegrationIsNotPrs`).
- **Every `pr` step that is not stopped rewrites its pull request's title and body under
  this machine's `gh` login.** Since `0055`. After the step, `_sync_pr` reads `cos.mjs
  pr-text` and, when `pr.md` is `accepted` and names a pull request URL, runs `gh pr view`
  and — unless both already match — `gh pr edit` on the pull request `pr.md` names, holding
  the `done` row up to 60s (two calls, `prcomment.TIMEOUT` 30s each, chosen, not measured).
  It overwrites whatever a person changed on GitHub since, and keeps the old text nowhere
  (`.cos/0055_*/spec.md` C1). A `pr.md` edited by hand to name another repository's pull
  request is written there. The trace is one `pr-sync` row in the run log per step, with
  `existed` (the lookup before the step saw the pull request; `null` when that lookup could
  not answer — count those apart, not as `false`) and `outcome` `updated`,
  `already`, `failed` or `skipped`; no screen shows it. A `pr` step at a terminal leaves
  none.

- **`POST /api/units/hold` closes a pull request under this machine's `gh` login.** Since
  `0045`. `to: "dropped"` appends `### Dropped` under `intent.md ## Answers`, then runs
  `gh pr list` and `gh pr close <n>` for each open pull request whose head is the unit's
  branch (no `--delete-branch`), and `git worktree remove` without `--force` — a tree with
  uncommitted changes stays and the move reports `failed` for it. Each `gh` call waits up to
  30s (chosen). A side effect that failed cannot be retried from the board: `dropped →
  dropped` is refused, so the person closes the pull request or removes the tree by hand;
  the route's reply and the `hold` row on Activity say which. `paused` and `→ active` run
  no `git` and no `gh`. The block is read by `cos.mjs`: no stage is offered and every gate
  is closed, so anyone holding the password or a live session can stop every unit under a
  name they chose. The block also reaches every later stage's prompt as
  part of `intent.md`. An older `cos.mjs` reads the reason as the tail of the answer above
  it and offers the next stage again: downgrading past `0045` with held units is unsafe.
  `next_step` now asks `cos.mjs next` once more, files only, before opening a worktree
  (unmeasured). Since `0050` a move — and *Integrate* — is also refused while a step of
  the unit is still being prepared: the gate, the fetch (up to `FETCH_TIMEOUT` = 20s),
  `impl`'s `prepare` and `pr`'s `gh pr list`, a stretch whose length is unmeasured. A step
  in that phase is not on `/api/board/steps` and has no *Stop*, so the only thing to do is
  wait; the refusal says so and says since when.

- **An `impl` prompt carries file names taken from other people's commits on `main`.**
  Since `0042` `run_step` diffs the commit the unit's last `done` run of `plan` ran on
  (its `start` record's `head`) against the tree's `origin/main`, keeps the paths the
  plan's `## Files that change` names, and puts them in the prompt under *The files main
  changed since the plan* — so a name somebody merged reaches a paid session verbatim. Only
  names the plan already wrote can match, and landing one needs a merge to `main`. The
  diff reads `origin/main` as the step's own preparation left it and does not fetch: an
  `impl` re-run on a tree already on its branch measures against the last fetch, and
  `plan_drift.main_sha` in the `start` record says which. Since `0048` that preparation
  reuses a fetch under 30s old, so a merge landing in the 30s before `impl` starts is not
  in the diff either (`coscc/service_test.py`,
  `test_a_merge_under_thirty_seconds_after_the_plans_fetch_is_not_seen`). Anything that fails — no `done`
  run of `plan`, no section, a commit the tree lacks — is `checked: false` with a reason,
  never an empty list, and never stops the step. A step started at a terminal gets none of
  this.
- **`GET /api/board/running` tells anyone holding the password which units have a paid
  session open, and since when.** Since `0051` every card on the Board shows the step or
  integration running on it (stage, agent name, start time), or `ended, unknown` for a
  `start` in the run log with no `end`. It is near
  real time: each tab on the Board asks every 5s (`RUNNING_POLL`, chosen, not measured),
  reading the run log's `start` and `end` rows each time — the cost of that on a large
  run log, and against `busy_timeout` with ten sessions appending, is unmeasured; a busy
  read comes back as a `note`, not an error. What is running is kept in one process's
  memory (`Service._running`), like `_active` and `pull`: a step another copy of the app
  runs on the same working folder shows here as `ended, unknown` while it is still going,
  and a person may read that as dead and press run again. An `ended, unknown` row stops
  showing when the unit's next `start` is written or after 24 hours; nothing writes an
  `end` for it. A step started at a terminal has no entry either. The password is what stands in front; `COS_HOST=127.0.0.1` still narrows who can try it.

- **`GET /api/board/events` hands out everything a step saw.** Since `0073` every board step
  carries a recorder (`coscc/events.py`) fed by `Sessions._stream`, the grant's gate and the
  runner: every SDK message, refusal and outcome, each field cut at 64 000 characters, kept
  in memory while the step runs and written each second to `step_runs` and `step_events` in
  `cos.db` — never the run log. Whoever holds the password or a live session reads all of
  it, commands, paths, thinking and tool output included, for 30 days (`KEEP_DAYS`) and
  200 MB of stored JSON (`KEEP_BYTES`), and `0068`'s `updates/cos.db.bak` carries a copy.
  The purge runs only in `coscc/run.py` before the server starts (spec C5): between starts
  the total can pass 200 MB by any amount, and without a `VACUUM` the file never shrinks. A
  purge that fails prints one line and the app starts anyway. The runner's `end` record
  waits for the recorder's last write: up to 20 s when `cos.db` is busy (`4 * CLOSE_WAIT`,
  chosen, unmeasured), the card reading "running" and a *Stop* refused as already sealed all
  that time. What a running step holds in memory is unmeasured (C7). A second copy of the app on the same data root writes its
  steps' events into the same tables, but nobody can follow them live, and this copy reads
  them as `ended-unknown` while they run (C9). The watch pane holds at most `WATCH_WINDOW` =
  400 events, because every frame resends the whole list (`spike.md ## U4`: about 2 062
  bytes each at the collapsed size, 1 649 608 bytes the largest frame measured within 2 s on
  loopback); on a link under about 13 Mbit/s the delay will pile up (unmeasured). Each tab
  that opens the pane keeps a follower until the step ends, the pane closes, or it falls
  5 000 events behind; a closed tab is not noticed (`plan.md` Risk 4). The list is drawn by
  position, so a page prepended or a row dropped from the top rewrites rows in place.
  Neither route writes anything; both are behind the `0070` login.
- **`POST /api/backlog/*` writes the backlog's order, and `propose` opens a paid session, for
  whoever holds the password.** Since `0074`. `estimate`, `relation` and `shortlist` each
  append one run-log row (`estimate-value`, `relation`, `shortlist`) under a typed `by`; a
  person's name may not start with `agent:`, but a hand-edited row in `cos.db` can, and the
  board then takes it for an agent's (`plan.md` Risk 9). `propose` opens one session on the
  model of the Settings row `estimate` — no tools, 1 turn, $2.0, all chosen, and nobody has
  measured a prompt carrying ~70 units — and writes `start`/`end` rows with `unit: ""` and
  `stage: "estimate"`, one `estimate` row, and each valid part of the reply. A second press in
  the same workspace is refused, in this process only (`_active`). *Áp dụng* waits for it
  like an integration. No gate, no `next` and no run button reads any of it; every board
  step's `start` row carries `shortlist` (R14), read only by `verify_0074 --measure`. A step
  started at a terminal has none, so the outcome's measurement cannot see it (`spec.md ##
  Answers, câu 3`). The password is what stands in front; `COS_HOST=127.0.0.1` still narrows
  who can try it.

- **`POST /api/update/*` stops work, restarts the app and builds upstream code, for whoever
  holds the password.** Since `0068`. *Áp dụng ngay* stops every board step (through Stop's road, so
  each that had begun gets an `end` with `stopped_by`; one cut before its first turn gets
  none) and cuts every chat turn of this process; *Áp
  dụng* waits for them instead, and a person can keep it waiting forever by starting new
  work. *Build từ origin/main* runs `scripts/build_wheel.sh` of the configured workspace's
  upstream `main` under this user — `uv sync`, Reflex fetching Node/Bun, all of it. The
  source of a release is a constant and a wheel is installed only after its sha256
  matched, but that checksum comes from the same release (spec C5). After a trial run on
  `127.0.0.1`, the app calls `Service.shutdown` and `Sessions.close_all` itself (the
  lifespan never runs on the real stack, `spike.md ## U5`), stops uvicorn from inside,
  installs offline in `main`, and exits 75: systemd logs it as a failure and `NRestarts`
  grows, which `install.sh` reads as a crash loop at 2 (spec C7, unmeasured). A new
  version that passes the trial and still fails to start is not rolled back by anything;
  the update's log holds the command, and restoring `updates/cos.db.bak` loses what the new
  version wrote (spec C1, C8). The trace is the `update` rows in the run log (workspace
  `""`, `by` a typed name) and `<COS_DATA_DIR>/updates/logs/`. The password is what stands in front; `COS_HOST=127.0.0.1` still narrows who can try it.
  Since `0070` the trial clears the password on its copy of `cos.db` with `coscc
  reset-password`, reads the setup token off the trial's output (written to the update's
  log as `<redacted>`), sets a throwaway password through `POST /setup` and needs `200`
  from `/api/workspaces` and `/` with that cookie. The updater of a release before `0070`
  asks `/api/workspaces` for `200` without a cookie, so the first update from the board
  past `0070` always fails its trial: that release is installed with `curl … | sh`.

## The proofs, and what each one costs

| | |
|---|---|
| `verify_0001.py` | creates real sessions |
| `verify_0002.py` | clones, creates sessions |
| `verify_0003.py` | browser, needs `COS_PORT` free; no session, no quota. Since `0070` the page it opens is the login page: not rewritten, so it fails until something logs it in (`.cos/0070_*/spec.md` C7) |
| `verify_0004.py` | 4 processes at once, creates a session |
| `verify_0005.py` | **pushes a branch and opens a PR.** Needs `COS_PROOF_REPO`; unset is exit 2 with claims 2, 3, 4, 6 skipped. Eight sessions, one with a $5 ceiling |
| `verify_0006.py` | browser, needs `COS_PORT` free; sends one short prompt. Since `0070` it meets the login page and was not rewritten (C7) |
| `verify_0011.py` | **needs another machine.** `COS_PROOF_TARGET`, an SSH destination it reboots twice; unset is exit 2 |
| `verify_0012.py` | measures the **installed** copy, not this checkout. Needs `node`, a running service at `COS_URL` and one workspace; no session, no quota. Since `0070` the service answers it `401`: not rewritten (C7) |
| `verify_0013.py` | reads git history into a **temporary** data root, never `~/.cos`. No session, no quota, no network. Run it plain and it is exit 1 by design — `--import` is what fills the log and makes it exit 0 |

| `verify_0014.py` | **spends real money and merges a real pull request.** Needs `COS_PROOF_REPO`, a throwaway repo; unset is exit 2. Five sessions — measured $3.28 and 11m49s end to end, 2026-09-22. `--dry` stops before the first paid step |
| `verify_0016.py` | no session, no quota, no network; temporary data root. Needs `node` and `uv`; either missing is exit 2 |
| `verify_0017.py` | no session, no quota, no network; temporary data root, bare-directory remote. `--this-repo` prepares a worktree of this checkout (`uv sync`, `npm ci`, a build: 20s measured 2026-09-23) and runs `npm test` twice. `--paid` **spends real money**: two real `impl` sessions on a clone of `COS_PROOF_REPO`; unset is exit 2 |
| `verify_0019.py` | no session, no quota, no network; temporary data root, bare-directory remote, a fake `gh` first on `PATH`. Needs `node`, `uv` and `git`; any missing is exit 2. The session and its transcript are stand-ins, so it cannot see a real budget stop's `terminal_reason` or a transcript read before it is flushed |
| `verify_0021.py` | no session, no quota, no network; temporary data root and a fake `gh` first on `PATH`. Needs `node`, `uv` and `git`; any missing is exit 2 |
| `verify_0024.py` | no session, no quota, no network; temporary data root, bare-directory remote (since `0056`: without one, every claim that opens a unit's tree on its branch failed from `0030` on) and a fake `gh` first on `PATH`. Needs `node`, `uv` and `git`; any missing is exit 2. Drives `StudioState`'s own handlers through Reflex's event processor, in-process; no browser, so the compiled page is not exercised |
| `verify_0025.py` | no session, no quota, no network; temporary data root. Needs `node` and `uv`; either missing is exit 2 |
| `verify_0034.py` | plain: no session, no quota, no network; temporary data root, the session replaced, the app driven in-process over ASGI (the dropped NDJSON client is a raw `http.disconnect`). Needs `node`; missing is exit 2 |
| `verify_0034.py --paid` | **spends real money**: three sessions through `Sessions.stream(step=...)` — two short `claude-haiku-4-5` ones, run to its end and closed after its first chunk, and one `claude-sonnet-5[1m]` stopped while it runs `sleep 47` through `Bash` (haiku holding `Bash` was refused with a long-context 400, measured 2026-09-24); counts the bundled `claude` processes under its own PID 10s later, the third from the Stop. No `/proc` is exit 2 |
| `verify_0035.py` | no session, no quota, no network; temporary data root, bare-directory remote, a fake `gh` first on `PATH` whose `pr update-branch` really rebases in a scratch clone. Needs `node`, `uv` and `git`; any missing is exit 2. Proves the mechanical road only: `--paid` (a real Gebo session) is not built and exits 2. Since `0052` the app's `Sessions` is a stand-in from the start of `run`, since a refused `update-branch` now reaches Gebo, and one more claim presses a unit counted `current` against a stale `origin/main` |
| `verify_0037.py` | plain: no session, no quota, no network; temporary data root, only the SDK client replaced. Claim (b) calls the SDK's private `SubprocessCLITransport._build_command`; if that cannot be called it is exit 2, not a pass. `--baseline` and `--measure` read `<COS_DATA_DIR>/cos.db` (`mode=ro`, never through `Data`) and `~/.claude/projects/*/<session>.jsonl`, and write only to `<COS_DATA_DIR>/measurements/`; too few sessions to compare is exit 2. `--paid` **spends real money**: six `claude -p` runs, three per branch. No `claude` on `PATH` is exit 2 |
| `verify_0041.py` | plain: no session, no quota, no network; temporary data root and a fake `gh` first on `PATH`. Needs `git` and `uv`; either missing is exit 2. `--measure` reads `<COS_DATA_DIR>/cos.db` (`mode=ro`) and the store's `pr.md` files, and writes only to `<COS_DATA_DIR>/measurements/`; fewer than five `pr` steps since `0041` is exit 2. `--paid` **spends real money, pushes to `main` of `COS_PROOF_REPO` and leaves two pull requests open there**: two real `pr` steps; unset is exit 2. Not run when it was written |
| `verify_0042.py` | no session, no quota, no network; temporary data root, bare-directory remote, a fake `gh` first on `PATH`. Needs `node`, `uv` and `git`; any missing is exit 2. The session is a stand-in, so it cannot show that a real `impl` stops on a contradiction |
| `verify_0045.py` | no session, no quota, no network; temporary data root, bare-directory remote, a fake `gh` first on `PATH` that logs every call. Needs `node`, `uv` and `git`; any missing is exit 2. Drives `POST /api/units/hold` in-process. Does not touch the real `0032`: pausing it from the board is a person's measurement after ship |
| `verify_0047.py` | no session, no quota, no network; temporary data root. Needs `node` and `uv`; either missing is exit 2. Fixes the board's `today` at 2026-10-08 for C5 and C6; does not measure the intent's outcome, which is three real units on the real board that day |
| `verify_0048.py` | no session, no quota, no network; temporary data root, bare-directory remote, a fake `gh` first on `PATH`. Needs `node`, `uv` and `git`; any missing is exit 2. Exit 2 also when `--baseline` reproduces no ref-lock race |
| `verify_0051.py` | no session, no quota, no network; temporary data root, bare-directory remote, a fake `gh` first on `PATH` that refuses everything. Needs `node`, `uv` and `git`; any missing is exit 2. Drives `StudioState` as `verify_0024` does, so the compiled page and its socket are not exercised; the ten steps go through `POST /api/board/run` on the in-process ASGI app, never through a second copy of the app, so the one-process limit is not measured |
| `verify_0055.py` | no session, no quota, no network; temporary data root, bare-directory remote, a fake `gh` first on `PATH` that keeps one pull request's title and body and logs every argv and stdin. Needs `node`, `uv`, `git` and a merge-base with `origin/main` (C7 runs the `cos.mjs` there); any missing is exit 2. C8 runs the terminal line of `write-pr/SKILL.md` step 5 with `bash -c`. Does not measure the intent's outcome — a trial on the real board before 2026-10-15 |
| `verify_0056.py` | browser, no session, no quota; needs `COS_PORT` free and a bundle built for it (`COS_HOST=127.0.0.1 COS_PORT=18756 uv run coscc-build`), temporary data root, two bare-directory remotes, a seeded session past the `0070` login. Measures (a) paste, (b) reload and (c) Back/Forward at nine addresses, and R8–R11, R14, R15, and — in place of R20, whose three proofs meet the login page — `/` opening Overview on a live socket. One width, 1440×900. `--url <base>` measures a running app with `COS_PROOF_PASSWORD`, needs two workspaces with a unit each, writes nothing and prints `SKIP` for R11 and R15; it has never been run against an install from `install.sh` |
| `verify_0053.py` | browser, no session, no quota, no network; needs `COS_PORT` free and a bundle built for it (`COS_HOST=127.0.0.1 COS_PORT=18753 uv run coscc-build`), a temporary data root, a fake `gh` first on `PATH` and `CLAUDE_CONFIG_DIR` in the temporary folder. Counts the socket's received bytes in chromium (CDP) for a load of 42 and of 200 cards and for a change of workspace; the fixture is this checkout's fourteen `.cos/` units repeated under `1000+` numbers. (f) R3 and (i) C4 drive `StudioState` in-process with the session replaced and count re-serialized deltas, not frames. `--url <base> --ws <name> --from <name>` measures a running app with `COS_PROOF_PASSWORD`, writes nothing and has no (f): run it at a terminal, since a step sees only its scratch data root (`0076`). Does not measure the intent's outcome — the real workspace on the installed app by 2026-10-16 |
| `verify_0060.py` | plain: no session, no quota, no network; temporary directory with a fixture run log and fixture transcripts. Needs `git` (it loads `coscc/policy.py` from `01699b8` with `git show`) and `bash` (`type -t`); either missing, or that commit absent from a shallow clone, is exit 2. `--measure --since --until [--confirmed FILE]` reads `<COS_DATA_DIR>/cos.db` (`mode=ro`) and `COS_TRANSCRIPTS_DIR`, and writes only to `<COS_DATA_DIR>/measurements/`; no session in the window is exit 2. It does not import `coscc`. "Fake" depends on the `PATH` of the machine running it |
| `verify_0061.py` | plain: no session, no quota, no network; temporary directory. Needs `node` and `git` (it loads `cos.mjs` from `git merge-base HEAD origin/main`) and `uv`; a missing one, or no merge-base, is exit 2. `--root <dir>` (repeatable) adds a store such as `~/.cos/units/<slot>` to the comparison. `--measure` reads every `<COS_DATA_DIR>/units/*/.cos/` through `cos.mjs status --json` and writes only to `<COS_DATA_DIR>/measurements/`; no merge line yet (this unit's `ship.md`) or fewer than 5 units shipped after it is exit 2. Kind (b) of the intent's wasted round is a person reading pull request history; it does not conclude it |
| `verify_0068.py` | plain: no session, no quota, no network; temporary data root, a fake `uv` (a shell script whose "installed" `coscc` serves 200 on a port) and `verify_0034`'s stand-in session, app driven in-process over ASGI. Needs `node` and `git`; either missing is exit 2. `--restart` builds two wheels of `HEAD` with `scripts/build_wheel.sh --local` in temporary worktrees, installs one with the real `uv` into a temporary tool dir, plays systemd itself (restart 2 s after a non-zero exit) and drives chromium: **needs the network** for the trial install, port 18790 free, and takes a few minutes. It does not measure the intent's outcome — two real updates on an `install.sh` machine. Since `0070` the fake `coscc` plays the login door for the trial; `--restart`'s browser was not taught to log in and was not run |
| `verify_0070.py` | plain: no session, no quota, no network; temporary data root. Composes the real Reflex app in-process as `run.py` serves it, so it **needs `uv run coscc-build` first** (no bundle is exit 2). Sets a password through `/setup`, walks every registered route and counts the ones that answer without a session; also measures R8 on the real `/_event` socket. `--url` counts against a running service at `COS_URL` from this checkout's route list, sends no `POST /login`, and is exit 2 while that service has no password. `--browser` starts `coscc.run` on a temporary root and drives chromium through `/setup`, the board's `/_event`, *Đăng xuất* and back to `/login`, through `127.0.0.1` and through this machine's first non-loopback address, then removes the session under an open board and needs it on `/login` within 20 s: needs `COS_PORT` free, bound off loopback, and a bundle built for it (`COS_PORT=18791 uv run coscc-build`). A step the app starts inherits `__REFLEX_*` blank, and `run.py`'s `setdefault` keeps a blank mount flag — no page, `/` a 404 — so `--browser` drops them; `verify_0003`/`0006` do not |
| `verify_0071.py` | browser, needs `COS_PORT` free and a bundle built for it; no session, no quota; temporary data root and a bare-directory remote. Writes a password hash and one session into that root before the app starts, so it passes the `0070` login without `/setup`, and drops blank `__REFLEX_*` as `verify_0070 --browser` does. Its `F<n>` unit carries a `pr.md` naming `github.com/o/r`; whether a board read asks `gh` about it, and so reaches the network, was not measured. Presses *Send this answer* in (a), (b), (c), double-clicks it on an `F<n>`, and presses a refused *Pause*, at 1280×900 and 390×844, and measures each message in view inside the dialog after scrolling it to the bottom. Does not measure the intent's outcome — a person's trial on the real board before 2026-10-08 |
| `verify_0073.py` | plain: no session, no quota, no network; temporary data root, a workspace that is not a git checkout, only `ClaudeSDKClient` in `coscc.sessions` replaced by a scripted client, the app driven in-process over ASGI. Needs `node`; missing is exit 2. R1–R9, R13–R15; integrate is not run (it needs `git` and `gh`). `--browser` starts `coscc.run` on `COS_PORT` (18773) with the same kind of client and a password and two sessions written into a temporary root, and drives chrome through two contexts that share no cookie: needs the port free and a bundle built for it (`COS_HOST=127.0.0.1 COS_PORT=18773 uv run coscc-build`). R11's latency is read from each seq's first appearance in the DOM, added or rewritten in place, so it covers a list at `WATCH_WINDOW` too; rows that arrive while B reads older ones are counted, not timed. F5 and F6 measure the scroll position on loopback, where F6 (b)'s race (a live batch landing between *older* and its page) is exercised but was not seen to happen. Does not measure the intent's outcome — a person's two-browser trial before 2026-10-15 |
| `verify_0074.py` | plain: no session, no quota, no network; temporary data root, the session replaced, the app driven in-process over ASGI. Needs `node`; missing is exit 2. R15 runs `cos.mjs gate` for every stage and `next` for every fixture unit before and after the three kinds of record. `--measure` reads `<COS_DATA_DIR>/cos.db` (`mode=ro`) and this unit's `ship.md` under `<COS_DATA_DIR>/units/*/.cos/`, and writes only to `<COS_DATA_DIR>/measurements/`; no merge line, or the 14-day window still open, is exit 2. Run it at a terminal: inside a step it reads a scratch data root (`0076`). It does not print the `lệch` count of each shortlist in use: that needs the board as it stood then |
| `verify_0076.py` | plain: no session, no quota, no network; a temporary `HOME` whose `~/.cos` plays the running app, a child process standing in for a step with the environment `sessions.child_env` builds. Needs `httpx` and `coscc` importable; either missing is exit 2. `--suite` runs `npm test` in a session's environment with the `cos.db` `from_env` names protected, and reads that database (`mode=ro`) before and after: **run it at a terminal**, a step is not to read the real one. Does not measure the intent's outcome — a real `impl` step from the board on a branch that raises the schema, before 2026-10-31 |
| `verify_stage_models.py` | no session, no quota, no network; temporary data root, `COS_MODEL` removed, a fake `gh` first on `PATH`. Needs `node`, `uv` and `git`; any missing is exit 2. Drives `StudioState`'s handlers as `verify_0024` does. `--paid` **spends real money**: since `0031_shipped-model-defaults-cap-every-stage-at-200k` it calls `claude -p` once per distinct id `coscc/models.json` ships (currently two: `claude-opus-5-5[1m]` and `claude-sonnet-5[1m]`) and requires `modelUsage[...].contextWindow` to read 1000000 for each. No `claude` on `PATH`, or a login that does not work, is exit 2; the CLI reporting an error for that model id is exit 1 |
| `verify_state_it_describes.py` | browser, needs `COS_PORT` free; no session, no quota, no network. The remote is a bare directory in a temp folder. Proof of the store's `0001_product-describes-a-state-it-is-not-in`, not of `.cos/0001_*` — hence the name. Since `0070` it meets the login page and was not rewritten (C7) |

Exit codes: `0` pass, `1` the page is broken, `2` the environment is not ready.

`verify_0003`, `verify_0006`, `verify_0056` and `verify_0071` open a real browser on `COS_PORT`. **In a
checkout** the bundle hardcodes its own address, so none can move to a spare port without a
bundle built for it: stop the app first, or build for another port, and never run two of
them at the same time. A wheel installed by `install.sh` behaves the other
way — `coscc/frontend.py` rewrites the address at startup, because a packaged install has
no Node to rebuild with. Both sentences are true; which one applies depends on which of the
two shapes you are looking at, and `coscc/run.py` is where they part.

A figure carries across a rewrite only if the mechanism did not change. `0006 spec.md` C2
says this about swapping the store; it holds the same way for swapping the interpreter.
