"""`coscc/features/__init__.py` and `coscc/plugin.py`: the list is the only place a feature is named."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from coscc import screens
from coscc.data import Data
from coscc import features
from coscc.hooks import Block, Guard, Parts, Tool
from coscc.plugin import Ctx, Plugin, create_tables, ctx_of, hooks_of
from coscc.api import build
from coscc.config import PROTECTED_DB_VAR, Config


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


def table_exists(config: Config, name: str) -> bool:
    with Data(config.data_dir).connect() as conn:
        row = conn.execute("SELECT name FROM sqlite_master WHERE name = ?", (name,)).fetchone()
    return row is not None


def start(api) -> None:
    """What `coscc.py`'s startup task does."""
    create_tables(ctx_of(api.state.service), api.state.tables)


class ATableIsCreatedAtStartup(Setup):
    def fake(self, *tables: str) -> Plugin:
        return Plugin("fake", lambda _ctx: [], tables=tables)

    def test_build_opens_no_database_and_startup_makes_the_table_twice_harmlessly(self):
        fake = self.fake("CREATE TABLE IF NOT EXISTS fake_things (id INTEGER PRIMARY KEY)")
        db = str((Path(self.config.data_dir) / "cos.db").resolve())
        with mock.patch("coscc.features.FEATURES", (fake,)):
            with mock.patch.dict(os.environ, {PROTECTED_DB_VAR: db}):
                api = build(self.config)
            start(api)
            start(api)
        self.assertTrue(table_exists(self.config, "fake_things"))

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


def _server(_facts):
    return {"type": "stdio", "command": "true"}


def fake_feature(
    name="fake", server="fake", stages=("impl",), guard="g", block="b", route=True
) -> Plugin:
    async def ping(_request):
        return PlainTextResponse("pong")

    return Plugin(
        name,
        lambda _ctx: [Route(f"/api/{name}/ping", ping)] if route else [],
        scripts=(f"window.__{name} = 1;",),
        tables=(f"CREATE TABLE IF NOT EXISTS {name}_things (id INTEGER PRIMARY KEY)",),
        agent=lambda _ctx: Parts(
            tools=(Tool(server, ("ping",), stages, _server),),
            guards=(Guard(guard, lambda _f: None),),
            blocks=(Block(block, lambda _f: "hello"),),
        ),
    )


class AFeatureHandsTheAgentItsParts(Setup):
    async def test_route_table_and_parts_are_there_and_the_switch_empties_them(self):
        fake = fake_feature()
        with mock.patch("coscc.features.FEATURES", (fake,)):
            async with self.client() as client:
                self.assertEqual((await client.get("/api/fake/ping")).text, "pong")
                api = client._transport.app
                hooks = api.state.service.steps.hooks
                held = hooks.for_step("impl", str(self.ws))
                self.assertEqual([t.server for t in held.tools], ["fake"])
                self.assertEqual([g.name for g in held.guards], ["g"])
                self.assertEqual([b.name for b in held.blocks], ["b"])
                self.assertEqual(hooks.for_step("plan", str(self.ws)).tools, ())
                off = await client.post(
                    "/api/features", json={"cwd": str(self.ws), "name": "fake", "on": False}
                )
                self.assertEqual(off.status_code, 200)
                self.assertEqual(hooks.for_step("impl", str(self.ws)), Parts())
                start(api)
        self.assertTrue(table_exists(self.config, "fake_things"))

    async def test_with_no_features_none_of_it_remains(self):
        with mock.patch("coscc.features.FEATURES", ()):
            async with self.client() as client:
                self.assertEqual((await client.get("/api/fake/ping")).status_code, 404)
                hooks = client._transport.app.state.service.steps.hooks
            shell = json.dumps(screens.index().render(), ensure_ascii=False, default=str)
        self.assertEqual(hooks.for_step("impl", str(self.ws)), Parts())
        self.assertNotIn("__fake", shell)

    def test_a_clash_or_a_tool_on_a_prose_stage_is_refused_naming_the_feature(self):
        ctx = Ctx(lambda: None, str, lambda _f, _w: True, None, None)
        with self.assertRaisesRegex(ValueError, "b: the MCP server 'fake' is also a's"):
            hooks_of([fake_feature("a"), fake_feature("b", guard="g2", block="b2")], ctx)
        with self.assertRaisesRegex(ValueError, "b: the guard 'g' is also a's"):
            hooks_of([fake_feature("a"), fake_feature("b", server="other", block="b2")], ctx)
        with self.assertRaisesRegex(ValueError, "b: the block 'b' is also a's"):
            hooks_of([fake_feature("a"), fake_feature("b", server="other", guard="g2")], ctx)
        with self.assertRaisesRegex(ValueError, "fake: .*prose stages \\(plan\\)"):
            hooks_of([fake_feature(stages=("impl", "plan"))], ctx)


class AFeatureWithAgentPartsSaysWhatTheAgentSees(unittest.TestCase):
    def test_its_doc_has_the_section(self):
        folder = Path(features.__file__).parent
        for f in features.FEATURES:
            if f.agent is None:
                continue
            doc = folder / f"{f.name}.md"
            self.assertIn(
                "## What the agent sees",
                doc.read_text() if doc.exists() else "",
                f"add `## What the agent sees` to coscc/features/{f.name}.md: the tools, "
                "guards and prompt blocks the agent meets",
            )


if __name__ == "__main__":
    unittest.main()
