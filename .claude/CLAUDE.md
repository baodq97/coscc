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

The `cos_baodo/` web app lists, creates and resumes Claude Code sessions across projects
(`0002`), manages the workspaces themselves — add, label, remove, clone, pull latest
(`0003`), and shows each workspace's work units as a board whose steps it can run
(`0008`). It binds loopback only, and its sessions are **chat only — no tools** by default;
`cos_baodo/config.py` is the single place that reads configuration, and the defaults there
are a safety posture rather than a suggestion. The one exception is a board step set to
`autonomous`, which gets a named, bounded grant from `cos_baodo/policy.py` — never from the
config. Each session it creates spends account quota, so nothing that talks to it belongs
in an unattended loop.

`COS_WORKING_DIR` is the one root under which workspaces may be created, and it is
**deliberately not settable over HTTP** — there is no setter outside `from_env`, so a
request has no path to it. A stored workspace is a *name*, never a path; the path is built
from the root on every read, which is why a hand-edited store cannot point the app at
`/etc`. Leave `COS_WORKING_DIR` unset and the app behaves exactly as `0002` did.

```
uv run cos-build                                          # build the page first
COS_WORKING_DIR=~/projects uv run cos-baodo               # then http://127.0.0.1:8790
uv run python scripts/verify_0002.py                      # proof for 0002; creates real sessions
uv run python scripts/verify_0003.py                      # proof for 0003; clones, creates sessions
uv run python scripts/verify_0004.py                      # proof for 0004; needs a browser and a free port
uv run python scripts/verify_0005.py                      # proof for 0005; 4 processes at once, creates a session
COS_PROOF_REPO=<url> uv run python scripts/verify_0008.py # proof for 0008; runs a whole unit, pushes, opens a PR
```

`verify_0005.py` spawns four copies of itself writing to one working folder and checks
that all 20 entries survive, then opens a real session and checks that `pull` refuses
while it is live. Concurrent writes to the workspace list are locked with `flock` on a
file beside the store, and the wait is bounded at 10 seconds — a busy folder gives an
error naming it, never a hang. **The lock and the `pull` refusal both cover this process
only.** Two copies of the app on one working folder still see past each other for
sessions, so `pull` can change files under the other's turn; that is recorded in
`.cos/0005_silent-concurrent-loss/spec.md` C2 and not fixed.

`verify_0004.py` is the only check that opens the page in a real browser, and the only one
that needs `COS_PORT` free — the bundle hardcodes its own address, so this proof cannot
move to a spare port the way the others do. Stop the app before running it, or build and
run both at another port. Its exit codes are worth knowing: `0` pass, `1` the page is
broken, `2` the environment is not ready (no browser, stale build, port in use). It creates
no session, so unlike the other two it spends no quota.

**The board (`0008`).** The page also shows every work unit of the open workspace as eight
cells, and can run a step. Three modules carry it, and the split is the point:

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
- `cos_baodo/journal.py` is an append-only JSONL log beside the store — modes, starts,
  finishes, denials and cost. Appends are `flock`-ed and `O_APPEND`, so four processes
  writing at once keep all their records.

The six prose stages get **no tools in either mode**. A session with no tools cannot write
a file, so for those the app writes the artifact from the reply and the session only
returns text. `.cos/0008_hand-driven-invisible-loop/plan.md` Risk 1 records that this
contradicts one sentence of that unit's `## Design`, and why the sentence is the wrong half.

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
