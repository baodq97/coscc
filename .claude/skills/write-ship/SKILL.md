---
name: write-ship
description: Merge a work unit's reviewed pull request and write the ship.md that records what went out, when, and how it is being watched. Use once review.md has passed.
---

# Merge, then write a ship record

`ship.md` closes a unit. It records what actually went out and what would tell you it went
wrong, which is the part every other artifact assumed and none of them wrote down.

It is also where the loop turns over. A control band breached in production is what writes
the next `intent.md`, and this file is where the band is stated — so there is something for
a later observation to be breached *against*.

Since `0015` this is also the stage that merges. `pr` stops at an open pull request; the
merge waits for a review round that passed.

## Before merging

```
node .claude/scripts/cos.mjs gate <NNNN_slug> ship
```

Exit 0 means the last review round passed, no finding is open or was dropped, the rounds
are numbered without a gap, and nothing outside `.cos/<unit>/` reached the branch — local,
`origin`, or the pull request's head as GitHub reports it — after the reviewed commit.
Exit 1 means do not merge: fix what it names. If it says code landed after the pass, that
code goes back to `write-review` — it was never reviewed.

The open line ends `— merge with --match-head-commit <sha>`. That is the head the gate
checked. Copy it; do not read the head again yourself.

## Merging

```
gh pr merge <number> --squash --delete-branch --match-head-commit <sha the gate named>
```

The number is the one in `pr.md`'s `PR:` field. `--match-head-commit` is what makes the
gate's answer hold at the moment of the merge: if anything was pushed between the gate and
this command, GitHub refuses the merge instead of landing a head nobody checked. Then ask
the gate again.

**`2 of 2 required status checks are expected` after a pass is a wait, not a failure.**
The commit that recorded the passing round reset the required checks. Wait for them
(`gh pr checks <number> --required --watch`), then merge. Do not force it and do not
bypass the ruleset.

**Refused because the branch is behind `main`?** Do not rebase and merge. A rebase
rewrites the reviewed commit away and the gate will close on `the reviewed commit … is not
on …`. Rebase, wait for green, and send it back to `write-review` for another round; a
round that passes does not count toward `COS_REVIEW_ROUNDS`.

If the merge is refused for any other reason, write `ship.md` as `draft` with the refusal
in `## What went out`, and stop.

## Output

One file, `ship.md`, in the unit's directory.

```markdown
# Ship: <title>
Review: review.md. Author: <name>. Status: accepted.

## What went out

## Did the outcome hold

## How it is watched

## What to do if it breaks
```

`Status` is `draft`, `accepted` or `rejected`.

## Invariants

1. **`## What went out` names the merge commit on `main`** as `gh pr view <number> --json
   mergeCommit,mergedAt` reported it.
2. **`## Did the outcome hold` answers `intent.md`'s outcome directly**, in its own terms,
   with the number it named. This is the only place the unit is judged against what it
   promised, so answering "yes" without restating the measurement is not answering.
3. **An outcome that came back false is recorded as false.** The intent was written so it
   could fail; a unit that quietly reinterprets its outcome to pass has removed the only
   check it had.
4. **`## How it is watched` names a command or a signal, not an intention.** "We will keep
   an eye on it" is not watching.
5. **`## What to do if it breaks` names the way back.** For this repository that is usually
   a commit to revert; name it.
6. Every figure names its source or is marked unverifiable.
7. Set `plan.md` to `done` only after this file is accepted — `nextAction` treats a `done`
   plan as terminal, and setting it early hides the stages that have not run.

## Done when

The pull request is merged, and a reader can tell what shipped, whether the unit's outcome
held, and what they would watch to find out that it stopped holding.

## Next

Nothing. The unit is closed. If watching turns up something, that is a new `idea.md`.
