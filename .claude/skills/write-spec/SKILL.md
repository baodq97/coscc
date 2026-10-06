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

Gate: from the board it was asked (the prompt says so); at a terminal ask
`uv run python -m coscc.loop gate <unit> spec` first and stop on non-zero.

## Steps

1. Write `.cos/NNNN_<slug>/spec.md` from the template.
2. Every requirement traces to the intent's outcome and is testable (a number with a unit).
   `## Design` names components, boundaries and data crossing them, not files or order of work,
   and where the new work plugs in (the extension points the repository's CLAUDE.md names). A
   need none of them serves is a change to the core: name it apart.
3. Two contradicting constraints: a numbered item under `## Open questions` with your
   recommendation, which the board can answer; `## Concerns` holds no decision. Never pick silently.
4. A doubt you cannot measure from here (does an SDK, CLI or process behave as assumed) is an
   `[unmeasured]` item, not a guess.
5. On a unit that changes a screen, the UI standard rule's part for this stage applies.

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

## Lines the app and the loop read

- `- [unmeasured] U1. <question>` at column 0 under `## Concerns`, and its id in `unmeasured`
  of `submit`: a non-empty `unmeasured` sends the unit to `spike`, the line alone does not.
  The id is the question's identity: keep it across rewrites, never reuse or duplicate one.
  After a spike `fails`, drop that id and every requirement resting on it; if no direction holds, write `Status: draft` with the question.
- `## Open questions`: a real question is an item `N. ` at column 0 whose first paragraph holds
  a `?`, and the same go into `questions` of `submit`. Anything else is a plain sentence. Keep
  the heading even when empty.
- `Status: accepted` is your judgement, not approval; `draft` stops the loop.

## Skip

Only when proposing a skip: report each of five criteria: (1) at most two existing files
touched, (2) no public interface, schema or stored data changes, (3) no dependency added,
(4) no behaviour beyond the intent, (5) nothing in auth, PII or security. All pass means the
spec *may* be skipped; the decision is a person's. Write `spec.md` with `Status: skipped`,
`## Why skipped` and the assessment, and submit `not-ready`.

## Done when

An engineer can plan from this file alone and every doubt is written under `## Concerns`.
