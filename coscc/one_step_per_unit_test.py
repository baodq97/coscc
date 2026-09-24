"""One step per unit, however many ask at once (`0050`).

The intent's outcome, measured in `npm test`: ten `POST /api/board/run` at the same moment,
one process, one `(workspace, unit, stage)`, the gate open — one step starts, nine are
refused with a reason, and the run log has one `start`. Driven in-process over ASGI, the
way `scripts/verify_0034.py` drives it; the gate is the real `cos.mjs`, and only the session
is a stand-in. Two processes are not measured here, and nothing claims they are
(`intent.md ## Answers`, câu 4).
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

import httpx

from coscc import board as board_reader
from coscc.api import build
from coscc.config import Config

N = 10


class _Client:
    """Stands in for the SDK client a real step hands its `StepHandle`, so a Stop has one to close."""

    async def disconnect(self) -> None:
        pass


class _Sessions:
    """A session that sends one chunk, then waits until the test lets it end.

    A copy of `scripts/verify_0034.py`'s `_Sessions`, cut to one unit: nothing under
    `coscc/` imports from `scripts/`.
    """

    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
        step = kw.get("step")
        if step is not None:
            step.client = _Client()
        try:
            yield ("chunk", "# Spec: a problem\n")
            await asyncio.wait_for(self.release.wait(), 20)
            yield ("chunk", "Author: proof. Status: accepted.\n")
            yield ("done", {"session_id": "s-raced", "terminal_reason": "success",
                            "cost": {"output_tokens": 3, "turns": 1, "cost_usd": 0.01}})
        finally:
            if step is not None:
                await step.close()


class _OneUnit(unittest.IsolatedAsyncioTestCase):
    """A workspace with one unit whose `spec` gate is open, and a session that waits."""

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        workspace = root / "work" / "proj"
        workspace.mkdir(parents=True)
        self.app = build(Config(
            workspaces=(str(workspace),), working_dir=str(root / "work"), data_dir=str(root / "data"),
        ))
        self.service = self.app.state.service
        self.fake = _Sessions()
        self.service.sessions = self.fake
        self.ws = str(workspace)
        made = await self.service.create_unit(self.ws, "raced", "words for the proof")
        (Path(made["path"]) / "intent.md").write_text(
            "# Intent: x\nAuthor: proof. Type: fix. Status: accepted.\n", encoding="utf-8"
        )
        self.unit = made["unit"]
        self.key = self.service._journal_key(self.ws)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://proof")

    async def asyncTearDown(self):
        self.fake.release.set()
        await self.client.aclose()
        self._tmp.cleanup()

    def post_run(self, stage: str = "spec"):
        return self.client.post("/api/board/run", json={"cwd": self.ws, "unit": self.unit, "stage": stage})

    async def listed(self) -> list[dict]:
        return (await self.client.get("/api/board/steps", params={"cwd": self.ws})).json()

    async def until(self, predicate, what: str) -> None:
        for _ in range(1000):
            if await predicate():
                return
            await asyncio.sleep(0.01)
        self.fail(f"timed out waiting for {what}")

    def records(self, kind: str) -> list[dict]:
        return [r for r in self.service._journal().records() if r["kind"] == kind and r["unit"] == self.unit]

    async def race(self) -> tuple[list[asyncio.Task], list[httpx.Response]]:
        """Ten runs at once; returns once nine have answered and the tenth is listed.

        `ASGITransport` reads the whole body before it returns, so the winner's response
        only comes back when its stream ends (`spike.md ## U1`).
        """
        tasks = [asyncio.create_task(self.post_run()) for _ in range(N)]

        async def nine_answered():
            return sum(t.done() for t in tasks) >= N - 1

        await self.until(nine_answered, "nine of ten requests to answer")

        async def winner_listed():
            return len(await self.listed()) == 1

        await self.until(winner_listed, "the winning step to be listed")
        return tasks, [t.result() for t in tasks if t.done()]


class TenAtOnce(_OneUnit):
    async def test_ten_requests_start_one_step(self):
        tasks, answered = await self.race()
        listed = await self.listed()
        self.fake.release.set()
        final = await asyncio.gather(*tasks)

        self.assertEqual(sorted(r.status_code for r in final), [200] + [400] * (N - 1))
        self.assertEqual([(r["unit"], r["stage"]) for r in listed], [(self.unit, "spec")])
        self.assertEqual(len(self.records("start")), 1)
        refused = [r for r in answered if r.status_code == 400]
        self.assertEqual(len(refused), N - 1)
        for r in refused:
            self.assertTrue(r.json().get("error"), r.text)

    async def test_the_winner_is_listed_and_stops(self):
        tasks, _ = await self.race()
        stop = await self.client.post(
            "/api/board/stop", json={"cwd": self.ws, "unit": self.unit, "by": "Proof person"}
        )
        self.assertEqual(stop.status_code, 200, stop.text)
        await asyncio.gather(*tasks)
        [end] = self.records("end")
        self.assertEqual(end.get("outcome"), "stopped")
        self.assertEqual(end.get("stopped_by"), "Proof person")


if __name__ == "__main__":
    unittest.main()
