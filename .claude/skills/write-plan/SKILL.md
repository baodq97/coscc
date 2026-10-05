---
name: write-plan
description: Write the plan.md that turns an accepted spec into an implementation plan. Use once the spec for a work unit in .cos/ has been accepted, or once an accepted intent has had its spec deliberately skipped — including when someone asks how a change will be implemented, which files it touches, or asks to start building an accepted piece of work.
---

# Write a plan

A plan names the files that change, the order, what could break, how done is checked and what
can run in parallel. Accepting it authorizes the code.

## Where it runs

In plan mode, in a session with a person, in this checkout: no worktree, no edits until the
plan is approved, no command that builds, tests or runs the app. From the board the inputs and
the gate are in the prompt; hand the plan back as the prompt says instead of `ExitPlanMode`.

## What you are given (trust it)

`intent.md`, `spec.md`, `spike.md` and their answers: read each once (from the board they are
in the prompt). A spec question with a block under `## Answers` is decided: plan to it, cite
it as `spec.md ## Answers, câu N`. Do not check the spec again.

## Reading the tree

- One batched `Glob` (or one `ls`) of every path you mean to name; mark missing ones `(new)`.
- Read only files you will name, and only the part the change touches: `Grep` for the symbol,
  then `Read` with an offset. `git log` / `git show` when history decides a choice.
- Run no test: `## Proof` and `## Verification` say what impl runs.

## Steps

1. Read the inputs, then the tree as above.
2. Write the plan from the template, at most 4 KB. At most 10 paths per step, not per unit: the
   repository's shared files are one step under `## Order of work`, done first; the new work's
   own files split into steps on disjoint paths under `## Parallelization`, which run at the
   same time. Only past about 30 paths in all is the unit too big: say how to split it and stop.
   Most new work is its own files, one registration line and its test.
3. `## Risks` answers what could break, which step is riskiest and which option you rejected.
4. Present it with `ExitPlanMode`; revise until the person approves.
5. Write it to the unit's `plan.md`, and nothing else.

````markdown
# Plan: <title>
Intent: intent.md. Spec: spec.md | skipped (<reason>). Author: <name>. Status: accepted. Impl: routine | novel.

## Files that change
- path (new)

## Order of work
1. <a step that leaves the repository checkable; cite the spec's requirement, do not restate it>

## Risks
<what breaks and what would show it, by blast radius; the riskiest step; the option rejected and why>

## Proof
<the tests named for the behaviour and the result that counts as passing. A baseline or outcome
to measure is not impl's work: name it under `## Risks`>

## Verification
<the commands impl runs at the end, and their healthy output: lint and the tests of the modules
that change, never the whole suite, which CI runs>

## Parallelization
(a) <title>
- <path or glob, one per bullet, none shared with another step>
Report: <what the step reports when done>

(b) <title>
- <path>
Report: <…>
````

Or, under the heading, the one line `none: one session`.

## Lines the app and the loop read

- `Impl: novel` for new logic or a security-sensitive file; otherwise `routine`. A missing label
  runs as `novel`.
- `## Files that change` is one path or glob per bullet, no prose.
- `## Parallelization` is `(a) <title>`, then its path bullets, then its report, per step: impl
  starts one helper per step from it, so a step's paths are its only files.
- When the spec had `[unmeasured] U<n>` items, every step resting on one cites
  `spike.md ## U<n>`; the `impl` gate is closed on a plan that never names `spike.md`. Never
  write "measure X first, stop if not" for such a question: a new one goes back to the spec.
- `Status: done` only after the shipped work passed `## Proof`; never to close a unit with
  stages left. "Verify manually" is not proof.

## Done when

Someone with no conversation can do the work and tell from `## Proof` whether they finished.
