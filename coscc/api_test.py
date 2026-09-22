"""Tests for the HTTP surface.

None of these create a session — the guards are exactly the paths that must refuse
*before* anything is spawned, so testing them costs nothing (`spec.md` C4). What needs a
real session is `scripts/verify_0001.py`, which is run on purpose.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import claude_agent_sdk as sdk
import httpx

from coscc.api import build
from coscc.config import Config


def _info(session_id="s1", cwd="/tmp"):
    return sdk.SDKSessionInfo(
        session_id=session_id, summary="s", last_modified=1, file_size=1,
        custom_title=None, first_prompt=None, git_branch=None, cwd=cwd,
        tag=None, created_at=1,
    )


class Surface(unittest.IsolatedAsyncioTestCase):
    """Driven over ASGI, the same way `scripts/verify_0001.py` drives it.

    No socket and no lifespan: the transport speaks to the app object directly, which is
    also the object Reflex mounts. A test that needed a port would be testing something
    the proof does not exercise.
    """

    async def asyncSetUp(self):
        self.app = build(Config(workspaces=("/tmp",)))
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_workspaces_lists_what_was_configured(self):
        # R13 widened this from a list of paths to a list of entries carrying
        # name, label, source and missing, plus the count the app could not answer
        # before. `paths` is kept so the older shape still reads.
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
        # spec.md C3: a long-lived credential is in this process. The knobs and the
        # environment must not be readable over the port.
        body = (await self.client.get("/api/workspaces")).text
        for leak in ("TOKEN", "bypass", "permission_mode", "tools"):
            self.assertNotIn(leak, body)

    async def test_sessions_refuses_a_directory_outside_the_workspaces(self):
        r = await self.client.get("/api/sessions", params={"cwd": "/etc"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("workspace", r.json()["error"])

    async def test_history_refuses_a_directory_outside_the_workspaces(self):
        r = await self.client.get(
            "/api/history", params={"cwd": "/etc", "session_id": "s1"}
        )
        self.assertEqual(r.status_code, 400)

    async def test_history_requires_a_session_id(self):
        r = await self.client.get("/api/history", params={"cwd": "/tmp"})
        self.assertEqual(r.status_code, 400)

    async def test_send_refuses_a_directory_outside_the_workspaces(self):
        r = await self.client.post("/api/send", json={"cwd": "/etc", "text": "hi"})
        self.assertEqual(r.status_code, 400)

    async def test_send_requires_text(self):
        r = await self.client.post("/api/send", json={"cwd": "/tmp", "text": "  "})
        self.assertEqual(r.status_code, 400)

    async def test_send_rejects_a_non_json_body(self):
        r = await self.client.post(
            "/api/send", content="notjson", headers={"Content-Type": "application/json"}
        )
        self.assertEqual(r.status_code, 400)

    async def test_a_foreign_session_is_marked_read_only_not_hidden(self):
        # spec.md C1: terminal sessions are visible because the read layer sees them, but
        # the page has to be able to tell which ones it may write to.
        with mock.patch.object(sdk, "list_sessions", return_value=[_info()]):
            body = (await self.client.get("/api/sessions", params={"cwd": "/tmp"})).json()
        self.assertEqual(len(body["sessions"]), 1)
        self.assertFalse(body["sessions"][0]["resumable"])

    async def test_refusing_to_resume_arrives_as_an_ndjson_error_line(self):
        # The status line is committed before streaming starts, so the caller only learns
        # of this by reading to the end. verify_0001.py depends on that being true.
        r = await self.client.post(
            "/api/send", json={"cwd": "/tmp", "text": "hi", "session_id": "not-ours"}
        )
        self.assertEqual(r.status_code, 200)
        lines = [json.loads(x) for x in r.text.splitlines() if x.strip()]
        self.assertEqual(lines[-1]["type"], "error")
        self.assertIn("not created by this app", lines[-1]["error"])

    async def test_the_api_app_does_not_own_the_page(self):
        """`spec.md` R8, from the API side.

        `/` must stay unclaimed here. Reflex mounts this app as the outer one, so a route
        for `/` defined in FastAPI would win over the compiled-frontend mount and the
        Python-built page would never render. 404 from the bare API app is correct.
        """
        self.assertEqual((await self.client.get("/")).status_code, 404)


class WorkspaceRoutes(unittest.IsolatedAsyncioTestCase):
    """The write surface. None of these clone -- the failures they test happen first."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.app = build(
            Config(workspaces=(), working_dir=str(self.root), data_dir=str(self.root))
        )
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
        """`spec.md` R11. The body naming one is ignored, not honoured."""
        (self.root / "repo").mkdir()
        await self.client.post(
            "/api/workspaces", json={"name": "repo", "working_dir": "/etc"}
        )
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
        r = await self.client.patch("/api/workspaces/repo", json={"label": "Renamed"})
        self.assertEqual(r.status_code, 200)
        body = (await self.client.get("/api/workspaces")).json()
        self.assertEqual(body["workspaces"][0]["label"], "Renamed")

    async def test_remove_delists_but_leaves_the_directory(self):
        (self.root / "repo").mkdir()
        await self.client.post("/api/workspaces", json={"name": "repo"})
        r = await self.client.delete("/api/workspaces/repo")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((await self.client.get("/api/workspaces")).json()["count"], 0)
        self.assertTrue((self.root / "repo").is_dir())

    async def test_a_clone_with_a_bad_url_leaves_no_trace(self):
        """`spec.md` R16: no listed workspace, and no leftover directory."""
        r = await self.client.post(
            "/api/workspaces", json={"name": "repo", "repo_url": "git@github.com:x/y.git"}
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual((await self.client.get("/api/workspaces")).json()["count"], 0)
        self.assertFalse((self.root / "repo").exists())

    async def test_pull_on_something_not_listed_is_refused(self):
        r = await self.client.post("/api/workspaces/ghost/pull")
        self.assertEqual(r.status_code, 400)


class WithoutAWorkingFolder(unittest.IsolatedAsyncioTestCase):
    """Without a store: the write routes say why rather than crashing."""

    async def asyncSetUp(self):
        self.app = build(Config(workspaces=("/tmp",)))
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
    """`spec.md` R8 as a standing check, not just a one-off in the proof."""

    def test_the_app_ships_no_hand_written_html_or_css(self):
        repo = Path(__file__).resolve().parent.parent
        found = [
            p.relative_to(repo)
            for p in list(repo.glob("coscc/**/*.html")) + list(repo.glob("coscc/**/*.css"))
        ]
        self.assertEqual(found, [], f"hand-written markup is back: {found}")


class Loopback(unittest.TestCase):
    """Until 2026-09-22 this asserted `127.0.0.1`, and it was right to.

    `0001` R5 made the default loopback deliberately, against Reflex's own `0.0.0.0`, and
    this test existed so the posture could not drift back by accident. `0011` changed it
    on purpose: the app now ships to a VM that people reach from elsewhere, and the
    originator decided that after being shown that this file's own module docstring
    describes an app with no authentication of any kind.

    So the guarantee this class protects is no longer "loopback". It is that a person is
    *told*, every single start, and `coscc/run_test.py` is where that is checked.
    """

    def test_the_default_bind_is_every_interface_since_0011(self):
        from coscc.config import from_env
        self.assertEqual(from_env({}).host, "0.0.0.0")

    def test_loopback_is_still_one_variable_away(self):
        # The capability `0001` built did not go away, it stopped being the default.
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
        self.app = build(Config(workspaces=("/tmp",)))
        self.app.state.sessions.close_all = mock.AsyncMock()

    async def test_leaving_the_lifespan_closes_every_session(self):
        app = self.app
        async with app.router.lifespan_context(app):
            # Startup must not close anything; the app is meant to be serving here.
            app.state.sessions.close_all.assert_not_awaited()
        app.state.sessions.close_all.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
