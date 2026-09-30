# The proofs, and what each one costs

Read this before running `npm run e2e` or `scripts/capture_screens.py`, and before writing a proof. A claim worth keeping is a test in `npm test` or a case in `npm run e2e`; a unit writes no proof or measuring script of its own.

- **`npm run e2e` is `scripts/e2e.py`.** Browser, no session, no quota, no
  network: it starts `coscc.run` on `COS_HOST`/`COS_PORT` with a temporary working folder
  and data root, four workspaces cloned from bare-directory remotes, and a password and one
  session written in before it starts, so every case runs past the login. Two of the
  workspaces, `f1` and `f2`, hold 117 units each written straight into
  the store; its board case measures them at 1280, 1440 and 1690 by 800 in both densities
  and at 390×844, and sets the density back to `comfortable` when it ends. It needs
  the port free and a bundle built for it (`COS_HOST=127.0.0.1 COS_PORT=<port> uv run
  coscc-build`). Each case is a function named for what it shows. It is not part of `npm
  test` and does not run in CI.
- **`scripts/capture_screens.py` overwrites `<repo>/.web`, then builds it back.** `coscc.run` always serves `<repo>/.web`, so the bundle it builds for its own port
  (18783) replaces the checkout's. When the old one was
  current for this environment's `COS_HOST`/`COS_PORT`, it is rebuilt at the end and the
  last line says so; interrupted, or a failed rebuild, leaves every proof that needs the
  default bundle at exit 2 until `uv run coscc-build`. Its screenshots are what `review`
  looks at, as an agent reading PNGs, not a person; the `ship` gate reads only the words of
  `review.md ### Screens`, never the images (`.claude/CLAUDE.md`, *A screenshot is not a
  person's look*).

Exit codes: `0` pass, `1` the page is broken, `2` the environment is not ready.

`npm run e2e` and `capture_screens.py` open a real browser. **In a
checkout** the bundle hardcodes its own address, so none can move to a spare port without a
bundle built for it: stop the app first, or build for another port, and never run two of
them at the same time. A wheel installed by `install.sh` behaves the other
way — `coscc/frontend.py` rewrites the address at startup, because a packaged install has
no Node to rebuild with. Both sentences are true; which one applies depends on which of the
two shapes you are looking at, and `coscc/run.py` is where they part.

A figure carries across a rewrite only if the mechanism did not change. This
holds for swapping the store, and it holds the same way for swapping the interpreter.
