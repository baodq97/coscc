# cos-baodo

Local AI-native SDLC harness, and the template for it.
Reference: `.claude/harness.md`. Each stage's rules live in its own skill.

## Commands

```
npm test                                          # every test, both runtimes
uv sync                                           # Python deps, after a fresh clone
uv run cos-build                                  # build the page; see "Build step" below
node .claude/scripts/cos.mjs status               # where every unit stands
node .claude/scripts/cos.mjs gate <unit> <stage>  # exit 0 = stage may proceed
node .claude/scripts/cos.mjs new-path <slug>      # next work unit path
```

Tests must be green before any task is reported complete; never skip or delete a failing
one. There is no linter; do not invent a command for one.

**Build step.** There is one, since `0003`. The page is Reflex, which compiles to
JavaScript. Build with `uv run cos-build`, not `reflex export` — the wrapper records a
fingerprint of what it built from, and both `cos-baodo` and `scripts/verify_0004.py`
**refuse to run against a bundle that does not match the source**. Without that, editing
the page and forgetting to rebuild leaves every check passing against the previous bundle.
Nothing runs the build automatically: `npm test` does not, because `verify_0002` and
`verify_0003` drive the ASGI app in-process and never need a compiled frontend — that is
deliberate, and it is what keeps the test command free of a JavaScript toolchain.

`npm test` covers both runtimes: `test:node` over `channel/` and `.claude/scripts/`, then
`test:python` over `cos_baodo/`. Adding a Python test file under `cos_baodo/` named
`*_test.py` is enough to be picked up. This is what keeps "tests must be green" meaning
something now that the repository has two languages in it — verified once, on 2026-09-21,
by making a Python test fail and watching `npm test` go red.

The `cos_baodo/` web app serves **one page** at `/`: six screens — Overview, Workspaces,
Board, Sessions, Activity & usage, Settings — built from Reflex Python components
(`cos_baodo/screens.py`), with all of their state in `cos_baodo/state.py` and all of their
logic behind `cos_baodo/service.py`. It lists, creates and resumes Claude Code sessions
across projects (`0002`), manages the workspaces themselves (`0003`), and shows each
workspace's work units as a board whose steps it can run (`0008`). `0011` replaced both the
page `0002`-`0008` built and `0009`'s `/prototype` with this one; a handler that decides
anything is a bug in `service.py`, not in the page.

It binds loopback only, and its sessions are **chat only — no tools** by default;
`cos_baodo/config.py` is the single place that reads configuration, and the defaults there
are a safety posture rather than a suggestion. The one exception is a board step set to
`autonomous`, which gets a named, bounded grant from `cos_baodo/policy.py` — never from the
config. Each session it creates spends account quota, so nothing that talks to it belongs
in an unattended loop.

**Two roots, and they are not the same thing.** `COS_DATA_DIR` (default `~/.cos`) holds the
app's own state: `cos.db` and `objects/`. `COS_WORKING_DIR` holds the workspaces — somebody
else's git checkouts. Backing up one does not back up the other, and the Settings screen
prints both for that reason. **Neither is settable over HTTP**: `config.from_env` is the
only reader of the environment and there is no setter, so a request has no path to either.
A stored workspace is a *name*, never a path; the path is built from the root on every
read, and after `0011` the table has no column for one — which is why a hand-edited store
cannot point the app at `/etc`. Leave `COS_WORKING_DIR` unset and the app behaves exactly
as `0002` did, except that it now has somewhere to remember things.

**Storage (`0011`).** `cos_baodo/data.py` owns the data directory, the connection and the
schema; it is the only module that knows where anything is. Three settings there are load
bearing and none is a default: WAL, a 10-second `busy_timeout` **issued as the first
statement on every connection**, and `BEGIN IMMEDIATE` around every read-modify-write.
Getting the order wrong was measured on 2026-09-22 — `PRAGMA journal_mode=WAL` before
`busy_timeout` failed about one run in ten with `database is locked`. The schema version
lives in `PRAGMA user_version`, so opening an existing database is one read and no lock.
`cos_baodo/objects.py` stores blobs under their own SHA-256, written through a `rename`.
`Store` and `Journal` kept their interfaces and changed their backing; a `.cos-baodo.json`
or `.cos-journal.jsonl` from before `0011` is imported once and **never deleted**.

```
uv run cos-build                                          # build the page first
COS_WORKING_DIR=~/projects uv run cos-baodo               # then http://127.0.0.1:8790
uv run python scripts/verify_0002.py                      # proof for 0002; creates real sessions
uv run python scripts/verify_0003.py                      # proof for 0003; clones, creates sessions
uv run python scripts/verify_0004.py                      # proof for 0004; needs a browser and a free port
uv run python scripts/verify_0005.py                      # proof for 0005; 4 processes at once, creates a session
COS_PROOF_REPO=<url> uv run python scripts/verify_0008.py # proof for 0008; runs a whole unit, pushes, opens a PR
uv run python scripts/verify_0011.py                      # proof for 0011; browser, free port, one short prompt
```

`verify_0005.py` spawns four copies of itself writing to one working folder and checks
that all 20 entries survive, then opens a real session and checks that `pull` refuses
while it is live. It was re-run on SQLite on 2026-09-22 and still measures 20 of 20 —
`0011 spec.md` C2 is explicit that swapping the mechanism does not carry the old proof
across. **The `pull` refusal covers this process only.** Two copies of the app on one
working folder still see past each other for sessions, so `pull` can change files under
the other's turn; that is recorded in `.cos/0005_silent-concurrent-loss/spec.md` C2 and not
fixed. Concurrent *writes* are now SQLite's problem rather than `flock`'s.

`verify_0004.py` and `verify_0011.py` are the two checks that open the page in a real
browser, and the only ones that need `COS_PORT` free — the bundle hardcodes its own
address, so they cannot move to a spare port. Stop the app before running either, and do
not run them at the same time. Exit codes: `0` pass, `1` the page is broken, `2` the
environment is not ready. `verify_0004.py` holds the floor — declared theme, three widths,
colour mode that survives a reload, AA contrast — and its negative control proves it can
still go red; it creates no session and spends no quota. `verify_0011.py` drives the five
flows of `0011 intent.md` on real data, restarts the app and checks all five again; it
sends **one** short prompt and never presses the run button.

**The board (`0008`).** Every work unit of the open workspace as eight cells, and it can
run a step. Three modules carry it, and the split is the point:

- `cos_baodo/board.py` reads a workspace's `.cos/` by running **this repository's**
  `.claude/scripts/cos.mjs` with `--root`. It never runs the `cos.mjs` inside the
  workspace — that file belongs to a repository somebody cloned. Same reasoning applies to
  the stage rules: `cos_baodo/runner.py` builds its prompts from **this** repository's
  `.claude/skills/`, never the workspace's.
- `cos_baodo/policy.py` is the grant table: what a step may do, keyed by `(stage, mode)`.
  It is deliberately **outside `Config`**, so the four knobs keep meaning what they meant.
  The default is the locked position: no tools, no commands, one turn, no budget. Only
  `("impl", "autonomous")` and `("pr", "autonomous")` carry anything, and `pr` carries a
  warning string that the page shows before the button is pressed, because its capability
  comes from this machine's own `gh` login and reaches every repository that login reaches.
- `cos_baodo/journal.py` is the run log — modes, starts, finishes, denials and cost. Since
  `0011` it is rows in `cos.db` rather than a JSONL file.

The board's four lanes do **not** use the harness's `blocked` flag. Measured on 2026-09-22:
`cos.mjs` returns `blocked: true` for every unit that is not finished
(`.claude/scripts/cos.mjs:122-135`), so mapping it onto a lane called *Needs review* puts
every unfinished unit there and leaves the other lanes empty. `cos_baodo/state.py` reads
the lanes off the artifact statuses instead.

The six prose stages get **no tools in either mode**. A session with no tools cannot write
a file, so for those the app writes the artifact from the reply and the session only
returns text. `.cos/0008_hand-driven-invisible-loop/plan.md` Risk 1 records that this
contradicts one sentence of that unit's `## Design`, and why the sentence is the wrong half.
The Settings screen says it on the page, because otherwise it looks like the agent wrote
the file.

`verify_0008.py` is the only proof that pushes anything anywhere. It needs `COS_PROOF_REPO`
set to a repository you are willing to have it push a branch to and open a pull request on;
there is no default, and unset means exit 2 with claims 2, 3, 4 and 6 skipped. Claims 1, 5
and 7 still run without it. It spends real quota — eight sessions, one with a $5 ceiling.

**The build bakes in the port.** The compiled page hardcodes the address it opens its
`/_event` WebSocket against, so a build made for one port serves a page that renders and
then shows "Connection Error" with a perfectly healthy API behind it. Build and run with
the same `COS_HOST`/`COS_PORT`; `cos-baodo` refuses to start if they disagree. This was
found on 2026-09-21 by driving the page with a browser — no HTTP-level check could see it.

**Start it with `cos-baodo`, not `reflex run`.** Reflex's dev mode serves the page from a
vite server that binds every interface, and 0.9.11 has no setting for its host — measured
on 2026-09-21, `ss -ltn` showed `*:3000`. `cos-baodo` mounts the compiled frontend into the
same ASGI app as the API and binds one loopback port, so there is one socket to check.

Run the web channel from an interactive session. Two `--print` runs on 2026-09-21 loaded
the server but never registered the channel, with no log line and no error. The documented
behaviour is that `-p` is supported, so the likely cause is the development flag's
confirmation dialog, which cannot be drawn headlessly — unverified, and worth retesting
before anyone builds on it:

```
claude --dangerously-load-development-channels server:webchannel   # then http://127.0.0.1:8789
```

## The loop

One unit of work per `.cos/NNNN_<slug>/` directory, holding its artifacts and nothing else:
`idea.md`, `intent.md`, `spec.md`, `plan.md`, `impl.md`, `pr.md`, `review.md`, `ship.md`.
Any other file in that directory is reported as a problem.

`write-idea` → `write-intent` → `write-spec` → `write-plan` → `write-impl` → `write-pr` →
`write-review` → `write-ship`, each gated on the one before. `idea` is optional and gates
nothing; `plan.md: done` is terminal, which is what kept the five units closed under the
old three-stage loop reading as finished when `0008` widened it to eight.

`cos-status` reports where everything stands. `.claude/scripts/cos.mjs:24-33` is the one
place the loop is defined — the table in `.claude/harness.md` restates it, nothing else may.

## Invariants

- Set `Status: accepted` when the artifact is finished, then commit it. `accepted` records
  that the agent judged it ready — it is not a human's approval and must not be read as one.
- There is no review step. Commits land on `main` and `accepted` is self-issued, so the only
  things still checking the work are `cos.mjs gate`, the tests, and the invariants in each
  skill. Treat those as the last line, not as formalities.
- Ask `cos.mjs gate` before a stage, and stop when it exits non-zero. Fix what it names;
  do not reason your way past it.
- No code while `plan.md` is `draft`. Accept the plan in its own commit first, so the
  authorization is separable from the thing it authorizes.
- Take work unit paths from `cos.mjs new-path`. Never guess a number.
- Cut a figure that has no source. Do not soften it.
- Cite only a file committed in this repository, by path and line range.
- Inside `.cos/`: English filenames and headings, Vietnamese prose. Everywhere else,
  English.
