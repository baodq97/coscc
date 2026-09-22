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
`channel/` is the first thing the harness built: a web page on localhost that talks to a
running Claude Code session, with `evidence/` holding the transcript it was measured by.

```
node .claude/scripts/cos.mjs status   # where everything stands
npm test                              # the harness scripts and the channel
```

Copying it into another repository means copying `.claude/`. Nothing else is needed, and
nothing lands in that repository's own tree.

## Interactive UI prototype

**COS Studio** is a local design preview built with actual Reflex Python components:

**Current status: unverified implementation draft.** Build/test execution was blocked by
the session's command permissions. A running preview has not yet been verified.

```sh
uv run cos-build
uv run cos-baodo
# Open http://127.0.0.1:8790/prototype
```

The existing application remains at `/`. The prototype has Overview, Workspaces, Board
with a detail panel, Sessions, Activity & Usage, and Settings. Try adding a demo workspace,
opening a work item, running a simulation, switching conversations, and light/dark mode.
The preview-state selector also exposes empty, loading and error states.

**Everything at `/prototype` is explicitly demo data.** It does not call an AI model,
create or clone folders, change real workspaces, grant tools, open PRs, or ship anything.
Demo changes live in UI state and are not durable. Design approval and connecting the
backend are separate, pending work. See [the prototype guide](docs/prototype.md).

Build and serve with the same `COS_HOST`/`COS_PORT` if changing the default address.
`uv run python scripts/verify_0009.py` exercises the five demo flows in Chromium; the app
port must be free because the proof starts and stops its own isolated app.
