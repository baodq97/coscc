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

## Opening it, and merging it

Two commands, in this order, and `.claude/CLAUDE.md` step 6 is where they come from:

```
git push -u origin HEAD
gh pr create --fill-first --body-file <the pr.md you just wrote>
gh pr merge --squash --delete-branch
```

The push is listed because `gh pr create` cannot open a pull request for a branch the
remote has never seen, and in a non-interactive session it has no way to ask.

**Merging here means the same party opened and merged it, and that is not an oversight.**
`.claude/CLAUDE.md` under *What is deliberately not built* already says so: *"a pull request
opened and merged by the same party changes the route, not the reviewer"*. This stage
running both commands does not remove a gate — there has never been one — it only stops
leaving the branch open for a reviewer who is not coming. The stage after this one still
writes `review.md`, and `Concluded by:` is where it says that an agent concluded on its own
work.

If the merge is refused, record why in `## Where` and leave `Status: draft`. The usual
refusal is `2 of 2 required status checks are expected`, which means a commit landed while
the checks were green and they have to run again — wait for them, do not force it.

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
