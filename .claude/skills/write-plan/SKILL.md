---
name: write-plan
description: Write the plan.md that turns an accepted spec into an implementation plan. Use once the spec for a work unit in .cos/ has been accepted, or once an accepted intent has had its spec deliberately skipped — including when someone asks how a change will be implemented, which files it touches, or asks to start building an accepted piece of work.
---

# Write a plan

A plan names the files that change, the order, what could break, and the command that decides
done. Accepting it authorizes the code.

## What you are given (trust it)

`intent.md`, `spec.md`, `spike.md` and the answers are in the prompt. Do not re-read them.
A spec question with a block under `## Answers` is decided: plan to it, cite it as
`spec.md ## Answers, câu N`, quote the person, never write into that section. `Answered by: Jera`
is an agent's inference: cite it as such.

Gate: from the board it was asked (the prompt says so); at a terminal run
`node .claude/scripts/cos.mjs gate <NNNN_slug> plan` first and stop on non-zero.

## Steps

1. One batched `Glob` (or one `ls`) of every existing path you mean to name; mark those that
   do not exist `(new)`. Do not read files you will not name.
2. Write `.cos/NNNN_<slug>/plan.md` from the template. An engineer who never saw this
   conversation must be able to implement from it alone.
3. Say what you chose not to do where a reader would assume it was overlooked.

````markdown
# Plan: <title>
Intent: intent.md. Spec: spec.md | skipped (<reason>). Author: <name>. Status: accepted. Impl: routine | novel.

## Files that change
- path (new)

## Order of work
1. <a step that leaves the repository checkable>

## Risks
<what breaks and what would show it, by blast radius>

## Proof
<`npm test` with tests named for the behaviour, or `npm run e2e` with a named case, and the
result that counts as passing. A baseline or outcome to measure is not impl's work: name it
under `## Risks`; the app or Leif measures it>
````

## Lines the app and `cos.mjs` read

- `Impl: novel` for new logic, or when `## Files that change` names a `SECURITY_SURFACE` file
  of `coscc/agent/labels.py`; otherwise `routine`. A missing label runs as `novel`.
- `## Files that change` is one path or glob per bullet, no prose.
- When the spec had `[unmeasured] U<n>` items, every step resting on one cites
  `spike.md ## U<n>`; the `impl` gate is closed on a plan that never names `spike.md`. Never
  write "measure X first, stop if not" for such a question: a new one goes back to the spec.
- `Status: done` only after the shipped work passed `## Proof`; never to close a unit with
  stages left. "Verify manually" is not proof.

## Done when

Someone with no conversation can do the work and tell from `## Proof` whether they finished.
