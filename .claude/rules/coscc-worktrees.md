---
paths:
  - "coscc/units/worktrees.py"
  - "coscc/git/gitops.py"
  - "coscc/git/drift.py"
  - "coscc/git/fetches.py"
---

# Things that break here

- Each unit works in `<COS_DATA_DIR>/worktrees/<slot>/<unit>`; a step runs there and `gate`/`next`
  get `--repo <that tree>`.
  - When the unit's branch is checked out in the workspace itself, the app runs `git switch main`
    there if the tree is clean: a person on that branch finds themselves on `main`, and when
    coscc works on itself the harness it reads changes too.
  - Every tree costs its own `.venv`, `node_modules` and `.web` (`uv sync --frozen`, `npm ci`,
    `uv run coscc-build`), running the repository's install scripts as this user.
  - After `ship`, or a board read of a `finished` unit, the app removes the tree and `branch -D`s
    the branch only when `gh` says merged at the local head. Otherwise every board read costs a
    `gh pr view` until someone removes it by hand; nothing remembers the refusal.
  - A still-detached tree's HEAD moves to `origin/main` after a fetch before every step, costing
    a fetch up to `FETCH_TIMEOUT` (`coscc/git/gitops.py:185`) unless one is already running or
    succeeded under `REUSE_SECONDS` (`coscc/git/fetches.py:29`); a commit pushed in that window
    is not in the base. The step still runs and says so in its prompt and the run log.
  - A board read with a branch whose tree is detached or missing calls `worktrees.ensure`,
    fetching each time. Offline it raises, `next_step` swallows it into `None`, and the run
    button has nothing to offer with no reason shown.
- An `impl` prompt carries file names from other people's commits on `main`: `run_step` diffs the
  last `plan` run's `head` against `origin/main`, keeps the paths in the plan's
  `## Files that change`, and puts them under *The files main changed since the plan*.
  - The diff does not fetch; `plan_drift.main_sha` in the `start` record says which `main` it
    used, and a merge landing just before `impl` is not seen
    (`coscc/service/steps_test.py`, `test_a_merge_under_thirty_seconds_after_the_plans_fetch_is_not_seen`).
  - Any failure is `checked: false` with a reason and never stops the step. A terminal step gets
    none of this.
- A `review` step can run the branch's code with no session: when `cos.mjs screens` says a UI
  unit's head was rewritten after its screenshots, `run_step` runs `scripts/capture_screens.py`
  through `uv run` (environment less `__REFLEX_*`) for up to `retake.RETAKE_TIMEOUT` under one
  app-wide lock, so other reviews wait.
  - *Stop* does not reach it; *Apply* waits for it (`_update_waited`).
  - Port 18783 is shared with every `impl` capture, which the lock does not cover: a concurrent
    one refuses the review and the autopilot stops at `e`.
  - A failed retake restores `.screens/` only. A build that rewrote a tracked file leaves the
    tree dirty and every later review of that unit is refused until a person cleans it.
