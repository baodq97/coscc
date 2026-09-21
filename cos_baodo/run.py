"""Starting the app. One process, one port, loopback.

This is not `reflex run`, and the difference is a safety property rather than a preference.
`plan.md` step 1 measured it: Reflex's two-port dev mode starts a vite server that binds
every interface, and 0.9.11 exposes no setting for its host — only `PORT` is passed
through. So the dev server is not a configuration this app supports.

Instead the compiled frontend is mounted into the same ASGI app that serves `/api/*`
(`__REFLEX_MOUNT_FRONTEND_COMPILED_APP`), and uvicorn binds it to the host in `config.py`,
which defaults to `127.0.0.1`. One port to check, and `ss -ltn` can check it.

Build the frontend first:

    uv run reflex export --frontend-only --no-zip
"""

from __future__ import annotations

import os
import sys

MOUNT_FLAG = "__REFLEX_MOUNT_FRONTEND_COMPILED_APP"


def main() -> None:
    # Set before importing the app: Reflex reads it while composing the ASGI stack.
    os.environ.setdefault(MOUNT_FLAG, "1")

    import uvicorn

    from cos_baodo.config import from_env

    config = from_env()

    from reflex.utils import prerequisites

    built = prerequisites.get_web_dir() / "build" / "client"
    if not (built / "index.html").is_file():
        print(
            "the frontend is not built yet — run:\n"
            "    uv run reflex export --frontend-only --no-zip",
            file=sys.stderr,
        )
        raise SystemExit(2)

    print(f"cos-baodo on http://{config.host}:{config.port}")
    print(f"working folder: {config.working_dir or '(unset — workspace management off)'}")
    print(f"env workspaces: {', '.join(config.workspaces) or '(none)'}")
    print(f"tools: {config.effective_tools() or 'none (chat only)'}")
    uvicorn.run(
        "cos_baodo.cos_baodo:app",
        factory=True,
        host=config.host,
        port=config.port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
