# Parallel: a plan's parallel steps, handed to impl

Read this before changing `coscc/features/parallel/__init__.py`: the block and `FEATURE` are
all in it.

## What it reads

`Facts.plan["steps"]`, the plan record's parallel steps (`{title, paths, report}`), which a
board step's features are handed. `submit` refuses a plan whose step names a path its `files`
does not, or a path two steps share.

## What the agent sees

- One prompt block, `parallel`, on `impl` only, when the record names two or more steps: each
  step's `(a) <title>`, its paths and its report, and the instruction to start one `worker` per
  step with that step's slice of the envelope (its part of `plan.md`, the answers and findings
  on its paths) in the worker's prompt.
- No tool and no guard. The `worker` helper, the hooks holding `Agent` and `SendMessage`, `peers`
  and the block on how the agents talk are the kernel's.

## Hazards

- A step past ten paths reaches impl as written. The run log's `worker_write` events show what
  each worker wrote.
- The record is read once, when the step starts: a step taken up again keeps the block its
  first prompt had.
