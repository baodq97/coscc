---
name: write-intent
description: Write the intent.md that opens a new unit of work in this repository. Use whenever a problem, idea, ticket, request or drift finding is first raised and no intent covers it yet — including when someone starts describing something they want built, and before any spec, plan or code for it exists.
---

# Write an intent

An intent states a problem and one outcome that could come back false. Everything downstream
is authorized by it.

## Where it runs

In a session with the originator, in this checkout: an interview, not a form. It ends when
they say it is done or every section below can be written without guessing. Nothing is
edited but `intent.md`. From the board the input and earlier answers are in the prompt: ask
through `questions` as the prompt says, and never re-ask a question that has a block under
`## Answers` (cite it as `<artifact> ## Answers, câu N`).

## The interview

1. Let them describe it in their own words first.
2. Before each round, look: `Grep`/`Read` the code the problem names, `git log` for what was
   tried, the idea or incident they point at. Read only what the next question needs.
3. Ask at most three questions a round, each with what you found and your recommended answer.
   Dig where the answer is thin: the evidence, who pays, how done is measured, what must not
   change.
4. Never close a gap by assuming; a gap they leave open goes under `## Open questions`.

## Write it

`cos.mjs new-path <slug>` allocates the directory (slug lowercase-hyphenated, names the
problem). Write `intent.md` there, at most 2 KB, in their words where they decided something. No
solution design: the spec decides how. `Type` is a conventional-commit type.

````markdown
# Intent: <title>
Author: <name>. Type: <type>. Status: accepted.

## Problem
<what cannot be done today, the evidence, and why it matters now>

## Proposed outcome
<one falsifiable outcome, with a number and a date (YYYY-MM-DD)>

## Affected users and systems

## Constraints

## Out of scope

## Open questions
````

- Exactly one outcome; two outcomes are two intents. A figure with no source is cut.
- Under `## Open questions` a real question is an item `N. ` at column 0 whose first
  paragraph holds a `?`; from the board the same go into `questions` of `submit`. Anything
  else is a plain sentence, never an item. Keep the heading when none is left.
- `Status: accepted` records your judgement, not approval; `draft` if something is missing.

**A fix the originator showed you.** Only for `Type: fix`, and only when they gave all three,
add these after `## Problem`, copying their command, log and words as they wrote them:

````markdown
## Reproduction
```
<the command, test or log they gave>
```

## Expected
Source: <path>[:<L1>-<L2>]
<what that file says should happen>

## Actual
<what happens instead>
````

`Source:` is a path they or an answer under `## Answers` named: relative, outside `.cos/`,
never one you inferred. With all three the unit takes the fast lane. If one is missing, write
none of them.

## Done when

Someone outside the conversation can state the problem and tell whether the outcome was met.
