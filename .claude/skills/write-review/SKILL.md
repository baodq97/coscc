---
name: write-review
description: Write or extend the review.md that records a round of review of an open pull request — what was reviewed, what was found, and whether it may merge. Use once pr.md names an open pull request whose required checks are green.
---

# Write a review round

Read this section before anything else, because it is the reason this stage is the weakest
one in the loop.

**There is no separation of duties here, and this file must not pretend otherwise.**
Since `0015` the review happens **before** the merge, and a separate agent session sits in
the chair — not the session that wrote the code. That makes this a step that can say "not
yet", and the `ship` gate will not open until it says "pass". It does not make it a
person's approval: it is still an agent judging an agent, and `Concluded by:` has to say
so. A `review.md` that reads as though someone other than an agent approved the change is
more misleading than no review at all, because it looks like a person's gate.

## Before writing

```
node .claude/scripts/cos.mjs gate <NNNN_slug> review
```

The gate is closed while `pr.md` names no pull request, while any required check on it is
red or still running, and once the round limit is used up. Red CI means the work goes back
to `impl` on the branch; it is not something this stage reviews around.

## The loop

Each run of this stage appends **one round** to `review.md`. Earlier rounds are never
rewritten or removed.

1. Read the pull request as it stands: `pr.md`, `impl.md`, and the files it changed.
2. Record the commit at the head of the branch you reviewed. In the app this stage can
   only read files, and only inside the unit's worktree and the unit's own directory —
   the worktree's git directory is outside both — so the app reads the head before the
   step starts and puts it in the prompt under *The commit you are reviewing*. Copy that
   value. At a terminal, `git rev-parse HEAD`. Never guess.
3. List every finding, each with a severity (below). Carry forward **every** finding any
   earlier round raised, marked `[fixed <sha>]` with the commit that fixed it, or `[open]`
   — a `low` one too. Dropping one is refused by the `ship` gate.
4. If any finding that blocks is `[open]`: `Verdict: changes-requested`, header
   `Status: changes-requested`. The fixes are made on the same branch, pushed, CI goes
   green again, and this stage runs again for round N+1.
5. If every finding not closed is one that does not block: `Verdict: pass`, header
   `Status: accepted`. That is what opens `ship`.

**Severity, and what blocks.** Since `0061` every finding carries `high`, `medium` or
`low` between two em dashes (`—`, U+2014) right after its location:
`- F2 [open] path/to/file.py:40 — low — what`. `cos.mjs` reads only that token. A hyphen,
an en dash, a word in another language or no token at all reads as no severity, and a
finding with no severity blocks. The line is the originator's (`0061` intent, Answers, Câu 1): `low` means
nothing a user sees behaves wrongly, nothing touches security, and no data is lost;
anything else is `medium` or above.

An `[open]` finding rated `low` does not block — unless an earlier round rated the same id
`high` or `medium`. Lowering a severity is not a fix, and the `ship` gate refuses it by
name: fix it on the branch, or keep it at what it was. Every other finding not closed
blocks, whatever its severity: `[needs-person]`, `[claim-rejected]` and an unbacked
`[answered]` never become "does not block". A `low` stays `[open]` until a round sees it
fixed, is carried forward like any other, and `ship.md` lists it. Rate a finding on what
it is, not on what gets the unit through: a severity is one agent's word about another
agent's work, and nothing but a person reading the pull request checks the first one given.

**Findings impl says only a person can close.** `impl.md` may carry a `## Needs a person`
section, one line per finding: `- F<k>: <reason>`. It is impl's word about its own work,
and this stage is where it is checked. For every finding still open that impl claims there,
decide, and label it:

- `[needs-person]` — you accept the claim: the reason names something the impl grant
  really lacks (a tool, a login) or something that costs real money, and the finding
  cannot be closed without it. The finding stays not closed.
- `[claim-rejected]` — impl could have fixed it. Say why in the finding's line. The unit
  goes back to `impl`.
- `[answered]` — a person answered it: `review.md ## Answers` holds a `### F<k>` block, and
  what it says settles the finding. Only use this when that block exists; the `ship` gate
  refuses an `[answered]` with no block behind it. If the answer does not settle it, keep
  the finding `[open]` and say what is still missing: the unit goes back to `impl`, not to
  another review. If what is missing is something a person must do again, do not keep it
  `[open]`: close it `[answered]` and raise a new finding with a new id for the rest
  (rule 7), which `impl` may then claim.

A claim no round has judged does not stop the loop: `cos.mjs next` sends a unit whose open
findings are all claimed, and claimed for the first time, to this stage, not to a person.
So never leave a claim you are judging `[open]`: give it one of the three labels above. A
finding any round has already labelled `[needs-person]`, `[claim-rejected]` or `[answered]`
counts as judged, and if a later round leaves it `[open]`, `next` sends it to `impl` even
while `impl.md` still claims it. Then:

6. If every finding that blocks and is not closed is `[needs-person]`:
   `Verdict: needs-person`, header `Status: changes-requested` — even with a `low` still
   `[open]` beside them. `cos.mjs next` then offers no stage and names the findings a
   person must answer; each is answered on the board's *Questions* tab (or
   `POST /api/units/answer` with `question: "F<k>"`, `artifact: "review.md"`), which appends
   `### F<k>` under `## Answers`. Once every one has an answer, this stage runs again and
   closes each as `[answered]` or keeps it open.
7. A finding once `[answered]` never goes back to `[needs-person]`. If a person must be
   asked again, raise a new finding with a new id: the old `### F<k>` block carries no round
   number, so reusing the id would read the old answer as the new one.

**The round limit.** After `COS_REVIEW_ROUNDS` rounds (default 3) have ended in
`changes-requested`, the gate stops the loop: `needs a person`. A person decides at a
terminal — set `Status: rejected` to close the unit, or raise the limit — and nothing typed
into the product unblocks it. That is one of the two places the loop waits for someone who
is not an agent; the other is a `needs-person` round, above.

Only rounds whose verdict is `changes-requested` count toward that limit. A round that
passes, or one that ends `needs-person`, costs nothing, so reviewing again after a rebase
never brings the loop closer to `needs a person`. A round left with nothing but findings
that do not block passes, so no round is ever counted for `low` findings alone.

**A rebase voids a pass.** The `ship` gate requires the reviewed commit to be an ancestor
of the branch, and `gh pr update-branch --rebase` rewrites every commit on it. So bring
the branch up to date with `main` **before** a round, not between a pass and the merge. If
it happens anyway — `main` moved and the merge was refused as out of date — the order is:
rebase, wait for green, append another round that reviews the new head (carrying every
finding forward), then `ship`. The earlier pass stays in the history as it was written.

## Output

One file, `review.md`, in the unit's directory. The header line is rewritten each round to
carry the current status; everything under it is appended.

```markdown
# Review: <title>
PR: pr.md. Author: <name>. Concluded by: <agent session, which one>. Status: changes-requested.

## Round 1

Reviewed: <40-hex sha>. Verdict: changes-requested.

### Findings

- F1 [open] path/to/file.py:12 — high — what is wrong

### What was not reviewed

## Round 2

Reviewed: <40-hex sha>. Verdict: pass.

### Findings

- F1 [fixed <sha of the fix>] path/to/file.py:12 — high — what was wrong
- F2 [open] path/to/other.py:40 — low — what is still wrong, and does not block

### What was not reviewed
```

`Status` is `draft`, `changes-requested`, `accepted` or `rejected`. `accepted` is a pass.
`rejected` closes the unit; `changes-requested` does not.

## Invariants

1. **The first non-empty line of each round is `Reviewed: <sha>. Verdict: <pass|changes-requested|needs-person>.`**
   The `ship` gate reads it: after a pass, a commit on the branch that touches anything
   outside `.cos/<unit>/` closes the gate, because nobody reviewed it. `needs-person` only
   when every blocking finding not closed is `[needs-person]`; a round that says so while
   a blocking one is `[open]`, `[claim-rejected]` or unbacked `[answered]` is read as
   `changes-requested`.
2. **Rounds are numbered 1, 2, 3… with no gap.** A renumbered history is refused.
3. **Every finding is one line under `### Findings`: `- F<k>` and one of five labels —
   `[open]`, `[fixed <sha>]`, `[needs-person]`, `[claim-rejected]`, `[answered]` — then
   `path:line — <high|medium|low> — what`, with em dashes.** A finding with no location is
   an opinion. Any other label counts as not fixed. Only `[fixed <sha>]`, and `[answered]`
   with its `### F<k>` block in `review.md ## Answers`, count as closed; an `[open]` `low`
   is not closed, it only does not block.
4. **`Concluded by:` is required and names an agent or a person.** If an agent reviewed,
   write that. Do not write a person who did not read it.
5. **`### What was not reviewed` is not optional.** A round that claims full coverage is
   claiming something nobody checked. In the app this stage cannot run `git diff`; if you
   did not see the diff, say so here.
6. Do not raise the verdict above what the findings support. A pass with a blocking
   finding `[open]` is refused by the gate anyway; do not write one. Nor lower it: a
   `changes-requested` round whose only open findings are `low` counts toward the limit
   for nothing — that round is a pass.
7. Never merge from this stage.

## Done when

A reader can tell, round by round, what was looked at, what was not, what was found and
what fixed it — and, without guessing, that the reviewer was an agent.

## Next

`changes-requested`: fix on the branch, then this stage again.
`needs-person` round (header still `changes-requested`): a person answers each finding,
then this stage again.
`accepted`: `write-ship`, which merges.
