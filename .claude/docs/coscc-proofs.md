# The proofs

Read this before running `npm run e2e` or `scripts/capture_screens.py`, and before writing a
proof. A claim worth keeping is a test in `npm test` or a case in `npm run e2e`.

- `npm run e2e` drives a real browser against a temporary app past the login: no session, no
  quota, not part of `npm test` or CI. It needs the port free and a bundle built for it
  (`COS_HOST=127.0.0.1 COS_PORT=<port> uv run coscc-build`).
- `capture_screens.py` overwrites `.web` with a bundle for its own port and rebuilds the old one
  at the end; an interrupted run or a failed rebuild leaves every other proof at exit 2 until
  `uv run coscc-build`. Its screenshots are what `review` reads.
- Exit codes: `0` pass, `1` the page is broken, `2` the environment is not ready.
- In a checkout the bundle hardcodes its address: stop the app first, or build for another
  port, and never run two browser proofs at once. An installed wheel rewrites the address at
  startup.
