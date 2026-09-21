---
name: write-plan
description: Write the plan.md that turns an accepted spec into an implementation plan. Use once the spec for a work unit in .cos/ has been accepted, or once an accepted intent has had its spec deliberately skipped — including when someone asks how a change will be implemented, which files it touches, or asks to start building an accepted piece of work.
---

# Write a plan

A plan names the files that change, the order they change in, what could break, and the
command that decides whether the work is done. It is written before any code, and accepting
it is what authorizes the code.

## Before writing

```
node .claude/scripts/cos.mjs gate <NNNN_slug> plan
```

Exit 0 means proceed. Anything else means stop and report what it printed. The gate clears
on an accepted spec or on one marked `skipped`; a missing `spec.md` does not clear it,
because a spec nobody wrote looks exactly like a spec someone decided to skip. Never change
a status yourself to unblock your own work.

Then read `intent.md` and `spec.md` in full — the gate checks that they are settled, not
what they say.

Read the files the plan will touch before naming them. A plan built from a guess at what
the code looks like is a plan the first step invalidates.

## Output

One file, `.cos/NNNN_<slug>/plan.md`, in the directory the intent already occupies.

## Template

````markdown
# Plan: <title>
Intent: intent.md. Spec: spec.md | skipped (<reason>). Author: <name>. Status: accepted.

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
7. No code until `Status: accepted`, and accept it in its own commit. The plan authorizes
   the implementation, so it cannot be written alongside it — a plan and its code in one
   commit is a plan written backwards from the code.
8. When the implementation departs from the plan, update `plan.md` in the same commit as
   the departure. The plan is the record of what was built, not of what was first imagined.
9. Accept it and commit it. `accepted` means you judged it finished, and nothing checks that
   judgement — so `## Proof` is the only thing that can contradict you. Set `done` only when
   the work has shipped and that command has passed.

## Done when

The work could be handed to someone else with no conversation attached, and you could tell
from the proof alone whether they finished it.

## Next

Implementation, once `gate <NNNN_slug> implement` exits 0 — which it does only after this
file is accepted and committed.

Work the steps in the order written, run the command under `## Proof`, and set
`Status: done` only after that command has passed.

If the implementation departs from the plan, update `plan.md` in the same commit. Do not
start on a draft — an unaccepted plan authorizes nothing.

## Limit

The gate is decided by a script, so the answer does not vary with how carefully a session
reads. What stays advisory is the invocation: nothing forces a session to run it, or to
stop when it exits non-zero. A `PreToolUse` hook would.

The invariants above are advisory throughout — they describe judgement, and no script
checks them.
