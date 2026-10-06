---
paths:
  - "coscc/units/board.py"
---

# Things that break here

- The `review` and `ship` gates call `gh` and `git` in the workspace through a child
  environment that carries only what it names: a machine logged in through a token variable
  alone sees `review` closed, and offline it cannot start.
- The run button offers what `next` names, even for a stage that has an artifact; it offers
  nothing on `waiting`.
- An `impl` that commits and does not push reads as not done: the button stays on `impl`.
- An answer is a row, never a file's section: a prose-stage reply is written as it comes, and
  every later prompt renders the rows.
- Nothing re-asks on a timer: a pending check shows no button until someone asks.
