---
name: write-review
description: Write or extend the review.md that records a round of review of an open pull request — what was reviewed, what was found, and whether it may merge. Use once pr.md names an open pull request whose required checks are green.
---

# Write a review round

Review the open pull request for bugs, security and compliance with the plan, and append one
round to `review.md`. You are an agent judging an agent's work: `Concluded by:` says so, and
nothing here reads as a person's approval.

## What you are given (trust it)

`intent.md`, `spec.md`, `plan.md` and its record, `impl.md` (scope, test counts), `pr.md`, the
commit under review, the gate, the last round's open findings and the answers are in the prompt,
each under its own heading: do not Read them. CI is green and `impl.md` records its tests: do
not re-run the suite or report what CI enforces. From the board the gate was asked (the prompt
says so); at a terminal ask `uv run python -m coscc.loop gate <unit> review` first and stop on
non-zero. Read the changed files, not the whole tree.

## Round

1. Copy the commit from *The commit you are reviewing* (at a terminal, `git rev-parse HEAD`).
2. List findings. Carry forward every finding of every earlier round, `[fixed <sha>]` or
   `[open]`, a `low` too; a round that drops one is not counted and runs again.
3. Severity: `high` or `medium` only for broken behaviour, lost data, a security hole, or a UI
   standard rule `S<n>`, and a finding naming `S<n>` always blocks. Wording, docstrings,
   comments, citations and style are `low` nits: at most 5, the rest as a count, never
   blocking. Lowering an earlier round's `high`/`medium` is not a fix.
4. Verdict: any blocking finding not closed, `changes-requested`; otherwise `pass`, which opens
   `ship`. An `[open]` `low` does not block. If every blocking one is `[needs-person]`, `needs-person`.
5. Never write `Verdict: incomplete` and never merge.

**Claims.** For each open finding impl claimed only a person can close (its `needs_person`,
with why under `impl.md ## Needs a person`), label it: `[needs-person]` (the grant really lacks it or it costs money), or `[claim-rejected]`
(impl could have fixed it; say why). Never leave a claim `[open]`. `[answered]` only when
`review.md ## Answers` holds a `### F<k>` block that settles it; if not, keep it `[open]` and
say what is missing. An `[answered]` never returns to `[needs-person]`: raise a new id.

**Rebase before a round, not after a pass**: a rebase that changes the patch voids a pass.

On a unit that changes a screen, the UI standard rule's part for this stage applies.

## Artifact

```markdown
# Review: <title>
PR: pr.md. Author: <name>. Concluded by: <agent session, which one>.

## Round 1

Reviewed: <40-hex sha>. Verdict: changes-requested.

### Findings

- F1 [open] path/to/file.py:12 — high — what is wrong
- F2 [fixed <sha>] path/to/other.py:40 — low — what was wrong

### Screens

### What was not reviewed
```

Line shapes code parses: `Reviewed: <sha>. Verdict: <pass|changes-requested|needs-person>.`;
rounds numbered without a gap; a finding `- F<k> [label] path:line — <high|medium|low> — text`
with em dashes, label one of `[open]`, `[fixed <sha>]`, `[needs-person]`, `[claim-rejected]`,
`[answered]`; no severity blocks; no location is an opinion. `### What was not reviewed` is
required.

## Done when

A reader can tell what was looked at, what was not, what was found and that an agent reviewed it.
