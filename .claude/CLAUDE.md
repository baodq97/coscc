# coscc

A local AI-native SDLC harness, and the template for it. Each stage's rules live in its own
skill; `cos.mjs` decides every gate. What is written here is only what none of them enforce.

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

The first six take `--root <dir>` and read another repository's `.cos/`. The last three
refuse it: given a root, they would answer about here while naming somewhere else.
`new-path` alone also takes `--reserve-from <dir>`, repeatable: numbers already used in
that directory's `.cos/` count as taken, though nothing is written there. `gate` and `next`
take `--repo <dir>`: the git checkout whose branch and pull request the `review` and
`ship` gates read. Without `--root` it is this checkout; with `--root` and no `--repo`,
those two gates stay closed rather than read the wrong one, and `next` offers nothing
where it would need them. `next` names a stage; it opens nothing — ask `gate` before
running it. `COS_REVIEW_ROUNDS` (default
3) is how many review rounds may ask for changes before the loop needs a person.

Tests must be green before any task is reported complete; never skip or delete a failing
one. There is no linter; do not invent a command for one.

## A unit of work

One directory, `.cos/NNNN_<slug>/`, holding its artifacts (eight, or nine when the spec
leaves a question unmeasured) and nothing else. The slug
names the problem rather than the solution and is fixed at creation — a slug named after a
solution stops making sense exactly when the directory still has to be findable.

The app writes into an artifact a stage wrote in exactly one way: it appends a
`## Answers` section, and blocks under it, to the end of the file. Nothing above that
section is ever rewritten. Since `0045` `intent.md`'s section also holds `### Paused`,
`### Dropped` and `### Resumed` blocks — a person's hold on the unit, not an answer — and
`cos.mjs` reads them: a held unit is offered no stage and every gate is closed on it.

Run `cos.mjs status` for the stages, their order and what each one reads.
`.claude/scripts/cos.mjs` is the one place the loop is defined; nothing may hold a second
copy of it.

**`spike` runs only when it is needed.** Since `0039` a spec item under `## Concerns` that
opens `[unmeasured] U<n>` puts a `spike` stage between `spec` and `plan`: ᛈ Perthro
measures each question in a throwaway directory and writes `spike.md`, and `plan` does not
open until every `U<n>` has `Verdict: holds`. A spec with no such item walks the loop as it
did before, with no ninth artifact.

**`plan.md: done` is terminal.** `cos.mjs` reports a unit finished without reading a single
later artifact. Set it only after the proof command has passed, and never to close a unit
that still has stages left.

**Four units are closed by a bypass.** `0005`–`0008` carry `plan.md: done` with empty `pr`,
`review` and `ship` cells. Deliberate, 2026-09-22; each of the four `plan.md` files carries
the reason at the top. `0009` is the first unit that ran all eight stages of the time for real — compare them.

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
   `COS_REVIEW_ROUNDS` such rounds the gate says `needs a person`. A round whose every
   remaining finding the review confirmed needs a person ends `Verdict: needs-person`,
   does not count toward that limit, and `next` offers nothing until each is answered.
   A pass is `accepted`. Since `0061` each finding carries `high`, `medium` or `low`, and
   an `[open]` `low` does not block: a round whose remaining findings are all `low` passes,
   `ship` merges with them open, and `ship.md` lists them. The severity is an agent's
   word — one that rated a real problem `low` from the first round lets it merge, and
   only a person reading the pull request would see it.
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
  anything. The loop waits for a person in two places: when `COS_REVIEW_ROUNDS` rounds have
  asked for changes and findings are still open; and, since `0028`, when a review round
  ends `Verdict: needs-person` — every finding left is one `impl` listed under
  `impl.md ## Needs a person` and the review accepted as needing one. That second stop is
  an agent's claim confirmed by another agent, not proof that the finding could not be
  fixed. Anyone copying this template should make that trade on purpose rather than
  inherit it.
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
  is a name the person typed, not an identity. Since `0070` there is one master password
  and it names nobody: whoever holds it or a live session cookie can answer under any
  name, and the next stage will read it as a person's decision.
  One kind of answer is read by more than a prompt. Since `0028` a finding the last review
  round marked `[needs-person]` is answered as `F<n>` into `review.md`, as a `### F<n>`
  block: `cos.mjs next` reads it to offer `review` again once every such finding has one,
  and the `ship` gate counts a finding that review then marks `[answered]` as closed only
  when that block exists. So a block anyone holding the password or a session can write,
  followed by one agent's round, is part of what opens `ship`.
  A third kind is no answer at all. Since `0047` a finished unit's outcome is recorded as a
  `### Outcome` block under `intent.md ## Answers` (`POST /api/units/outcome`, or the
  unit's *Outcome* panel): `cos.mjs` reads it into `outcome` for the board's label, and no
  gate and no `next` reads it — a unit that is `done` stays done whatever it says.
- **A review comment is not an approval, and no gate reads it.** Since `0021` the app posts
  each round of `review.md` to the unit's pull request as one ordinary review comment —
  never `gh pr review` — under this machine's `gh` login, verbatim, first line saying an
  agent session wrote it. It is a deliberate exception to "nothing of coscc's goes into
  the repository": the pull request is where the team reads. A round the board ran is
  posted when it is written; a round written at a terminal reaches the pull request only
  when someone presses *Post to PR* on the board, or calls `POST /api/units/review-comment`.
  Whoever holds the password or a live session can make this machine's login post a
  round through that route. Neither gate changes its answer because of a comment.
- **An integration is not an approval, and nothing starts one.** Since `0035` the board
  shows whether a unit between `pr` and `ship` has fallen behind `main`, and an
  *Integrate* button (`POST /api/units/integrate`) rebases it: the app itself through
  `gh pr update-branch --rebase` when GitHub reports no conflict, or Gebo — an agent
  session under the `integrate` grant, rules in `.claude/skills/integrate/SKILL.md` — when
  it conflicts, when CI went red after an integration, or, since `0052`, when
  `gh pr update-branch --rebase` was refused, whatever the reason: a login or the network
  opens a paid session too. Since `0052` a press fetches `origin/main` first, so a unit the
  board counted `current` against a stale ref also has the button. Gebo's grant allows one push, with a
  lease bound to the head it began at, and refuses the other roads its own commands hold
  (`gh api`, `git send-pack`, an alias made during the step) — but, like every grant here,
  it reads words: `node -e` or `python -c` pushing by itself still walks past. Gebo stops with `[needs-person]` rather than drop one side. How
  a conflict was resolved is the app's or an agent's word; the next review round is the
  only thing that reads it. No board read, timer or finished step presses the button, and
  whoever holds the password or a live session can make this machine's `gh` login rebase a
  unit's pull request or open a paid session. It is not a stage and `cos.mjs`
  does not know it exists beyond the `betweenPrAndShip` field `status --json` carries.
- **Stopping a step is not an approval, and anyone holding the password can do it.** Since
  `0034` the board lists the steps running in a workspace, each with a *Stop* button
  (`POST /api/board/stop`). A stopped step ends `stopped` in the run log with
  `stopped_by`, a name the person typed rather than an identity; it writes no artifact,
  opens and closes no gate, and starts nothing. What it had already committed or pushed
  stays. A step stopped before its first turn ran nothing and leaves no line in the run
  log at all. Whoever holds the password or a live session can stop anyone's step under
  any name.
- **A hold is not an approval, and it starts nothing.** Since `0045` the board can pause,
  drop or resume a unit (`POST /api/units/hold`), with one line of reason and a typed name.
  It appends a block under `intent.md ## Answers` and a `hold` row to the run log; `cos.mjs`
  then offers the unit no stage and closes every gate on it. Resuming runs nothing either.
  Dropping also closes the unit's open pull request **with this machine's `gh` login** and
  removes its worktree; the remote and local branches stay. Whoever holds the password or
  a live session can pause every unit, or drop one and close its pull request, under any
  name. A move is refused while a step or an
  integration of that unit runs — but only one this process started; a chat, a terminal or
  a second app is not seen.
- **An update is not an approval, and anyone holding the password can press it.** Since
  `0068` an install made by `install.sh` updates itself from the Board
  (`POST /api/update/apply`, `/cancel`, `/build-local`). Applying can stop every running
  step and chat turn of this process when the person chooses "áp dụng ngay", and restarts
  the process; the local build runs upstream `main`'s build scripts under this user.
  Whoever holds the password or a live session can do all of it under any name. The only constraint is what gets installed: a wheel from
  `github.com/baodq97/coscc` checked against its release's `SHA256SUMS`, or one this
  machine built from `origin/main`, and no request carries a URL, path, version or ref.
  Nothing in the repository enforces that constraint beyond the app's own code: a
  `SHA256SUMS` from the same release catches a torn file, not a compromised release, and
  anyone who can write to `COS_DATA_DIR` can place a wheel and a manifest that agree. It
  opens and closes no gate and starts no stage.
- **A login that knows who you are.** Since `0070` every route — the page, its socket,
  `/api`, the static files, paths that do not exist — is refused without a live session;
  only `/api/health`, `/login`, and `/setup` until a password is set, answer.
  `coscc/auth.py` is that door, and it is one master password for one user: it proves
  someone holds the password, not who they are, so every typed name above is still only a
  word. The default bind is still `0.0.0.0` and the app serves plain HTTP, so off loopback
  the password, the cookie and the setup token cross the network readable until someone
  puts TLS in front. The setup token sits in the service's journal, readable by the `adm`
  and `systemd-journal` groups until the password is set. `coscc reset-password`, at a
  shell on the machine, is the only way back from a forgotten password.
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

## Copying this into another repository

Copy `.claude/`. That is the whole harness, and nothing lands in the host repository's own
tree. Claude Code loads `.claude/CLAUDE.md` as project instructions, so no import, symlink
or root file is needed. Then put that repository's real build and test commands under
`## Commands`, and rebuild the two legs named above by hand.
