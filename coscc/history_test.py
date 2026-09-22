"""Tests for the transition log, and for the claim that the log is the only record.

Every test here passes an explicit data root. `coscc/journal.py:108-109` records why:
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

from coscc import states
from coscc.data import Data
from coscc.history import CODE, DELIVERABLE, UNKNOWN, BadTransition, History, settled_edits

WS = "repo"
UNIT = "0001_a-problem"

# The same set `coscc/states_test.py` uses: no stage name, artifact or status is shared
# with the default. `spec.md` R6's second half is only demonstrated if a unit can be driven
# from end to end through this without a line of Python changing.
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
        path = self.root / "other.json"
        path.write_text(json.dumps(OTHER), encoding="utf-8")
        return History(self.root / "work", self.data, machine=states.load(path))

    def rows(self, **kw):
        return self.history.transitions(**kw)


class DeletingTheLastRowMovesTheUnitBack(Fixture):
    """R1. There is no current-state column, and this is how you can tell."""

    def test_the_projection_falls_back_with_nothing_else_updated(self):
        self.history.record(WS, UNIT, "intent.md", "draft")
        self.history.record(WS, UNIT, "intent.md", "accepted")
        self.assertEqual(self.history.state(WS, UNIT)["intent.md"], "accepted")

        with sqlite3.connect(self.data.db_path) as conn:
            conn.execute("DELETE FROM transitions WHERE id = (SELECT MAX(id) FROM transitions)")

        # Nothing else was touched, and the answer moved anyway.
        self.assertEqual(self.history.state(WS, UNIT)["intent.md"], "draft")

    def test_no_table_carries_a_current_state_column(self):
        # The structural half of the same claim: a column named for a current state is
        # the thing that would let the test above keep passing while being wrong.
        self.history.record(WS, UNIT, "intent.md", "draft")
        with self.data.connect() as conn:
            tables = [r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )]
            for table in tables:
                names = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
                for banned in ("state", "status", "current_state", "current"):
                    self.assertNotIn(banned, names, f"{table}.{banned}")

    def test_an_artifact_with_no_transition_reads_as_the_absent_state(self):
        self.history.record(WS, UNIT, "intent.md", "draft")
        projected = self.history.state(WS, UNIT)
        machine = states.default()
        self.assertEqual(list(projected), list(machine.artifacts))
        self.assertEqual(projected["ship.md"], machine.absent)


class EveryFieldIsWrittenOrSaysItIsNotKnown(Fixture):
    """R3. Never a NULL, never a blank, never a column left out."""

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
        self.history.record(WS, UNIT, "intent.md", "accepted", actor="me", session="s1", source="run:7")
        with self.data.connect() as conn:
            for row in conn.execute("SELECT * FROM transitions"):
                for name in row.keys():
                    if name == "once_key":
                        continue  # absence of a key means "do not deduplicate", not "unknown"
                    self.assertIsNotNone(row[name], name)
                    self.assertNotEqual(str(row[name]).strip(), "", name)

    def test_the_state_set_the_row_was_written_under_is_recorded(self):
        # `spec.md` C5: two configurations comparing states that never meant the same
        # thing is a failure that runs rather than stops. The row has to say which.
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

    def test_a_settled_artifact_edited_again_is_the_event_0013_counts(self):
        self.history.record(WS, UNIT, "intent.md", "draft")
        self.history.record(WS, UNIT, "intent.md", "accepted")
        self.history.record(WS, UNIT, "intent.md", "accepted", source="commit:abc")
        self.history.record(WS, UNIT, "spec.md", "draft")
        counted = settled_edits(self.rows())
        self.assertEqual(len(counted), 1)
        self.assertEqual(counted[0]["from_state"], "accepted")
        self.assertEqual(counted[0]["to_state"], "accepted")

    def test_two_units_do_not_see_each_others_history(self):
        self.history.record(WS, UNIT, "intent.md", "draft")
        self.history.record(WS, "0002_another", "intent.md", "draft")
        row = self.history.record(WS, "0002_another", "intent.md", "accepted")
        self.assertEqual(row["from_state"], "draft")
        self.assertEqual(self.history.state(WS, UNIT)["intent.md"], "draft")
        self.assertEqual(self.history.units(WS), [UNIT, "0002_another"])


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
            {"workspace": WS, "unit": UNIT, "artifact": "intent.md", "to_state": "draft",
             "once_key": "commit:aaa:intent.md"},
            {"workspace": WS, "unit": UNIT, "artifact": "intent.md", "to_state": "accepted",
             "once_key": "commit:bbb:intent.md"},
        ]
        first = self.history.record_many(batch)
        second = self.history.record_many(batch)
        self.assertEqual(len(first), 2)
        self.assertEqual(second, [])
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(self.history.state(WS, UNIT)["intent.md"], "accepted")

    def test_without_a_key_two_identical_moves_are_two_events(self):
        # An append-only log that silently dropped the second would be lying by omission:
        # a settled artifact edited twice is two edits.
        self.history.record(WS, UNIT, "intent.md", "accepted")
        self.history.record(WS, UNIT, "intent.md", "accepted")
        self.assertEqual(len(self.rows()), 2)


class AUnitCarriesASequenceOfSessions(Fixture):
    """R4, and the distinction `intent.md` constraint 2 says a design must keep."""

    def test_eight_steps_are_eight_sessions_in_order(self):
        for index, artifact in enumerate(("intent.md", "spec.md", "plan.md"), start=1):
            self.history.record(WS, UNIT, artifact, "draft", session=f"s{index}", actor="agent")
        found = self.history.sessions_of(WS, UNIT)
        self.assertEqual([s["session"] for s in found["sessions"]], ["s1", "s2", "s3"])
        self.assertEqual([s["stages"] for s in found["sessions"]], [["intent"], ["spec"], ["plan"]])

    def test_one_session_over_many_turns_stays_one_row(self):
        self.history.record(WS, UNIT, "intent.md", "draft", session="chat-1")
        self.history.record(WS, UNIT, "intent.md", "accepted", session="chat-1")
        self.history.record(WS, UNIT, "spec.md", "draft", session="chat-1")
        found = self.history.sessions_of(WS, UNIT)
        self.assertEqual(len(found["sessions"]), 1)
        self.assertEqual(found["sessions"][0]["transitions"], 3)
        self.assertEqual(found["sessions"][0]["stages"], ["intent", "spec"])

    def test_imported_history_reports_no_sessions_rather_than_one_called_unknown(self):
        # `spec.md` C1. Git knows commit authors, not sessions, so the imported events
        # have none — and a row labelled "unknown" in the sequence would read as though
        # one session did all of it.
        self.history.record(WS, UNIT, "intent.md", "draft", source="commit:aaa")
        self.history.record(WS, UNIT, "intent.md", "accepted", source="commit:bbb")
        found = self.history.sessions_of(WS, UNIT)
        self.assertEqual(found["sessions"], [])
        self.assertEqual(found["unknown_transitions"], 2)


class WhatAUnitProduced(Fixture):
    """R5: one table, one classifying field, and a total nobody has to add up."""

    def test_the_two_kinds_are_counted_apart_and_together(self):
        self.history.add_output(WS, UNIT, "intent", DELIVERABLE, ".cos/0001/intent.md")
        self.history.add_output(WS, UNIT, "impl", CODE, "coscc/history.py")
        self.history.add_output(WS, UNIT, "impl", CODE, "coscc/history_test.py")
        counts = self.history.output_counts(WS, UNIT)
        self.assertEqual(counts, {DELIVERABLE: 1, CODE: 2, "total": 3})

    def test_a_kind_outside_the_two_is_refused(self):
        with self.assertRaises(BadTransition):
            self.history.add_output(WS, UNIT, "impl", "sideways", "x.py")

    def test_an_output_says_who_and_which_session_or_says_it_does_not_know(self):
        row = self.history.add_output(WS, UNIT, "impl", CODE, "coscc/history.py")
        self.assertEqual((row["actor"], row["session"], row["source"]), (UNKNOWN,) * 3)

    def test_counts_are_per_unit(self):
        self.history.add_output(WS, UNIT, "impl", CODE, "a.py")
        self.history.add_output(WS, "0002_another", "impl", CODE, "b.py")
        self.assertEqual(self.history.output_counts(WS, UNIT)["total"], 1)
        self.assertEqual(self.history.output_counts(WS)["total"], 2)


class ADifferentStateSetDrivesAUnitEndToEnd(Fixture):
    """`spec.md` R6, second half, end to end. Not one line of Python differs."""

    def test_a_unit_runs_the_whole_of_a_set_this_module_has_never_seen(self):
        history = self.other_history()
        history.record(WS, UNIT, "ticket.txt", "open")
        history.record(WS, UNIT, "ticket.txt", "closed")
        history.record(WS, UNIT, "wrap.txt", "open")
        history.record(WS, UNIT, "wrap.txt", "closed")
        # Edited after settling: the event this unit exists to count, under a set whose
        # word for settled is not `accepted`.
        history.record(WS, UNIT, "ticket.txt", "closed", source="commit:zzz")

        self.assertEqual(
            history.state(WS, UNIT), {"ticket.txt": "closed", "wrap.txt": "closed"}
        )
        counted = settled_edits(history.transitions(WS, UNIT), history.machine)
        self.assertEqual(len(counted), 1)
        self.assertEqual(counted[0]["source"], "commit:zzz")
        self.assertEqual([row["stage"] for row in history.transitions(WS, UNIT)][:1], ["ticket"])

    def test_the_default_set_is_not_consulted_anywhere(self):
        history = self.other_history()
        with self.assertRaises(BadTransition):
            history.record(WS, UNIT, "intent.md", "draft")
        row = history.record(WS, UNIT, "ticket.txt", "open")
        self.assertEqual(row["from_state"], "nowhere")


if __name__ == "__main__":
    unittest.main()
