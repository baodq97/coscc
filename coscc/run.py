"""Starting the app. One process, one port, loopback.

This is not `reflex run`, and the difference is a safety property rather than a preference.
`plan.md` step 1 measured it: Reflex's two-port dev mode starts a vite server that binds
every interface, and 0.9.11 exposes no setting for its host — only `PORT` is passed
through. So the dev server is not a configuration this app supports.

Instead the compiled frontend is mounted into the same ASGI app that serves `/api/*`
(`__REFLEX_MOUNT_FRONTEND_COMPILED_APP`), and uvicorn binds it to the host in `config.py`,
which defaults to `127.0.0.1`. One port to check, and `ss -ltn` can check it.

Build the frontend first:

    uv run coscc-build

That wrapper exists rather than `reflex export` so the build leaves a fingerprint; this
function refuses to serve a bundle that does not match the source it claims to be built
from. See `coscc/build.py`.
"""

from __future__ import annotations

import os
import sys

MOUNT_FLAG = "__REFLEX_MOUNT_FRONTEND_COMPILED_APP"


def main() -> None:
    # Set before importing the app: Reflex reads it while composing the ASGI stack.
    os.environ.setdefault(MOUNT_FLAG, "1")

    import uvicorn

    from coscc import build
    from coscc.config import from_env

    config = from_env()

    # One question, one place that answers it. The compiled page bakes in the address it
    # opens its /_event WebSocket against, so serving a bundle built elsewhere renders a
    # page that never connects while the API behind it stays perfectly healthy — a
    # failure no HTTP check can see (found 2026-09-21 by driving the page with a
    # browser). The same fingerprint also catches a bundle older than the page source,
    # which is `spec.md` C3.
    #
    # This used to grep the compiled JS for "host:port". That answered a narrower
    # question, and answered it separately from `scripts/verify_0003.py` — the
    # arrangement this repo paid to learn about when `sessions.py` kept its own gate.
    built = build.web_dir() / "build" / "client"
    state, message = build.check(config, built)
    if state != build.OK:
        print(message, file=sys.stderr)
        raise SystemExit(2)

    print(f"coscc on http://{config.host}:{config.port}")
    print(f"working folder: {config.working_dir or '(unset — workspace management off)'}")
    print(f"env workspaces: {', '.join(config.workspaces) or '(none)'}")
    print(f"tools: {config.effective_tools() or 'none (chat only)'}")
    uvicorn.run(
        "coscc.coscc:app",
        factory=True,
        host=config.host,
        port=config.port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
