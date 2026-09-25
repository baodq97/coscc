---
paths:
  - "coscc/events.py"
---

# Step events: what is kept and who reads it

- **`GET /api/board/events` hands out everything a step saw.** Every board step carries a
  recorder (`coscc/events.py`) fed by `Sessions._stream`, the grant's gate and the runner:
  every SDK message, refusal and outcome, each field cut at `FIELD_MAX`
  (`coscc/events.py:46`), kept in memory while the step runs and written each second to
  `step_runs` and `step_events` in `cos.db` — never the run log. Whoever holds the password
  or a live session reads all of it, commands, paths, thinking and tool output included,
  for `KEEP_DAYS` and `KEEP_BYTES` of stored JSON (`coscc/events.py:76-77`), and the
  update's `updates/cos.db.bak` carries a copy.
- The purge runs only in `coscc/run.py` before the server starts (`.cos/0073_*/spec.md` C5):
  between starts the total can pass `KEEP_BYTES` by any amount, and without a `VACUUM` the
  file never shrinks. A purge that fails prints one line and the app starts anyway.
- The runner's `end` record waits for the recorder's last write: up to `4 * CLOSE_WAIT`
  (`coscc/events.py:59`, chosen, unmeasured) when `cos.db` is busy, the card reading
  "running" and a *Stop* refused as already sealed all that time. What a running step holds
  in memory is unmeasured (C7).
- A second copy of the app on the same data root writes its steps' events into the same
  tables, but nobody can follow them live, and this copy reads them as `ended-unknown` while
  they run (C9).
- The watch pane holds at most `WATCH_WINDOW` (`coscc/state.py:622`) events, because every
  frame resends the whole list (`.cos/0073_*/spike.md ## U4` measured the frame sizes); on
  a slow link the delay will pile up (unmeasured). Each tab that opens the pane keeps a
  follower until the step ends, the pane closes, or it falls `SUB_LIMIT`
  (`coscc/events.py:62`) events behind; a closed tab is not noticed
  (`.cos/0073_*/plan.md` Risk 4). The list is drawn by position, so a page prepended or a
  row dropped from the top rewrites rows in place.
- Neither route writes anything; both are behind the login. Watching is not an approval
  (`.claude/docs/not-built.md`).
