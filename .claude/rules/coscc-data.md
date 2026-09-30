---
paths:
  - "coscc/config.py"
  - "coscc/units/__init__.py"
  - "coscc/data.py"
  - "coscc/runlog/journal.py"
---

# Things that break here

- The data directory holds the database and the units' artifacts; the working directory holds
  other people's checkouts. Backing up one does not back up the other. A stored workspace is a
  name, never a path.
- A state-changing route writes one run-log row with who and the old and new value.
- Measuring scripts read run-log rows with `sqlite3`, not through `coscc`: renaming or nesting
  a field breaks a measurement no test runs.
