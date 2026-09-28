"""Tests for `UpdateMixin` in `coscc/service/update.py`, split from `coscc/service/service_test.py` (`0095`).
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from coscc.config import Config
from coscc.service import Service
from coscc.agent.sessions import Sessions


class TheUpdateWindow(unittest.IsolatedAsyncioTestCase):
    """`0068` R8 and R11, and `0138`'s one Apply, at the `Service` seam."""

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

    async def test_apply_waits_only_for_a_mechanical_integration_or_a_retake(self):
        # `0138` R2, R3, C10: a step, an estimate, Jera and a chat turn are paused, not waited for.
        self.s.steps.claim("/w", "0001_a", "impl")
        self.s._mark_running("/w", "0002_b", "integrate", "rebase")
        self.s._mark_running("/w", "0003_c", "impl", "step")
        self.s._mark_running("/w", "", "estimate", "estimate")
        self.s._mark_running("/w", "0004_d", "precedent", "precedent")
        self.s.sessions._begin_turn("/w", "sid")
        self.s._retakes["r"] = {"workspace": "/w", "unit": "0005_e", "started": "t"}
        waited = sorted((j["stage"], j["unit"]) for j in self.s._update_waited())
        self.assertEqual(waited, [("integrate", "0002_b"), ("screens", "0005_e")])

    async def test_a_gebo_integration_is_suspended_not_waited_for(self):
        rid = self.s._mark_running("/w", "0002_b", "integrate", "rebase")
        self.assertEqual(len(self.s._update_waited()), 1)
        # `service/steps.py`: GitHub refused the rebase and the press agreed to Gebo.
        self.s._running[rid]["kind"] = "gebo"
        self.assertEqual(self.s._update_waited(), [])

    async def test_suspend_rows_are_written_before_hand_off(self):
        # `0138` R6: one `suspend` row per paused session, with who pressed Apply; a stream
        # whose caller named no owner is closed and has none.
        root = Path(self.tmp.name)
        config = Config(workspaces=(self.tmp.name,), working_dir=str(root / "work"), data_dir=str(root / "data"))
        s = Service(config, Sessions(config))
        owner = {"kind": "step", "workspace": "/w", "unit": "0001_a", "stage": "impl", "start_at": "t0"}

        async def suspend_all():
            return [{"owner": owner, "cwd": "/w/tree", "session_id": "sid", "model": "m", "start_at": "t0",
                     "boundary": 7, "safe_uuid": "u1", "dropped": [], "api_calls": 3, "spent_usd": 0.5},
                    {"owner": {}, "cwd": "/w", "session_id": "", "model": None, "start_at": None,
                     "unresumable": "no session id yet"}]

        s.sessions.suspend_all = suspend_all  # type: ignore[method-assign]
        written = await s.suspend_sessions("an")
        self.assertEqual(len(written), 1)
        rows = s._journal().unresumed()
        self.assertEqual([(r["unit"], r["stage"], r["by"], r["session_id"], r["safe_uuid"]) for r in rows],
                         [("0001_a", "impl", "an", "sid", "u1")])
        self.assertTrue(rows[0]["suspend_id"])

    async def test_a_step_with_no_session_open_finishes_before_the_settle_ends(self):
        # Review round 2, F6: a step between its `end` and its `finally` -- posting a round,
        # syncing `pr.md` -- had nothing to pause; the settle waits for its `_running` entry.
        # Past that entry, in `_after_end`, is round 3's F6, driven through `_drive` in
        # `steps_test.py`.
        rid = self.s._mark_running("/w", "0001_a", "review", "step")

        async def posts_its_round():
            await asyncio.sleep(0.05)
            self.s._running.pop(rid, None)

        task = asyncio.create_task(posts_its_round())
        self.assertEqual(await self.s.settle_after_suspend(5), [])
        self.assertTrue(task.done())

    async def test_what_outlives_the_settle_is_returned_to_be_named(self):
        self.s._mark_running("/w", "0002_b", "integrate", "gebo")
        left = await self.s.settle_after_suspend(0.05)
        self.assertEqual([(j["kind"], j["unit"], j["stage"]) for j in left], [("gebo", "0002_b", "integrate")])

    def test_the_routes_seam_refuses_where_updates_are_not_available(self):
        from coscc.service import NotUpdatable

        self.assertEqual(self.s.update_status()["shape"], "unavailable")
        self.assertFalse(hasattr(self.s, "update_cut_list"))
        with self.assertRaises(NotUpdatable):
            asyncio.run(self.s.update_apply("release", "an"))
        with self.assertRaises(NotUpdatable):
            self.s.update_build_local("an")


class WhatThePanelSays(unittest.TestCase):
    """`0138` R12, through `update_words`, the words `/settings` shows."""

    def test_the_updates_section_shows_one_apply_per_channel(self):
        from coscc.service.update import update_words

        words = update_words({"shape": "service", "state": "idle", "release": {"state": "ready", "version": "0.13.0"},
                              "local": {"state": "ready", "version": "0.12.0+gabc"}})
        self.assertEqual([a for a in words["actions"] if a.startswith(("apply-", "now-"))],
                         ["apply-release", "apply-local"])

    def test_pending_says_what_it_waits_for_in_one_sentence(self):
        from coscc.service.update import update_words
        from coscc.update import updater

        words = update_words({"shape": "service", "state": "pending", "release": {"state": "ready"}, "local": {}})
        self.assertEqual(words["line"],
                         "An update waits for an integration, a screenshot retake or a knowledge gather to finish.")
        self.assertEqual(words["line"], updater.WAITING_WARNING)
        self.assertEqual(words["line"].count("."), 1)
        self.assertNotIn("apply-release", words["actions"])
        self.assertIn("cancel", words["actions"])
        self.assertEqual(update_words({"shape": "service", "state": "applying", "release": {}, "local": {}})["line"], "Updating now.")
