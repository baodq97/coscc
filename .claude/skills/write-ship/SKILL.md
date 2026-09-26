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

Exit 0 means the last review round passed, no finding that blocks is open, none was
dropped, the rounds
are numbered without a gap, and nothing outside `.cos/<unit>/` reached the branch — local,
`origin`, or the pull request's head as GitHub reports it — after the reviewed commit.
Exit 1 means do not merge: fix what it names. If it says code landed after the pass, that
code goes back to `write-review` — it was never reviewed. Since `0112` it also exits 1 when
the pull request is behind the `origin/main` this repository knows (it does not fetch):
integrate, then another review round.

The open line ends `— merge with --match-head-commit <sha>`. That is the head the gate
checked. Copy it; do not read the head again yourself.

## Merging

```
gh pr merge <url> --squash --delete-branch --match-head-commit <sha the gate named>
```

The URL is `pr.md`'s `PR:` field. Name the pull request by URL, not by number, and do not
run this from inside the unit's worktree: there `--delete-branch` merges, then fails to
switch the worktree to a `main` another worktree holds, and exits 1 with the branches left
behind — a merge that reads as a failure. The app runs this step in the unit's own
directory, which is not a checkout, for that reason. `--match-head-commit` is what makes the
gate's answer hold at the moment of the merge: if anything was pushed between the gate and
this command, GitHub refuses the merge instead of landing a head nobody checked. Then ask
the gate again.

**`2 of 2 required status checks are expected` after a pass is a wait, not a failure.**
The commit that recorded the passing round reset the required checks. Wait for them
(`gh pr checks <url> --required --watch`), then merge. Do not force it and do not
bypass the ruleset.

**Refused — the head branch not up to date, or anything else?** Write `ship.md` as `draft`
with, in `## What went out`, one line at column 0: `Refused: <what gh said, on one line>`.
Then stop. Do not rebase: the autopilot or a person integrates, and a rebase needs another
review round anyway. `cos.mjs next` reads that line and the header's `Round:`, so a later
passing round is not blocked by this draft.

## Output

One file, `ship.md`, in the unit's directory.

```markdown
# Ship: <title>
Review: review.md. Round: <n>. Author: <name>. Status: accepted.

## What went out

## Did the outcome hold

## How it is watched

## What to do if it breaks
```

`Status` is `draft`, `accepted` or `rejected`. `<n>` is the number of the passing review
round the gate read.

## Invariants

1. **`## What went out` names the merge commit on `main`** as `gh pr view <url> --json
   mergeCommit,mergedAt` reported it.
2. **`## What went out` lists what went out unfixed.** Since `0061` an `[open]` finding the
   review rated `low` does not block the merge; it goes out with it. Under the words
   "không chặn", list each one — id, location and what — or write "không có". Take the
   list from the unit's `nonBlocking` field in
   `node .claude/scripts/cos.mjs status --json`, with the same `--root` you gave the gate;
   do not read `review.md` and decide for yourself which findings qualify. No new unit is
   opened for them.
3. **`## Did the outcome hold` answers `intent.md`'s outcome directly**, in its own terms,
   with the number it named. This is the only place the unit is judged against what it
   promised, so answering "yes" without restating the measurement is not answering.
4. **An outcome that came back false is recorded as false.** The intent was written so it
   could fail; a unit that quietly reinterprets its outcome to pass has removed the only
   check it had.
5. **`## How it is watched` names a command or a signal, not an intention.** "We will keep
   an eye on it" is not watching.
6. **`## What to do if it breaks` names the way back.** For this repository that is usually
   a commit to revert; name it.
7. Every figure names its source or is marked unverifiable.
8. Set `plan.md` to `done` only after this file is accepted — `nextAction` treats a `done`
   plan as terminal, and setting it early hides the stages that have not run.

## Done when

The pull request is merged, and a reader can tell what shipped, whether the unit's outcome
held, and what they would watch to find out that it stopped holding.

## Next

Nothing. The unit is closed. If watching turns up something, that is a new `idea.md`.
