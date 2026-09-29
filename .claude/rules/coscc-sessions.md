---
paths:
  - "coscc/agent/sessions.py"
  - "coscc/agent/steps.py"
  - "coscc/agent/instructions.py"
---

# Things that break here

- Every session gets `COS_DATA_DIR` pointed at a fresh `/tmp/coscc-session-*`
  (`sessions.scratch_dir`) and `COSCC_PROTECTED_DB` naming this app's `cos.db`; `Data.connect`
  raises `Protected` on a listed file. This is a tripwire, not a lock: `python -c`, `sqlite3` or
  a literal `~/.cos/cos.db` path walks past it.
  - A step's scratch directory is removed when its CLI closes. Gebo's and a chat's live in
    `Sessions._live` until `suspend_all` or `close_all`, which run only on an update. A SIGKILL
    or any restart without one leaves every `/tmp/coscc-session-*` behind, unswept. A paused
    session resumes from its transcript under `~/.claude/projects`.
  - A measuring script's `--measure` inside a step reads an empty database and exits 2: run it
    at a terminal. With the app's own `cos.db` in `COSCC_PROTECTED_DB`, every route reading it
    is a `500` while `/api/health` says `ok`.
- `sessions._options` sets `setting_sources=[]` and `strict_mcp_config=True`. `None` there
  passes no flag and the CLI loads the machine's MCP servers, skills, plugins, hooks and
  `permissions.allow`.
  - A proxy or key in `~/.claude/settings.json` `env` is not used: put it in the service's env
    file (`docs/install.md`).
  - `CLAUDE.md`, `.claude/CLAUDE.md` and every rule without `paths:` reach the session through
    `coscc/agent/instructions.py`, handed over as a file in the session's data root, never as an
    argument (Linux refuses one past 128 KiB with `E2BIG`).
  - A rule with `paths:` arrives as one line telling the session to `Read` it; nothing checks
    that it did. `coscc/rules_budget_test.py` holds sizes, not the count.
  - `@path` imports, `CLAUDE.local.md`, parent directories and the project's
    `.claude/settings*.json` are not read.
  - A session with a preset also gets `--settings` holding only `attribution`
    (`agents.settings_json`); any other key there reaches the session past `setting_sources=[]`.
