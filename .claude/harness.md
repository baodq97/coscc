# The harness

Reference for how this repository runs a unit of work. `.claude/CLAUDE.md` carries only
the rules that must hold in every session; everything explaining *why* lives here, and
everything that applies to one stage lives in that stage's skill.

Read this when setting the harness up, changing it, or copying it into another repository.
A session in the middle of a stage does not need it.

## What this is

A local implementation of the AI-native SDLC described in `ai-native-sdlc-playbook.md`,
covering the first three stages: Plan, Design, Build. It is also the template — copy
`.claude/` into another repository and the loop works there.

It is driven by hand. There are no hooks, no CI and no scheduled jobs.

What is mechanical lives in `.claude/scripts/cos.mjs`; what is judgement lives in the skills.
The split matters for how much each can be trusted. Whether an intent is accepted is a fact
the script reads off disk the same way every time. Whether a spec is any good is not, and no
script pretends otherwise.

Even the mechanical half stays advisory in one respect: nothing forces a session to run the
script, or to stop when it exits non-zero. A `PreToolUse` hook would. Until then the gate
is a question the session is asked to ask, and each skill states that limit on itself.

## The loop

| Stage | Skill | Artifact | Reads |
|---|---|---|---|
| Plan | `write-intent` | `intent.md` | nothing |
| Design | `write-spec` | `spec.md` | accepted `intent.md` |
| Build | `write-plan` | `plan.md` | accepted `intent.md` + accepted `spec.md` |

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

Every artifact carries `Status: draft | accepted | rejected`; `plan.md` may additionally be
`done`.

The agent writes the artifact, accepts it and commits it. `Status: accepted` therefore
records readiness, not approval: it says the agent believes the file is finished, and
nothing more. A reader who takes it as a human's sign-off is reading it wrong.

Approval lives at the pull request instead. That is now the only point where a human stands
between a proposal and the thing it authorizes, which makes two rules load-bearing rather
than stylistic: nothing is committed to `main`, and no branch is merged without being read.
Drop either and there is no human left anywhere in the loop — the agent proposes, approves,
implements and ships, and `accepted` is a word it wrote about its own work.

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
node .claude/scripts/cos.mjs gate <unit> <stage>  # 0 open, 1 blocked with reasons, 2 misuse
node .claude/scripts/cos.mjs new-path <slug>      # allocates number, validates slug
node --test '.claude/scripts/*.test.mjs'          # the script's own tests
```

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
  removed on purpose, on the grounds that review happens at the pull request instead. The
  cost is written down so a copy of this template is not read as never having had one: if
  the PR step is skipped, or a branch is merged unread, nothing anywhere checks the agent.
- **Hooks.** The gates are advisory by choice. Tightening one means adding a `PreToolUse`
  hook that blocks `Write`/`Edit` while `plan.md` is `draft` — one file, not a rewrite.
- **Stages 4 to 6** (Test, Deploy, Maintain), and with them `REVIEW.md`, the eval suite,
  CI integration and `bands.yaml`.
- **A feedback loop.** There is no application code, so `make test` has nothing to run. A
  verifier subagent is only worth writing once a real command can fail.

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
