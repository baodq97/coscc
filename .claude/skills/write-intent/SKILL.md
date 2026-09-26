---
name: write-intent
description: Write the intent.md that opens a new unit of work in this repository. Use whenever a problem, idea, ticket, request or drift finding is first raised and no intent covers it yet — including when someone starts describing something they want built, and before any spec, plan or code for it exists.
---

# Write an intent

An intent states a problem and one outcome that could come back false. Everything
downstream is authorized by it, which is why it is written before them and never backwards
from them: an intent derived from a design it was meant to judge can no longer kill it.

## Output

One file, `intent.md`, in the directory the script names:

```
node .claude/scripts/cos.mjs new-path <slug>
```

It allocates the number and rejects a malformed slug. Use the path it prints; do not
compose one yourself and do not guess the number.

Choosing the slug is still yours: lowercase, hyphenated, naming the problem rather than the
solution, and fixed from now on. The directory holds nothing else until the intent is
accepted.

`Status` is one of `draft`, `accepted` or `rejected`. Write `accepted` once the file meets
`## Done when` below; write `draft` and say what is missing if it does not.

`Type` is what kind of work this is, and it decides the branch the work happens on:

```
feat  fix  docs  refactor  test  chore  perf  build  ci  revert
```

The set is closed. Pick the one that names the change a reader would see, not the effort
it took. Then take the branch name from the script rather than typing it:

```
node .claude/scripts/cos.mjs unit-branch <NNNN_slug>
```

It reads the `Type` you just wrote, joins it to the slug, and prints `<type>/<slug>`. That
is the whole point of the field: a branch composed by hand drifts from the unit it belongs
to on the second try.

It reads that `Type` **from disk**, and exits 2 with `No such work unit` while the file is
missing — so it cannot answer until `intent.md` has been written. Write the file, then ask
for the name, then cut the branch, and commit nothing before that branch exists.

## Template

````markdown
# Intent: <title>
Author: <name>. Type: <type>. Status: accepted.

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
7. `Type` is one of the ten, and it is not decoration: `unit-branch` refuses a type it does
   not know, so a wrong one stops the branch from being named at all.
8. Accept it and commit it. `accepted` means you judged it finished, not that the originator
   approved it — they still have to read it, and nothing in the repository makes them.
9. Interrogation still comes first. Accepting your own file removes the check that used to
   catch a thin intent, so the questions in invariant 2 are now the only thing standing in
   for it — ask them before writing, not after.
10. A question with a block under `## Answers` in the artifact that holds it has been
    decided. Do not ask it again. Cite it as `<artifact> ## Answers, câu N`. The words of
    an answer are a person's, not yours: quote them, and do not restate them as your own
    finding. The app only ever appends that section; never write into it yourself.
11. Under `## Open questions`, each question is an item `N. ` at column 0 whose first
    paragraph holds a `?`. `cos.mjs` counts nothing else: a bullet, or a numbered line with
    no `?`, is read as a note and never stops the autopilot. So a real question always
    carries its `?`. A note — no question is left open, how the answers were used, the
    originator should reread this — is a plain sentence there or goes in another section,
    never an item. When nothing is left open, keep the heading.

## Done when

Someone who was not in the conversation can state the problem from the file alone, and can
tell whether the outcome was met without asking anyone.

## Next

`write-spec`, once this file is accepted and committed.

You may run it yourself. Nothing now separates the two stages, so the separation has to come
from you: finish the intent, commit it, and re-read it before writing a spec against it.
Never run `write-spec` on a `draft` — a file you have not judged finished cannot authorize
the thing after it.

## Limit

The script allocates the number and validates the slug, so those two cannot go wrong
silently. Everything else here is advisory: nothing forces a session to run it, and no
check reads the prose it writes.
