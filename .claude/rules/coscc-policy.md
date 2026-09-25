---
paths:
  - "coscc/policy.py"
  - "coscc/policy_test.py"
---

# The grant table: what no test here catches

- **`ship` merges; `pr` does not.** `pr` stops at an open pull request, and its grant refuses
  the merge by the command's words with flags removed — `gh -R o/r pr merge`, the merge
  endpoint through `gh api` and `gh alias set` included. An alias defined before the step,
  or `node -e` spawning `gh`, still walks past (`coscc/policy_test.py`,
  `test_the_known_limit_of_the_deny_list`). `ship`'s grant holds `git` and `gh` with this
  machine's login and lands the change on `main` after the `ship` gate opens.
- **The `pr` and `ship` grants reach further than this repository.** Their capability is
  this machine's `gh` login, so they reach every repository that login reaches. The page
  shows a warning string before the button is pressed; do not remove either.
- **The mode grants nothing, so the default button hands `pr` and `ship` their full grant.**
  `pr` pushes and `ship` merges with this machine's `gh` login whatever mode is set. What
  still stands in front is the gate `run_step` asks and the warning string above.
- **The read boundary is not a sandbox.** `Read`, `Glob` and `Grep` are held to the unit's
  worktree and its own folder in the store, for every grant. It binds only the stages
  without `Bash`: `impl`, `pr` and `ship` still have `cat` and `head`, and `check_command`
  reads only the target of a redirect that writes, never the paths `cat` or `head` are
  given. A `Glob` pattern is checked only up to its first wildcard. `ship` runs in the
  store's unit folder, so it cannot `Read` the worktree. `coscc/policy_test.py`,
  `TheReadBoundaryIsNotASandbox`, pins the gaps. A worktree's `.git` is a file pointing
  outside both roots, so no read-only stage can read a commit out of `.git/`: `review` is
  handed the head in its prompt instead (`build_prompt`, *The commit you are reviewing*).
  The prompts of `impl`, `pr`, `ship`, `review` and Gebo name the unit's artifacts by path
  and rely on this boundary letting them `Read` the unit's folder; narrowing it turns
  `coscc/runner_test.py` `EveryPathAPromptNamesCanBeRead` red, which is the point.
- **A redirect may write under `/tmp`, outside the write boundary.** `check_command` reads a
  line as bash does and lets a redirect write to `/dev/null`, to another descriptor, or below
  `/tmp/<a directory whose name carries the unit's NNNN_slug>/` — for `impl`, `pr` and
  `ship`, whose `decide` gets a `unit_dir`. `spike` and `integrate` get none, so they have
  `/dev/null` and descriptors only. The target is resolved, symlinks included, when `decide`
  runs, not when bash opens it, so a directory swapped for a symlink in between is not seen.
  Nothing creates or removes that directory, and `/tmp` is shared: anyone on the machine can
  make one carrying a unit's name first. The rest is still words, not capability —
  `python -c` writes anywhere.
- The ceilings a label buys, and who can change a stage's model, are in
  `.claude/docs/coscc-settings.md`; `spike`'s grant is in `.claude/docs/coscc-spike.md`.
