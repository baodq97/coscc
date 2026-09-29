---
name: write-intent
description: Write the intent.md that opens a new unit of work in this repository. Use whenever a problem, idea, ticket, request or drift finding is first raised and no intent covers it yet — including when someone starts describing something they want built, and before any spec, plan or code for it exists.
---

# Write an intent

An intent states a problem and one outcome that could come back false. Everything downstream
is authorized by it.

## What you are given (trust it)

The originator's input, the idea if any, and the answers to earlier questions are in the
prompt. A question with a block under `## Answers` is decided: do not ask it again, cite it
as `<artifact> ## Answers, câu N`, quote the person's words, never write into that section.

## Steps

1. Ask what the input leaves open (scope, users, constraints, success); never close a gap
   by assuming.
2. `node .claude/scripts/cos.mjs new-path <slug>` allocates the directory; slug is
   lowercase-hyphenated, names the problem, never composed by hand.
3. Write `intent.md` there. No solution design: the spec decides how.
4. `node .claude/scripts/cos.mjs unit-branch <NNNN_slug>` prints the branch name; cut it
   before the first commit.

`Type` is one of `feat fix docs refactor test chore perf build ci revert` and `unit-branch`
refuses any other.

````markdown
# Intent: <title>
Author: <name>. Type: <type>. Status: accepted.

## Problem

## Proposed outcome
<one falsifiable outcome, with a number and a date (YYYY-MM-DD)>

## Affected users and systems

## Constraints

## Open questions
````

- Exactly one outcome; two outcomes are two intents. A figure with no source is cut.
- Under `## Open questions` a real question is an item `N. ` at column 0 whose first
  paragraph holds a `?`; the same go into `questions` of `submit`. Anything else is a plain
  sentence, never an item. Keep the heading when none is left.
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
never one you inferred. With all three, `cos.mjs` puts the unit in the `fast` lane, with no
spec, spike or plan. If one is missing, write none of them: the unit walks the full lane.

## Done when

Someone outside the conversation can state the problem and tell whether the outcome was met.
