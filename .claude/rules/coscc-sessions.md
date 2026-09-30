---
paths:
  - "coscc/agent/sessions.py"
  - "coscc/agent/steps.py"
  - "coscc/agent/instructions.py"
---

# Things that break here

- Each session gets a scratch data directory and a marker naming the app's database, and
  `Data.connect` refuses a marked file. That is a tripwire, not a lock: `sqlite3` or a literal
  path walks past it.
- A step's scratch directory is removed when its CLI closes; a paused session's stays until an
  update, and a kill leaves them unswept.
- A session must not inherit the machine's settings, MCP servers, plugins or hooks: options set
  `setting_sources=[]` and `strict_mcp_config=True`, and `None` there would load them. A proxy
  or key in `~/.claude/settings.json` is not used.
- Instructions reach a session as a file, never as an argument (the OS limits argument size).
  A rule with `paths:` arrives as one line telling the session to `Read` it, and nothing checks
  that it did: keep rules small.
- `--settings` holds only attribution: any other key there passes `setting_sources=[]`.
- A measuring script's `--measure` inside a step reads an empty database: run it at a terminal.
