# cos-baodo

A harness for running an AI-native SDLC locally. It is also the template: copy `.claude/`
and this file into another repository to get the same loop there.

Everything is driven by hand through skills. There are no hooks, no CI and no scheduled
jobs, so every rule below is advisory — it holds because the session reads this file, not
because something blocks the action.

## Commands

None yet. This repository holds no application code. When code arrives, list the build,
test and lint commands here with an example of healthy output, because that is what lets a
session check its own work without asking.

## The loop

A unit of work moves through three artifacts. Each one is written by a skill, corrected and
accepted by the human, and committed before the next begins.

| Stage | Skill | Artifact |
|---|---|---|
| Plan | `write-intent` | `intent.md` |
| Design | `write-spec` | `spec.md` (skippable, see below) |
| Build | `write-plan` | `plan.md` |

`cos-status` reads the whole tree and reports where every unit stands. It changes nothing.

## Work units

A unit of work lives in exactly one directory, `.cos/NNNN_<slug>/`, and holds nothing but
its artifacts.

- `NNNN` is four digits. Allocate it by listing `.cos/`, taking the highest number present
  and adding one. Never guess the number.
- `<slug>` is lowercase, hyphenated, and contains no underscore — the underscore separates
  the number from the slug.
- The slug names the problem, not the solution, and is fixed at creation.

## The gate

Every artifact carries a status line: `Status: draft | accepted | rejected`. `plan.md` may
additionally be `done`.

A skill writes `draft` and leaves it there. The human edits it to `accepted` and commits.
That edit is the approval, and the commit is the record of it.

Before writing any artifact, read the status of the one before it. If that status is not
`accepted`, stop and say what is missing. Do not proceed on a draft.

Never set `accepted` yourself. Setting it is the one thing in this loop reserved for the
human, and an agent that sets its own gate has removed the gate.

## When the spec can be skipped

Skip `spec.md` only when every one of these is true:

1. The change touches two or fewer files that already exist.
2. No public interface, API contract, data schema or stored data changes.
3. No dependency is added.
4. No behaviour appears beyond what `intent.md` already states.
5. Nothing in auth, PII or the security surface is touched.

One failure means write the spec. Present the assessment criterion by criterion and let the
human decide; never skip silently. When skipped, `plan.md` records
`Spec: skipped (<reason>)`.

## Output language

Inside `.cos/`: filenames, directory names and every heading are English; the prose under
each heading is Vietnamese. The structure stays machine-readable and greppable while the
content stays fast for the author to review.

Everything outside `.cos/` — this file, every `SKILL.md` — is English throughout. Those are
instructions to the model, not artifacts to review.

## Things Claude gets wrong

- Do not set `Status: accepted`. Write `draft` and hand it back.
- Do not write code while `plan.md` is `draft`. An unaccepted plan authorizes nothing.
- Do not put anything in a work unit directory except `intent.md`, `spec.md` and `plan.md`.
- Do not guess the next work unit number. List `.cos/` first.
- Do not soften a figure that has no source. Cut it.
