---
name: write-spike
description: Write the spike.md that measures the questions an accepted spec marked [unmeasured], before any plan is written against them. Use once spec.md for a work unit in .cos/ is accepted and carries items that open "[unmeasured] U<n>" under ## Concerns — including when someone asks whether an SDK, CLI or process behaves the way the spec assumes.
---

# Write a spike

Signed ᛈ Perthro. A spike answers the questions the spec could not, by running something,
so that `plan` orders work on measurements rather than on guesses and `impl` never spends
its turns finding out whether the foundation stands.

It only measures. It builds nothing the unit will keep, and it never edits the spec — a
question that does not hold sends the unit back to `spec`, which rewrites itself on what
this file measured.

## Before writing

```
node .claude/scripts/cos.mjs gate <NNNN_slug> spike
```

Exit 0 means proceed. The gate is open only when `spec.md` is accepted and carries at
least one `[unmeasured] U<n>` item (or a `spike.md` already exists). A spec with none
never runs this stage.

Read `spec.md`. Every `U<n>` under its `## Concerns` is one question this file must answer.
An id this spike measured before that the spec no longer carries is not asked again.

## Where the probe code goes

Outside the checkout, always. On the board the step's working directory is a throwaway
directory the app deletes when the step ends, and the unit's worktree is read-only; if its
`HEAD` or `git status --porcelain` changed, the step fails and no `spike.md` is written.
At a terminal, make one: `mktemp -d`, and work there.

Never run `git`. Never write into the worktree, into `.cos/`, or into the unit's store.

## Output

One file, `spike.md`, in the unit's directory. On the board the app writes it from your
reply.

````markdown
# Spike: <title>
Spec: spec.md. Author: ᛈ Perthro. Round: <N>. Status: accepted.

## U1

<What was asked, what was run, and what it shows.>

Verdict: holds.

```
$ <the command, exactly as run>
<what it printed, verbatim — trimmed only at a marked cut>
```

## U2

...
````

## Invariants

1. One `## U<n>` for every `U<n>` the spec carries now, headed exactly so.
2. Each carries one line `Verdict: holds.` or `Verdict: fails.` — nothing else on it —
   and at least one fenced block holding a command that was run and what it printed. A
   verdict with no block is not a measurement, and the gate reads it as missing.
3. `holds` means the spec's assumption stood when measured. `fails` means it did not, and
   the plan must not be written on it. A measurement that could not be taken is not
   `holds`: say why, and write `fails`.
4. `Round:` is 1 the first time. When the `spike.md` before this one had a `fails`, it is
   that round plus one. `cos.mjs` stops the loop for a person when a question still fails
   at round 2 (`SPIKE_ROUNDS`); writing 1 every time would hide that, so do not.
5. Every figure names the command that printed it. Cut a figure that has no source.
6. Never edit `spec.md`, and never answer a question the spec did not ask.

## Done when

A planner can cite `spike.md ## U<n>` for every step that rests on a question the spec
could not answer, and a reader can rerun each block and get the same result.

## Next

All `holds`: `write-plan`, which cites `spike.md ## U<n>`. Any `fails`: `write-spec`
again, which drops that id and every requirement resting on it.

## Limit

The gate reads the form — a heading, a verdict, a block — not whether the block measured
the question. That stays judgement, and no script checks it.
