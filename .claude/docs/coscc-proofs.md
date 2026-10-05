# The proofs

Read this before running `npm run e2e` or `scripts/capture_screens.py`, and before writing a
proof. A claim worth keeping is a test in `npm test` or a case in `npm run e2e`.

- `npm run e2e` drives chromium against the studio, served by a temporary app on a free
  loopback port, past the login: no session, no quota, not part of `npm test` or CI.
- `capture_screens.py <path>...` (studio paths such as `/up-next`, `/unit/proj/2`) takes each
  at two sizes into `.screens/`; it refuses a dirty tree. Its screenshots are what `review` reads.
- Both build `coscc/_studio/` first (`npm --prefix ui run build`) when it is missing or older
  than `ui/src`. They take a free port, so they can run beside the app.
- Exit codes: `0` pass, `1` the page is broken, `2` the environment is not ready.
