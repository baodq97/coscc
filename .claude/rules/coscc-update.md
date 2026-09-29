---
paths:
  - "coscc/update/__init__.py"
  - "coscc/update/updater.py"
  - "scripts/build_wheel.sh"
---

# Things that break here

- `POST /api/update/*` pauses work, restarts the app and builds upstream code, for whoever holds
  the password.
  - *Apply* waits only for a mechanical integration and a screenshot retake.
    Then `Sessions.suspend_all` pauses every agent session: descendants listed, transcript line
    count read, `interrupt()`, client closed, live descendants SIGKILLed (one that left by
    `setsid` is not). Each is a `suspend` row and a local build is cut. Work with no session open
    gets `SETTLE_WITHIN` s; what outlives it is a `cut` row and cancelled.
  - The next start (`coscc/service/resume.py`) takes each row up once, before the autopilot, in
    the same session, with no git run on the worktree first. Its transcript must be under
    `~/.claude/projects/<cwd, every non-alphanumeric character as ->/`; missing, the step ends
    `failed`, and so does a row whose unit has a later `start` or `end`.
  - *Build from origin/main* runs `scripts/build_wheel.sh` of the workspace's upstream `main`
    under this user: `uv sync`, Reflex fetching Node/Bun.
  - A wheel is installed only after its sha256 matched, but the checksum comes from the same
    release.
  - After a trial on `127.0.0.1` the app calls `Service.shutdown` and `Sessions.close_all`,
    installs offline and exits 75: systemd logs a failure and `NRestarts` grows, which
    `install.sh` reads as a crash loop at 2.
  - A new version that passes the trial and fails to start is not rolled back; restoring
    `updates/cos.db.bak` loses what the new version wrote.
  - Trace: the `update` run-log rows and `<COS_DATA_DIR>/updates/logs/`.
- The trial resets the password on its copy of `cos.db`, sets a throwaway one through
  `POST /setup` and needs `200` from `/api/workspaces` and `/` with that cookie. An updater that
  asks without a cookie fails every trial against a build with the login.
- `update.identity` supplies a step's `start` row `app_version` and `app_commit`
  (`Steps.app_identity`), which measuring scripts split on.
