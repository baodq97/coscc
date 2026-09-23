---
name: write-pr
description: Write the pr.md that records the pull request opened for a work unit — its URL, branch and scope. Use once impl.md is accepted and the change is ready to be proposed.
---

# Write a PR record

`pr.md` records where the change was proposed and what it covers. It is short on purpose:
the argument for the change is in `intent.md` and `spec.md`, and what was built is in
`impl.md`. This file exists so that a unit in `.cos/` can be followed to the pull request
without anyone searching for it, and so that the `review` gate can find it: that gate
reads the `PR:` field in the header and nothing else.

## Before writing

```
node .claude/scripts/cos.mjs gate <NNNN_slug> pr
```

Exit 0 means proceed.

**This stage needs a remote.** If `git remote -v` is empty, stop and say so — do not invent
a URL, and do not record a pull request that was never opened.

Recording `draft` because no pull request exists never clears the gate after this one. So
"stop and say so" means stop, not write `draft` and carry on.

## Opening it — and not merging it

`.claude/CLAUDE.md` step 6 is where this comes from:

```
git push -u origin HEAD
gh pr create --fill-first --body-file <the pr.md you just wrote>
gh pr checks <number> --required --watch
```

The push is listed because `gh pr create` cannot open a pull request for a branch the
remote has never seen, and in a non-interactive session it has no way to ask.

**This stage stops at an open pull request.** It does not merge. The merge belongs to
`ship`, and `ship`'s gate opens only after a review round passed with no finding open and
no code landed after it. In the app, the `pr` step is refused the merge command outright;
at a terminal nothing refuses it, so this sentence is the rule.

**Wait for the required checks.** The `review` gate asks GitHub for them and stays closed
until every one is green — pending is not green, and a repository with no required
checks never opens it. If a check is red, the work goes back to `impl`: fix it on the same
branch, push, and wait again. Each fix is its own commit; name it in `## Where`. Do not
write that CI passed unless `gh pr checks` said so.

## Output

One file, `pr.md`, in the unit's directory.

```markdown
# PR: <title>
Intent: intent.md. Impl: impl.md. PR: <url>. Author: <name>. Status: accepted.

## Where

## Scope of the diff

## What a reviewer should look at first
```

`PR: <url>` is the URL `gh pr create` printed, ending in `/pull/<number>`. `## Where`
carries the URL again, the branch, and the state of the required checks when this file was
written. `Status` is `draft`, `accepted` or `rejected`.

## Invariants

1. **The URL is real and was returned by the command that opened the PR.** Never compose
   one from a pattern.
2. **`PR: <url>` is in the header.** Without it the `review` gate is closed with
   `pr.md names no pull request`.
3. **`## Scope of the diff` names files and counts**, both taken from `git diff --stat`.
4. **`## What a reviewer should look at first` names the risky part**, not the largest
   part. If `impl.md` recorded a departure from the plan, that is usually it. The reviewer
   is a separate agent session that reads this first.
5. A PR that could not be opened is recorded as `draft` with the reason, not omitted.
6. Every figure names its source. Cite only files committed in this repository.

## Done when

Someone can reach the pull request from this file alone, knows whether its checks were
green, and knows, before opening it, which part of it deserves their attention.

## Next

`write-review`, once the required checks are green.
