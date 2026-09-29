"""`coscc knowledge ...`: the terminal's way into the knowledge store.

Reached only from `coscc/run.py`; no route, button or autopilot pass reaches it, so
`gather --yes`, the one command that spends quota, needs a shell. `show` and `measure` spend
nothing, fetch `origin/main` first and write only `knowledge.HEALTH`.

Exit codes: 0 done, 1 failed with a reason, 2 misuse or refused.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from typing import Callable

from coscc import knowledge
from coscc.knowledge import admit, gather, measure

USAGE = (
    "usage: coscc knowledge gather [--all] [--yes] [--model M]\n"
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
    """Print the store's entries by scope, each checked on a fresh `origin/main` (saved to
    `knowledge.HEALTH`), and per workspace how many apply, pass and would be carried.
    `1` an entry is broken or a fetch failed, `2` the store, run log or git cannot be read."""
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
        return 2
    db = measure._db(data_dir)
    try:
        if not db.is_file():
            raise FileNotFoundError(str(db))
        rows = [r for r in measure.read_rows(db) if r.get("kind") == "end"]
    except (OSError, sqlite3.Error) as e:
        say(f"the run log cannot be read: {type(e).__name__}: {e}")
        return 2
    if not admit.git_runs():
        say("git cannot be run")
        return 2
    parsed = knowledge.parse(text)
    entries = parsed["entries"]
    h = parsed["header"]
    say(f"Version: {h['version']}. Gathered: {h['gathered']}. Max id: K{h['max_id']}.")
    say(f"entries: {len(entries)}" + (f", unreadable blocks: {len(parsed['skipped'])}" if parsed["skipped"] else ""))
    scopes: dict[str, int] = {}
    for e in entries:
        scopes[e["scope"]] = scopes.get(e["scope"], 0) + 1
    for scope, n in sorted(scopes.items()):
        say(f"  {scope}: {n}")

    paths = admit.workspaces(rows)
    wanted = sorted({e["scope"][len("workspace:"):] for e in entries if e["scope"].startswith("workspace:")})
    fetched = {slot: paths[slot] for slot in wanted if slot in paths} if wanted else dict(paths)
    try:
        shas = asyncio.run(_fresh(fetched))
    except admit.GitError as e:
        say(f"coscc knowledge show: nothing was checked, and {knowledge.HEALTH} is as it was: {e}")
        return 1
    try:
        found = admit.health(entries, fetched, admit.Reader("origin/main"))
    except admit.GitError as e:
        say(f"coscc knowledge show: {e}")
        return 2
    admit.save_health(data_dir, shas, found)
    for why in parsed["skipped"]:
        say(f"{why}: fail: cannot be checked")
    for e in entries:
        verdict = found[f"K{e['id']}"]
        say(f"K{e['id']} {e['scope']}: " + (f"fail: {verdict}" if verdict else "pass"))
    good = [e for e in entries if not found[f"K{e['id']}"]]
    for s in sorted({s["slot"] for s in gather.sources(data_dir)} | set(fetched)):
        mine = knowledge.for_workspace(good, s)
        broken = len(knowledge.for_workspace(entries, s)) - len(mine)
        _, record, _ = knowledge._slice(mine)
        on = f"origin/main {shas[s][:12]}" if s in shas else "no origin/main fetched"
        say(f"{s}: {len(mine)} apply ({broken} broken, not counted), {len(mine)} pass on {on}, "
            f"{record['entries']} carried ({record['bytes']}/{knowledge.CAP_BYTES} bytes)")
    return 1 if parsed["skipped"] or len(good) < len(entries) else 0


async def _fresh(paths: dict[str, str]) -> dict[str, str]:
    return {slot: await admit.fresh_main(path) for slot, path in sorted(paths.items())}


def _gather(config, opts: dict[str, str | bool], say: Callable[[str], None]) -> int:
    mode = "all" if opts.get("--all") else "new"
    if not config.working_dir:
        # Like Jera: a batch that spent and could not be recorded is money nobody can count.
        say("coscc knowledge gather: no working folder is set, so nothing it spends can be recorded — set COS_WORKING_DIR")
        return 2
    from coscc.runlog.journal import Journal

    # Before the plan, so a dry run refuses an unreadable run log or git too.
    journal = Journal(config.working_dir, config.data_dir)
    try:
        planned = gather.plan_of(config.data_dir, mode, journal)
    except gather.Refused as e:
        say(f"coscc knowledge gather: {e}")
        return 2
    except ValueError as e:
        say(f"coscc knowledge gather: {e}")
        return 1
    n, parts = len(planned["sources"]), len(planned["batches"])
    if planned["resumed"]:
        say(f"an unfinished --all ({knowledge.path_of(config.data_dir) / gather.PROGRESS}): "
            f"{planned['passed']} source(s) passed; {parts} batch(es) left, at most ${planned['ceiling_usd']:.2f}")
    say(f"{n} source(s) in {parts} batch(es); at most ${planned['ceiling_usd']:.2f}, {parts} × the "
        "knowledge grant's ceiling, which is checked after each turn, so a batch can pass it")
    if not opts.get("--yes"):
        if parts:
            say("nothing was run: add --yes to open " + ("one paid session" if parts == 1 else f"{parts} paid sessions")
                + ", and more to repair a refused reply while the total stays under that figure")
        elif planned["resumed"]:
            say("nothing was run: add --yes to finish it, which opens no session")
        return 0
    if not parts and not planned["resumed"]:
        return 0
    from coscc.agent.sessions import Sessions

    model = opts.get("--model") or config.model
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
