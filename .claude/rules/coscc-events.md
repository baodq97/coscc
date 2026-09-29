---
paths:
  - "coscc/runlog/events.py"
---

# Things that break here

- `GET /api/board/events` hands out everything a step saw (commands, paths, thinking, tool
  output) to whoever holds the password, for `KEEP_DAYS` and `KEEP_BYTES` of stored JSON
  (`coscc/runlog/events.py:67-68`); each field is cut at `FIELD_MAX` (`coscc/runlog/events.py:39`).
  `updates/cos.db.bak` carries a copy. Events go to `step_runs` and `step_events`, never the run log.
- The purge runs only in `coscc/run.py` before the server starts, so the total can pass
  `KEEP_BYTES` between starts and the file never shrinks without a `VACUUM`.
- The runner's `end` record waits up to `4 * CLOSE_WAIT` (`coscc/runlog/events.py:51`) for the
  recorder when `cos.db` is busy; the card reads "running" and *Stop* is refused meanwhile.
- A second copy of the app on the same data root writes into the same tables; this copy reads
  its steps as `ended-unknown` while they run.
- The watch pane holds at most `WATCH_WINDOW` (`coscc/state/views.py:939`) events, since every
  frame resends the list. Each open pane keeps a follower until the step ends, the pane closes,
  or it falls `SUB_LIMIT` (`coscc/runlog/events.py:54`) behind; a closed tab is not noticed.
  The list is drawn by position, so prepending rewrites rows in place.
