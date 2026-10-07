"""Tests for a run that hit a ceiling and the raise that goes on from it: `_raised` and
`Steps.raise_step` in `coscc/runner/steps.py`, the ceiling's reading in `coscc/runner/run.py`."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from contextlib import suppress
from pathlib import Path
from unittest import mock

from coscc.agent import transcript
from coscc.agent.sessions import Sessions
from coscc.config import Config
from coscc.http.app import Core
from coscc.kernel import Invalid
from coscc.runner import run as run_mod
from coscc.runner.queue import Refused
from coscc.runner.steps import _raised
from coscc.store.journal import Journal, paused_of, paused_stage
from coscc.units import board as board_reader
from tests.http.test_app import create_sync

OWNER = {
    "kind": "step",
    "workspace": "/w",
    "workspace_dir": "/w",
    "unit": "0001_a",
    "stage": "impl",
    "artifact": "impl.md",
    "start_at": "t0",
    "max_turns": 250,
    "max_budget_usd": 4.0,
    "segments": [],
}
END = {
    "kind": "end",
    "stage": "impl",
    "outcome": "paused-budget",
    "ceiling": "usd",
    "max_budget_usd": 4.0,
    "max_turns": 250,
    "cost_usd": 3.9,
    "turns": 61,
    "session_id": "sess-1",
    "cwd": "/w",
    "model": "claude-opus-5-5",
    "owner": OWNER,
}


def a_transcript(directory: str) -> Path:
    """A session of two model calls and a safe point after the second's result."""
    path = Path(directory) / "sess-1.jsonl"
    lines = [
        {"type": "user", "uuid": "u1", "message": {"role": "user", "content": "go"}},
        {
            "type": "assistant",
            "uuid": "a1",
            "message": {"id": "m1", "content": [{"type": "text", "text": "reading"}]},
        },
        {
            "type": "assistant",
            "uuid": "a2",
            "message": {"id": "m2", "content": [{"type": "text", "text": "editing"}]},
        },
        {"type": "user", "uuid": "u2", "message": {"role": "user", "content": "more"}},
    ]
    path.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    return path


class TheCeilingASessionHit(unittest.TestCase):
    def test_it_names_which_one(self):
        self.assertEqual(run_mod.ceiling_of("max_turns"), "turns")
        self.assertEqual(run_mod.ceiling_of("error_max_turns"), "turns")
        self.assertEqual(run_mod.ceiling_of("error_max_budget_usd"), "usd")
        self.assertEqual(run_mod.ceiling_of("completed"), "")
        self.assertEqual(run_mod.ceiling_of(""), "")

    def test_a_ceiling_ends_a_board_step_paused_and_a_stop_cancelled(self):
        self.assertEqual(run_mod.status_of("paused-budget"), "paused-budget")
        self.assertEqual(run_mod.status_of("stopped"), "cancelled")
        self.assertEqual(run_mod.status_of("failed"), "failed")


class ARaiseGoesOnInTheSameSession(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = a_transcript(self._tmp.name)
        patch = mock.patch.object(transcript, "path_for", lambda cwd, sid: path)
        patch.start()
        self.addCleanup(patch.stop)

    def test_it_takes_up_the_session_with_the_new_ceiling_less_what_was_spent(self):
        record, raised = _raised(END, {"usd": 8})
        self.assertEqual((record["session_id"], record["cwd"]), ("sess-1", "/w"))
        self.assertEqual((record["safe_uuid"], record["api_calls"]), ("u2", 2))
        self.assertEqual(record["spent_usd"], 3.9)
        self.assertEqual(record["owner"]["max_budget_usd"], 8.0)
        self.assertEqual(record["owner"]["max_turns"], 250)
        self.assertEqual(record["owner"]["max_budget_source"], "raise")
        self.assertIn("$8", record["message"])
        turns, budget, used_up = transcript.ceilings_left(
            record["owner"]["max_turns"], record["owner"]["max_budget_usd"], record
        )
        self.assertEqual((turns, budget, used_up), (248, 4.1, ""))
        self.assertEqual(raised["from_usd"], 4.0)
        self.assertEqual(raised["max_budget_usd"], 8.0)

    def test_it_raises_a_turn_ceiling_too(self):
        record, _ = _raised({**END, "ceiling": "turns", "max_turns": 2}, {"turns": 10})
        self.assertEqual(record["owner"]["max_turns"], 10)
        self.assertEqual(record["owner"]["max_budget_usd"], 4.0)

    def test_the_ceiling_it_hit_must_go_up(self):
        for ceilings in ({}, {"usd": 4}, {"usd": 2}, {"turns": 500}):
            with self.assertRaises(Invalid, msg=str(ceilings)):
                _raised(END, ceilings)

    def test_a_dollar_raise_not_above_what_was_spent_is_refused(self):
        with self.assertRaises(Invalid) as caught:
            _raised({**END, "cost_usd": 4.2, "max_budget_usd": 4.0}, {"usd": 4.2})
        self.assertIn("$4.2", str(caught.exception))

    def test_a_ceiling_out_of_bounds_is_refused(self):
        with self.assertRaises(Invalid):
            _raised(END, {"usd": "lots"})
        with self.assertRaises(Invalid):
            _raised({**END, "ceiling": "turns", "max_turns": 2}, {"turns": 100_000})

    def test_a_turn_ceiling_below_the_turns_used_is_refused(self):
        with self.assertRaises(Invalid):
            _raised({**END, "ceiling": "turns", "max_turns": 1}, {"turns": 2})

    def test_a_session_whose_transcript_is_gone_cannot_be_taken_up(self):
        with mock.patch.object(transcript, "path_for", lambda cwd, sid: Path("/nowhere/x.jsonl")):
            with self.assertRaises(Invalid):
                _raised(END, {"usd": 8})


class ARaiseIsAPersonsAndOnlyOfAPausedStage(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "work" / "proj"
        (self.repo / ".git").mkdir(parents=True)
        config = Config(
            workspaces=(str(self.repo),),
            working_dir=str(self.root / "work"),
            data_dir=str(self.root / "data"),
        )
        self.core = Core(config, Sessions(config))
        made = create_sync(self.core, str(self.repo), "a-problem", "words")
        self.unit = made["unit"]
        self.key = self.core.ws.key(str(self.repo))
        self.journal = self.core.ws.journal()

    def pause(self, **end):
        self.journal.started(self.key, self.unit, "impl", "manual", run="r1")
        self.journal.finished(
            self.key,
            self.unit,
            "impl",
            "paused-budget",
            run="r1",
            **{**{k: v for k, v in END.items() if k not in ("kind", "stage", "outcome")}, **end},
        )

    def raise_(self, ceilings, **kw):
        async def go():
            async for _ in self.core.steps.raise_step(
                str(self.repo), self.unit, "impl", ceilings, **kw
            ):
                pass

        asyncio.run(go())

    def test_the_autopilot_never_raises(self):
        self.pause()
        with self.assertRaises(Refused) as caught:
            self.raise_({"usd": 8}, started_by="autopilot")
        self.assertEqual(caught.exception.reasons, ("rerun-by-person",))

    def test_a_stage_that_is_not_paused_has_nothing_to_raise(self):
        self.journal.started(self.key, self.unit, "impl", "manual", run="r1")
        self.journal.finished(self.key, self.unit, "impl", "done", run="r1")
        with self.assertRaises(Invalid):
            self.raise_({"usd": 8})

    def test_a_raise_takes_up_the_ended_session_and_writes_one_raise_row(self):
        self.pause()
        taken: list = []
        path = a_transcript(self._tmp.name)

        def resume_step(record, raised=None):
            taken.append((record, raised))
            raise Invalid("stop here")

        with (
            mock.patch.object(transcript, "path_for", lambda cwd, sid: path),
            mock.patch.object(self.core.steps, "resume_step", resume_step),
            self.assertRaises(Invalid),
        ):
            self.raise_({"usd": 8})
        ((record, raised),) = taken
        self.assertEqual(record["session_id"], "sess-1")
        self.assertEqual(record["owner"]["max_budget_usd"], 8.0)
        self.assertEqual(raised["by"], "owner")

    def test_the_raised_run_has_its_own_start_and_the_row_stays_one(self):
        from coscc.agent.sessions import Suspended

        here = {"workspace": self.key, "workspace_dir": str(self.repo), "unit": self.unit}
        self.pause(owner={**OWNER, **here})
        path = a_transcript(self._tmp.name)

        class Ends:
            def __init__(self, *a, **kw):
                pass

            async def run(self, **kw):
                raise Suspended("stop here")
                yield

        with (
            mock.patch.object(transcript, "path_for", lambda cwd, sid: path),
            mock.patch("coscc.runner.steps.Runner", Ends),
        ):
            with suppress(Exception):
                self.raise_({"usd": 8})
        [raise_row] = self.journal.records(self.key, self.unit, kind="raise")
        starts = self.journal.records(self.key, self.unit, kind="start")
        self.assertEqual(len(starts), 2)
        self.assertEqual(starts[1]["run"], raise_row["run"])
        self.assertEqual(
            (starts[1]["raised_by"], starts[1]["continues"], starts[1]["agent"]),
            ("owner", "r1", "impl"),
        )
        self.assertEqual(starts[1]["model"], END["model"])
        rows = self.journal.timeline(self.key, self.unit)
        self.assertEqual([r["run"] for r in rows], [raise_row["run"]])

    def test_the_run_log_shows_one_session_in_two_parts_and_holds_the_stage_until_then(self):
        self.pause()
        rows = self.journal.timeline(self.key, self.unit)
        self.assertEqual(paused_stage(rows, "impl")["ceiling"], "usd")
        self.journal.raised(self.key, self.unit, "impl", run="r2", by="owner", max_budget_usd=8.0)
        rows = self.journal.timeline(self.key, self.unit)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["ended"])
        self.assertEqual((rows[0]["run"], [p["run"] for p in rows[0]["parts"]]), ("r2", ["r1"]))
        self.assertIsNone(paused_stage(rows, "impl"))
        self.journal.finished(self.key, self.unit, "impl", "done", run="r2")
        rows = self.journal.timeline(self.key, self.unit)
        self.assertEqual((rows[0]["outcome"], len(rows)), ("done", 1))

    def test_a_plain_run_of_a_paused_stage_is_refused_and_a_rerun_is_not(self):
        self.pause()

        async def open_gate(units_root, unit, stage, repo=None, **kw):
            return True, f"open: {stage} may proceed"

        async def go(**kw):
            async for _ in self.core.steps.run_step(str(self.repo), self.unit, "impl", **kw):
                pass

        for name in ("intent.md", "spec.md", "plan.md"):
            (self.core.ws.unit_dir(str(self.repo), self.unit) / name).write_text("# x\n")
        with mock.patch.object(board_reader, "gate", open_gate):
            with self.assertRaises((Refused, Invalid)) as caught:
                asyncio.run(go())
        self.assertIn("budget-reached", getattr(caught.exception, "reasons", ()))

    def test_a_held_unit_is_not_raised(self):
        self.pause()

        async def read(*a, **kw):
            return {
                "stages": ["impl"],
                "units": [
                    {
                        "name": self.unit,
                        "hold": {"state": "dropped", "reason": "dropped"},
                        "stages": [{"stage": "impl"}],
                    }
                ],
            }

        with mock.patch.object(board_reader, "read", read), self.assertRaises(Refused) as caught:
            self.raise_({"usd": 8})
        self.assertEqual(caught.exception.reasons, ("held",))

    def test_a_rerun_of_a_paused_stage_is_still_a_persons_and_its_note_is_capped(self):
        self.pause()

        async def go(**kw):
            async for _ in self.core.steps.run_step(str(self.repo), self.unit, "impl", **kw):
                pass

        with self.assertRaises(Refused) as caught:
            asyncio.run(go(rerun=True, started_by="autopilot"))
        self.assertEqual(caught.exception.reasons, ("rerun-by-person",))
        with self.assertRaises(Invalid):
            asyncio.run(go(rerun=True, note="x" * 5000))


class ARaisedSessionCountsEachDollarOnce(unittest.TestCase):
    def test_pause_at_8_raise_finish_at_14_counts_14_in_the_card_insights_and_the_cap(self):
        from coscc.leif import decide, spend

        with tempfile.TemporaryDirectory() as d:
            journal = Journal(d, d)
            journal.started("k", "u", "impl", "manual", run="r1")
            journal.finished(
                "k", "u", "impl", "paused-budget", run="r1", cost_usd=8.0, max_budget_usd=8.0,
                ceiling="usd",
            )  # fmt: skip
            journal.raised("k", "u", "impl", run="r2", by="owner")
            journal.finished(
                "k", "u", "impl", "done", run="r2", cost_usd=6.0, session_cost_usd=14.0
            )
            [row] = journal.timeline("k", "u")
            self.assertEqual(row["cost"]["cost_usd"], 14.0)
            ends = journal.records("k", "u", kinds=("end",))
            self.assertEqual(sum(r["cost_usd"] for r in ends), 14.0)
            day = spend.local_day(ends[-1]["at"])
            self.assertEqual(decide.spent_on(ends, day)["known"], 14.0)
            acc = spend._zero()
            for r in ends:
                spend._add(acc, r)
            self.assertEqual(acc["usd"], 14.0)

    def test_a_second_pause_names_the_session_total_and_a_second_raise_checks_it(self):
        end = {**END, "cost_usd": 6.0, "session_cost_usd": 14.0, "max_budget_usd": 14.0}
        self.assertEqual(paused_of(end)["usd"], 14.0)
        with self.assertRaises(Invalid):
            _raised(end, {"usd": 12})
