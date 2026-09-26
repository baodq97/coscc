# What the page stopped explaining

Read this before adding words to a screen, or removing the one sentence a screen keeps beside a button. Since `0082` (`.cos/0082_*/spec.md ## Answers, câu 5`) the page says one sentence per place; `.cos/0082_*/impl.md` holds the table, paragraph by paragraph, of what left the page and where it is said now.

- **The page says one sentence per place, and these are no longer on it.** *Cut this unit's
  branch* fetches `main` first and cuts nothing if that fetch fails; the app never pushes,
  merges or commits for it. A chat session holds only what Settings' chat knobs list, no
  tools by default, is created on its first message and is saved by the SDK, not by this
  app. Settings cannot change the workspace root, the data root, the address or the chat
  knobs: they come from the environment this process started with, and nothing over HTTP
  sets them. Of those knobs, `tools` empty means a chat session has no tool at all, not
  even read; `allow_write_and_exec` off removes every write and exec tool whatever `tools`
  lists; `bypass_permissions` has no way in but the environment; and
  `resume_foreign_sessions` is off because resuming a session the app did not create is
  untested, not because it is known to be dangerous (`coscc/config.py`, `Config`). The
  Updates section says why updates are unavailable in one line; the updater's own reason
  (not a packaged install, no systemd unit, no `uv`) is in `/api/update` as `reason`. A
  model or effort change applies to the next session and opens no gate. A dropped unit's
  dialog writes nothing but the hold panel. A new unit's number comes from `cos.mjs
  new-path` and its brief becomes `idea.md`, which the intent step reads. An empty board
  read only the app's store; the host repository's own `.cos/` is counted, never listed. A
  stage's status on a card comes from its artifact's `Status:` line, never from the run
  log; the columns sort cards by stage and a badge says the state. Paths, full shas, UUIDs, variable names and
  the update logs' tails are on the page only inside a *Details* the person opens
  (`coscc/studio.py`, `details`).
- **The sentences that stay are the warnings.** Run (spends quota), `pr` and `ship` (this
  machine's `gh` login), Drop (closes the pull request), *Propose estimates* (a paid
  session), Integrate and *Apply now* (stops what runs) each keep one sentence beside the
  button, from `service.CONSEQUENCE`. The full strings stay in `coscc/policy.py`,
  `coscc/hold.py` and `/api/board`; `.claude/rules/coscc-policy.md` and
  `.claude/docs/not-built.md` are where they are said.
