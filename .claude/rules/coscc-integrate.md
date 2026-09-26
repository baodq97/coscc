---
paths:
  - "coscc/integrate.py"
---

# Integrate, and the `gh` calls around the pull request

- **`POST /api/units/integrate` force-pushes under this machine's `gh` login.**
  - On a `behind` unit it runs `gh pr update-branch --rebase` and then moves the unit's
    local branch with `reset --keep`. On a `conflicting` or `red-after-integration` unit it
    opens Gebo, a paid session (ceilings at `coscc/policy.py:350-354`, chosen, not measured)
    whose grant allows exactly one push: `--force-with-lease=<branch>:<head at start>` to
    the unit's own branch.
  - The roads to the branch the grant's own commands hold are refused by their words:
    `gh api` (it reaches `git/refs` with `force=true`), `gh repo sync`, `gh extension`,
    `git send-pack`, `git http-push`, and an alias, include or `GIT_CONFIG_*` made during
    the step. The grant still reads tokens, so any program it may start can push past the
    lease itself — `node -e`, `python -c`, or a script the step wrote and then runs through
    `npm test` — and so can an alias already in a git config before the step
    (`coscc/policy_test.py`, `test_the_known_limit_c6`). What stops a force on `main` is
    the GitHub ruleset, not this grant.
  - Gebo may read its own unit's folder and the intent, spec and plan of the units the app
    lists as related (`read_paths`) — a widening of the read boundary, and not a sandbox
    while it has `cat`. Its prompt names its own unit's artifacts by path and carries none
    of them.
  - A `behind` or `current` unit can open a paid session too: a non-zero exit of
    `update-branch` after which `gh pr view` still reads the pull request's head unmoved
    opens Gebo with gh's code and words in its prompt — a missing permission, or a network
    that failed only the first call, included, and that session will likely fail the same
    way (`.cos/0052_*/plan.md` Risk 1). A lapsed login fails that read too and opens none. A
    timeout, and a head GitHub has not moved yet, stay `failed` with no session. A non-zero
    exit after which the head has moved is taken as GitHub's rebase (`pushed`, the tree
    follows it); one after which it cannot be read is `failed`.
  - A press whose local head holds commits the pull request lacks (`ahead`, `diverged`,
    `0114`) opens Gebo in every state, `current` too, and the panel does not say so first.
    A rebase left in progress is aborted only when the run log shows a cut integration.
  - The head is read once, not polled, so a rebase GitHub finishes after that read still
    races the session: the lease refuses Gebo's push, the row is `failed` rather than Gebo's
    `pushed` whenever Gebo's tree does not end on the moved head, and the tree is moved to
    it — the session is paid for either way.
  - Every press costs one fetch through the fetch coordinator (`coscc/fetches.py`) and one
    `gh pr view` for `mergeStateStatus` (up to `GH_TIMEOUT`, `coscc/integrate.py:43`), both
    before the lock and the answer. The fetch moves `refs/remotes/origin/main` for every
    worktree of the workspace. `merge_state` is written to the row and decides nothing.
    Behind the password like every route; every attempt, refused ones included, is one
    `integration` row in the run log.
- **Every board read with a unit between `pr` and `ship` costs one `gh pr list`,** up to
  `GH_TIMEOUT`, plus a `gh pr checks` for a unit whose head is the one its last integration
  pushed. Offline, every such unit reads `unknown` and the board waits out the timeout.
  Unmeasured. The counts use the `origin/main` of the last fetch; the read does not fetch —
  but an autopilot pass does, through the coordinator, when a listed unit is at `ship`
  (`0112`), and that moves the ref for every worktree. A
  `current` unit has the *Integrate* button too, since only a press fetches; pressed on a
  unit that is really current, it leaves a `refused` row. After the read, such a unit may
  start one background `gh pr checks` for its state badge (`0100`): one per unit at a time,
  no oftener than `CI_REFRESH` per head, never awaited by the board.
- **Every `pr` step costs one `gh pr list` before the session starts,** under this machine's
  `gh` login, up to `GH_TIMEOUT`. Offline or logged out, the step still runs and its prompt
  says the lookup failed. The `pr` grant refuses `git rebase`, `git merge`, `git pull`,
  `gh pr update-branch` and a forced push (`--force`, `-f`, `--force-with-lease`,
  `--force-if-includes`, a `+` refspec) by their words. It also refuses a git alias, include
  or `GIT_CONFIG_*` made during the step, and `gh api` naming the update-branch endpoint
  (`pulls/<n>/update-branch`, `updatePullRequestBranch`). It does not refuse `gh api` as a
  whole, as the `integrate` grant does. `node -e`, or an alias defined before the step,
  still walks past (`coscc/policy_test.py`, `IntegrationIsNotPrs`).
