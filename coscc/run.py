"""Starting the app: one process, one port, the FastAPI app behind the login guard.

The studio is built into the package (`coscc/_studio/`, `coscc/studio.py`), so the process
needs no Node and nothing to rebuild; uvicorn binds the app to the host in `config.py`.
"""

from __future__ import annotations

import logging
import sys

# Standard library only.
from coscc import update
from coscc.config import LOOPBACK

log = logging.getLogger(__name__)


# Longer than the studio's stream lasts (`api.STREAM_LIFETIME_SECONDS`), so a stop lets it end
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
    from coscc import api
    from coscc.auth import Guard
    from coscc.config import from_env
    from coscc.store.db import Data

    config = from_env()
    return Guard(api.build(config, starting=True), Data(config.data_dir))


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
    if args[0] == "vault-measure":
        raise SystemExit(_vault_measure(args[1:]))
    print(
        f"coscc: unrecognised argument {args[0]!r}\n"
        "usage: coscc [--version | reset-password | state <workspace> | skip <workspace> <unit> spec <reason> | vault-measure <workspace> [--since ISO] [--until ISO]]\n"
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


VAULT_MEASURE_USAGE = "usage: coscc vault-measure <workspace> [--since ISO] [--until ISO]"

# The outcome the vault was built for: this many secrets used, through this many programs, and
# not one value found anywhere it must not be. Chosen with the owner, not measured.
MEASURE_SECRETS = 10
MEASURE_TOOLS = 3

# Bounds one GET route, so a stream that never ends does not hold the measure.
ROUTE_WAIT = 10.0


def measure_passes(secrets: int, tools: int, hits: int) -> bool:
    return secrets >= MEASURE_SECRETS and tools >= MEASURE_TOOLS and hits == 0


def _route_bodies(config, cwd: str) -> list[tuple[str, bytes]]:
    """The body of every GET route of the board and its features, each called in this process with
    `cwd` as the query. A route that needs a path parameter, or that does not answer in
    `ROUTE_WAIT` seconds, is left out."""
    import asyncio

    import httpx

    from coscc import api, plugin

    app = api.build(config)
    plugin.create_tables(plugin.ctx_of(app.state.service), app.state.tables)
    paths = sorted(
        {
            path
            for r in app.routes
            if "GET" in (getattr(r, "methods", None) or ())
            and "{" not in (path := str(getattr(r, "path", "")))
        }
    )

    async def fetch() -> list[tuple[str, bytes]]:
        out = []
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            for path in paths:
                try:
                    got = await asyncio.wait_for(client.get(path, params={"cwd": cwd}), ROUTE_WAIT)
                except TimeoutError, httpx.HTTPError:
                    continue
                out.append((f"GET {path}", got.content))
        return out

    return asyncio.run(fetch())


def _measure_sources(config, journal, key: str, units_seen: list[str]):
    """Everything a value could be found in, for the steps of `units_seen`: their transcripts, run
    log, artifacts, commits and pull request, then the routes."""
    from coscc import units, vault
    from coscc.units import worktrees

    sources: list[tuple[str, bytes]] = []
    for unit in units_seen:
        try:
            directory = units.unit_dir(key, unit, config.data_dir)
            tree = str(worktrees.path(key, unit, config.data_dir))
        except units.BadUnit:
            continue
        found = vault.unit_sources(journal, key, unit, directory, tree, pull_request=True)
        sources += [(f"{unit}/{where}", body) for where, body in found]
    return sources + _route_bodies(config, key)


def _vault_measure(args: list[str]) -> int:
    """Decrypt a workspace's secrets, look for them in everything the steps in a window wrote and
    every GET route, and count the secrets and programs the run log shows in use (refused uses do
    not count). It writes the result, by names only, under `<data root>/measurements/` and passes
    with `measure_passes`.

    A step is in the window when a run-log line of it is; a bound is an ISO date or time, compared
    as far as it goes.
    """
    import json
    from datetime import datetime, timezone

    from coscc import vault
    from coscc.config import from_env
    from coscc.store.db import Data
    from coscc.store.journal import Journal

    bounds: dict[str, str] = {"--since": "", "--until": ""}
    rest: list[str] = []
    i = 0
    while i < len(args):
        if args[i] in bounds and i + 1 < len(args):
            bounds[args[i]] = args[i + 1]
            i += 2
            continue
        rest.append(args[i])
        i += 1
    if len(rest) != 1:
        print(f"coscc: {VAULT_MEASURE_USAGE}", file=sys.stderr)
        return 2
    since, until = bounds.get("--since", ""), bounds.get("--until", "")

    config = from_env()
    if not config.working_dir:
        print("coscc: vault-measure needs COS_WORKING_DIR", file=sys.stderr)
        return 2
    data = Data(config.data_dir)
    _, key = _workspace(config, data, rest[0])
    if key is None:
        return 2
    journal = Journal(config.working_dir, data)
    records = [
        r
        for r in journal.records(key)
        if str(r.get("at", ""))[: len(since)] >= since
        and (not until or str(r.get("at", ""))[: len(until)] <= until)
    ]
    uses = [
        r
        for r in records
        if r.get("kind") == vault.KIND
        and r.get("action") == "use"
        and not r.get("codes")
        and not r.get("refused")
    ]
    names = sorted({n for r in uses for n in r.get("names", [])})
    tools = sorted({p for r in uses for p in r.get("programs", [])})
    try:
        values = vault.Store(data, config.config_home, config.home).values_for(key)
    except vault.BadSecret as e:
        print(f"coscc: the secrets could not be read: {e}", file=sys.stderr)
        return 1
    seen = sorted({str(r["unit"]) for r in records if r.get("unit")})
    sources = _measure_sources(config, journal, key, seen)
    hits = vault.scan(values, sources)
    ok = measure_passes(len(names), len(tools), len(hits))

    now = datetime.now(timezone.utc)
    result = {
        "workspace": rest[0],
        "at": now.isoformat(timespec="seconds"),
        "since": since,
        "until": until,
        "secrets_used": names,
        "tools_used": tools,
        "secrets_visible": sorted(values),
        "sources_read": len(sources),
        "hits": [{"name": h.name, "where": h.where, "form": h.form} for h in hits],
        "pass": ok,
    }
    out = data.root / "measurements"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"vault-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        f"coscc: vault-measure {rest[0]}: {len(names)} secrets used, {len(tools)} tools, "
        f"{len(hits)} hits in {len(sources)} sources: {'pass' if ok else 'fail'}"
    )
    print(f"coscc: written {path}")
    return 0 if ok else 1


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
