"""Tests for the board over HTTP, driven in-process the way the proof commands are.

The workspace under test is this repository itself, because it is the only one with real
work units in it. `spec.md` R10 is the rule these hold: a route may translate and nothing
more, so every refusal here has to come back word for word from `Service`.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx

from cos_baodo.api import build
from cos_baodo.config import Config

REPO = Path(__file__).resolve().parent.parent
STAGES = ["idea", "intent", "spec", "plan", "impl", "pr", "review", "ship"]


def _a_unit(body: dict) -> str:
    """Any unit name, taken from the board itself.

    Naming one here pinned these tests to a numbering that renumbering breaks; what they
    actually need is a unit that exists.
    """
    return body["units"][0]["name"]


class BoardOverHttp(unittest.IsolatedAsyncioTestCase):
    """A working folder exists, so modes can be recorded."""

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = build(
            Config(
                workspaces=(str(REPO),),
                working_dir=self._tmp.name,
                data_dir=self._tmp.name,
            )
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self._tmp.cleanup()

    async def test_the_board_carries_every_unit_with_all_eight_stages(self):
        body = (await self.client.get("/api/board", params={"cwd": str(REPO)})).json()
        self.assertEqual(body["stages"], STAGES)
        self.assertEqual(body["count"], len([d for d in (REPO / ".cos").iterdir() if d.is_dir()]))
        for unit in body["units"]:
            self.assertEqual([r["stage"] for r in unit["stages"]], STAGES)

    async def test_a_directory_that_is_not_a_workspace_is_refused(self):
        r = await self.client.get("/api/board", params={"cwd": "/etc"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("not a configured workspace", r.json()["error"])

    async def test_every_step_starts_manual_because_starting_work_is_a_decision(self):
        body = (await self.client.get("/api/board", params={"cwd": str(REPO)})).json()
        modes = {r["mode"] for u in body["units"] for r in u["stages"]}
        self.assertEqual(modes, {"manual"})

    async def test_a_mode_set_over_http_comes_back_on_the_next_read(self):
        first = (await self.client.get("/api/board", params={"cwd": str(REPO)})).json()
        payload = {"cwd": str(REPO), "unit": _a_unit(first),
                   "stage": "impl", "mode": "autonomous"}
        r = await self.client.post("/api/board/mode", json=payload)
        self.assertEqual(r.status_code, 200, r.text)

        body = (await self.client.get("/api/board", params={"cwd": str(REPO)})).json()
        unit = next(u for u in body["units"] if u["name"] == payload["unit"])
        by_stage = {r["stage"]: r["mode"] for r in unit["stages"]}
        self.assertEqual(by_stage["impl"], "autonomous")
        # and only that step moved
        self.assertEqual(by_stage["spec"], "manual")

    async def test_a_mode_is_validated_against_the_board_not_a_second_list(self):
        body = (await self.client.get("/api/board", params={"cwd": str(REPO)})).json()
        unit = _a_unit(body)
        for bad, expected in (
            ({"unit": "9999_not-here", "stage": "impl", "mode": "manual"}, "no such work unit"),
            ({"unit": unit, "stage": "deploy", "mode": "manual"}, "no such stage"),
            ({"unit": unit, "stage": "impl", "mode": "turbo"}, "mode must be one of"),
        ):
            r = await self.client.post("/api/board/mode", json={"cwd": str(REPO), **bad})
            self.assertEqual(r.status_code, 400, r.text)
            self.assertIn(expected, r.json()["error"])

    async def test_a_body_that_is_not_json_is_a_status_code_not_a_crash(self):
        r = await self.client.post(
            "/api/board/mode", content=b"not json", headers={"content-type": "application/json"}
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("body must be JSON", r.json()["error"])

    async def test_a_unit_that_never_ran_has_an_empty_timeline_and_no_cost(self):
        board_body = (await self.client.get("/api/board", params={"cwd": str(REPO)})).json()
        body = (
            await self.client.get(
                "/api/timeline",
                params={"cwd": str(REPO), "unit": _a_unit(board_body)},
            )
        ).json()
        self.assertEqual(body["runs"], [])
        # Zeroed rather than empty: the page gets the same shape whether or not a unit has
        # ever run, so it never has to branch on the difference.
        self.assertEqual(body["cost"]["input_tokens"], 0)
        self.assertEqual(body["cost"]["turns"], 0)

    async def test_the_board_says_it_is_recording(self):
        body = (await self.client.get("/api/board", params={"cwd": str(REPO)})).json()
        self.assertTrue(body["recording"])
        self.assertIsNone(body["read_only_because"])


class WithNoWorkingFolder(unittest.IsolatedAsyncioTestCase):
    """The board still reads, but nothing can be recorded."""

    async def asyncSetUp(self):
        self.app = build(Config(workspaces=(str(REPO),)))
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_the_board_still_reads(self):
        body = (await self.client.get("/api/board", params={"cwd": str(REPO)})).json()
        self.assertEqual(body["count"], len([d for d in (REPO / ".cos").iterdir() if d.is_dir()]))

    async def test_it_says_why_it_is_read_only_rather_than_looking_broken(self):
        body = (await self.client.get("/api/board", params={"cwd": str(REPO)})).json()
        self.assertFalse(body["recording"])
        self.assertIn("COS_WORKING_DIR", body["read_only_because"])

    async def test_setting_a_mode_is_refused_with_the_same_reason(self):
        body = (await self.client.get("/api/board", params={"cwd": str(REPO)})).json()
        r = await self.client.post(
            "/api/board/mode",
            json={"cwd": str(REPO), "unit": _a_unit(body),
                  "stage": "impl", "mode": "autonomous"},
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("COS_WORKING_DIR", r.json()["error"])


if __name__ == "__main__":
    unittest.main()
