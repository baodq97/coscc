# Board steps: what they record, stop and share

Read this before changing `/api/timeline`, `POST /api/board/stop`, `GET /api/board/running`, `Service.run_step`, `coscc/steps.py` or `runner.describe_attempt`. Moved here whole from `.claude/rules/coscc-app.md` (`0094`); the history ("Since `00xx`") is kept at this tier.

- **`/api/timeline` returns what a failed paid step replied.** Since `0014` a step whose
  reply could not be used keeps the last 2000 characters of it (`coscc/runner.py:150`), and
  that text reaches the board as `detail`, for whoever holds the password or a live
  session.
- **`pull` refuses only within this process.** Two copies of the app on one working folder
  still see past each other for sessions. `.cos/0004_silent-concurrent-loss/spec.md` C2.
- **A failed step's transcript tail is stored in `cos.db` and put into the next prompt.**
  Since `0019` a stage that ends without `done` — a ceiling hit, an exception, a reply with
  no `Status:` line — has `Runner.run` capture the tree (`HEAD`, branch, the commits since
  the trunk, `git status --porcelain`) and the last `runner.ATTEMPT_EXCERPT` (8000, chosen;
  measured 2026-09-24 as too short to hold `0032`'s own measurements, which sat 87656 and
  101788 characters from the end — the unit's `impl.md` says why it was left) characters of what the session's own
  turns produced, and append it to the run log as one `kind: "attempt"` row, read back only
  by `journal.failed_attempts` and placed in the *next* run's prompt
  (`runner.describe_attempt`), never in an artifact. No route returns it — `/api/timeline`,
  `Service.board`, `.activity`, `.usage` and `.activity_and_usage` all project a fixed set
  of fields that does not include it — but it still sits in `cos.db` under the data root,
  and a tool's own output can carry a token or a local path. Capturing is best-effort:
  `Runner.run`'s `finally` swallows every exception around it, so a step's outcome and its
  `end` record never depend on the capture succeeding.
- **`POST /api/board/stop` ends anyone's step.** Since
  `0034`. It closes the step's CLI client and cancels the step's task; a CLI still running
  `sessions.DISCONNECT_TIMEOUT` (5s, chosen) after the close began gets SIGTERM from the
  app, and SIGKILL `KILL_AFTER` (3s, chosen) later — through the SDK's private
  `_transport._process`, so an SDK that renames it loses this silently; the step ends `stopped`, writes no artifact and records no
  transition, and whatever it already committed or pushed stays. `stopped_by` is `owner`
  from the board since `0082` (or a name the request carried), not an identity, and the
  trace is that one `end` record. A Stop whose
  cancel reaches the step's task before its first turn leaves no trace at all: the runner
  never ran, so there is neither `start` nor `end`, and only the Stop's own reply names
  `stopped_by` (`Service._never_driven`, since `0050`). A step that
  has begun writing its artifact refuses the stop. A step stopped before its session
  reported a cost records `cost_unknown` and no cost at all, so Activity reads it as free.
  Stopping a `pr` or `ship` midway can leave a pushed branch or a merged pull request with
  no `pr.md` or an unremoved worktree. Whoever holds the password or a live session can
  press it.
- **Units run their steps at the same time.** Since `0034` each board step is its own
  task, one per unit (a second is refused before it spends anything) and any number of
  units at once; a reader that goes away no longer ends the step, and every step's CLI
  process is closed when it ends. Since `0048` a fetch of the same clone waits for one
  already running; the rest of two steps' `git` in one workspace — `switch main` among it —
  can still collide on a lock, and nothing here serialises it (unmeasured). The list
  of running steps is in memory: a restart forgets it, and a step cut off by a restart has
  no `end` record. Since `0050` a unit is held from before `run_step`'s first board read:
  a second request for any stage of it is refused before it runs `cos.mjs`, `git` or `gh`,
  with a reason naming the stage, the phase (`preparing` or `running`) and when it began
  (`steps.describe`). Still one process only: a second copy of the app, a chat or a
  terminal is not seen.
- **`GET /api/board/running` tells anyone holding the password which units have a paid
  session open, and since when.** Since `0051` every card on the Board shows the step or
  integration running on it (stage, agent name, start time), or `ended, unknown` for a
  `start` in the run log with no `end`. It is near
  real time: each tab on the Board asks every 5s (`RUNNING_POLL`, chosen, not measured),
  reading the run log's `start` and `end` rows each time — the cost of that on a large
  run log, and against `busy_timeout` with ten sessions appending, is unmeasured; a busy
  read comes back as a `note`, not an error. What is running is kept in one process's
  memory (`Service._running`), like `_active` and `pull`: a step another copy of the app
  runs on the same working folder shows here as `ended, unknown` while it is still going,
  and a person may read that as dead and press run again. An `ended, unknown` row stops
  showing when the unit's next `start` is written or after 24 hours; nothing writes an
  `end` for it. A step started at a terminal has no entry either. The password is what stands in front; `COS_HOST=127.0.0.1` still narrows who can try it.
