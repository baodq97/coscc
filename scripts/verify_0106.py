"""`0106` proof: was every fully answered draft on the shortlist run again in time?

The intent's outcome (R7): by 2026-10-31, no `answer` record that finished a `draft`'s
questions — of a shortlisted unit, with the autopilot on — was followed by nothing, by a
run later than the next pass (300 s, or 600 s after a `full` stop), by the day's cap or by
another stop. A held unit, a closed gate and a draft already run again twice are excluded.
With no such answer at all the outcome is not measured, which is not met.

No session, no quota, no network, and nothing written. It opens `<COS_DATA_DIR>/cos.db`
(default `~/.cos`) with `mode=ro`, never through `Data`, and hands every record of the
workspace to `coscc.autopilot.measure_reruns`. `--workspace` is the journal key: the
workspace's resolved path, as the run log names it. `--since` defaults to the day of the
workspace's first `answer` record; both days are the machine's: run it on the machine the
app runs on, and at a terminal — inside a step `COS_DATA_DIR` is that step's scratch root
(`0076`).

A case the exit code counts as missed may still be one the intent excludes: a hold placed
after the answer leaves no record (spec C7). Read each missed line, not only the code.

    uv run python scripts/verify_0106.py --workspace <path> --until 2026-10-31

Exit codes: 0 met, 1 a case missed, 2 no `cos.db` or a bad argument, 3 no case to measure.
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

from coscc import autopilot, spend  # noqa: E402

UNTIL = "2026-10-31"


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
    parser.add_argument("--since")
    parser.add_argument("--until", default=UNTIL)
    args = parser.parse_args(argv)
    try:
        until = date.fromisoformat(args.until).isoformat()
        since = date.fromisoformat(args.since).isoformat() if args.since else None
    except ValueError as e:
        print(f"--since and --until must be YYYY-MM-DD: {e}", file=sys.stderr)
        return 2
    root = Path(os.environ.get("COS_DATA_DIR") or Path.home() / ".cos").expanduser()
    db = root / "cos.db"
    if not db.is_file():
        print(f"no run log at {db}", file=sys.stderr)
        return 2
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        records = _records(conn, args.workspace)
    finally:
        conn.close()
    if since is None:
        first = next((r for r in records if r.get("kind") == "answer"), None)
        if first is None:
            print(f"0 cases: no answer record in {args.workspace} yet, so nothing is measured")
            return 3
        since = spend.local_day(first.get("at"))
    found = autopilot.measure_reruns(records, args.workspace, since, until)
    for c in found["cases"]:
        after = "" if c["after"] is None else f" after {c['after']:.0f}s"
        print(f"{c['class']} {c['unit']} {c['artifact']} question {c['question']} at {c['at']}{after}")
    print(f"{len(found['cases'])} cases, {since}..{until}")
    if found["met"] is None:
        return 3
    return 0 if found["met"] else 1


if __name__ == "__main__":
    sys.exit(main())
