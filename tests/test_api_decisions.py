"""The owner's decisions over HTTP, and the log of what was decided in their place."""

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

    async def test_a_delegation_is_added_then_withdrawn_and_its_row_stays(self):
        added = await self.client.post(
            "/api/decisions",
            json={
                "kind": "delegation",
                "text": "Leif answers spec questions on fixes",
                "source": "chat 10-04",
                "agent": "Leif",
                "covers": "spec questions on fix units",
            },
        )
        self.assertEqual(added.status_code, 200, added.text)
        (row,) = added.json()["rows"]
        self.assertEqual((row["id"], row["kind"], row["state"]), ("D1", "delegation", "in force"))
        gone = await self.client.post("/api/decisions/withdraw", json={"id": "D1"})
        self.assertEqual(gone.json()["rows"][0]["state"], "withdrawn")
        again = await self.client.post("/api/decisions/withdraw", json={"id": "D1"})
        self.assertEqual(again.status_code, 400)
        self.assertIn("withdrawn already", again.json()["error"])

    async def test_a_delegation_must_say_what_it_covers(self):
        r = await self.client.post(
            "/api/decisions",
            json={"kind": "delegation", "text": "x", "source": "y", "agent": "Leif"},
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("covers", r.json()["error"])

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
