"""`0090` plan step 7: `baseline` and `measure`, on a `cos.db` built by the app's own schema.

The records are written with the field names `coscc/runner.py` and `coscc/gather.py` use
(`knowledge`, `mode`, `gather.KIND`), so a rename there turns this red (plan Risk 6).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from coscc import gather, knowledge, measure, units
from coscc.data import Data

WS = "/x/coscc"
SLOT = units.slot(WS)
ELSEWHERE = "/y/other"


def day(n: int, hour: int = 12) -> str:
    """A time `n` days after 2026-09-01, in UTC."""
    from datetime import datetime, timedelta, timezone

    return (datetime(2026, 9, 1, hour, tzinfo=timezone.utc) + timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = Path(self._tmp.name)
        self.db = Data(self.data)
        with self.db.connect():
            pass
        self.rows: list[tuple] = []
        self.said: list[str] = []

    def add(self, kind: str, at: str, workspace: str = WS, unit: str = "", stage: str = "", **record) -> None:
        record = {"kind": kind, "at": at, "workspace": workspace, "unit": unit, "stage": stage, **record}
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO runs (at, root, workspace, unit, stage, kind, record) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (at, "/x", workspace, unit, stage, kind, json.dumps(record)),
            )

    def a_unit(self, name: str, n: int, entries: int | None = None, cost: float | None = 1.0,
               spike: bool = False, verdicts: list[str] | None = None, workspace: str = WS) -> None:
        """One unit whose spec, (spike,) plan ran on day `n`; `entries` `None` is the flag off."""
        extra = {} if entries is None else {"knowledge": {"version": "v", "entries": entries, "bytes": 9}}
        stages = ["spec"] + (["spike"] if spike else []) + ["plan"]
        for i, stage in enumerate(stages):
            self.add("start", day(n, 8 + i), workspace, name, stage, **extra)
            end = {"outcome": "done"} | ({"cost_usd": cost} if cost is not None else {})
            self.add("end", day(n, 8 + i), workspace, name, stage, **end)
        if verdicts is not None:
            self.add("end", day(n, 20), workspace, name, "review", outcome="done", verdicts=verdicts)

    def run_baseline(self, wanted=None) -> int:
        return measure.run_baseline(str(self.data), wanted, self.said.append)

    def run_measure(self, wanted=None) -> int:
        return measure.run_measure(str(self.data), wanted, self.said.append)

    def result(self) -> dict:
        self.assertEqual(self.run_measure(), 0, self.said)
        return json.loads(self.said[-1])


class TheBaseline(Fixture):
    def test_it_takes_the_last_ten_units_that_ran_with_the_flag_off(self):
        for n in range(12):
            self.a_unit(f"{n:04d}_u", n)
        self.a_unit("0100_on", 13, entries=2)
        self.a_unit("0200_else", 14, workspace=ELSEWHERE)
        self.assertEqual(self.run_baseline(), 0)
        written = json.loads((knowledge.path_of(str(self.data)) / knowledge.BASELINE).read_text())
        self.assertEqual(written["workspace"], SLOT)
        self.assertEqual([u["unit"] for u in written["units"]], [f"{n:04d}_u" for n in range(2, 12)])
        self.assertEqual(written["n"], 10)

    def test_it_is_written_with_fewer_and_never_overwritten(self):
        self.a_unit("0001_u", 1)
        self.assertEqual(self.run_baseline(), 0)
        self.assertEqual(json.loads((knowledge.path_of(str(self.data)) / knowledge.BASELINE).read_text())["n"], 1)
        self.assertEqual(self.run_baseline(), 2)

    def test_an_unknown_or_ambiguous_workspace_is_refused(self):
        self.a_unit("0001_u", 1, workspace=ELSEWHERE)
        self.assertEqual(self.run_baseline(), 2)
        self.assertIn(units.slot(ELSEWHERE), self.said[-1])
        self.assertEqual(self.run_baseline("nope-000000000000"), 2)


class TheMeasurement(Fixture):
    def baseline_now(self) -> None:
        self.assertEqual(self.run_baseline(), 0)

    def test_it_refuses_with_no_baseline(self):
        self.assertEqual(self.run_measure(), 2)
        self.assertIn("no baseline", self.said[-1])

    def test_it_refuses_a_baseline_written_after_the_flag_went_on(self):
        self.a_unit("0001_u", 1)
        self.baseline_now()
        path = knowledge.path_of(str(self.data)) / knowledge.BASELINE
        record = json.loads(path.read_text())
        record["written_at"] = day(5)
        path.write_text(json.dumps(record))
        self.a_unit("0002_on", 3, entries=1)
        self.assertEqual(self.run_measure(), 2)
        self.assertIn("before the flag is turned on", self.said[-1])

    def test_a_mixed_unit_is_left_out_and_listed(self):
        self.a_unit("0001_u", 1)
        self.baseline_now()
        self.add("start", day(40, 8), WS, "0002_mixed", "spec")
        self.add("start", day(40, 9), WS, "0002_mixed", "plan", knowledge={"version": "v", "entries": 1, "bytes": 1})
        self.add("end", day(40, 9), WS, "0002_mixed", "plan", outcome="done", cost_usd=1.0)
        got = self.result()
        self.assertEqual(got["mixed"], ["0002_mixed"])
        self.assertEqual(got["on"]["units"], [])

    def test_reruns_count_and_unknown_costs_are_counted_apart(self):
        self.a_unit("0001_u", 1)
        self.add("end", day(1, 23), WS, "0001_u", "plan", outcome="failed", cost_usd=0.5)
        self.add("end", day(1, 23), WS, "0001_u", "spec", outcome="failed")
        self.baseline_now()
        off = self.result()["off"]
        self.assertEqual((off["cost_usd"], off["cost_unknown"]), (2.5, 1))

    def test_the_backfill_is_not_in_the_ratio_and_gathering_in_the_window_is(self):
        for n in range(10):
            self.a_unit(f"{n:04d}_off", n, cost=1.0, verdicts=["changes-requested", "pass"])
        self.baseline_now()
        for n in range(10):
            self.a_unit(f"{n + 100:04d}_on", 30 + n, entries=3, cost=0.5, verdicts=["pass"])
        self.add(gather.KIND, day(29), "", mode="all", cost_usd=40.0)
        self.add(gather.KIND, day(35), "", mode="new", cost_usd=2.0)
        self.add(gather.KIND, day(50), "", mode="new", cost_usd=99.0)  # after the window
        got = self.result()
        self.assertEqual((got["off"]["n"], got["on"]["n"]), (10, 10))
        self.assertEqual(got["reduction"], 0.5)
        self.assertEqual(got["gather_cost_usd"], 2.0)
        self.assertEqual(got["on"]["cost_per_unit"], 1.0)
        self.assertEqual(got["reduction_with_gather"], round(1 - 1.2 / 2.0, 4))
        self.assertEqual(got["backfill_cost_usd"], 40.0)
        self.assertEqual(got["break_even_units"], round(40.0 / 0.8, 2))
        self.assertEqual(got["verdict"], measure.PASS)

    def test_a_verdict_for_each_branch(self):
        for n in range(10):
            self.a_unit(f"{n:04d}_off", n, cost=1.0, verdicts=["changes-requested"])
        self.baseline_now()
        # Nine units: not enough.
        for n in range(9):
            self.a_unit(f"{n + 100:04d}_on", 30 + n, entries=1, cost=0.7, verdicts=["pass"])
        self.assertEqual(self.result()["verdict"], measure.SHORT)
        # Ten, 30% cheaper, and no more changes requested: it passes.
        self.a_unit("0109_on", 39, entries=1, cost=0.7, verdicts=["pass"])
        self.assertEqual(self.result()["verdict"], measure.PASS)

    def test_cheaper_but_more_changes_requested_fails(self):
        for n in range(10):
            self.a_unit(f"{n:04d}_off", n, cost=1.0, verdicts=["pass"])
        self.baseline_now()
        for n in range(10):
            self.a_unit(f"{n + 100:04d}_on", 30 + n, entries=1, cost=0.5, verdicts=["changes-requested", "pass"])
        got = self.result()
        self.assertEqual(got["verdict"], measure.FAIL)

    def test_not_cheap_enough_fails_and_has_no_break_even(self):
        for n in range(10):
            self.a_unit(f"{n:04d}_off", n, cost=1.0, verdicts=["pass"])
        self.baseline_now()
        for n in range(10):
            self.a_unit(f"{n + 100:04d}_on", 30 + n, entries=1, cost=1.0, verdicts=["pass"])
        got = self.result()
        self.assertEqual(got["verdict"], measure.FAIL)
        self.assertIsNone(got["break_even_units"])
        self.assertIn("saves nothing", got["break_even_reason"])

    def test_no_reviewed_unit_on_a_side_is_not_enough(self):
        for n in range(10):
            self.a_unit(f"{n:04d}_off", n, cost=1.0)
        self.baseline_now()
        for n in range(10):
            self.a_unit(f"{n + 100:04d}_on", 30 + n, entries=1, cost=0.1, verdicts=["pass"])
        got = self.result()
        self.assertEqual((got["off"]["review"]["n"], got["verdict"]), (0, measure.SHORT))

    def test_a_plan_done_after_the_deadline_is_not_counted(self):
        self.a_unit("0001_u", 1)
        self.baseline_now()
        self.a_unit("0002_late", 46, entries=1)  # 2026-10-17
        self.a_unit("0003_in_time", 45, entries=1)  # 2026-10-16
        got = self.result()
        self.assertEqual(got["on"]["units"], ["0003_in_time"])
        self.assertEqual((got["deadline"], got["timezone"]), (measure.DEADLINE, "UTC"))

    def test_the_database_is_not_written(self):
        self.a_unit("0001_u", 1)
        self.baseline_now()
        db = self.db.db_path
        before = (hashlib.sha256(db.read_bytes()).hexdigest(), os.stat(db).st_mtime_ns)
        self.result()
        self.assertEqual((hashlib.sha256(db.read_bytes()).hexdigest(), os.stat(db).st_mtime_ns), before)


if __name__ == "__main__":
    unittest.main()
