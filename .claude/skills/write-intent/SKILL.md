---
name: write-intent
description: Write the intent.md that opens a new unit of work in this repository. Use whenever a problem, idea, ticket, request or drift finding is first raised and no intent covers it yet — including when someone starts describing something they want built, and before any spec, plan or code for it exists.
---

# Write an intent

An intent states a problem and one outcome that could come back false. Everything
downstream is authorized by it, which is why it is written before them and never backwards
from them: an intent derived from a design it was meant to judge can no longer kill it.

## Output

One file, `.cos/NNNN_<slug>/intent.md`.

`NNNN` is four digits: list `.cos/`, take the highest number present, add one. Never guess
it. The slug is lowercase and hyphenated, contains no underscore, names the problem rather
than the solution, and is fixed now. The directory holds nothing else until the intent is
accepted.

`Status` is one of `draft`, `accepted` or `rejected`. Write `draft`.

## Template

````markdown
# Intent: <title>
Author: <name>. Status: draft.

## Problem

## Proposed outcome

## Affected users and systems

## Constraints

## Open questions
````

## Invariants

1. The originator states the problem in their own words first. Do not draft from a summary.
2. Interrogate before writing: scope, users, constraints, what success looks like. Ask;
   never close a gap by assuming.
3. Exactly one falsifiable outcome, carrying a number and a date, that could come back
   false. Two outcomes means two intents; split before writing.
4. Every figure names its source or is marked unverifiable. A figure with no source is cut,
   not softened.
5. Cite only a file committed in this repository, by path and line range.
6. No solution design. Problem, outcome, constraints, open questions. The spec decides how.
7. Leave `Status: draft`. The originator corrects it and the originator commits it.
8. Nothing downstream exists yet — no spec, no plan, no code — until this intent is
   accepted.

## Done when

Someone who was not in the conversation can state the problem from the file alone, and can
tell whether the outcome was met without asking anyone.

## Limit

This is advisory, and nothing forces a session to comply with it. These invariants hold
only until a deterministic check stands behind them.
