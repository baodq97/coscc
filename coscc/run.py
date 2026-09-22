"""Starting the app. One process, one port.

This is not `reflex run`, and the difference is a safety property rather than a preference.
`plan.md` step 1 measured it: Reflex's two-port dev mode starts a vite server that binds
every interface, and 0.9.11 exposes no setting for its host — only `PORT` is passed
through. So the dev server is not a configuration this app supports.

Instead the compiled frontend is mounted into the same ASGI app that serves `/api/*`
(`__REFLEX_MOUNT_FRONTEND_COMPILED_APP`), and uvicorn binds it to the host in `config.py`.

**Two kinds of install, and they get different answers to the same question.**
`0011` gave this app a second shape: a wheel that carries its own compiled bundle under
`coscc/_web/`, installed on a machine with no checkout and no Node. `coscc/frontend.py`
decides which of the two is in front of us, and the branch below is the whole difference:

*A checkout* can rebuild, so a bundle that disagrees with the source is a real error with a
real fix, and this refuses to serve it — that is `0003`'s guard and it stays exactly as it
was.

*A packaged install* cannot rebuild; there is no `reflex export` to run and no Node to run
it with. Refusing there would leave a person holding a wheel that can never start. So the
address baked into the bundle is rewritten to the address actually being served, and the
app comes up. `coscc/frontend.py` explains what is rewritten and why the `.gz` sidecar
matters as much as the `.js`.

Build the frontend first, in a checkout:

    uv run coscc-build

That wrapper exists rather than `reflex export` so the build leaves a fingerprint. See
`coscc/build.py`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Safe at module level, and the helpers below need it there: it imports nothing from
# Reflex, so it cannot disturb the ordering the two environment variables depend on.
from coscc import frontend

MOUNT_FLAG = "__REFLEX_MOUNT_FRONTEND_COMPILED_APP"

REPO = Path(__file__).resolve().parent.parent


def main() -> None:
    # Set before importing the app: Reflex reads it while composing the ASGI stack.
    os.environ.setdefault(MOUNT_FLAG, "1")

    from coscc.config import from_env

    config = from_env()

    # The one place the question "where is the compiled frontend" is answered, and the
    # answer is handed to Reflex rather than computed twice (`spec.md` R2). Reflex reads
    # this variable when it composes its static mount — measured 2026-09-22: it reads it
    # on every call rather than caching it at import — so setting it here, before the app
    # factory runs inside uvicorn, is what makes the mount and this file agree.
    os.environ[frontend.WEB_WORKDIR_VAR] = str(frontend.web_dir(REPO))
    static = frontend.static_dir(REPO)

    if frontend.is_packaged():
        _point_the_bundle_here(static, config)
    else:
        _refuse_a_bundle_that_does_not_match_the_source(static, config)

    import uvicorn

    for line in banner(config):
        print(line)
    uvicorn.run(
        "coscc.coscc:app",
        factory=True,
        host=config.host,
        port=config.port,
        log_level="warning",
    )


# Addresses that reach this machine and nowhere else. `0.0.0.0` and a bare interface
# address are both absent on purpose: binding either is what the warning below is about.
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


def banner(config) -> list[str]:
    """What is printed at startup, as lines, so a test can read them.

    The second line is a requirement rather than a courtesy -- `0011`'s `spec.md` R5. The
    default bind address changed to `0.0.0.0` in that unit, and this app has no
    authentication anywhere: `coscc/api.py` states the assumption it was built under, that
    every caller is a local process holding this machine's own credentials. Nothing
    enforces that assumption. So when the address is not loopback, the only thing standing
    where a login would be is this sentence, and it has to be printed every time rather
    than documented once.
    """
    lines = [f"coscc on http://{config.host}:{config.port}"]
    if config.host not in LOOPBACK:
        lines.append(
            "  ⚠ reachable from any machine that can route to this port, and coscc has no "
            "login — anyone who reaches it gets every screen, including the two controls "
            "that spend real Claude quota."
        )
        lines.append("  set COS_HOST=127.0.0.1 to bind this machine only.")
    lines.append(
        f"working folder: {config.working_dir or '(unset — workspace management off)'}"
    )
    lines.append(f"env workspaces: {', '.join(config.workspaces) or '(none)'}")
    lines.append(f"tools: {config.effective_tools() or 'none (chat only)'}")
    return lines


def _point_the_bundle_here(static: Path, config) -> None:
    """A packaged bundle is built once and served wherever it lands."""
    try:
        frontend.rewrite_address(static, config.host, config.port)
    except frontend.NoEnvChunk as missing:
        # The one failure that must stop the process. Serving on is the 2026-09-21
        # failure exactly: a page that renders, an API that is healthy, and a socket
        # that never connects. `coscc/frontend.py` measurement 2.
        print(str(missing), file=sys.stderr)
        raise SystemExit(2)
    except OSError as denied:
        print(
            f"the packaged frontend could not be written ({denied}) — it lives inside the "
            "installed package, so this usually means the install is read-only",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _refuse_a_bundle_that_does_not_match_the_source(static: Path, config) -> None:
    """A checkout can rebuild, so a mismatch is an error rather than something to fix up.

    The compiled page bakes in the address it opens its `/_event` WebSocket against, so
    serving a bundle built elsewhere renders a page that never connects while the API
    behind it stays perfectly healthy — a failure no HTTP check can see (found 2026-09-21
    by driving the page with a browser). The same fingerprint also catches a bundle older
    than the page source, which is `spec.md` C3 of `0003`.
    """
    from coscc import build

    state, message = build.check(config, static)
    if state != build.OK:
        print(message, file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
