---
paths:
  - "coscc/worktrees.py"
  - "coscc/gitops.py"
  - "coscc/drift.py"
  - "coscc/fetches.py"
---

# Worktrees, fetches and what `main` changed

- **Each unit works in its own `git worktree`, and the app may move the workspace to
  `main`.** A unit's tree is `<COS_DATA_DIR>/worktrees/<slot>/<unit>`; a step runs there,
  and `gate`/`next` get `--repo <that tree>`.
  - When a unit's branch is checked out in the workspace itself (cut at a terminal), the app
    runs `git switch main` there, only if that tree is clean. A person standing on that
    branch finds themselves on `main`, and when coscc works on itself the harness it reads
    changes with it.
  - Every tree costs its own `.venv`, `node_modules` and `.web` (unmeasured), prepared by
    `uv sync --frozen`, `npm ci` and `uv run coscc-build`, running the repository's own
    install scripts under this process's user.
  - After `ship`, or on a board read of a `finished` unit, the app removes the tree and
    `branch -D`s the local branch, but only when `gh` says merged at the local head. A tree
    that cannot be removed that way (clean, but `gh` fails or the branch is off the merged
    head) costs a `gh pr view` (its wait unmeasured) on **every** board read until someone removes it
    by hand; nothing remembers the refusal.
  - The app moves a still-detached tree's HEAD to `origin/main` as a fetch just brought it,
    before every step that runs there. Each such step costs one more fetch, up to
    `FETCH_TIMEOUT` (`coscc/gitops.py:239`, chosen, not measured) when the remote does not
    answer — unless a fetch of the same clone is already running (the step waits for it) or
    one succeeded under `REUSE_SECONDS` (`coscc/fetches.py:36`) before (the step fetches
    nothing, and a commit pushed in that window is not in its base, cutting a branch
    included). The step still runs on whatever the tree already had, and says so in its
    prompt and in the run log.
  - A plain board read does the same: when a unit's branch exists but its tree is still
    detached, or never existed, `next_step`'s call into `worktrees.ensure` opens the tree
    onto that branch there and then, fetching first, on **every** such read. Offline, that
    fetch fails, `ensure` raises, and `next_step` swallows it into a plain `None`: the read
    still answers, but with no worktree and no reason shown for why the run button has
    nothing to offer.
- **An `impl` prompt carries file names taken from other people's commits on `main`.**
  `run_step` diffs the commit the unit's last `done` run of `plan` ran on (its `start`
  record's `head`) against the tree's `origin/main`, keeps the paths the plan's
  `## Files that change` names, and puts them in the prompt under *The files main changed
  since the plan* — so a name somebody merged reaches a paid session verbatim. Only names
  the plan already wrote can match, and landing one needs a merge to `main`.
  - The diff reads `origin/main` as the step's own preparation left it and does not fetch:
    an `impl` re-run on a tree already on its branch measures against the last fetch, and
    `plan_drift.main_sha` in the `start` record says which. That preparation reuses a
    recent fetch, so a merge landing just before `impl` starts is not in the diff either
    (`coscc/service_test.py`,
    `test_a_merge_under_thirty_seconds_after_the_plans_fetch_is_not_seen`).
  - Anything that fails — no `done` run of `plan`, no section, a commit the tree lacks — is
    `checked: false` with a reason, never an empty list, and never stops the step. A step
    started at a terminal gets none of this.
- **A `review` step can run the branch's own code before its session, with no session at
  all** (`0111`). When `cos.mjs screens` says a UI unit's head was rewritten after its
  screenshots, `run_step` runs the tree's `scripts/capture_screens.py` through `uv run`, with
  this process's environment less `__REFLEX_*`, for up to `retake.RETAKE_TIMEOUT` (300 s,
  chosen, not measured) under one lock for the whole app, so other units' reviews wait.
  - *Stop* does not reach it: the step is not running yet, and the unit's mark is held.
  - Port 18783 is shared with every `impl` session's capture, which the lock does not cover;
    one of those running at the same time refuses the review, and the autopilot stops at `e`.
  - A failed retake puts `.screens/` back as it was, so the next review retakes from the
    same manifest; nothing else is undone. A build that rewrote a tracked file leaves the
    tree dirty, and every later review of that unit is refused until a person cleans it.
