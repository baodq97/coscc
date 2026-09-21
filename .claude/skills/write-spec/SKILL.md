---
name: write-spec
description: Write the spec.md that turns an accepted intent into requirements and a design. Use once an intent.md in .cos/ has been accepted and the work unit has no spec yet — including when someone asks how a problem should be solved, what the requirements are, or asks to move an accepted intent towards implementation.
---

# Write a spec

A spec decides what the system must do and how it should be shaped, and it answers only to
the intent that authorized it. It is the last point where a decision is still a matter of
editing a document, so a concern raised here costs a paragraph and the same concern raised
later costs a rewrite.

## Before writing

Read `.cos/NNNN_<slug>/intent.md`. If its `Status` is not `accepted`, stop. Say which work
unit you read, what its status is, and that the intent must be accepted first. Do not write
the spec anyway, and do not change the status yourself.

Then run the skip assessment. Report all five criteria with a verdict on each:

1. The change touches two or fewer files that already exist.
2. No public interface, API contract, data schema or stored data changes.
3. No dependency is added.
4. No behaviour appears beyond what `intent.md` already states.
5. Nothing in auth, PII or the security surface is touched.

All five pass means the spec *may* be skipped. Say so and let the human decide — the
decision is theirs, not yours. If they skip it, write nothing; the plan will record
`Spec: skipped (<reason>)`. Any criterion failing means write the spec, and name the
criterion that forced it.

## Output

One file, `.cos/NNNN_<slug>/spec.md`, in the directory the intent already occupies. Do not
create a new work unit and do not renumber the existing one.

## Template

````markdown
# Spec: <title>
Intent: intent.md. Author: <name>. Status: draft.

## Requirements

## Design

## Out of scope

## Concerns

## Open questions
````

## Invariants

1. Every requirement traces back to the outcome stated in `intent.md`. A requirement that
   nothing in the intent authorizes is cut, not justified.
2. Requirements are testable. "Fast" is not a requirement; a number with a unit is.
3. Where two constraints contradict, name the contradiction under `## Concerns` and say
   which policy owner decides it. Never resolve it silently by picking a side — a
   contradiction that reaches the plan hidden reaches production hidden.
4. `## Design` describes the shape of the solution: the components, their boundaries, and
   the data that crosses them. It does not name the order of work or the files to edit;
   the plan decides those.
5. Carry the intent's open questions forward. Answer them, or restate them under
   `## Open questions` with what an answer would change. Silence loses them.
6. `## Out of scope` names what a reader would reasonably expect and will not get. An
   empty section here usually means the boundary was never thought about.
7. Every figure names its source or is marked unverifiable. Cite only a file committed in
   this repository, by path and line range.
8. Leave `Status: draft`. The human corrects it, accepts it and commits it.

## Done when

An engineer can plan against this file alone, and every concern that would have stopped
them later is already written down under `## Concerns`.

## Limit

This is advisory, and nothing forces a session to comply with it. These invariants hold
only until a deterministic check stands behind them.
