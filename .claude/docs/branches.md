# Branches and pull requests

Read this when a pull request falls behind `main`, before a review round, and before a merge.

- Rebase onto `main`, never merge it in (`gh pr update-branch --rebase`); do it before a review
  round.
- The gate compares against the local `origin/main` and does not fetch: fetch first at a
  terminal, or a stale one reads as a changed patch.
- A rebase that leaves the patch unchanged (only line numbers and `index` lines differ) answers
  no finding after `changes-requested`, and opens `ship` after a pass once CI is green. One that
  changes the patch voids a pass and needs another round, which does not count toward the limit.
- A red required check goes back to `impl`. A failure no commit can fix (a branch-name check on
  a branch that cannot be renamed) is `needs a person`, with the check named.
- After a rebase a UI unit's screenshots name a head that is gone; the app retakes them before a
  board review, and at a terminal the reviewer does.
- A finding's severity is an agent's word: an `[open]` `low` does not block and merges, listed in
  `ship.md`.
- The merge is pinned to the head the gate names. A push resets required checks, so a first
  refusal may be "expected": wait. A refusal as not up to date, on a clean rebase of the reviewed
  commit with green CI, offers `ship` again with no round; any other refusal waits for a round.
