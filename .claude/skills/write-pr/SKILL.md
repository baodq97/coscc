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

From the board no session runs this stage: the app pushes the branch, opens the pull
request and writes `pr.md` itself (`coscc/github/prmachine.py`). This skill is for a
terminal.

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

`.claude/CLAUDE.md` step 6 is where this comes from. In this order — `pr.md` is written
before anything waits, because a step that runs out of turns while waiting must still
leave the file behind:

1. **Does the pull request already exist?** Ask once:
   `gh pr view --json url,number,mergeable`. If one is open for this branch, use
   its URL and skip step 2 — never open a second pull request for one branch.
2. **Open it.** Write `pr.md` with its `# PR:` line in the title grammar under `## Output`,
   its three body sections, `Status: draft` and no `PR:` yet, and `## Scope of the diff`
   left empty — there is no pull request yet to count. Run
   `node .claude/scripts/cos.mjs pr-text <NNNN_slug>`; while its `titleProblem` is not
   `null`, fix the `# PR:` line and run it again. Then, with the `title` it printed:

   ```
   git push -u origin HEAD
   gh pr create --title '<title>' --body-file <that pr.md>
   ```

   The push is listed because `gh pr create` cannot open a pull request for a branch the
   remote has never seen, and in a non-interactive session it has no way to ask. The title
   is never a commit's subject. The body this puts up is temporary — a `pr.md` still
   `draft` — and step 5 replaces it.
3. **Record it at once.** As soon as you have the URL — from step 1 or step 2 — run
   `gh pr view <url> --json files,changedFiles,additions,deletions`, write
   `## Scope of the diff` from what it returned in the grammar under `## Output`, then put
   `PR: <url>` in the header and set `Status: accepted`.
4. **Then read the checks, once.** `gh pr checks <number> --required`, without `--watch`.
   Write under `## Where` what it said — `pending`, `green` or `red` — exactly as it said
   it. Pending is not green, and a repository with no required checks never opens the
   `review` gate. Do not write that CI passed unless `gh pr checks` said so.
5. **Put pr.md onto the pull request.** Its title is the `# PR:` line; its body is the
   rest of `pr.md` less that line and the header line holding `Status:` — whether this
   step opened the pull request or found it open, and whatever a person wrote there since
   (`pr.md` is the source). It is the last thing the step does, and it is exactly this:

   ```
   node .claude/scripts/cos.mjs pr-text <NNNN_slug> | node -e '
   const t = JSON.parse(require("fs").readFileSync(0, "utf8"))
   require("child_process").execFileSync("gh", ["pr", "edit", t.url, ...(t.title ? ["--title=" + t.title] : []), "--body-file", "-"], { input: t.body, stdio: ["pipe", "inherit", "inherit"] })'
   ```

   `cos.mjs pr-text` is the one place the title and body are cut out of `pr.md`; the app
   reads the same command, so the terminal and the board put up the same words.

**This stage stops at an open pull request.** It does not merge. The merge belongs to
`ship`, and `ship`'s gate opens only after a review round passed with no finding open and
no code landed after it. At a terminal nothing refuses the merge command, so this sentence
is the rule.

**It does not integrate either.** Never `git rebase`, `git merge`, `git pull`,
`gh pr update-branch` or a forced push.

- **The branch conflicts with `main`:** `pr.md` is still `accepted`, with its URL, and
  `## Where` says the pull request conflicts and that resolving it is *Integrate*'s, on
  the board. It must be accepted: the board offers *Integrate* only to a unit whose
  `pr.md` is accepted and names its pull request (`betweenPrAndShip`,
  `.claude/scripts/cos.mjs`).
- **A required check is red:** name the red checks under `## Where` and stop. The fix is
  `impl`'s, on the same branch; `cos.mjs next` sends the unit there. Do not fix it here.

## Output

One file, `pr.md`, in the unit's directory.

```markdown
# PR: <title>
Intent: intent.md. Impl: impl.md. PR: <url>. Author: <name>. Status: accepted.

## Where

## Scope of the diff

## What a reviewer should look at first
```

The title on the `# PR:` line is `<type>(<NNNN>): <text>`: `<type>` is `intent.md`'s `Type:`,
`<NNNN>` the unit's four digits, and `<text>` English, not empty, not opening with `wip`,
with no letter carrying a diacritic — an em dash is fine. It becomes the squash commit's
subject. `cos.mjs pr-text` prints `titleProblem`, `null` or what is wrong; the `review` and
`ship` gates stay closed on any title it refuses, and `ship` also on a pull request whose
title differs from this line.

`PR: <url>` is the URL `gh pr create` printed — or, when the pull request already existed,
the one the lookup or `gh pr view` returned — ending in `/pull/<number>`. `## Where`
carries the URL again, the branch, and the state of the required checks when this file was
written. `Status` is `draft`, `accepted` or `rejected`; `draft` only between steps 2 and 3
above, or when no pull request could be opened (invariant 5).

`## Scope of the diff` has one grammar, which `cos.mjs pr-text` reads and the app compares
with GitHub after the step:

```markdown
## Scope of the diff

<changedFiles> files, +<additions>/-<deletions>
- `<path>`
- `<path>`

<prose, if any>
```

- The first non-blank line is exactly that, plain ASCII, no bold, `files` even for one.
- Right after it, one `` - `<path>` `` line for each entry of `files`, each path once.
- Prose, the source sentence of invariant 6 among it, only after the list.
- When `files` has fewer entries than `changedFiles`, list only what `gh` returned, and
  add a sentence after the list giving both numbers.

## Invariants

1. **The URL is real and was returned by `gh`** — by the command that opened the PR, or by
   `gh pr view` or the app's lookup for one already open. Never compose one from a pattern.
2. **`PR: <url>` is in the header.** Without it the `review` gate is closed with
   `pr.md names no pull request`.
3. **`## Scope of the diff` names files and counts**, both from step 3's
   `gh pr view <url> --json files,changedFiles,additions,deletions`, never from `git diff`
   against any ref: GitHub counts from the merge-base with the remote base,
   which is what the reader of the pull request sees.
4. **`## What a reviewer should look at first` names the risky part**, not the largest
   part. If `impl.md` recorded a departure from the plan, that is usually it. The reviewer
   is a separate agent session that reads this first.
5. A PR that could not be opened is recorded as `draft` with the reason, not omitted.
6. Every figure names its source. Cite only files committed in this repository.

## Done when

Someone can reach the pull request from this file alone, knows whether its checks were
green, and knows, before opening it, which part of it deserves their attention. The title
and description on the pull request are the ones `pr.md` gives (step 5).

## Next

`write-review`, once the required checks are green.
