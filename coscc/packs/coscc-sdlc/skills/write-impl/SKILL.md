---
name: write-impl
description: Write the impl.md that records what was actually built for a work unit, and what was measured. Use once the plan is accepted and the code has been written, before opening a PR.
---

# Write an impl record

`impl.md` records what was built and what was measured, so `pr` can describe a change and not
a diff. The code is in git; do not copy it here.

## What you are given (trust it)

`intent.md`, `spec.md`, the accepted `plan.md`, the answers, the findings a review left open, the
files `main` changed since the plan and the files' line maps are in the prompt, each under its
own heading. Do not Read them or re-verify the plan; Read `impl.md` only to edit it.
From the board the gate was asked (the prompt says so); at a terminal ask
`uv run python -m coscc.loop gate <unit> impl` first and stop on non-zero.

## Reading the tree

- The line map says where things are: `Read` only the range you edit; batch independent reads in one turn.
- For "where is X" across big files, ask `scout` (the `Agent` tool): it returns a `path:line`
  map. Trust it; re-read only what it marks "unsure".
- Open `intent.md`, `spec.md` or `spike.md` only where the plan cites a section.

## Work

1. Write the code from the plan, starting from an existing example of the same kind and its
   shared helpers. Run the tests of the files you change while working, and the plan's
   `## Verification` and the whole suite once at the end. A test red that your change did not
   cause goes under `## What is still open` with its name and error; never rerun it to green.
2. For each rule you change, search again for every other path to the same outcome, plan or no
   plan: other writers of the same field or state, the automatic paths and the ones a person
   starts, every caller. Change one the plan missed to the new rule and note it under
   `## Where the plan was departed from`. Put each search and the count it printed under
   `## What was measured`. For a symbol moved, renamed or deleted, a search for the old name
   finds 0 callers left.
3. Commit. Each claim in `impl.md` names a commit.
4. A file `main` changed that contradicts the plan: stop before editing it, record it under
   `## What is still open`, submit `not-ready`, leave `plan.md` alone. Otherwise note what you
   adjusted under `## Where the plan was departed from`.
5. When a review or red CI sent the work back: fix what it named, commit and **push** in the
   one form the prompt names, `git push origin <branch>` (`next` offers `impl` until a commit
   outside `.cos/` reaches the PR head), and record which commit fixed which finding. For each
   open `high`/`medium` finding of the last round, read its location at the commit you push and
   apply the same fix to the other paths to the same outcome, searched as in step 2. A `low`
   need not be fixed; never list one under `## Needs a person`.
6. Write `impl.md`.

**A finding this stage cannot close** (needs real money, a command the grant lacks, a person's
measurement): name its `F<k>` in `needs_person` of `submit`, and say why under
`## Needs a person` for the reviewer. Only an open finding of the last round may be named. It
is a claim the review accepts or rejects. Fix everything you can first.

**Work only a person can do** goes under `## Open questions`, one `N. …?` per item at column 0.
What waits on nobody goes under `## What is still open`. Never edit `## Answers`; cite an
answer as `impl.md ## Answers, câu N`.

On a unit that changes a screen, the UI standard rule's part for this stage applies.

## Artifact

```markdown
# Impl: <title>
Intent: intent.md. Plan: plan.md. Author: <name>.

## What was built

## Where the plan was departed from

## What was measured

## Screens

## What is still open

## Open questions

## Needs a person

- F<k>: <what the grant lacks, or what costs real money>
```

`## Screens` only on a UI unit; `## Needs a person` only on a run a review sent back.
`## What was measured` holds commands with the count they printed, not adjectives; name a
figure's source or mark it unverifiable; say when a proof was not run.

## Done when

Someone who did not write the code can say what changed and what was checked.
