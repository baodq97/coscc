# Branches and pull requests: the detail

Read this when a pull request falls behind `main`, before a review round, and before a merge.
Moved here from `.claude/CLAUDE.md`; `## Branches, tags and releases` there keeps
the eight steps.

## When the pull request falls behind

When the pull request falls behind, `gh pr update-branch --rebase`. Rebase, not a merge of
`main` into the branch: the squash would remove the merge commit anyway, and keeping the
two rules pointing the same way is worth more than the shortcut. Do it before a review
round. After `changes-requested`, a rebase that leaves the unit's patch unchanged answers no
finding, so `next` offers `impl`, not another round, until a fix reaches the branch.
After a pass, a rebase that leaves the unit's patch unchanged — the same added,
removed and context lines; only line numbers and `index` lines may differ — opens `ship`
once CI is green, with no round. One that changes any of those lines closes the
gate and needs another round, which does not count toward `COS_REVIEW_ROUNDS`. The gate
compares against the `origin/main` it has and does not fetch: at a terminal, fetch it
before asking, or a stale one reads as a changed patch.

A red required check sends the work back to `impl`, except the harness's branch-name check: when a job whose `run:` step calls `cos.mjs check-branch` is red and
`check-branch` refuses the head GitHub reports, no commit and no rerun can turn it green.
`next` then offers nothing and says `needs a person`, with the check and the line
`check-branch` prints, and the autopilot stops `b` on it. The stop lifts only once `pr.md`
names a pull request from a valid branch: renaming the branch and opening that pull request
is the person's to do. When `next` cannot tell whether the name is why, the unit goes to
`impl` with the red line and a clause saying so.

A rebase before a round no longer spends that round on a UI unit's screenshots alone.
The rebase leaves `.screens/manifest.json` naming a head that is gone; before a
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
A pass is `accepted`. Each finding carries `high`, `medium` or `low`, and
an `[open]` `low` does not block: a round whose remaining findings are all `low` passes,
`ship` merges with them open, and `ship.md` lists them. The severity is an agent's
word — one that rated a real problem `low` from the first round lets it merge, and
only a person reading the pull request would see it. A unit whose branch
changes a file `.claude/rules/ui-standard.md` lists also needs a `### Screens` section in
its passing round, and a finding naming a rule of that standard (`S<n>`) blocks even
when `low`.

## Ship, in full (step 8)

The merge sets `--match-head-commit` to the head the gate names. Every push resets the
required checks — including the commit recording the pass — so the merge may first be
refused with `2 of 2 required status checks are expected`: wait. The gate is
closed while the pull request is behind the `origin/main` this repository knows, and a
merge refused anyway leaves a `draft` `ship.md` with `Round:` and a `Refused:` line, which
a later passing round goes past. So does a refusal as not up to date once the
pull request's head is a clean rebase of the reviewed commit and CI is green on it: `next`
offers `ship` again with no round. Any other refusal still stops until a later round.
