---
name: integrate
description: Rebase a unit's pull request onto main when it conflicts, its CI went red after an integration, or GitHub refused the app's own rebase, resolving each conflict by the intent of both sides. Run by the app as Gebo, on a person's request, on a unit between pr and ship. Not a stage.
---

# Integrate a unit that fell behind

Put this unit's branch on top of `origin/main` so both sides keep what they
meant: this unit's and every unit merged since the branch was cut. Not a stage, no artifact;
the app judges you by the pull request's head before and after.

## Rules

- Work only in the current directory, on the unit's branch. Rebase; never `git merge`,
  `git pull` or `gh pr update-branch`.
- Push only as the prompt spells out: `git push --force-with-lease=<branch>:<head at start> origin <branch>`.
  Any other road to the branch is refused; when the push is refused, stop and report it.
- Read `gh pr view` and `gh pr checks`, this unit's artifacts, and `intent.md`, `spec.md`,
  `plan.md` of the units the prompt lists. Touch nothing of another unit.

## Steps

1. `git fetch origin main`, `git rebase origin/main`.
2. Each conflict: use the intent of the unit that brought each commit (the prompt names it;
   "no unit found" means only the diff, say so). Keep both behaviours; removing one side to
   pass tests is not a resolution.
3. `git add`, `git rebase --continue`; run the repository's tests; push with the lease.
4. Red after integration: read the checks, fix what the integration broke, test, commit, push
   the same way.
5. A refused mechanical rebase that meets no conflict: test and push. A failed fetch or push
   (auth, permission, network): push nothing and say what failed; that is not `needs_person`.

## Commits never pushed

If the prompt has *Commits that were never pushed*, do not rebase, commit or reset; push at most
once, with the lease, the tree's head as it is.
- `ahead`: push it.
- `diverged`: run the `git range-diff` the prompt names; push only when every difference is
  context the new base brought. Otherwise push nothing and hand back one `needs_person` item per
  differing commit, `commit` set.

## Stop

`git rebase --abort`, push nothing, when the two intents contradict with no resolution keeping
both, or tests cannot pass without changing a behaviour one unit states. Hand back one
`needs_person` item per contradiction (`commit` is `""` unless one commit is the cause), `why`:
`<side A: unit, artifact, what it requires> vs <side B: unit, artifact, what it requires>`.

Before your last reply call `submit` once with `{"needs_person": [{"commit", "why"}]}`, `[]`
when nothing needs a person. The app reads that object, never your reply.

End a reply that pushed with:

```
## Integration report
- <file>: <conflict> — resolved by <how>, keeping <unit A>'s <what> and <unit B>'s <what>
Tests: <command> — <what it printed>
Pushed: <new head>
```
