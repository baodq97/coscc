"""Tests for `UpdateMixin` in `coscc/service_update.py`, split from `coscc/service_test.py` (`0095`).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coscc.config import Config
from coscc.service import Service
from coscc.sessions import Sessions


class TheUpdateWindow(unittest.IsolatedAsyncioTestCase):
    """`0068` R8, R10 and R11, at the `Service` seam."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config = Config(workspaces=(self.tmp.name,), data_dir=str(Path(self.tmp.name) / "data"))
        self.s = Service(config, Sessions(config))

    def tearDown(self):
        self.tmp.cleanup()

    async def test_r11_run_integrate_and_send_refuse_with_updating(self):
        from coscc.service import Updating

        self.s.updater.window = True
        with self.assertRaises(Updating):
            await self.s.run_step(self.tmp.name, "0001_a", "impl").__anext__()
        with self.assertRaises(Updating):
            await self.s.integrate(self.tmp.name, "0001_a").__anext__()
        with self.assertRaises(Updating):
            self.s.check_send(self.tmp.name, "hi")
        self.s.updater.window = False
        self.s.check_send(self.tmp.name, "hi")

    async def test_r8_steps_integrations_and_chat_turns_are_jobs(self):
        self.s.steps.claim("/w", "0001_a", "impl")
        self.s._mark_running("/w", "0002_b", "integrate", "rebase")
        self.s._mark_running("/w", "0003_c", "impl", "step")  # a step: already in `steps`
        self.s.sessions._begin_turn("/w", "sid")
        kinds = sorted(j["kind"] for j in self.s._update_jobs())
        self.assertEqual(kinds, ["chat", "integration", "step"])

    async def test_r10_a_cut_step_goes_through_stop(self):
        running = self.s.steps.claim("/w", "0001_a", "impl")
        job = next(j for j in self.s._update_jobs() if j["kind"] == "step")
        self.assertTrue(await self.s._update_cut(job, "an"))
        self.assertTrue(running.stop_requested)
        self.assertEqual(running.stopped_by, "an")
        integration = {"kind": "integration", "workspace": "/w", "unit": "0002_b"}
        self.assertFalse(await self.s._update_cut(integration, "an"))

    def test_the_routes_seam_refuses_where_updates_are_not_available(self):
        from coscc.service import NotUpdatable

        self.assertEqual(self.s.update_status()["shape"], "unavailable")
        with self.assertRaises(NotUpdatable):
            self.s.update_cut_list()
        with self.assertRaises(NotUpdatable):
            self.s.update_build_local("an")
