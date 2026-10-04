# Decisions: what was decided about a unit, and by whose authority

Read this before changing `coscc/features/decisions.py`: the table, the routes, the tool, the
block and `_JS` are all in it. What a decision does to a unit is the kernel's
(`coscc/service/outdated.py`): it reads the run-log rows below, never this table.

## The table and its trace

`unit_decisions`, owned here: `id` (the `C<n>`, never reused), `workspace`, `unit`, `text` (at
most 4000 characters), `authority` (`person`, `delegated` or `agent`, fixed when it is written,
not read off the writer's name), `recorded_by`, `date`, `delegation` (a `D<n>`, only when
`delegated`), `reverses` (a `C<k>` or empty) and `withdrawn` (the day, or empty). No row is
deleted; a decision that is reversed gets `withdrawn` once and keeps its text.

Every write also appends, in the same transaction, through `Journal.append_with`:

- `{"kind": "decision", workspace, unit, id: "C<n>", authority, reverses, by, delegation}`
- for the decision it reverses, `{"kind": "decision-withdrawn", workspace, unit, id: "C<k>", by,
  reversed_by: "C<n>"}`

So the trace is the run log, and a write with no run log (no working folder) is refused. A
refusal raised while the table is read leaves neither the row nor the records. The number is
read first and inserted by name; a writer that took it in between rolls the other back, which
tries once more.

## Who may write what

The authority comes from the door, not from a field the caller sends:

- `POST /api/decisions` `{cwd, unit, text, delegation?, by?, reverses?}`: a `person` decision,
  or a `delegated` one when `delegation` is a `D<n>` in force today for this workspace, a
  delegation (not a decision) and naming the agent `by` opens with, as an answer's delegation is
  checked. `by` defaults to `owner`. Cost: one row and one or two run-log rows; it starts no
  stage.
- `POST /api/decisions/reverse` `{cwd, unit, id, text?, by?}`: a `person` decision that
  reverses `id`, whatever its authority; `text` defaults to "Reverses C<k>.". This is what the
  Reverse button sends.
- `GET /api/decisions?cwd=&unit=`: every decision of the unit, withdrawn ones included. Reads
  only.
- The tool `record_decision` (below): always `agent`.

A decision never overrides a stronger authority: an `agent` decision cannot reverse a `person`
or a `delegated` one, a `delegated` one cannot reverse a `person`, a `person` one reverses any.
Also refused: an unknown workspace, a `C<k>` that is not a decision of this unit or is already
withdrawn, a bad `D<n>`, and empty text or text over 4000 characters. A refusal is a 400 with
one sentence why.

The routes are behind the password like every `/api` route, and the password has no identities
(`.claude/docs/not-built.md`): a request that carries it can write `person` and cite any
delegation that names the `by` it sends. Nothing here tells a person from an agent that holds
the password; the tool is what an agent is given.

## What the agent sees

- One tool server, `decisions`, with `record_decision {text, reverses?}`, on `spike` and `impl`.
  A prose stage cannot carry a tool, so `intent`, `spec`, `plan` and `review` have none. It
  writes an `agent` decision for the run's own unit, which reaches the spec and plan unconfirmed;
  `reverses` may name an earlier `agent` decision only. A refusal comes back as one sentence.
- One prompt block, `decisions`, on `spec` and `plan` only, and only when the unit has a decision
  not withdrawn: "# Decisions that came after", each as `C<n> (<authority>): <text>`, and the
  instruction to follow the strongest authority (`person` over `delegated` over `agent`), and,
  when an `agent` decision contradicts a `person` or `delegated` one without reversing it, to
  follow the person's and raise the contradiction as a numbered question `N.` under
  `## Open questions`.
- No guard.

## On the unit screen

`_JS` draws into the child `[data-decisions]` of `slot-unit`: each decision with its number, its
authority (an agent's is "inferred by an agent"), its text, and a withdrawn one dimmed and struck
through with the decision that reversed it. A live agent decision has a Reverse button, which
sends `POST /api/decisions/reverse`. It draws again every 15 seconds, so a decision an agent
recorded appears without a reload.

## Hazards

- The feature being off for a workspace stops the tool and the block, not the routes: a person's
  decision is still recorded, and the kernel still reads it.
- `Data.decisions()` (the Settings delegations) is read, never written; the checks of
  `Answers._delegation_or_refuse` are written out again here, since a feature may not import
  `coscc.service.answers`.
- What a delegation `covers` is not checked, as for answers.
