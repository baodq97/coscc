"""Tests for `BoardMixin` in `coscc/service/board.py`, split from `coscc/service/service_test.py`."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.config import Config
from coscc.service.common import Invalid
from coscc.service import Service
from coscc.agent.sessions import Sessions
from coscc.service.service_test import create_sync
from coscc.agent.submit_test import submits as _submits


class WhatIsRunningIsKeptWhileItRuns(unittest.TestCase):
    """One `_running` entry per step, gone however the step ends."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "work" / "proj"
        self.repo.mkdir(parents=True)
        self.seen: list[list[dict]] = []
        test = self

        class Looks:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                test.seen.append(list(test.service._running.values()))
                yield ("chunk", "# Spec: a problem\nAuthor: t. Status: accepted.\n\n## Body\n")
                await _submits(kw)
                yield ("done", {"session_id": "sess-51", "cost": {}})

        self.service = Service(
            Config(
                workspaces=(str(self.repo),),
                working_dir=str(self.root / "work"),
                data_dir=str(self.root / "data"),
            ),
            Looks(),
        )
        self.made = create_sync(self.service, str(self.repo), "a-problem", "some words")
        self.unit = self.made["unit"]
        (Path(self.made["path"]) / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
        )

    def _run(self, stage: str, stop_after: int | None = None):
        async def go():
            out = []
            agen = self.service.run_step(str(self.repo), self.unit, stage)
            try:
                async for item in agen:
                    out.append(item)
                    if stop_after is not None and len(out) >= stop_after:
                        break
            finally:
                await agen.aclose()
            return out

        return asyncio.run(go())

    def test_every_stage_column_carries_its_glyph_and_label(self):
        # Every stage the board read names, from the table, overrides included.
        self.service.set_agent("review", {"name": "Judge"})
        data = asyncio.run(self.service.board(str(self.repo)))
        self.assertEqual(set(data["stage_agents"]), set(data["stages"]) - {"pr", "ship"})
        self.assertEqual(
            data["stage_agents"]["plan"],
            {
                "glyph": "ᚱ",
                "label": "Raidho (agent, plan)",
                "meaning": "journey: the right road in the right order",
                "role": "Orders the work and names its proof, and writes no code.",
            },
        )
        self.assertEqual(data["stage_agents"]["review"]["label"], "Judge (agent, review)")
        self.assertEqual(data["stage_agents"]["spike"]["meaning"], "")

    def test_one_entry_while_the_step_runs_and_none_after_done(self):
        self._run("spec")
        [[entry]] = self.seen
        self.assertEqual(
            (entry["unit"], entry["stage"], entry["kind"]), (self.unit, "spec", "step")
        )
        self.assertIsNone(entry["turns"])
        self.assertIsNone(entry["cost_usd"])
        self.assertEqual(self.service._running, {})

    def test_none_after_a_run_error(self):
        from coscc.runner import RunError

        async def fails(*a, **kw):
            self.seen.append(list(self.service._running.values()))
            raise RunError("stand-in")
            yield  # pragma: no cover

        with mock.patch("coscc.runner.Runner.run", fails):
            with self.assertRaises(Invalid):
                self._run("spec")
        self.assertEqual(len(self.seen[0]), 1)
        self.assertEqual(self.service._running, {})

    def test_none_after_the_caller_goes_away_mid_step(self):
        self._run("spec", stop_after=1)
        self.assertEqual(len(self.seen[0]), 1)
        self.assertEqual(self.service._running, {})

    def test_a_step_the_gate_refuses_leaves_none(self):
        with self.assertRaises(Invalid):
            self._run("ship")
        self.assertEqual(self.service._running, {})

    def test_a_step_refused_as_busy_leaves_none_and_keeps_the_other(self):
        key = self.service._journal_key(str(self.repo))
        self.service._take(key, self.unit, "step", "spec")
        self.service._running["other"] = {"workspace": key, "unit": self.unit}
        with self.assertRaises(Invalid) as caught:
            self._run("spec")
        self.assertIn("a spec step is being prepared", str(caught.exception))
        self.assertEqual(list(self.service._running), ["other"])


class RunningAnswersFromMemoryAndTheRunLog(unittest.TestCase):
    """`running` and `unknown_end`, and what keeps them apart."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.repo = root / "work" / "proj"
        self.other = root / "work" / "other"
        self.repo.mkdir(parents=True)
        self.other.mkdir(parents=True)
        config = Config(
            workspaces=(str(self.repo), str(self.other)),
            working_dir=str(root / "work"),
            data_dir=str(root / "data"),
        )
        self.service = Service(config, Sessions(config))
        self.cwd = str(self.repo)
        self.key = self.service._journal_key(self.cwd)
        self.journal = self.service._journal()

    def test_an_entry_and_its_own_start_show_only_as_running(self):
        self.service._mark_running(self.key, "0009_x", "impl", "step")
        self.journal.started(self.key, "0009_x", "impl", "manual")
        got = self.service.running(self.cwd)
        [row] = got["running"]["0009_x"]
        self.assertEqual(row["stage"], "impl")
        self.assertEqual(row["agent"], {"glyph": "ᚢ", "name": "Uruz"})
        self.assertEqual(row["kind"], "step")
        self.assertIsNone(row["turns"])
        self.assertIsNone(row["cost_usd"])
        self.assertEqual(got["unknown_end"], {})

    def test_an_orphan_start_shows_as_ended_unknown(self):
        rec = self.journal.started(self.key, "0009_x", "plan", "manual")
        got = self.service.running(self.cwd)
        self.assertEqual(got["running"], {})
        # A start with no `agent` is named from its stage.
        self.assertEqual(
            got["unknown_end"],
            {"0009_x": [{"stage": "plan", "started": rec["at"], "agent": "Raidho"}]},
        )

    def test_an_orphan_start_keeps_the_name_it_was_written_with(self):
        self.journal.started(self.key, "0009_x", "plan", "manual", agent="Wayfarer")
        [row] = self.service.running(self.cwd)["unknown_end"]["0009_x"]
        self.assertEqual(row["agent"], "Wayfarer")

    def test_an_override_reaches_the_running_line(self):
        # The running line reads the one lookup, overrides included.
        self.service.set_agent("impl", {"name": "Builder", "glyph": "ᛒ"})
        self.service._mark_running(self.key, "0009_x", "impl", "step")
        [row] = self.service.running(self.cwd)["running"]["0009_x"]
        self.assertEqual(row["agent"], {"glyph": "ᛒ", "name": "Builder"})

    def test_a_later_start_retires_the_orphan(self):
        self.journal.append(
            {
                "kind": "start",
                "workspace": self.key,
                "unit": "0009_x",
                "stage": "plan",
                "mode": "manual",
                "at": "2026-09-24T01:00:00+00:00",
            }
        )
        self.journal.started(self.key, "0009_x", "impl", "manual")
        self.journal.finished(self.key, "0009_x", "impl", "done")
        self.assertEqual(self.service.running(self.cwd)["unknown_end"], {})

    def test_an_orphan_older_than_a_day_is_not_shown(self):
        self.journal.append(
            {
                "kind": "start",
                "workspace": self.key,
                "unit": "0009_x",
                "stage": "plan",
                "mode": "manual",
                "at": "2020-01-01T00:00:00+00:00",
            }
        )
        self.assertEqual(self.service.running(self.cwd)["unknown_end"], {})

    def test_two_workspaces_do_not_mix(self):
        other_key = self.service._journal_key(str(self.other))
        self.service._mark_running(other_key, "0009_x", "spec", "step")
        self.journal.started(other_key, "0010_y", "spec", "manual")
        self.assertEqual(self.service.running(self.cwd), {"running": {}, "unknown_end": {}})
        got = self.service.running(str(self.other))
        self.assertEqual(list(got["running"]), ["0009_x"])
        self.assertEqual(list(got["unknown_end"]), ["0010_y"])

    def test_gebo_is_named_and_a_rebase_is_not(self):
        self.service._mark_running(self.key, "0009_x", "integrate", "gebo")
        self.service._mark_running(self.key, "0010_y", "integrate", "rebase")
        got = self.service.running(self.cwd)["running"]
        self.assertEqual(got["0009_x"][0]["agent"], {"glyph": "ᚷ", "name": "Gebo"})
        self.assertIsNone(got["0010_y"][0]["agent"])
        self.assertEqual(got["0010_y"][0]["kind"], "rebase")

    def test_a_workspace_outside_the_list_is_refused(self):
        with self.assertRaises(Invalid):
            self.service.running("/nonexistent/elsewhere")

    def test_a_busy_run_log_is_a_note_not_a_refusal(self):
        from coscc.runlog.journal import Journal
        from coscc.data import Busy

        self.service._mark_running(self.key, "0009_x", "impl", "step")
        with mock.patch.object(Journal, "open_starts", side_effect=Busy("locked")):
            got = self.service.running(self.cwd)
        self.assertEqual(got["note"], "locked")
        self.assertEqual(list(got["running"]), ["0009_x"])
        self.assertEqual(got["unknown_end"], {})

    def test_no_working_folder_means_no_unknown_end(self):
        config = Config(workspaces=(self.cwd,))
        service = Service(config, Sessions(config))
        self.assertEqual(service.running(self.cwd), {"running": {}, "unknown_end": {}})


class TheGuide(unittest.TestCase):
    """`_guide_block` on memory."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        (root / "work" / "proj").mkdir(parents=True)
        config = Config(
            workspaces=(str(root / "work" / "proj"),),
            working_dir=str(root / "work"),
            data_dir=str(root / "data"),
        )
        self.service = Service(config, Sessions(config))
        self.cwd = str(root / "work" / "proj")
        self.key = self.service._journal_key(self.cwd)
        self.journal = self.service._journal()

    def block(self) -> dict:
        with mock.patch.object(self.service, "_autopilot_values", return_value={"autopilot": True}):
            return self.service._guide_block(self.key)

    def test_guide_lists_running_steps(self):
        self.service._mark_running(self.key, "0009_x", "impl", "step")
        self.service._mark_running(self.key, "0010_y", "spec", "step")
        self.service._mark_running("/elsewhere", "0011_z", "spec", "step")
        got = self.block()["running"]
        self.assertEqual(
            [(r["unit"], r["stage"], r["agent"]) for r in got],
            [("0009_x", "impl", "Uruz"), ("0010_y", "spec", "Kenaz")],
        )
        self.assertTrue(all(r["started"] for r in got))

    def test_guide_turns_every_stop_but_full_into_one_thing_to_do(self):
        kinds = ("a", "b", "c", "d", "e", "f", "cap", "reruns", "full")
        self.service._autopilot_stops[self.key] = {
            f"00{n:02d}_u": {"unit": f"00{n:02d}_u", "kind": k, "reason": f"why {k}"}
            for n, k in enumerate(kinds, 1)
        }
        self.service._autopilot_stops[self.key][""] = {
            "unit": "",
            "kind": "shortlist",
            "reason": "none",
        }
        got = self.block()["needs_you"]
        self.assertEqual([r["kind"] for r in got], [*kinds[:-1], "shortlist"])
        by = {r["kind"]: r for r in got}
        self.assertEqual(
            (by["a"]["unit"], by["a"]["screen"], by["a"]["tab"], by["a"]["reason"]),
            ("0001_u", "unit", "questions", "why a"),
        )
        self.assertEqual((by["cap"]["screen"], by["shortlist"]["screen"]), ("settings", "backlog"))

    def test_guide_says_only_that_the_autopilot_is_off_when_it_is(self):
        self.service._mark_running(self.key, "0009_x", "impl", "step")
        self.assertEqual(self.service._guide_block(self.key), {"on": False})
        self.assertEqual(asyncio.run(self.service.board(self.cwd))["guide"], {"on": False})
