---
name: write-spec
description: Write the spec.md that turns an accepted intent into requirements and a design. Use once an intent.md in .cos/ has been accepted and the work unit has no spec yet — including when someone asks how a problem should be solved, what the requirements are, or asks to move an accepted intent towards implementation.
---

# Write a spec

A spec decides what the system must do and its shape; a concern raised here costs a paragraph,
later a rewrite.

## What you are given (trust it)

`intent.md`, the answers, and any prior `spec.md` or `spike.md` are in the prompt. Do not
re-read or re-verify them. A question with a block under `## Answers` is decided: cite it as
`<artifact> ## Answers, câu N`, quote the person, never re-ask, never write into that section.
`Answered by: Jera` is an agent's inference from precedent: cite it as such.

Gate: from the board it was asked (the prompt says so); at a terminal run
`node .claude/scripts/cos.mjs gate <NNNN_slug> spec` first and stop on non-zero.

## Steps

1. Write `.cos/NNNN_<slug>/spec.md` from the template.
2. Every requirement traces to the intent's outcome and is testable (a number with a unit).
   `## Design` names components, boundaries and data crossing them, not files or order of work.
3. Two contradicting constraints: name it under `## Concerns` and who decides. Never pick silently.
4. A doubt you cannot measure from here (does an SDK, CLI or process behave as assumed) is an
   `[unmeasured]` item, not a guess.
5. A UI unit (spec changes a path under `paths:` in `.claude/rules/ui-standard.md`): list in
   `## Design` each screen (at most six) as an app address with the `S<n>` rules that apply,
   on fixture workspace `proj`, units `0001_fresh-intent`, `0002_open-question`,
   `0003_awaiting-ship`, `0004_finished`, `0005_unfinished-review`; say when a screen has no address.

````markdown
# Spec: <title>
Intent: intent.md. Author: <name>. Status: accepted.

## Requirements

## Design

## Out of scope
<what a reader would expect and will not get>

## Concerns

## Open questions
````

## Lines the app and `cos.mjs` read

- `- [unmeasured] U1. <question>` at column 0 under `## Concerns`; list the ids in `unmeasured`
  of `submit`. It sends the unit to `spike`. The id is the question's identity: keep it across
  rewrites, never reuse or duplicate one. After a spike `fails`, drop that id and every
  requirement resting on it; if no direction holds, write `Status: draft` with the question.
- `## Open questions`: a real question is an item `N. ` at column 0 whose first paragraph holds
  a `?`, and the same go into `questions` of `submit`. Anything else is a plain sentence. Keep
  the heading even when empty.
- `Status: accepted` is your judgement, not approval; `draft` stops the loop.

## Skip

Only when proposing a skip: report each of five criteria: (1) at most two existing files
touched, (2) no public interface, schema or stored data changes, (3) no dependency added,
(4) no behaviour beyond the intent, (5) nothing in auth, PII or security. All pass means the
spec *may* be skipped; the decision is a person's. Write `spec.md` with `Status: skipped`,
`## Why skipped` and the assessment, and submit `not-ready`: only `coscc skip` makes it a
person's.

## Done when

An engineer can plan from this file alone and every doubt is written under `## Concerns`.
