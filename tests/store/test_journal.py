"""Tests for the run log, including the one failure that was measured.

The concurrency case is the reason this file is not just round-trip assertions. The same shape is
run here at unit-test size: real processes, one folder, count what survives."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from coscc.store.journal import BELL, BadRecord, Journal, last_runs, totals_of
from coscc.store.db import Busy

WRITERS = 4
PER_WRITER = 5


class ARecordSurvivesAndIsStamped(unittest.TestCase):
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
                "w",
                "0009_x",
                "spec",
                "done",
                artifact="spec.md",
                input_tokens=100,
                output_tokens=20,
                turns=1,
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

    def test_a_stopped_run_says_who_stopped_it_and_claims_no_cost(self):
        # A person pressed Stop. The name rides on the record; a cost that never arrived is absent,
        # not zero.
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            j.started("w", "0009_x", "impl", "manual")
            j.finished("w", "0009_x", "impl", "stopped", stopped_by="Lan", cost_unknown=True)
            [end] = [r for r in j.records() if r["kind"] == "end"]
            self.assertEqual(end["outcome"], "stopped")
            self.assertEqual(end["stopped_by"], "Lan")
            self.assertNotIn("cost_usd", end)
            [row] = j.timeline("w", "0009_x")
            self.assertEqual(row["outcome"], "stopped")

    def test_a_row_carries_its_run_and_what_was_lost_and_an_older_one_none(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            j.started("w", "0009_x", "spec", "manual")
            j.finished("w", "0009_x", "spec", "done")
            j.started("w", "0009_x", "plan", "manual", run="r-1")
            j.finished("w", "0009_x", "plan", "done", run="r-1", events_lost=3)
            before, after = j.timeline("w", "0009_x")
            self.assertEqual((before["run"], before["events_lost"]), (None, None))
            self.assertEqual((after["run"], after["events_lost"]), ("r-1", 3))

    def test_an_integrate_row_carries_the_state_that_opened_it(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            j.started("w", "0009_x", "integrate", "manual", integrate_state="conflicting")
            j.finished("w", "0009_x", "integrate", "done")
            j.started("w", "0009_x", "integrate", "manual")
            j.finished("w", "0009_x", "integrate", "done")
            recorded, older = j.timeline("w", "0009_x")
            self.assertEqual(recorded["integrate_state"], "conflicting")
            self.assertIsNone(older["integrate_state"])

    def test_a_folded_start_keeps_its_agent(self):
        # Top level, not nested, and None for a start written before it.
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            j.started("w", "0009_x", "plan", "manual")
            j.finished("w", "0009_x", "plan", "done")
            j.started("w", "0009_x", "impl", "manual", agent="Uruz")
            older, named = j.timeline("w", "0009_x")
            self.assertIsNone(older["agent"])
            self.assertEqual(named["agent"], "Uruz")
            self.assertEqual(j.open_starts("w")["0009_x"]["open"][0]["agent"], "Uruz")


class TheTrialModelFillsInOneStart(unittest.TestCase):
    """The one field a written `start` is ever given afterwards."""

    def test_set_trial_model_touches_only_that_start(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            trial = {"model_trial": {"arm": "opus-5-5", "requested": "m"}}
            j.started("w", "0002_b", "impl", "manual", **trial)
            j.started("w", "0001_a", "review", "manual", **trial)
            j.started("w", "0001_a", "impl", "manual")
            mine = j.started("w", "0001_a", "impl", "manual", **trial)
            before = j.records("w", kind="start")
            self.assertTrue(
                j.set_trial_model("w", "0001_a", "impl", mine["at"], "claude-opus-5-5[1m]")
            )
            # Set once: a second answer, and a `start` stamped at another time, change nothing.
            self.assertFalse(j.set_trial_model("w", "0001_a", "impl", mine["at"], "never-started"))
            self.assertFalse(j.set_trial_model("w", "0001_a", "impl", "1999-01-01T00:00:00Z", "x"))
            after = j.records("w", kind="start")
        self.assertEqual(
            after[-1]["model_trial"],
            {"arm": "opus-5-5", "requested": "m", "model": "claude-opus-5-5[1m]"},
        )
        self.assertEqual(after[:-1], before[:-1])
        self.assertEqual(
            {k: v for k, v in after[-1].items() if k != "model_trial"},
            {k: v for k, v in before[-1].items() if k != "model_trial"},
        )


class OpenStartsAreTheRunsNobodyEnded(unittest.TestCase):
    def test_a_start_with_no_end_is_open(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            rec = j.started("w", "0009_x", "impl", "manual")
            got = j.open_starts("w")
            self.assertEqual(list(got), ["0009_x"])
            [row] = got["0009_x"]["open"]
            self.assertEqual(row["stage"], "impl")
            self.assertEqual(row["started"], rec["at"])
            self.assertEqual(got["0009_x"]["last_start"], rec["at"])

    def test_two_starts_one_end_leaves_the_other_open(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            j.started("w", "0009_x", "plan", "manual", session_id="a")
            j.started("w", "0009_x", "plan", "manual", session_id="b")
            j.finished("w", "0009_x", "plan", "done")
            [row] = j.open_starts("w")["0009_x"]["open"]
            self.assertEqual(row["session_id"], "a")

    def test_workspaces_do_not_mix(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            j.started("w", "0009_x", "impl", "manual")
            self.assertEqual(j.open_starts("v"), {})


class AFailedStepLeavesARecord(unittest.TestCase):
    """`attempted`, `failed_attempts`, `last_runs`, and `reported`."""

    def test_an_end_with_no_cost_reads_as_null_not_zero(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            j.started("w", "0009_x", "impl", "autonomous")
            j.finished("w", "0009_x", "impl", "failed")  # died before any cost was billed
            [row] = j.timeline("w", "0009_x")
            self.assertFalse(row["reported"])
            self.assertEqual(row["cost"]["cost_usd"], 0.0)  # zero_cost() still fills this in

            j.started("w", "0009_x", "spec", "autonomous")
            j.finished("w", "0009_x", "spec", "done", turns=3, cost_usd=1.5)
            [row2] = j.timeline("w", "0009_x", timeout=None)[1:]
            self.assertTrue(row2["reported"])

    def test_fail_fail_fail_done_reads_as_no_open_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            for _ in range(3):
                j.started("w", "0009_x", "impl", "autonomous")
                j.finished("w", "0009_x", "impl", "failed", turns=121, cost_usd=6.88)
            j.started("w", "0009_x", "impl", "autonomous")
            j.finished("w", "0009_x", "impl", "done", artifact="impl.md")
            self.assertIsNone(j.failed_attempts("w", "0009_x", "impl"))

    def test_no_run_at_all_reads_as_no_open_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            self.assertIsNone(j.failed_attempts("w", "0009_x", "impl"))

    def test_done_then_two_fails_gives_one_latest_and_one_earlier(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            j.started("w", "0009_x", "impl", "autonomous")
            j.finished("w", "0009_x", "impl", "done", artifact="impl.md")

            j.started("w", "0009_x", "impl", "autonomous")
            j.attempted("w", "0009_x", "impl", head="aaa", turns=121, cost_usd=6.88)
            j.finished("w", "0009_x", "impl", "failed", turns=121, cost_usd=6.88)

            j.started("w", "0009_x", "impl", "autonomous")
            j.attempted("w", "0009_x", "impl", head="bbb", turns=60, cost_usd=4.91)
            j.finished("w", "0009_x", "impl", "failed", turns=60, cost_usd=4.91)

            found = j.failed_attempts("w", "0009_x", "impl")
            self.assertIsNotNone(found)
            self.assertEqual(found["attempt"]["head"], "bbb")
            self.assertEqual(found["latest"]["turns"], 60)
            self.assertEqual(len(found["earlier"]), 1)
            self.assertEqual(found["earlier"][0]["turns"], 121)

    def test_a_dead_end_with_no_cost_carries_nulls_not_zeros(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            j.started("w", "0009_x", "impl", "autonomous")
            j.attempted("w", "0009_x", "impl", head="ccc")
            j.finished("w", "0009_x", "impl", "failed")  # no cost reported at all
            found = j.failed_attempts("w", "0009_x", "impl")
            self.assertIsNone(found["latest"]["turns"])
            self.assertIsNone(found["latest"]["cost_usd"])

    def test_a_run_whose_capture_failed_is_not_described_by_an_older_one(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            j.started("w", "0009_x", "impl", "autonomous")
            j.attempted("w", "0009_x", "impl", head="old")
            j.finished("w", "0009_x", "impl", "failed", turns=121, cost_usd=6.88)
            j.started("w", "0009_x", "impl", "autonomous")
            j.finished("w", "0009_x", "impl", "failed")  # no attempt row: capture failed
            found = j.failed_attempts("w", "0009_x", "impl")
            self.assertIsNone(found["attempt"])
            self.assertEqual(len(found["earlier"]), 1)

    def test_timeline_with_no_attempt_row_still_has_no_kind_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            j.started("w", "0009_x", "impl", "autonomous")
            j.finished("w", "0009_x", "impl", "failed", turns=5, cost_usd=0.1)
            self.assertEqual([r["kind"] for r in j.records("w", "0009_x")], ["start", "end"])


class LastRunsIsAPureFold(unittest.TestCase):
    def test_a_stage_with_no_end_is_absent(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            j.started("w", "0009_x", "impl", "autonomous")
            self.assertEqual(last_runs(j.timeline("w", "0009_x")), {})

    def test_the_most_recent_ended_row_wins(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            j.started("w", "0009_x", "impl", "autonomous")
            j.finished("w", "0009_x", "impl", "failed", turns=121, cost_usd=6.88)
            j.started("w", "0009_x", "impl", "autonomous")
            j.finished("w", "0009_x", "impl", "done", artifact="impl.md", turns=23, cost_usd=0.66)
            got = last_runs(j.timeline("w", "0009_x"))["impl"]
            self.assertEqual(got["outcome"], "done")
            self.assertEqual(got["turns"], 23)


class TotalsAreAddedNotStored(unittest.TestCase):
    def test_the_unit_total_equals_the_sum_of_its_steps(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            for stage, tokens in (("spec", 100), ("plan", 250), ("impl", 700)):
                j.started("w", "0009_x", stage, "autonomous")
                j.finished("w", "0009_x", stage, "done", input_tokens=tokens, output_tokens=1)

            total = totals_of(j.timeline("w", "0009_x"))
            self.assertEqual((total["input_tokens"], total["output_tokens"]), (1050, 3))

    def test_two_runs_of_one_stage_add_rather_than_replace(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            for _ in range(2):
                j.started("w", "0009_x", "impl", "autonomous")
                j.finished("w", "0009_x", "impl", "done", input_tokens=40)
            self.assertEqual(totals_of(j.timeline("w", "0009_x"))["input_tokens"], 80)


class AnEndClosesTheRunItNames(unittest.TestCase):
    """An `end` is matched to its `start` by `run`, and turns and cost are known apart."""

    def test_an_end_written_after_a_later_start_closes_its_own_run(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            j.started("w", "0009_x", "impl", "autonomous", run="A")
            j.started("w", "0009_x", "impl", "autonomous", run="B")
            j.finished("w", "0009_x", "impl", "failed", run="A", turns=3, cost_unknown=True)
            a, b = j.timeline("w", "0009_x")
            self.assertEqual((a["run"], a["outcome"]), ("A", "failed"))
            self.assertEqual((b["run"], b["ended"]), ("B", None))
            [still] = j.open_starts("w")["0009_x"]["open"]
            self.assertEqual(still["run"], "B")
            j.finished("w", "0009_x", "impl", "done", run="B", turns=4, cost_usd=0.5)
            self.assertEqual(j.open_starts("w"), {})

    def test_an_old_end_with_no_run_still_closes_by_stage(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            j.started("w", "0009_x", "impl", "autonomous", run="A")
            j.finished("w", "0009_x", "impl", "done", turns=2, cost_usd=0.1)
            [row] = j.timeline("w", "0009_x")
            self.assertEqual((row["run"], row["outcome"]), ("A", "done"))
            self.assertEqual(j.open_starts("w"), {})

    def test_an_end_naming_no_open_run_closes_by_stage(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            j.started("w", "0009_x", "impl", "autonomous")
            j.finished("w", "0009_x", "impl", "failed", run="elsewhere")
            [row] = j.timeline("w", "0009_x")
            self.assertEqual(row["outcome"], "failed")

    def test_turns_without_a_cost_are_kept(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            j.started("w", "0009_x", "impl", "autonomous", run="A")
            j.finished("w", "0009_x", "impl", "failed", run="A", turns=5, cost_unknown=True)
            [row] = j.timeline("w", "0009_x")
            self.assertEqual((row["turns_reported"], row["reported"]), (True, False))
            got = last_runs([row])["impl"]
            self.assertEqual((got["turns"], got["cost_usd"]), (5, None))

    def test_a_cost_without_turns_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            j.started("w", "0009_x", "impl", "autonomous")
            j.finished("w", "0009_x", "impl", "failed", cost_usd=0.25)
            got = last_runs(j.timeline("w", "0009_x"))["impl"]
            self.assertEqual((got["turns"], got["cost_usd"]), (None, 0.25))

    def test_a_sum_counts_the_runs_whose_cost_is_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            j.started("w", "0009_x", "spec", "autonomous")
            j.finished("w", "0009_x", "spec", "done", turns=4, cost_usd=0.52)
            j.started("w", "0009_x", "impl", "autonomous")
            j.finished("w", "0009_x", "impl", "failed", turns=109, cost_unknown=True)
            j.started("w", "0009_x", "impl", "autonomous")  # still running: not unknown
            got = totals_of(j.timeline("w", "0009_x"))
            self.assertEqual((got["cost_usd"], got["unknown"]), (0.52, 1))


WRITER = """
import sys
from coscc.store.journal import Journal
j = Journal(sys.argv[1], sys.argv[1])
tag = sys.argv[2]
for i in range({per_writer}):
    j.set_mode("w", "unit-%s-%d" % (tag, i), "impl", "autonomous")
""".format(per_writer=PER_WRITER)


class FourProcessesLoseNothing(unittest.TestCase):
    def test_every_record_survives_concurrent_writers(self):
        repo = Path(__file__).resolve().parents[2]
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
                len(records),
                WRITERS * PER_WRITER,
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


class AppendCheckedReadsAndWritesInOneTransaction(unittest.TestCase):
    """The check sees the rows of its kinds; a refusal writes nothing."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.j = Journal(self._tmp.name, self._tmp.name)

    def test_a_refusal_inserts_nothing(self):
        def no(rows):
            raise BadRecord("no")

        with self.assertRaises(BadRecord):
            self.j.append_checked(
                {"kind": "shortlist", "workspace": "w", "units": ["a"]}, ["shortlist"], no
            )
        self.assertEqual(self.j.records("w"), [])

    def test_the_check_sees_only_its_kinds_in_its_workspace(self):
        self.j.append({"kind": "shortlist", "workspace": "w", "units": ["a"]})
        self.j.append({"kind": "shortlist", "workspace": "other", "units": ["b"]})
        self.j.append(
            {"kind": "start", "workspace": "w", "unit": "a", "stage": "spec", "mode": "manual"}
        )
        seen = []
        self.j.append_checked(
            {"kind": "relation", "workspace": "w"}, ["shortlist", "relation"], seen.append
        )
        self.assertEqual([r["units"] for r in seen[0]], [["a"]])
        self.assertEqual(len(self.j.records("w", kind="relation")), 1)

    def test_two_threads_checking_each_others_rows_do_not_both_pass(self):
        import threading

        barrier = threading.Barrier(2)
        errors = []

        def only_first(rows):
            if rows:
                raise BadRecord("already one")

        def go():
            barrier.wait()
            try:
                Journal(self._tmp.name, self._tmp.name).append_checked(
                    {"kind": "shortlist", "workspace": "w", "units": ["a"]},
                    ["shortlist"],
                    only_first,
                )
            except BadRecord as e:
                errors.append(e)

        threads = [threading.Thread(target=go) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self.j.records("w", kind="shortlist")), 1)
        self.assertEqual(len(errors), 1)


class TheBellWakesAReaderAndTheReadsNarrow(unittest.TestCase):
    """What the notice stream reads, and what wakes it."""

    SOURCE = ("autopilot-stop", "questions", "end", "merge")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.j = Journal(self._tmp.name, self._tmp.name)

    def test_an_append_wakes_a_waiter_armed_before_it(self):
        async def go():
            ticket = BELL.arm()
            self.j.append({"kind": "note"})
            return await BELL.wait(ticket, 5)

        self.assertTrue(asyncio.run(go()))

    def test_an_append_from_another_thread_wakes_the_loop(self):
        async def go():
            ticket = BELL.arm()
            threading.Thread(target=lambda: self.j.append({"kind": "note"})).start()
            began = time.monotonic()
            rung = await BELL.wait(ticket, 5)
            return rung, time.monotonic() - began

        rung, took = asyncio.run(go())
        self.assertTrue(rung)
        self.assertLess(took, 5)

    def test_a_ring_after_the_loop_closed_does_not_fail_the_append(self):
        loop = asyncio.new_event_loop()
        ticket = loop.run_until_complete(_arm(BELL))
        loop.close()
        self.j.append({"kind": "note"})
        self.assertEqual(len(self.j.records()), 1)
        BELL.disarm(ticket)

    def test_notice_rows_are_past_after_in_id_order_and_only_source_kinds(self):
        self.j.append(
            {"kind": "end", "workspace": "w", "unit": "u", "stage": "spec", "outcome": "failed"}
        )
        self.j.append(
            {"kind": "start", "workspace": "w", "unit": "u", "stage": "spec", "mode": "manual"}
        )
        self.j.append({"kind": "merge", "workspace": "w", "unit": "u", "result": "shipped"})
        self.j.append({"kind": "questions", "workspace": "w", "unit": "u"})
        rows = self.j.notice_rows(0, self.SOURCE)
        self.assertEqual([r["kind"] for _, r in rows], ["end", "merge", "questions"])
        ids = [i for i, _ in rows]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(
            [r["kind"] for _, r in self.j.notice_rows(ids[0], self.SOURCE)], ["merge", "questions"]
        )
        self.assertEqual(len(self.j.notice_rows(0, self.SOURCE, limit=1)), 1)

    def test_notice_rows_narrow_to_one_workspace(self):
        self.j.append({"kind": "merge", "workspace": "w", "unit": "u", "result": "shipped"})
        self.j.append({"kind": "merge", "workspace": "other", "unit": "u", "result": "shipped"})
        self.assertEqual(
            [r["workspace"] for _, r in self.j.notice_rows(0, self.SOURCE, "w")], ["w"]
        )
        self.assertEqual(len(self.j.notice_rows(0, self.SOURCE)), 2)


async def _arm(bell):
    return bell.arm()


class ASuspendedSessionIsTakenUpOnce(unittest.TestCase):
    def test_a_suspend_row_without_a_resume_row_is_unresumed(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            a = j.suspended("w1", "0001_a", "impl", session_id="s1")
            j.suspended("w2", "", "estimate", session_id="s2")
            self.assertEqual([r["session_id"] for r in j.unresumed()], ["s1", "s2"])
            self.assertEqual(len(a["suspend_id"]), 32)

    def test_a_resume_row_closes_its_suspend_row_only(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            a = j.suspended("w", "0001_a", "impl")
            b = j.suspended("w", "0002_b", "impl")
            j.resumed("w", "0001_a", "impl", a["suspend_id"], by="app", result="resumed")
            self.assertEqual([r["suspend_id"] for r in j.unresumed()], [b["suspend_id"]])

    def test_suspend_and_resume_rows_do_not_close_a_start_row(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            j.started("w", "0001_a", "impl", "manual")
            s = j.suspended("w", "0001_a", "impl")
            j.resumed("w", "0001_a", "impl", s["suspend_id"])
            self.assertIn("0001_a", j.open_starts("w"))
            self.assertIsNone(j.timeline("w", "0001_a")[0]["ended"])


if __name__ == "__main__":
    unittest.main()
