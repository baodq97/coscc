# The spike step

Read this before changing the `spike` grant, `service.run_step`'s scratch directory, or `runner`'s progress-file write. Moved here whole from `.claude/rules/coscc-app.md` (`0094`); the history ("Since `00xx`") is kept at this tier.

- **`spike` runs arbitrary code, and nothing is a sandbox.** Since `0039` a spec that marks
  a concern `[unmeasured] U<n>` sends its unit to a `spike` step holding `Bash` with
  `python`, `node`, `npm` and `uv` (`impl`'s commands without `git`,
  `policy.SPIKE_COMMANDS`), run under this process's user. Its `cwd` is
  `<COS_DATA_DIR>/spikes/<slot>/<unit>`, emptied before the step and removed after it
  (`service.run_step`); the write tools are held to that directory, and the worktree and
  the unit are read only. `check_command` is not a sandbox: `python -c` writes anywhere the
  user can, `~/.ssh` and other units' stores included, and none of that is seen. What is
  seen is the worktree: its `HEAD` and `git status --porcelain` are read before and after
  (`gitops.tree_state`), and a difference fails the step with no `spike.md` and the files
  named in the run log — detected, not undone, and a write to a path `.gitignore` covers is
  not in `status`. A client that drops the stream leaves the scratch until the generator
  is collected or the next spike clears it. The `spec ↔ spike` loop stops for a person at
  `Round: 2` (`cos.mjs` `SPIKE_ROUNDS`), and `Round:` is the agent's own word: a spike
  that writes `Round: 1` every time loops until the money runs out. Since `0080` the step
  keeps a progress file, `spike.md` in its `cwd` (`write-spike`, *The progress file*), and
  when its reply is not an artifact — a turn or budget ceiling, no `Status:` line, a session
  that broke — `Runner.run` writes the unit's `spike.md` from that file through the same
  checks and the same `## Answers`-keeping write, before the scratch is removed. It does
  not on a Stop, on a changed worktree, or when the app goes down. The step still ends
  `exhausted` or `failed`, never `done`, and its `detail` says the file was written. The
  file carries `Status: accepted` from its first write while `U<n>` may still be missing;
  `cos.mjs` closes `plan` on each missing one, but a `Verdict: holds.` the agent wrote for a
  half-measured question now reaches the unit where it used to be lost with the reply.
  Every spike `end` row carries `spike_md`: `reply`, `progress`, `none`, `unusable`,
  `withheld` (a Stop, a changed worktree) or `unchecked` (git could not read the worktree,
  so nothing was written — a failure to `verify_0080 --measure`, not a Stop). The ceilings are 80 turns / $8.0 (`policy.py`, chosen, not measured), so one
  press can spend twice what it did before, and `SPIKE_ROUNDS` does not count presses that
  hit the ceiling.
