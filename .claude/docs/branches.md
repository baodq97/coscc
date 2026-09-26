# Branches and pull requests: the detail

Read this when a pull request falls behind `main`, before a review round, and before a merge.
Moved here from `.claude/CLAUDE.md` (`0094`); `## Branches, tags and releases` there keeps
the eight steps.

## When the pull request falls behind

When the pull request falls behind, `gh pr update-branch --rebase`. Rebase, not a merge of
`main` into the branch: the squash would remove the merge commit anyway, and keeping the
two rules pointing the same way is worth more than the shortcut. Do it before a review
round, not after a pass: a rebase rewrites the reviewed commit, the `ship` gate then
closes, and another round is needed. A round that passes does not count toward
`COS_REVIEW_ROUNDS`, so that round costs time and nothing else.

A rebase before a round no longer spends that round on a UI unit's screenshots alone
(`0111`). The rebase leaves `.screens/manifest.json` naming a head that is gone; before a
board `review` step, `cos.mjs screens <unit> --repo <tree>` says so, and the app takes them
again on the addresses `impl` chose, or refuses the step when that fails. At a terminal
nothing does it for you: `write-review`, *Screens*, says what the one running the round
does first.

## Review, in full (step 7)

A separate agent session appends a round to `review.md`. Findings open means
`changes-requested`, a fix on the branch, green CI again, and another round; after
`COS_REVIEW_ROUNDS` such rounds the gate says `needs a person`. A round whose every
remaining finding the review confirmed needs a person ends `Verdict: needs-person`,
does not count toward that limit, and `next` offers nothing until each is answered.
A pass is `accepted`. Since `0061` each finding carries `high`, `medium` or `low`, and
an `[open]` `low` does not block: a round whose remaining findings are all `low` passes,
`ship` merges with them open, and `ship.md` lists them. The severity is an agent's
word — one that rated a real problem `low` from the first round lets it merge, and
only a person reading the pull request would see it. Since `0083` a unit whose branch
changes a file `.claude/rules/ui-standard.md` lists also needs a `### Screens` section in
its passing round, and a finding naming a rule of that standard (`S<n>`) blocks even
when `low`.

## Ship, in full (step 8)

The merge sets `--match-head-commit` to the head the gate names. Every push resets the
required checks — including the commit recording the pass — so the merge may first be
refused with `2 of 2 required status checks are expected`: wait.
