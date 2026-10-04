"""The owner's decisions, read over HTTP and never written there, and the log of what was
decided in their place."""

from __future__ import annotations

import tempfile
import unittest
from unittest import mock

import httpx

from coscc.api import build
from coscc.config import Config


class Routes(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.app = build(Config(workspaces=(tmp.name,), data_dir=tmp.name))
        self.cwd = tmp.name
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_decisions_are_read_and_never_written_here(self):
        r = await self.client.get("/api/decisions")
        self.assertEqual(
            r.json(),
            {"rows": [], "workspaces": [self.app.state.service.ws.all()["workspaces"][0]["name"]]},
        )
        self.assertEqual((await self.client.post("/api/decisions", json={})).status_code, 405)

    async def test_decided_lists_answers_given_for_the_owner_newest_first(self):
        board = {
            "units": [
                {
                    "name": "0001_x",
                    "answers": [
                        {
                            "artifact": "spec.md",
                            "n": 1,
                            "text": "mine",
                            "by": "owner",
                            "authority": "person",
                            "date": "2026-10-03",
                        },
                        {
                            "artifact": "spec.md",
                            "n": 2,
                            "text": "Leif's",
                            "by": "Leif (CoS), for the originator",
                            "authority": "person",
                            "date": "2026-10-02",
                        },
                        {
                            "artifact": "plan.md",
                            "n": 1,
                            "text": "delegated",
                            "by": "someone",
                            "authority": "delegated",
                            "date": "2026-10-04",
                        },
                    ],
                }
            ]
        }
        with mock.patch.object(self.app.state.service, "board", mock.AsyncMock(return_value=board)):
            r = await self.client.get("/api/decided", params={"cwd": self.cwd})
        self.assertEqual(
            [(d["text"], d["date"]) for d in r.json()],
            [("delegated", "2026-10-04"), ("Leif's", "2026-10-02")],
        )
