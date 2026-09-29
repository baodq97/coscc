---
paths:
  - "coscc/agent/policy.py"
  - "coscc/agent/policy_test.py"
---

# The grant table: what no test here catches

- **No board step opens a `pr` or `ship` session** (`0136`). `run_step` hands both to
  `coscc/github/prmachine.py`, which pushes the unit's branch, opens its pull request and
  merges it with this machine's `gh` login, with no model between. What stands in front of a
  merge is `cos.mjs gate` and then guard `ship-ready`, which reads CI and the last round in
  `cos.db` at the head its own `gh pr view` found and pins `--match-head-commit` to it. Since
  `0139` R12 there is no `pr` or `ship` grant, skill or agent: a step of either starts from
  the locked `Grant()`. The merge refusal by the command's words lives on in `integrate`'s
  deny list, and an alias defined before the step, or `node -e` spawning `gh`, still walks
  past it (`coscc/agent/policy_test.py`, `test_the_known_limit_of_the_deny_list`).
- **`pr` and `ship` reach further than this repository.** Their capability is this machine's
  `gh` login, so they reach every repository that login reaches. The page keeps one sentence
  beside the button saying so (`service.CONSEQUENCE`; 0082 spec ## Answers, câu 5), and this
  bullet is its place in the documentation. Do not remove the sentence. `/api/board` carries no `warning`
  for either since `0139` R12 took their grants. The mode grants nothing: the default button
  pushes and merges whatever mode is set.
- **The read boundary is not a sandbox.** `Read`, `Glob` and `Grep` are held to the unit's
  worktree and its own folder in the store, for every grant. It binds only the stages
  without `Bash`: `impl`, `spike` and Gebo still have `cat` and `head`, and `check_command`
  reads only the target of a redirect that writes, never the paths `cat` or `head` are
  given. A `Glob` pattern is checked only up to its first wildcard. `coscc/agent/policy_test.py`,
  `TheReadBoundaryIsNotASandbox`, pins the gaps. A worktree's `.git` is a file pointing
  outside both roots, so no read-only stage can read a commit out of `.git/`: `review` is
  handed the head in its prompt instead (`build_prompt`, *The commit you are reviewing*).
  The prompts of `impl`, `review` and Gebo name the unit's artifacts by path
  and rely on this boundary letting them `Read` the unit's folder; narrowing it turns
  `coscc/runner/prompt_test.py` `EveryPathAPromptNamesCanBeRead` red, which is the point.
- **`impl` reads a sibling repository and cannot be kept from writing it** (`0040` R13).
  `read_also` widens reading to the other checkouts of the unit's idea. `_git_into` refuses a
  `git` whose `-C` chain, `--git-dir`, `--work-tree`, `-c` value or `GIT_*=` assignment lands
  there, or is a variable. A path a subcommand takes (`git worktree add <sibling>/x`) and
  `python` are not read (`test_the_known_limit_of_git_into`).
- **A redirect may write under `/tmp`, outside the write boundary.** `check_command` reads a
  line as bash does and lets a redirect write to `/dev/null`, to another descriptor, or below
  `/tmp/<a directory whose name carries the unit's NNNN_slug>/` — for `impl`, whose
  `decide` gets a `unit_dir`. `spike` and `integrate` get none, so they have
  `/dev/null` and descriptors only. The target is resolved, symlinks included, when `decide`
  runs, not when bash opens it, so a directory swapped for a symlink in between is not seen.
  Nothing creates or removes that directory, and `/tmp` is shared: anyone on the machine can
  make one carrying a unit's name first. The rest is still words, not capability —
  `python -c` writes anywhere.
- The ceilings a label buys, and who can change a stage's model, are in
  `.claude/docs/coscc-settings.md`; `spike`'s grant is in `.claude/docs/coscc-spike.md`.
