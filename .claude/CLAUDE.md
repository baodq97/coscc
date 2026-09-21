# cos-baodo

Local AI-native SDLC harness, and the template for it.
Reference: `.claude/harness.md`. Each stage's rules live in its own skill.

## Commands

```
npm test                                          # node --test over channel/ and .claude/scripts/
node .claude/scripts/cos.mjs status               # where every unit stands
node .claude/scripts/cos.mjs gate <unit> <stage>  # exit 0 = stage may proceed
node .claude/scripts/cos.mjs new-path <slug>      # next work unit path
```

Tests must be green before any task is reported complete; never skip or delete a failing
one. There is no build step — the channel runs from source — and no linter. Do not invent a
command for either; add one here if one ever exists.

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
