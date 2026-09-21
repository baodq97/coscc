"""Tests for the HTTP surface.

None of these create a session — the guards are exactly the paths that must refuse
*before* anything is spawned, so testing them costs nothing (`spec.md` C4). What needs a
real session is `scripts/verify_0002.py`, which is run on purpose.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import claude_agent_sdk as sdk
import httpx

from cos_baodo.api import build
from cos_baodo.config import Config


def _info(session_id="s1", cwd="/tmp"):
    return sdk.SDKSessionInfo(
        session_id=session_id, summary="s", last_modified=1, file_size=1,
        custom_title=None, first_prompt=None, git_branch=None, cwd=cwd,
        tag=None, created_at=1,
    )


class Surface(unittest.IsolatedAsyncioTestCase):
    """Driven over ASGI, the same way `scripts/verify_0002.py` drives it.

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
        # `0003` R13 widened this from a list of paths to a list of entries carrying
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
        # of this by reading to the end. verify_0002.py depends on that being true.
        r = await self.client.post(
            "/api/send", json={"cwd": "/tmp", "text": "hi", "session_id": "not-ours"}
        )
        self.assertEqual(r.status_code, 200)
        lines = [json.loads(x) for x in r.text.splitlines() if x.strip()]
        self.assertEqual(lines[-1]["type"], "error")
        self.assertIn("not created by this app", lines[-1]["error"])

    async def test_the_page_is_served(self):
        r = await self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("cos-baodo", r.text)


class WorkspaceRoutes(unittest.IsolatedAsyncioTestCase):
    """The write surface. None of these clone -- the failures they test happen first."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.app = build(Config(workspaces=(), working_dir=str(self.root)))
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
    """`0002` behaviour: no store, and the write routes say why rather than crashing."""

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


class Loopback(unittest.TestCase):
    def test_the_default_bind_is_loopback(self):
        # R5. Asserted here as well as in config_test because this is where it is used.
        from cos_baodo.config import from_env
        self.assertEqual(from_env({}).host, "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
