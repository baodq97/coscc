"""Tests for the HTTP surface.

None of these create a session — the guards are exactly the paths that must refuse
*before* anything is spawned, so testing them costs nothing (`spec.md` C4). What needs a
real session is `scripts/verify_0002.py`, which is run on purpose.
"""

import json
import unittest
from unittest import mock

import claude_agent_sdk as sdk
from aiohttp.test_utils import AioHTTPTestCase

from cos_baodo.config import Config
from cos_baodo.web import build


def _info(session_id="s1", cwd="/tmp"):
    return sdk.SDKSessionInfo(
        session_id=session_id, summary="s", last_modified=1, file_size=1,
        custom_title=None, first_prompt=None, git_branch=None, cwd=cwd,
        tag=None, created_at=1,
    )


class Surface(AioHTTPTestCase):
    async def get_application(self):
        return build(Config(workspaces=("/tmp",)))

    async def test_workspaces_lists_what_was_configured(self):
        body = await (await self.client.get("/api/workspaces")).json()
        self.assertEqual(body["workspaces"], ["/tmp"])

    async def test_no_route_leaks_configuration(self):
        # spec.md C3: a long-lived credential is in this process. The knobs and the
        # environment must not be readable over the port.
        body = await (await self.client.get("/api/workspaces")).text()
        for leak in ("TOKEN", "bypass", "permission_mode", "tools"):
            self.assertNotIn(leak, body)

    async def test_sessions_refuses_a_directory_outside_the_workspaces(self):
        r = await self.client.get("/api/sessions", params={"cwd": "/etc"})
        self.assertEqual(r.status, 400)
        self.assertIn("workspace", (await r.json())["error"])

    async def test_history_refuses_a_directory_outside_the_workspaces(self):
        r = await self.client.get(
            "/api/history", params={"cwd": "/etc", "session_id": "s1"}
        )
        self.assertEqual(r.status, 400)

    async def test_history_requires_a_session_id(self):
        r = await self.client.get("/api/history", params={"cwd": "/tmp"})
        self.assertEqual(r.status, 400)

    async def test_send_refuses_a_directory_outside_the_workspaces(self):
        r = await self.client.post("/api/send", json={"cwd": "/etc", "text": "hi"})
        self.assertEqual(r.status, 400)

    async def test_send_requires_text(self):
        r = await self.client.post("/api/send", json={"cwd": "/tmp", "text": "  "})
        self.assertEqual(r.status, 400)

    async def test_send_rejects_a_non_json_body(self):
        r = await self.client.post(
            "/api/send", data="notjson", headers={"Content-Type": "application/json"}
        )
        self.assertEqual(r.status, 400)

    async def test_a_foreign_session_is_marked_read_only_not_hidden(self):
        # spec.md C1: terminal sessions are visible because the read layer sees them, but
        # the page has to be able to tell which ones it may write to.
        with mock.patch.object(sdk, "list_sessions", return_value=[_info()]):
            body = await (await self.client.get("/api/sessions", params={"cwd": "/tmp"})).json()
        self.assertEqual(len(body["sessions"]), 1)
        self.assertFalse(body["sessions"][0]["resumable"])

    async def test_refusing_to_resume_arrives_as_an_ndjson_error_line(self):
        # The status line is committed before streaming starts, so the caller only learns
        # of this by reading to the end. verify_0002.py depends on that being true.
        r = await self.client.post(
            "/api/send", json={"cwd": "/tmp", "text": "hi", "session_id": "not-ours"}
        )
        self.assertEqual(r.status, 200)
        lines = [json.loads(x) for x in (await r.text()).splitlines() if x.strip()]
        self.assertEqual(lines[-1]["type"], "error")
        self.assertIn("not created by this app", lines[-1]["error"])

    async def test_the_page_is_served(self):
        r = await self.client.get("/")
        self.assertEqual(r.status, 200)
        self.assertIn("cos-baodo", await r.text())


class Loopback(unittest.TestCase):
    def test_the_default_bind_is_loopback(self):
        # R5. Asserted here as well as in config_test because this is where it is used.
        from cos_baodo.config import from_env
        self.assertEqual(from_env({}).host, "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
