# Scratch: where a step writes besides the worktree

Read this before changing `coscc/features/scratch.py`: the block and `PLUGIN` are all in it.
The directories, the variables naming them, the cap and the cleanup are the kernel's
(`coscc/units/scratch.py`, `policy.decide`, `sessions.child_env`).

## What the agent sees

- One prompt block, `scratch`, on every step: the worktree for what is committed,
  `$COS_SCRATCH_RAM` (capped at `RAM_CAP`) for small one-off files, `$COS_SCRATCH_DISK`
  (also `TMPDIR`) for large files and what a later stage reads back, and that the app removes
  both when the unit ends.
- No tool and no guard. What may be written is `policy.decide`'s: a redirect may name either
  directory by its variable, a write tool needs the path itself.

## Hazards

- The cap is checked before a write, not during it: `build > $COS_SCRATCH_RAM/log` can pass it
  while it runs. Only a tmpfs mount of its own, which needs root, would hold it hard.
- The ram root sits in the shared temp directory. Another user who makes
  `coscc-scratch-<uid>` first stops every step; the step says why.
