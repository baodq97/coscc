---
paths:
  - "coscc/github/integrate.py"
---

# Things that break here

- Integrating force-pushes under the machine's `gh` login. A `behind` unit is rebased by GitHub;
  a conflict or a red check after integration opens a paid session holding one lease push to the
  unit's own branch.
- The critical push check reads words: any program the session starts can push past the lease. What
  stops a force on `main` is the GitHub ruleset, not the grant.
- Every press costs a fetch that moves `origin/main` for every worktree, and a `gh` call.
- The head is read once: a rebase finishing later races the session, the lease refuses the push,
  and the session is paid for.
- Every board read of a unit between `pr` and `ship` costs a `gh` call; offline it reads
  `unknown` after the wait.
