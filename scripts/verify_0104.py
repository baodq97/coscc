"""`0104` proof: did the autopilot start ten steps in the shortlist's order, and none outside it?

The intent's outcome (R8): by 2026-10-02, the autopilot started at least 10 steps in one
workspace, and not one of them broke the shortlist's order — V1 a pick of a unit not on its
shortlist, V2 a unit's first pick passing over one ranked above it with no known reason, V3
a step with no pick of its unit and stage since that unit's previous step.

No session, no quota, no network, and nothing written. It opens `<COS_DATA_DIR>/cos.db`
(default `~/.cos`) with `mode=ro`, never through `Data`, and hands every record of the
workspace to `coscc.autopilot.measure_order`. `--workspace` is the journal key: the
workspace's resolved path, as the run log names it. The window opens at the workspace's
first `autopilot-pick` and closes at the end of `--until` on the machine's clock: run it on
the machine the app runs on, and at a terminal — inside a step `COS_DATA_DIR` is that step's
scratch root (`0076`).

Each pick is judged against the shortlist it recorded, so a step started before the
shortlist was edited is measured against the old one. The reasons are the app's own
(`spec.md` C3): this checks the app kept to what it wrote, not that what it wrote was true.

    uv run python scripts/verify_0104.py --workspace <path> --until 2026-10-02

Exit codes: 0 at least 10 steps and no violation, 1 otherwise, 2 no `cos.db` or a bad argument.
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

NEEDED = 10
UNTIL = "2026-10-02"


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
    parser.add_argument("--until", default=UNTIL)
    args = parser.parse_args(argv)
    try:
        until = date.fromisoformat(args.until).isoformat()
    except ValueError as e:
        print(f"--until must be YYYY-MM-DD: {e}", file=sys.stderr)
        return 2
    root = Path(os.environ.get("COS_DATA_DIR") or Path.home() / ".cos").expanduser()
    db = root / "cos.db"
    if not db.is_file():
        print(f"no run log at {db}", file=sys.stderr)
        return 2
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        found = autopilot.measure_order(_records(conn, args.workspace), args.workspace, until)
    finally:
        conn.close()
    for v in found["violations"]:
        print(f"{v['v']} {v['unit']} {v['stage']} at {v['at']}: {v['why']}")
    since = found["since"] or "no autopilot-pick yet"
    print(
        f"{found['steps']} of {NEEDED} steps needed, {len(found['violations'])} violations, "
        f"{since}..{until}"
    )
    return 0 if found["steps"] >= NEEDED and not found["violations"] else 1


if __name__ == "__main__":
    sys.exit(main())
