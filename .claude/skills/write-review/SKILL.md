---
name: write-review
description: Write or extend the review.md that records a round of review of an open pull request — what was reviewed, what was found, and whether it may merge. Use once pr.md names an open pull request whose required checks are green.
---

# Write a review round

Review the open pull request for bugs, security and compliance with the plan, and append one
round to `review.md`. You are an agent judging an agent's work: `Concluded by:` says so, and
nothing here reads as a person's approval.

## What you are given (trust it)

`impl.md` (scope, test counts, answers), the commit under review, the gate, the earlier
findings and any answers are in the prompt. CI is green and `impl.md` records its tests: do not
re-run the suite or report what CI enforces. From the board the gate was asked (the prompt
says so); at a terminal run `cos.mjs gate <unit> review` first and stop on non-zero. Read the
changed files, not the whole tree.

## Round

1. Copy the commit from *The commit you are reviewing* (at a terminal, `git rev-parse HEAD`).
2. List findings. Carry forward every finding of every earlier round, `[fixed <sha>]` or
   `[open]`, a `low` too; a round that drops one is not counted and runs again.
3. Severity: `high` or `medium` only for broken behaviour, lost data, a security hole, or a UI
   standard rule `S<n>`, and a finding naming `S<n>` always blocks. Wording, docstrings,
   comments, citations and style are `low` nits: at most 5, the rest as a count, never
   blocking. Lowering an earlier round's `high`/`medium` is not a fix.
4. Verdict: any blocking finding not closed, `changes-requested` (header `Status:
   changes-requested`); otherwise `pass` (`Status: accepted`), which opens `ship`. An `[open]`
   `low` does not block. If every blocking one is `[needs-person]`, `needs-person`.
5. Never write `Verdict: incomplete` (only the app's closing turn does) and never merge. After
   an `incomplete` round, read its *What was not reviewed* first, then write a full round for
   this head, carrying every finding forward.

**Claims.** For each open finding `impl.md ## Needs a person` claims (`- F<k>: <reason>`),
label it: `[needs-person]` (the grant really lacks it or it costs money), or `[claim-rejected]`
(impl could have fixed it; say why). Never leave a claim `[open]`. `[answered]` only when
`review.md ## Answers` holds a `### F<k>` block that settles it; if not, keep it `[open]` and
say what is missing. An `[answered]` never returns to `[needs-person]`: raise a new id.

**The `fast` lane** (a `Type: fix` with no `spec.md` or `plan.md`): check three things, and a
missing one is a finding of `medium` or more, never `low`: the commit holding only the
reproducing test comes before the fix; `impl.md` shows that same test failing at the first
and passing at the fix; the file `Source:` names says what `intent.md ## Expected` says.

**Rebase before a round, not after a pass**: a rebase that changes the patch voids a pass.

## Screens

On a UI unit (a changed file under `paths:` in `.claude/rules/ui-standard.md`) open
`.screens/manifest.json` and `Read` every PNG it lists against `S1`-`S8`. Add `### Screens`:
first line exactly `Taken at: <manifest head>. Standard: .claude/rules/ui-standard.md. Looked at by: <agent session>, from screenshots.`
then `- <path>.png — <W>×<H> — <address> — <what you saw>` per image. A violation is a finding
whose first word after its severity is the rule id. `high`, and `changes-requested`: no
manifest or image, a `head` older than the last UI commit, `dirty: true`, an unexplained
`hits` entry. When the prompt carries *The screenshots, taken again*, the app took them; a hit
counts as explained if `impl.md ## Screens` explains one with the same address, size and kind.
At a terminal, if the manifest `head` is not an ancestor of HEAD, run
`uv run python scripts/capture_screens.py <its addresses>` first. Name unreachable screens
under `### What was not reviewed`. If *The gate, already asked* says a round passed and ship is
still closed, fix what that line names in this round.

## Artifact

```markdown
# Review: <title>
PR: pr.md. Author: <name>. Concluded by: <agent session, which one>. Status: changes-requested.

## Round 1

Reviewed: <40-hex sha>. Verdict: changes-requested.

### Findings

- F1 [open] path/to/file.py:12 — high — what is wrong
- F2 [fixed <sha>] path/to/other.py:40 — low — what was wrong

### Screens

### What was not reviewed
```

Lines code parses: a round opens `Reviewed: <sha>. Verdict: <pass|changes-requested|needs-person>.`;
rounds numbered 1, 2, 3 with no gap; a finding is one line `- F<k> [label] path:line — <high|medium|low> — text`
with em dashes (U+2014), label one of `[open]`, `[fixed <sha>]`, `[needs-person]`,
`[claim-rejected]`, `[answered]`; a finding with no severity blocks; no location is an opinion.
`### What was not reviewed` is required; if you did not see the diff, say so. `Status`:
`draft`, `changes-requested`, `accepted`, `rejected`; the header is rewritten each round, the
rest appended.

## Done when

A reader can tell what was looked at, what was not, what was found and that an agent reviewed it.
