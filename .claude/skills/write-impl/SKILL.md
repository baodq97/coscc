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

## When a review sent the work back

If `review.md` is `changes-requested`, or the pull request's required checks are red, this
stage runs again on the same branch. Fix what the review or CI named, commit, and **push**:
`cos.mjs next <unit> --repo <dir>` offers `impl` until a commit outside the unit's own
`.cos/` files reaches the pull request's head, so a fix that stays local keeps the loop on
this stage. Then record in `impl.md` which commit fixed which finding. The step after is
`write-review` once CI is green — not `write-pr`, which already ran.

**A finding rated `low`.** Since `0061` a review rates each finding `high`, `medium` or
`low`, and an `[open]` `low` does not block (`write-review`, *Severity, and what blocks*).
Fix every finding that blocks. Fix a `low` too when you can — the next round marks it
`[fixed <sha>]` — but nothing requires it: `cos.mjs next` does not wait for one, and it
does not need claiming. Never list a `low` under `## Needs a person`.

**A finding this stage cannot close.** Some findings cannot be fixed with this stage's
grant: a proof that spends real money, a command the grant does not hold (`gh`, a login),
a measurement only a person can take. List each one under `## Needs a person`, one line
each, exactly `- F<k>: <reason>`, `F<k>` being the finding's id in `review.md`. The reason
names what the grant lacks or what would cost real money — not "hard", not "out of scope".
`cos.mjs` reads only the id, and a line of any other shape is not read at all.

This is a claim, not a verdict. When every open finding is claimed, `cos.mjs next` sends
the unit to `review`, which accepts or rejects each claim; only an accepted one makes the
loop wait for a person, and a rejected one comes back here. Listing a finding this stage
could have fixed is using the section to get past the review — do not. Fix everything you
can first; claim only what is left.

## When main changed the plan's files

A step started from the coscc board may carry a section *The files main changed since the
plan*. It lists the files named in the plan's `## Files that change` that `main` has changed
since the commit the plan was written on, each with the `git diff` command that shows how.

- The line numbers the plan cites in those files may no longer point where they did. Read
  the diff before relying on one.
- If what was merged contradicts what the plan sets out to do, stop before editing that
  file. Record the contradiction under `## What is still open`, set `Status: draft`, and do
  not edit `plan.md`.
- If nothing contradicts it, carry on, and record what you adjusted under
  `## Where the plan was departed from`.

When the section says the app could not check, nothing is known either way: read the plan's
citations against the tree as it is.

## Screens

This applies when the branch changes a file listed under `paths:` in
`.claude/rules/ui-standard.md` — a **UI unit** (`0083`). The `ship` gate will not open on one
until a review round has looked at screenshots of it.

1. After your last commit that touches such a file, run
   `uv run python scripts/capture_screens.py <address>...` with the addresses the spec's
   `## Design` lists (at most six). It takes each at 1440×900 and 390×844 on a fixed fixture
   and writes the PNGs and `manifest.json` to `.screens/`, which git ignores.
2. Open every PNG with `Read` and compare it against the standard's rules, `S1` to `S8`. Fix
   what you can, commit, and run the command again, so the manifest's `head` is the last
   commit touching a UI file.
3. Record it in `impl.md` under `## Screens`: the command, its exit code, the manifest's
   `head`, the path of each image, and every entry of the manifest's `hits` you left, each
   with the reason. A hit nobody explains is a `high` finding in review.

The command builds a bundle for its own port into `<repo>/.web`, overwriting the one the
worktree had, and builds that one again at the end (about 26 s); its last line says whether
it did. If that rebuild failed, run the command it prints before any browser proof.

## Output

One file, `impl.md`, in the unit's directory.

```markdown
# Impl: <title>
Intent: intent.md. Plan: plan.md. Author: <name>. Status: accepted.

## What was built

## Where the plan was departed from

## What was measured

## Screens

## What is still open

## Needs a person

- F<k>: <what the grant lacks, or what costs real money>
```

`## Screens` is present only on a UI unit (`## Screens` above). `## Needs a person` is
present only on a run a review sent back, and only when a finding is left that this stage
cannot close. Omit each otherwise.

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
