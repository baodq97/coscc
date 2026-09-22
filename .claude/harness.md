# The harness

Reference for how this repository runs a unit of work. `.claude/CLAUDE.md` carries only
the rules that must hold in every session; everything explaining *why* lives here, and
everything that applies to one stage lives in that stage's skill.

Read this when setting the harness up, changing it, or copying it into another repository.
A session in the middle of a stage does not need it.

**Paths in `.cos/` artifacts written before `0008` name the Python package as it was called
then.** They were deliberately not rewritten — `.cos/RENAMES.md` is the lookup table, and
records why adding a note to each affected file would have broken more citations than it
fixed.

## What this is

A local implementation of the AI-native SDLC described in [Anthropic's AI-Native SDLC
playbook](https://claude.com/blog/the-ai-native-sdlc-playbook), covering all eight stages:
idea, intent, spec, plan, impl, pr, review, ship. It is also the
template — copy `.claude/` into another repository and the loop works there.

It was three stages until `0005` (2026-09-22) widened it to eight. The five units closed
under the three-stage loop still read as finished, because `plan.md: done` is terminal in
`cos.mjs` and `idea` gates nothing — widening the loop was not allowed to reopen work that
had already been proved.

There are no hooks, no CI and no scheduled jobs. It is driven by hand **except for two
stages**: `impl` and `pr` can be set to `autonomous` on the board in `coscc/`, and then
the app runs them itself with a bounded grant. Everything else is a person starting a step.
Nothing starts the next step when one finishes — the board gives you a place to press, not
something that presses for you.

What is mechanical lives in `.claude/scripts/cos.mjs`; what is judgement lives in the skills.
The split matters for how much each can be trusted. Whether an intent is accepted is a fact
the script reads off disk the same way every time. Whether a spec is any good is not, and no
script pretends otherwise.

Even the mechanical half stays advisory in one respect: nothing forces a session to run the
script, or to stop when it exits non-zero. A `PreToolUse` hook would. Until then the gate
is a question the session is asked to ask, and each skill states that limit on itself.

## The loop

| Stage | Skill | Artifact | Reads | Optional |
|---|---|---|---|---|
| `idea` | `write-idea` | `idea.md` | nothing | yes |
| `intent` | `write-intent` | `intent.md` | `idea.md`, if there is one | no |
| `spec` | `write-spec` | `spec.md` | accepted `intent.md` | no |
| `plan` | `write-plan` | `plan.md` | accepted `intent.md` + settled `spec.md` | no |
| `impl` | `write-impl` | `impl.md` | accepted `plan.md` | no |
| `pr` | `write-pr` | `pr.md` | `impl.md` | no |
| `review` | `write-review` | `review.md` | `pr.md` | no |
| `ship` | `write-ship` | `ship.md` | `review.md` | no |

`idea` is the one optional stage, and it gates nothing. Its absence means only that nobody
wrote a note before the intent.

The eight rows above are a restatement of one array — `STAGES` in
`.claude/scripts/cos.mjs:25-34`. That array is the authoritative copy: it decides the
artifact names, the statuses each may carry, what the gate demands and what `status`
proposes next. Adding a stage is editing it, and then this table.

`cos-status` reads the tree and reports where every unit stands. It writes nothing.

Each stage ends by committing an artifact, and that commit is what starts the next stage.
The chain of commits is the audit trail of what was asked for and what the agent produced.
Who approved it is recorded by the merge, not by the commits — every commit in the chain is
the agent's.

## Work units

A unit of work lives in exactly one directory, `.cos/NNNN_<slug>/`, holding nothing but its
artifacts.

- `NNNN` is four digits, allocated by listing `.cos/`, taking the highest number present
  and adding one.
- `<slug>` is lowercase and hyphenated and contains no underscore, because the underscore
  is what separates the number from the slug.
- The slug names the problem rather than the solution, and is fixed at creation. A slug
  named after a solution stops making sense the moment the solution changes, which is
  exactly when the directory still has to be findable.

## The gate

Every artifact carries `Status: draft | accepted | rejected`. Two carry one more each:
`spec.md` may be `skipped`, and `plan.md` and `impl.md` may be `done`. The authoritative
list is the `statuses` field on each entry of `STAGES` in `.claude/scripts/cos.mjs:25-34`;
a status outside it is reported as a problem rather than guessed at.

The agent writes the artifact, accepts it and commits it. `Status: accepted` therefore
records readiness, not approval: it says the agent believes the file is finished, and
nothing more. A reader who takes it as a human's sign-off is reading it wrong.

There is no approval step anywhere. The author works alone, so the agent proposes,
accepts, implements and ships, and `accepted` is a word it wrote about its own work.
Since `0009` the work does at least travel through a branch and a pull request rather than
straight onto `main` — see `## Branches, tags and releases`. That changes the route, not
the reviewer: the pull request is opened, approved and merged by the same party.

What is left is not separation of duties but three weaker things: `cos.mjs gate` holding the
stages in order, the tests, and the author reading an artifact because they want to rather
than because anything stops them. That is a deliberate trade — speed for the only control
the repository had. Anyone copying this template should make that trade on purpose too,
rather than inheriting it.

## Spec skip

Small work goes straight from intent to plan. The five criteria that decide this, and the
rule that the human makes the call rather than the agent, live in
`.claude/skills/write-spec/SKILL.md`. That file is the authoritative copy — do not restate
the criteria elsewhere, because two copies drift and the drift is silent.

A skip still produces a `spec.md`, carrying `Status: skipped` and the reason. This is not
ceremony. A skip that left no file would be indistinguishable a month later from a spec
nobody got round to writing, and the gate ahead could not tell them apart either — it would
have to either block real skipped work or wave through work whose design was never
considered.

## The scripts

```
node .claude/scripts/cos.mjs status               # every unit and its one next action
node .claude/scripts/cos.mjs status --json        # the same, plus the stage list, for a reader
node .claude/scripts/cos.mjs gate <unit> <stage>  # 0 open, 1 blocked with reasons, 2 misuse
node .claude/scripts/cos.mjs new-path <slug>      # allocates number, validates slug
node .claude/scripts/cos.mjs --root <dir> status  # another repository's units, these rules
node --test '.claude/scripts/*.test.mjs'          # the script's own tests
```

`--root` exists for one reason and it is a boundary, not a convenience. The board in
`coscc/` reads a workspace's `.cos/` by pointing **its own** copy of this script at that
directory. It never executes the `cos.mjs` it finds there: a workspace is a repository
somebody cloned from a URL they typed, so the copy inside it is someone else's code, and
running it would hand that code everything the app process has.

`cos.mjs` is the only thing that decides whether a gate is open, which makes it an oracle
the rest of the harness defers to — so it is tested. The tests cover the cases where
being wrong would be quiet: a status line shadowed by prose later in the file, a file that
exists but carries no status, a skipped spec against a missing one, and numbering from the
highest existing unit rather than the count of them.

## Output language

Inside `.cos/`: filenames, directory names and headings in English; the prose under each
heading in Vietnamese. The structure stays greppable and machine-readable while the content
stays fast for the author to review, which matters because the author is the gate.

Everything else — `CLAUDE.md`, every `SKILL.md`, this file — is English throughout. Those
are instructions to the model, not artifacts to be reviewed.

## What is deliberately not built

- **An in-repo acceptance gate.** Until 2026-09-21 a skill wrote `draft` and only a human
  could type `accepted`; that edit was the whole separation of duties available here. It was
  removed on purpose — first in favour of review at the pull request, then, once the author
  confirmed they work alone and commit straight to `main`, in favour of nothing. Written
  down so a copy of this template is not read as never having had a gate: as it stands, no
  step anywhere checks the agent's work before it ships.
- **Hooks.** The gates are advisory by choice. Tightening one means adding a `PreToolUse`
  hook that blocks `Write`/`Edit` while `plan.md` is `draft` — one file, not a rewrite.
- **A review anyone has to pass.** `write-review` exists and `review.md` gates `ship`, but
  the reviewer is the same agent that wrote the code, and `accepted` is self-issued. A green
  `review` cell is a chair with nobody in it. That is recorded in
  `.cos/0005_hand-driven-invisible-loop/spec.md` C5 and it is the weakest joint in the loop.
- **Anything that starts the next stage on its own.** An accepted artifact does not light
  the gate after it. A person chooses the mode and presses the button, every time.
- **CI that decides anything.** `0009` added two workflows, and neither is a gate: one
  checks a branch name and runs the tests, the other builds a release from a tag. Nothing
  blocks a merge on them, and a green check still measures a different interpreter than the
  one the proofs were measured on.
- **The eval suite and `bands.yaml`** from the source material this harness was built from.
  That document is not in this repository and is not quoted here.

## Branches, tags and releases

Added by `0009`. The grammar lives here so a copy of `.claude/` carries it; **the thing that
enforces it does not travel with the harness**. Read that sentence twice before relying on
any of this in another repository — the only mechanism that actually stops a commit is a
GitHub ruleset, which is a setting on one repository and is in no file.

### Branch names

`<type>/<slug>`. Ten types, and the set is closed:

```
feat  fix  docs  refactor  test  chore  perf  build  ci  revert
```

The slug is lowercase letters, digits and single hyphens, up to 60 characters. `main` is
the trunk and is not a work branch. A work branch is cut from `main`, merged through a pull
request, and deleted.

Do not compose the name. Every unit declares `Type:` on its `intent.md` header, and the
branch follows from the unit:

```
node .claude/scripts/cos.mjs unit-branch 0009_branch-and-release-conventions
# feat/branch-and-release-conventions
node .claude/scripts/cos.mjs check-branch            # the branch you are on
node .claude/scripts/cos.mjs check-branch feat/thing # a name you are considering
```

`Type:` is required for units opened after `0009` and absent from the eight before it,
which were closed before the field existed and are not backfilled.

### Tags and releases

`vX.Y.Z` is a release. `vX.Y.Z-rc.N`, with `N` starting at 1, is a prerelease. Nothing else
is a tag this repository recognises, and leading zeros are refused so one release has one
spelling.

```
node .claude/scripts/cos.mjs check-tag v0.1.0-rc.1   # prints: prerelease
node .claude/scripts/cos.mjs check-tag v0.1.0        # prints: release
```

Pushing a tag is what builds the release. The workflow asks `check-tag` whether it is a
prerelease rather than comparing the string itself, so the grammar has one implementation
instead of one here and one in YAML.

### The version, and the five places that carry it

`pyproject.toml` is the source. `package.json`, `uv.lock` and the two keys in
`package-lock.json` are copies, and nothing makes them agree on its own:

```
node .claude/scripts/cos.mjs check-version
```

It names the source in the failure, because "they disagree" is not actionable until
something says which one is right. A tag on `HEAD` is compared too, when there is one.

### Which commands take `--root`

`--root <dir>` exists so this repository's rules can read **another** repository's `.cos/`
without running the copy of `cos.mjs` found over there. It therefore applies to the
commands that read a `.cos/` — `status`, `gate`, `new-path`, `unit-branch` — and is
**refused** by the three that report on the checkout this script lives in: `check-branch`,
`check-tag`, `check-version`. Given `--root`, those would quietly answer about here while
naming somewhere else.

### What does not come with the copy

| Where it is checked | What it cannot do |
|---|---|
| `cos.mjs`, locally | block anything — it returns an exit code and is run by choice |
| A workflow on GitHub | block a `git push` straight to `main`; no pull request, no workflow |
| A repository ruleset | know anything about the grammar or the version |

Copying `.claude/` brings the grammar, the four commands and their tests. It does **not**
bring `.github/workflows/`, which sits outside `.claude/`, and it does not bring the
ruleset, which is a setting rather than a file. A fresh copy of this harness therefore has
a convention that nothing enforces until somebody rebuilds those two legs by hand.

## Copying this into another repository

Copy `.claude/`. That is the whole harness — instructions, skills, scripts and this
reference — so nothing lands in the host repository's own tree and nothing collides with a
`scripts/` or `docs/` directory it already has.

Claude Code loads `.claude/CLAUDE.md` as project instructions the same way it loads a root
`CLAUDE.md`, so no import, symlink or root file is needed to make it take effect. Keep the
root free for whatever the repository itself wants there.

Then add that repository's real build, test and lint commands to `## Commands` in
`.claude/CLAUDE.md` — that section is what lets a session check its own work without
asking. Adjust the output language rule if the reviewer there reads English. Everything
else transfers unchanged.
