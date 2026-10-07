"""Tests for the board over HTTP, driven in-process the way the proof commands are.

The workspace under test is this repository itself, because it is the only one with real work units
in it."""

from __future__ import annotations

import asyncio

import tempfile
import unittest
from pathlib import Path

import httpx

from coscc.http.app import build
from coscc.config import Config

REPO = Path(__file__).resolve().parents[2]
STAGES = ["idea", "intent", "spec", "spike", "plan", "impl", "pr", "review", "ship"]


def seed_store(data_dir, workspace=REPO) -> Path:
    """Copy this repository's real `.cos/` into the product's store for `workspace`.

    Copied rather than pointed at, because these tests drive routes that could write.

    The fixture stays this repository's own `.cos/` for the reason `tests/units/test_board.py:1-7`
    gives: what breaks here is the *agreement* with the loop, and a hand-built fixture
    keeps passing after the two drift apart."""
    import shutil

    from coscc import units

    store = units.cos_dir(workspace, data_dir)
    store.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(Path(workspace) / ".cos", store, dirs_exist_ok=True)
    return store


def _a_unit(body: dict) -> str:
    """Any unit name, taken from the board itself.

    Naming one here pinned these tests to a numbering that renumbering breaks; what they
    actually need is a unit that exists.
    """
    return body["units"][0]["name"]


class BoardOverHttp(unittest.IsolatedAsyncioTestCase):
    """A working folder exists, so modes can be recorded."""

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = build(
            Config(
                workspaces=(str(REPO),),
                working_dir=self._tmp.name,
                data_dir=self._tmp.name,
            )
        )
        seed_store(self._tmp.name)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        # A change re-reads the board in the background; it must end before its folder goes.
        await self.app.state.core.shutdown()
        self._tmp.cleanup()

    async def test_a_board_read_walks_the_packs_folder_once_not_per_row_asked(self):
        from unittest import mock

        from coscc.agent import pack

        with mock.patch.object(pack, "_stamp", wraps=pack._stamp) as walked:
            board = await self.app.state.core.boards.read(str(REPO))
        self.assertTrue(board["units"])
        # Once for the app's read and once for the loop's: it was once per row asked.
        self.assertLessEqual(walked.call_count, 2)

    async def test_a_directory_that_is_not_a_workspace_is_refused(self):
        r = await self.client.get("/api/units", params={"cwd": "/etc"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("not a configured workspace", r.json()["error"])

    async def test_a_mode_set_over_http_is_what_the_run_log_holds_for_that_step_alone(self):
        first = await self.app.state.core.board(str(REPO), "held")
        unit = _a_unit(first)
        payload = {"cwd": str(REPO), "unit": unit, "stage": "impl", "mode": "autonomous"}
        r = await self.client.post("/api/board/mode", json=payload)
        self.assertEqual(r.status_code, 200, r.text)
        core = self.app.state.core
        modes = core.ws.journal().modes(core.ws.key(str(REPO)))
        self.assertEqual(modes[(unit, "impl")], "autonomous")
        self.assertNotIn((unit, "spec"), modes)

    async def test_a_mode_is_validated_against_the_board_not_a_second_list(self):
        body = await self.app.state.core.board(str(REPO), "held")
        unit = _a_unit(body)
        for bad, expected in (
            ({"unit": "9999_not-here", "stage": "impl", "mode": "manual"}, "no such work unit"),
            ({"unit": unit, "stage": "deploy", "mode": "manual"}, "no such stage"),
            ({"unit": unit, "stage": "impl", "mode": "turbo"}, "mode must be one of"),
        ):
            r = await self.client.post("/api/board/mode", json={"cwd": str(REPO), **bad})
            self.assertEqual(r.status_code, 400, r.text)
            self.assertIn(expected, r.json()["error"])

    async def test_a_body_that_is_not_json_is_a_status_code_not_a_crash(self):
        r = await self.client.post(
            "/api/board/mode", content=b"not json", headers={"content-type": "application/json"}
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("send a JSON object", r.json()["error"])


class TheHeldBoard(unittest.IsolatedAsyncioTestCase):
    """The board answers what the last read held; a `new` read waits for a fresh one."""

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = build(Config(workspaces=(str(REPO),), data_dir=self._tmp.name))
        self.store = seed_store(self._tmp.name)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )
        self.addAsyncCleanup(self.client.aclose)

    async def get(self, **params):
        return await self.app.state.core.board(str(REPO), "new" if params.get("fresh") else "held")

    async def test_a_change_outside_the_app_shows_on_a_fresh_read_only(self):
        first = await self.get()
        (self.store / "9999_made-outside").mkdir()
        (self.store / "9999_made-outside" / "intent.md").write_text("# made outside\n")

        held = await self.get()
        self.assertEqual(held["read_at"], first["read_at"])
        self.assertEqual(held["count"], first["count"])

        fresh = await self.get(fresh="1")
        self.assertGreaterEqual(fresh["read_at"], first["read_at"])
        self.assertEqual(fresh["count"], first["count"] + 1)
        self.assertIn("9999_made-outside", [u["name"] for u in fresh["units"]])


class TheBoardIsReadWhenTheAppStarts(unittest.IsolatedAsyncioTestCase):
    """The served app's start reads every board once; an app built for a test does not."""

    async def test_the_first_board_opened_finds_one_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = build(Config(workspaces=(str(REPO),), data_dir=tmp), starting=True)
            core = app.state.core
            seed_store(tmp)
            warmed = asyncio.Event()
            real = core.boards.warm

            async def warm() -> None:
                await real()
                warmed.set()

            core.boards.warm = warm
            async with app.router.lifespan_context(app):
                await asyncio.wait_for(warmed.wait(), 60)
            held = core.boards.held[core.ws.key(str(REPO))]
            self.assertTrue(held["read_at"])
            self.assertEqual(held["data"]["stages"], STAGES)

    async def test_an_app_not_starting_reads_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = build(Config(workspaces=(str(REPO),), data_dir=tmp))
            async with app.router.lifespan_context(app):
                pass
            self.assertEqual(app.state.core.boards.held, {})


class WithNoWorkingFolder(unittest.IsolatedAsyncioTestCase):
    """The board still reads, but nothing can be recorded."""

    async def asyncSetUp(self):
        # A data root is still set: `working_dir` and `data_dir` are different knobs, and
        # leaving this one unset would point the store at the real `~/.cos`.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = build(Config(workspaces=(str(REPO),), data_dir=self._tmp.name))
        seed_store(self._tmp.name)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_setting_a_mode_is_refused_with_the_same_reason(self):
        body = await self.app.state.core.board(str(REPO), "held")
        r = await self.client.post(
            "/api/board/mode",
            json={"cwd": str(REPO), "unit": _a_unit(body), "stage": "impl", "mode": "autonomous"},
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("COS_WORKING_DIR", r.json()["error"])


if __name__ == "__main__":
    unittest.main()
