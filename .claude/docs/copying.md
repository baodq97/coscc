# Copying this harness into another repository

Read this before copying `.claude/` into another repository. Moved here from `.claude/CLAUDE.md` (`0094`).

**What enforces this does not travel with the harness.** Copying `.claude/` brings the
grammars, the commands and their tests. It does not bring `.github/workflows/`, which sits
outside `.claude/`, and it cannot bring the GitHub ruleset, which is a setting rather than a
file — and the ruleset is the only thing here that actually stops a push. Since `0015` the
`review` and `ship` gates also need `git` and a logged-in `gh`, and the `review` gate reads
the pull request's **required** checks: a repository with no CI and no required check has
a `review` gate that never opens.

**The loop needs the coscc app and its database (`0135`).** `cos.mjs status`, `gate`,
`next`, `rerun`, `unit-branch` and `pr-text` decide on the snapshot `--state` hands them, which
`uv run coscc state <workspace>` prints from `cos.db`; without it they exit 2. An artifact
written at a terminal reaches that database only when a step of the app next ends on its unit.

**`pr` and `ship` are the app's (`0136`).** From the board neither runs a session: the app's
PR machine (`coscc/github/prmachine.py`) pushes the branch, opens the pull request, reads its
CI while the autopilot is on, and merges pinned to the head its own guard read. A repository
used only at a terminal has none of that: a person runs `gh pr create` and `gh pr merge`, as
steps 6 and 8 of `.claude/CLAUDE.md` say, and nothing reads CI between two asks of `next`.

Copy `.claude/`. That is the whole harness, and nothing lands in the host repository's own
tree. Claude Code loads `.claude/CLAUDE.md` as project instructions, so no import, symlink
or root file is needed. Then put that repository's real build and test commands under
`## Commands`, and rebuild the two legs named above by hand.

`.claude/rules/ui-standard.md` comes with it, and its `paths:` list is coscc's files. Kept
as it is, the `ship` gate stays closed on any unit touching those paths until a review
records screenshots — and the command that takes them, `scripts/capture_screens.py`, sits
outside `.claude/` and does not come along. Point the list at your own screens and write a
capture command, or delete the file: without it the `ship` gate asks for no screenshots. One
thing stays either way: an `[open]` `low` finding whose text opens with `S` and a number
(`S3 bucket …`) is read as a rule of the standard and blocks.
