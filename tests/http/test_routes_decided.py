"""The log of what was decided in the owner's place."""

from __future__ import annotations

import tempfile
import unittest
from unittest import mock

import httpx

from coscc.http.app import build
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

    async def test_decided_lists_only_delegated_answers_newest_first(self):
        def answer(n, text, by, name, date, artifact="spec.md"):
            return {
                "artifact": artifact,
                "n": n,
                "text": text,
                "by": by,
                "name": name,
                "date": date,
            }

        board = {
            "units": [
                {
                    "name": "0001_x",
                    "answers": [
                        answer(1, "mine", "person", "owner", "2026-10-03"),
                        answer(2, "Leif's", "delegated", "Leif", "2026-10-02"),
                        # A person's press that names Leif is still a person's.
                        answer(
                            3, "typed", "person", "Leif (CoS), for the originator", "2026-10-05"
                        ),
                        answer(1, "inferred", "delegated", "an agent", "2026-10-04", "plan.md"),
                    ],
                }
            ]
        }
        with mock.patch.object(
            self.app.state.core.boards, "get", mock.AsyncMock(return_value=board)
        ):
            r = await self.client.get("/api/decided", params={"cwd": self.cwd})
        rows = r.json()
        self.assertEqual(
            [(d["text"], d["name"], d["date"]) for d in rows],
            [("inferred", "an agent", "2026-10-04"), ("Leif's", "Leif", "2026-10-02")],
        )
        self.assertTrue(all(d["unit"] == "0001_x" and "name" in d for d in rows))
