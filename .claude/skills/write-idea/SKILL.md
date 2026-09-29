---
name: write-idea
description: Write the idea.md that records an observation before it is a problem. Use when something is noticed — a friction, a question, a half-formed want — and it is not yet clear enough to be an intent.
---

# Write an idea

An idea is the note you take before you know whether there is a problem. It exists so the
thought survives the week, and so the intent that may follow it can be checked against what
was actually noticed rather than against what the intent wanted to be true.

It is the only optional stage. A unit may open straight at `intent.md`, and eight units in
this repository did — `.claude/scripts/cos.mjs` never asks for an idea and never will.

## Output

One file, `idea.md`, in the unit's directory.

```markdown
# Idea: <title>
Author: <name>. Status: accepted.

## What was noticed

## Why it might matter

## What is not known yet
```

`Status` is `draft`, `accepted` or `rejected`. `rejected` is a real outcome here and the
most valuable one the stage has: an idea written down and turned down is a decision the
repository keeps, and it stops the same thought being re-raised as new.

## Hand back your judgement

The app does not read `Status:` or `## Open questions` out of the file to decide anything (`0136`). A step started from the board holds a `submit` tool: call it with `stage`, `judgement` (`ready` for `accepted`, `not-ready` for `draft`) and `questions` (`{n, text}` per open question). Call it before your reply, and end the reply with the file. If `submit` returns an error, the app has checked your object against the unit: correct the object and call `submit` again until it is accepted. A step that hands back no object ends failed. At a terminal there is no such tool; the `Status:` line is then all there is.

## Invariants

1. Record the observation, not a solution and not a plan. If you already know what to
   build, you are writing an intent.
2. Say when and where it was noticed. An idea with no occasion is a preference.
3. Every figure names its source or is marked unverifiable. Cite only files committed in
   this repository, by path and line range.
4. `## What is not known yet` is not optional. An idea with nothing unknown in it has
   skipped the thinking that makes the next stage worth doing.
5. Do not widen. One observation per idea; a second one is a second idea.

## Done when

Someone reading only this file can say what was noticed and why anyone cared, and can tell
that it has not yet been decided.

## Next

`write-intent`, if the idea is worth pursuing. Nothing forces that, and most ideas should
end at `rejected`.
