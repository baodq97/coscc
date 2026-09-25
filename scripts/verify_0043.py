"""`0043` proof: did the autopilot carry three units to the stop before `ship` with nobody pressing?

The intent's outcome (R11): by 2026-10-31, at least 3 units of one workspace went from an
accepted `intent` to the stop before `ship`, and every `start` on the way that is not at one
of R6's stops carries `started_by: autopilot`.

No session, no quota, no network, and nothing written. It opens `<COS_DATA_DIR>/cos.db`
(default `~/.cos`) with `mode=ro`, never through `Data`, and hands every record of the
workspace to `coscc.autopilot.measure`. `--workspace` is the journal key: the workspace's
resolved path, as the run log names it. Days are the machine's, as the cap counts them:
run it on the machine the app runs on, and at a terminal — inside a step `COS_DATA_DIR` is
that step's scratch root (`0076`).

A unit counts when a `review` step ended `done` with a `pass` round, at least one start on
the way was the autopilot's, and no person's start was outside a stop. A person's start is
at a stop when the unit's last `autopilot-stop` record before it names one, or the unit's
last `end` before it was not `done`. A record written before `0043` has no `started_by` and
reads as `person`.

    uv run python scripts/verify_0043.py --workspace <path> --since 2026-09-25 --until 2026-10-31

Exit codes: 0 at least 3 units count, 1 fewer, 2 no `cos.db` or a bad argument.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coscc import autopilot  # noqa: E402

NEEDED = 3


def _records(conn: sqlite3.Connection, workspace: str) -> list[dict]:
    out = []
    for (text,) in conn.execute("SELECT record FROM runs WHERE workspace = ? ORDER BY id", (workspace,)):
        try:
            record = json.loads(text)
        except ValueError:
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    args = parser.parse_args(argv)
    try:
        since, until = date.fromisoformat(args.since).isoformat(), date.fromisoformat(args.until).isoformat()
    except ValueError as e:
        print(f"a date must be YYYY-MM-DD: {e}", file=sys.stderr)
        return 2
    root = Path(os.environ.get("COS_DATA_DIR") or Path.home() / ".cos").expanduser()
    db = root / "cos.db"
    if not db.is_file():
        print(f"no run log at {db}", file=sys.stderr)
        return 2
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        found = autopilot.measure(_records(conn, args.workspace), args.workspace, since, until)
    finally:
        conn.close()
    for u in found["units"]:
        mark = "counts" if u["unit"] in found["met"] else ("reached" if u["reached"] else "not reached")
        print(f"{u['unit']}: {mark}; {u['autopilot']} started by the autopilot, {u['person']} by a person")
        for o in u["outside"]:
            print(f"  outside a stop: {o['kind']} {o['stage']} at {o['at']}")
    print(f"{len(found['met'])} of {NEEDED} units needed, {since}..{until}")
    return 0 if len(found["met"]) >= NEEDED else 1


if __name__ == "__main__":
    sys.exit(main())
