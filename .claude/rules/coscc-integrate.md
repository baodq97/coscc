---
paths:
  - "coscc/github/integrate.py"
---

# Things that break here

- `POST /api/units/integrate` force-pushes under this machine's `gh` login. On `behind` it runs
  `gh pr update-branch --rebase` and moves the local branch with `reset --keep`. On
  `conflicting` or `red-after-integration` it opens Gebo, a paid session under the
  `integrate` grant (`coscc/agent/policy.py:248-252`) that allows one push: `--force-with-lease=<branch>:<head at start>`
  to the unit's own branch.
  - Denied by words: `gh api`, `gh repo sync`, `gh extension`, `git send-pack`, `git http-push`,
    and an alias, include or `GIT_CONFIG_*` made during the step. Any program it may start
    (`node -e`, `python -c`, a script it wrote) can push past the lease, as can an alias already
    in a git config (`coscc/agent/policy_test.py`, `test_the_known_limit_c6`). What stops a force
    on `main` is the GitHub ruleset, not this grant.
  - Gebo may read its unit's folder and the intent, spec and plan of related units
    (`read_paths`); not a sandbox while it has `cat`.
  - A `behind` or `current` unit can open Gebo too: a non-zero `update-branch` exit after which
    `gh pr view` still reads the head unmoved opens it with gh's words in the prompt, and it
    will likely fail the same way. A timeout or an unmoved head stays `failed` with no session;
    a moved head is taken as GitHub's rebase.
  - Local commits the pull request lacks (`ahead`, `diverged`) open Gebo in every state.
  - The head is read once, so a rebase finishing later races the session: the lease refuses the
    push, the row is `failed`, the tree is moved to the new head, and the session is paid for.
  - Every press costs one fetch (`coscc/git/fetches.py`), which moves `refs/remotes/origin/main`
    for every worktree, and one `gh pr view` up to `GH_TIMEOUT` (`coscc/github/integrate.py:43`).
    Every attempt is one `integration` row.
- Every board read of a unit between `pr` and `ship` costs one `gh pr list` (up to
  `GH_TIMEOUT`), plus `gh pr checks` for a unit at its last pushed head; offline each reads
  `unknown` after the wait. The read does not fetch, but an autopilot pass does when a unit is
  at `ship`. A background `gh pr checks` runs no oftener than `CI_REFRESH` per head.
- Every `pr` step costs one `gh pr list` (`coscc/github/prmachine.py`), up to `GH_TIMEOUT`, and
  opens no session.
