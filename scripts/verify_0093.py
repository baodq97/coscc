"""`0093` proof: the *Cost* screen's figures are the `runs` table's, within 1%, on a real `cos.db`.

No session, no quota, no network, and nothing written. It opens `<COS_DATA_DIR>/cos.db`
(default `~/.cos`) with `mode=ro`, never through `Data`, and imports only `coscc.spend` —
the model the page draws from. For each `(root, workspace)` holding an `end` row it reads
that workspace's records in `id` order (a row whose JSON will not parse is skipped, as
`Journal.records` skips it), builds the model, and runs the spec's reference queries
(`## Design` §5) for every unit, stage and local day, and for every unit by stage (R11).

It fails on a key the model has and SQL does not, on one SQL has and the model does not,
on a `NULL` read as a number or a number read as `NULL`, and on any figure more than 1% off.
Days are counted in this machine's zone, by both sides: run it on the machine the app runs
on. Run it at a terminal — inside a step `COS_DATA_DIR` is that step's scratch root (`0076`).

What it does not measure: that the page draws the model's figures (`coscc/state_test.py`
and the screenshots do), or Leif's own total, whose script was never committed (spec C1).

Exit codes: 0 every figure matches, 1 a mismatch (each printed), 2 no `cos.db`, no `end`
row, an `end` row whose JSON is broken (each printed), or a reference query SQLite refused.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coscc import spend  # noqa: E402

SUM = "SELECT SUM(json_extract(record, '$.cost_usd')) FROM runs WHERE root = ? AND workspace = ? AND kind = 'end'"
QUERIES = {
    "unit": (SUM + " AND unit = ?", "unit"),
    "stage": (SUM + " AND stage = ?", "stage"),
    "day": (SUM + " AND date(at, 'localtime') = ?", "date(at, 'localtime')"),
}
TOLERANCE = 0.01


def _off(model_usd: float | None, sql_usd: float | None) -> bool:
    if model_usd is None or sql_usd is None:
        return model_usd is not sql_usd
    return abs(model_usd - sql_usd) > TOLERANCE * abs(sql_usd)


def _records(conn: sqlite3.Connection, root: str, ws: str) -> list[dict]:
    out = []
    for (text,) in conn.execute("SELECT record FROM runs WHERE root = ? AND workspace = ? ORDER BY id", (root, ws)):
        try:
            record = json.loads(text)
        except ValueError:
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


def _check(conn: sqlite3.Connection, root: str, ws: str) -> tuple[int, list[str]]:
    model = spend.model(_records(conn, root, ws))
    rows = {"unit": model["by_unit"], "stage": model["by_stage"], "day": model["by_day"]}
    checked, wrong = 0, []
    for name, (query, column) in QUERIES.items():
        served = {r["key"]: r["usd"] for r in rows[name]}
        seen = {
            k for (k,) in conn.execute(
                f"SELECT DISTINCT {column} FROM runs WHERE root = ? AND workspace = ? AND kind = 'end'", (root, ws))
            if k is not None
        }
        for key in sorted(seen - set(served)):
            wrong.append(f"{name} {key!r}: in runs, not on the screen")
        for key in sorted(set(served) - seen):
            wrong.append(f"{name} {key!r}: on the screen, not in runs")
        for key in sorted(seen & set(served)):
            [(sql,)] = conn.execute(query, (root, ws, key)).fetchall()
            checked += 1
            if _off(served[key], sql):
                wrong.append(f"{name} {key!r}: screen {served[key]}, runs {sql}")
    for unit, stages in model["unit_stages"].items():
        for row in stages:
            [(sql,)] = conn.execute(SUM + " AND unit = ? AND stage = ?", (root, ws, unit, row["key"])).fetchall()
            checked += 1
            if _off(row["usd"], sql):
                wrong.append(f"unit {unit!r} stage {row['key']!r}: screen {row['usd']}, runs {sql}")
    return checked, wrong


def main() -> int:
    data_dir = Path(os.environ.get("COS_DATA_DIR") or "~/.cos").expanduser()
    db = data_dir / "cos.db"
    if not db.is_file():
        print(f"SKIP: no {db}")
        return 2
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        pairs = conn.execute(
            "SELECT DISTINCT root, workspace FROM runs WHERE kind = 'end' ORDER BY root, workspace"
        ).fetchall()
        if not pairs:
            print(f"SKIP: {db} holds no end row")
            return 2
        # An `end` whose JSON is broken is skipped by the model and summed by nobody: the two
        # sides would disagree about it without either being wrong, so it is said, not judged.
        broken = conn.execute(
            "SELECT id, workspace FROM runs WHERE kind = 'end' AND NOT json_valid(record) ORDER BY id"
        ).fetchall()
        if broken:
            print(f"SKIP: {len(broken)} end row(s) whose record is not JSON")
            for row_id, ws in broken:
                print(f"  runs.id {row_id} in {ws}")
            return 2
        failed = False
        for root, ws in pairs:
            try:
                checked, wrong = _check(conn, root, ws)
            except sqlite3.Error as e:
                print(f"SKIP: a reference query failed in {ws}: {e}")
                for row_id, kind in conn.execute(
                    "SELECT id, kind FROM runs WHERE root = ? AND workspace = ? AND NOT json_valid(record)", (root, ws)
                ):
                    print(f"  runs.id {row_id} ({kind}): its record is not JSON")
                return 2
            print(f"{'FAIL' if wrong else 'PASS'} {ws}: {checked} figures, {len(wrong)} off by more than 1%")
            for line in wrong:
                print(f"  {line}")
            failed = failed or bool(wrong)
        return 1 if failed else 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
