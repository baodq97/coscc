---
name: write-plan
description: Write the plan.md that turns an accepted spec into an implementation plan. Use once the spec for a work unit in .cos/ has been accepted, or once an accepted intent has had its spec deliberately skipped — including when someone asks how a change will be implemented, which files it touches, or asks to start building an accepted piece of work.
---

# Write a plan

A plan orders the work, says what could break and how done is checked; its record names the
files, the label and what can run in parallel. Accepting it authorizes the code.

## Where it runs

In plan mode, in a session with a person, in this checkout: no worktree, no edits until the
plan is approved, no command that builds, tests or runs the app. From the board the inputs and
the gate are in the prompt; hand the plan back as the prompt says instead of `ExitPlanMode`.

## What you are given (trust it)

`intent.md`, `spec.md`, `spike.md`, the plan as it stands and the answers are in the prompt,
each under its own heading: do not Read them. An answered spec question is decided: plan to it,
cite it as `spec.md câu N`. Do not check the spec again.

## Reading the tree

- One batched `Glob` (or one `ls`) of every path you mean to name; mark missing ones `(new)`.
- Read only files you will name, and only the part the change touches: `Grep` for the symbol,
  then `Read` with an offset. `git log` / `git show` when history decides a choice.
- Run no test: `## Proof` and `## Verification` say what impl runs.

## Steps

1. Read the inputs, then the tree as above.
2. Write the plan from the template, at most 4 KB. At most 10 paths per step, not per unit: the
   repository's shared files come first under `## Order of work`, in no step; the new work's
   own files split into the record's `steps` on disjoint paths, which run at the same time.
   Only past about 30 paths in all is the unit too big: say how to split it and stop. Most new
   work is its own files, one registration line and its test.
3. `## Risks` answers what could break, which step is riskiest and which option you rejected.
4. Present it with `ExitPlanMode`; revise until the person approves.
5. Write it to the unit's `plan.md`, and nothing else.

````markdown
# Plan: <title>
Intent: intent.md. Spec: spec.md. Author: <name>.

## Order of work
1. <a step that leaves the repository checkable; cite the spec's requirement, do not restate it>

## Risks
<what breaks and what would show it, by blast radius; the riskiest step; the option rejected and why>

## Proof
<the tests named for the behaviour and the result that counts as passing. A baseline or outcome
to measure is not impl's work: name it under `## Risks`>

## Verification
<the commands impl runs at the end, and their healthy output: lint, `tests/test_*.py` and the
tests of the modules that change, never the whole suite, which CI runs>
````

## What `submit` carries

- `variant`: `novel` for new logic or a security-sensitive file, otherwise `routine`.
- `files`: every path the unit edits or creates, one per item; mark none `(new)` here.
- `steps`: each parallel step, `{title, paths, report}`; its `paths` are files of `files` no other
  step names, and impl starts one helper per step. `[]` for one session.
- `rests_on`: each `U<n>` of the spec a step rests on; the `impl` gate is closed while the spec
  has `[unmeasured]` items and this is empty. Never write "measure X first, stop if not" for
  such a question: a new one goes back to the spec.
- "Verify manually" is not proof.

## Done when

Someone with no conversation can do the work and tell from `## Proof` whether they finished.
