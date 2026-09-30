---
paths:
  - "coscc/runlog/events.py"
---

# Things that break here

- The step view hands out everything a step saw (commands, paths, thinking, tool output) to
  whoever holds the password. Stored transcripts can hold secrets, and the update backup copies
  them. Events go to their own tables, never the run log.
- Retention runs only at start, and the file never shrinks without a `VACUUM`.
- The watch pane resends its list on every frame, so it is bounded and drawn by position:
  prepending rewrites rows in place.
- A second copy of the app on one data root writes the same tables and reads this copy's live
  steps as ended.
