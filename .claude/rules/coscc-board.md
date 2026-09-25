---
paths:
  - "coscc/board.py"
---

# Reading the board: the gates and the run button

- **The `review` and `ship` gates call `gh` and `git` in the workspace.** `board.gate`
  passes `--repo` and waits `GATE_TIMEOUT` (`coscc/board.py:233`, chosen). `child_env`
  carries `PATH`, `HOME` and `COS_REVIEW_ROUNDS` only, so a machine logged in through
  `GH_TOKEN` alone sees the `review` gate closed with gh's own error. Offline, `review`
  cannot start.
- **The run button offers the stage `cos.mjs next` names, even one that already has an
  artifact.** The fix → review-again loop is driven from the board: after a review asks
  for changes it offers `impl`, then `review` once a fix is on the pull request and CI is
  green.
  - Re-running `impl` overwrites `impl.md`. `review.md`'s rounds are guarded on top of that:
    the reply carries only its new round, the runner writes the earlier ones back from the
    file, and it refuses a reply that rewrites one. Every prose stage's `## Answers` section
    is guarded the same way, `review.md` included (`.claude/docs/coscc-answers.md`).
  - Opening a unit, finishing a step and pressing *Ask again* each ask `gh` in the
    workspace under this machine's login (how long, unmeasured); nothing re-asks
    on a timer, so a pending CI shows no button until someone asks.
  - An `impl` that commits and does not push leaves the button on `impl`.
  - The button offers nothing when `next` returns `waiting`: the last review round
    confirmed those findings need a person, and the frame names them and links to
    *Questions* instead. An `impl.md ## Needs a person` that claims every open finding sends
    the button to `review`, not `impl`, even with no new commit — nothing but the review
    checks the claim.
