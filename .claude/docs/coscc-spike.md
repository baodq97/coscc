# The spike step

Read this before changing the `spike` grant or its scratch directory.

- `spike` runs arbitrary code as this user and nothing is a sandbox: `python -c` writes anywhere
  the user can. Its working directory is a scratch that is emptied before and removed after;
  the worktree and the unit are read only.
- The worktree's `HEAD` and status are compared before and after: a difference fails the step
  with no `spike.md`. This detects, and does not undo; an ignored path is not seen.
- `Round:` is the agent's own word. The loop stops for a person at the second round, and presses
  that hit a ceiling do not count, so a spike that always writes `Round: 1` loops until the
  money runs out.
- When the reply is not an artifact (a ceiling, a broken session), the app writes `spike.md`
  from the progress file through the same checks. The step still ends `exhausted` or `failed`.
