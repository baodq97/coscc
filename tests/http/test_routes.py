"""Tests for the HTTP surface.

None of these create a session — the guards are exactly the paths that must refuse
*before* anything is spawned, so testing them costs nothing. What needs a
real session is `scripts/verify_0001.py`, which is run on purpose."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import claude_agent_sdk as sdk
import httpx

from coscc.kernel import Feature
from coscc.http.app import build
from coscc.config import Config
from coscc.runner import triggers
from coscc.runner.run import LIVE
from tests.http.test_app import seed_unit, use_sessions


def _tmp_config(test: unittest.TestCase) -> Config:
    """`/tmp` as the one workspace, and a data root of the test's own.

    These fixtures used to leave `data_dir` unset, which is `~/.cos`, and `/api/send` opened the
    real database on every `npm test`. Nothing caught it until a step's environment named that
    database as one not to open."""
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    return Config(workspaces=("/tmp",), data_dir=tmp.name)


def _info(session_id="s1", cwd="/tmp"):
    return sdk.SDKSessionInfo(
        session_id=session_id,
        summary="s",
        last_modified=1,
        file_size=1,
        custom_title=None,
        first_prompt=None,
        git_branch=None,
        cwd=cwd,
        tag=None,
        created_at=1,
    )


class Surface(unittest.IsolatedAsyncioTestCase):
    """Driven over ASGI, the same way `scripts/verify_0001.py` drives it.

    No socket and no lifespan: the transport speaks to the app object directly, which is
    also the object Reflex mounts. A test that needed a port would be testing something
    the proof does not exercise.
    """

    async def asyncSetUp(self):
        self.app = build(_tmp_config(self))
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_no_route_leaks_configuration(self):
        # A long-lived credential is in this process. The knobs and the environment must not be
        # readable over the port.
        body = (await self.client.get("/api/workspaces")).text
        for leak in ("TOKEN", "bypass", "permission_mode", "tools"):
            self.assertNotIn(leak, body)

    async def test_a_foreign_session_is_marked_read_only_not_hidden(self):
        # Terminal sessions are visible because the read layer sees them, but the page has to be
        # able to tell which ones it may write to.
        with mock.patch.object(sdk, "list_sessions", return_value=[_info()]):
            body = self.app.state.core.chat.sessions_for("/tmp")
        self.assertEqual(len(body["sessions"]), 1)
        self.assertFalse(body["sessions"][0]["resumable"])

    async def test_the_app_serves_the_studio_at_every_other_path_and_no_unknown_route(self):
        """The studio answers last: a page path gets it (503 when unbuilt), an `/api/` one never."""
        for path in ("/", "/unit/w/1"):
            got = await self.client.get(path)
            self.assertIn(got.status_code, (200, 503), path)
            self.assertTrue(got.headers["content-type"].startswith("text/html"), path)
        self.assertEqual((await self.client.get("/api/no-such")).status_code, 404)

    async def test_there_is_no_route_to_widen_what_impl_runs(self):
        self.assertEqual((await self.client.get("/api/grants/impl")).status_code, 404)
        got = await self.client.post("/api/grants/impl", json={"cwd": "/tmp", "allow": ["ssh"]})
        never = await self.client.post("/api/no-such", json={})
        self.assertEqual(got.status_code, never.status_code)
        self.assertGreaterEqual(got.status_code, 400)


class WorkspaceRoutes(unittest.IsolatedAsyncioTestCase):
    """The write surface. None of these clone -- the failures they test happen first."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.app = build(Config(workspaces=(), working_dir=str(self.root), data_dir=str(self.root)))
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_adopting_a_directory_then_listing_it(self):
        (self.root / "repo").mkdir()
        r = await self.client.post("/api/workspaces", json={"name": "repo", "label": "Mine"})
        self.assertEqual(r.status_code, 200)
        body = (await self.client.get("/api/workspaces")).json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["workspaces"][0]["label"], "Mine")
        self.assertEqual(body["workspaces"][0]["source"], "store")

    async def test_a_name_that_would_escape_the_root_is_refused(self):
        for bad in ("../escape", "/etc", "a/b", "", "."):
            r = await self.client.post("/api/workspaces", json={"name": bad})
            self.assertEqual(r.status_code, 400, bad)

    async def test_the_working_folder_cannot_be_set_over_http(self):
        """The body naming one is ignored, not honoured."""
        (self.root / "repo").mkdir()
        await self.client.post("/api/workspaces", json={"name": "repo", "working_dir": "/etc"})
        body = (await self.client.get("/api/workspaces")).json()
        self.assertEqual(body["working_dir"], str(self.root))
        self.assertTrue(body["workspaces"][0]["path"].startswith(str(self.root)))

    async def test_adopting_something_that_is_not_there_is_refused(self):
        r = await self.client.post("/api/workspaces", json={"name": "ghost"})
        self.assertEqual(r.status_code, 400)

    async def test_adding_the_same_name_twice_is_refused(self):
        (self.root / "repo").mkdir()
        await self.client.post("/api/workspaces", json={"name": "repo"})
        r = await self.client.post("/api/workspaces", json={"name": "repo"})
        self.assertEqual(r.status_code, 400)

    async def test_remove_delists_but_leaves_the_directory(self):
        (self.root / "repo").mkdir()
        await self.client.post("/api/workspaces", json={"name": "repo"})
        r = await self.client.post("/api/workspaces/repo/remove")
        self.assertEqual(r.json(), {"removed": "repo", "count": 0})
        self.assertEqual((await self.client.get("/api/workspaces")).json()["count"], 0)
        self.assertTrue((self.root / "repo").is_dir())

    async def test_a_clone_with_a_bad_url_leaves_no_trace(self):
        """No listed workspace, and no leftover directory."""
        r = await self.client.post(
            "/api/workspaces", json={"name": "repo", "repo_url": "git@github.com:x/y.git"}
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual((await self.client.get("/api/workspaces")).json()["count"], 0)
        self.assertFalse((self.root / "repo").exists())

    async def test_pull_on_something_not_listed_is_refused(self):
        r = await self.client.post("/api/workspaces/ghost/pull")
        self.assertEqual(r.status_code, 400)

    async def test_label_and_remove_on_something_not_listed_are_refused(self):
        for path in ("/api/workspaces/ghost/label", "/api/workspaces/ghost/remove"):
            self.assertEqual((await self.client.post(path, json={})).status_code, 400, path)


class AgentsOverHttp(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.app = build(Config(workspaces=(), working_dir=str(self.root), data_dir=str(self.root)))
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def page(self):
        r = await self.client.get("/api/agents")
        self.assertEqual(r.status_code, 200)
        return r.json()

    async def test_set_then_reset(self):
        body = {"key": "spec", "field": "ceilings", "value": {"turns": 30, "usd": 4.0}}
        r = await self.client.post("/api/agents/field", json=body)
        self.assertEqual(r.status_code, 200)
        spec = next(x for x in r.json()["rows"] if x["key"] == "spec")
        self.assertEqual(spec["config"]["ceilings"]["max_turns"], 30)
        self.assertEqual(spec["edited"], ["ceilings"])
        r = await self.client.post("/api/agents/field", json={"key": "spec", "field": "ceilings"})
        self.assertEqual(r.status_code, 200)
        spec = next(x for x in r.json()["rows"] if x["key"] == "spec")
        self.assertEqual(spec["config"]["ceilings"]["max_turns_source"], "default")
        self.assertEqual(spec["edited"], [])

    async def test_the_page_carries_the_catalog_and_every_part(self):
        page = await self.page()
        catalog = {t["name"]: t for t in page["catalog"]}
        self.assertEqual((catalog["Bash"]["effect"], catalog["Bash"]["tier"]), ("external", "high"))
        self.assertEqual(catalog["vault"]["feature"], "vault")
        self.assertNotIn("submit", catalog)
        impl = next(x for x in page["rows"] if x["key"] == "impl")
        self.assertEqual(impl["group"], "stage")
        self.assertEqual(impl["row"]["tools"]["vault"], "allow")
        self.assertTrue(impl["row"]["body"])
        self.assertEqual([s["name"] for s in impl["skills"]], ["write-impl"])
        self.assertEqual(len(impl["row_hash"]), 12)
        groups = {x["key"]: x["group"] for x in page["rows"]}
        self.assertEqual((groups["leif"], groups["scout"]), ("engine", "helper"))

    async def test_bad_requests_are_400_with_a_reason(self):
        for body in (
            {"key": "bogus", "field": "model", "value": {"id": "m"}},
            {"key": "plan", "field": "model", "value": {"id": "  "}},
            {"key": "plan", "field": "model", "value": {"id": "m", "effort": "turbo"}},
            {"key": "chat", "field": "model", "value": {"id": "m"}},
            {"key": "plan", "field": "ceilings", "value": {"turns": 501}},
            {"key": "plan", "field": "ceilings", "value": {"usd": 0.05}},
            {"key": "review", "field": "name", "value": "two words"},
            {"key": "plan", "field": "tools", "value": {"Nope": "allow"}},
            {"key": "plan", "field": "tools", "value": {"submit": "allow"}},
            {"key": "intent", "field": "tools", "value": {"Bash": "allow"}},
            {"key": "plan", "field": "trigger", "value": {"state": "spec"}},
            {"key": "plan", "field": "skill:write-intent", "value": "x"},
            {"field": "model", "value": "m"},
            [1, 2],
        ):
            r = await self.client.post("/api/agents/field", json=body)
            self.assertEqual(r.status_code, 400, body)
            self.assertTrue(r.json()["error"], body)
        r = await self.client.post("/api/agents/field", content=b"not json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual([x["edited"] for x in (await self.page())["rows"] if x["edited"]], [])

    async def test_one_route_writes_a_row_and_none_writes_a_grant(self):
        # A row's tools are the owner's to set; the grant is still the engine's, per run.
        for field in ("commands", "mcp", "submits", "grant", "row"):
            r = await self.client.post(
                "/api/agents/field", json={"key": "impl", "field": field, "value": ["Bash"]}
            )
            self.assertEqual(r.status_code, 400, field)
        writes = [
            route.path
            for route in self.app.routes
            if "POST" in getattr(route, "methods", ()) and "agents" in route.path
        ]
        # The owner's on/off and *Run now*, beside the row; neither writes a grant or a row.
        self.assertEqual(writes, ["/api/agents/field", "/api/agents/state", "/api/agents/run"])


class TriggersAndProposalsOverHttp(unittest.IsolatedAsyncioTestCase):
    """`/api/agents/state`, `/api/agents/run` and `/api/proposals`: the owner's presses."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.ws = self.root / "ws"
        self.ws.mkdir()
        self.app = build(
            Config(workspaces=(str(self.ws),), working_dir=str(self.root), data_dir=str(self.root))
        )
        self.core = self.app.state.core
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )
        self.addAsyncCleanup(self.client.aclose)

    async def test_on_or_off_is_the_owners_setting_and_is_logged(self):
        body = {"cwd": str(self.ws), "key": "scan", "on": True}
        r = await self.client.post("/api/agents/state", json=body)
        self.assertEqual(r.status_code, 200)
        scan = next(x for x in r.json()["rows"] if x["key"] == "scan")
        self.assertEqual((scan["group"], scan["on"]), ("triggered", True))
        (said,) = self.core.ws.journal().records(kinds=("agent-state",))
        self.assertEqual((said["agent"], said["on"], said["by"]), ("scan", True, "owner"))
        for bad in ({**body, "key": "estimate"}, {**body, "on": "yes"}, {**body, "cwd": "/etc"}):
            r = await self.client.post("/api/agents/state", json=bad)
            self.assertEqual(r.status_code, 400, bad)

    async def test_run_now_starts_a_manual_run_and_refuses_a_row_it_does_not_start(self):
        with mock.patch("coscc.http.routes.triggers.start") as start:
            r = await self.client.post("/api/agents/run", json={"cwd": str(self.ws), "key": "scan"})
        self.assertEqual((r.status_code, r.json()), (200, {"agent": "scan", "started": True}))
        self.assertEqual(start.call_args.kwargs["by"], "manual")
        r = await self.client.post("/api/agents/run", json={"cwd": str(self.ws), "key": "impl"})
        self.assertEqual((r.status_code, r.json()["code"]), (400, "not-triggered"))

    async def test_run_now_on_a_unit_the_workspace_does_not_hold_starts_nothing(self):
        body = {"cwd": str(self.ws), "key": "outcome", "unit": "0047_nonexistent"}
        r = await self.client.post("/api/agents/run", json=body)
        self.assertEqual((r.status_code, r.json()["code"]), (400, "no-unit"))
        self.assertEqual(triggers._TASKS, set())

    async def test_proposals_name_their_agent_and_the_owner_decides(self):
        from coscc.store.db import Data
        from coscc.units import proposals

        item = {
            "type": "fix",
            "slug": "steps-stop",
            "title": "Steps stop",
            "problem": "p" * 250,
            "sources": ["x"],
        }
        (pid,) = proposals.add(Data(self.root), str(self.ws.resolve()), "scan", "", [item])
        r = await self.client.get("/api/proposals", params={"cwd": str(self.ws)})
        self.assertEqual(r.status_code, 200)
        got = r.json()
        self.assertEqual(got["proposals"][0]["agent_name"], "Sowilo")
        self.assertEqual(got["agents"], [{"key": "scan", "name": "Sowilo", "on": False}])
        r = await self.client.post(
            f"/api/proposals/{pid}", json={"cwd": str(self.ws), "action": "accept", "slug": "No"}
        )
        self.assertEqual(r.status_code, 400)
        r = await self.client.post(
            f"/api/proposals/{pid}",
            json={"cwd": str(self.ws), "action": "dismiss", "reason": "done in 0150"},
        )
        self.assertEqual(
            (r.status_code, r.json()["state"], r.json()["by"]), (200, "dismissed", "owner")
        )


class WithoutAWorkingFolder(unittest.IsolatedAsyncioTestCase):
    """Without a store: the write routes say why rather than crashing."""

    async def asyncSetUp(self):
        self.app = build(_tmp_config(self))
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_listing_still_works(self):
        self.assertEqual((await self.client.get("/api/workspaces")).json()["count"], 1)

    async def test_writing_explains_the_missing_setting(self):
        r = await self.client.post("/api/workspaces", json={"name": "repo"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("COS_WORKING_DIR", r.json()["error"])


class ShutdownClosesSessions(unittest.IsolatedAsyncioTestCase):
    """The one place the CLI processes are closed, and until now the one nothing checked.

    `scripts/verify_0001.py:153` has to close them by hand because `httpx.ASGITransport`
    never sends lifespan events, so every other test in this file walks past this path. The
    lifespan context is driven directly here instead: no socket, no port, no session and no
    quota, which is what lets the check live in `npm test` rather than in a command nobody
    runs. What it does not prove is that uvicorn runs *this* app's lifespan in production —
    that holds because Reflex mounts its app inside the FastAPI one (`reflex/app.py:815`,
    measured 2026-09-22), and nothing here would notice if a later release reversed it.
    """

    def setUp(self):
        # Built outside the coroutine: IsolatedAsyncioTestCase runs the event loop in debug
        # mode, and constructing the app inside the task took long enough to trip asyncio's
        # slow-callback line. That is noise this unit exists to remove.
        self.app = build(_tmp_config(self))
        self.app.state.sessions.close_all = mock.AsyncMock()

    async def test_leaving_the_lifespan_closes_every_session(self):
        app = self.app
        async with app.router.lifespan_context(app):
            # Startup must not close anything; the app is meant to be serving here.
            app.state.sessions.close_all.assert_not_awaited()
        app.state.sessions.close_all.assert_awaited_once()

    async def test_running_steps_are_cancelled_before_the_sessions_close(self):
        # A step's task goes first, so it is not left writing after its client was closed under it.
        app = self.app
        order = []
        app.state.core.shutdown = mock.AsyncMock(side_effect=lambda: order.append("steps"))
        app.state.sessions.close_all.side_effect = lambda: order.append("sessions")
        async with app.router.lifespan_context(app):
            app.state.core.shutdown.assert_not_awaited()
        app.state.core.shutdown.assert_awaited_once()
        self.assertEqual(order, ["steps", "sessions"])


class StoppingAStepOverHttp(unittest.IsolatedAsyncioTestCase):
    """The route decides nothing; it translates `Steps.stop_step`."""

    async def asyncSetUp(self):
        self.app = build(_tmp_config(self))
        self.core = self.app.state.core
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_a_stop_on_nothing_running_is_a_400(self):
        r = await self.client.post(
            "/api/board/stop", json={"cwd": "/tmp", "unit": "0001_a", "by": "Lan"}
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("no step running", r.json()["error"])

    async def test_a_stop_without_a_name_is_a_400(self):
        r = await self.client.post(
            "/api/board/stop", json={"cwd": "/tmp", "unit": "0001_a", "by": ""}
        )
        self.assertEqual(r.status_code, 400)

    async def test_a_stop_of_a_running_step_is_a_200_naming_it(self):
        from coscc.agent.steps import Running

        key = self.core.ws.key("/tmp")
        row = self.core.attempts.open("step", key, "0001_a", "spec", state="running")
        # The live part of the attempt: its session handle and task, by attempt id.
        running = Running(workspace=key, unit="0001_a", stage="spec", started_at=row["since"])
        running.attempt = row["id"]
        self.core.steps.tasks[row["id"]] = running
        r = await self.client.post(
            "/api/board/stop", json={"cwd": "/tmp", "unit": "0001_a", "by": "Lan"}
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"unit": "0001_a", "stage": "spec", "stopped_by": "Lan"})
        self.assertTrue(running.stop_requested)
        # the Stop is recorded on the attempt, with the name that asked.
        self.assertEqual(self.core.attempts.get(row["id"])["stop_asked_by"], "Lan")

    async def test_an_integration_is_on_the_running_list_until_it_ends(self):
        key = self.core.ws.key("/tmp")
        held = self.core.attempts.open("integration", key, "0001_a", "integrate", state="running")
        self.core.attempts.set_road(held["id"], "gebo")
        [row] = (await self.client.get("/api/board/steps", params={"cwd": "/tmp"})).json()
        self.assertEqual(
            (row["unit"], row["stage"], row["stopping"], row["run"], row["kind"]),
            ("0001_a", "integrate", False, None, "integration"),
        )
        self.assertEqual(row["started_at"], held["since"])
        self.core.attempts.move(held["id"], "ended", "done")
        self.assertEqual(
            (await self.client.get("/api/board/steps", params={"cwd": "/tmp"})).json(), []
        )

    async def test_the_running_list_refuses_a_directory_that_is_not_a_workspace(self):
        r = await self.client.get("/api/board/steps", params={"cwd": "/etc"})
        self.assertEqual(r.status_code, 400)


class WatchingARunOverHttp(unittest.IsolatedAsyncioTestCase):
    """The routes translate `Watch.events_page` and `.follow_events`; the reads are tested there."""

    async def asyncSetUp(self):
        from coscc.runlog.events import Recorder

        self.app = build(_tmp_config(self))
        self.core = self.app.state.core
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )
        key = self.core.ws.key("/tmp")
        self.recorder = Recorder("r1", None, "/tmp", key, "0001_a", "impl")
        for i in range(3):
            self.recorder.denied("Bash", {"command": f"c{i}"}, "not granted")
        LIVE["r1"] = self.recorder

    async def asyncTearDown(self):
        LIVE.pop("r1", None)
        await self.client.aclose()

    async def test_a_page_is_the_last_events_oldest_first(self):
        r = await self.client.get("/api/runs/r1", params={"cwd": "/tmp", "limit": "2"})
        self.assertEqual(r.status_code, 200)
        page = r.json()
        self.assertEqual(
            (page["status"], page["unit"], page["stage"], page["has_older"]),
            ("running", "0001_a", "impl", True),
        )
        self.assertEqual([e["seq"] for e in page["events"]], [2, 3])
        self.assertEqual(page["events"][0]["input"], {"command": "c1"})
        self.assertEqual(page["events"][0]["reason"], "not granted")

    async def test_a_run_of_another_workspace_or_a_bad_number_is_a_400(self):
        self.recorder.workspace = "elsewhere"
        other = await self.client.get("/api/runs/r1", params={"cwd": "/tmp"})
        self.assertEqual(other.status_code, 400)
        self.recorder.workspace = self.core.ws.key("/tmp")
        bad = await self.client.get("/api/runs/r1", params={"cwd": "/tmp", "before": "x"})
        self.assertEqual(bad.status_code, 400)

    async def test_following_gives_what_is_past_after_then_says_it_is_over(self):
        self.recorder.closed = True
        r = await self.client.get("/api/runs/r1/follow", params={"cwd": "/tmp", "after": "1"})
        self.assertEqual(r.headers["content-type"].split(";")[0], "text/event-stream")
        blocks = r.text.split("\n\n")
        [batch] = [b for b in blocks if b.startswith("data: ")]
        self.assertEqual([e["seq"] for e in json.loads(batch[6:])], [2, 3])
        self.assertIn("event: status", r.text)
        self.assertTrue(r.text.rstrip().startswith("retry: 1000"))
        self.assertIn("event: done", blocks[-2])


class TalkingOverHttp(unittest.IsolatedAsyncioTestCase):
    """The chat routes translate `Chat`; a turn is stood in for, so none is paid."""

    async def asyncSetUp(self):
        self.app = build(_tmp_config(self))
        self.core = self.app.state.core
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_a_workspace_with_no_session_lists_none(self):
        with mock.patch("coscc.agent.sessions.sdk.list_sessions", return_value=[]):
            r = await self.client.get("/api/chat/sessions", params={"cwd": "/tmp"})
        self.assertEqual(r.json(), {"cwd": "/tmp", "sessions": []})

    async def test_a_turn_with_no_text_or_session_is_refused_before_anything_opens(self):
        self.assertEqual(
            (await self.client.post("/api/chat", json={"cwd": "/tmp", "text": " "})).status_code,
            400,
        )
        r = await self.client.get("/api/chat/history", params={"cwd": "/tmp"})
        self.assertEqual(r.status_code, 400)

    async def test_a_turn_streams_its_text_tools_and_session(self):
        async def turn(cwd, text, session_id=None):
            self.assertEqual((text, session_id), ("hi", "s0"))
            yield ("chunk", "he")
            yield ("tool", "Read")
            yield ("chunk", "llo")
            yield ("done", {"session_id": "s1"})

        with mock.patch.object(self.core.chat, "stream", turn):
            r = await self.client.post(
                "/api/chat", json={"cwd": "/tmp", "text": "hi", "session_id": "s0"}
            )
        lines = [json.loads(line) for line in r.text.splitlines()]
        self.assertEqual(
            lines,
            [
                {"type": "chunk", "text": "he"},
                {"type": "tool", "name": "Read"},
                {"type": "chunk", "text": "llo"},
                {"type": "done", "session_id": "s1"},
            ],
        )


if __name__ == "__main__":
    unittest.main()


QUESTIONS = (
    "# Intent: q\n"
    "Author: t. Type: feat.\n\n"
    "## Problem\n\nx\n\n"
    "## Open questions\n\n"
    "1. One?\n"
    "2. Two?\n"
    "3. Three?\n"
)


class AnsweringAQuestionOverHttp(unittest.IsolatedAsyncioTestCase):
    """The route appends one block or writes nothing at all."""

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        (root / "work" / "proj").mkdir(parents=True)
        self.cwd = str(root / "work" / "proj")
        self.data_dir = root / "data"
        self.app = build(
            Config(
                workspaces=(self.cwd,),
                working_dir=str(root / "work"),
                data_dir=str(self.data_dir),
            )
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )
        made = (
            await self.client.post(
                "/api/units", json={"cwd": self.cwd, "slug": "a-problem", "brief": "x"}
            )
        ).json()
        self.unit = made["unit"]
        self.dir = Path(made["path"])
        self.intent = self.dir / "intent.md"
        self.intent.write_text(QUESTIONS, encoding="utf-8")
        seed_unit(
            self.app.state.core,
            self.cwd,
            self.unit,
            statuses={"intent.md": "accepted"},
            type="feat",
        )
        seed_unit(
            self.app.state.core,
            self.cwd,
            self.unit,
            questions={"intent.md": ["One?", "Two?", "Three?"]},
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    def body(self, **over):
        return {
            "cwd": self.cwd,
            "unit": self.unit,
            "artifact": "intent.md",
            "question": 2,
            "answer": "Tách ra. MARK-0016",
            "by": "person",
            "name": "Phong",
            **over,
        }

    async def post(self, **over):
        return await self.client.post("/api/units/answer", json=self.body(**over))

    def rows(
        self, table: str = "unit_answers", columns: str = "artifact, ref, name, via, text"
    ) -> list[tuple]:
        """What the database holds for this unit, where the file's block once was."""
        from coscc.store.db import Data

        with Data(self.data_dir).connect() as conn:
            return [
                tuple(r)
                for r in conn.execute(
                    f"SELECT {columns} FROM {table} WHERE unit = ? AND once_key = '' ORDER BY id",
                    (self.unit,),
                )
            ]

    async def test_an_answer_writes_its_journal_row_in_the_same_transaction(self):
        import sqlite3

        from coscc.store.journal import Journal

        def answers() -> list:
            return Journal(str(Path(self.cwd).parent), self.data_dir).records(kind="answer")

        got = await self.post()
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual((len(self.rows()), len(answers())), (1, 1))
        # The run-log row fails after the answer's row was written: neither is kept.
        with mock.patch.object(
            Journal, "_insert", side_effect=sqlite3.OperationalError("disk I/O error")
        ):
            got = await self.post(question=1, answer="Có.")
        self.assertEqual(got.status_code, 400, got.text)
        self.assertIn("the answer was not recorded", got.text)
        self.assertNotIn("disk I/O error", got.text)
        self.assertEqual((len(self.rows()), len(answers())), (1, 1))

    async def test_a_locked_database_before_the_answer_is_one_sentence_without_its_path(self):
        """The snapshot the answer is checked against reads `cos.db`, and `Busy` names the
        database's path. The dialog gets one sentence; the log gets the path."""
        from coscc.store.db import Busy
        from coscc.units.meta import UnitMeta

        held = "another process is holding /tmp/somewhere/cos.db"
        before = self.intent.read_bytes()
        with (
            mock.patch.object(UnitMeta, "snapshot", side_effect=Busy(held)),
            self.assertLogs("coscc", "WARNING") as log,
        ):
            got = await self.post()
        self.assertEqual(got.status_code, 400, got.text)
        self.assertIn("could not be read", got.text)
        self.assertNotIn("/tmp/somewhere", got.text)
        self.assertIn(held, log.output[-1])
        self.assertEqual((self.rows(), self.intent.read_bytes()), ([], before))

    async def refused(self, **over):
        before = self.intent.read_bytes()
        got = await self.post(**over)
        self.assertEqual(got.status_code, 400, got.text)
        self.assertEqual(self.intent.read_bytes(), before)
        return got.json()["error"]

    async def test_a_unit_that_does_not_exist_is_refused(self):  # (a)
        self.assertIn("no such work unit", await self.refused(unit="0099_nothing"))

    async def test_an_artifact_without_questions_is_refused(self):  # (b)
        await self.refused(artifact="spec.md")
        await self.refused(artifact="idea.md")

    async def test_a_number_that_is_not_a_question_is_refused(self):  # (c)
        self.assertIn("no question 4", await self.refused(question=4))
        await self.refused(question="two")

    async def test_an_empty_answer_is_refused(self):  # (d)
        await self.refused(answer="   \n  ")

    async def test_an_answer_with_no_name_is_recorded_as_owner(self):
        await self.refused(name="A\nStatus: rejected")
        got = await self.post(name="  ")
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual((got.json()["by"], got.json()["name"]), ("person", "owner"))

    async def test_an_answer_without_by_is_refused_naming_both_values(self):
        body = self.body()
        del body["by"]
        before = self.intent.read_bytes()
        got = await self.client.post("/api/units/answer", json=body)
        self.assertEqual(got.status_code, 400, got.text)
        self.assertIn("person", got.json()["error"])
        self.assertIn("delegated", got.json()["error"])
        self.assertEqual((self.rows(), self.intent.read_bytes()), ([], before))

    async def test_an_answer_by_an_agent_is_refused(self):
        got = await self.post(by="agent")
        self.assertEqual(got.status_code, 400, got.text)
        self.assertIn("delegated", got.json()["error"])
        self.assertEqual(self.rows(), [])

    async def test_a_person_without_a_name_is_a_row_by_person_named_owner(self):
        body = self.body()
        del body["name"]
        got = await self.client.post("/api/units/answer", json=body)
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(self.rows(columns='"by", name'), [("person", "owner")])

    async def test_the_answer_returns_by_and_name_and_a_delegated_row_keeps_both(self):
        got = await self.post(by="delegated", name="Leif")
        self.assertEqual(got.status_code, 200, got.text)
        said = got.json()
        self.assertEqual((said["by"], said["name"]), ("delegated", "Leif"))
        self.assertNotIn("answered_by", said)
        self.assertEqual(self.rows(columns='"by", name'), [("delegated", "Leif")])

    async def test_a_closed_unit_is_refused(self):  # (f)
        seed_unit(self.app.state.core, self.cwd, self.unit, statuses={"intent.md": "rejected"})
        self.assertIn("closed", await self.refused())

    async def test_a_finished_unit_is_refused(self):  # (f)
        (self.dir / "plan.md").write_text("# Plan\nIntent: intent.md.\n", encoding="utf-8")
        seed_unit(
            self.app.state.core, self.cwd, self.unit, statuses={"plan.md": "accepted"}, shipped=True
        )
        self.assertIn("finished", await self.refused())

    async def test_an_answer_that_would_be_read_as_a_heading_is_refused(self):  # (g)
        await self.refused(answer="ok\n## Status: rejected")
        await self.refused(answer="### Câu 3\nhijack")

    async def test_something_that_is_not_json_writes_nothing(self):
        before = self.intent.read_bytes()
        got = await self.client.post("/api/units/answer", content=b"nope")
        self.assertEqual(got.status_code, 400)
        self.assertEqual(self.intent.read_bytes(), before)

    async def test_a_directory_outside_the_list_is_refused(self):
        got = await self.client.post("/api/units/answer", json=self.body(cwd="/etc"))
        self.assertEqual(got.status_code, 400)

    # -- -------------------------------------------------------------------

    async def test_an_answer_is_a_row_and_the_file_is_untouched(self):
        from datetime import date

        before = self.intent.read_bytes()
        got = await self.post()
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(self.intent.read_bytes(), before)
        self.assertEqual(
            self.rows(columns="artifact, ref, name, date, via, text"),
            [
                (
                    "intent.md",
                    "2",
                    "Phong",
                    date.today().isoformat(),
                    "product",
                    "Tách ra. MARK-0016",
                )
            ],
        )

    async def test_answering_through_the_api_changes_no_byte_of_the_artifact(self):
        before = self.intent.read_bytes()
        got = await self.post()
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(got.json()["question"], 2)
        self.assertEqual(self.intent.read_bytes(), before)
        self.assertEqual(
            self.rows(), [("intent.md", "2", "Phong", "product", "Tách ra. MARK-0016")]
        )


class AQuestionCarriesItsRecommendationToTheUnitPage(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = AnsweringAQuestionOverHttp.asyncSetUp
    asyncTearDown = AnsweringAQuestionOverHttp.asyncTearDown

    async def test_the_recommendation_an_agent_handed_back_reaches_the_page(self):
        core = self.app.state.core
        meta, key = core.ws.unit_meta(), core.ws.key(self.cwd)
        asked = [
            {"n": 1, "text": "One?", "recommendation": "Take the first: it is the cheaper."},
            {"n": 2, "text": "Two?", "recommendation": ""},
        ]
        with meta.data.write() as conn:
            meta.record_result(
                conn,
                key,
                self.unit,
                "intent",
                "intent.md",
                {
                    "run": "r",
                    "revision": "h",
                    "object": {"judgement": "not-ready", "type": "feat", "questions": asked},
                },
            )
        got = await self.client.get(f"/api/units/{self.unit}", params={"cwd": self.cwd})
        self.assertEqual(got.status_code, 200, got.text)
        questions = got.json()["questions"]
        self.assertEqual(
            [(q["n"], q["recommendation"]) for q in questions],
            [(1, "Take the first: it is the cheaper."), (2, "")],
        )


class RecordingAnOutcomeOverHttp(unittest.IsolatedAsyncioTestCase):
    """The route writes one `outcome` row or nothing at all; what it refuses is
    `Answers.record_outcome`'s decision, tested there."""

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        (root / "work" / "proj").mkdir(parents=True)
        self.cwd = str(root / "work" / "proj")
        self.app = build(
            Config(
                workspaces=(self.cwd,), working_dir=str(root / "work"), data_dir=str(root / "data")
            )
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )
        made = (
            await self.client.post(
                "/api/units", json={"cwd": self.cwd, "slug": "a-problem", "brief": "x"}
            )
        ).json()
        self.unit = made["unit"]
        unit_dir = Path(made["path"])
        for stage in ("spec", "plan", "impl", "pr", "review", "ship"):
            (unit_dir / f"{stage}.md").write_text(f"# {stage}\n", encoding="utf-8")
        self.intent = unit_dir / "intent.md"
        self.intent.write_text(QUESTIONS, encoding="utf-8")
        seed_unit(
            self.app.state.core,
            self.cwd,
            self.unit,
            statuses=dict.fromkeys(
                ("intent.md", "spec.md", "plan.md", "impl.md", "pr.md", "review.md", "ship.md"),
                "accepted",
            ),
            type="feat",
            shipped=True,
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    def body(self, **over):
        return {
            "cwd": self.cwd,
            "unit": self.unit,
            "result": "trượt",
            "measured_by": "agent",
            "source": "board, 2026-10-08",
            "recorded_by": "Phong",
            **over,
        }

    def files(self):
        return {p.name: p.read_bytes() for p in self.intent.parent.iterdir()}

    def decisions(self):
        core = self.app.state.core
        return core.ws.unit_meta().decisions(core.ws.key(self.cwd), self.unit)

    async def test_a_valid_outcome_is_a_row_and_no_file_moves(self):
        before = self.files()
        got = await self.client.post("/api/units/outcome", json=self.body())
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual((got.json()["result"], got.json()["recorded_by"]), ("trượt", "Phong"))
        self.assertEqual(self.files(), before)
        [row] = self.decisions()
        self.assertEqual((row["kind"], row["by"]), ("outcome", "Phong"))
        self.assertEqual(
            (row["fields"]["result"], row["fields"]["source"]), ("missed", "board, 2026-10-08")
        )

    async def test_a_refusal_is_a_400_and_writes_nothing(self):
        before = self.intent.read_bytes()
        got = await self.client.post("/api/units/outcome", json=self.body(source=""))
        self.assertEqual(got.status_code, 400)
        self.assertIn("source", got.json()["error"])
        for bad in (b"nope", b"[1]"):
            got = await self.client.post("/api/units/outcome", content=bad)
            self.assertEqual(got.status_code, 400)
        self.assertEqual(self.intent.read_bytes(), before)


class HoldingAUnitOverHttp(unittest.IsolatedAsyncioTestCase):
    """`POST /api/units/hold` appends one block, or answers 400 and writes nothing."""

    asyncSetUp = AnsweringAQuestionOverHttp.asyncSetUp
    asyncTearDown = AnsweringAQuestionOverHttp.asyncTearDown

    async def hold(self, **over):
        body = {
            "cwd": self.cwd,
            "unit": self.unit,
            "to": "paused",
            "reason": "chờ 0034",
            "by": "Leif",
            **over,
        }
        return await self.client.post("/api/units/hold", json=body)

    rows = AnsweringAQuestionOverHttp.rows

    async def test_a_pause_is_a_row_and_read_back(self):
        before = self.intent.read_bytes()
        got = await self.hold()
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(
            (got.json()["from"], got.json()["to"], got.json()["effects"]), ("active", "paused", [])
        )
        self.assertEqual(self.intent.read_bytes(), before)
        self.assertEqual(
            self.rows("unit_holds", "move, decided_by, reason"), [("paused", "Leif", "chờ 0034")]
        )
        nxt = (
            await self.client.get("/api/units/next", params={"cwd": self.cwd, "unit": self.unit})
        ).json()
        self.assertEqual(nxt["stage"], "")

    async def test_a_refusal_is_400_and_writes_nothing(self):
        before = self.intent.read_bytes()
        for over in ({"to": "active"}, {"reason": ""}, {"to": "sideways"}, {"cwd": "/etc"}):
            got = await self.hold(**over)
            self.assertEqual(got.status_code, 400, over)
        got = await self.client.post("/api/units/hold", content=b"nope")
        self.assertEqual(got.status_code, 400)
        self.assertEqual(self.intent.read_bytes(), before)

    async def test_no_name_is_recorded_as_owner(self):
        """What `{"by": ""}` was refused for until then."""
        got = await self.hold(by="")
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(self.rows("unit_holds", "decided_by"), [("owner",)])

    async def test_an_idea_with_no_intent_is_dropped(self):
        import subprocess

        # A workspace is a repository; an idea has no branch and no worktree in it.
        subprocess.run(["git", "init", "-q", "-b", "main", self.cwd], check=True)
        self.intent.unlink()
        idea = self.dir / "idea.md"
        before = idea.read_bytes()
        got = await self.hold(to="dropped", reason="gộp vào 0055")
        self.assertEqual(got.status_code, 200, got.text)
        effects = got.json()["effects"]
        self.assertTrue(effects)
        self.assertEqual({e["result"] for e in effects}, {"skipped"}, effects)
        self.assertEqual(self.rows("unit_holds", "move, reason"), [("dropped", "gộp vào 0055")])
        self.assertEqual(idea.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["idea.md"])


def open_pr(core, cwd: str, unit: str, number: int = 3) -> None:
    """Test glue: the PR machine's `open` row, the unit's pull request."""
    core.ws.unit_meta().history.record(
        core.ws.key(cwd),
        unit,
        "pr.md",
        "accepted",
        source="prmachine:open",
        guard="branch-named",
        authority="code",
        inputs={"number": number, "url": f"https://github.com/o/r/pull/{number}", "head": "a" * 40},
    )


def record_rounds(core, cwd: str, unit: str, rounds) -> None:
    """Test glue: review rounds as rows, `(n, verdict, [(finding, state)])` each."""
    meta = core.ws.unit_meta()
    key = core.ws.key(cwd)
    with meta.data.write() as conn:
        for n, verdict, findings in rounds:
            submitted = {
                "n": n,
                "run": f"r{n}",
                "head": "aaaaaaa",
                "object": {
                    "verdict": verdict,
                    "findings": [
                        {
                            "id": fid,
                            "state": state,
                            "fixed_in": "",
                            "severity": "low",
                            "criterion": "R1",
                            "path": "",
                            "lines": "",
                            "text": "a",
                        }
                        for fid, state in findings
                    ],
                },
            }
            meta.record_round(conn, key, unit, submitted)


_ROUND_0028 = "\n## Round {n}\n\nReviewed: aaaaaaa. Verdict: {v}.\n\n### Findings\n\n{f}\n"
REVIEW_STUCK = "# Review: q\nAuthor: t. Status: changes-requested.\n" + "".join(
    _ROUND_0028.format(n=i, v="changes-requested", f="- F1 [open] a") for i in (1, 2, 3)
)


class AllowingOneMoreRoundOverHttp(unittest.IsolatedAsyncioTestCase):
    """`POST /api/units/more-rounds` writes one row for the one unit named, or answers 400 and
    writes nothing; the loop reads the row, and only it. No file moves."""

    _answering_setup = AnsweringAQuestionOverHttp.asyncSetUp
    asyncTearDown = AnsweringAQuestionOverHttp.asyncTearDown

    async def asyncSetUp(self):
        await self._answering_setup()
        self.root = self.dir.parent.parent
        self.first = self._stuck("0101_first-stuck", REVIEW_STUCK)
        self.second = self._stuck("0102_second-stuck", REVIEW_STUCK)

    def _stuck(self, name: str, review: str, rounds=(1, 2, 3)) -> Path:
        """A unit whose `review.md` is `review`; `REVIEW_STUCK` is three rounds, each asking for
        changes, at the limit of 3."""
        d = self.dir.parent / name
        d.mkdir()
        (d / "intent.md").write_text("# I\nAuthor: t. Type: feat.\n", encoding="utf-8")
        for f in ("spec.md", "plan.md", "impl.md"):
            (d / f).write_text("# X\n", encoding="utf-8")
        (d / "pr.md").write_text(f"# PR: feat({name[:4]}): x\n", encoding="utf-8")
        (d / "review.md").write_text(review, encoding="utf-8")
        seed_unit(
            self.app.state.core,
            self.cwd,
            name,
            statuses=dict.fromkeys(("intent.md", "spec.md", "plan.md", "impl.md"), "accepted")
            | {"review.md": "changes-requested"},
            type="feat",
        )
        open_pr(self.app.state.core, self.cwd, name)
        self.rounds(name, rounds)
        return d

    def rounds(self, name: str, ns) -> None:
        """Review rounds `ns`, each asking for changes on one open finding."""
        record_rounds(
            self.app.state.core,
            self.cwd,
            name,
            [(n, "changes-requested", [("F1", "open")]) for n in ns],
        )

    def files(self) -> dict[str, bytes]:
        return {str(p): p.read_bytes() for p in self.dir.parent.rglob("*.md")}

    def decisions(self, name: str) -> list:
        core = self.app.state.core
        return core.ws.unit_meta().decisions(core.ws.key(self.cwd), name)

    async def allow(self, **over):
        return await self.client.post(
            "/api/units/more-rounds",
            json={"cwd": self.cwd, "unit": self.first.name, "by": "", **over},
        )

    async def test_one_unit_is_given_a_round_and_the_other_still_needs_a_person(self):
        from coscc.units import board

        with mock.patch.dict(os.environ):
            os.environ.pop("COS_REVIEW_ROUNDS", None)
            before = self.files()
            got = await self.allow()
            self.assertEqual(got.status_code, 200, got.text)
            self.assertEqual((got.json()["by"], got.json()["rounds"]), ("owner", 1))
            self.assertEqual(self.files(), before)
            [row] = self.decisions(self.first.name)
            self.assertEqual(
                (row["kind"], row["by"], row["fields"]), ("more-rounds", "owner", {"rounds": 1})
            )
            self.assertEqual(self.decisions(self.second.name), [])
            # The real the loop, no `--repo`: past the limit, the gate stops at the repository.
            allowed, said = await board.gate(
                str(self.root),
                self.first.name,
                "review",
                state=self.app.state.core.ws.snapshot(self.cwd),
            )
            self.assertFalse(allowed)
            self.assertIn("no repository given", said)
            self.assertNotIn("needs a person", said)
            allowed, said = await board.gate(
                str(self.root),
                self.second.name,
                "review",
                state=self.app.state.core.ws.snapshot(self.cwd),
            )
            self.assertFalse(allowed)
            self.assertIn("needs a person — review used 3 of 3", said)
            self.assertIsNone(os.environ.get("COS_REVIEW_ROUNDS"))

    async def test_a_second_press_is_refused_until_the_unit_is_out_of_rounds_again(self):
        self.assertEqual((await self.allow()).status_code, 200)
        before = self.files()
        got = await self.allow()
        self.assertEqual(got.status_code, 400)
        self.assertIn("has not used all its review rounds", got.json()["error"])
        self.assertEqual((self.files(), len(self.decisions(self.first.name))), (before, 1))
        board = await self.app.state.core.board(self.cwd, "new")
        [row] = [u for u in board["units"] if u["name"] == self.first.name]
        self.assertEqual((row["more_rounds"], row["rounds_granted"]), (False, 1))
        # A fourth round asking for changes reaches the new limit, and a press is taken again.
        self.rounds(self.first.name, [4])
        board = await self.app.state.core.board(self.cwd, "new")
        [row] = [u for u in board["units"] if u["name"] == self.first.name]
        self.assertEqual((row["more_rounds"], row["rounds_granted"]), (True, 1))
        self.assertEqual((await self.allow()).status_code, 200)
        self.assertEqual(
            [d["kind"] for d in self.decisions(self.first.name)], ["more-rounds", "more-rounds"]
        )

    async def test_a_refusal_is_400_and_writes_nothing(self):
        not_yet = self._stuck("0103_not-yet", REVIEW_STUCK.split("\n## Round 2")[0], rounds=(1,))
        files = [d / "review.md" for d in (self.first, self.second, not_yet)]
        before = [f.read_bytes() for f in files]
        for over in (
            {"unit": "0199_nothing"},
            {"cwd": "/etc"},
            {"unit": not_yet.name},
            {"unit": ""},
        ):
            got = await self.allow(**over)
            self.assertEqual(got.status_code, 400, over)
        got = await self.client.post("/api/units/more-rounds", content=b"nope")
        self.assertEqual(got.status_code, 400)
        self.assertEqual([f.read_bytes() for f in files], before)


REVIEW_CLAIMED = "# Review: q\nAuthor: t. Status: changes-requested.\n" + _ROUND_0028.format(
    n=1, v="changes-requested", f="- F2 [open] b\n- F3 [open] c"
)
REVIEW_CONFIRMED = REVIEW_CLAIMED + _ROUND_0028.format(
    n=2, v="needs-person", f="- F2 [needs-person] b\n- F3 [needs-person] c"
)


class AnsweringAFindingOverHttp(AnsweringAQuestionOverHttp):
    """A finding the last review round confirmed needs a person is answered by its id into
    `review.md`; nothing else may be answered that way."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        for name, text in {
            "spec.md": "# Spec\n",
            "plan.md": "# Plan\n",
            "impl.md": "# Impl\n\n## Needs a person\n\n- F2: no budget\n- F3: no gh\n",
            "pr.md": "# PR\n",
            "review.md": REVIEW_CONFIRMED,
        }.items():
            (self.dir / name).write_text(text, encoding="utf-8")
        self.review = self.dir / "review.md"
        seed_unit(
            self.app.state.core,
            self.cwd,
            self.unit,
            statuses=dict.fromkeys(("spec.md", "plan.md", "impl.md"), "accepted")
            | {"review.md": "changes-requested"},
        )
        open_pr(self.app.state.core, self.cwd, self.unit)
        record_rounds(
            self.app.state.core,
            self.cwd,
            self.unit,
            [
                (1, "changes-requested", [("F2", "open"), ("F3", "open")]),
                (2, "needs-person", [("F2", "needs-person"), ("F3", "needs-person")]),
            ],
        )

    async def finding(self, **over):
        return await self.post(**{"artifact": "review.md", "question": "F2", **over})

    async def test_a_finding_is_a_row_and_review_md_is_not_touched(self):
        before = self.review.read_bytes()
        got = await self.finding()
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(got.json()["question"], "F2")
        self.assertEqual(self.review.read_bytes(), before)
        self.assertEqual(
            self.rows(), [("review.md", "F2", "Phong", "product", "Tách ra. MARK-0016")]
        )

    async def test_the_board_then_waits_on_the_other_one_only(self):
        await self.finding()
        board = await self.app.state.core.board(self.cwd, "new")
        [u] = board["units"]
        self.assertEqual(u["waiting"], ["F3"])
        self.assertEqual([p["answered"] for p in u["person_findings"]], [True, False])

    async def refused_in_review(self, **over):
        before = self.review.read_bytes()
        got = await self.finding(**over)
        self.assertEqual(got.status_code, 400, got.text)
        self.assertEqual(self.review.read_bytes(), before)
        return got.json()["error"]

    async def test_a_finding_not_awaiting_a_person_is_refused(self):
        self.assertIn("F9 is not a finding", await self.refused_in_review(question="F9"))

    async def test_a_finding_is_answered_only_in_review_md(self):
        before = (self.dir / "impl.md").read_bytes()
        got = await self.finding(artifact="impl.md")
        self.assertEqual(got.status_code, 400, got.text)
        self.assertIn("answered in review.md", got.json()["error"])
        self.assertEqual((self.dir / "impl.md").read_bytes(), before)

    async def test_a_claim_no_review_has_confirmed_is_refused(self):
        record_rounds(
            self.app.state.core,
            self.cwd,
            self.unit,
            [(3, "changes-requested", [("F2", "open"), ("F3", "open")])],
        )
        await self.refused_in_review()


class PostingAReviewRoundOverHttp(unittest.IsolatedAsyncioTestCase):
    """The route posts a round once; a second press finds it and says so."""

    PR_URL = "https://github.com/o/r/pull/7"
    REVIEW = "# Review: a problem\nAuthor: t.\n"

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        (root / "work" / "proj").mkdir(parents=True)
        self.cwd = str(root / "work" / "proj")
        self.app = build(
            Config(
                workspaces=(self.cwd,), working_dir=str(root / "work"), data_dir=str(root / "data")
            )
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )
        made = (
            await self.client.post(
                "/api/units", json={"cwd": self.cwd, "slug": "a-problem", "brief": "x"}
            )
        ).json()
        self.unit = made["unit"]
        d = Path(made["path"])
        (d / "pr.md").write_text("# PR\n", encoding="utf-8")
        (d / "review.md").write_text(self.REVIEW, encoding="utf-8")
        core = self.app.state.core
        open_pr(core, self.cwd, self.unit, 7)
        record_rounds(core, self.cwd, self.unit, [(1, "changes-requested", [("F1", "open")])])
        self.calls: list[list[str]] = []
        self.comments: list[dict] = []

        async def gh(argv, cwd, stdin):
            self.calls.append(list(argv))
            if argv[:2] == ["pr", "view"]:
                return 0, json.dumps({"comments": self.comments}), ""
            self.comments.append({"body": stdin, "url": f"{self.PR_URL}#c1"})
            return 0, f"{self.PR_URL}#c1\n", ""

        patcher = mock.patch("coscc.git.gh.run", gh)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.client.aclose()

    async def post(self, **over):
        body = {"cwd": self.cwd, "unit": self.unit, "round": 1, **over}
        return await self.client.post("/api/units/review-comment", json=body)

    async def test_two_presses_make_one_comment(self):
        first, second = await self.post(), await self.post()
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual((first.json()["state"], second.json()["state"]), ("posted", "already"))
        self.assertEqual(len([c for c in self.calls if c[:2] == ["pr", "comment"]]), 1)

    async def test_bad_requests_are_400_and_reach_no_gh(self):
        for over in ({"round": 9}, {"round": "x"}, {"unit": "0099_nothing"}, {"cwd": "/etc"}):
            with self.subTest(over):
                self.assertEqual((await self.post(**over)).status_code, 400)
        got = await self.client.post("/api/units/review-comment", content=b"nope")
        self.assertEqual(got.status_code, 400)
        self.assertEqual(self.calls, [])


class StartingAUnitOverHttp(unittest.IsolatedAsyncioTestCase):
    """The routes translate and decide nothing."""

    async def asyncSetUp(self):
        import subprocess

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.repo = root / "work" / "proj"
        self.repo.mkdir(parents=True)
        for args in (
            ("init", "-q", "-b", "main"),
            ("add", "-A"),
        ):
            if args[0] == "add":
                (self.repo / "README.md").write_text("x\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.repo),
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-q",
                "-m",
                "first",
            ],
            check=True,
            capture_output=True,
        )
        # A bare directory, pushed to once: no network.
        remote = root / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
        for args in (("remote", "add", "origin", str(remote)), ("push", "-q", "origin", "main")):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)
        self.cwd = str(self.repo)
        self.app = build(
            Config(
                workspaces=(self.cwd,),
                working_dir=str(root / "work"),
                data_dir=str(root / "data"),
            )
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_a_unit_is_created_and_then_visible_on_the_board(self):
        made = await self.client.post(
            "/api/units",
            json={"cwd": self.cwd, "slug": "a-first-problem", "brief": "Nút Run im lặng."},
        )
        self.assertEqual(made.status_code, 200, made.text)
        name = made.json()["unit"]
        board = await self.app.state.core.board(self.cwd, "new")
        self.assertEqual([u["name"] for u in board["units"]], [name])

    async def test_the_brief_is_stored_as_the_idea_the_intent_step_will_read(self):
        made = await self.client.post(
            "/api/units",
            json={"cwd": self.cwd, "slug": "a-problem", "brief": "Nút Run im lặng."},
        )
        body = made.json()
        self.assertTrue(body["brief"])
        text = (Path(body["path"]) / "idea.md").read_text(encoding="utf-8")
        self.assertIn("Nút Run im lặng.", text)

    async def test_a_directory_outside_the_list_is_refused_with_400(self):
        for call in (
            self.client.post("/api/units", json={"cwd": "/etc", "slug": "a-problem"}),
            self.client.post("/api/units/branch", json={"cwd": "/etc", "unit": "0001_a"}),
        ):
            got = await call
            self.assertEqual(got.status_code, 400, got.text)
            self.assertIn("/etc", got.json()["error"])

    async def test_the_branch_route_cuts_it_and_the_board_sees_it(self):
        made = (
            await self.client.post(
                "/api/units", json={"cwd": self.cwd, "slug": "a-problem", "brief": "x"}
            )
        ).json()
        (Path(made["path"]) / "intent.md").write_text("# Intent: a problem\n", encoding="utf-8")
        seed_unit(
            self.app.state.core,
            self.cwd,
            made["unit"],
            statuses={"intent.md": "accepted"},
            type="feat",
        )
        cut = await self.client.post(
            "/api/units/branch", json={"cwd": self.cwd, "unit": made["unit"]}
        )
        self.assertEqual(cut.status_code, 200, cut.text)
        self.assertEqual(cut.json()["branch"], "feat/a-problem")
        self.assertEqual(cut.json()["base"], "origin/main")
        self.assertEqual(len(cut.json()["sha"]), 7)
        # The workspace stays on `main`; the branch is on the unit's worktree, and the board says
        # so.
        board = await self.app.state.core.board(self.cwd, "new")
        tree = next(u["worktree"] for u in board["units"] if u["name"] == made["unit"])
        self.assertEqual(tree["branch"], "feat/a-problem")
        self.assertEqual(tree["path"], cut.json()["worktree"])

    async def test_something_that_is_not_json_is_refused_before_anything_is_made(self):
        got = await self.client.post("/api/units", content=b"not json")
        self.assertEqual(got.status_code, 400)
        board = await self.app.state.core.board(self.cwd, "new")
        self.assertEqual(board["count"], 0)


class TheNextStageOverHttp(unittest.IsolatedAsyncioTestCase):
    """`GET /api/units/next` is `coscc.loop next`'s answer, and it starts nothing."""

    # The fixture of `AnsweringAQuestionOverHttp`, borrowed so its tests run once.
    asyncSetUp = AnsweringAQuestionOverHttp.asyncSetUp
    asyncTearDown = AnsweringAQuestionOverHttp.asyncTearDown

    async def test_the_route_names_the_stage_the_script_names(self):
        got = await self.client.get("/api/units/next", params={"cwd": self.cwd, "unit": self.unit})
        self.assertEqual(got.status_code, 200, got.text)
        body = got.json()
        # QUESTIONS is an accepted intent, so the files alone say `spec`.
        self.assertEqual((body["stage"], body["blocked"]), ("spec", True))
        self.assertIn("write-spec", body["action"])
        # Asking wrote nothing: the unit still holds only what the fixture put there.
        self.assertFalse((self.dir / "spec.md").exists())

    async def test_a_stage_the_gate_refuses_carries_what_the_gate_says(self):
        from coscc.units import board as board_reader

        offered = {"stage": "review", "action": "run it", "blocked": False}
        shut = board_reader.Gate(False, "cannot read the required checks of #1", ("gate-closed",))
        with (
            mock.patch.object(board_reader, "next_step", mock.AsyncMock(return_value=offered)),
            mock.patch.object(board_reader, "gate", mock.AsyncMock(return_value=shut)),
        ):
            got = await self.client.get(
                "/api/units/next", params={"cwd": self.cwd, "unit": self.unit}
            )
        self.assertEqual(got.json()["gate"], "cannot read the required checks of #1")

    async def test_a_blocked_next_asks_the_gate_nothing(self):
        from coscc.units import board as board_reader

        offered = {"stage": "review", "action": "wait", "blocked": True}
        gate = mock.AsyncMock()
        with (
            mock.patch.object(board_reader, "next_step", mock.AsyncMock(return_value=offered)),
            mock.patch.object(board_reader, "gate", gate),
        ):
            got = await self.client.get(
                "/api/units/next", params={"cwd": self.cwd, "unit": self.unit}
            )
        self.assertEqual((got.json()["blocked"], got.json()["gate"]), (True, ""))
        gate.assert_not_called()

    async def test_missing_or_unknown_arguments_are_a_400(self):
        for params in (
            {"unit": self.unit},
            {"cwd": self.cwd},
            {"cwd": self.cwd, "unit": "0099_nope"},
            {"cwd": "/etc", "unit": self.unit},
        ):
            with self.subTest(params=params):
                got = await self.client.get("/api/units/next", params=params)
                self.assertEqual(got.status_code, 400)


class WhatIsRunning(unittest.IsolatedAsyncioTestCase):
    """`Board.running`: two keys, and nothing a stop button would need."""

    asyncSetUp = AnsweringAQuestionOverHttp.asyncSetUp
    asyncTearDown = AnsweringAQuestionOverHttp.asyncTearDown

    async def test_both_keys_and_no_session_id_prompt_or_path(self):
        core = self.app.state.core
        key = core.ws.key(self.cwd)
        core.attempts.open("step", key, self.unit, "impl", state="running")
        core.ws.journal().started(
            key,
            "0099_other",
            "plan",
            "manual",
            session_id="sess-secret",
            prompt_chars=10,
        )
        body = json.loads(json.dumps(core.boards.running(self.cwd)))
        self.assertEqual(set(body), {"running", "unknown_end"})
        self.assertEqual(body["running"][self.unit][0]["agent"], {"glyph": "ᚢ", "name": "Uruz"})
        self.assertEqual(list(body["unknown_end"]), ["0099_other"])

        keys: set[str] = set()

        def walk(node):
            if isinstance(node, dict):
                keys.update(node)
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(body)
        for banned in (
            "session_id",
            "prompt",
            "prompt_chars",
            "worktree",
            "path",
            "workspace",
            "cwd",
        ):
            self.assertNotIn(banned, keys)
        self.assertNotIn("sess-secret", json.dumps(body))


class IntegratingOverHttp(PostingAReviewRoundOverHttp):
    """Over HTTP: outside the window is a 400 before anything runs, and the route is never a stage
    `next` offers."""

    async def test_a_unit_outside_the_window_is_a_400_and_leaves_a_record(self):
        # A draft pr.md: the loop says the unit is not between pr and ship, so no gh is asked.
        seed_unit(self.app.state.core, self.cwd, self.unit, statuses={"pr.md": "draft"})
        got = await self.client.post(
            "/api/units/integrate", json={"cwd": self.cwd, "unit": self.unit}
        )
        self.assertEqual(got.status_code, 400)
        self.assertIn("not between pr and ship", got.json()["error"])
        core = self.app.state.core
        rows = core.ws.journal().records(core.ws.key(self.cwd), kind="integration")
        self.assertEqual([r["outcome"] for r in rows], ["refused"])

    async def test_unknown_arguments_are_a_400(self):
        for body in (
            {"cwd": self.cwd, "unit": "0099_nope"},
            {"cwd": "/etc", "unit": self.unit},
            {},
        ):
            with self.subTest(body=body):
                got = await self.client.post("/api/units/integrate", json=body)
                self.assertEqual(got.status_code, 400)

    # The parent's own tests post comments; they are not this class's to run again.
    test_two_presses_make_one_comment = None  # type: ignore[assignment]
    test_the_board_then_shows_the_round_on_the_pr = None  # type: ignore[assignment]
    test_bad_requests_are_400_and_reach_no_gh = None  # type: ignore[assignment]

    async def test_next_never_offers_it(self):
        got = await self.client.get("/api/units/next", params={"cwd": self.cwd, "unit": self.unit})
        self.assertNotEqual(got.json().get("stage"), "integrate")


class UpdateRoutes(unittest.IsolatedAsyncioTestCase):
    SHA = "0" * 40

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = build(
            Config(workspaces=(self.tmp.name,), data_dir=str(Path(self.tmp.name) / "d"))
        )
        self.core = self.app.state.core
        self.updater = self.core.updater
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.tmp.cleanup()

    def as_a_service(self):
        self.updater._me = {
            "version": "0.12.0",
            "commit": self.SHA,
            "commit_label": self.SHA,
            "install": "package",
            "shape": "service",
            "reason": "",
            "build_id": f"0.12.0+{self.SHA}",
            "uv": "/u/uv",
            "tool_dir": "/t",
            "bin_dir": "/b",
        }
        self.updater.release = {"state": "ready", "version": "0.13.0"}

    async def test_a_failed_update_is_reported_not_a_500(self):
        self.as_a_service()
        self.updater.error = {"message": "the trial failed", "log": "/l", "log_tail": "x"}
        r = await self.client.get("/api/update")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["error"]["message"], "the trial failed")

    async def test_every_post_is_409_where_updates_are_unavailable(self):
        for path in ("/api/update/apply", "/api/update/cancel", "/api/update/build-local"):
            with self.subTest(path=path):
                r = await self.client.post(path, json={"channel": "release", "by": "an"})
                self.assertEqual(r.status_code, 409)

    async def test_a_source_in_the_body_is_never_read(self):
        self.as_a_service()
        seen = []

        async def apply(channel, by):
            seen.append((channel, by))
            return {"state": "applying"}

        self.updater.apply = apply
        plain = {"channel": "release", "by": "an"}
        # A `mode` or a `token` is one more field nobody reads.
        smuggled = {
            **plain,
            "url": "https://evil.example/x.whl",
            "path": "/tmp/x.whl",
            "version": "9.9.9",
            "ref": "evil",
            "wheel": "/tmp/x.whl",
            "mode": "now",
            "token": "t",
        }
        a = await self.client.post("/api/update/apply", json=plain)
        b = await self.client.post("/api/update/apply", json=smuggled)
        self.assertEqual((a.status_code, a.json()), (b.status_code, b.json()))
        self.assertEqual(seen, [("release", "an")] * 2)

    async def test_is_503_on_run_integrate_and_build(self):
        self.as_a_service()
        self.updater.window = True
        cwd = self.tmp.name
        for path, body in (
            ("/api/board/run", {"cwd": cwd, "unit": "0001_a", "stage": "impl"}),
            ("/api/units/integrate", {"cwd": cwd, "unit": "0001_a"}),
            ("/api/update/build-local", {"by": "an"}),
        ):
            with self.subTest(path=path):
                r = await self.client.post(path, json=body)
                self.assertEqual(r.status_code, 503, r.text)
                self.assertIn("update is being applied", r.json()["error"])


class TheBacklogOverHttp(unittest.IsolatedAsyncioTestCase):
    """Each route is 200 on a valid body and 400 on a refusal; what is refused is `backlog.py`'s and
    `Core`'s decision, tested there."""

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        (root / "work" / "proj").mkdir(parents=True)
        self.cwd = str(root / "work" / "proj")
        self.app = build(
            Config(
                workspaces=(self.cwd,), working_dir=str(root / "work"), data_dir=str(root / "data")
            )
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )
        post = lambda slug: self.client.post(
            "/api/units", json={"cwd": self.cwd, "slug": slug, "brief": "x"}
        )
        self.a = (await post("one-problem")).json()["unit"]
        self.b = (await post("two-problem")).json()["unit"]

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_estimate_relation_and_shortlist(self):
        est = {
            "cwd": self.cwd,
            "unit": self.a,
            "value": 3,
            "effort": "M",
            "basis": "x",
            "by": "Leif",
        }
        self.assertEqual(
            (await self.client.post("/api/backlog/estimate", json=est)).status_code, 200
        )
        bad = await self.client.post("/api/backlog/estimate", json={**est, "value": 0})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("value", bad.json()["error"])
        rel = {
            "cwd": self.cwd,
            "unit": self.a,
            "other": self.b,
            "type": "trùng",
            "op": "add",
            "reason": "r",
            "by": "L",
        }
        self.assertEqual(
            (await self.client.post("/api/backlog/relation", json=rel)).status_code, 200
        )
        self.assertEqual(
            (await self.client.post("/api/backlog/relation", json=rel)).status_code, 400
        )
        short = {"cwd": self.cwd, "units": [self.a], "reason": "r", "by": "L"}
        self.assertEqual(
            (await self.client.post("/api/backlog/shortlist", json=short)).status_code, 200
        )
        self.assertEqual(
            (
                await self.client.post("/api/backlog/shortlist", json={**short, "units": [self.b]})
            ).status_code,
            400,
        )
        board = await self.app.state.core.board(self.cwd, "new")
        self.assertEqual([e["unit"] for e in board["backlog"]["shortlist"]], [self.a])
        self.assertTrue(board["backlog"]["propose_warning"])
        up = (await self.client.get("/api/backlog", params={"cwd": self.cwd})).json()
        self.assertEqual([e["unit"] for e in up["shortlist"]], [self.a])
        self.assertEqual(up["shortlist"][0]["estimate"]["value"], 3)
        self.assertEqual((up["unestimated"], up["max"]), ([self.b], 7))
        self.assertEqual(up["shortlist_record"]["reason"], "r")
        # A held read starts the next; a new read begins after it, so both end before the
        # data root is removed.
        await self.app.state.core.board(self.cwd, "new")
        self.assertEqual(
            (await self.client.post("/api/backlog/shortlist", content=b"nope")).status_code, 400
        )

    async def test_propose_streams_and_a_refusal_is_a_400(self):
        class Replies:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                # It hands back no object, so the proposal fails.
                yield ("chunk", "not json")
                yield ("done", {"session_id": "s", "cost": {"cost_usd": 0.01}})

        use_sessions(self.app.state.core, Replies())
        got = await self.client.post("/api/backlog/propose", json={"cwd": self.cwd})
        self.assertEqual(got.status_code, 200)
        last = json.loads(got.text.strip().splitlines()[-1])
        self.assertEqual((last["type"], last["estimate"]["outcome"]), ("done", "failed"))
        self.assertEqual(
            (await self.client.post("/api/backlog/propose", json={"cwd": "/nope"})).status_code, 400
        )


class TheAutopilotsSettingsOverHttp(unittest.IsolatedAsyncioTestCase):
    """Md ## Answers`, câu 3."""

    async def client_for(self, host: str) -> httpx.AsyncClient:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config = Config(
            workspaces=("/tmp",), data_dir=tmp.name, working_dir=tmp.name, host=host
        )
        app = build(self.config)
        self.core = app.state.core
        self.started: list[str] = []
        self.core.autopilot.start = self.started.append
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        self.addAsyncCleanup(client.aclose)
        return client

    def prefs(self) -> dict:
        from coscc.store.db import Data

        return Data(self.config.data_dir).prefs()

    async def test_a_wrong_value_is_a_400_and_nothing_is_written(self):
        client = await self.client_for("127.0.0.1")
        for name, value in [
            ("autopilot", "yes"),
            ("autopilot", 1),
            ("autopilot_may_ship", None),
            ("max_parallel", 0),
            ("max_parallel", 2.5),
            ("max_parallel", True),
            ("max_parallel", "3"),
            ("daily_cap_usd", 0),
            ("daily_cap_usd", -1),
            ("daily_cap_usd", True),
            ("daily_cap_usd", "50"),
            ("no_such", 1),
        ]:
            got = await client.post(
                "/api/settings/autopilot", json={"cwd": "/tmp", "name": name, "value": value}
            )
            self.assertEqual(got.status_code, 400, (name, value))
        # JSON has no infinity; the service refuses one too.
        from coscc.kernel import Invalid

        with self.assertRaises(Invalid):
            self.core.autopilot.set_setting("/tmp", "daily_cap_usd", float("inf"))
        self.assertEqual(self.prefs(), {})
        self.assertEqual(self.started, [])

    async def test_off_loopback_the_switch_will_not_turn_on(self):
        client = await self.client_for("0.0.0.0")
        got = await client.post(
            "/api/settings/autopilot", json={"cwd": "/tmp", "name": "autopilot", "value": True}
        )
        self.assertEqual(got.status_code, 400)
        self.assertIn("restart it on 127.0.0.1", got.json()["error"])
        self.assertEqual((self.prefs(), self.started), ({}, []))
        # Everything but the switch itself may still be set.
        ok = await client.post(
            "/api/settings/autopilot", json={"cwd": "/tmp", "name": "max_parallel", "value": 2}
        )
        self.assertEqual(ok.status_code, 200)

    async def test_a_change_is_stored_started_and_logged_with_old_and_new(self):
        from coscc.store.journal import Journal

        client = await self.client_for("127.0.0.1")
        got = await client.post(
            "/api/settings/autopilot", json={"cwd": "/tmp", "name": "autopilot", "value": True}
        )
        self.assertEqual(got.status_code, 200, got.text)
        self.assertTrue(got.json()["autopilot"])
        self.assertEqual(self.started, ["/tmp"])
        await client.post(
            "/api/settings/autopilot", json={"cwd": "/tmp", "name": "daily_cap_usd", "value": 20}
        )
        rows = Journal(self.config.working_dir, self.config.data_dir).records(kind="setting")
        self.assertEqual(
            [(r["name"], r["old"], r["new"]) for r in rows],
            [("autopilot:/tmp", False, True), ("autopilot_daily_cap_usd", 50.0, 20.0)],
        )


class NoRequestIsTheAutopilot(unittest.IsolatedAsyncioTestCase):
    """`started_by` is not read off a body: a request is always `person`."""

    async def test_a_body_naming_the_autopilot_is_not_believed(self):
        app = build(_tmp_config(self))
        called: list[tuple] = []

        async def run_step(*args, **kwargs):
            called.append(("run", args, kwargs))
            yield ("done", {"outcome": "done"})

        async def integrate(*args, **kwargs):
            called.append(("integrate", args, kwargs))
            yield ("done", {"integration": {}})

        core = app.state.core
        core.steps.run_step = run_step
        core.integration.integrate = integrate
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            body = {"cwd": "/tmp", "unit": "0001_a", "stage": "spec", "started_by": "autopilot"}
            self.assertEqual((await client.post("/api/board/run", json=body)).status_code, 200)
            await client.post("/api/units/integrate", json=body)
        self.assertEqual([c[0] for c in called], ["run", "integrate"])
        for _, args, kwargs in called:
            self.assertNotIn("started_by", kwargs)
            self.assertNotIn("autopilot", args)


class ARerunIsReadOffTheBodyOnlyWhenItSaysTrue(unittest.IsolatedAsyncioTestCase):
    """`POST /api/board/run` passes `rerun` and `note` on only when the body's `rerun` is `true`
    itself; any other body calls `run_step` exactly as it did before."""

    async def test_rerun_and_note_are_passed_on_and_nothing_otherwise(self):
        app = build(_tmp_config(self))
        called: list[tuple] = []

        async def run_step(*args, **kwargs):
            called.append((args, kwargs))
            yield ("done", {"outcome": "done"})

        app.state.core.steps.run_step = run_step
        base = {"cwd": "/tmp", "unit": "0001_a", "stage": "pr"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            for body in (
                base,
                {**base, "rerun": "true", "note": "n"},
                {**base, "rerun": True, "note": "ghi chú"},
            ):
                self.assertEqual((await client.post("/api/board/run", json=body)).status_code, 200)
        self.assertEqual(
            called,
            [
                (("/tmp", "0001_a", "pr"), {}),
                (("/tmp", "0001_a", "pr"), {}),
                (("/tmp", "0001_a", "pr"), {"rerun": True, "note": "ghi chú"}),
            ],
        )


class OneFeatureOverTwoWorkspaces(unittest.IsolatedAsyncioTestCase):
    """Two temporary git repositories, registered as `proj` and `api`."""

    async def asyncSetUp(self):
        import subprocess

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.app = build(
            Config(
                workspaces=(), working_dir=str(self.root / "work"), data_dir=str(self.root / "data")
            )
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )
        self.addAsyncCleanup(self.client.aclose)
        self.cwd = {}
        for name in ("proj", "api"):
            repo = self.root / "work" / name
            repo.mkdir(parents=True)
            for args in (
                ["init", "-q", "-b", "main"],
                [
                    "-c",
                    "user.name=t",
                    "-c",
                    "user.email=t@t",
                    "commit",
                    "-q",
                    "--allow-empty",
                    "-m",
                    "0",
                ],
            ):
                subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
            r = await self.client.post("/api/workspaces", json={"name": name})
            self.assertEqual(r.status_code, 200, r.text)
            self.cwd[name] = r.json()["path"]

    async def post(self, route: str, **body):
        return await self.client.post(route, json=body)

    async def board(self, ws: str) -> dict:
        return await self.app.state.core.board(self.cwd[ws], "new")

    async def test_post_api_ideas_makes_and_an_empty_brief_is_400(self):
        r = await self.post(
            "/api/ideas", cwd=self.cwd["proj"], slug="one-feature", brief="both sides"
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(
            (r.json()["id"], r.json()["ref"]),
            ("0001_one-feature", "proj/ideas/0001_one-feature.md"),
        )
        self.assertEqual(
            (await self.post("/api/ideas", cwd=self.cwd["proj"], slug="x", brief="  ")).status_code,
            400,
        )

    async def test_what_an_idea_refuses_is_refused_before_a_unit_is_made(self):
        idea = (await self.post("/api/ideas", cwd=self.cwd["proj"], slug="f", brief="b")).json()[
            "ref"
        ]
        for body in (
            {"idea": idea, "brief": "a second copy of the idea"},
            {"idea": idea, "depends_on": "api/0009_nothing"},
            {"idea": "proj/ideas/0009_none.md"},
            {"idea": "not-a-ref"},
            {"depends_on": "api/0001_x"},
        ):
            r = await self.post("/api/units", cwd=self.cwd["api"], slug="x", **body)
            self.assertEqual(r.status_code, 400, body)
        self.assertEqual((await self.board("api"))["units"], [])

    async def test_a_feature_split_over_two_workspaces_is_navigable_both_ways_through_the_apps_routes(
        self,
    ):
        from coscc import units

        idea = (
            await self.post(
                "/api/ideas",
                cwd=self.cwd["proj"],
                slug="one-feature",
                brief="backend adds, frontend calls",
            )
        ).json()
        back = await self.post(
            "/api/units", cwd=self.cwd["api"], slug="backend-adds-api", idea=idea["ref"]
        )
        self.assertEqual(back.status_code, 200, back.text)
        back_ref = f"api/{back.json()['unit']}"
        front = await self.post(
            "/api/units",
            cwd=self.cwd["proj"],
            slug="frontend-calls-api",
            idea=idea["ref"],
            depends_on=back_ref,
        )
        self.assertEqual(front.status_code, 200, front.text)
        # A unit opened from an idea has no `idea.md`, and the idea file lists nothing: both
        # units are rows of `unit_links`.
        data = self.root / "data"
        self.assertFalse(
            (units.unit_dir(self.cwd["api"], back.json()["unit"], data) / "idea.md").exists()
        )
        front_dir = units.unit_dir(self.cwd["proj"], front.json()["unit"], data)
        (front_dir / "intent.md").write_text(
            "# Intent: f\nAuthor: t. Type: feat.\n", encoding="utf-8"
        )
        (front_dir / "spec.md").write_text("# S\n", encoding="utf-8")
        (front_dir / "plan.md").write_text("# P\n", encoding="utf-8")
        (units.unit_dir(self.cwd["api"], back.json()["unit"], data) / "intent.md").write_text(
            "# Intent: b\nAuthor: t. Type: feat.\n", encoding="utf-8"
        )
        core = self.app.state.core
        seed_unit(
            core,
            self.cwd["proj"],
            front.json()["unit"],
            statuses=dict.fromkeys(("intent.md", "spec.md", "plan.md"), "accepted"),
            type="feat",
        )
        seed_unit(
            core,
            self.cwd["api"],
            back.json()["unit"],
            statuses={"intent.md": "accepted"},
            type="feat",
        )
        self.assertEqual(
            sorted(core.ws.unit_meta().idea_units(idea["ref"])),
            sorted(
                [
                    (core.ws.key(self.cwd["api"]), back.json()["unit"], []),
                    (core.ws.key(self.cwd["proj"]), front.json()["unit"], [back_ref]),
                ]
            ),
        )
        self.assertNotIn("## Units", (await self.idea_text(idea)))

        proj = await self.board("proj")
        self.assertNotIn("ideas", proj)
        [f] = proj["units"]
        self.assertEqual((f["why"], f["state"]["state"]), ("dependency", "awaiting"))
        self.assertEqual(f["idea"], idea["ref"])
        [b] = (await self.board("api"))["units"]
        self.assertEqual(b["idea"], idea["ref"])
        self.assertNotIn("repo", b)
        self.assertEqual(b["problems"], [])

        # What the next steps are told, from the rows: the other unit of the idea, and where it is.
        note = core.ideas.idea_note(self.cwd["proj"], front.json()["unit"])
        self.assertIn("backend adds, frontend calls", note)
        self.assertIn(f"- {back_ref}\n", note)
        sib = await core.ideas.siblings(self.cwd["proj"], front.json()["unit"])
        self.assertIn("- api:", sib)

    async def idea_text(self, idea: dict) -> str:
        from coscc.units import ideas

        return ideas.read_text(ideas.idea_path(self.cwd["proj"], idea["id"], self.root / "data"))


class FeatureStatesOverHttp(unittest.IsolatedAsyncioTestCase):
    """`/api/features` with a two-state feature, a pilot one and one its status locks."""

    async def asyncSetUp(self):

        self.config = _tmp_config(self)
        self.cwd = self.config.workspaces[0]
        self.told: list[tuple[str, str]] = []
        self.may = True
        plugins = (
            Feature("plain", lambda _c: []),
            Feature(
                "graph",
                lambda _c: [],
                default="off",
                pilot=True,
                status=lambda _c, _w: ("Needs npm.", self.may),
                on_set=lambda _c, ws, state: self.told.append((ws, state)),
            ),
        )
        from tests.features.ctx import rows_without_feature_tools

        for patch in (mock.patch("coscc.features.FEATURES", plugins), rows_without_feature_tools()):
            patch.start()
            self.addCleanup(patch.stop)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=build(self.config)), base_url="http://t"
        )
        self.addAsyncCleanup(self.client.aclose)

    async def states(self) -> dict:
        return (await self.client.get("/api/features", params={"cwd": self.cwd})).json()

    async def test_a_state_is_set_read_back_and_the_feature_told(self):
        self.assertEqual(await self.states(), {"plain": "on", "graph": "off"})
        r = await self.client.post(
            "/api/features", json={"cwd": self.cwd, "name": "graph", "state": "pilot"}
        )
        self.assertEqual((r.status_code, r.json()), (200, {"name": "graph", "state": "pilot"}))
        self.assertEqual(await self.states(), {"plain": "on", "graph": "pilot"})
        self.assertEqual(self.told, [(self.cwd, "pilot")])

    async def test_a_wrong_state_a_pilot_without_one_or_a_locked_feature_is_400(self):
        self.may = False
        for name, state, says in (
            ("graph", "maybe", "state must be one of off, pilot, on"),
            ("plain", "pilot", "plain has no pilot: choose on or off"),
            ("graph", "on", "Needs npm."),
            ("graph", "pilot", "Needs npm."),
        ):
            with self.subTest(name=name, state=state):
                r = await self.client.post(
                    "/api/features", json={"cwd": self.cwd, "name": name, "state": state}
                )
                self.assertEqual((r.status_code, r.json()["error"]), (400, says))
        self.assertEqual(await self.states(), {"plain": "on", "graph": "off"})
        self.assertEqual(self.told, [])
        r = await self.client.post(
            "/api/features", json={"cwd": self.cwd, "name": "graph", "state": "off"}
        )
        self.assertEqual(r.status_code, 200)


class PacksOverHttp(unittest.IsolatedAsyncioTestCase):
    """`/api/packs`: a pack on or off and the default process per workspace."""

    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        (root / "work" / "proj").mkdir(parents=True)
        self.cwd = str(root / "work" / "proj")
        self.app = build(
            Config(
                workspaces=(self.cwd,), working_dir=str(root / "work"), data_dir=str(root / "data")
            )
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )
        self.addAsyncCleanup(self.client.aclose)

    async def packs(self) -> dict:
        return (await self.client.get("/api/packs", params={"cwd": self.cwd})).json()[0]

    async def open_unit(self, slug: str):
        return await self.client.post(
            "/api/units", json={"cwd": self.cwd, "slug": slug, "brief": "x"}
        )

    async def test_a_pack_is_on_with_full_until_the_owner_says_otherwise(self):
        got = await self.packs()
        self.assertEqual(
            (got["name"], got["on"], got["process"]), ("coscc-sdlc", True, "coscc-sdlc/full")
        )
        self.assertEqual([p["name"] for p in got["processes"]], ["full", "short"])

    async def test_short_as_the_default_is_what_a_new_unit_records(self):
        r = await self.client.post(
            "/api/packs",
            json={"cwd": self.cwd, "name": "coscc-sdlc", "process": "coscc-sdlc/short"},
        )
        self.assertEqual(r.status_code, 200)
        made = (await self.open_unit("quick-fix")).json()
        core = self.app.state.core
        entry = core.ws.meta_of(self.cwd, made["unit"])
        self.assertEqual(entry["process"], "coscc-sdlc/short")
        board = (await self.client.get("/api/units", params={"cwd": self.cwd})).json()
        self.assertEqual(board["units"][0]["process"], "coscc-sdlc/short")

    async def test_off_refuses_a_new_unit_and_an_idea_but_a_running_unit_still_steps(self):
        made = (await self.open_unit("already-here")).json()
        r = await self.client.post(
            "/api/packs", json={"cwd": self.cwd, "name": "coscc-sdlc", "on": False}
        )
        self.assertEqual(r.status_code, 200)
        self.assertFalse((await self.packs())["on"])
        for url, body in (
            ("/api/units", {"slug": "next-one", "brief": "x"}),
            ("/api/ideas", {"slug": "an-idea", "brief": "x"}),
        ):
            r = await self.client.post(url, json={"cwd": self.cwd, **body})
            self.assertEqual((r.status_code, r.json()["code"]), (400, "no-process"))
        steps = self.app.state.core.steps
        await steps._find_stage(self.cwd, made["unit"], "intent")

    async def test_a_unit_whose_process_no_pack_has_is_held_state_gone(self):
        from coscc.runner.queue import Refused

        made = (await self.open_unit("orphan")).json()
        core = self.app.state.core
        with core.ws.unit_meta().data.write() as conn:
            conn.execute(
                "UPDATE unit_meta SET process = 'gone/pack' WHERE unit = ?", (made["unit"],)
            )
        with self.assertRaises(Refused) as caught:
            await core.steps._find_stage(self.cwd, made["unit"], "intent")
        self.assertEqual(caught.exception.reasons, ("state-gone",))
        board = (await self.client.get("/api/units", params={"cwd": self.cwd})).json()
        self.assertEqual(board["units"][0]["process"], "gone/pack")
        self.assertEqual(board["units"][0]["why"], "state-gone")

    async def test_a_pack_or_process_not_known_is_a_400(self):
        for body in (
            {"name": "other", "on": True},
            {"name": "coscc-sdlc", "process": "coscc-sdlc/nope"},
            {"name": "coscc-sdlc", "process": "other/full"},
            {"name": "coscc-sdlc", "on": "yes"},
        ):
            r = await self.client.post("/api/packs", json={"cwd": self.cwd, **body})
            self.assertEqual(r.status_code, 400, body)
        r = await self.client.get("/api/packs", params={"cwd": "/etc"})
        self.assertEqual(r.status_code, 400)
