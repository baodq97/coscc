"""`0123` plan step 7: `coscc effort measure`, on a `cos.db` built by the app's own schema.

The records are written with the field names `coscc/runner.py` writes, `effort_trial` and
`ci_red` by `coscc/knowledge/efforttrial.py`'s constants, so a rename there turns this red (plan Risk 5).
"""

from __future__ import annotations

import ast
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from coscc import units
from coscc.knowledge import effort_measure, efforttrial
from coscc.data import Data
from coscc.knowledge.cli_test import imported

REPO = Path(__file__).resolve().parents[2]
WS = "/x/coscc"
SLOT = units.slot(WS)
TRIAL, CONTROL = efforttrial.TRIAL_ARM, efforttrial.CONTROL_ARM
UNSET = object()


def day(n: int, hour: int = 12) -> str:
    """A time `n` days after 2026-09-01, in UTC; `day(90)` is the deadline."""
    return (datetime(2026, 9, 1, hour, tzinfo=timezone.utc) + timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = Path(self._tmp.name)
        self.db = Data(self.data)
        with self.db.connect():
            pass
        self.said: list[str] = []

    def add(self, kind: str, at: str, unit: str, stage: str, workspace: str = WS, **record) -> None:
        record = {"kind": kind, "at": at, "workspace": workspace, "unit": unit, "stage": stage, **record}
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO runs (at, root, workspace, unit, stage, kind, record) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (at, "/x", workspace, unit, stage, kind, json.dumps(record)),
            )

    def impl(self, unit: str, n: int, arm: str | None, run: int = 1, turns: object = 100, cost: object = 4.0,
             label: str = "routine", source: str = "declared", declared: str = "routine",
             applied: bool | None = None, ci_red: object = UNSET, model: str = "claude-opus-5-5[1m]",
             ended: bool = True, workspace: str = WS) -> None:
        """One `impl` run, `start` and `end`, as `Runner.run` writes them."""
        start = {
            "model": model, "model_source": "override", "label_declared": declared,
            "label": label, "label_source": source, "impl_run": run,
        }
        if arm is not None:
            if applied is None:
                applied = arm == TRIAL and label == "routine"
            start[efforttrial.FIELD] = {"arm": arm, "applied": applied}
            if run > 1:
                start[efforttrial.CI_RED] = False if ci_red is UNSET else ci_red
        self.add("start", day(n, 8 + run), unit, "impl", workspace, **start)
        if ended:
            end = {"outcome": "done"}
            if turns is not None:
                end["turns"] = turns
            if cost is not None:
                end["cost_usd"] = cost
            self.add("end", day(n, 8 + run), unit, "impl", workspace, **end)

    def ship(self, unit: str, n: int, verdicts: list[str] = (), workspace: str = WS) -> None:
        if verdicts:
            self.add("end", day(n, 18), unit, "review", workspace, outcome="done", verdicts=list(verdicts))
        self.add("end", day(n, 20), unit, "ship", workspace, outcome="done")

    def a_unit(self, unit: str, n: int, arm: str, turns: int = 100, cost: float = 4.0,
               changes_requested: int = 0, reds: int = 0) -> None:
        """A unit shipped on day `n`: one run of `turns`, then one return per red CI."""
        self.impl(unit, n, arm, turns=turns, cost=cost)
        for i in range(reds):
            self.impl(unit, n, arm, run=2 + i, turns=0, cost=0.0, ci_red=True)
        self.ship(unit, n, ["changes-requested"] * changes_requested + ["pass"])

    def ten_a_side(self, trial: dict | None = None, control: dict | None = None, start: int = 0) -> None:
        for i in range(effort_measure.GROUP):
            self.a_unit(f"{i:04d}_t", start + i, TRIAL, **(trial or {}))
            self.a_unit(f"{100 + i:04d}_c", start + i, CONTROL, **(control or {}))

    def rows(self) -> list[dict]:
        return effort_measure.reader.read_rows(self.db.db_path)

    def result(self, today: str = "2026-10-01") -> dict:
        return effort_measure.measure(self.rows(), SLOT, today=today)


class TheVerdict(Fixture):
    def test_ten_units_a_side_with_fewer_turns_and_no_more_cost_pass(self):
        # 80 against 100 is exactly the 20% of `intent.md ## Answers, câu 1`.
        self.ten_a_side(trial={"turns": 80, "cost": 4.0}, control={"turns": 100, "cost": 4.0})
        got = self.result()
        self.assertEqual((got["trial"]["n"], got["control"]["n"]), (10, 10))
        self.assertEqual(got["reduction"], 0.2)
        self.assertEqual(got["trial"]["median"], {"turns": 80, "usd": 4.0, "changes_requested": 0, "ci_red": 0})
        self.assertEqual(got["trial"]["models"], ["claude-opus-5-5[1m]"])
        self.assertEqual(got["verdict"], effort_measure.PASS)

    def test_a_reduction_below_twenty_percent_fails(self):
        self.ten_a_side(trial={"turns": 81}, control={"turns": 100})
        got = self.result()
        self.assertEqual(got["reduction"], 0.19)
        self.assertEqual(got["verdict"], effort_measure.FAIL)

    def test_fewer_turns_at_a_higher_cost_fails(self):
        self.ten_a_side(trial={"turns": 50, "cost": 4.5}, control={"turns": 100, "cost": 4.0})
        self.assertEqual(self.result()["verdict"], effort_measure.FAIL)

    def test_more_changes_requested_rounds_fail(self):
        self.ten_a_side(trial={"turns": 50, "changes_requested": 1}, control={"turns": 100})
        got = self.result()
        self.assertEqual(got["trial"]["median"]["changes_requested"], 1)
        self.assertEqual(got["verdict"], effort_measure.FAIL)

    def test_more_red_ci_returns_fail(self):
        self.ten_a_side(trial={"turns": 50, "reds": 1}, control={"turns": 100})
        got = self.result()
        self.assertEqual(got["trial"]["median"]["ci_red"], 1)
        self.assertEqual(got["excluded"], [])
        self.assertEqual(got["verdict"], effort_measure.FAIL)

    def test_nine_units_in_one_arm_is_short(self):
        self.ten_a_side(trial={"turns": 50}, control={"turns": 100})
        self.add("end", day(0, 21), "0000_t", "impl", outcome="done")  # no turns: now excluded
        got = self.result()
        self.assertEqual((got["trial"]["n"], got["control"]["n"]), (9, 10))
        self.assertEqual(got["verdict"], effort_measure.SHORT)

    def test_a_control_median_of_nothing_fails(self):
        self.ten_a_side(trial={"turns": 0}, control={"turns": 0})
        got = self.result()
        self.assertIsNone(got["reduction"])
        self.assertEqual(got["verdict"], effort_measure.FAIL)


class WhichUnitsCount(Fixture):
    def test_units_shipped_after_the_deadline_do_not_count(self):
        self.ten_a_side(trial={"turns": 50}, control={"turns": 100}, start=81)  # days 81-90
        self.a_unit("0050_late", 91, TRIAL, turns=1)
        got = self.result(today="2026-12-01")
        self.assertNotIn("0050_late", got["trial"]["units"])
        self.assertNotIn("0050_late", [e["unit"] for e in got["excluded"]])
        self.assertEqual(got["trial"]["n"], 10)
        self.assertTrue(got["past_deadline"])
        self.assertFalse(self.result(today="2026-11-30")["past_deadline"])
        self.assertEqual((got["deadline"], got["timezone"]), ("2026-11-30", "UTC"))

    def test_the_first_ten_by_ship_time_are_taken(self):
        # Named against the order they shipped in, so a sort by name would take the wrong ten.
        for i in range(12):
            self.a_unit(f"{99 - i:04d}_t", i, TRIAL)
        got = self.result()
        self.assertEqual(got["trial"]["units"], [f"{99 - i:04d}_t" for i in range(10)])

    def test_an_escalated_run_counts_its_novel_turns(self):
        self.impl("0001_e", 0, TRIAL, turns=120, cost=5.0)
        self.impl("0001_e", 0, TRIAL, run=2, turns=30, cost=2.0, label="novel", source="escalated")
        self.ship("0001_e", 0)
        got = self.result()
        self.assertEqual(got["excluded"], [])
        self.assertEqual(got["trial"]["units"], ["0001_e"])
        self.assertEqual((got["trial"]["median"]["turns"], got["trial"]["median"]["usd"]), (150, 7.0))

    def test_a_unit_from_before_the_flag_is_neither_counted_nor_listed(self):
        self.impl("0001_old", 0, None)
        self.ship("0001_old", 0)
        got = self.result()
        self.assertEqual((got["trial"]["n"], got["control"]["n"], got["excluded"]), (0, 0, []))


class EachConditionOfR9(Fixture):
    def excluded(self) -> dict[str, list[int]]:
        return {e["unit"]: e["failed"] for e in self.result()["excluded"]}

    def test_a_unit_whose_starts_disagree_on_the_arm_is_excluded(self):
        self.impl("0001_x", 0, TRIAL)
        self.impl("0001_x", 0, CONTROL, run=2)
        self.ship("0001_x", 0)
        self.impl("0002_y", 0, None)
        self.impl("0002_y", 0, CONTROL, run=2)
        self.ship("0002_y", 0)
        self.assertEqual(self.excluded(), {"0001_x": [1], "0002_y": [1]})

    def test_a_forced_or_missing_label_is_excluded(self):
        self.impl("0001_f", 0, TRIAL, label="novel", source="forced")
        self.ship("0001_f", 0)
        self.impl("0002_m", 0, CONTROL, declared="missing", label="novel", source="missing")
        self.ship("0002_m", 0)
        self.impl("0003_n", 0, CONTROL, declared="novel", label="novel")
        self.ship("0003_n", 0)
        self.assertEqual(self.excluded(), {"0001_f": [2], "0002_m": [2], "0003_n": [2]})

    def test_a_trial_routine_start_not_applied_is_excluded(self):
        self.impl("0001_o", 0, TRIAL, applied=False)
        self.ship("0001_o", 0)
        # Not applied in `control` is what `control` is.
        self.impl("0002_c", 0, CONTROL, applied=False)
        self.ship("0002_c", 0)
        self.assertEqual(self.excluded(), {"0001_o": [3]})

    def test_an_impl_end_without_turns_or_cost_is_excluded(self):
        self.impl("0001_t", 0, TRIAL, turns=None)
        self.ship("0001_t", 0)
        self.impl("0002_c", 0, TRIAL, cost=None)
        self.ship("0002_c", 0)
        self.impl("0003_n", 0, TRIAL, ended=False)
        self.ship("0003_n", 0)
        self.assertEqual(self.excluded(), {"0001_t": [4], "0002_c": [4], "0003_n": [4]})

    def test_a_return_with_ci_red_null_is_excluded(self):
        self.impl("0001_r", 0, TRIAL)
        self.impl("0001_r", 0, TRIAL, run=2, ci_red=None)
        self.ship("0001_r", 0)
        self.assertEqual(self.excluded(), {"0001_r": [5]})

    def test_a_model_change_mid_unit_is_excluded(self):
        self.impl("0001_m", 0, TRIAL)
        self.impl("0001_m", 0, TRIAL, run=2, model="claude-sonnet-5[1m]")
        self.ship("0001_m", 0)
        self.assertEqual(self.excluded(), {"0001_m": [6]})

    def test_every_condition_failed_is_listed(self):
        self.impl("0001_a", 0, TRIAL, label="novel", source="forced", turns=None)
        self.impl("0001_a", 0, CONTROL, run=2, ci_red=None, model="other")
        self.ship("0001_a", 0)
        self.assertEqual(self.excluded(), {"0001_a": [1, 2, 4, 5, 6]})


class TheCommand(Fixture):
    def test_it_prints_json_and_exits_zero(self):
        self.ten_a_side(trial={"turns": 50}, control={"turns": 100})
        self.assertEqual(effort_measure.run(str(self.data), None, self.said.append), 0)
        got = json.loads(self.said[-1])
        self.assertEqual(got["workspace"], SLOT)
        self.assertEqual(got["verdict"], effort_measure.PASS)

    def test_no_cos_db_and_an_ambiguous_workspace_exit_two(self):
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(effort_measure.run(empty, None, self.said.append), 2)
            self.assertIn("no ", self.said[-1])
        self.impl("0001_a", 0, TRIAL)
        self.impl("0002_b", 0, TRIAL, workspace="/z/coscc")
        self.assertEqual(effort_measure.run(str(self.data), None, self.said.append), 2)
        self.assertIn("--workspace", self.said[-1])
        self.assertEqual(effort_measure.run(str(self.data), "nowhere", self.said.append), 2)
        self.assertEqual(effort_measure.run(str(self.data), SLOT, self.said.append), 0)

    def test_it_reads_read_only(self):
        self.ten_a_side()
        before = self.db.db_path.stat().st_mtime_ns
        self.assertEqual(effort_measure.run(str(self.data), None, self.said.append), 0)
        self.assertEqual(self.db.db_path.stat().st_mtime_ns, before)

    def test_anything_but_measure_is_misuse(self):
        for argv in ([], ["baseline"], ["measure", "--all"], ["measure", "--workspace"], ["measure", "x"]):
            with self.subTest(argv=argv):
                self.assertEqual(effort_measure.main(argv, self.said.append), 2)


class OnlyATerminalReachesIt(unittest.TestCase):
    """R8: no route, no autopilot pass and no service call imports the measurement. Read from
    the source, as `knowledge_cli_test.py` reads it for `coscc knowledge`."""

    MEASURE = "coscc.knowledge.effort_measure"

    def test_api_autopilot_and_service_do_not_import_it(self):
        split = [f"coscc/{p.name}" for p in sorted((REPO / "coscc").glob("service*.py")) if not p.name.endswith("_test.py")]
        self.assertTrue(split)
        for name in ("coscc/api.py", "coscc/units/autopilot.py", *split):
            with self.subTest(module=name):
                self.assertNotIn(self.MEASURE, imported(REPO / name))

    def test_it_imports_nothing_of_the_web_app(self):
        found = imported(REPO / "coscc/knowledge/effort_measure.py")
        self.assertFalse({n for n in found if n.startswith(("coscc.service", "coscc.state", "coscc.screens", "coscc.api"))}, found)

    def test_the_check_would_see_one(self):
        with tempfile.TemporaryDirectory() as d:
            probe = Path(d) / "probe.py"
            probe.write_text("from coscc.knowledge import effort_measure\n")
            self.assertIn(self.MEASURE, imported(probe))


if __name__ == "__main__":
    unittest.main()
