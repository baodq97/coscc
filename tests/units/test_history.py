"""Tests for the transition log, and for the claim that the log is the only record.

Every test here passes an explicit data root. `coscc/store/journal.py:108-109` records why:
one that forgets writes into the real `~/.cos`, and these two tables inherit that hazard
without changing it.

The test that carries this unit is `DeletingTheLastRowMovesTheUnitBack`. It edits the
database by hand — the one thing the module never does — because that is the only way to
show there is no second copy of the state to disagree with the log. If a current-state
column is ever added, that test is what fails.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from coscc.units import states
from coscc.store.db import Data
from coscc.units.history import UNKNOWN, BadTransition, History

WS = "repo"
UNIT = "0001_a-problem"

# The same set `tests/units/test_states.py` uses: no stage name, artifact or status is shared with
# the default.
OTHER = {
    "name": "two-step",
    "absent": "nowhere",
    "settled": ["closed"],
    "stages": [
        {"name": "ticket", "artifact": "ticket.txt", "statuses": ["open", "closed"]},
        {"name": "wrap", "artifact": "wrap.txt", "statuses": ["open", "closed", "void"]},
    ],
}


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.data = Data(self.root / "data")
        self.history = History(self.root / "work", self.data)

    def other_history(self) -> History:
        return History(self.root / "work", self.data, machine=states.Machine.of(OTHER))

    def rows(self, **kw):
        return self.history.transitions(**kw)


class DeletingTheLastRowMovesTheUnitBack(Fixture):
    """There is no current-state column, and this is how you can tell."""

    def test_the_projection_falls_back_with_nothing_else_updated(self):
        self.history.record(WS, UNIT, "intent.md", "draft")
        self.history.record(WS, UNIT, "intent.md", "accepted")
        self.assertEqual(self.rows()[-1]["to_state"], "accepted")

        with sqlite3.connect(self.data.db_path) as conn:
            conn.execute("DELETE FROM transitions WHERE id = (SELECT MAX(id) FROM transitions)")

        # Nothing else was touched, and the answer moved anyway.
        self.assertEqual(self.rows()[-1]["to_state"], "draft")

    def test_no_table_carries_a_current_state_column(self):
        # The structural half of the same claim: a column named for a current state is
        # the thing that would let the test above keep passing while being wrong.
        self.history.record(WS, UNIT, "intent.md", "draft")
        with self.data.connect() as conn:
            tables = [
                r["name"]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            for table in tables:
                names = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
                for banned in ("state", "status", "current_state", "current"):
                    self.assertNotIn(banned, names, f"{table}.{banned}")


class EveryFieldIsWrittenOrSaysItIsNotKnown(Fixture):
    """Never a NULL, never a blank, never a column left out."""

    def test_what_a_caller_does_not_supply_becomes_the_word_unknown(self):
        row = self.history.record(WS, UNIT, "intent.md", "draft")
        self.assertEqual(row["actor"], UNKNOWN)
        self.assertEqual(row["session"], UNKNOWN)
        self.assertEqual(row["source"], UNKNOWN)

    def test_a_blank_is_not_a_way_past_it(self):
        row = self.history.record(WS, UNIT, "intent.md", "draft", actor="   ", session="")
        self.assertEqual(row["actor"], UNKNOWN)
        self.assertEqual(row["session"], UNKNOWN)

    def test_no_stored_column_is_ever_null_or_empty(self):
        self.history.record(WS, UNIT, "intent.md", "draft")
        self.history.record(
            WS, UNIT, "intent.md", "accepted", actor="me", session="s1", source="run:7"
        )
        with self.data.connect() as conn:
            for row in conn.execute("SELECT * FROM transitions"):
                for name in row.keys():
                    if name == "once_key":
                        continue  # absence of a key means "do not deduplicate", not "unknown"
                    self.assertIsNotNone(row[name], name)
                    self.assertNotEqual(str(row[name]).strip(), "", name)

    def test_a_row_with_no_guard_says_so_rather_than_leaving_it_blank(self):
        row = self.history.record(WS, UNIT, "intent.md", "draft")
        self.assertEqual(
            (row["guard"], row["authority"], row["run"], row["inputs"]),
            (UNKNOWN, UNKNOWN, UNKNOWN, "{}"),
        )

    def test_guard_authority_run_and_inputs_are_stored_as_given(self):
        self.history.record(
            WS,
            UNIT,
            "intent.md",
            "draft",
            guard="stage-result",
            authority="agent",
            run="r1",
            inputs={"revision": "ab", "pr": 7},
        )
        row = self.rows()[-1]
        self.assertEqual(
            (row["guard"], row["authority"], row["run"]), ("stage-result", "agent", "r1")
        )
        self.assertEqual(json.loads(row["inputs"]), {"pr": 7, "revision": "ab"})

    def test_an_authority_outside_the_four_is_refused(self):
        with self.assertRaises(BadTransition):
            self.history.record(WS, UNIT, "intent.md", "draft", authority="owner")
        self.assertEqual(self.rows(), [])

    def test_the_state_set_the_row_was_written_under_is_recorded(self):
        # Two configurations comparing states that never meant the same thing is a failure that runs
        # rather than stops. The row has to say which.
        self.history.record(WS, UNIT, "intent.md", "draft")
        self.other_history().record(WS, UNIT, "ticket.txt", "open")
        machines = [row["machine"] for row in self.rows()]
        self.assertEqual(machines, ["coscc-default", "two-step"])


class FromStateComesFromTheLog(Fixture):
    def test_the_first_transition_leaves_the_absent_state(self):
        row = self.history.record(WS, UNIT, "intent.md", "draft")
        self.assertEqual(row["from_state"], states.default().absent)

    def test_each_later_one_starts_where_the_previous_left_off(self):
        self.history.record(WS, UNIT, "intent.md", "draft")
        row = self.history.record(WS, UNIT, "intent.md", "accepted")
        self.assertEqual(row["from_state"], "draft")

    def test_two_units_do_not_see_each_others_history(self):
        self.history.record(WS, UNIT, "intent.md", "draft")
        self.history.record(WS, "0002_another", "intent.md", "draft")
        row = self.history.record(WS, "0002_another", "intent.md", "accepted")
        self.assertEqual(row["from_state"], "draft")
        self.assertEqual(len(self.rows(unit=UNIT)), 1)


class AStateTheArtifactCannotCarryIsRefused(Fixture):
    def test_a_status_from_another_stage(self):
        with self.assertRaises(BadTransition) as caught:
            self.history.record(WS, UNIT, "intent.md", "done")
        self.assertIn("intent.md", str(caught.exception))

    def test_an_artifact_the_state_set_does_not_know(self):
        with self.assertRaises(BadTransition):
            self.history.record(WS, UNIT, "notes.md", "draft")

    def test_a_bad_row_in_a_batch_refuses_the_batch(self):
        with self.assertRaises(BadTransition):
            self.history.record_many(
                [
                    {"workspace": WS, "unit": UNIT, "artifact": "intent.md", "to_state": "draft"},
                    {"workspace": WS, "unit": UNIT, "artifact": "intent.md", "to_state": "shipped"},
                ]
            )
        self.assertEqual(self.rows(), [])


class AnImportCanBeRunTwice(Fixture):
    def test_a_row_with_the_same_key_is_stored_once(self):
        batch = [
            {
                "workspace": WS,
                "unit": UNIT,
                "artifact": "intent.md",
                "to_state": "draft",
                "once_key": "commit:aaa:intent.md",
            },
            {
                "workspace": WS,
                "unit": UNIT,
                "artifact": "intent.md",
                "to_state": "accepted",
                "once_key": "commit:bbb:intent.md",
            },
        ]
        first = self.history.record_many(batch)
        second = self.history.record_many(batch)
        self.assertEqual(len(first), 2)
        self.assertEqual(second, [])
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(self.rows()[-1]["to_state"], "accepted")

    def test_without_a_key_two_identical_moves_are_two_events(self):
        # An append-only log that silently dropped the second would be lying by omission:
        # a settled artifact edited twice is two edits.
        self.history.record(WS, UNIT, "intent.md", "accepted")
        self.history.record(WS, UNIT, "intent.md", "accepted")
        self.assertEqual(len(self.rows()), 2)


class ADifferentStateSetDrivesAUnitEndToEnd(Fixture):
    """Not one line of Python differs."""

    def test_a_unit_runs_the_whole_of_a_set_this_module_has_never_seen(self):
        history = self.other_history()
        history.record(WS, UNIT, "ticket.txt", "open")
        history.record(WS, UNIT, "ticket.txt", "closed")
        history.record(WS, UNIT, "wrap.txt", "open")
        history.record(WS, UNIT, "wrap.txt", "closed")
        history.record(WS, UNIT, "ticket.txt", "closed", source="commit:zzz")

        self.assertEqual(history.transitions(WS, UNIT)[-1]["from_state"], "closed")
        self.assertEqual([row["stage"] for row in history.transitions(WS, UNIT)][:1], ["ticket"])

    def test_the_default_set_is_not_consulted_anywhere(self):
        history = self.other_history()
        with self.assertRaises(BadTransition):
            history.record(WS, UNIT, "intent.md", "draft")
        row = history.record(WS, UNIT, "ticket.txt", "open")
        self.assertEqual(row["from_state"], "nowhere")


if __name__ == "__main__":
    unittest.main()
