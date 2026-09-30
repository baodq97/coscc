---
paths:
  - "coscc/update/__init__.py"
  - "coscc/update/updater.py"
  - "scripts/build_wheel.sh"
---

# Things that break here

- Update pauses work, restarts the app and builds upstream code as this user, for whoever holds
  the password.
- Pausing interrupts each session, closes it and kills its descendants; a child that left by
  `setsid` escapes. A paused step resumes at the next start from its transcript, which must sit
  under the CLI's project directory for the cwd; missing, the step ends `failed`.
- A wheel is verified by a checksum that comes from the same release.
- After the trial the app exits 75: systemd logs a failure and `NRestarts` grows, which
  `install.sh` reads as a crash loop at 2.
- A new version that passes the trial and fails to start is not rolled back; restoring the
  backup loses what the new version wrote.
- The trial resets the password on its copy and needs a cookie: an updater that asks without
  one fails every trial.
- `update.identity` feeds the version fields of a step's `start` row, which measuring scripts
  split on.
