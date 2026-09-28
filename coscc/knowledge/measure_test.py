"""`0131` plan step 8: `measure`, the two arms of the store, on a `cos.db` built by the app's own schema.

The records are written with the field names `coscc/runner/__init__.py` and `coscc/knowledge/gather.py` use
(`knowledge.TRIAL_FIELD`, `efforttrial.CI_RED`, `mode`, `gather.KIND`), so a rename there turns
this red (plan Risk 8).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from coscc import knowledge, units
from coscc.data import Data
from coscc.knowledge import efforttrial, gather, measure
from coscc.knowledge.admit_test import failing_fetch, lock, make_repo, no_fetch

ELSEWHERE = "/y/other"


def day(n: int, hour: int = 12) -> str:
    """A time `n` days after 2026-09-01, in UTC."""
    from datetime import datetime, timedelta, timezone

    return (datetime(2026, 9, 1, hour, tzinfo=timezone.utc) + timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.data = self.root / "data"
        self.db = Data(self.data)
        with self.db.connect():
            pass
        # A real repository, named so `choose` picks it, whose `origin/main` a `run_measure` reads.
        self.ws = str(make_repo(self.root / "coscc", ("2026-09-20T00:00:00+00:00", {
            ".python-version": "3.14\n", "uv.lock": lock(reflex="0.9.12"), "a.py": "def f():\n    pass\n"})))
        self.slot = units.slot(self.ws)
        self.said: list[str] = []

    def add(self, kind: str, at: str, workspace: str | None = None, unit: str = "", stage: str = "", **record) -> None:
        workspace = self.ws if workspace is None else workspace
        record = {"kind": kind, "at": at, "workspace": workspace, "unit": unit, "stage": stage, **record}
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO runs (at, root, workspace, unit, stage, kind, record) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (at, "/x", workspace, unit, stage, kind, json.dumps(record)),
            )

    def a_unit(self, name: str, n: int, arm: str | None = knowledge.ON, turns: int = 10, cost: float = 1.0,
               verdicts: tuple[str, ...] = ("pass",), red: bool = False, ship: int | None = None) -> None:
        """One unit from `spec` to `ship` on day `n`: every `end` is `turns` turns and `cost`
        dollars; `red` adds a second `impl` CI sent back. `arm` `None` is the flag off."""
        trial = {} if arm is None else {knowledge.TRIAL_FIELD: {"arm": arm}}
        stages = ["spec", "plan", "impl"] + (["impl"] if red else []) + ["review", "ship"]
        runs = 0
        for i, stage in enumerate(stages):
            at = day(ship if stage == "ship" and ship is not None else n, 8 + i)
            extra = dict(trial)
            if stage == "impl":
                runs += 1
                extra["impl_run"] = runs
                if runs > 1:
                    extra[efforttrial.CI_RED] = True
            self.add("start", at, None, name, stage, **extra)
            end = {"outcome": "done", "turns": turns, "cost_usd": cost}
            if stage == "review":
                end["verdicts"] = list(verdicts)
            self.add("end", at, None, name, stage, **end)

    def side(self, prefix: str, arm: str, count: int, first: int = 0, **kw) -> None:
        for k in range(count):
            self.a_unit(f"{first + k:04d}_{prefix}", 1 + k, arm, **kw)

    def result(self) -> dict:
        return measure.measure(measure.read_rows(self.db.db_path), self.slot, today="2026-10-01")


class WhoCounts(Fixture):
    def test_a_unit_counts_only_when_every_start_carries_one_arm(self):
        self.a_unit("0001_on", 1)
        self.a_unit("0002_off", 1, knowledge.OFF)
        self.a_unit("0003_before", 1, arm=None)
        # One `start` from before the flag, the rest on: condition 1.
        self.a_unit("0004_mixed", 2)
        self.add("start", day(2, 7), None, "0004_mixed", "idea")
        # Two arms: condition 1 too.
        self.a_unit("0005_both", 2)
        self.add("start", day(2, 23), None, "0005_both", "pr", **{knowledge.TRIAL_FIELD: {"arm": knowledge.OFF}})
        # No `turns` on an `end`: condition 3.
        self.a_unit("0006_uncounted", 2)
        self.add("end", day(2, 23), None, "0006_uncounted", "pr", outcome="failed", cost_usd=0.1)
        got = self.result()
        self.assertEqual((got["on"]["units"], got["off"]["units"]), (["0001_on"], ["0002_off"]))
        self.assertEqual(got["excluded"], [{"unit": "0004_mixed", "failed": [1]}, {"unit": "0005_both", "failed": [1]},
                                           {"unit": "0006_uncounted", "failed": [3]}])
        # Before the flag: in no arm, and not listed.
        self.assertNotIn("0003_before", json.dumps(got))

    def test_a_unit_shipped_after_the_deadline_is_excluded_with_its_condition(self):
        self.a_unit("0001_in_time", 1, ship=45)  # 2026-10-16
        self.a_unit("0002_late", 1, ship=46)  # 2026-10-17
        self.a_unit("0003_unshipped", 1)
        self.add("end", day(1, 23), None, "0003_unshipped", "ship", outcome="failed", turns=1, cost_usd=0.1)
        found = measure.read_rows(self.db.db_path)
        found = [r for r in found if not (r.get("unit") == "0003_unshipped" and r.get("outcome") == "done"
                                         and r.get("stage") == "ship")]
        got = measure.measure(found, self.slot)
        self.assertEqual(got["on"]["units"], ["0001_in_time"])
        self.assertEqual(got["excluded"], [{"unit": "0002_late", "failed": [2]}, {"unit": "0003_unshipped", "failed": [2]}])
        self.assertEqual((got["deadline"], got["timezone"]), ("2026-10-16", "UTC"))

    def test_every_valid_unit_counts_with_no_cut_at_ten(self):
        self.side("on", knowledge.ON, 12)
        got = self.result()
        self.assertEqual(got["on"]["n"], 12)

    def test_the_crosstab_sets_the_knowledge_arm_against_the_effort_arm_of_the_name(self):
        self.side("on", knowledge.ON, 6)
        self.side("off", knowledge.OFF, 4, first=100)
        got = self.result()["crosstab"]
        for arm, count in ((knowledge.ON, 6), (knowledge.OFF, 4)):
            self.assertEqual(sum(got[arm].values()), count)
        names = [f"{k:04d}_on" for k in range(6)]
        self.assertEqual(got[knowledge.ON][efforttrial.TRIAL_ARM],
                         sum(1 for n in names if efforttrial.arm(n) == efforttrial.TRIAL_ARM))


class TheVerdict(Fixture):
    def test_verdict_is_short_under_ten_a_side(self):
        self.side("on", knowledge.ON, 9, turns=1)
        self.side("off", knowledge.OFF, 12, first=100, turns=10)
        got = self.result()
        self.assertEqual((got["on"]["n"], got["off"]["n"], got["verdict"]), (9, 12, measure.SHORT))

    def test_verdict_passes_at_twenty_percent_fewer_turns_with_no_more_cost_rounds_or_red_ci(self):
        # 5 `end`s a unit: 40 turns on, 50 off, a reduction of exactly 0.20.
        self.side("on", knowledge.ON, 10, turns=8, cost=0.9)
        self.side("off", knowledge.OFF, 10, first=100, turns=10, cost=1.0)
        got = self.result()
        self.assertEqual((got["on"]["median"]["turns"], got["off"]["median"]["turns"]), (40, 50))
        self.assertEqual(got["reduction"], 0.2)
        self.assertEqual(got["verdict"], measure.PASS)

    def test_more_changes_requested_or_red_ci_on_the_on_arm_fails(self):
        for extra in ({"verdicts": ("changes-requested", "pass")}, {"red": True}):
            with self.subTest(**{k: str(v) for k, v in extra.items()}):
                self.setUp()
                self.side("on", knowledge.ON, 10, turns=5, cost=0.5, **extra)
                self.side("off", knowledge.OFF, 10, first=100, turns=10, cost=1.0)
                self.assertEqual(self.result()["verdict"], measure.FAIL)

    def test_verdict_fails_when_the_on_arm_costs_more(self):
        self.side("on", knowledge.ON, 10, turns=5, cost=1.1)
        self.side("off", knowledge.OFF, 10, first=100, turns=10, cost=1.0)
        got = self.result()
        self.assertEqual(got["reduction"], 0.5)
        self.assertEqual(got["verdict"], measure.FAIL)

    def test_gather_cost_is_added_to_the_on_arm(self):
        # 5 `end`s at $0.18: $0.90 a unit on, $1.00 off. $2.00 of gathering in the window over
        # 10 `on` units is $0.20 each, which takes the `on` arm past the `off` one.
        self.side("on", knowledge.ON, 10, turns=5, cost=0.18)
        self.side("off", knowledge.OFF, 10, first=100, turns=10, cost=0.2)
        self.add(gather.KIND, day(3), "", mode="unit", cost_usd=1.5)
        self.add(gather.KIND, day(4), "", mode="new", cost_usd=0.5)
        self.add(gather.KIND, day(5), "", mode="all", cost_usd=40.0)  # a backfill is not the arm's
        self.add(gather.KIND, day(60), "", mode="unit", cost_usd=99.0)  # after the window
        got = self.result()
        self.assertEqual((got["gather_cost_usd"], got["gather_per_on_unit"]), (2.0, 0.2))
        self.assertEqual(got["usd_on_with_gather"], 1.1)
        self.assertEqual(got["verdict"], measure.FAIL)


class RunMeasure(Fixture):
    def run_measure(self, wanted=None) -> int:
        return measure.run_measure(str(self.data), wanted, self.said.append)

    def test_it_fetches_checks_the_store_and_prints_both(self):
        self.a_unit("0001_on", 1)
        text = ("# Knowledge\nVersion: 1. Gathered: x. Max id: K2.\n\n"
                f"## K1\nScope: tool:reflex 0.9.11\nSource: {self.slot}/0001_on/spec.md ## R\nMeasured: 2026-09-25\nA.\n\n"
                f"## K2\nScope: workspace:{self.slot}\nSource: {self.slot}/0001_on/spec.md ## R\nRef: a.py::f\n"
                "Measured: 2026-09-25\nB.\n")
        knowledge.save(knowledge.path_of(str(self.data)) / knowledge.STORE, text)
        with no_fetch():
            self.assertEqual(self.run_measure(), 0, self.said)
        got = json.loads(self.said[-1])
        from coscc.knowledge import admit

        self.assertEqual(got["origin_main"], admit._git(self.ws, "rev-parse", "origin/main").strip())
        self.assertEqual(got["health"], {"entries": 2, "broken": {"K1": f"version 0.9.11, origin/main has 0.9.12 in {self.slot}"}})
        health = json.loads((knowledge.path_of(str(self.data)) / knowledge.HEALTH).read_text())
        self.assertEqual(health["sha"], {self.slot: got["origin_main"]})
        self.assertIn("verdict", got)

    def test_measure_exits_1_and_prints_no_verdict_when_the_fetch_fails(self):
        self.a_unit("0001_on", 1)
        with failing_fetch():
            self.assertEqual(self.run_measure(), 1)
        self.assertNotIn('"verdict"', "\n".join(self.said))
        self.assertIn("could not read from remote repository", self.said[-1])
        self.assertFalse((knowledge.path_of(str(self.data)) / knowledge.HEALTH).exists())

    def test_an_unknown_or_ambiguous_workspace_is_refused(self):
        self.add("end", day(1), ELSEWHERE, "0001_u", "spec", outcome="done")
        self.assertEqual(self.run_measure("nope-000000000000"), 2)
        self.assertIn(units.slot(ELSEWHERE), self.said[-1])

    def test_the_database_is_not_written(self):
        self.a_unit("0001_on", 1)
        db = self.db.db_path
        before = (hashlib.sha256(db.read_bytes()).hexdigest(), os.stat(db).st_mtime_ns)
        with no_fetch():
            self.assertEqual(self.run_measure(), 0, self.said)
        self.assertEqual((hashlib.sha256(db.read_bytes()).hexdigest(), os.stat(db).st_mtime_ns), before)


if __name__ == "__main__":
    unittest.main()
