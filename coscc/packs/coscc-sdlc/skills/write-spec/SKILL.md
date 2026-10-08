---
name: write-spec
description: Write the spec.md that turns an accepted intent into requirements and a design. Use once an intent.md in .cos/ has been accepted and the work unit has no spec yet — including when someone asks how a problem should be solved, what the requirements are, or asks to move an accepted intent towards implementation.
---

# Write a spec

A spec decides what the system must do and its shape; a concern raised here costs a paragraph,
later a rewrite.

## What you are given (trust it)

`intent.md`, any `spike.md`, the spec as it stands and the answers a person gave are in the
prompt, each under its own heading: do not Read them or re-verify them. An answered question is
decided: cite it as `<artifact> câu N`, quote the person, never re-ask, never copy it into the
file.

Gate: from the board it was asked (the prompt says so); at a terminal ask
`uv run python -m coscc.loop gate <unit> spec` first and stop on non-zero.

## Steps

1. Write `.cos/NNNN_<slug>/spec.md` from the template.
2. Every requirement traces to the intent's outcome and is testable (a number with a unit).
   `## Design` names components, boundaries and data crossing them, not files or order of work,
   and where the new work plugs in (the extension points the repository's CLAUDE.md names). A
   need none of them serves is a change to the core: name it apart. A requirement that changes a
   rule states it as an invariant on the outcome, "every path to <outcome> <rule>", never on one
   function or branch, so review scores it on every path to the same outcome.
3. Two contradicting constraints: a numbered item under `## Open questions`, its recommended
   answer in `recommendation` of `submit`, which the board can take; `## Concerns` holds no
   decision. Never pick silently.
4. A doubt you cannot measure from here (does an SDK, CLI or process behave as assumed) is an
   `[unmeasured]` item, not a guess.
5. On a unit that changes a screen, the UI standard rule's part for this stage applies.

````markdown
# Spec: <title>
Intent: intent.md. Author: <name>.

## Requirements

## Design

## Out of scope
<what a reader would expect and will not get>

## Concerns

## Open questions
````

## What `submit` carries

- `- [unmeasured] U1. <question>` at column 0 under `## Concerns`, and its id in `unmeasured`
  of `submit`: a non-empty `unmeasured` sends the unit to `spike`, the line alone does not.
  The id is the question's identity: keep it across rewrites, never reuse or duplicate one.
  After a spike `fails`, drop that id and every requirement resting on it; if no direction holds, submit `not-ready` with the question.
  When a `spike.md` is handed to you, rewrite the spec on its results; a new question takes a new `U<n>`.
- `## Open questions`: a real question is an item `N. ` at column 0 whose first paragraph holds
  a `?`, and the same go into `questions` of `submit`: `text` only asks, `recommendation` is the
  answer you recommend and why. Anything else is a plain sentence. Keep the heading even when
  empty.
- The file carries no status. `judgement` of `submit` is yours, not approval; `not-ready`
  stops the loop.

## Skip

Only when proposing a skip: report each of five criteria: (1) at most two existing files
touched, (2) no public interface, schema or stored data changes, (3) no dependency added,
(4) no behaviour beyond the intent, (5) nothing in auth, PII or security. All pass means the
spec *may* be skipped; the decision is a person's. Write the assessment under
`## Why skipped`, submit `not-ready` with one question asking a person to skip (`coscc skip`).

## Done when

An engineer can plan from this file alone and every doubt is written under `## Concerns`.
