# coscc

A local AI-native SDLC harness, and the template for it. Each stage's rules live in its own
skill; `cos.mjs` decides every gate. What is written here is only what none of them enforce.

## Commands

```
npm test                                           # every test, both runtimes
uv sync                                            # dependencies, after a fresh clone

node .claude/scripts/cos.mjs status [--json]       # where every unit stands
node .claude/scripts/cos.mjs gate <unit> <stage> [--repo <dir>]   # 0 open · 1 blocked, with reasons · 2 misuse
node .claude/scripts/cos.mjs new-path <slug>       # allocates the number, validates the slug
node .claude/scripts/cos.mjs unit-branch <unit>    # the branch name this unit's Type implies
node .claude/scripts/cos.mjs check-branch [name]   # the branch you are on, or one you are considering
node .claude/scripts/cos.mjs check-tag <tag>       # prints: release | prerelease
node .claude/scripts/cos.mjs check-version         # the five places a version is declared
```

The first four take `--root <dir>` and read another repository's `.cos/`. The last three
refuse it: given a root, they would answer about here while naming somewhere else.
`new-path` alone also takes `--reserve-from <dir>`, repeatable: numbers already used in
that directory's `.cos/` count as taken, though nothing is written there. `gate` alone
takes `--repo <dir>`: the git checkout whose branch and pull request the `review` and
`ship` gates read. Without `--root` it is this checkout; with `--root` and no `--repo`,
those two gates stay closed rather than read the wrong one. `COS_REVIEW_ROUNDS` (default
3) is how many review rounds may ask for changes before the loop needs a person.

Tests must be green before any task is reported complete; never skip or delete a failing
one. There is no linter; do not invent a command for one.

## A unit of work

One directory, `.cos/NNNN_<slug>/`, holding its eight artifacts and nothing else. The slug
names the problem rather than the solution and is fixed at creation — a slug named after a
solution stops making sense exactly when the directory still has to be findable.

The app writes into an artifact a stage wrote in exactly one way: it appends a
`## Answers` section, and blocks under it, to the end of the file. Nothing above that
section is ever rewritten.

Run `cos.mjs status` for the stages, their order and what each one reads.
`.claude/scripts/cos.mjs` is the one place the loop is defined; nothing may hold a second
copy of it.

**`plan.md: done` is terminal.** `cos.mjs` reports a unit finished without reading a single
later artifact. Set it only after the proof command has passed, and never to close a unit
that still has stages left.

**Four units are closed by a bypass.** `0005`–`0008` carry `plan.md: done` with empty `pr`,
`review` and `ship` cells. Deliberate, 2026-09-22; each of the four `plan.md` files carries
the reason at the top. `0009` is the first unit that ran all eight for real — compare them.

**Paths in artifacts written before `0008` name the Python package as it was called then.**
They were not rewritten. `.cos/RENAMES.md` is the lookup table and says why.

## Branches, tags and releases

`main` is the trunk and is not a work branch. Every change reaches it through one branch and
one pull request, squashed to one commit, rebased onto the latest `main` first.

The order per unit, and the reason it cannot be reordered:

1. `cos.mjs new-path <slug>` — allocates the number.
2. Write `intent.md` there, declaring `Type:` in its header. **Accept and commit nothing
   yet.**
3. `cos.mjs unit-branch <unit>` — it reads the file from step 2, so it cannot run before it.
4. `git switch -c <that name>` — cut from `main`, before the first commit. `main` is closed;
   a commit made on it is a commit that has to be moved.
5. Work the stages. Each artifact is its own commit.
6. `pr`: `gh pr create`, then wait for the required checks. Red sends the work back to
   `impl` on the same branch; the `review` gate stays closed until every check is green.
7. `review`: a separate agent session appends a round to `review.md`. Findings open means
   `changes-requested`, a fix on the branch, green CI again, and another round; after
   `COS_REVIEW_ROUNDS` such rounds the gate says `needs a person`. A pass is `accepted`.
8. `ship`: only once `cos.mjs gate <unit> ship` exits 0, `gh pr merge --squash --delete-branch`
   with `--match-head-commit` set to the head the gate names. Every push resets the required checks — including the commit recording the pass — so
   the merge may first be refused with `2 of 2 required status checks are expected`: wait.

When the pull request falls behind, `gh pr update-branch --rebase`. Rebase, not a merge of
`main` into the branch: the squash would remove the merge commit anyway, and keeping the
two rules pointing the same way is worth more than the shortcut. Do it before a review
round, not after a pass: a rebase rewrites the reviewed commit, the `ship` gate then
closes, and another round is needed. A round that passes does not count toward
`COS_REVIEW_ROUNDS`, so that round costs time and nothing else.

Do not compose a branch name or a tag by hand. The grammars are named here and enforced by
`unit-branch`, `check-branch` and `check-tag`; pushing a tag is what builds the release.

```
<type>/<slug>    feat fix docs refactor test chore perf build ci revert
vX.Y.Z           a release
vX.Y.Z-rc.N      a prerelease, N from 1
```

**What enforces this does not travel with the harness.** Copying `.claude/` brings the
grammars, the commands and their tests. It does not bring `.github/workflows/`, which sits
outside `.claude/`, and it cannot bring the GitHub ruleset, which is a setting rather than a
file — and the ruleset is the only thing here that actually stops a push. Since `0015` the
`review` and `ship` gates also need `git` and a logged-in `gh`, and the `review` gate reads
the pull request's **required** checks: a repository with no CI and no required check has
a `review` gate that never opens.

## What is deliberately not built

- **A person's approval.** Since `0015` there is a step that can say "not yet": `review`
  runs on the open pull request, before the merge, and `ship` cannot merge until a round
  passes with nothing open. But the one saying it is a separate agent session, not a
  person, so `accepted` is still an agent's word about an agent's work. The chair is in
  front of the door now, and an agent sits in it — nobody who is not an agent approves
  anything. The loop waits for a person in exactly one place: when `COS_REVIEW_ROUNDS`
  rounds have asked for changes and findings are still open. Anyone copying this template
  should make that trade on purpose rather than inherit it.
- **Hooks.** Every gate is advisory: nothing forces a session to run `cos.mjs`, or to stop
  when it exits non-zero. A `PreToolUse` hook blocking `Write` while `plan.md` is `draft`
  would be one file. The same holds for the merge: in the app, the `pr` grant refuses the
  merge command, `gh api …/pulls/<n>/merge` and `gh alias set` with their flags removed,
  but `node -e` spawning `gh`, or an alias defined before the step, still walks past it,
  and at a terminal nothing refuses anything. What stops a merge before review is the
  `ship` gate being asked — and only when it is asked.
- **Anything that starts the next stage.** An accepted artifact lights no gate. A person
  chooses the mode and presses the button, every time.
- **A person's answer is not an approval, and it starts nothing either.** Since `0016` the
  app has one place where a person answers an item under `## Open questions`: the
  *Questions* tab, or `POST /api/units/answer`. It appends a block under `## Answers` and
  a row to the run log, and that is all — no gate reads it and no stage runs because of it;
  the next stage finds it in its prompt when somebody presses the button. `Answered by:`
  is a name the person typed, not an identity. No route has a login and the default bind
  is `0.0.0.0`, so anyone who can reach the port can answer under any name, and the next
  stage will read it as a person's decision.
- **CI that decides more than one thing.** Since `0015` CI decides whether `review` may
  begin: the gate reads the pull request's required checks and stays closed on red,
  pending or none. Nothing else reads it. A green check also measures a different
  interpreter than the one the proofs were measured on.

## Invariants

- Set `Status: accepted` when the artifact is finished, then commit it. It records that the
  agent judged it ready. It is not a human's approval and must not be read as one.
- Ask `cos.mjs gate` before a stage and stop when it exits non-zero. Fix what it names; do
  not reason your way past it. A stage started from the coscc board has had this asked for
  it already — the app refuses to start a step the gate closes, and puts the gate's answer
  in the prompt. That is for the four prose stages that run with no tools and could never
  obey this line themselves; at a terminal it still means you.
- No code while `plan.md` is `draft`. Accept the plan in its own commit, so the
  authorization is separable from the thing it authorizes.
- Take unit paths from `cos.mjs new-path`. Never guess a number.
- Cut a figure that has no source. Do not soften it.
- Cite only a file committed in this repository, by path and line range.
- Inside `.cos/`: English filenames and headings, Vietnamese prose. Everywhere else,
  English — those are instructions to the model, not artifacts to be reviewed.

## Copying this into another repository

Copy `.claude/`. That is the whole harness, and nothing lands in the host repository's own
tree. Claude Code loads `.claude/CLAUDE.md` as project instructions, so no import, symlink
or root file is needed. Then put that repository's real build and test commands under
`## Commands`, and rebuild the two legs named above by hand.
