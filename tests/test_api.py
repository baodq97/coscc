"""Tests for the HTTP surface.

None of these create a session — the guards are exactly the paths that must refuse
*before* anything is spawned, so testing them costs nothing. What needs a
real session is `scripts/verify_0001.py`, which is run on purpose."""

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import claude_agent_sdk as sdk
import httpx

from coscc.kernel import Feature
from coscc import update
from coscc.api import build
from coscc.config import Config
from tests.service.test_service import use_sessions


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

    async def test_workspaces_lists_what_was_configured(self):
        # `paths` is kept so the older shape still reads.
        body = (await self.client.get("/api/workspaces")).json()
        self.assertEqual(body["paths"], ["/tmp"])
        self.assertEqual(body["count"], 1)
        entry = body["workspaces"][0]
        self.assertEqual(entry["path"], "/tmp")
        self.assertEqual(entry["source"], "env")
        self.assertFalse(entry["missing"])

    async def test_an_env_workspace_is_marked_as_such(self):
        # It matters that a caller can tell: an env entry cannot be renamed or removed
        # from the app, and showing a delete button for one would be a lie.
        body = (await self.client.get("/api/workspaces")).json()
        self.assertTrue(all(e["source"] == "env" for e in body["workspaces"]))

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
            body = self.app.state.service.chat.sessions_for("/tmp")
        self.assertEqual(len(body["sessions"]), 1)
        self.assertFalse(body["sessions"][0]["resumable"])

    async def test_the_app_serves_the_studio_at_every_other_path_and_no_unknown_route(self):
        """The studio answers last: a page path gets it (503 when unbuilt), an `/api/` one never."""
        for path in ("/", "/unit/w/1"):
            got = await self.client.get(path)
            self.assertIn(got.status_code, (200, 503), path)
            self.assertTrue(got.headers["content-type"].startswith("text/html"), path)
        self.assertEqual((await self.client.get("/api/no-such")).status_code, 404)


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

    async def test_label_can_be_changed_and_read_back(self):
        (self.root / "repo").mkdir()
        await self.client.post("/api/workspaces", json={"name": "repo"})
        r = await self.client.post("/api/workspaces/repo/label", json={"label": "Renamed"})
        self.assertEqual(r.status_code, 200)
        body = (await self.client.get("/api/workspaces")).json()
        self.assertEqual(body["workspaces"][0]["label"], "Renamed")

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

    async def test_a_folder_that_is_no_git_checkout_has_no_release(self):
        (self.root / "repo").mkdir()
        await self.client.post("/api/workspaces", json={"name": "repo"})
        cwd = str(self.root / "repo")
        r = await self.client.get("/api/release", params={"cwd": cwd})
        self.assertEqual((r.status_code, r.json()), (200, None))
        await self.app.state.service.board(cwd, "new")


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

    async def test_eight_agents_and_two_other_sessions(self):
        page = await self.page()
        self.assertEqual(len(page["rows"]), 8)
        self.assertEqual([r["key"] for r in page["others"]], ["estimate", "chat"])
        impl = next(r for r in page["rows"] if r["key"] == "impl")
        self.assertEqual(impl["config"]["model_source"], "default")
        self.assertEqual([v["key"] for v in impl["variants"]], ["impl:novel"])
        self.assertEqual(impl["variants"][0]["effort"], "high")

    async def test_set_then_reset(self):
        body = {"key": "spec", "field": "turns", "value": 30}
        r = await self.client.post("/api/agents/field", json=body)
        self.assertEqual(r.status_code, 200)
        spec = next(x for x in r.json()["rows"] if x["key"] == "spec")
        self.assertEqual(spec["config"]["ceilings"]["max_turns"], 30)
        r = await self.client.post("/api/agents/field", json={"key": "spec", "field": "turns"})
        self.assertEqual(r.status_code, 200)
        spec = next(x for x in r.json()["rows"] if x["key"] == "spec")
        self.assertEqual(spec["config"]["ceilings"]["max_turns_source"], "default")

    async def test_bad_requests_are_400_with_a_reason(self):
        for body in (
            {"key": "bogus", "field": "model", "value": "m"},
            {"key": "plan", "field": "model", "value": "  "},
            {"key": "plan", "field": "effort", "value": "turbo"},
            {"key": "chat", "field": "effort", "value": "low"},
            {"key": "plan", "field": "turns", "value": 501},
            {"key": "plan", "field": "budget", "value": 0.05},
            {"key": "review", "field": "name", "value": "two words"},
            {"field": "model", "value": "m"},
            [1, 2],
        ):
            r = await self.client.post("/api/agents/field", json=body)
            self.assertEqual(r.status_code, 400, body)
            self.assertTrue(r.json()["error"], body)
        r = await self.client.post("/api/agents/field", content=b"not json")
        self.assertEqual(r.status_code, 400)

    async def test_no_route_writes_a_grant(self):
        # A grant is shown, never written.
        before = (await self.page())["rows"]
        for field in ("tools", "commands", "mcp", "submits", "warning", "grant"):
            r = await self.client.post(
                "/api/agents/field", json={"key": "impl", "field": field, "value": ["Bash"]}
            )
            self.assertEqual(r.status_code, 400, field)
        self.assertEqual(
            [r["grant"] for r in (await self.page())["rows"]], [r["grant"] for r in before]
        )
        writes = [
            route.path
            for route in self.app.routes
            if "POST" in getattr(route, "methods", ()) and "agents" in route.path
        ]
        self.assertEqual(writes, ["/api/agents/field"])

    async def test_the_settings_routes_are_gone(self):
        for path in ("/api/settings/models", "/api/settings/efforts", "/api/settings/agents"):
            r = await self.client.post(path, json={"name": "impl", "model": "m"})
            self.assertIn(r.status_code, (404, 405), path)
            r = await self.client.get(path)
            self.assertIn(r.status_code, (404, 405), path)


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


class NoHandWrittenMarkup(unittest.TestCase):
    """No hand-written markup, as a standing check, not just a one-off in the proof.

    `coscc/_web/` and `coscc/_studio/` are excluded, and the exclusion is narrow on purpose. They
    hold the *compiled* bundles (Reflex's, and the studio's built from `ui/`): generated `.html`
    and `.css` files that the release copies into the package and `.gitignore` keeps out of git. The claim this test defends is about
    markup somebody typed, so generated output is not in its scope. Excluding any wider path
    would start hiding the thing it is here to catch."""

    GENERATED = ("_web", "_studio")

    def test_the_app_ships_no_hand_written_html_or_css(self):
        repo = Path(__file__).resolve().parents[1]
        found = [
            p.relative_to(repo)
            for p in list(repo.glob("coscc/**/*.html")) + list(repo.glob("coscc/**/*.css"))
            if not set(self.GENERATED) & set(p.relative_to(repo).parts)
        ]
        self.assertEqual(found, [], f"hand-written markup is back: {found}")

    def test_the_exclusion_is_only_the_compiled_bundle(self):
        # If someone widens GENERATED to something like "coscc", the check above passes
        # while defending nothing. This is the tripwire for that.
        self.assertEqual(self.GENERATED, ("_web", "_studio"))


class Loopback(unittest.TestCase):
    """The default bind is every interface, not loopback.

    The app ships to a VM that people reach from elsewhere, so it binds `0.0.0.0` (Reflex's own
    default) and asks for a password; `COS_HOST` puts it back on `127.0.0.1`.

    So the guarantee this class protects is not "loopback". It is that a person is *told*, every
    single start, and `tests/test_run.py` is where that is checked."""

    def test_the_default_bind_is_every_interface_since(self):
        from coscc.config import from_env

        self.assertEqual(from_env({}).host, "0.0.0.0")

    def test_loopback_is_still_one_variable_away(self):
        from coscc.config import from_env

        self.assertEqual(from_env({"COS_HOST": "127.0.0.1"}).host, "127.0.0.1")


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
        app.state.service.shutdown = mock.AsyncMock(side_effect=lambda: order.append("steps"))
        app.state.sessions.close_all.side_effect = lambda: order.append("sessions")
        async with app.router.lifespan_context(app):
            app.state.service.shutdown.assert_not_awaited()
        app.state.service.shutdown.assert_awaited_once()
        self.assertEqual(order, ["steps", "sessions"])


class StoppingAStepOverHttp(unittest.IsolatedAsyncioTestCase):
    """The route decides nothing; it translates `Steps.stop_step`."""

    async def asyncSetUp(self):
        self.app = build(_tmp_config(self))
        self.service = self.app.state.service
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

        key = self.service.ws.key("/tmp")
        row = self.service.attempts.open("step", key, "0001_a", "spec", state="running")
        # The live part of the attempt: its session handle and task, by attempt id.
        running = Running(workspace=key, unit="0001_a", stage="spec", started_at=row["since"])
        running.attempt = row["id"]
        self.service.steps.tasks[row["id"]] = running
        r = await self.client.post(
            "/api/board/stop", json={"cwd": "/tmp", "unit": "0001_a", "by": "Lan"}
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"unit": "0001_a", "stage": "spec", "stopped_by": "Lan"})
        self.assertTrue(running.stop_requested)
        # the Stop is recorded on the attempt, with the name that asked.
        self.assertEqual(self.service.attempts.get(row["id"])["stop_asked_by"], "Lan")

    async def test_the_running_list_is_what_the_attempts_hold(self):
        self.assertEqual(
            (await self.client.get("/api/board/steps", params={"cwd": "/tmp"})).json(), []
        )
        self.service.attempts.open(
            "step", self.service.ws.key("/tmp"), "0001_a", "plan", state="running"
        )
        [row] = (await self.client.get("/api/board/steps", params={"cwd": "/tmp"})).json()
        self.assertEqual((row["unit"], row["stage"], row["stopping"]), ("0001_a", "plan", False))
        self.assertEqual(row["kind"], "step")

    async def test_an_integration_is_on_the_running_list_until_it_ends(self):
        key = self.service.ws.key("/tmp")
        held = self.service.attempts.open(
            "integration", key, "0001_a", "integrate", state="running"
        )
        self.service.attempts.set_road(held["id"], "gebo")
        [row] = (await self.client.get("/api/board/steps", params={"cwd": "/tmp"})).json()
        self.assertEqual(
            (row["unit"], row["stage"], row["stopping"], row["run"], row["kind"]),
            ("0001_a", "integrate", False, None, "integration"),
        )
        self.assertEqual(row["started_at"], held["since"])
        self.service.attempts.move(held["id"], "ended", "done")
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
        self.service = self.app.state.service
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )
        key = self.service.ws.key("/tmp")
        self.recorder = Recorder("r1", None, "/tmp", key, "0001_a", "impl")
        for i in range(3):
            self.recorder.denied("Bash", {"command": f"c{i}"}, "not granted")
        self.service.watch.recorders["r1"] = self.recorder

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_a_page_is_the_last_events_oldest_first(self):
        r = await self.client.get("/api/units/0001_a/runs/r1", params={"cwd": "/tmp", "limit": "2"})
        self.assertEqual(r.status_code, 200)
        page = r.json()
        self.assertEqual(
            (page["status"], page["stage"], page["has_older"]), ("running", "impl", True)
        )
        self.assertEqual([e["seq"] for e in page["events"]], [2, 3])
        self.assertEqual(page["events"][0]["input"], {"command": "c1"})
        self.assertEqual(page["events"][0]["reason"], "not granted")

    async def test_a_run_of_another_unit_or_a_bad_number_is_a_400(self):
        other = await self.client.get("/api/units/0002_b/runs/r1", params={"cwd": "/tmp"})
        self.assertEqual(other.status_code, 400)
        bad = await self.client.get(
            "/api/units/0001_a/runs/r1", params={"cwd": "/tmp", "before": "x"}
        )
        self.assertEqual(bad.status_code, 400)

    async def test_following_gives_what_is_past_after_then_says_it_is_over(self):
        self.recorder.closed = True
        r = await self.client.get(
            "/api/units/0001_a/runs/r1/follow", params={"cwd": "/tmp", "after": "1"}
        )
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
        self.service = self.app.state.service
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

        with mock.patch.object(self.service.chat, "stream", turn):
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


class FeaturesInTheStudio(unittest.IsolatedAsyncioTestCase):
    """The studio frames each feature's page and loads the kit with every feature's scripts."""

    async def asyncSetUp(self):
        self.app = build(_tmp_config(self))
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_the_vault_has_a_page(self):
        pages = (await self.client.get("/api/features/pages")).json()
        self.assertIn(
            {"name": "vault", "label": "Vault", "icon": "key-round", "path": "/vault"}, pages
        )

    async def test_the_scripts_are_the_kit_then_each_feature_s(self):
        from coscc.plugin import KIT_JS

        r = await self.client.get("/api/features/scripts")
        self.assertEqual(r.headers["content-type"].split(";")[0], "text/javascript")
        self.assertTrue(r.text.startswith(KIT_JS))
        for js in self.app.state.scripts:
            self.assertIn(js, r.text)
        self.assertIn("slot-unit", r.text)


if __name__ == "__main__":
    unittest.main()


QUESTIONS = (
    "# Intent: q\n"
    "Author: t. Type: feat. Status: accepted.\n\n"
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

    async def asyncTearDown(self):
        await self.client.aclose()

    def body(self, **over):
        return {
            "cwd": self.cwd,
            "unit": self.unit,
            "artifact": "intent.md",
            "question": 2,
            "answer": "Tách ra. MARK-0016",
            "answered_by": "Phong",
            **over,
        }

    async def post(self, **over):
        return await self.client.post("/api/units/answer", json=self.body(**over))

    def rows(
        self, table: str = "unit_answers", columns: str = "artifact, ref, answered_by, via, text"
    ) -> list[tuple]:
        """What the database holds for this unit, where the file's block once was."""
        from coscc.data import Data

        with Data(self.data_dir).connect() as conn:
            return [
                tuple(r)
                for r in conn.execute(
                    f"SELECT {columns} FROM {table} WHERE unit = ? AND once_key = '' ORDER BY id",
                    (self.unit,),
                )
            ]

    async def test_answering_through_the_api_changes_no_byte_of_the_artifact(self):
        before = self.intent.read_bytes()
        got = await self.post()
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(got.json()["question"], 2)
        self.assertEqual(self.intent.read_bytes(), before)
        self.assertEqual(
            self.rows(), [("intent.md", "2", "Phong", "product", "Tách ra. MARK-0016")]
        )

    async def test_an_answer_writes_its_journal_row_in_the_same_transaction(self):
        import sqlite3

        from coscc.runlog.journal import Journal

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
        from coscc.data import Busy
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

    async def test_the_board_then_counts_one_fewer_open(self):
        board = (await self.client.get("/api/board", params={"cwd": self.cwd, "fresh": 1})).json()
        self.assertEqual(board["units"][0]["open"], 3)
        await self.post()
        board = (await self.client.get("/api/board", params={"cwd": self.cwd, "fresh": 1})).json()
        self.assertEqual(board["units"][0]["open"], 2)

    async def test_a_second_answer_is_a_second_row(self):
        before = self.intent.read_bytes()
        await self.post()
        await self.post(question=1, answer="Có.")
        self.assertEqual(self.intent.read_bytes(), before)
        self.assertEqual([r[1] for r in self.rows()], ["2", "1"])

    async def test_the_answer_is_recorded_as_a_person_in_the_history(self):
        from coscc.units.history import History

        await self.post()
        rows = History(str(Path(self.cwd).parent), self.data_dir).outputs(
            str(Path(self.cwd).resolve()), self.unit
        )
        mine = [r for r in rows if r["source"] == "answer"]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["actor"], "human:Phong")
        self.assertEqual(mine[0]["path"], "intent.md")

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
        await self.refused(answered_by="A\nStatus: rejected")
        got = await self.post(answered_by="  ")
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(got.json()["answered_by"], "owner")

    async def test_a_closed_unit_is_refused(self):  # (f)
        self.intent.write_text(
            QUESTIONS.replace("Status: accepted", "Status: rejected"), encoding="utf-8"
        )
        self.assertIn("closed", await self.refused())

    async def test_a_finished_unit_is_refused(self):  # (f)
        (self.dir / "plan.md").write_text(
            "# Plan\nIntent: intent.md. Status: done.\n", encoding="utf-8"
        )
        self.assertIn("finished", await self.refused())

    async def test_an_answer_that_would_be_read_as_a_heading_is_refused(self):  # (g)
        await self.refused(answer="ok\n## Status: rejected")
        await self.refused(answer="### Câu 3\nhijack")

    async def test_a_file_with_a_section_after_its_answers_is_answered_all_the_same(self):  # (g)
        # It is a row now.
        self.intent.write_text(QUESTIONS + "\n## Answers\n\n## Later\n", encoding="utf-8")
        before = self.intent.read_bytes()
        self.assertEqual((await self.post()).status_code, 200)
        self.assertEqual(self.intent.read_bytes(), before)

    async def test_something_that_is_not_json_writes_nothing(self):
        before = self.intent.read_bytes()
        got = await self.client.post("/api/units/answer", content=b"nope")
        self.assertEqual(got.status_code, 400)
        self.assertEqual(self.intent.read_bytes(), before)

    async def test_a_directory_outside_the_list_is_refused(self):
        got = await self.client.post("/api/units/answer", json=self.body(cwd="/etc"))
        self.assertEqual(got.status_code, 400)

    # -- -------------------------------------------------------------------

    def decide(self, **over) -> str:
        from datetime import date

        from coscc import units
        from coscc.data import Data

        fields = {
            "kind": "delegation",
            "text": "Tên nhánh.",
            "source": "chat",
            "workspace": units.slot(self.cwd),
            "agent": "Leif",
            "covers": "naming",
            "from_day": date.today().isoformat(),
            "until_day": "",
            **over,
        }
        withdrawn = fields.pop("withdrawn", "")
        data = Data(self.data_dir)
        n = data.decision_add(**fields)
        if withdrawn:
            data.decision_withdraw(n, withdrawn)
        return f"D{n}"

    async def test_a_delegated_answer_ends_with_its_delegation_line(self):
        before = self.intent.read_bytes()
        d = self.decide()
        got = await self.post(answered_by="Leif (CoS)", delegation=d)
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(self.intent.read_bytes(), before)
        self.assertEqual(
            self.rows(),
            [
                (
                    "intent.md",
                    "2",
                    "Leif (CoS)",
                    "product",
                    f"Tách ra. MARK-0016\n\nTheo ủy quyền: {d}",
                )
            ],
        )

    async def test_a_delegation_that_is_missing_expired_withdrawn_wrong_kind_wrong_agent_or_wrong_workspace_is_refused_and_writes_nothing(
        self,
    ):
        from datetime import date

        today = date.today().isoformat()
        cases = [
            ("there is no decision D99", "D99"),
            ("is named D<n>", "1"),
            ("is a decision, not a delegation", self.decide(kind="decision")),
            ("is not in force today", self.decide(from_day="2026-01-01", until_day="2026-01-02")),
            ("is not in force today", self.decide(withdrawn=today)),
            ("delegates to Kenaz", self.decide(agent="Kenaz")),
            ("does not cover this workspace", self.decide(workspace="elsewhere-000000000000")),
        ]
        for said, d in cases:
            before = hashlib.sha256(self.intent.read_bytes()).hexdigest()
            got = await self.post(answered_by="Leif (CoS)", delegation=d)
            self.assertEqual(got.status_code, 400, (said, got.text))
            self.assertIn(said, got.json()["error"])
            self.assertEqual(hashlib.sha256(self.intent.read_bytes()).hexdigest(), before, said)
            self.assertEqual(self.rows(), [], said)

    async def test_an_answer_without_delegation_is_written_as_before(self):
        from datetime import date

        before = self.intent.read_bytes()
        got = await self.post()
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(self.intent.read_bytes(), before)
        self.assertEqual(
            self.rows(columns="artifact, ref, answered_by, date, via, text"),
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


class RecordingAnOutcomeOverHttp(unittest.IsolatedAsyncioTestCase):
    """The route appends one `### Outcome` block or writes nothing at all; what it refuses is
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
        for stage in ("spec", "impl", "pr", "review", "ship"):
            (unit_dir / f"{stage}.md").write_text(
                f"# {stage}\nStatus: accepted.\n", encoding="utf-8"
            )
        (unit_dir / "plan.md").write_text("# plan\nStatus: done.\n", encoding="utf-8")
        self.intent = unit_dir / "intent.md"
        self.intent.write_text(QUESTIONS, encoding="utf-8")

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

    async def test_a_valid_outcome_is_appended_and_the_board_shows_it(self):
        before = self.intent.read_bytes()
        got = await self.client.post("/api/units/outcome", json=self.body())
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual((got.json()["result"], got.json()["recorded_by"]), ("trượt", "Phong"))
        after = self.intent.read_bytes()
        self.assertTrue(after.startswith(before))
        self.assertIn("### Outcome", after[len(before) :].decode("utf-8"))
        board = (await self.client.get("/api/board", params={"cwd": self.cwd, "fresh": 1})).json()
        self.assertEqual(board["units"][0]["outcome"]["result"], "missed")
        self.assertEqual(board["units"][0]["outcome_label"]["text"], "trượt")

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


_ROUND_0028 = "\n## Round {n}\n\nReviewed: aaaaaaa. Verdict: {v}.\n\n### Findings\n\n{f}\n"
REVIEW_STUCK = "# Review: q\nAuthor: t. Status: changes-requested.\n" + "".join(
    _ROUND_0028.format(n=i, v="changes-requested", f="- F1 [open] a") for i in (1, 2, 3)
)


class AllowingOneMoreRoundOverHttp(unittest.IsolatedAsyncioTestCase):
    """`POST /api/units/more-rounds` appends one block to the one unit named, or answers 400 and
    writes nothing; the loop reads the block, and only it."""

    _answering_setup = AnsweringAQuestionOverHttp.asyncSetUp
    asyncTearDown = AnsweringAQuestionOverHttp.asyncTearDown

    async def asyncSetUp(self):
        await self._answering_setup()
        self.root = self.dir.parent.parent
        self.first = self._stuck("0101_first-stuck", REVIEW_STUCK)
        self.second = self._stuck("0102_second-stuck", REVIEW_STUCK)

    def _stuck(self, name: str, review: str) -> Path:
        """A unit whose `review.md` is `review`; `REVIEW_STUCK` is three rounds, each asking for
        changes, at the limit of 3."""
        d = self.dir.parent / name
        d.mkdir()
        (d / "intent.md").write_text(
            "# I\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
        )
        for f in ("spec.md", "plan.md", "impl.md"):
            (d / f).write_text("Status: accepted.\n", encoding="utf-8")
        (d / "pr.md").write_text(
            f"# PR: feat({name[:4]}): x\nPR: https://github.com/o/r/pull/3. Status: accepted.\n",
            encoding="utf-8",
        )
        (d / "review.md").write_text(review, encoding="utf-8")
        return d

    async def allow(self, **over):
        return await self.client.post(
            "/api/units/more-rounds",
            json={"cwd": self.cwd, "unit": self.first.name, "by": "", **over},
        )

    async def test_one_unit_is_given_a_round_and_the_other_still_needs_a_person(self):
        from datetime import date
        import os

        from coscc.units import board, more_rounds
        from tests.units.test_meta import snapshot_of

        with mock.patch.dict(os.environ):
            os.environ.pop("COS_REVIEW_ROUNDS", None)
            first, second = self.first / "review.md", self.second / "review.md"
            before, other = first.read_bytes(), second.read_bytes()
            got = await self.allow()
            self.assertEqual(got.status_code, 200, got.text)
            self.assertEqual((got.json()["by"], got.json()["rounds"]), ("owner", 1))
            after = first.read_bytes()
            self.assertTrue(after.startswith(before))
            added = more_rounds.block("owner", date.today().isoformat())
            # `append_to_answers` opens the section first when the file has none.
            self.assertEqual(after[len(before) :].decode("utf-8"), "\n## Answers\n" + added)
            self.assertEqual(second.read_bytes(), other)
            # The real the loop, no `--repo`: past the limit, the gate stops at the repository.
            allowed, said = await board.gate(
                str(self.root), self.first.name, "review", state=snapshot_of(self.root)
            )
            self.assertFalse(allowed)
            self.assertIn("no repository given", said)
            self.assertNotIn("needs a person", said)
            allowed, said = await board.gate(
                str(self.root), self.second.name, "review", state=snapshot_of(self.root)
            )
            self.assertFalse(allowed)
            self.assertIn("needs a person — review used 3 of 3", said)
            self.assertIsNone(os.environ.get("COS_REVIEW_ROUNDS"))

    async def test_a_second_press_is_refused_until_the_unit_is_out_of_rounds_again(self):
        self.assertEqual((await self.allow()).status_code, 200)
        before = (self.first / "review.md").read_bytes()
        got = await self.allow()
        self.assertEqual(got.status_code, 400)
        self.assertIn("has not used all its review rounds", got.json()["error"])
        self.assertEqual((self.first / "review.md").read_bytes(), before)
        board = (await self.client.get("/api/board", params={"cwd": self.cwd, "fresh": 1})).json()
        [row] = [u for u in board["units"] if u["name"] == self.first.name]
        self.assertEqual((row["more_rounds"], row["rounds_granted"]), (False, 1))
        # A fourth round asking for changes, written above `## Answers` as the runner writes
        # it, reaches the new limit, and a press is taken again.
        head, answers = before.decode("utf-8").split("\n## Answers\n", 1)
        round4 = _ROUND_0028.format(n=4, v="changes-requested", f="- F1 [open] a")
        (self.first / "review.md").write_text(
            f"{head}{round4}\n## Answers\n{answers}", encoding="utf-8"
        )
        board = (await self.client.get("/api/board", params={"cwd": self.cwd, "fresh": 1})).json()
        [row] = [u for u in board["units"] if u["name"] == self.first.name]
        self.assertEqual((row["more_rounds"], row["rounds_granted"]), (True, 1))
        self.assertEqual((await self.allow()).status_code, 200)
        self.assertEqual(
            (self.first / "review.md").read_text(encoding="utf-8").count("### More rounds"), 2
        )

    async def test_a_refusal_is_400_and_writes_nothing(self):
        not_yet = self._stuck("0103_not-yet", REVIEW_STUCK.split("\n## Round 2")[0])
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
            "spec.md": "Status: accepted.\n",
            "plan.md": "Status: accepted.\n",
            "impl.md": "# Impl\nStatus: accepted.\n\n## Needs a person\n\n- F2: no budget\n- F3: no gh\n",
            "pr.md": "PR: https://github.com/o/r/pull/3. Status: accepted.\n",
            "review.md": REVIEW_CONFIRMED,
        }.items():
            (self.dir / name).write_text(text, encoding="utf-8")
        self.review = self.dir / "review.md"

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
        board = (await self.client.get("/api/board", params={"cwd": self.cwd, "fresh": 1})).json()
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
        self.review.write_text(REVIEW_CLAIMED, encoding="utf-8")
        await self.refused_in_review()


class PostingAReviewRoundOverHttp(unittest.IsolatedAsyncioTestCase):
    """The route posts a round once; a second press finds it and says so."""

    PR_URL = "https://github.com/o/r/pull/7"
    REVIEW = (
        "# Review: a problem\nAuthor: t. Status: changes-requested.\n\n"
        "## Round 1\n\nReviewed: abcdef1. Verdict: changes-requested.\n\n"
        "### Findings\n\n- F1 [open] a thing\n"
    )

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
        (d / "pr.md").write_text(f"# PR\nStatus: accepted.\nPR: {self.PR_URL}\n", encoding="utf-8")
        (d / "review.md").write_text(self.REVIEW, encoding="utf-8")
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

    async def test_the_board_then_shows_the_round_on_the_pr(self):
        await self.post()
        board = (await self.client.get("/api/board", params={"cwd": self.cwd, "fresh": 1})).json()
        [rnd] = board["units"][0]["rounds"]
        self.assertEqual(rnd["comment"]["url"], f"{self.PR_URL}#c1")

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
        board = (await self.client.get("/api/board", params={"cwd": self.cwd, "fresh": 1})).json()
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

    async def test_a_bad_slug_is_400_in_the_scripts_own_words(self):
        got = await self.client.post("/api/units", json={"cwd": self.cwd, "slug": "Bad_Slug"})
        self.assertEqual(got.status_code, 400)
        self.assertIn("Bad_Slug", got.json()["error"])

    async def test_the_branch_route_cuts_it_and_the_board_sees_it(self):
        made = (
            await self.client.post(
                "/api/units", json={"cwd": self.cwd, "slug": "a-problem", "brief": "x"}
            )
        ).json()
        (Path(made["path"]) / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
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
        seen = await self.app.state.service.backlog.branch_here(self.cwd)
        self.assertEqual(seen["branch"], "main")
        board = (await self.client.get("/api/board", params={"cwd": self.cwd, "fresh": 1})).json()
        tree = next(u["worktree"] for u in board["units"] if u["name"] == made["unit"])
        self.assertEqual(tree["branch"], "feat/a-problem")
        self.assertEqual(tree["path"], cut.json()["worktree"])

    async def test_something_that_is_not_json_is_refused_before_anything_is_made(self):
        got = await self.client.post("/api/units", content=b"not json")
        self.assertEqual(got.status_code, 400)
        board = (await self.client.get("/api/board", params={"cwd": self.cwd, "fresh": 1})).json()
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


class WhatIsRunningOverHttp(unittest.IsolatedAsyncioTestCase):
    """`GET /api/board/running`: two keys, and nothing a stop button would need."""

    asyncSetUp = AnsweringAQuestionOverHttp.asyncSetUp
    asyncTearDown = AnsweringAQuestionOverHttp.asyncTearDown

    async def test_missing_or_foreign_cwd_is_a_400(self):
        for params in ({}, {"cwd": ""}, {"cwd": "/etc"}):
            with self.subTest(params=params):
                got = await self.client.get("/api/board/running", params=params)
                self.assertEqual(got.status_code, 400)

    async def test_both_keys_and_no_session_id_prompt_or_path(self):
        service = self.app.state.service
        key = service.ws.key(self.cwd)
        service.attempts.open("step", key, self.unit, "impl", state="running")
        service.ws.journal().started(
            key,
            "0099_other",
            "plan",
            "manual",
            session_id="sess-secret",
            prompt_chars=10,
        )
        got = await self.client.get("/api/board/running", params={"cwd": self.cwd})
        self.assertEqual(got.status_code, 200, got.text)
        body = got.json()
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
        self.assertNotIn("sess-secret", got.text)


class IntegratingOverHttp(PostingAReviewRoundOverHttp):
    """Over HTTP: outside the window is a 400 before anything runs, and the route is never a stage
    `next` offers."""

    async def test_a_unit_outside_the_window_is_a_400_and_leaves_a_record(self):
        # A draft pr.md: the loop says the unit is not between pr and ship, so no gh is asked.
        pr_md = Path(self.app.state.service.ws.unit_dir(self.cwd, self.unit)) / "pr.md"
        pr_md.write_text(f"# PR\nStatus: draft.\nPR: {self.PR_URL}\n", encoding="utf-8")
        got = await self.client.post(
            "/api/units/integrate", json={"cwd": self.cwd, "unit": self.unit}
        )
        self.assertEqual(got.status_code, 400)
        self.assertIn("not between pr and ship", got.json()["error"])
        service = self.app.state.service
        rows = service.ws.journal().records(service.ws.key(self.cwd), kind="integration")
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

    async def test_next_never_offers_it(self):
        got = await self.client.get("/api/units/next", params={"cwd": self.cwd, "unit": self.unit})
        self.assertNotEqual(got.json().get("stage"), "integrate")

    # The parent's own tests post comments; they are not this class's to run again.
    test_two_presses_make_one_comment = None  # type: ignore[assignment]
    test_the_board_then_shows_the_round_on_the_pr = None  # type: ignore[assignment]
    test_bad_requests_are_400_and_reach_no_gh = None  # type: ignore[assignment]


class UpdateRoutes(unittest.IsolatedAsyncioTestCase):
    SHA = "0" * 40

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = build(
            Config(workspaces=(self.tmp.name,), data_dir=str(Path(self.tmp.name) / "d"))
        )
        self.service = self.app.state.service
        self.updater = self.service.updater
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

    async def test_status_says_what_runs_and_why_there_is_no_button(self):
        body = (await self.client.get("/api/update")).json()
        self.assertEqual(body["shape"], "unavailable")
        self.assertIn(update.UNAVAILABLE, body["reason"])
        self.assertTrue(body["version"])

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

    async def test_the_cut_list_route_is_gone(self):
        # One Apply, so nothing lists what another way would cut.
        self.as_a_service()
        self.assertEqual((await self.client.get("/api/update/cut-list")).status_code, 404)

    def test_no_update_route_uses_a_path_reflex_reserves(self):
        paths = [
            getattr(r, "path", "") for r in self.app.routes if "update" in getattr(r, "path", "")
        ]
        self.assertEqual(len(paths), 4)
        for p in paths:
            self.assertFalse(p.startswith(("/ping/", "/_event", "/_upload")), p)


class TheBacklogOverHttp(unittest.IsolatedAsyncioTestCase):
    """Each route is 200 on a valid body and 400 on a refusal; what is refused is `backlog.py`'s and
    `Service`'s decision, tested there."""

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
        board = (await self.client.get("/api/board", params={"cwd": self.cwd, "fresh": 1})).json()
        self.assertEqual([e["unit"] for e in board["backlog"]["shortlist"]], [self.a])
        self.assertTrue(board["backlog"]["propose_warning"])
        up = (await self.client.get("/api/backlog", params={"cwd": self.cwd})).json()
        self.assertEqual([e["unit"] for e in up["shortlist"]], [self.a])
        self.assertEqual(up["shortlist"][0]["estimate"]["value"], 3)
        self.assertEqual((up["unestimated"], up["max"]), ([self.b], 7))
        self.assertEqual(up["shortlist_record"]["reason"], "r")
        # A held read starts the next; a new read begins after it, so both end before the
        # data root is removed.
        await self.app.state.service.board(self.cwd, "new")
        self.assertEqual(
            (await self.client.post("/api/backlog/shortlist", content=b"nope")).status_code, 400
        )

    async def test_propose_streams_and_a_refusal_is_a_400(self):
        class Replies:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                # It hands back no object, so the proposal fails.
                yield ("chunk", "not json")
                yield ("done", {"session_id": "s", "cost": {"cost_usd": 0.01}})

        use_sessions(self.app.state.service, Replies())
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
        self.service = app.state.service
        self.started: list[str] = []
        self.service.autopilot.start = self.started.append
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        self.addAsyncCleanup(client.aclose)
        return client

    def prefs(self) -> dict:
        from coscc.data import Data

        return Data(self.config.data_dir).prefs()

    async def test_defaults_are_off_four_and_fifty(self):
        client = await self.client_for("127.0.0.1")
        got = (await client.get("/api/settings/autopilot", params={"cwd": "/tmp"})).json()
        self.assertEqual(
            (
                got["autopilot"],
                got["autopilot_may_ship"],
                got["max_parallel"],
                got["daily_cap_usd"],
            ),
            (False, False, 4, 50.0),
        )
        self.assertEqual(got["refused_because"], "")

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
            self.service.autopilot.set_setting("/tmp", "daily_cap_usd", float("inf"))
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
        from coscc.runlog.journal import Journal

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

        service = app.state.service
        service.steps.run_step = run_step
        service.steps.integrate = integrate
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

        app.state.service.steps.run_step = run_step
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
        r = await self.client.get("/api/board", params={"cwd": self.cwd[ws], "fresh": 1})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

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

    async def test_post_api_units_with_a_brief_alone_is_unchanged(self):
        r = await self.post("/api/units", cwd=self.cwd["proj"], slug="plain", brief="words")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("idea", r.json())
        from coscc import units

        self.assertTrue(
            (
                units.unit_dir(self.cwd["proj"], r.json()["unit"], self.root / "data") / "idea.md"
            ).is_file()
        )

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
        # A unit opened from an idea has no `idea.md`, and the idea lists both.
        data = self.root / "data"
        self.assertFalse(
            (units.unit_dir(self.cwd["api"], back.json()["unit"], data) / "idea.md").exists()
        )
        # The frontend's intent, as the intent step is told to write it; the plan accepted.
        front_dir = units.unit_dir(self.cwd["proj"], front.json()["unit"], data)
        (front_dir / "intent.md").write_text(
            f"# Intent: f\nAuthor: t. Type: feat. Status: accepted.\nIdea: {idea['ref']}. Repo: proj. Depends on: {back_ref}.\n",
            encoding="utf-8",
        )
        (front_dir / "spec.md").write_text("# S\nStatus: accepted.\n", encoding="utf-8")
        (front_dir / "plan.md").write_text("# P\nStatus: accepted.\n", encoding="utf-8")
        (units.unit_dir(self.cwd["api"], back.json()["unit"], data) / "intent.md").write_text(
            f"# Intent: b\nAuthor: t. Type: feat. Status: accepted.\nIdea: {idea['ref']}. Repo: api.\n",
            encoding="utf-8",
        )

        proj = await self.board("proj")
        self.assertEqual(
            proj["ideas"][0]["units"],
            [
                {"ref": back_ref, "depends_on": []},
                {"ref": f"proj/{front.json()['unit']}", "depends_on": [back_ref]},
            ],
        )
        [f] = proj["units"]
        self.assertEqual(
            (f["why"], f["waits_for"], f["state"]["state"]), ("dependency", [back_ref], "awaiting")
        )
        [b] = (await self.board("api"))["units"]
        self.assertEqual((b["idea"], b["repo"]), (idea["ref"], "api"))
        self.assertEqual(b["problems"], [])

        page = await self.app.state.service.ideas.idea(self.cwd["proj"], "0001_one-feature")
        self.assertEqual(
            [(r["ref"], r["repo"], r["waits_for"]) for r in page["units"]],
            [
                (back_ref, "api", []),
                (f"proj/{front.json()['unit']}", "proj", [back_ref]),
            ],
        )
        self.assertEqual(page["brief"], "backend adds, frontend calls")


@unittest.skipUnless(shutil.which("uv"), "uv is needed")
class ReleasingOverHttp(unittest.IsolatedAsyncioTestCase):
    """Over HTTP: a refusal is a 400 before any line of output, with one record."""

    async def asyncSetUp(self):
        from coscc.git import fetches
        from tests.service.test_release import Fixture

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.fx = Fixture(Path(tmp.name))
        for patch in (
            mock.patch.dict(os.environ, self.fx.env),
            mock.patch.object(fetches, "shared", fetches.Fetches()),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.app = build(
            Config(
                workspaces=(self.fx.cwd,),
                working_dir=str(Path(tmp.name) / "work"),
                data_dir=str(Path(tmp.name) / "data"),
            )
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_a_refused_press_is_a_400_and_one_record(self):
        for route in ("/api/release/prepare", "/api/release/publish"):
            with self.subTest(route=route):
                got = await self.client.post(
                    route, json={"cwd": self.fx.cwd, "version": "0.2.0-rc.1"}
                )
                self.assertEqual(got.status_code, 400)
                self.assertIn("prerelease", got.json()["error"])
        service = self.app.state.service
        rows = service.ws.journal().records(service.ws.key(self.fx.cwd), kind="release")
        self.assertEqual(
            [(r["phase"], r["outcome"]) for r in rows],
            [("prepare", "refused"), ("publish", "refused")],
        )

    async def test_bad_bodies_are_400(self):
        for body in ({"cwd": "/etc", "version": "0.2.0"}, [], {}):
            with self.subTest(body=body):
                got = await self.client.post("/api/release/prepare", json=body)
                self.assertEqual(got.status_code, 400)


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
        patch = mock.patch("coscc.features.FEATURES", plugins)
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

    async def test_a_boolean_on_is_still_read_as_on_or_off(self):
        for on, state in ((False, "off"), (True, "on")):
            r = await self.client.post(
                "/api/features", json={"cwd": self.cwd, "name": "plain", "on": on}
            )
            self.assertEqual(r.json(), {"name": "plain", "state": state})
            self.assertEqual((await self.states())["plain"], state)

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
