---
paths:
  - "coscc/units/worktrees.py"
  - "coscc/git/gitops.py"
  - "coscc/git/drift.py"
  - "coscc/git/fetches.py"
---

# Things that break here

- Each unit works in its own tree; a step and its gate ask there. When the branch is checked out
  in the workspace itself, the app switches that tree to `main` if clean, so a person on it
  finds themselves on `main`.
- Every tree costs its own dependencies and runs the repository's install scripts as this user.
- A tree is removed after ship only if `gh` says merged at the local head; otherwise every board
  read costs a `gh` call until someone removes it.
- A fetch before each step moves `origin/main` for every worktree and is skipped while another
  is running or recent; a commit pushed in that window is not in the base.
- A board read of a unit whose tree is detached or missing fetches; offline it raises, and the
  run button shows nothing and no reason.
- A step that runs the branch's code outside a session (screenshot retake) shares a port and a
  lock with `impl` captures: a concurrent one refuses the review. A failed retake restores only
  `.screens/`; a build that rewrote a tracked file leaves the tree dirty and later reviews are
  refused until a person cleans it.
