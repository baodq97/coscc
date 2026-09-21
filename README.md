# cos-baodo

A local AI-native SDLC harness: a unit of work moves from `intent.md` to `spec.md` to
`plan.md`, each artifact accepted by a human and committed before the next begins.

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
node --test '.claude/scripts/*.test.mjs'
```

Copying it into another repository means copying `.claude/`. Nothing else is needed, and
nothing lands in that repository's own tree.
