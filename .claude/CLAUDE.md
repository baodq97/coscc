# cos-baodo

Local AI-native SDLC harness, and the template for it.
Reference: `.claude/harness.md`. Each stage's rules live in its own skill.

## Commands

```
node --test '.claude/scripts/*.test.mjs'
node .claude/scripts/cos.mjs status               # where every unit stands
node .claude/scripts/cos.mjs gate <unit> <stage>  # exit 0 = stage may proceed
node .claude/scripts/cos.mjs new-path <slug>      # next work unit path
```

Tests must be green before any task is reported complete; never skip or delete a failing
one. No application code yet — add its build, test and lint commands here when it arrives.

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
