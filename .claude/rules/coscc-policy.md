---
paths:
  - "coscc/agent/policy.py"
  - "coscc/agent/policy_test.py"
---

# Things that break here

- No board step opens a `pr` or `ship` session. `coscc/github/prmachine.py` pushes, opens the
  pull request and merges with this machine's `gh` login, so it reaches every repository that
  login reaches (the page says so beside the button: `service.CONSEQUENCE`; do not remove it).
  What stands before a merge is `cos.mjs gate` and guard `ship-ready`, pinned to the head its
  own `gh pr view` found. There is no `pr` or `ship` grant.
- The mode grants nothing: the default button pushes and merges whatever mode is set.
- The read boundary is not a sandbox. `Read`, `Glob` and `Grep` are held to the unit's worktree
  and folder, but `impl`, `spike` and Gebo have `cat`; `check_command` reads only a redirect's
  target; a `Glob` is checked up to its first wildcard (`coscc/agent/policy_test.py`,
  `TheReadBoundaryIsNotASandbox`). A worktree's `.git` is a file outside both roots, so
  `review` gets the head in its prompt. Prompts name artifacts by path and rely on this
  boundary allowing the read; narrowing it turns `coscc/runner/prompt_test.py` red.
- `impl` can read a sibling repository and cannot be kept from writing it. `_git_into` refuses
  `-C`, `--git-dir`, `--work-tree`, `-c` or `GIT_*=` landing there; a subcommand's path and
  `python` are not read (`test_the_known_limit_of_git_into`).
- A redirect may write under `/tmp/<dir whose name carries the unit's NNNN_slug>/` (`impl` only;
  `spike` and `integrate` get `/dev/null` and descriptors). The target is resolved when `decide`
  runs, so a swap for a symlink later is missed, and anyone on the machine can create that
  directory first. `python -c` writes anywhere.
- Ceilings and who can change a stage's model: `.claude/docs/coscc-settings.md`; `spike`'s
  grant: `.claude/docs/coscc-spike.md`.
