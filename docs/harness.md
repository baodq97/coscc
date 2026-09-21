# The harness

Reference for how this repository runs a unit of work. `CLAUDE.md` carries only the rules
that must hold in every session; everything explaining *why* lives here, and everything
that applies to one stage lives in that stage's skill.

Read this when setting the harness up, changing it, or copying it into another repository.
A session in the middle of a stage does not need it.

## What this is

A local implementation of the AI-native SDLC described in `ai-native-sdlc-playbook.md`,
covering the first three stages: Plan, Design, Build. It is also the template — copy
`CLAUDE.md` and `.claude/` into another repository and the loop works there.

It is driven by hand. There are no hooks, no CI and no scheduled jobs, which means every
rule is advisory: it holds because the session read it, not because anything blocked the
action. Each skill states this limit on itself.

## The loop

| Stage | Skill | Artifact | Reads |
|---|---|---|---|
| Plan | `write-intent` | `intent.md` | nothing |
| Design | `write-spec` | `spec.md` | accepted `intent.md` |
| Build | `write-plan` | `plan.md` | accepted `intent.md` + accepted `spec.md` |

`cos-status` reads the tree and reports where every unit stands. It writes nothing.

Each stage ends by committing an artifact, and that commit is what starts the next stage.
The chain of commits is the audit trail: what was asked for, what the agent produced, and
who approved it.

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

A skill writes `draft` and leaves it there. The human edits it to `accepted` and commits.
That edit is the approval and the commit is the record of it.

This is the whole separation of duties available to a solo developer. There is no reviewer
and no branch protection, so the only thing standing between a proposal and its
authorization is that a human, not the agent, types the word. An agent that sets its own
gate has removed the gate, which is why that one rule sits in `CLAUDE.md` rather than here.

## Spec skip

Small work goes straight from intent to plan. The five criteria that decide this, and the
rule that the human makes the call rather than the agent, live in
`.claude/skills/write-spec/SKILL.md`. That file is the authoritative copy — do not restate
the criteria elsewhere, because two copies drift and the drift is silent.

## Output language

Inside `.cos/`: filenames, directory names and headings in English; the prose under each
heading in Vietnamese. The structure stays greppable and machine-readable while the content
stays fast for the author to review, which matters because the author is the gate.

Everything else — `CLAUDE.md`, every `SKILL.md`, this file — is English throughout. Those
are instructions to the model, not artifacts to be reviewed.

## What is deliberately not built

- **Hooks.** The gates are advisory by choice. Tightening one means adding a `PreToolUse`
  hook that blocks `Write`/`Edit` while `plan.md` is `draft` — one file, not a rewrite.
- **Stages 4 to 6** (Test, Deploy, Maintain), and with them `REVIEW.md`, the eval suite,
  CI integration and `bands.yaml`.
- **A feedback loop.** There is no application code, so `make test` has nothing to run. A
  verifier subagent is only worth writing once a real command can fail.

## Copying this into another repository

Take `CLAUDE.md` and `.claude/skills/`. Fill in `## Commands` in `CLAUDE.md` with that
repository's real build, test and lint commands — that section is what lets a session check
its own work without asking. Adjust the output language rule if the reviewer there reads
English. Everything else transfers unchanged.
