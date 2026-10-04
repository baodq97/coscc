"""Which accepted spec or plan a decision or `main` came after, read from run-log rows alone."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from coscc.service import outdated
from tests.git import test_drift

A, B = "a" * 40, "b" * 40
SPEC = "# Spec: x\nStatus: accepted.\n\nWe change `src/a.py`.\n"


def decision(n: int, authority: str = "person") -> dict:
    return {"kind": "decision", "id": f"C{n}", "authority": authority}


def withdrawn(n: int) -> dict:
    return {"kind": "decision-withdrawn", "id": f"C{n}"}


def run(stage: str, outcome: str = "done", **start) -> list[dict]:
    return [
        {"kind": "start", "stage": stage, "head": A, **start},
        {"kind": "end", "stage": stage, "outcome": outcome},
    ]


class TheDecisionsAndWhatTookThem(unittest.TestCase):
    def test_a_withdrawn_decision_is_not_live(self):
        rows = [decision(1), decision(2, "agent"), withdrawn(1)]
        self.assertEqual(outdated.live_decisions(rows), [{"id": "C2", "authority": "agent"}])

    def test_only_a_done_run_of_that_stage_takes_its_decisions(self):
        rows = [
            *run("spec", decisions=["C1"]),
            *run("spec", "failed", decisions=["C2"]),
            *run("plan", decisions=["C3"]),
        ]
        self.assertEqual(outdated.taken(rows, "spec"), {"C1"})

    def test_a_spec_or_plan_start_carries_the_live_decisions_and_the_cause(self):
        cause = outdated.cause_of(
            {"decisions": [{"id": "C2", "authority": "agent"}], "main": unchecked()}
        )
        self.assertEqual(
            outdated.start_fields([decision(2, "agent")], "plan", cause),
            {"decisions": ["C2"], "cause": cause},
        )
        self.assertEqual(outdated.start_fields([decision(1)], "spec", None), {"decisions": ["C1"]})
        self.assertIsNone(outdated.start_fields([decision(1)], "impl", None))

    def test_the_cause_names_main_only_when_it_was_checked_and_moved(self):
        moved = {**unchecked(), "checked": True, "from_sha": A, "main_sha": B, "paths": ["x"]}
        self.assertEqual(
            outdated.cause_of({"decisions": [], "main": moved}),
            {"kinds": ["main"], "decisions": [], "from_sha": A, "main_sha": B, "paths": ["x"]},
        )
        self.assertIsNone(outdated.cause_of({"decisions": [], "main": unchecked()}))
        self.assertIsNone(outdated.cause_of(None))


def unchecked() -> dict:
    return {
        "stage": "spec",
        "from_sha": None,
        "main_sha": None,
        "paths": None,
        "checked": False,
        "reason": "x",
    }


class OneUnit(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        (self.dir / "spec.md").write_text(SPEC, encoding="utf-8")
        (self.dir / "plan.md").write_text(SPEC.replace("Spec", "Plan"), encoding="utf-8")

    def of(self, rows, statuses=None) -> dict:
        statuses = statuses or {"spec.md": "accepted", "plan.md": "accepted"}
        arts = {f: {"status": s} for f, s in statuses.items()}
        return asyncio.run(outdated.of_unit(rows, arts, self.dir, None))

    def test_a_decision_no_done_run_took_makes_each_accepted_stage_outdated(self):
        rows = [*run("spec", decisions=["C1"]), decision(1), decision(2)]
        found = self.of(rows, {"spec.md": "accepted", "plan.md": "draft"})
        self.assertEqual(list(found), ["spec"])
        self.assertEqual(found["spec"]["decisions"], [{"id": "C2", "authority": "person"}])

    def test_a_check_of_main_that_could_not_be_made_makes_nothing_outdated(self):
        self.assertEqual(self.of(run("spec") + run("plan")), {})

    def test_no_decision_rows_is_no_decision(self):
        # The feature that writes them is gone, or never wrote one: no error, nothing outdated.
        self.assertEqual(self.of([]), {})


class APersonsRerunOnABranchNotRebased(unittest.TestCase):
    """A person runs an outdated spec again on a branch cut before main moved: its `start`
    carries the main it was handed, so the stage is no longer outdated, rebased or not."""

    setUp = test_drift.ComputingOnARealRepository.setUp
    _git = test_drift.ComputingOnARealRepository._git
    _merge = test_drift.ComputingOnARealRepository._merge

    def of(self, rows) -> dict:
        (self.repo / "spec.md").write_text(SPEC, encoding="utf-8")
        arts = {"spec.md": {"status": "accepted"}}
        return asyncio.run(outdated.of_unit(rows, arts, self.repo, str(self.repo)))

    def test_the_rerun_clears_what_main_changed(self):
        self._git("checkout", "-q", "-b", "unit")
        (self.repo / "own.txt").write_text("mine\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-qm", "own")
        own = self._git("rev-parse", "HEAD").strip()
        self._git("checkout", "-q", "main")
        b = self._merge("src/a.py")
        self._git("checkout", "-q", "unit")
        first = [{"kind": "start", "stage": "spec", "head": own}, *run("spec")[1:]]
        entry = self.of(first)["spec"]
        self.assertEqual(entry["main"]["paths"], ["src/a.py"])
        cause = outdated.cause_of(entry)
        fields = outdated.start_fields(first, "spec", cause)
        self.assertEqual((fields["cause"]["from_sha"], fields["cause"]["main_sha"]), (self.a, b))
        again = {"kind": "start", "stage": "spec", "head": own, "started_by": "person", **fields}
        self.assertEqual(self.of([*first, again, *run("spec")[1:]]), {})


if __name__ == "__main__":
    unittest.main()
