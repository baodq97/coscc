# cos-baodo

Local AI-native SDLC harness, and the template for it.
Reference: `docs/harness.md`. Each stage's rules live in its own skill.

## Commands

```
cos=.claude/scripts/cos.mjs

node --test '.claude/scripts/*.test.mjs'   # all green; never skip or delete a failing test
node $cos status                           # where every unit of work stands
node $cos gate <unit> <stage>              # exit 0 = stage may proceed
node $cos new-path <slug>                  # next work unit path
```

No application code yet. Add its build, test and lint commands here when it arrives.

## The loop

One unit of work per `.cos/NNNN_<slug>/` directory, holding `intent.md`, `spec.md` and
`plan.md` and nothing else.

`write-intent` → `write-spec` → `write-plan`, each gated on the one before.
`cos-status` reports where everything stands.

## Invariants

- Never set `Status: accepted`. Write `draft` and hand it back; that edit is the human's.
- Ask `cos.mjs gate` before a stage, and stop when it exits non-zero. Do not reason your
  way past it, and do not change a status to open your own gate.
- No code while `plan.md` is `draft`. An unaccepted plan authorizes nothing.
- Take work unit paths from `cos.mjs new-path`. Never guess a number.
- Cut a figure that has no source. Do not soften it.
- Cite only a file committed in this repository, by path and line range.
- Inside `.cos/`: English filenames and headings, Vietnamese prose. Everywhere else,
  English.
