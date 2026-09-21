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


def _build_targets(built, expected: str) -> bool:
    """Does the compiled bundle point at `host:port`?

    A string search over the built assets, which is crude and is the point: it checks the
    artifact that actually ships rather than re-deriving what it should contain.
    """
    for path in built.rglob("*.js"):
        try:
            if expected in path.read_text(errors="ignore"):
                return True
        except OSError:
            continue
    return False


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

    # The compiled page bakes in the address it opens its /_event WebSocket against
    # (see rxconfig.py). Serving it from a different port produces a page that renders
    # and then shows "Connection Error" with a perfectly healthy API behind it — a
    # failure no HTTP check can see, which is why this guard is here rather than a
    # comment. Found on 2026-09-21 by driving the page with a browser.
    expected = f"{config.host}:{config.port}"
    if not _build_targets(built, expected):
        print(
            f"the built frontend was not made for {expected} — rebuild with the same "
            f"settings:\n"
            f"    COS_HOST={config.host} COS_PORT={config.port} "
            f"uv run reflex export --frontend-only --no-zip",
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
