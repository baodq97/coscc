---
paths:
  - "coscc/agent/policy.py"
  - "coscc/agent/policy_test.py"
---

# The grant table: what no test here catches

- **Since `0136` no board step opens a `pr` or `ship` session.** `run_step` hands both to
  `coscc/github/prmachine.py`, which pushes the unit's branch, opens its pull request and
  merges it with this machine's `gh` login, with no model between. What stands in front of a
  merge is `cos.mjs gate` and then guard `ship-ready`, which reads CI and the last round in
  `cos.db` at the head its own `gh pr view` found and pins `--match-head-commit` to it. The
  `pr` and `ship` grants are still in the table, for a terminal session; the `pr` grant
  refuses the merge by the command's words with flags removed, and an alias defined before
  the step, or `node -e` spawning `gh`, still walks past (`coscc/agent/policy_test.py`,
  `test_the_known_limit_of_the_deny_list`).
- **`pr` and `ship` reach further than this repository.** Their capability is this machine's
  `gh` login, so they reach every repository that login reaches. The page keeps one sentence
  beside the button saying so (`service.CONSEQUENCE`; 0082 spec ## Answers, câu 5); the full
  warning string is `policy.py`'s, still in `/api/board` as `warning`, and this bullet is its
  place in the documentation. Do not remove the sentence. The mode grants nothing: the
  default button pushes and merges whatever mode is set.
- **The read boundary is not a sandbox.** `Read`, `Glob` and `Grep` are held to the unit's
  worktree and its own folder in the store, for every grant. It binds only the stages
  without `Bash`: `impl`, `pr` and `ship` still have `cat` and `head`, and `check_command`
  reads only the target of a redirect that writes, never the paths `cat` or `head` are
  given. A `Glob` pattern is checked only up to its first wildcard. `ship` runs in the
  store's unit folder, so it cannot `Read` the worktree. `coscc/agent/policy_test.py`,
  `TheReadBoundaryIsNotASandbox`, pins the gaps. A worktree's `.git` is a file pointing
  outside both roots, so no read-only stage can read a commit out of `.git/`: `review` is
  handed the head in its prompt instead (`build_prompt`, *The commit you are reviewing*).
  The prompts of `impl`, `pr`, `ship`, `review` and Gebo name the unit's artifacts by path
  and rely on this boundary letting them `Read` the unit's folder; narrowing it turns
  `coscc/runner/prompt_test.py` `EveryPathAPromptNamesCanBeRead` red, which is the point.
- **`impl` reads a sibling repository and cannot be kept from writing it** (`0040` R13).
  `read_also` widens reading to the other checkouts of the unit's idea. `_git_into` refuses a
  `git` whose `-C` chain, `--git-dir`, `--work-tree`, `-c` value or `GIT_*=` assignment lands
  there, or is a variable. A path a subcommand takes (`git worktree add <sibling>/x`) and
  `python` are not read (`test_the_known_limit_of_git_into`).
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
