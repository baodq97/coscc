# cos-baodo

A local AI-native SDLC harness: a unit of work moves from `intent.md` to `spec.md` to
`plan.md`, each artifact accepted and committed before the next begins. The agent writes
those artifacts and accepts its own, so `Status: accepted` records readiness rather than
approval; `.claude/harness.md` explains what was traded away for that and what is left.

The harness is entirely inside `.claude/`:

| Path | What it is |
|---|---|
| `.claude/CLAUDE.md` | The rules that hold in every session. Claude Code loads it automatically. |
| `.claude/harness.md` | How the loop works and why. Read this first. |
| `.claude/skills/` | One skill per stage, plus `cos-status`. |
| `.claude/scripts/` | The mechanical checks — numbering, gates, status — and their tests. |

Work units live in `.cos/NNNN_<slug>/`. `docs/` holds the playbook this is built from.

```
node .claude/scripts/cos.mjs status   # where everything stands
npm test                              # the harness scripts and the app
```

Copying it into another repository means copying `.claude/`. Nothing else is needed, and
nothing lands in that repository's own tree.

## The page

**COS Studio** is the app's one page, at `/`, built from Reflex Python components. Six
screens — Overview, Workspaces, Board with a work-unit drawer, Sessions, Activity & usage,
Settings — all reading the running service.

```sh
uv run cos-build                                   # compile the page
COS_WORKING_DIR=~/projects uv run cos-baodo        # then http://127.0.0.1:8790
```

It binds loopback only, and its chat sessions have **no tools** by default. The app keeps
its own state — the workspace list, the run log, interface preferences — in one SQLite
database under `COS_DATA_DIR`, which defaults to `~/.cos`. Workspaces themselves stay
under `COS_WORKING_DIR`; backing up one does not back up the other. Neither is settable
over HTTP.

Two controls spend real account quota and both say so before they are used: sending a chat
message, and running a step of the loop. See [the page guide](docs/studio.md).

Build and serve with the same `COS_HOST`/`COS_PORT` if changing the default address —
`cos-baodo` refuses to start if the build it finds was made for a different one.

```sh
uv run python scripts/verify_0004.py   # the page renders, and the check can fail
uv run python scripts/verify_0011.py   # five flows on real data, and a restart
```

Both need a browser and a free `COS_PORT`; stop the app first.
