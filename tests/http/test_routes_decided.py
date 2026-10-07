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
        rows = r.json()["rows"]
        self.assertEqual(
            [(d["text"], d["name"], d["date"]) for d in rows],
            [("inferred", "an agent", "2026-10-04"), ("Leif's", "Leif", "2026-10-02")],
        )
        self.assertTrue(all(d["unit"] == "0001_x" and "name" in d for d in rows))

    async def page(self, units, **params):
        with mock.patch.object(
            self.app.state.core.boards, "get", mock.AsyncMock(return_value={"units": units})
        ):
            r = await self.client.get("/api/decided", params={"cwd": self.cwd, **params})
        return r.json()

    @staticmethod
    def delegated(unit, text, n=1, artifact="spec.md", name="Leif", question="", date=""):
        answers = [
            {
                "artifact": artifact,
                "n": n,
                "text": text,
                "by": "delegated",
                "name": name,
                "question": question,
                "date": date,
            }
        ]
        return {"name": unit, "answers": answers}

    def many(self):
        return [
            self.delegated("0001_x", f"answer {i}", n=i, date=f"2026-01-{i % 28 + 1:02d}")
            for i in range(120)
        ]

    async def test_a_call_returns_at_most_fifty_answers_and_the_total(self):
        page = await self.page(self.many())
        self.assertEqual((len(page["rows"]), page["total"]), (50, 120))

    async def test_offset_pages_on_and_the_last_page_is_short(self):
        page = await self.page(self.many(), offset=100)
        self.assertEqual((len(page["rows"]), page["total"]), (20, 120))

    async def test_a_limit_above_fifty_still_returns_fifty(self):
        page = await self.page(self.many(), limit=500)
        self.assertEqual(len(page["rows"]), 50)

    async def test_q_keeps_the_answers_with_every_word_in_any_field(self):
        units = [
            self.delegated("0001_a", "use sqlite"),
            self.delegated("0002_b", "use postgres"),
        ]
        self.assertEqual((await self.page(units, q="use SQLITE"))["total"], 1)
        self.assertEqual((await self.page(units, q="  "))["total"], 2)
        self.assertEqual((await self.page(units, q="0002"))["total"], 1)
        self.assertEqual((await self.page(units, q="use postgres nothing"))["total"], 0)
        field = [self.delegated("0003_c", "t", artifact="plan.md", name="Bao", question="Why?")]
        for q in ("plan.md", "bao", "why", "bao plan.md 0003"):
            self.assertEqual((await self.page(field, q=q))["total"], 1, q)

    async def test_q_filters_over_answers_outside_the_first_page(self):
        units = self.many() + [self.delegated("0002_late", "the rare word", date="2000-01-01")]
        page = await self.page(units, q="rare")
        self.assertEqual(([d["unit"] for d in page["rows"]], page["total"]), (["0002_late"], 1))
