# Copying this harness into another repository

Read this before copying `.claude/` elsewhere.

- Copy `.claude/`: it is the whole harness, and Claude Code loads `.claude/CLAUDE.md` with no
  import or root file. Put the repository's own build and test commands under `## Commands`.
  The agents, their skills and the processes are not in it: they are the app's packs, turned on
  per workspace, each unit on the process it opened with.
- What enforces does not travel: CI (`.github/workflows/`) and the host's ruleset, the only thing
  that stops a push. A process's review and merge gates need `git`, a logged-in `gh` and
  required checks: with none, review never opens.
- The deciding commands (`status`, `gate`, `next`, `rerun`, `unit-branch`) need the
  app's snapshot and exit 2 without it. An artifact written at a terminal reaches the database
  when a step of the app next ends on its unit.
- Pushing, opening and merging the pull request belong to the app; at a terminal a person does
  them.
- `rules/ui-standard.md` lists this repository's screens: re-point its `paths:` and give it a
  capture command, or delete it, and no gate asks for screenshots.
