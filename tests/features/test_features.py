"""`coscc/features/__init__.py`, `coscc/kernel.py` and `coscc/http/plugin.py`: the list is the only place a feature is named."""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from coscc.store.db import Data
from coscc import features
from coscc.kernel import Block, Feature, Guard, Parts, Schedule, Tool
from coscc.http.plugin import (
    SCHEDULE_PREF,
    create_tables,
    hooks_of,
    set_schedule_of,
    set_state,
    shown,
    tick,
)
from coscc.http.app import build
from tests.features.ctx import ctx_for
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
    async def test_with_no_features_there_is_no_route_and_the_rest_answers(self):
        with mock.patch("coscc.features.FEATURES", ()):
            async with self.client() as client:
                self.assertEqual((await client.get("/api/notices/follow")).status_code, 404)
                self.assertEqual((await client.get("/api/health")).status_code, 200)
                board = await client.get("/api/units", params={"cwd": str(self.ws)})
                self.assertEqual(board.status_code, 200)

    async def test_with_the_list_as_shipped_the_route_is_there(self):
        # The route is a stream that ends at its lifetime; the test needs only its first answer.
        with mock.patch("coscc.features.notices.LIFETIME_SECONDS", 0.1):
            async with self.client() as client:
                self.assertEqual((await client.get("/api/notices/follow")).status_code, 200)


def table_exists(config: Config, name: str) -> bool:
    with Data(config.data_dir).connect() as conn:
        row = conn.execute("SELECT name FROM sqlite_master WHERE name = ?", (name,)).fetchone()
    return row is not None


def start(api) -> None:
    """What `coscc.py`'s startup task does."""
    create_tables(Data(api.state.config.data_dir), api.state.tables)


class ATableIsCreatedAtStartup(Setup):
    def fake(self, *tables: str) -> Feature:
        return Feature("fake", lambda _ctx: [], tables=tables)

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
            self.assertEqual((got.status_code, got.json()["notices"]), (200, "on"))
            off = await client.post(
                "/api/features", json={"cwd": str(self.ws), "name": "notices", "state": "off"}
            )
            self.assertEqual(
                (off.status_code, off.json()), (200, {"name": "notices", "state": "off"})
            )
            got = await client.get("/api/features", params={"cwd": str(self.ws)})
            self.assertEqual(got.json()["notices"], "off")
            got = await client.get("/api/features", params={"cwd": str(other)})
            self.assertEqual(got.json()["notices"], "on")
            await client.post(
                "/api/features", json={"cwd": str(self.ws), "name": "notices", "state": "on"}
            )
            got = await client.get("/api/features", params={"cwd": str(self.ws)})
            self.assertEqual(got.json()["notices"], "on")

    async def test_a_wrong_request_is_a_400_and_writes_nothing(self):
        async with self.client() as client:
            good = {"cwd": str(self.ws), "name": "notices", "state": "off"}
            for body in (
                {**good, "name": "nope"},
                {**good, "cwd": "/etc"},
                {**good, "state": "no"},
                {k: v for k, v in good.items() if k != "state"},
            ):
                r = await client.post("/api/features", json=body)
                self.assertEqual(r.status_code, 400, body)
                self.assertIn("error", r.json())
            self.assertEqual((await client.post("/api/features", json=[1])).status_code, 400)
            self.assertEqual(
                (await client.get("/api/features", params={"cwd": "/etc"})).status_code, 400
            )
            got = await client.get("/api/features", params={"cwd": str(self.ws)})
            self.assertEqual(got.json()["notices"], "on")

    async def test_a_feature_with_a_default_of_off_starts_off_and_the_rest_on(self):
        fake = Feature("graph", lambda _ctx: [], default="off", pilot=True)
        with mock.patch("coscc.features.FEATURES", (*features.FEATURES, fake)):
            async with self.client() as client:
                got = (await client.get("/api/features", params={"cwd": str(self.ws)})).json()
        self.assertEqual(got["graph"], "off")
        self.assertEqual({got[f.name] for f in features.FEATURES if f.default == "on"}, {"on"})
        self.assertEqual(got["codegraph"], "off")

    async def test_with_detail_each_feature_says_what_it_does_in_one_short_sentence(self):
        async with self.client() as client:
            got = await client.get("/api/features", params={"cwd": str(self.ws), "detail": "1"})
        rows = got.json()
        self.assertEqual(set(rows), {f.name for f in features.FEATURES})
        self.assertEqual(len(rows), 7)
        for name, row in rows.items():
            with self.subTest(feature=name):
                self.assertTrue(row["summary"])
                self.assertLessEqual(len(row["summary"]), 100)
                self.assertEqual(
                    set(row),
                    {"state", "pilot", "sentence", "locked", "schedule", "hours", "summary"},
                )
        self.assertEqual(rows["notices"]["state"], "on")

    async def test_the_routes_are_behind_the_login(self):
        from coscc.http import auth

        paths = {r.path for r in build(self.config).routes}
        self.assertIn("/api/features", paths)
        self.assertNotIn("/api/features", {path for _, path in auth.EXEMPT})


def _server(_facts):
    return {"type": "stdio", "command": "true"}


def fake_feature(
    name="fake", server="fake", stages=("impl",), guard="g", block="b", route=True
) -> Feature:
    async def ping(_request):
        return PlainTextResponse("pong")

    return Feature(
        name,
        lambda _ctx: [Route(f"/api/{name}/ping", ping)] if route else [],
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
                hooks = api.state.core.steps.hooks
                held = hooks.for_step("impl", str(self.ws))
                self.assertEqual([t.server for t in held.tools], ["fake"])
                self.assertEqual([g.name for g in held.guards], ["g"])
                self.assertEqual([b.name for b in held.blocks], ["b"])
                self.assertEqual(hooks.for_step("plan", str(self.ws)).tools, ())
                off = await client.post(
                    "/api/features", json={"cwd": str(self.ws), "name": "fake", "state": "off"}
                )
                self.assertEqual(off.status_code, 200)
                self.assertEqual(hooks.for_step("impl", str(self.ws)), Parts())
                start(api)
        self.assertTrue(table_exists(self.config, "fake_things"))

    async def test_with_no_features_none_of_it_remains(self):
        with mock.patch("coscc.features.FEATURES", ()):
            async with self.client() as client:
                self.assertEqual((await client.get("/api/fake/ping")).status_code, 404)
                hooks = client._transport.app.state.core.steps.hooks
        self.assertEqual(hooks.for_step("impl", str(self.ws)), Parts())

    def test_a_clash_or_a_tool_on_a_prose_stage_is_refused_naming_the_feature(self):
        ctx = {n: ctx_for() for n in ("a", "b", "fake")}
        with self.assertRaisesRegex(ValueError, "b: the MCP server 'fake' is also a's"):
            hooks_of([fake_feature("a"), fake_feature("b", guard="g2", block="b2")], ctx)
        with self.assertRaisesRegex(ValueError, "b: the guard 'g' is also a's"):
            hooks_of([fake_feature("a"), fake_feature("b", server="other", block="b2")], ctx)
        with self.assertRaisesRegex(ValueError, "b: the block 'b' is also a's"):
            hooks_of([fake_feature("a"), fake_feature("b", server="other", guard="g2")], ctx)
        with self.assertRaisesRegex(ValueError, "fake: .*prose stages \\(plan\\)"):
            hooks_of([fake_feature(stages=("impl", "plan"))], ctx)


class AScheduledFeatureRunsOnItsOwn(Setup):
    """The pref `features.schedule`, its door, and the core's tick."""

    def scheduled(self, ticked: list) -> Feature:
        async def tick(_ctx, cwd: str, hours: int) -> None:
            ticked.append((cwd, hours))

        return Feature("timed", lambda _ctx: [], schedule=Schedule((0, 12, 24), 24, tick))

    async def test_the_door_writes_the_pref_and_settings_reads_it_back(self):
        timed = self.scheduled([])
        with mock.patch("coscc.features.FEATURES", (*features.FEATURES, timed)):
            async with self.client() as client:
                api = client._transport.app
                listed = {f.name: f for f in shown(api.state.ctxs, api.state.plugins, str(self.ws))}
                self.assertEqual(
                    (listed["timed"].schedule, listed["timed"].hours), (24, (0, 12, 24))
                )
                self.assertIsNone(listed["notices"].schedule)
                good = {"cwd": str(self.ws), "name": "timed", "schedule": 12}
                r = await client.post("/api/features", json=good)
                self.assertEqual(
                    (r.status_code, r.json()), (200, {"name": "timed", "schedule": 12})
                )
                self.assertEqual(api.state.ctxs["timed"].settings.schedule(str(self.ws)), 12)
                stored = Data(self.config.data_dir).pref(SCHEDULE_PREF, {})
                self.assertEqual(stored, {"timed": {str(self.ws.resolve()): 12}})
                for bad in (
                    {**good, "schedule": 6},
                    {**good, "schedule": True},
                    {**good, "name": "notices"},
                    {**good, "cwd": "/etc"},
                ):
                    r = await client.post("/api/features", json=bad)
                    self.assertEqual(r.status_code, 400, bad)
                self.assertEqual(api.state.ctxs["timed"].settings.schedule(str(self.ws)), 12)

    async def test_a_tick_asks_only_where_it_is_on_and_scheduled(self):
        ticked: list = []
        timed = self.scheduled(ticked)
        with mock.patch("coscc.features.FEATURES", (timed,)):
            api = build(self.config)
        ctx, core = api.state.ctxs, api.state.core
        await tick(core, ctx, (timed,))
        self.assertEqual(ticked, [(str(self.ws), 24)])
        set_schedule_of(core, (timed,), "timed", str(self.ws), 0)
        await tick(core, ctx, (timed,))
        self.assertEqual(len(ticked), 1)
        set_schedule_of(core, (timed,), "timed", str(self.ws), 12)
        set_state(core, ctx, (timed,), "timed", str(self.ws), "off")
        await tick(core, ctx, (timed,))
        self.assertEqual(len(ticked), 1)


if __name__ == "__main__":
    unittest.main()


class OnlyReleaseWritesGit(unittest.TestCase):
    """The kernel's git writers commit, push and tag. A feature's agent tools run in the app,
    past the command policy, so one that writes git must give an agent none, and widening who
    writes git is a change to this test, seen in review."""

    WRITERS = (
        "OwnTree",
        "commit_files",
        "push_branch",
        "push_tag",
        "detach_here",
        "own_tree_remove",
    )

    def test_release_alone_uses_the_writers_and_hands_an_agent_nothing(self):
        root = Path(features.__file__).parent
        writing = sorted(
            folder.name
            for folder in root.iterdir()
            if folder.is_dir()
            and any(
                re.search(rf"\b{name}\b", path.read_text(encoding="utf-8"))
                for path in folder.glob("*.py")
                for name in self.WRITERS
            )
        )
        self.assertEqual(writing, ["release"])
        for feature in features.FEATURES:
            if feature.name in writing:
                self.assertIsNone(feature.agent, feature.name)
                self.assertEqual(feature.sessions, (), feature.name)
