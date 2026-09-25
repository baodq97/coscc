---
paths:
  - "coscc/config.py"
  - "coscc/units.py"
  - "coscc/data.py"
  - "coscc/journal.py"
---

# The two roots

- **Two roots, and backing up one does not back up the other.** `COS_DATA_DIR` (default
  `~/.cos`) holds `cos.db`; `COS_WORKING_DIR` holds somebody else's git checkouts. A stored
  workspace is a *name*, never a path — the path is rebuilt from the root on every read,
  which is why a hand-edited store cannot point the app at `/etc`.
- A unit's artifacts live under `COS_DATA_DIR`, not in the repository the work is done in;
  `coscc/units.py` is the one place that answers where.
- A field added to a `start` or `end` row is read by `scripts/verify_*.py --measure` with
  `sqlite3` directly (they do not import `coscc`): renaming or nesting one breaks a
  measurement no test runs.
