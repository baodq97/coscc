"""`0135` R10, measured once: is reading a unit through the database slower than through its files?

On copies, as `verify_0135.py` makes them (plan Risk 1), for the largest store: 20 rounds,
each timing, by the wall clock and in alternating order, the four calls. Each side is the
lines the app runs, on a `Service` over the copies (review F2: the after side once skipped
`Service._snapshot`'s `imported` checks):

- `board.read` before: what `Service.board` did on `main` — `_peer_table()`, then `cos.mjs`
  at `git merge-base HEAD origin/main` with a `--peer` for each. `board._source`, the one
  place a call's `--state` is added, is patched to hand those instead, so both sides run the
  same Python and differ only in the script and what it is handed;
- `board.read` after: what `Service.board` does now — `_peer_table()`, `_snapshot(cwd,
  peers=…)`, then this checkout's `cos.mjs --state -`;
- one `next` as the autopilot asks it first (`Service.next_step`, no `--repo`), for the
  store's last unit: before, `_peers()` and the old script; after, `_snapshot(cwd, [unit])`
  and the new one.

Prints each median and whether after ≤ before. Exit 0 only when both hold. Every store is
imported once before the first round, as the app imports it on its first read; the snapshot
is built inside the timed "after" call, because the app builds it on every read.

    uv run python scripts/measure_0135_reads.py [--data ~/.cos] [--rounds 20]
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from coscc import units  # noqa: E402
from coscc.agent import harness  # noqa: E402
from coscc.agent.sessions import Sessions  # noqa: E402
from coscc.config import Config  # noqa: E402
from coscc.data import Data  # noqa: E402
from coscc.service import Service  # noqa: E402
from coscc.service.store import valid_name  # noqa: E402
from coscc.units import board  # noqa: E402


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True).stdout


def timed(work) -> float:
    start = time.perf_counter()
    asyncio.run(work())
    return time.perf_counter() - start


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", default=str(Path("~/.cos").expanduser()))
    parser.add_argument("--rounds", type=int, default=20)
    args = parser.parse_args()
    source = Path(args.data).expanduser()

    tmp = Path(tempfile.mkdtemp(prefix="measure_0135-"))
    try:
        data_dir = tmp / "data"
        (data_dir / "units").mkdir(parents=True)
        with sqlite3.connect(f"file:{source / 'cos.db'}?mode=ro", uri=True) as src, \
                sqlite3.connect(data_dir / "cos.db") as dst:
            src.backup(dst)
        for store in sorted((source / "units").iterdir()):
            if store.is_dir():
                shutil.copytree(store, data_dir / "units" / store.name, symlinks=True)
        base = git("merge-base", "HEAD", "origin/main").strip()
        shutil.copytree(REPO / ".claude", tmp / "before" / ".claude")
        old = tmp / "before" / ".claude" / "scripts" / "cos.mjs"
        old.write_text(git("show", f"{base}:.claude/scripts/cos.mjs"), encoding="utf-8")
        new = harness.script()

        data = Data(data_dir)
        with data.connect() as conn:
            rows = [(str(r["root"]), str(r["name"])) for r in conn.execute("SELECT root, name FROM workspaces")]
        names = [n for _, n in rows]
        paths = {n: str(Path(r) / n) for r, n in rows if names.count(n) == 1 and valid_name(n)}
        stores = {n: units.root(p, data_dir) for n, p in paths.items() if (units.root(p, data_dir) / ".cos").is_dir()}
        name = max(stores, key=lambda n: sum(1 for _ in (stores[n] / ".cos").iterdir()))
        store, cwd = stores[name], paths[name]
        root = next(r for r, n in rows if n == name)
        config = Config(workspaces=(), working_dir=root, data_dir=str(data_dir))
        svc = Service(config, Sessions(config))
        svc._snapshot(cwd)  # every store's import, once, before any round
        unit = sorted(d.name for d in (store / ".cos").iterdir() if d.is_dir() and d.name != "ideas")[-1]

        def with_peers(peers):
            """`main`'s `board._peer_args`: each `(name, store root)` as a `--peer`."""
            args = [a for n, s in peers for a in ("--peer", f"{n}={s}")]
            return mock.patch.multiple(board, _source=lambda state: (args, None))

        async def before_read():
            peers, _ = svc._peer_table()
            with mock.patch.object(harness, "script", return_value=old), \
                    with_peers([(n, svc._units_root(p)) for n, p in peers]):
                return await board.read(svc._units_root(cwd))

        async def after_read():
            peers, _ = svc._peer_table()
            return await board.read(svc._units_root(cwd), state=svc._snapshot(cwd, peers=peers))

        async def before_next():
            with mock.patch.object(harness, "script", return_value=old), with_peers(svc._peers()):
                return await board.next_step(svc._units_root(cwd), unit, repo=None)

        async def after_next():
            return await board.next_step(svc._units_root(cwd), unit, repo=None, state=svc._snapshot(cwd, [unit]))

        pairs = {"board.read": (before_read, after_read), f"next {unit}": (before_next, after_next)}
        times: dict[str, dict[str, list[float]]] = {k: {"before": [], "after": []} for k in pairs}
        for i in range(args.rounds):
            for label, (b, a) in pairs.items():
                order = (("before", b), ("after", a)) if i % 2 == 0 else (("after", a), ("before", b))
                for which, work in order:
                    times[label][which].append(timed(work))

        ok = True
        print(f"store: {name} ({sum(1 for _ in (store / '.cos').iterdir())} entries), rounds: {args.rounds}, script now: {new}")
        for label, t in times.items():
            b, a = statistics.median(t["before"]), statistics.median(t["after"])
            holds = a <= b
            ok = ok and holds
            print(f"{label}: median before {b * 1000:.1f} ms, after {a * 1000:.1f} ms — {'holds' if holds else 'fails'}")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
