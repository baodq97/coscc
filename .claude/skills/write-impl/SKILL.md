---
name: write-impl
description: Write the impl.md that records what was actually built for a work unit, and what was measured. Use once the plan is accepted and the code has been written, before opening a PR.
---

# Write an impl record

`impl.md` records what was built and what was measured, so `pr` can describe a change and not
a diff. The code is in git; do not copy it here.

## What you are given (trust it)

The plan (accepted), the answers, the files `main` changed since the plan, the files' line maps
and the commands this step may run are in the prompt. Do not re-read them or re-verify the plan.
From the board the gate was asked (the prompt says so); at a terminal ask `cos.mjs gate <unit> impl`
first and stop on non-zero.

## Reading the tree

- The line map says where things are: `Read` only the range you edit; batch independent reads in one turn.
- For "where is X" across big files, ask `scout` (the `Agent` tool): it returns a `path:line`
  map. Trust it; re-read only what it marks "unsure".
- Open `intent.md`, `spec.md` or `spike.md` only where the plan cites a section.

## Work

1. Write the code from the plan, starting from an existing example of the same kind and its
   shared helpers. Run the tests of the files you change while working; the full suite once at
   the end.
2. Commit. Each claim in `impl.md` names a commit.
3. A file `main` changed that contradicts the plan: stop before editing it, record it under
   `## What is still open`, `Status: draft`, leave `plan.md` alone. Otherwise note what you
   adjusted under `## Where the plan was departed from`.
4. When a review or red CI sent the work back: fix what it named, commit and **push** (`next`
   offers `impl` until a commit outside `.cos/` reaches the PR head), and record which commit
   fixed which finding. A `low` need not be fixed; never list one under `## Needs a person`.
5. Write `impl.md`.

**A finding this stage cannot close** (needs real money, a command the grant lacks, a person's
measurement): one line under `## Needs a person`, exactly `- F<k>: <reason>`; `cos.mjs`
reads only the id. It is a claim the review accepts or rejects. Fix everything you can first.

**Work only a person can do** goes under `## Open questions`, one `N. …?` per item at column 0.
What waits on nobody goes under `## What is still open`. Never edit `## Answers`; cite an
answer as `impl.md ## Answers, câu N`.

On a unit that changes a screen, the UI standard rule's part for this stage applies.

## Artifact

```markdown
# Impl: <title>
Intent: intent.md. Plan: plan.md. Author: <name>. Status: accepted.

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
figure's source or mark it unverifiable; say when a proof was not run. `Status`: `draft`,
`accepted`, `rejected`, `done`.

## Done when

Someone who did not write the code can say what changed and what was checked.
