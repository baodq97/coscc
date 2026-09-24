"""Tests for the registry of steps running now (`0034`)."""

import unittest

from coscc import steps
from coscc.steps import Busy, Finishing, NotRunning, Registry


class OneStepPerUnit(unittest.TestCase):
    def test_a_second_claim_on_the_same_unit_is_refused_and_names_the_first(self):
        r = Registry()
        r.claim("w", "0001_a", "spec")
        with self.assertRaises(Busy) as e:
            r.claim("w", "0001_a", "plan")
        self.assertIn("spec", str(e.exception))
        self.assertIsInstance(e.exception, ValueError)

    def test_two_units_run_at_once(self):
        r = Registry()
        r.claim("w", "0001_a", "spec")
        r.claim("w", "0002_b", "impl")
        self.assertEqual([x["unit"] for x in r.listing("w")], ["0001_a", "0002_b"])

    def test_the_same_unit_name_in_another_workspace_is_another_unit(self):
        r = Registry()
        r.claim("w", "0001_a", "spec")
        r.claim("v", "0001_a", "spec")
        self.assertEqual(len(r.listing("w")), 1)
        self.assertEqual(len(r.listing("v")), 1)

    def test_release_frees_the_unit(self):
        r = Registry()
        running = r.claim("w", "0001_a", "spec")
        r.release(running)
        self.assertIsNone(r.get("w", "0001_a"))
        self.assertEqual(r.listing("w"), [])
        r.claim("w", "0001_a", "plan")

    def test_release_removes_only_that_object(self):
        r = Registry()
        old = r.claim("w", "0001_a", "spec")
        r.release(old)
        new = r.claim("w", "0001_a", "plan")
        r.release(old)  # a late release of the finished step
        self.assertIs(r.get("w", "0001_a"), new)

    def test_listing_carries_what_the_page_shows(self):
        r = Registry()
        r.claim("w", "0001_a", "spec")
        [row] = r.listing("w")
        self.assertEqual(set(row), {"unit", "stage", "started_at", "stopping"})
        self.assertFalse(row["stopping"])


class Stopping(unittest.TestCase):
    def test_nothing_running_is_refused(self):
        with self.assertRaises(NotRunning):
            Registry().request_stop("w", "0001_a", "Lan")

    def test_a_stop_is_recorded_with_its_name(self):
        r = Registry()
        r.claim("w", "0001_a", "spec")
        running = r.request_stop("w", "0001_a", "Lan")
        self.assertTrue(running.stop_requested)
        self.assertEqual(running.stopped_by, "Lan")
        self.assertTrue(r.listing("w")[0]["stopping"])

    def test_a_second_stop_is_the_first_one(self):
        r = Registry()
        r.claim("w", "0001_a", "spec")
        first = r.request_stop("w", "0001_a", "Lan")
        second = r.request_stop("w", "0001_a", "Minh")
        self.assertIs(first, second)
        self.assertEqual(second.stopped_by, "Lan")

    def test_a_sealed_step_cannot_be_stopped(self):
        r = Registry()
        running = r.claim("w", "0001_a", "spec")
        self.assertTrue(steps.seal(running))
        with self.assertRaises(Finishing):
            r.request_stop("w", "0001_a", "Lan")
        self.assertFalse(running.stop_requested)

    def test_a_stopped_step_cannot_be_sealed(self):
        r = Registry()
        running = r.claim("w", "0001_a", "spec")
        r.request_stop("w", "0001_a", "Lan")
        self.assertFalse(steps.seal(running))
        self.assertFalse(running.sealed)

    def test_a_step_with_no_registry_row_always_seals(self):
        self.assertTrue(steps.seal(None))


if __name__ == "__main__":
    unittest.main()
