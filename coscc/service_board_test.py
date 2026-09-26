"""Tests for `BoardMixin` in `coscc/service_board.py`, split from `coscc/service_test.py` (`0095`).
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.config import Config
from coscc.service_common import Invalid
from coscc.service import Service
from coscc.sessions import Sessions
from coscc.service_test import create_sync


class WhatIsRunningIsKeptWhileItRuns(unittest.TestCase):
    """`0051` plan step 3: one `_running` entry per step, gone however the step ends."""

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

    def test_one_entry_while_the_step_runs_and_none_after_done(self):
        self._run("spec")
        [[entry]] = self.seen
        self.assertEqual((entry["unit"], entry["stage"], entry["kind"]), (self.unit, "spec", "step"))
        self.assertIsNone(entry["turns"])
        self.assertIsNone(entry["cost_usd"])
        self.assertEqual(self.service._running, {})

    def test_none_after_a_run_error(self):
        from coscc.runner import RunError

        async def fails(*a, **kw):
            self.seen.append(list(self.service._running.values()))
            raise RunError("stand-in")
            yield  # pragma: no cover

        with mock.patch("coscc.service.Runner.run", fails):
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
    """`0051` plan step 4: `running` and `unknown_end`, and what keeps them apart."""

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
        self.assertEqual(got["unknown_end"], {"0009_x": [{"stage": "plan", "started": rec["at"]}]})

    def test_a_later_start_retires_the_orphan(self):
        self.journal.append({
            "kind": "start", "workspace": self.key, "unit": "0009_x", "stage": "plan",
            "mode": "manual", "at": "2026-09-24T01:00:00+00:00",
        })
        self.journal.started(self.key, "0009_x", "impl", "manual")
        self.journal.finished(self.key, "0009_x", "impl", "done")
        self.assertEqual(self.service.running(self.cwd)["unknown_end"], {})

    def test_an_orphan_older_than_a_day_is_not_shown(self):
        self.journal.append({
            "kind": "start", "workspace": self.key, "unit": "0009_x", "stage": "plan",
            "mode": "manual", "at": "2020-01-01T00:00:00+00:00",
        })
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
        from coscc.journal import Busy, Journal

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
