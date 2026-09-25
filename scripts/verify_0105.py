"""`0105` proof: on a day with integrations and a failure, did the autopilot still run, under the cap?

The intent's outcome: by 2026-09-30, on at least one day, one workspace had at least 5
integrations and at least one failed or exhausted step, the autopilot started at least one
step there, and the day's spend — known costs, and each unknown one at its grant's ceiling —
stayed within the daily cap.

No session, no quota, no network, and nothing written. It opens `<COS_DATA_DIR>/cos.db`
(default `~/.cos`) with `mode=ro`, never through `Data`, reads every record of the run log —
every workspace, since the cap is the app's — and hands them to
`coscc.autopilot.measure_days`, which sums a day's spend with `spent_on`, the cap's own
sum. `--workspace` is the journal key: the workspace's resolved path, as the run log names
it. Days are the machine's, as the cap counts them; each line also prints that day's bounds
in UTC, since the intent counts UTC days. Run it on the machine the app runs on, and at a
terminal — inside a step `COS_DATA_DIR` is that step's scratch root (`0076`).

The cap it compares with is the one set *now* (`prefs.autopilot_daily_cap_usd`, or
`autopilot.DEFAULT_DAILY_CAP_USD`), not the one that was set on that day.

    uv run python scripts/verify_0105.py --workspace <path> --since 2026-09-25 --until 2026-09-30

Exit codes: 0 at least one day met, 1 none, 2 no `cos.db` or a bad argument.
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

CAP_PREF = "autopilot_daily_cap_usd"


def _records(conn: sqlite3.Connection) -> list[dict]:
    out = []
    for (text,) in conn.execute("SELECT record FROM runs ORDER BY id"):
        try:
            record = json.loads(text)
        except ValueError:
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


def _cap(conn: sqlite3.Connection) -> float:
    row = conn.execute("SELECT value FROM prefs WHERE key = ?", (CAP_PREF,)).fetchone()
    try:
        value = json.loads(row[0]) if row else None
    except ValueError:
        value = None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return autopilot.DEFAULT_DAILY_CAP_USD


def _yes(flag: bool) -> str:
    return "yes" if flag else "no"


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
        records, cap = _records(conn), _cap(conn)
    finally:
        conn.close()
    days = autopilot.measure_days(records, args.workspace, since, until, cap)
    for d in days:
        print(
            f"{d['day']}: {'met' if d['met'] else 'not met'}; "
            f"{d['integrations']} integrations ({_yes(d['enough_integrations'])}), "
            f"{d['failed']} failed ({_yes(d['a_failure'])}), "
            f"{d['autopilot_starts']} autopilot starts ({_yes(d['ran'])}), "
            f"spent {d['spent']:.2f} of it {d['estimated']:.2f} estimated, cap {d['cap']:.2f} "
            f"(within: {_yes(d['within'])}); UTC {d['utc_from']} .. {d['utc_to']}"
        )
    met = [d["day"] for d in days if d["met"]]
    print(f"{len(met)} day(s) met, {since}..{until}")
    return 0 if met else 1


if __name__ == "__main__":
    sys.exit(main())
