---
paths:
  - "coscc/units/board.py"
---

# Things that break here

- The `review` and `ship` gates call `gh` and `git` in the workspace: `board.gate` passes
  `--repo` and waits `GATE_TIMEOUT` (`coscc/units/board.py:283`). `child_env` carries only
  `PATH`, `HOME` and `COS_REVIEW_ROUNDS`, so a machine logged in through `GH_TOKEN` alone sees
  `review` closed. Offline, `review` cannot start.
- The run button offers the stage `cos.mjs next` names, even one that already has an artifact.
  After a review asks for changes it offers `impl`, then `review` once the fix is pushed and CI
  is green.
- Re-running `impl` overwrites `impl.md`. `review.md`'s earlier rounds and every `## Answers`
  section are written back by the runner, which refuses a reply that rewrites one.
- Opening a unit, finishing a step and *Ask again* each ask `gh`; nothing re-asks on a timer,
  so a pending CI shows no button until someone asks.
- An `impl` that commits and does not push leaves the button on `impl`.
- The button offers nothing when `next` returns `waiting`. An `impl.md ## Needs a person` that
  claims every open finding sends the button to `review`, which alone checks the claim.
