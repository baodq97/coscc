"""Tests for pausing the app's sessions for an update (`Resume.suspend_sessions`,
`settle_after_suspend`, `update_waited`) and the refusals while one waits."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coscc.config import Config
from coscc.http.app import Core
from coscc.agent.sessions import Sessions


class TheUpdateWindow(unittest.IsolatedAsyncioTestCase):
    """The update window and the one Apply per channel, at the `Core` seam."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config = Config(workspaces=(self.tmp.name,), data_dir=str(Path(self.tmp.name) / "data"))
        self.s = Core(config, Sessions(config))

    def tearDown(self):
        self.tmp.cleanup()

    async def test_run_integrate_and_send_refuse_with_updating(self):
        from coscc.runner.queue import Updating

        self.s.updater.window = True
        with self.assertRaises(Updating):
            await self.s.steps.run_step(self.tmp.name, "0001_a", "impl").__anext__()
        with self.assertRaises(Updating):
            await self.s.integration.integrate(self.tmp.name, "0001_a").__anext__()
        with self.assertRaises(Updating):
            self.s.chat.check_send(self.tmp.name, "hi")
        self.s.updater.window = False
        self.s.chat.check_send(self.tmp.name, "hi")

    async def test_apply_waits_only_for_a_mechanical_integration_or_a_retake(self):
        # A step, an estimate and a chat turn are paused, not waited for.
        held = self.s.attempts
        held.open("step", "/w", "0001_a", "impl", state="running")
        held.open("integration", "/w", "0002_b", "integrate", state="running")
        held.open("step", "/w", "0003_c", "impl", state="running")
        held.open("estimate", "/w", "", "estimate", state="running")
        self.s.sessions._begin_turn("/w", "sid")
        self.s.steps.retakes["r"] = {"workspace": "/w", "unit": "0005_e", "started": "t"}
        waited = sorted((j["stage"], j["unit"]) for j in self.s.resume.update_waited())
        self.assertEqual(waited, [("integrate", "0002_b"), ("screens", "0005_e")])

    async def test_a_gebo_integration_is_suspended_not_waited_for(self):
        row = self.s.attempts.open("integration", "/w", "0002_b", "integrate", state="running")
        self.assertEqual(len(self.s.resume.update_waited()), 1)
        # `runner/steps.py`: GitHub refused the rebase and the press agreed to Gebo.
        self.s.attempts.set_road(row["id"], "gebo")
        self.assertEqual(self.s.resume.update_waited(), [])

    async def test_suspend_rows_are_written_before_hand_off(self):
        # One `suspend` row per paused session, with who pressed Apply; a stream whose caller named
        # no owner is closed and has none.
        root = Path(self.tmp.name)
        config = Config(
            workspaces=(self.tmp.name,), working_dir=str(root / "work"), data_dir=str(root / "data")
        )
        s = Core(config, Sessions(config))
        owner = {
            "kind": "step",
            "workspace": "/w",
            "unit": "0001_a",
            "stage": "impl",
            "start_at": "t0",
        }

        async def suspend_all():
            return [
                {
                    "owner": owner,
                    "cwd": "/w/tree",
                    "session_id": "sid",
                    "model": "m",
                    "start_at": "t0",
                    "boundary": 7,
                    "safe_uuid": "u1",
                    "dropped": [],
                    "api_calls": 3,
                    "spent_usd": 0.5,
                },
                {
                    "owner": {},
                    "cwd": "/w",
                    "session_id": "",
                    "model": None,
                    "start_at": None,
                    "unresumable": "no session id yet",
                },
            ]

        s.sessions.suspend_all = suspend_all  # type: ignore[method-assign]
        written = await s.resume.suspend_sessions("an")
        self.assertEqual(len(written), 1)
        rows = s.ws.journal().unresumed()
        self.assertEqual(
            [(r["unit"], r["stage"], r["by"], r["session_id"], r["safe_uuid"]) for r in rows],
            [("0001_a", "impl", "an", "sid", "u1")],
        )
        self.assertTrue(rows[0]["suspend_id"])

    async def test_what_outlives_the_settle_is_returned_to_be_named(self):
        row = self.s.attempts.open("integration", "/w", "0002_b", "integrate", state="running")
        self.s.attempts.set_road(row["id"], "gebo")
        left = await self.s.resume.settle_after_suspend(0.05)
        self.assertEqual(
            [(j["kind"], j["unit"], j["stage"]) for j in left], [("gebo", "0002_b", "integrate")]
        )
