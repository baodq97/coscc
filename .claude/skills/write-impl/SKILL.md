---
name: write-impl
description: Write the impl.md that records what was actually built for a work unit, and what was measured. Use once the plan is accepted and the code has been written, before opening a PR.
---

# Write an impl record

`impl.md` is not the code. The code is in git, and duplicating it here would rot. This file
is the record of **what was done and what was measured**, and it exists because the stage
after it — `pr` — has to describe the change to someone, and a description written from a
diff is a description of lines rather than of a change.

## Before writing

```
node .claude/scripts/cos.mjs gate <NNNN_slug> impl
```

Exit 0 means proceed. The gate clears on an accepted plan. `implement` is accepted as an
alias for the same stage name.

## Output

One file, `impl.md`, in the unit's directory.

```markdown
# Impl: <title>
Intent: intent.md. Plan: plan.md. Author: <name>. Status: accepted.

## What was built

## Where the plan was departed from

## What was measured

## What is still open
```

`Status` is `draft`, `accepted`, `rejected` or `done`.

## Invariants

1. **Name the commits.** Every claim about what was built points at a commit in this
   repository. A claim with no commit behind it is cut.
2. **Departures are the point of `## Where the plan was departed from`.** `write-plan`
   invariant 8 already requires `plan.md` to be updated in the same commit as a departure;
   this section is where they are collected so the `pr` stage does not have to hunt.
   "None" is an acceptable answer only if it is true.
3. **`## What was measured` carries commands and their results**, not adjectives. "Tests
   pass" is not a measurement; `npm test` with the count it printed is.
4. Every figure names its source or is marked unverifiable.
5. Do not claim a proof command passed unless it was run. If it was not run, say so here
   rather than leaving the reader to assume.
6. Set `done` only once the unit has shipped.

## Done when

Someone who did not write the code can describe the change accurately from this file, and
can tell which parts of it were checked and which were not.

## Next

`write-pr`.
