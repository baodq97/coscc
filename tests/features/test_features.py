"""`coscc/features/__init__.py` and `coscc/plugin.py`: the list is the only place a feature is named."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from coscc import screens
from coscc.data import Data
from coscc.plugin import Plugin
from coscc.api import build
from coscc.config import Config


class Setup(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.ws = root / "work" / "proj"
        self.ws.mkdir(parents=True)
        self.config = Config(
            workspaces=(str(self.ws),), working_dir=str(root / "work"), data_dir=str(root / "data")
        )

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=build(self.config)), base_url="http://t"
        )


class TakingTheLineOutRemovesTheFeature(Setup):
    async def test_with_no_features_there_is_no_route_no_script_and_the_rest_answers(self):
        with mock.patch("coscc.features.FEATURES", ()):
            async with self.client() as client:
                self.assertEqual((await client.get("/api/notices/follow")).status_code, 404)
                self.assertEqual((await client.get("/api/health")).status_code, 200)
                board = await client.get("/api/board", params={"cwd": str(self.ws)})
                self.assertEqual(board.status_code, 200)
            shell = json.dumps(screens.index().render(), ensure_ascii=False, default=str)
        self.assertNotIn("__coscc_notices", shell)

    async def test_with_the_list_as_shipped_both_are_there(self):
        async with self.client() as client:
            self.assertEqual((await client.get("/api/notices/follow")).status_code, 200)
        self.assertIn("__coscc_notices", json.dumps(screens.index().render(), default=str))


class ATableIsCreatedAtBuild(Setup):
    def fake(self, *tables: str) -> Plugin:
        return Plugin("fake", lambda _ctx: [], tables=tables)

    def test_a_feature_table_exists_after_build_and_a_second_build_is_harmless(self):
        fake = self.fake("CREATE TABLE IF NOT EXISTS fake_things (id INTEGER PRIMARY KEY)")
        with mock.patch("coscc.features.FEATURES", (fake,)):
            build(self.config)
            build(self.config)
        with Data(self.config.data_dir).connect() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE name = 'fake_things'"
            ).fetchone()
        self.assertIsNotNone(row)

    def test_a_statement_that_is_not_a_create_table_raises_at_build(self):
        for bad in ("DROP TABLE prefs", "CREATE TABLE t (a INTEGER)", "SELECT 1"):
            with mock.patch("coscc.features.FEATURES", (self.fake(bad),)):
                with self.assertRaises(ValueError):
                    build(self.config)


class TurningAFeatureOffForAWorkspace(Setup):
    async def test_the_switch_reads_back_and_a_second_workspace_is_untouched(self):
        other = self.ws.parent / "other"
        other.mkdir()
        self.config = Config(
            workspaces=(str(self.ws), str(other)),
            working_dir=self.config.working_dir,
            data_dir=self.config.data_dir,
        )
        async with self.client() as client:
            got = await client.get("/api/features", params={"cwd": str(self.ws)})
            self.assertEqual((got.status_code, got.json()["notices"]), (200, True))
            off = await client.post(
                "/api/features", json={"cwd": str(self.ws), "name": "notices", "on": False}
            )
            self.assertEqual((off.status_code, off.json()), (200, {"name": "notices", "on": False}))
            got = await client.get("/api/features", params={"cwd": str(self.ws)})
            self.assertIs(got.json()["notices"], False)
            got = await client.get("/api/features", params={"cwd": str(other)})
            self.assertIs(got.json()["notices"], True)
            await client.post(
                "/api/features", json={"cwd": str(self.ws), "name": "notices", "on": True}
            )
            got = await client.get("/api/features", params={"cwd": str(self.ws)})
            self.assertIs(got.json()["notices"], True)

    async def test_a_wrong_request_is_a_400_and_writes_nothing(self):
        async with self.client() as client:
            good = {"cwd": str(self.ws), "name": "notices", "on": False}
            for body in (
                {**good, "name": "nope"},
                {**good, "cwd": "/etc"},
                {**good, "on": "no"},
                {k: v for k, v in good.items() if k != "on"},
            ):
                r = await client.post("/api/features", json=body)
                self.assertEqual(r.status_code, 400, body)
                self.assertIn("error", r.json())
            self.assertEqual((await client.post("/api/features", json=[1])).status_code, 400)
            self.assertEqual(
                (await client.get("/api/features", params={"cwd": "/etc"})).status_code, 400
            )
            got = await client.get("/api/features", params={"cwd": str(self.ws)})
            self.assertIs(got.json()["notices"], True)

    async def test_the_routes_are_behind_the_login(self):
        from coscc import auth

        paths = {r.path for r in build(self.config).routes}
        self.assertIn("/api/features", paths)
        self.assertNotIn("/api/features", {path for _, path in auth.EXEMPT})


if __name__ == "__main__":
    unittest.main()
