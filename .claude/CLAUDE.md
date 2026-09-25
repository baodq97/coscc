# coscc

A local AI-native SDLC harness, and the template for it. Each stage's rules live in its own
skill; `cos.mjs` decides every gate. What is written here is only what none of them enforce.

This file and `.claude/rules/` reach every session, so they have byte ceilings
(`coscc/rules_budget_test.py`). New detail goes into `.claude/docs/`, pointed to from here or
from `.claude/rules/coscc-app.md` with when to read it.

## Commands

```
npm test                                           # every test, both runtimes
uv sync                                            # dependencies, after a fresh clone

node .claude/scripts/cos.mjs status [--json]       # where every unit stands
node .claude/scripts/cos.mjs gate <unit> <stage> [--repo <dir>]   # 0 open · 1 blocked, with reasons · 2 misuse
node .claude/scripts/cos.mjs next <unit> [--repo <dir>]           # JSON: the one stage to run now, or "" and why
node .claude/scripts/cos.mjs new-path <slug>       # allocates the number, validates the slug
node .claude/scripts/cos.mjs unit-branch <unit>    # the branch name this unit's Type implies
node .claude/scripts/cos.mjs pr-text <unit>        # JSON: title and body pr.md puts on its pull request
node .claude/scripts/cos.mjs check-branch [name]   # the branch you are on, or one you are considering
node .claude/scripts/cos.mjs check-tag <tag>       # prints: release | prerelease
node .claude/scripts/cos.mjs check-version         # the five places a version is declared
```

The first six take `--root <dir>` and read another repository's `.cos/`; the last three
refuse it, and `new-path` alone also takes `--reserve-from <dir>` (repeatable) to count
that directory's numbers as taken. `gate` and `next` take `--repo <dir>`, the checkout whose
branch and pull request the `review` and `ship` gates read: with `--root` and no `--repo`
those two gates stay closed. `next` names a stage and opens nothing — ask `gate` before
running it. `COS_REVIEW_ROUNDS` (default 3) is how many review rounds may ask for changes
before the loop needs a person.

Tests must be green before any task is reported complete; never skip or delete a failing
one. There is no linter; do not invent a command for one.

## A unit of work

One directory, `.cos/NNNN_<slug>/`, holding its artifacts (eight, or nine with `spike.md`)
and nothing else. The slug names the problem rather than the solution and is fixed at
creation.

The app writes into an artifact a stage wrote in exactly one way: it appends a
`## Answers` section, and blocks under it, to the end of the file. Nothing above that
section is ever rewritten. `intent.md`'s section also holds `### Paused`, `### Dropped` and
`### Resumed` blocks, and `cos.mjs` reads them: a held unit is offered no stage and every
gate is closed on it.

Run `cos.mjs status` for the stages, their order and what each one reads.
`.claude/scripts/cos.mjs` is the one place the loop is defined; nothing may hold a second
copy of it.

**`spike` runs only when it is needed**: a spec item under `## Concerns` opening
`[unmeasured] U<n>` puts it between `spec` and `plan`, and `plan` does not open until every
`U<n>` has `Verdict: holds`.

**`plan.md: done` is terminal.** `cos.mjs` reports a unit finished without reading a single
later artifact. Set it only after the proof command has passed, and never to close a unit
that still has stages left.

Reading an artifact of a unit below `0010`: `.claude/docs/old-units.md` and
`.cos/RENAMES.md` first.

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
7. `review`: a separate agent session appends a round to `review.md`. Open findings mean
   `changes-requested`, a fix, green CI and another round, until `COS_REVIEW_ROUNDS` sends
   it to a person. An `[open]` `low` does not block; a finding naming a rule of the UI
   standard (`S<n>`) does. The detail is in `.claude/docs/branches.md`.
8. `ship`: only once `cos.mjs gate <unit> ship` exits 0, `gh pr merge --squash --delete-branch`
   with `--match-head-commit` set to the head the gate names.

Rebase, never merge `main` in; do it before a review round, not after a pass
(`.claude/docs/branches.md` says why).

Do not compose a branch name or a tag by hand. The grammars are named here and enforced by
`unit-branch`, `check-branch` and `check-tag`; pushing a tag is what builds the release.

```
<type>/<slug>    feat fix docs refactor test chore perf build ci revert
vX.Y.Z           a release
vX.Y.Z-rc.N      a prerelease, N from 1
```

What enforces this does not travel with the harness: the workflows and the GitHub ruleset
stay behind when `.claude/` is copied. Before copying it into another repository, read
`.claude/docs/copying.md`.

## What is deliberately not built

Nothing here is a person's approval — `accepted`, a review's pass, an answer, a hold — and
every gate is advisory: nothing forces a session to ask one. Before adding a route, a button
or a grant, read `.claude/docs/not-built.md`.

## Invariants

- Set `Status: accepted` when the artifact is finished, then commit it. It records that the
  agent judged it ready. It is not a human's approval and must not be read as one.
- Ask `cos.mjs gate` before a stage and stop when it exits non-zero. Fix what it names; do
  not reason your way past it. A stage started from the coscc board has had this asked for
  it already — the app refuses to start a step the gate closes, and puts the gate's answer
  in the prompt. That is for the five prose stages, which run no command — `idea` and
  `intent` hold no tools at all, `spec`, `plan` and `review` may only read — and so could
  never obey this line themselves; at a terminal it still means you.
- No code while `plan.md` is `draft`. Accept the plan in its own commit, so the
  authorization is separable from the thing it authorizes.
- Take unit paths from `cos.mjs new-path`. Never guess a number.
- Cut a figure that has no source. Do not soften it.
- Cite only a file committed in this repository, by path and line range.
- Inside `.cos/`: English filenames and headings, Vietnamese prose. Everywhere else,
  English — those are instructions to the model, not artifacts to be reviewed.
