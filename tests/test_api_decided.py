"""The log of what was decided in the owner's place."""

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
                            "text": "inferred",
                            "by": "someone",
                            "authority": "agent",
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
            [("inferred", "2026-10-04"), ("Leif's", "2026-10-02")],
        )
