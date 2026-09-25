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

# The same: standard library only (`coscc/update.py`'s docstring says why it must be).
from coscc import update

MOUNT_FLAG = "__REFLEX_MOUNT_FRONTEND_COMPILED_APP"

REPO = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if args:
        _answer_and_stop(args)
        return

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

    # `0073` R14: the only time a step's events are purged, before the first request. Not in
    # `api.py`'s lifespan, which the real stack never runs (`0068`'s `spike.md ## U5`).
    purge_events(config)

    import uvicorn

    for line in banner(config):
        print(line)
    # `0068` R12 step 7: the app keeps its own `Server`, because `uvicorn.run` does not
    # hand it out and the updater has to ask it to stop from inside. Measured in
    # `spike.md ## U5` part 1: `run()` returns 0.119 s after `should_exit` is set.
    #
    # `0070`: the target is the guarded app. `proxy_headers=False` because uvicorn's
    # default trusts `X-Forwarded-For` from a loopback peer (`spike.md ## U3`), which lets
    # any process on this machine choose the address the login limiter sees; with it off,
    # `scope["client"]` is always the real peer (`spec.md ## Answers, câu 14`). The guard
    # reads `X-Forwarded-Proto` from a loopback peer itself, for the cookie's `Secure`.
    server = uvicorn.Server(uvicorn.Config(
        "coscc.coscc:served",
        factory=True,
        host=config.host,
        port=config.port,
        log_level="warning",
        proxy_headers=False,
    ))
    update.SERVER.register(server)
    server.run()
    # A hand-off exists only when the updater asked the server to stop. SIGTERM never gets
    # here: the process dies with 143 first (`spike.md ## U5`, the `sigterm` control).
    handoff = update.take_handoff()
    if handoff is not None:
        raise SystemExit(update.finish(handoff))


def purge_events(config) -> None:
    """`0073` R14. A purge that fails is one line on stderr, and the app starts anyway: the
    events are kept longer than asked, which is not a reason to have no board."""
    from coscc import events

    try:
        runs, freed = events.purge_on_start(config)
    except Exception as e:  # noqa: BLE001 - `Busy`, `Protected`, anything
        print(f"coscc: step events were not purged this start: {type(e).__name__}: {e}", file=sys.stderr)
        return
    if runs:
        print(f"step events purged: {runs} run(s), {freed} bytes")


def installed_version() -> str:
    """The version of the package this process is running from.

    Read from installed metadata rather than from `pyproject.toml`, because a packaged
    install has no `pyproject.toml` to read -- and because the number that matters is the
    one that was installed, not the one in whatever source tree happens to be nearby.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("coscc")
    except PackageNotFoundError:  # pragma: no cover - coscc is always installed to run
        return "unknown"


def _answer_and_stop(args: list[str]) -> None:
    """`--version`, and a refusal for anything else.

    `--version` is answered before the frontend is resolved or the address written, so it
    still answers when the bundle is broken. That is the point of it: `0011`'s outcome
    step 3 is "the next release arrives in one command", and this is how a person -- or
    `scripts/verify_0011.py` -- tells an update that happened from one that only appeared
    to.

    Refusing an unrecognised argument is a deliberate addition beyond `spec.md` R8, and
    `plan.md` records it. Before `0011` this program took no arguments and ignored them
    all; ignoring them became more expensive in the same unit that made `0.0.0.0` the
    default, because a mistyped flag would now quietly start a server reachable from the
    network instead of doing whatever was intended.

    `reset-password` (`0070` R10) is the only way back from a forgotten master password,
    and it is here on purpose rather than on a route: it needs a shell on this machine.
    It clears the password and every session in the database the environment points at,
    and says which file. It does not talk to a running process — the database is the only
    channel, and the guard reads it on the next request.
    """
    if args in (["--version"], ["-V"]):
        print(f"coscc {installed_version()}")
        return
    if args == ["reset-password"]:
        from coscc.config import from_env
        from coscc.data import Data

        data = Data(from_env().data_dir)
        data.auth_clear()
        print(f"coscc: password and sessions removed from {data.db_path}")
        return
    print(
        f"coscc: unrecognised argument {args[0]!r}\n"
        "usage: coscc [--version | reset-password]\n"
        "everything else is configuration, and it is read from the environment "
        "(COS_HOST, COS_PORT, COS_WORKING_DIR, ...) -- see docs/install.md",
        file=sys.stderr,
    )
    raise SystemExit(2)


# Addresses that reach this machine and nowhere else. `0.0.0.0` and a bare interface
# address are both absent on purpose: binding either is what the warning below is about.
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


def banner(config) -> list[str]:
    """What is printed at startup, as lines, so a test can read them.

    The warning lines are a requirement rather than a courtesy -- `0011`'s `spec.md` R5,
    rewritten by `0070` R12. The default bind address is `0.0.0.0`. Since `0070` a master
    password stands in front of every route, but coscc serves plain HTTP: off loopback,
    whoever can watch the network reads the password, the session cookie and the setup
    token as they pass. That has to be printed every time rather than documented once.
    """
    lines = [f"coscc on http://{config.host}:{config.port}"]
    if config.host not in LOOPBACK:
        lines.append(
            "  ⚠ reachable from any machine that can route to this port: anyone who reaches "
            "it gets the login page. Over plain HTTP the password, the session cookie and "
            "the setup token cross the network readable — put coscc behind a TLS reverse "
            "proxy or on a private network."
        )
        lines.append("  set COS_HOST=127.0.0.1 to bind this machine only.")
    lines.append(
        f"working folder: {config.working_dir or '(unset — workspace management off)'}"
    )
    lines.append(f"env workspaces: {', '.join(config.workspaces) or '(none)'}")
    lines.append(f"tools: {config.effective_tools() or 'none (chat only)'}")
    return lines


def _point_the_bundle_here(static: Path, config) -> None:
    """A packaged bundle is built once and served wherever it lands.

    Two things have to be true before it can serve at all, and the second one is the half
    that was missed until a clean machine found it -- see `coscc/frontend.py`.
    """
    # Without this, Reflex recompiles on every start and ends that compile by shelling out
    # to Bun or npm, which a packaged install does not have. `Type=simple` makes that look
    # like a healthy service, so nothing short of an HTTP request notices.
    os.environ[frontend.SKIP_COMPILE_VAR] = "1"

    absent = frontend.missing_compile_marker(REPO)
    if absent is not None:
        print(
            f"this packaged install has no {absent.name} at {absent} — the release that "
            "built it copied the static bundle but not the build state beside it, so "
            "Reflex would enter its compile anyway and stop on a missing Node. The wheel "
            "is incomplete; reinstall from a release built after 2026-09-22.",
            file=sys.stderr,
        )
        raise SystemExit(2)

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
