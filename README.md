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
