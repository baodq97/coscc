---
paths:
  - "coscc/config.py"
  - "coscc/units/__init__.py"
  - "coscc/data.py"
  - "coscc/runlog/journal.py"
---

# Things that break here

- `COS_DATA_DIR` (default `~/.cos`) holds `cos.db`; `COS_WORKING_DIR` holds others' checkouts.
  Backing up one does not back up the other. A stored workspace is a name, never a path.
- A unit's artifacts live under `COS_DATA_DIR`; `coscc/units/__init__.py` is the one place that
  says where.
- The measuring scripts' `--measure` reads `start` and `end` run-log rows with `sqlite3`
  directly, without importing `coscc`: renaming or nesting a field breaks a measurement no test
  runs.
