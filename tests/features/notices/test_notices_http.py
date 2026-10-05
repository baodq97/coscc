"""Tests for the notices route in `coscc/features/notices.py`, driven over ASGI."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from coscc.config import COOKIE
from coscc.http.app import build
from coscc.config import Config
from tests.http.test_routes import _tmp_config


async def _drain(lines):
    async for _ in lines:
        pass


class FollowingNoticesOverHttp(unittest.IsolatedAsyncioTestCase):
    """Its refusals, and how long a stream outlives the session it opened on; what it sends is
    `test_notices.py`'s, which reads the generator itself."""

    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.ws = root / "work" / "proj"
        self.ws.mkdir(parents=True)
        self.config = Config(
            workspaces=(str(self.ws),), working_dir=str(root / "work"), data_dir=str(root / "data")
        )
        self.app = build(self.config)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_notice_refusals_are_400(self):
        for params in (
            {"after": "x"},
            {"after": "-1"},
            {"after": "1.5"},
            {"workspace": "/etc"},
            {"workspace": str(self.ws.parent / "other")},
        ):
            r = await self.client.get("/api/notices/follow", params=params)
            self.assertEqual(r.status_code, 400, params)
            self.assertIn("error", r.json())

    async def test_no_working_folder_is_400(self):
        app = build(_tmp_config(self))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            self.assertEqual((await client.get("/api/notices/follow")).status_code, 400)

    async def test_the_notice_route_is_behind_the_login(self):
        from coscc.http import auth

        paths = {r.path for r in self.app.routes}
        self.assertIn("/api/notices/follow", paths)
        self.assertNotIn("/api/notices/follow", {path for _, path in auth.EXEMPT})

    async def test_a_session_that_ended_hears_nothing_past_the_stream_it_was_on(self):
        """The login door asks for a live session once per request, so the stream ends after
        `notices.LIFETIME_SECONDS` and the next connection meets the door."""
        import asyncio
        import io
        import time

        from coscc.http import auth
        from coscc.features import notices
        from coscc.store.db import Data
        from coscc.store.journal import Journal

        data = Data(self.config.data_dir)
        now = int(time.time())
        data.auth_set_password("a hash nothing here verifies", now)
        token = "t" * 43
        data.auth_session_add(auth._sha(token), now, now + auth.SESSION_TTL)
        guard = auth.Guard(self.app, data, err=io.StringIO())
        cookie = {"cookie": f"{COOKIE}={token}"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=guard), base_url="http://t"
        ) as client:
            with mock.patch.object(notices, "LIFETIME_SECONDS", 0.5):
                began = time.monotonic()
                async with client.stream("GET", "/api/notices/follow", headers=cookie) as r:
                    lines = r.aiter_lines()
                    # The stream is open past the door: its head has come.
                    head = await asyncio.wait_for(anext(lines), 5)
                    data.auth_session_delete(auth._sha(token))
                    await asyncio.wait_for(_drain(lines), 5)
                self.assertLess(time.monotonic() - began, 0.5 + 1.5)
                self.assertEqual(r.status_code, 200)
                self.assertEqual(json.loads(head)["type"], "head")
                Journal(self.config.working_dir, self.config.data_dir).append(
                    {
                        "kind": "autopilot-stop",
                        "workspace": str(self.ws),
                        "unit": "0001_a",
                        "stage": "",
                        "stop": "a",
                        "reason": "r",
                    }
                )
                r = await client.get("/api/notices/follow", params={"after": "0"}, headers=cookie)
                self.assertEqual(r.status_code, 401)
                self.assertNotIn("notice", r.text)
