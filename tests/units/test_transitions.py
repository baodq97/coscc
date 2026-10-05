"""The one place a transition is applied."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.store.db import Data
from coscc.store.journal import Journal
from coscc.units import transitions
from coscc.units.history import BadTransition, History

WS = "repo"
UNIT = "0001_a-problem"
OPEN_RESULT = {"run": "r1", "open_run": "r1", "revision": "abc", "computed_revision": "abc"}


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.data = Data(root / "data")
        self.history = History(root / "work", self.data)
        self.journal = Journal(root / "work", self.data)

    def apply(self, **kw):
        args = dict(
            machine="unit",
            transition="result",
            workspace=WS,
            unit=UNIT,
            artifact="intent.md",
            to_state="accepted",
            inputs=OPEN_RESULT,
            authority="agent",
            run="r1",
        )
        args.update(kw)
        return transitions.apply(self.history, self.journal, **args)

    def counts(self):
        with self.data.connect() as conn:
            return (
                conn.execute("SELECT COUNT(*) FROM transitions").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM runs WHERE kind = 'transition'").fetchone()[0],
            )


class TheTransitionAndItsEventAreOneTransaction(Fixture):
    def test_a_failure_between_the_transition_and_its_event_leaves_neither(self):
        # `append_with` writes the transition in `also`, then the event: failing the event's
        # insert is failing between the two.
        with mock.patch.object(Journal, "_insert", side_effect=RuntimeError("disk gone")):
            with self.assertRaises(RuntimeError):
                self.apply()
        self.assertEqual(self.counts(), (0, 0))

    def test_an_open_guard_writes_one_of_each(self):
        self.apply()
        self.assertEqual(self.counts(), (1, 1))


class EveryRowSaysWhichGuardAndWhoseAuthority(Fixture):
    def test_every_row_says_which_guard_and_whose_authority(self):
        applied = self.apply()
        self.assertTrue(applied.open)
        row = self.history.transitions(WS, UNIT)[-1]
        self.assertEqual(
            (row["guard"], row["authority"], row["run"], row["to_state"]),
            ("stage-result", "agent", "r1", "accepted"),
        )
        self.assertEqual(json.loads(row["inputs"]), OPEN_RESULT)
        event = [r for r in self.journal.records() if r.get("kind") == "transition"][-1]
        self.assertEqual(
            (event["guard"], event["authority"], event["run"]), ("stage-result", "agent", "r1")
        )

    def test_an_unknown_authority_is_refused_before_anything_is_written(self):
        with self.assertRaises(BadTransition):
            self.apply(authority="unknown")
        self.assertEqual(self.counts(), (0, 0))


class AClosedGuardWritesNothing(Fixture):
    def test_the_reasons_come_back_and_no_row_is_written(self):
        told = []
        applied = self.apply(
            inputs={**OPEN_RESULT, "open_run": "r2"},
            notify=told.append,
        )
        self.assertFalse(applied.open)
        self.assertEqual((applied.guard, applied.reasons), ("stage-result", ("wrong-run",)))
        self.assertIsNone(applied.row)
        self.assertEqual(self.counts(), (0, 0))
        self.assertEqual(told, [])

    def test_an_agent_cannot_skip_a_stage(self):
        applied = self.apply(
            transition="skip",
            artifact="spec.md",
            to_state="skipped",
            inputs={"authority": "agent"},
        )
        self.assertEqual(applied.reasons, ("agent-cannot-skip",))
        self.assertEqual(self.counts(), (0, 0))

    def test_a_transition_the_machine_does_not_have_is_refused(self):
        with self.assertRaises(BadTransition):
            self.apply(transition="teleport")


class NotifyComesAfterTheCommit(Fixture):
    def test_the_rows_are_there_when_notify_runs(self):
        seen = []
        self.apply(notify=lambda applied: seen.append((applied.guard, self.counts())))
        self.assertEqual(seen, [("stage-result", (1, 1))])


if __name__ == "__main__":
    unittest.main()
