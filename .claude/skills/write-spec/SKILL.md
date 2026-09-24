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

```
node .claude/scripts/cos.mjs gate <NNNN_slug> spec
```

Exit 0 means proceed. Anything else means stop and report what it printed — it names what
is missing. Do not write the spec anyway, and do not change a status to open your own gate.

Then run the skip assessment. Report all five criteria with a verdict on each:

1. The change touches two or fewer files that already exist.
2. No public interface, API contract, data schema or stored data changes.
3. No dependency is added.
4. No behaviour appears beyond what `intent.md` already states.
5. Nothing in auth, PII or the security surface is touched.

All five pass means the spec *may* be skipped. Say so and let the human decide — the
decision is theirs, not yours. Any criterion failing means write the spec, and name the
criterion that forced it.

A skip is still written down. Record it as `spec.md` with `Status: skipped`, the reason,
and the assessment that produced it:

````markdown
# Spec: <title>
Intent: intent.md. Author: <name>. Status: skipped.

## Why skipped
<the criteria, and who decided>
````

A skip that leaves no file is indistinguishable later from a spec nobody got round to
writing, and the gate ahead cannot tell those apart either.

## Output

One file, `.cos/NNNN_<slug>/spec.md`, in the directory the intent already occupies. Do not
create a new work unit and do not renumber the existing one.

## Template

````markdown
# Spec: <title>
Intent: intent.md. Author: <name>. Status: accepted.

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
8. Accept it and commit it. `accepted` means you judged it finished — no one else will look,
   so the `## Concerns` section is where a doubt gets recorded instead of resolved by you.
9. A question with a block under `## Answers` in the artifact that holds it has been
   decided. Do not carry it forward as open and do not ask it again. Cite it as
   `<artifact> ## Answers, câu N`. The words of an answer are a person's, not yours: quote
   them, and do not restate them as your own finding. The app only ever appends that
   section; never write into it yourself.
10. A concern you could not measure from here — whether an SDK, a CLI or a process behaves
    the way the design assumes — is written as an item at column 0 under `## Concerns`
    that opens `[unmeasured]` and an id: `- [unmeasured] U1. Does disconnect() make the
    process exit within 10 s?`. `cos.mjs` reads these, and a spec that carries one sends
    the unit to `spike` (`write-spike`) before `plan`. `U<n>` is the question's identity:
    keep it across rewrites. An item with no id, or one id used twice, closes the `plan`
    gate. A spec with no such item never runs `spike`.
11. Rewritten after a spike found a `U<n>` that `fails`: drop that id and every
    requirement resting on it — never keep it. A new question takes a new id; never reuse
    one. When no direction left holds, write `Status: draft` with the question under
    `## Open questions`: a draft stops the loop until a person answers.

## Done when

An engineer can plan against this file alone, and every concern that would have stopped
them later is already written down under `## Concerns`.

## Next

`write-spike`, once this file is accepted, when it carries an `[unmeasured] U<n>` item.
Otherwise `write-plan`, once this file is accepted and committed. If the spec was skipped instead,
`write-plan` runs on the accepted intent alone and records the reason.

You may run it yourself. Nothing now separates the two stages, so the separation has to come
from you: finish the spec, commit it, and re-read it before planning against it. Never run
`write-plan` on a `draft` — a file you have not judged finished cannot authorize the thing
after it.

## Limit

The gate is decided by a script, so the answer does not vary with how carefully a session
reads. What stays advisory is the invocation: nothing forces a session to run it, or to
stop when it exits non-zero. A `PreToolUse` hook would.

The invariants above are advisory throughout — they describe judgement, and no script
checks them.
