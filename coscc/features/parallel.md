# Parallel: a plan's parallel steps, handed to impl

Read this before changing `coscc/features/parallel.py`: the reader, the block and `PLUGIN` are
all in it.

## The section it reads

`plan.md ## Parallelization`, in the shape the plan skill's template gives:

```markdown
## Parallelization
(a) <title>
- <path or glob>
Report: <what the step reports when done>

(b) <title>
- <path or glob>
Report: <…>
```

A bullet under a step's line is a path until the first other line, which starts the report; the
report runs to the next step. `none: one session` means no step.

## What the agent sees

- One prompt block, `parallel`, on `impl` only, when the plan names two or more steps: each
  step's line, its paths and its report, and the instruction to start one `worker` per step.
- No tool and no guard. The `worker` helper, the hooks holding `Agent` and `SendMessage`, `peers`
  and the block on how the agents talk are the kernel's.

## Hazards

- Nothing checks a plan: two steps naming the same path, or a step past ten paths, reach impl as
  written. The run log's `worker_write` events show what each worker wrote.
- The plan is read once, when the step starts: a step taken up again after an update keeps the
  block its first prompt had, even if `plan.md` changed since.
