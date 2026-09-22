"""Tests for the run log, including the one failure that was measured.

The concurrency case is the reason this file is not just round-trip assertions. That
measured four processes leaving 8 of 20 entries behind, with no error anywhere
(`.cos/0004_silent-concurrent-loss/plan.md:115`). The same shape is run here at unit-test size:
real processes, one folder, count what survives.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from coscc.journal import BadRecord, Busy, Journal

WRITERS = 4
PER_WRITER = 5


class ARecordSurvivesAndIsStamped(unittest.TestCase):
    def test_a_record_comes_back_with_a_version_and_a_time(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            j.append({"kind": "note", "text": "hello"})
            [got] = j.records()
            self.assertEqual(got["kind"], "note")
            self.assertEqual(got["v"], 1)
            self.assertIn("T", got["at"])

    def test_appending_leaves_earlier_records_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            for i in range(5):
                j.append({"kind": "note", "n": i})
            self.assertEqual([r["n"] for r in j.records()], [0, 1, 2, 3, 4])

    def test_a_record_without_a_kind_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            with self.assertRaises(BadRecord):
                j.append({"text": "no kind here"})

    def test_a_record_that_cannot_be_serialised_is_refused_before_anything_is_stored(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            with self.assertRaises(BadRecord):
                j.append({"kind": "note", "bad": object()})
            self.assertEqual(j.records(), [], "a refused record still left a row")

    def test_a_missing_journal_reads_as_empty_rather_than_failing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(Journal(d, d).records(), [])

    def test_a_corrupt_record_is_skipped_not_repaired(self):
        """The database is hand-editable like the file was, and a repair loses intent."""
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            j.append({"kind": "note", "n": 1})
            with j.data.write() as conn:
                conn.execute(
                    "INSERT INTO runs (at, root, workspace, unit, stage, kind, record) "
                    "VALUES ('x', ?, '', '', '', 'note', 'this is not json')",
                    (str(j.working_dir),),
                )
            j.append({"kind": "note", "n": 2})
            self.assertEqual([r["n"] for r in j.records()], [1, 2])
            # and the hand-written row is still there, because nothing rewrote it
            with j.data.connect() as conn:
                stored = [row["record"] for row in conn.execute("SELECT record FROM runs")]
            self.assertIn("this is not json", stored)


class ModeIsTheLatestRecordForAStep(unittest.TestCase):
    def test_setting_a_mode_twice_leaves_the_second_one_in_force(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            j.set_mode("w", "0009_x", "impl", "manual")
            j.set_mode("w", "0009_x", "impl", "autonomous")
            self.assertEqual(j.modes("w"), {("0009_x", "impl"): "autonomous"})

    def test_modes_are_per_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            j.set_mode("a", "0009_x", "impl", "autonomous")
            j.set_mode("b", "0009_x", "impl", "manual")
            self.assertEqual(j.modes("a"), {("0009_x", "impl"): "autonomous"})
            self.assertEqual(j.modes("b"), {("0009_x", "impl"): "manual"})

    def test_an_invented_mode_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(BadRecord):
                Journal(d, d).set_mode("w", "0009_x", "impl", "semi-automatic")


class TheTimelineSaysWhatItKnows(unittest.TestCase):
    def test_a_finished_run_carries_its_ends_and_its_cost(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            j.started("w", "0009_x", "spec", "autonomous", session_id="s-1")
            j.finished(
                "w", "0009_x", "spec", "done",
                artifact="spec.md", input_tokens=100, output_tokens=20, turns=1,
            )
            [row] = j.timeline("w", "0009_x")
            self.assertEqual(row["stage"], "spec")
            self.assertEqual(row["session_id"], "s-1")
            self.assertEqual(row["outcome"], "done")
            self.assertEqual(row["artifact"], "spec.md")
            self.assertEqual(row["cost"]["input_tokens"], 100)
            self.assertIsNotNone(row["started"])
            self.assertIsNotNone(row["ended"])

    def test_a_run_with_no_end_is_shown_unfinished_rather_than_guessed_at(self):
        # The app being killed mid-step and the step still running look identical from
        # here. Leaving `ended` unset is the only honest answer.
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            j.started("w", "0009_x", "impl", "autonomous", session_id="s-2")
            [row] = j.timeline("w", "0009_x")
            self.assertIsNone(row["ended"])
            self.assertIsNone(row["outcome"])

    def test_an_invented_outcome_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(BadRecord):
                Journal(d, d).finished("w", "0009_x", "impl", "probably fine")

    def test_runs_are_ordered_oldest_first(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            for stage in ("intent", "spec", "plan"):
                j.started("w", "0009_x", stage, "manual")
                j.finished("w", "0009_x", stage, "done")
            self.assertEqual([r["stage"] for r in j.timeline("w", "0009_x")], ["intent", "spec", "plan"])


class TotalsAreAddedNotStored(unittest.TestCase):
    def test_the_unit_total_equals_the_sum_of_its_steps(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            for stage, tokens in (("spec", 100), ("plan", 250), ("impl", 700)):
                j.started("w", "0009_x", stage, "autonomous")
                j.finished("w", "0009_x", stage, "done", input_tokens=tokens, output_tokens=1)

            totals = j.totals("w", "0009_x")
            self.assertEqual(totals["total"]["input_tokens"], 1050)
            self.assertEqual(totals["total"]["output_tokens"], 3)
            summed = sum(b["input_tokens"] for b in totals["per_stage"].values())
            self.assertEqual(summed, totals["total"]["input_tokens"])

    def test_two_runs_of_one_stage_add_rather_than_replace(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            for _ in range(2):
                j.started("w", "0009_x", "impl", "autonomous")
                j.finished("w", "0009_x", "impl", "done", input_tokens=40)
            self.assertEqual(j.totals("w", "0009_x")["total"]["input_tokens"], 80)

    def test_a_unit_that_never_ran_totals_zero_rather_than_failing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(Journal(d, d).totals("w", "0009_x")["total"]["input_tokens"], 0)


WRITER = """
import sys
from coscc.journal import Journal
j = Journal(sys.argv[1], sys.argv[1])
tag = sys.argv[2]
for i in range({per_writer}):
    j.set_mode("w", "unit-%s-%d" % (tag, i), "impl", "autonomous")
""".format(per_writer=PER_WRITER)


class FourProcessesLoseNothing(unittest.TestCase):
    def test_every_record_survives_concurrent_writers(self):
        repo = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as d:
            procs = [
                subprocess.Popen(
                    [sys.executable, "-c", WRITER, d, str(n)],
                    cwd=repo,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                for n in range(WRITERS)
            ]
            for p in procs:
                _, err = p.communicate(timeout=60)
                self.assertEqual(p.returncode, 0, err.decode(errors="replace"))

            journal = Journal(d, d)
            records = journal.records()
            self.assertEqual(
                len(records), WRITERS * PER_WRITER,
                f"expected {WRITERS * PER_WRITER} records, {len(records)} survived",
            )
            # and every stored record is whole — a torn write would show up as a parse
            # failure, which `records` silently skips, so count the raw rows too and parse
            # each one here where a failure is loud.
            with journal.data.connect() as conn:
                raw = [row["record"] for row in conn.execute("SELECT record FROM runs")]
            self.assertEqual(len(raw), WRITERS * PER_WRITER)
            for stored in raw:
                json.loads(stored)

    def test_a_held_database_becomes_an_error_that_names_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            other = Journal(d, d)
            with j.transaction():
                with self.assertRaises(Busy) as caught:
                    other.append({"kind": "note"}, timeout=0.05)
            self.assertIn(d, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
