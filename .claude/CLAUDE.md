# cos-baodo

Local AI-native SDLC harness, and the template for it.
Reference: `.claude/harness.md`. Each stage's rules live in its own skill.

## Commands

```
npm test                                          # every test, both runtimes
uv sync                                           # Python deps, after a fresh clone
uv run reflex export --frontend-only --no-zip     # build the page; see "Build step" below
node .claude/scripts/cos.mjs status               # where every unit stands
node .claude/scripts/cos.mjs gate <unit> <stage>  # exit 0 = stage may proceed
node .claude/scripts/cos.mjs new-path <slug>      # next work unit path
```

Tests must be green before any task is reported complete; never skip or delete a failing
one. There is no linter; do not invent a command for one.

**Build step.** There is one, since `0003`. The page is Reflex, which compiles to
JavaScript, and `uv run cos-baodo` refuses to start until it has been built. Nothing runs
it automatically: `npm test` does not, because the proofs drive the ASGI app in-process and
never need a compiled frontend — that is deliberate, and it is what keeps the test command
free of a JavaScript toolchain. Run it by hand after changing anything under
`cos_baodo/cos_baodo.py`, and before running the app.

`npm test` covers both runtimes: `test:node` over `channel/` and `.claude/scripts/`, then
`test:python` over `cos_baodo/`. Adding a Python test file under `cos_baodo/` named
`*_test.py` is enough to be picked up. This is what keeps "tests must be green" meaning
something now that the repository has two languages in it — verified once, on 2026-09-21,
by making a Python test fail and watching `npm test` go red.

The `cos_baodo/` web app lists, creates and resumes Claude Code sessions across projects
(`0002`), and manages the workspaces themselves — add, label, remove, clone, pull latest
(`0003`). It binds loopback only, and its sessions are **chat only — no tools** by default;
`cos_baodo/config.py` is the single place that reads configuration, and the defaults there
are a safety posture rather than a suggestion. Each session it creates spends account
quota, so nothing that talks to it belongs in an unattended loop.

`COS_WORKING_DIR` is the one root under which workspaces may be created, and it is
**deliberately not settable over HTTP** — there is no setter outside `from_env`, so a
request has no path to it. A stored workspace is a *name*, never a path; the path is built
from the root on every read, which is why a hand-edited store cannot point the app at
`/etc`. Leave `COS_WORKING_DIR` unset and the app behaves exactly as `0002` did.

```
uv run reflex export --frontend-only --no-zip             # build the page first
COS_WORKING_DIR=~/projects uv run cos-baodo               # then http://127.0.0.1:8790
uv run python scripts/verify_0002.py                      # proof for 0002; creates real sessions
uv run python scripts/verify_0003.py                      # proof for 0003; clones, creates sessions
```

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

One unit of work per `.cos/NNNN_<slug>/` directory, holding `intent.md`, `spec.md` and
`plan.md` and nothing else.

`write-intent` → `write-spec` → `write-plan`, each gated on the one before.
`cos-status` reports where everything stands.

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
