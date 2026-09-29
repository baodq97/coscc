---
name: write-spike
description: Write the spike.md that measures the questions an accepted spec marked [unmeasured], before any plan is written against them. Use once spec.md for a work unit in .cos/ is accepted and carries items that open "[unmeasured] U<n>" under ## Concerns — including when someone asks whether an SDK, CLI or process behaves the way the spec assumes.
---

# Write a spike

Signed ᛈ Perthro. Answer each `U<n>` the spec could not, by running something. Measure only:
build nothing to keep, never edit the spec.

## What you are given (trust it)

`spec.md` and any previous `spike.md` are in the prompt; every `U<n>` under its `## Concerns`
is one question to answer. Do not re-read them.

Gate: from the board it was asked (the prompt says so); at a terminal run
`node .claude/scripts/cos.mjs gate <NNNN_slug> spike` first and stop on non-zero.

## Where the probe code goes

Outside the checkout: on the board your working directory is a throwaway; at a terminal
`mktemp -d`. Never run `git`, never write into the worktree, `.cos/` or the unit's store; if
the worktree's `HEAD` or `git status --porcelain` changed, the step fails.

## The progress file

Keeps what you measured if the step runs out of turns or budget.

1. Your first `Write`, before any probe, creates `spike.md` in your working directory with the
   whole header and one `## U<n>` per `U<n>` the spec carries.
2. An unmeasured question holds prose only, no line starting `Verdict:`.
3. The moment a `U<n>` is measured, write its `Verdict:` line and fenced block, before the next.
   Never write `Verdict: holds.` for a partial measurement.
4. On a rerun, a previous `## U<n>` that is complete (`Verdict: holds.` plus a fenced block) is
   copied verbatim and not measured again; measure only the `U<n>` `cos.mjs` reports missing.
5. If the step ends with no usable reply, the app writes `spike.md` from this file.

## Output

````markdown
# Spike: <title>
Spec: spec.md. Author: ᛈ Perthro. Round: <N>. Status: accepted.

## U1

<what was asked, what was run, what it shows>

Verdict: holds.

```
$ <the command, exactly as run>
<what it printed, verbatim; trimmed only at a marked cut>
```
````

## Lines `cos.mjs` reads

- One `## U<n>` per id the spec carries now, headed exactly so; `verdicts` of `submit` holds
  one `{id, verdict}` for each.
- Each has a line `Verdict: holds.` or `Verdict: fails.` and at least one fenced block with a
  command that was run and what it printed. No block means missing. `holds`: the assumption
  stood. `fails`: it did not, or could not be measured (say why).
- `Round:` is 1, or the previous spike's round plus one when it had a `fails`; it stops the
  loop for a person at a second failing round.
- Every figure names the command that printed it; never answer a question the spec did not ask.

## Done when

A planner can cite `spike.md ## U<n>` for every step resting on a question, and each block reruns
to the same result.
