---
name: write-plan
description: Write the plan.md that turns an accepted spec into an implementation plan. Use once the spec for a work unit in .cos/ has been accepted, or once an accepted intent has had its spec deliberately skipped — including when someone asks how a change will be implemented, which files it touches, or asks to start building an accepted piece of work.
---

# Write a plan

A plan names the files that change, the order they change in, what could break, and the
command that decides whether the work is done. It is written before any code, and accepting
it is what authorizes the code.

## Before writing

Read `.cos/NNNN_<slug>/intent.md` and `.cos/NNNN_<slug>/spec.md`.

- The intent must be `Status: accepted`. If it is not, stop and say so.
- The spec must be `Status: accepted`, unless the human deliberately skipped it. If the
  spec exists but is still `draft`, stop. If no spec exists, ask whether it was skipped and
  why, then record that reason in the plan. Do not assume a missing spec means it was
  skipped — it more often means nobody has written it yet.
- Never change a status yourself to unblock your own work.

Read the files the plan will touch before naming them. A plan built from a guess at what
the code looks like is a plan the first step invalidates.

## Output

One file, `.cos/NNNN_<slug>/plan.md`, in the directory the intent already occupies.

## Template

````markdown
# Plan: <title>
Intent: intent.md. Spec: spec.md | skipped (<reason>). Author: <name>. Status: draft.

## Files that change

## Order of work

## Risks

## Proof
````

## Invariants

1. Every path under `## Files that change` is real. A file that does not exist yet is
   marked `(new)`. Verify each existing path before writing it down.
2. `## Order of work` is a numbered sequence where each step leaves the repository in a
   state someone can check. A step that only makes sense once the next one lands is two
   steps written as one.
3. `## Risks` names what could break and what would show it breaking, ordered by blast
   radius. Include the risk you would rather not write down; it is usually the real one.
4. `## Proof` is a command whose output decides pass or fail, plus the result that counts
   as passing. "Verify manually" is not proof. If no such command exists yet, the first
   step of the plan is to create one.
5. State what you chose not to do and why, where a reader would otherwise assume it was
   overlooked.
6. An engineer who never saw this conversation implements the change from this file alone.
   That is the bar; if the plan needs you present to be understood, it is not finished.
7. No code until `Status: accepted`. The plan authorizes the implementation, so it cannot
   be written alongside it.
8. When the implementation departs from the plan, update `plan.md` in the same commit as
   the departure. The plan is the record of what was built, not of what was first imagined.
9. Leave `Status: draft`. The human corrects it, accepts it and commits it. Set `done` only
   when the work has shipped and the proof has run.

## Done when

The work could be handed to someone else with no conversation attached, and you could tell
from the proof alone whether they finished it.

## Limit

This is advisory, and nothing forces a session to comply with it. These invariants hold
only until a deterministic check stands behind them.
