"""Tests for what this process holds of a launched step (`Running`, `seal`) and for the attempt
that holds the unit (`coscc.runner.queue`): one per unit, the sentence a busy unit gets,
and what a Stop records."""

import tempfile
import unittest

from coscc.agent import steps
from coscc.agent.steps import Running
from coscc.bus import Bus
from coscc.runner import queue as attempts_mod
from coscc.runner.queue import Attempts
from coscc.runner.queue import Refused


def running(unit: str = "0001_a", stage: str = "spec") -> Running:
    return Running(workspace="w", unit=unit, stage=stage, started_at="2026-09-24T01:02:03+00:00")


class WithAttempts(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.bus = Bus()
        self.attempts = Attempts(self._tmp.name, self.bus)

    def listing(self, workspace: str):
        return [r for r in self.attempts.unfinished(workspace)]


class OneStepPerUnit(WithAttempts):
    def test_a_second_claim_on_the_same_unit_is_refused_and_names_the_first(self):
        self.attempts.open("step", "w", "0001_a", "spec", state="running")
        with self.assertRaises(Refused) as e:
            self.attempts.open("step", "w", "0001_a", "plan")
        self.assertIn("spec", str(e.exception))
        self.assertEqual(e.exception.reasons, ("unit-busy",))

    def test_two_units_run_at_once(self):
        self.attempts.open("step", "w", "0001_a", "spec", state="running")
        self.attempts.open("step", "w", "0002_b", "impl", state="running")
        self.assertEqual([x["unit"] for x in self.listing("w")], ["0001_a", "0002_b"])

    def test_the_same_unit_name_in_another_workspace_is_another_unit(self):
        self.attempts.open("step", "w", "0001_a", "spec", state="running")
        self.attempts.open("step", "v", "0001_a", "spec", state="running")
        self.assertEqual(len(self.listing("w")), 1)
        self.assertEqual(len(self.listing("v")), 1)

    def test_an_end_frees_the_unit(self):
        row = self.attempts.open("step", "w", "0001_a", "spec", state="running")
        self.attempts.move(row["id"], "ended", "done")
        self.assertIsNone(self.attempts.holding("w", "0001_a"))
        self.assertEqual(self.listing("w"), [])
        self.attempts.open("step", "w", "0001_a", "plan")

    def test_an_end_frees_only_that_attempt(self):
        old = self.attempts.open("step", "w", "0001_a", "spec", state="running")
        self.attempts.move(old["id"], "ended", "done")
        new = self.attempts.open("step", "w", "0001_a", "plan", state="running")
        # A late second end of the finished step is not a move its machine has.
        with self.assertRaises(attempts_mod.Illegal):
            self.attempts.move(old["id"], "ended", "done")
        self.assertEqual(self.attempts.holding("w", "0001_a")["id"], new["id"])


class Stopping(WithAttempts):
    def test_nothing_running_is_refused(self):
        with self.assertRaises(attempts_mod.Illegal):
            self.attempts.ask_stop(999, "Lan")
        row = self.attempts.open("step", "w", "0001_a", "spec", state="running")
        self.attempts.move(row["id"], "ended", "done")
        with self.assertRaises(attempts_mod.Illegal):
            self.attempts.ask_stop(row["id"], "Lan")

    def test_a_stop_is_recorded_with_its_name(self):
        row = self.attempts.open("step", "w", "0001_a", "spec", state="running")
        asked = self.attempts.ask_stop(row["id"], "Lan")
        self.assertTrue(asked["stop_asked_at"])
        self.assertEqual(asked["stop_asked_by"], "Lan")
        self.assertTrue(self.listing("w")[0]["stop_asked_at"])

    def test_a_second_stop_is_the_first_one(self):
        row = self.attempts.open("step", "w", "0001_a", "spec", state="running")
        first = self.attempts.ask_stop(row["id"], "Lan")
        second = self.attempts.ask_stop(row["id"], "Minh")
        self.assertEqual(second["stop_asked_by"], "Lan")
        self.assertEqual(first["stop_asked_at"], second["stop_asked_at"])

    def test_a_stop_is_recorded_on_a_sealed_step_too(self):
        # a Stop is recorded at every unfinished state; one that reaches an `ending`
        # step is no longer refused, the step runs on and ends `stop_late`.
        row = self.attempts.open("step", "w", "0001_a", "spec", state="running")
        self.attempts.move(row["id"], "ending")
        asked = self.attempts.ask_stop(row["id"], "Lan")
        self.assertEqual((asked["state"], asked["stop_asked_by"]), ("ending", "Lan"))


class Sealing(unittest.TestCase):
    def test_a_sealed_step_is_sealed_and_tells_its_attempt_once(self):
        r = running()
        told = []
        r.on_seal = lambda: told.append(1)
        self.assertTrue(steps.seal(r))
        self.assertTrue(steps.seal(r))
        self.assertTrue(r.sealed)
        self.assertEqual(told, [1])

    def test_a_stopped_step_cannot_be_sealed(self):
        r = running()
        told = []
        r.on_seal = lambda: told.append(1)
        r.stop_requested, r.stopped_by = True, "Lan"
        self.assertFalse(steps.seal(r))
        self.assertFalse(r.sealed)
        self.assertEqual(told, [])

    def test_a_step_with_no_running_always_seals(self):
        self.assertTrue(steps.seal(None))


if __name__ == "__main__":
    unittest.main()
