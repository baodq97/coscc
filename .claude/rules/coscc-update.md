---
paths:
  - "coscc/update.py"
  - "coscc/updater.py"
  - "scripts/build_wheel.sh"
---

# Updating from the board

- **`POST /api/update/*` stops work, restarts the app and builds upstream code, for whoever
  holds the password.**
  - *Áp dụng ngay* stops every board step (through Stop's road, so each that had begun gets
    an `end` with `stopped_by`; one cut before its first turn gets none) and cuts every chat
    turn of this process. *Áp dụng* waits for them instead, and a person can keep it waiting
    forever by starting new work.
  - *Build từ origin/main* runs `scripts/build_wheel.sh` of the configured workspace's
    upstream `main` under this user — `uv sync`, Reflex fetching Node/Bun, all of it.
  - The source of a release is a constant and a wheel is installed only after its sha256
    matched, but that checksum comes from the same release (`.cos/0068_*/spec.md` C5).
  - After a trial run on `127.0.0.1`, the app calls `Service.shutdown` and
    `Sessions.close_all` itself (the lifespan never runs on the real stack,
    `.cos/0068_*/spike.md ## U5`), stops uvicorn from inside, installs offline in `main`, and
    exits 75: systemd logs it as a failure and `NRestarts` grows, which `install.sh` reads as
    a crash loop at 2 (C7, unmeasured).
  - A new version that passes the trial and still fails to start is not rolled back by
    anything; the update's log holds the command, and restoring `updates/cos.db.bak` loses
    what the new version wrote (C1, C8).
  - The trace is the `update` rows in the run log (workspace `""`, `by` a typed name) and
    `<COS_DATA_DIR>/updates/logs/`. The password is what stands in front;
    `COS_HOST=127.0.0.1` still narrows who can try it.
- **The trial logs in.** It clears the password on its copy of `cos.db` with
  `coscc reset-password`, reads the setup token off the trial's output (written to the
  update's log as `<redacted>`), sets a throwaway password through `POST /setup` and needs
  `200` from `/api/workspaces` and `/` with that cookie. An updater that asks
  `/api/workspaces` for `200` without a cookie fails every trial against a build with the
  login: such a release is installed with `curl … | sh`.
- `update.identity` is also what a board step's `start` row takes `app_version` and
  `app_commit` from (`Service._app_identity`); `scripts/verify_0094.py --measure` splits
  before and after on them.
