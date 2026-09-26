---
paths:
  - "coscc/sessions.py"
  - "coscc/steps.py"
  - "coscc/instructions.py"
---

# Sessions: their data root and what they load

- **A session reads a scratch `COS_DATA_DIR`, and `cos.db` is a tripwire, not a lock.** Every
  session `Sessions` opens — each stage's step, Gebo, chat — gets `COS_DATA_DIR` pointed at
  a fresh `/tmp/coscc-session-*` (`sessions.scratch_dir`), and it and the commands
  `worktrees.prepare` runs carry `COSCC_PROTECTED_DB`, this app's `cos.db` appended to
  whatever list the app itself was given; `Data.connect` raises `Protected` before opening a
  listed file, reads included.
  - What it does not stop: a branch that edits the check has only the scratch directory
    (`.cos/0076_*/spec.md` C1); `python -c`, `sqlite3` or anything opening `~/.cos/cos.db`
    by its literal path walks past both.
  - A board step's directory is removed after its CLI is closed. Gebo's and a chat's live
    with their client in `Sessions._live` — Gebo streams with no `step` — and nothing in the
    app closes one but `Sessions.close_all`, which the installed service runs only on an
    update, and `cut_turn`, only on "apply now": every Gebo run and every new chat adds
    one that stays until then. A SIGKILL of the app, or any restart without that update,
    leaves every `/tmp/coscc-session-*` behind for good, and nothing sweeps them (C4, size
    unmeasured). Chat does not fall back to `~/.cos` (C6).
  - A measuring script's `--measure` run inside a step reads an empty database and exits
    2: run it at a terminal. If the app itself is started with its own `cos.db` in
    `COSCC_PROTECTED_DB`, every route that reads it is a `500` while `/api/health` says `ok`
    (`.cos/0076_*/plan.md` Risk 2).
- **A session reads nothing of `~/.claude/`, and of the project only what the app hands it.**
  `sessions._options` sets `setting_sources=[]` and `strict_mcp_config=True` for every
  session. `None` there, on claude-agent-sdk 0.2.158 and 0.2.159 (`.cos/0088_*/spike.md ## U6`), passes no flag, and
  the CLI then loads every source — the machine's MCP servers, skills, plugins, hooks and
  `permissions.allow`. Consequences:
  - A proxy or a key in the `env` block of `~/.claude/settings.json` is not used: it belongs
    in the service's env file (`docs/install.md`).
  - `CLAUDE.md`, `.claude/CLAUDE.md` and every rule without `paths:` reach the session
    through `coscc/instructions.py`, in the system prompt — handed over as a file in the
    session's data root, never as an argument, since Linux refuses one argument past
    128 KiB with `E2BIG` (`coscc/sessions.py` `_options`) and every session would fail to start.
  - A rule **with** `paths:` — every file under `.claude/rules/` here — arrives only as one
    line telling the session to `Read` it, and nothing checks that it did (the `start` row's
    `instructions` lists what was sent, not what was read). Each such rule costs every
    session that one line; `coscc/rules_budget_test.py` holds their size, not their count.
  - `@path` in those files is not resolved, and `CLAUDE.local.md`, parent directories and
    `.claude/settings*.json` of the project are not read.
  - The CLI's own built-in skills and slash commands are still in every init: no option
    measured removes them (`.cos/0088_*/spike.md ## U8`).
