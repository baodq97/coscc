---
name: write-pr
description: Write the pr.md that records the pull request opened for a work unit — its URL, branch and scope. Use once impl.md is accepted and the change is ready to be proposed.
---

# Write a PR record

`pr.md` records where the change was proposed and what it covers. It is short on purpose:
the argument for the change is in `intent.md` and `spec.md`, and what was built is in
`impl.md`. This file exists so that a unit in `.cos/` can be followed to the pull request
without anyone searching for it.

## Before writing

```
node .claude/scripts/cos.mjs gate <NNNN_slug> pr
```

Exit 0 means proceed.

**This stage needs a remote.** If `git remote -v` is empty, stop and say so — do not invent
a URL, and do not record a pull request that was never opened.

Recording `draft` because no pull request exists never clears the gate after this one. So
"stop and say so" means stop, not write `draft` and carry on.

## Output

One file, `pr.md`, in the unit's directory.

```markdown
# PR: <title>
Intent: intent.md. Impl: impl.md. Author: <name>. Status: accepted.

## Where

## Scope of the diff

## What a reviewer should look at first
```

`## Where` carries the URL and the branch. `Status` is `draft`, `accepted` or `rejected`.

## Invariants

1. **The URL is real and was returned by the command that opened the PR.** Never compose
   one from a pattern.
2. **`## Scope of the diff` names files and counts**, both taken from `git diff --stat`.
3. **`## What a reviewer should look at first` names the risky part**, not the largest
   part. If `impl.md` recorded a departure from the plan, that is usually it.
4. A PR that could not be opened is recorded as `draft` with the reason, not omitted.
5. Every figure names its source. Cite only files committed in this repository.

## Done when

Someone can reach the pull request from this file alone and knows, before opening it, which
part of it deserves their attention.

## Next

`write-review`.
