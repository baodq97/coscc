---
paths:
  - "coscc/update/__init__.py"
  - "coscc/update/updater.py"
  - "scripts/build_wheel.sh"
---

# Updating from the board

- **`POST /api/update/*` pauses work, restarts the app and builds upstream code, for whoever
  holds the password.**
  - *Apply* (one way since `0138`) waits only for a mechanical integration, a screenshot
    retake and a knowledge gather, and none of them may begin once it is pressed; from the
    trial on, no session may. After the trial, `Sessions.suspend_all` pauses every agent
    session of this process at once: its CLI's descendants listed, the transcript's line
    count read, `interrupt()`, the client closed, every listed descendant still alive
    SIGKILLed — one that left the tree by `setsid` first is not (0138 C3). Each is a
    `suspend` row, and a local build is cut.
  - The next start (`coscc/service/resume.py`) takes each row up once, before the autopilot,
    in the same session from the last point every tool call had its result, and runs no git
    on the worktree first. Its transcript must be under `~/.claude/projects/<cwd, every
    non-alphanumeric character as ->/`: missing there, the step ends `failed` (C8).
  - *Build from origin/main* runs `scripts/build_wheel.sh` of the configured workspace's
    upstream `main` under this user — `uv sync`, Reflex fetching Node/Bun, all of it.
  - The source of a release is a constant and a wheel is installed only after its sha256
    matched, but that checksum comes from the same release (0068 spec C5).
  - After a trial run on `127.0.0.1`, the app calls `Service.shutdown` and
    `Sessions.close_all` itself (the lifespan never runs on the real stack,
    0068 spike ## U5), stops uvicorn from inside, installs offline in `main`, and
    exits 75: systemd logs it as a failure and `NRestarts` grows, which `install.sh` reads as
    a crash loop at 2 (C7, unmeasured).
  - A new version that passes the trial and still fails to start is not rolled back by
    anything; the update's log holds the command, and restoring `updates/cos.db.bak` loses
    what the new version wrote (C1, C8).
  - The trace is the `update` rows in the run log (workspace `""`, `by` `owner` when started from the board) and
    `<COS_DATA_DIR>/updates/logs/`. The password is what stands in front;
    `COS_HOST=127.0.0.1` still narrows who can try it.
- **The trial logs in.** It clears the password on its copy of `cos.db` with
  `coscc reset-password`, reads the setup token off the trial's output (written to the
  update's log as `<redacted>`), sets a throwaway password through `POST /setup` and needs
  `200` from `/api/workspaces` and `/` with that cookie. An updater that asks
  `/api/workspaces` for `200` without a cookie fails every trial against a build with the
  login: such a release is installed with `curl … | sh`.
- `update.identity` is also what a board step's `start` row takes `app_version` and
  `app_commit` from (`Service._app_identity`); `0094`'s measuring script splits before and
  after on them.
