---
name: cos-status
description: Report where every unit of work in .cos/ stands and what is blocking each one. Use when someone asks what is in flight, what needs accepting, what to pick up next, or for the state of a particular work unit.
---

# Report work unit status

This skill reads and reports. It does not write, does not accept anything, and does not
start the next stage. If the report makes the next step obvious, say what it is and stop.

## How to read the tree

```
ls .cos/ 2>/dev/null && grep -rn '^Status:' .cos/ 2>/dev/null
```

If `.cos/` does not exist or is empty, say there are no work units yet and name
`write-intent` as the way to open one. Do not create the directory.

For each `.cos/NNNN_<slug>/`, read the `Status:` line of each artifact present. A work unit
with no status line in a file it has is a finding — report it rather than inferring one.

## Output

One table, most recently numbered last:

| Unit | Intent | Spec | Plan | Blocked on |
|---|---|---|---|---|
| 0001_slow-portal-login | accepted | draft | — | spec waiting to be accepted |

Use `—` for an artifact that does not exist. Distinguish the two cases that look alike: a
missing spec that nobody has written, and a spec deliberately skipped — the latter is
recorded in `plan.md` as `Spec: skipped (<reason>)`, so read the plan before calling a spec
missing.

## What counts as blocked

- An artifact at `draft` blocks the stage after it, and the human accepting it unblocks it.
  The action is theirs, not yours: name it, do not perform it.
- An artifact at `rejected` closes the unit. Say so; do not propose continuing it.
- A `plan.md` at `accepted` is not blocked — it is work ready to start.
- A `plan.md` at `done` is finished. Report it, do not propose anything for it.

## Naming the next action

| State | Next action |
|---|---|
| no work units | `write-intent` opens one |
| `intent.md` draft | human accepts it |
| `intent.md` accepted, no spec | `write-spec`, which first assesses whether to skip |
| `spec.md` draft | human accepts it |
| `spec.md` accepted or skipped, no plan | `write-plan` |
| `plan.md` draft | human accepts it |
| `plan.md` accepted | implementation starts |

Name one action per unit. Do not invoke the skill yourself — reporting that a stage is
ready is not the same as the human deciding to run it.

## Done when

The table matches what is on disk, and for every unit that is waiting, the report names the
one action that would move it.
