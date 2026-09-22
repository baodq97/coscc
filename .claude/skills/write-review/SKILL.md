---
name: write-review
description: Write the review.md that records what a review of the change found, and who concluded it. Use once pr.md is accepted and the change has been reviewed.
---

# Write a review record

Read this section before anything else, because it is the reason this stage is the weakest
one in the loop.

**There is no separation of duties here, and this file must not pretend otherwise.**
The author works alone and `accepted` is a word the agent writes about its own work, so
adding a `review` stage does not create an approver — it creates a *place to record one*. A `review.md` marked `accepted` by
the same agent that wrote the code is a green cell over an empty chair, and it is **more**
misleading than having no cell at all, because it looks like a gate.

This is unresolved and the author decides it, not this file. Until they do, the minimum
owed to a reader is to say **who concluded it**, in a way that distinguishes an agent's
self-issued verdict from a person's.

## Before writing

```
node .claude/scripts/cos.mjs gate <NNNN_slug> review
```

## Output

One file, `review.md`, in the unit's directory.

```markdown
# Review: <title>
PR: pr.md. Author: <name>. Concluded by: <agent|human, and which>. Status: accepted.

## Findings

## What was not reviewed

## Verdict
```

`Status` is `draft`, `accepted` or `rejected`.

## Invariants

1. **`Concluded by:` is required and names an agent or a person.** If an agent reviewed its
   own work, write that. Do not leave it blank and do not write a person who did not read
   it.
2. **`## Findings` is a list with locations**, each `path:line`. A finding with no location
   is an opinion.
3. **`## What was not reviewed` is not optional.** A review that claims full coverage is
   claiming something nobody checked. Name what was skipped and why.
4. Severity is stated, not implied by ordering alone.
5. A finding that was fixed says which commit fixed it.
6. Do not raise the verdict above what the findings support.

## Done when

A reader can tell what was looked at, what was not, what was found, and — without guessing
— whether a person ever read it.

## Next

`write-ship`.
