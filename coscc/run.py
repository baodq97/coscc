"""Starting the app: one process, one port, the FastAPI app behind the login guard.

The studio is built into the package (`coscc/_studio/`, `coscc/http/studio.py`), so the process
needs no Node and nothing to rebuild; uvicorn binds the app to the host in `config.py`.
"""

from __future__ import annotations

import logging
import sys

# Standard library only.
from coscc import update
from coscc.config import LOOPBACK

log = logging.getLogger(__name__)


# Longer than the studio's stream lasts (`routes.STREAM_LIFETIME_SECONDS`), so a stop lets it end
# on its own first.
STOP_WAIT_SECONDS = 40


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if args:
        _answer_and_stop(args)
        return

    # Every logger to stderr, which journald timestamps; the app's own from `info` up.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("coscc").setLevel(logging.INFO)

    from coscc.config import from_env

    config = from_env()

    # Ends the steps the app went down under, before the purge takes the events their turns
    # are counted from.
    recover_steps(config)
    # The only time step events are purged: before anything can write one.
    purge_events(config)
    # Nothing is running yet, so every scratch directory of a unit that is gone is an orphan.
    sweep_scratch(config)

    import uvicorn

    for line in banner(config):
        print(line)
    # The app keeps its own `Server`: `uvicorn.run` does not hand it out and the updater
    # must ask it to stop from inside.
    # `proxy_headers=False`: uvicorn would trust `X-Forwarded-For` from a loopback peer, so
    # any local process could choose the address the login limiter sees. The guard reads
    # `X-Forwarded-Proto` itself, for the cookie's `Secure`.
    server = uvicorn.Server(
        uvicorn.Config(
            "coscc.run:served",
            factory=True,
            host=config.host,
            port=config.port,
            log_level="warning",
            proxy_headers=False,
            # A stop waits this long for open responses, then closes them: a reader that never
            # ends (a stream, a follower) must not hold an update for good.
            timeout_graceful_shutdown=STOP_WAIT_SECONDS,
        )
    )
    update.SERVER.register(server)
    server.run()
    # A hand-off exists only when the updater asked the server to stop; SIGTERM never gets here.
    handoff = update.take_handoff()
    if handoff is not None:
        raise SystemExit(update.finish(handoff))


def served():
    """What uvicorn serves: the app behind the login guard, which sees every scope."""
    from coscc.http.app import build
    from coscc.http.auth import Guard
    from coscc.config import from_env
    from coscc.store.db import Data

    config = from_env()
    return Guard(build(config, starting=True), Data(config.data_dir))


def recover_steps(config) -> None:
    """End the steps the app went down under. A failure is logged; the app starts anyway."""
    from coscc.runlog import recovery

    try:
        runs = recovery.recover_on_start(config)
    except Exception:
        log.exception("steps the app went down under were not ended this start")
        return
    if runs:
        log.info("steps the app went down under, ended as failed: %s", runs)


def purge_events(config) -> None:
    """Purge old step events. A failure is logged; the app starts anyway."""
    from coscc.runlog import events

    try:
        runs, freed = events.purge_on_start(config)
    except Exception:
        log.exception("step events were not purged this start")
        return
    if runs:
        log.info("step events purged: %s run(s), %s bytes", runs, freed)


def sweep_scratch(config) -> None:
    """Remove the scratch of units no workspace has. A failure is logged; the app starts anyway."""
    from coscc.store.workspaces import Store
    from coscc.units.workspaces import live_units
    from coscc.units import scratch

    try:
        paths = list(config.workspaces)
        if config.working_dir:
            store = Store(config.working_dir, config.data_dir)
            paths += [str(store.path_of(e.name)) for e in store.entries()]
        scratch.sweep(live_units(paths, config.data_dir), config.data_dir)
    except Exception:
        log.exception("unit scratch directories were not swept this start")


def installed_version() -> str:
    """The installed package version (a packaged install has no `pyproject.toml`)."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("coscc")
    except PackageNotFoundError:  # pragma: no cover - coscc is always installed to run
        return "unknown"


def _answer_and_stop(args: list[str]) -> None:
    """`--version`, the shell-only subcommands, and a refusal for anything else.

    An unrecognised argument is refused: a mistyped flag must not start a network-reachable
    server. `reset-password` is here rather than on a route because it needs a shell on this
    machine; it clears the password and every session in the database.
    """
    if args in (["--version"], ["-V"]):
        print(f"coscc {installed_version()}")
        return
    if args == ["reset-password"]:
        from coscc.config import from_env
        from coscc.store.db import Data

        data = Data(from_env().data_dir)
        data.auth_clear()
        print(f"coscc: password and sessions removed from {data.db_path}")
        return
    if args[0] == "state" and len(args) == 2:
        raise SystemExit(_state(args[1]))
    if args[0] == "skip":
        raise SystemExit(_skip(args[1:]))
    print(
        f"coscc: unrecognised argument {args[0]!r}\n"
        "usage: coscc [--version | reset-password | state <workspace> | skip <workspace> <unit> spec <reason>]\n"
        "everything else is configuration, and it is read from the environment "
        "(COS_HOST, COS_PORT, COS_WORKING_DIR, ...) -- see docs/install.md",
        file=sys.stderr,
    )
    raise SystemExit(2)


def _state(target: str) -> int:
    """Print the snapshot `coscc.loop --state` reads for workspace `target` (board name or path).

    A store not imported yet is imported first. Workspaces are named as `Workspaces.peer_table`
    names them: a shared name, or one `valid_name` refuses, gets none.
    """
    import json

    from coscc import units
    from coscc.config import from_env
    from coscc.store.db import Data
    from coscc.units.meta import MetaError, UnitMeta

    config = from_env()
    if not config.working_dir:
        print(
            "coscc: state needs COS_WORKING_DIR — the database keys every unit by it",
            file=sys.stderr,
        )
        return 2
    data = Data(config.data_dir)
    names, wanted = _workspace(config, data, target)
    if wanted is None:
        return 2
    meta = UnitMeta(config.working_dir, data)
    try:
        for key in {wanted, *names.values()}:
            store = units.root(key, config.data_dir)
            if (store / units.COS_DIR).is_dir():
                meta.import_store(key, store)
    except MetaError as e:
        print(f"coscc: {e}", file=sys.stderr)
        return 1
    print(json.dumps(meta.snapshot(wanted, names), ensure_ascii=False))
    return 0


def _workspace(config, data, target: str) -> tuple[dict[str, str], str | None]:
    """Every workspace by board name (as `Workspaces.peer_table`), and the key of `target`, or `None` (stderr says so)."""
    from collections import Counter
    from pathlib import Path

    from coscc import units
    from coscc.store.workspaces import valid_name

    with data.connect() as conn:
        rows = [
            (str(r["name"]), str(Path(r["root"]) / r["name"]))
            for r in conn.execute("SELECT root, name FROM workspaces")
        ]
    rows += [(Path(p).name, p) for p in config.workspaces]
    count = Counter(name for name, _ in rows)
    names = {name: units.key(path) for name, path in rows if count[name] == 1 and valid_name(name)}
    wanted = names.get(target) or next(
        (units.key(p) for _, p in rows if units.key(p) == units.key(target)), None
    )
    if wanted is None:
        print(
            f"coscc: no workspace named {target!r} — one of {', '.join(sorted(names)) or 'none'}",
            file=sys.stderr,
        )
    return names, wanted


SKIP_USAGE = "usage: coscc skip <workspace> <unit> spec <reason>"


def _skip(args: list[str]) -> int:
    """Record a person's decision to skip a unit's spec.

    Shell-only, so no session can make it. It writes the transition through guard
    `skip-decision` and no file. Only `spec`: the unit machine has no `skipped` for `plan.md`.
    """
    rest = list(args)
    reason = " ".join(rest[3:]).strip()
    if len(rest) < 4 or rest[2] != "spec" or not reason:
        print(f"coscc: {SKIP_USAGE}", file=sys.stderr)
        return 2
    target, unit = rest[0], rest[1]

    from coscc import units
    from coscc.config import from_env
    from coscc.store.db import Data
    from coscc.store.journal import Journal
    from coscc.units import transitions
    from coscc.units.history import BadTransition
    from coscc.units.meta import MetaError, UnitMeta

    config = from_env()
    if not config.working_dir:
        print(
            "coscc: skip needs COS_WORKING_DIR — the database keys every unit by it",
            file=sys.stderr,
        )
        return 2
    data = Data(config.data_dir)
    _, wanted = _workspace(config, data, target)
    if wanted is None:
        return 2
    meta = UnitMeta(config.working_dir, data)
    try:
        store = units.root(wanted, config.data_dir)
        if (store / units.COS_DIR).is_dir():
            meta.import_store(wanted, store)
    except MetaError as e:
        print(f"coscc: {e}", file=sys.stderr)
        return 1
    with data.connect() as conn:
        known = conn.execute(
            "SELECT 1 FROM unit_meta WHERE root = ? AND workspace = ? AND unit = ?",
            (meta.root, wanted, unit),
        ).fetchone()
    if known is None:
        print(f"coscc: the app knows no unit {unit!r} in {target!r}", file=sys.stderr)
        return 2
    authority = "person"
    try:
        applied = transitions.apply(
            meta.history,
            Journal(meta.root, config.data_dir),
            machine="unit",
            transition="skip",
            workspace=wanted,
            unit=unit,
            artifact="spec.md",
            to_state="skipped",
            inputs={"authority": authority, "reason": reason},
            authority=authority,
            actor="human:terminal",
            source="cli:skip",
        )
    except BadTransition as e:
        print(f"coscc: {e}", file=sys.stderr)
        return 1
    if not applied.open:
        print(
            f"coscc: guard {applied.guard} refused the skip: {', '.join(applied.reasons)}",
            file=sys.stderr,
        )
        return 1
    print(f"coscc: {unit} spec.md skipped by {authority} — {reason}")
    return 0


def banner(config) -> list[str]:
    """What is printed at startup, as lines.

    Off loopback the warning is printed every time: coscc serves plain HTTP, so the
    password, session cookie and setup token cross the network readable.
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
    lines.append(f"working folder: {config.working_dir or '(unset — workspace management off)'}")
    lines.append(f"env workspaces: {', '.join(config.workspaces) or '(none)'}")
    lines.append(f"tools: {config.effective_tools() or 'none (chat only)'}")
    return lines


if __name__ == "__main__":
    main()
