---
name: write-impl
description: Write the impl.md that records what was actually built for a work unit, and what was measured. Use once the plan is accepted and the code has been written, before opening a PR.
---

# Write an impl record

`impl.md` records what was built and what was measured, so `pr` can describe a change and not
a diff. The code is in git; do not copy it here.

## What you are given (trust it)

The plan (accepted) is in the prompt, with the answers already given, any files `main` changed
since the plan, the files' line maps and the commands this
step may run. Do not re-read what the prompt carries or re-verify the plan. From the board the
gate was asked (the prompt says so); at a terminal run `cos.mjs gate <unit> impl` first and stop on
non-zero.

## Reading the tree

- The line map in the prompt says where things are. `Read` only the range you are about to
  edit (`offset`/`limit`), never a whole large file.
- Put `Read`, `Grep` and `Glob` calls that do not depend on each other in one turn.
- For "where is X" across big files, ask `scout` (the `Agent` tool, on its own context): it
  returns a `path:line` map. Trust it; re-read only what it marks "unsure".
- The plan is complete. Do not open `intent.md`, `spec.md` or `spike.md` unless the plan cites
  a section of one.

## Work

1. Write the code from the plan. Run the tests of the files you change while working; run
   `npm test` once at the end.
2. Commit. Each claim in `impl.md` names a commit.
3. If a file `main` changed contradicts the plan, stop before editing it: record it under
   `## What is still open`, `Status: draft`, and leave `plan.md` alone. Otherwise note what you
   adjusted under `## Where the plan was departed from`.
4. When a review or red CI sent the work back: fix what it named, commit and **push** (`next`
   offers `impl` until a commit outside `.cos/` reaches the PR head), and record which commit
   fixed which finding. A `low` need not be fixed; never list one under `## Needs a person`.
5. Write `impl.md`.

**The `fast` lane** (a `Type: fix` with no spec or plan: `intent.md` is the plan). The gate
opening is what lets you code. In this order:

1. Check the file `intent.md ## Expected` names under `Source:` in the checkout: it must say
   what `## Expected` says.
2. Commit a test that reproduces the bug, and nothing else; run it and keep its failing output.
3. Commit the fix; run the same test and keep its passing output.
4. `## What was measured` gives both shas, the test command, the failing output at the first
   and the passing output at the second. The header names no `Plan:`.

Leave the lane when the source does not say what `## Expected` says, when something cannot be
measured from here, or when the fix fails criterion 1, 2, 3 or 5 of `write-spec`'s `## Skip`:
add `Lane: full` to the header of `impl.md` and a `## Why full`, set `Status: draft` (submit
`not-ready`), and keep the code on the branch. `next` then offers `spec`; after the plan, impl
runs again and rewrites `impl.md` without the `Lane:` line. There is no way back.

**A finding this stage cannot close** (needs real money, a command the grant lacks, a person's
measurement): one line under `## Needs a person`, exactly `- F<k>: <reason>`; `cos.mjs`
reads only the id, a line of another shape is not read. It is a claim: the review accepts or
rejects it. Fix everything you can first.

**Screens.** If the branch changes a file under `paths:` in `.claude/rules/ui-standard.md`,
after your last commit touching one, with a clean tree, run
`uv run python scripts/capture_screens.py <address>...` (the spec's `## Design` addresses, at
most six), `Read` every PNG against `S1`-`S8`, fix, commit and run it again so the manifest's
`head` is the last UI commit. Record under `## Screens`: the command, its exit code, the
`head`, each image path, and every manifest `hit` left with its reason (an unexplained hit is
a `high` finding). If its last line says the `.web` rebuild failed, run the command it prints
before any browser proof.

**Work only a person can do** (a command at a terminal, a login, real money) goes under
`## Open questions`, one `N. …?` per item at column 0; keep the heading once written. What
waits on nobody goes under `## What is still open`. Never edit `## Answers`; cite an answer as
`impl.md ## Answers, câu N`.

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

1. <what only a person can do, and what to bring back>?

## Needs a person

- F<k>: <what the grant lacks, or what costs real money>
```

`## Screens` only on a UI unit; `## Open questions` once a person was waited on;
`## Needs a person` only on a run a review sent back. `## What was measured` holds commands
with the count they printed, not adjectives; name a figure's source or mark it unverifiable;
say when a proof was not run. `Status`: `draft`, `accepted`, `rejected`, `done`.

## Done when

Someone who did not write the code can say what changed and what was checked.
