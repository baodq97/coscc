---
name: write-ship
description: Write the ship.md that records what went out, when, and how it is being watched. Use once review.md is accepted and the change has landed.
---

# Write a ship record

`ship.md` closes a unit. It records what actually went out and what would tell you it went
wrong, which is the part every other artifact assumed and none of them wrote down.

It is also where the loop turns over: `docs/ai-native-sdlc-playbook.md` describes a
breached control band in production writing the next `intent.md`. This file is where that
band is stated, so there is something for a later observation to be breached *against*.

## Before writing

```
node .claude/scripts/cos.mjs gate <NNNN_slug> ship
```

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

1. **`## Did the outcome hold` answers `intent.md`'s outcome directly**, in its own terms,
   with the number it named. This is the only place the unit is judged against what it
   promised, so answering "yes" without restating the measurement is not answering.
2. **An outcome that came back false is recorded as false.** The intent was written so it
   could fail; a unit that quietly reinterprets its outcome to pass has removed the only
   check it had.
3. **`## How it is watched` names a command or a signal, not an intention.** "We will keep
   an eye on it" is not watching.
4. **`## What to do if it breaks` names the way back.** For this repository that is usually
   a commit to revert; name it.
5. Every figure names its source or is marked unverifiable.
6. Set `plan.md` to `done` only after this file is accepted — `nextAction` treats a `done`
   plan as terminal, and setting it early hides the stages that have not run.

## Done when

A reader can tell what shipped, whether the unit's outcome held, and what they would watch
to find out that it stopped holding.

## Next

Nothing. The unit is closed. If watching turns up something, that is a new `idea.md`.
