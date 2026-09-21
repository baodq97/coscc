---
name: cos-status
description: Report where every unit of work in .cos/ stands and what is blocking each one. Use when someone asks what is in flight, what needs accepting, what to pick up next, or for the state of a particular work unit.
---

# Report work unit status

```
node .claude/scripts/cos.mjs status
node .claude/scripts/cos.mjs status --json   # same facts, for a caller that needs to branch
```

The script owns the mechanics: which artifacts exist, what status each carries, which
action comes next. This skill reads and reports. It writes nothing, accepts nothing, and
starts no stage.

## Reporting it

Reproduce the table as printed. Do not recompute a cell by reading the files yourself — if
you would report something other than what the script printed, that disagreement is itself
the finding and belongs in the report.

Everything under `Problems` is a finding: an artifact with no `Status` line, a directory
that does not match `NNNN_<slug>`, a stray file in a work unit. Report each one. Never
infer past it into a status the script declined to assign.

`—` means the artifact does not exist. `skipped` in the Spec column means the human
decided to skip it and `spec.md` records why; that is a settled state, not a gap.

## The one action

The table gives one next action per unit. Name it and stop there.

Reporting that a stage is ready is not the same as the human deciding to run it, so do not
invoke the next skill and do not accept anything on their behalf. When the action is
`human accepts <file>`, that is theirs alone.

When there are no work units, say so and name `write-intent` as the way to open one.

## Done when

The report matches what the script printed, every problem it raised is visible in the
report, and each waiting unit has exactly one named action against it.
