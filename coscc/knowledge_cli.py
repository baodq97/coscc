"""`coscc knowledge ...`: the terminal's only way into the knowledge store (`0090` R9).

Reached from `coscc/run.py` alone, imported there lazily, like `reset-password`. No route,
button or autopilot pass reaches this module (`knowledge_cli_test.py` holds that), so the
one command here that spends quota, `gather --yes`, needs a shell on this machine.

Exit codes: 0 done, 1 failed with a reason, 2 misuse or refused.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Callable

from coscc import gather, knowledge, measure

USAGE = (
    "usage: coscc knowledge gather [--all] [--yes] [--model M]\n"
    "       coscc knowledge baseline [--workspace SLOT]\n"
    "       coscc knowledge measure [--workspace SLOT]\n"
    "       coscc knowledge show"
)


class _Misuse(Exception):
    pass


def _options(args: list[str], flags: set[str], valued: set[str]) -> dict[str, str | bool]:
    out: dict[str, str | bool] = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a in flags and a not in out:
            out[a] = True
        elif a in valued and a not in out and i + 1 < len(args) and not args[i + 1].startswith("--"):
            out[a] = args[i + 1]
            i += 1
        else:
            raise _Misuse(f"unrecognised argument {a!r}")
        i += 1
    return out


def show(data_dir, say: Callable[[str], None]) -> int:
    """The store's path, header and entries by scope, and for each workspace the app knows
    how many entries apply and how many its prompt would carry (plan Risk 8)."""
    directory = knowledge.path_of(data_dir)
    path = directory / knowledge.STORE
    say(f"store: {path}")
    try:
        text = knowledge.load(path)
    except FileNotFoundError:
        say("no store yet: `coscc knowledge gather` writes one")
        return 0
    except (OSError, UnicodeDecodeError) as e:
        say(f"the store cannot be read: {type(e).__name__}: {e}")
        return 1
    parsed = knowledge.parse(text)
    h = parsed["header"]
    say(f"Version: {h['version']}. Gathered: {h['gathered']}. Max id: K{h['max_id']}.")
    say(f"entries: {len(parsed['entries'])}" + (f", unreadable blocks: {len(parsed['skipped'])}" if parsed["skipped"] else ""))
    scopes: dict[str, int] = {}
    for e in parsed["entries"]:
        scopes[e["scope"]] = scopes.get(e["scope"], 0) + 1
    for scope, n in sorted(scopes.items()):
        say(f"  {scope}: {n}")
    for s in sorted({s["slot"] for s in gather.sources(data_dir)}):
        applicable = len(knowledge.for_workspace(parsed["entries"], s))
        _, record = knowledge.slice_for(text, s)
        say(f"{s}: {applicable} apply, {record['entries']} carried ({record['bytes']}/{knowledge.CAP_BYTES} bytes)")
    return 0


def _gather(config, opts: dict[str, str | bool], say: Callable[[str], None]) -> int:
    mode = "all" if opts.get("--all") else "new"
    if not config.working_dir:
        # Like Jera: a batch that spent and could not be recorded is money nobody can count.
        say("coscc knowledge gather: no working folder is set, so nothing it spends can be recorded — set COS_WORKING_DIR")
        return 2
    try:
        planned = gather.plan_of(config.data_dir, mode)
    except gather.Refused as e:
        say(f"coscc knowledge gather: {e}")
        return 2
    except ValueError as e:
        say(f"coscc knowledge gather: {e}")
        return 1
    n, parts = len(planned["sources"]), len(planned["batches"])
    say(f"{n} source(s) in {parts} batch(es); at most ${planned['ceiling_usd']:.2f}, {parts} × the "
        "knowledge grant's ceiling, which is checked after each turn, so a batch can pass it")
    if not opts.get("--yes"):
        if parts:
            say("nothing was run: add --yes to open " + ("one paid session" if parts == 1 else f"{parts} paid sessions"))
        return 0
    if not parts:
        return 0
    from coscc.journal import Journal
    from coscc.sessions import Sessions

    model = opts.get("--model") or config.model
    journal = Journal(config.working_dir, config.data_dir)
    try:
        return asyncio.run(gather.gather(config.data_dir, journal, Sessions(config), model, mode, say))
    except gather.Refused as e:
        say(f"coscc knowledge gather: {e}")
        return 2


def main(argv: list[str], say: Callable[[str], None] | None = None) -> int:
    say = say or print
    from coscc.config import from_env

    try:
        if not argv:
            raise _Misuse("a command is needed")
        command, rest = argv[0], argv[1:]
        if command == "gather":
            opts = _options(rest, {"--all", "--yes"}, {"--model"})
            return _gather(from_env(), opts, say)
        if command == "baseline":
            opts = _options(rest, set(), {"--workspace"})
            return measure.run_baseline(from_env().data_dir, opts.get("--workspace") or None, say)
        if command == "measure":
            opts = _options(rest, set(), {"--workspace"})
            return measure.run_measure(from_env().data_dir, opts.get("--workspace") or None, say)
        if command == "show":
            _options(rest, set(), set())
            return show(from_env().data_dir, say)
        raise _Misuse(f"unrecognised command {command!r}")
    except _Misuse as e:
        print(f"coscc knowledge: {e}\n{USAGE}", file=sys.stderr)
        return 2
