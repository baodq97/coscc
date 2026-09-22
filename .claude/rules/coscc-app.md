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

`policy.py` is the grant table, keyed by `(stage, mode)`, and it sits **outside `Config`**
so the four knobs keep meaning what they meant. Default is the locked position: no tools,
no commands, one turn, no budget.

## Hazards

- **Sessions spend account quota.** Nothing that talks to the app belongs in an unattended
  loop.
- **`("pr", "autonomous")` reaches further than this repository.** Its capability comes
  from this machine's `gh` login, so it reaches every repository that login reaches. The
  page shows a warning string before the button is pressed; do not remove it.
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
- **The six prose stages get no tools in either mode.** A session with no tools cannot write
  a file, so the app writes the artifact from the reply and the session returns text only.
  Settings says so on the page, because otherwise it looks like the agent wrote the file.
  `.cos/0005_hand-driven-invisible-loop/plan.md` Risk 1 records why.
- **A `coscc/_harness/` left in a checkout shadows `.claude/`.** Both are gitignored and
  both are built, not committed, so `git status` stays clean while the app reads the stale
  copy — edit a skill, and the step still runs the old text. `coscc/harness.py` prefers the
  packaged tree on purpose (a wheel has no checkout to fall back to); the cost is this.
  `rm -rf coscc/_harness` after building a wheel by hand. The same is true of
  `coscc/_web/`, where it costs a stale page instead of stale rules.
- **`pull` refuses only within this process.** Two copies of the app on one working folder
  still see past each other for sessions. `.cos/0004_silent-concurrent-loss/spec.md` C2.

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

Exit codes: `0` pass, `1` the page is broken, `2` the environment is not ready.

`verify_0003` and `verify_0006` are the two that open a real browser. **In a checkout** the
bundle hardcodes its own address, so neither can move to a spare port: stop the app first,
and never run them at the same time. A wheel installed by `install.sh` behaves the other
way — `coscc/frontend.py` rewrites the address at startup, because a packaged install has
no Node to rebuild with. Both sentences are true; which one applies depends on which of the
two shapes you are looking at, and `coscc/run.py` is where they part.

A figure carries across a rewrite only if the mechanism did not change. `0006 spec.md` C2
says this about swapping the store; it holds the same way for swapping the interpreter.
