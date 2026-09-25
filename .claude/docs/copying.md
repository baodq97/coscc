# Copying this harness into another repository

Read this before copying `.claude/` into another repository. Moved here from `.claude/CLAUDE.md` (`0094`).

**What enforces this does not travel with the harness.** Copying `.claude/` brings the
grammars, the commands and their tests. It does not bring `.github/workflows/`, which sits
outside `.claude/`, and it cannot bring the GitHub ruleset, which is a setting rather than a
file — and the ruleset is the only thing here that actually stops a push. Since `0015` the
`review` and `ship` gates also need `git` and a logged-in `gh`, and the `review` gate reads
the pull request's **required** checks: a repository with no CI and no required check has
a `review` gate that never opens.

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
