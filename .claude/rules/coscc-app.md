---
paths:
  - "coscc/**"
  - "coscc/**/*"
  - "rxconfig.py"
  - "scripts/*.py"
---

# The coscc app

## Commands

```
uv run coscc-build                          # build the page; never `reflex export`
COS_WORKING_DIR=~/projects uv run coscc     # never `reflex run`
uv run coscc reset-password                 # clears the master password and every session
uv run python scripts/capture_screens.py /board /settings   # into .screens/
```

`npm test` never builds: editing the page needs a rebuild.

## Invariants

- A handler that decides anything belongs in `Service`; the page only shows and navigates.
- Sessions spend account quota: nothing unattended talks to the app except its own autopilot;
  a `--paid` script spends money.
- In-memory state (marks, the running list, holds) is per process: a second copy of the app on
  one data root is not seen.
- A card's state never uses the harness's `blocked` flag (true for every unfinished unit): read
  `next.why`.
- A build directory left in a checkout shadows `.claude/` and the page while `git status` stays
  clean: remove it after a hand-built wheel.
- A database newer than the app is a 500 on every page while `/api/health` says `ok`: match the
  app to the database.
- Every route, button and grant acts for whoever holds the password: `.claude/docs/not-built.md`
  before adding one.
- The vault (`coscc/features/vault/`) is not a security boundary: an agent with python or node
  can read a call's tmpfs file or `/proc/<pid>/environ`, use its `SSH_AUTH_SOCK`, or print a value
  the filter misses. The leak scan detects, it does not prevent; one login, every action is
  `owner`. Rest in `coscc/features/vault.md`.

## Docs

Read the one that names what you edit:

- `.claude/docs/coscc-steps.md`: board steps, stop, running list.
- `.claude/docs/coscc-answers.md`: routes that write into a unit's artifacts, rerun.
- `.claude/docs/coscc-settings.md`: settings and backlog routes.
- `.claude/docs/coscc-spike.md`: the spike step.
- `.claude/docs/coscc-proofs.md`: `npm run e2e`, `capture_screens.py`.
