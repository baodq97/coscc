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
from collections import Counter
from pathlib import Path
from unittest import mock

import httpx

from coscc import board as board_reader
from coscc import worktrees
from coscc.api import build
from coscc.config import Config
from coscc.service import Invalid

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

        self.calls: Counter = Counter()
        real_gate = board_reader.gate

        async def counting_gate(*a, **kw):
            self.calls["gate"] += 1
            return await real_gate(*a, **kw)

        patched = mock.patch.object(board_reader, "gate", counting_gate)
        patched.start()
        self.addCleanup(patched.stop)

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

    async def one_running(self) -> asyncio.Task:
        """One `spec` step, left waiting in its session once it is listed."""
        task = asyncio.create_task(self.post_run())

        async def listed_once():
            return len(await self.listed()) == 1

        await self.until(listed_once, "the step to be listed")
        return task

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
        # R3: the stage, and the one start time the Board lists -- whether the loser met
        # the winner still preparing or already running (`plan.md` step 3i).
        t = listed[0]["started_at"]
        for r in refused:
            said = r.json().get("error", "")
            self.assertTrue(said.startswith(f"{self.unit} is busy: a spec step is "), said)
            self.assertTrue(
                f"is running since {t};" in said or f"is being prepared since {t} " in said, said
            )

    async def test_losers_run_no_gate_and_no_git(self):
        """R4: refused before the worktree, the fetch or the gate -- at HEAD all three ran
        ten times for ten requests (`spike.md ## U1`, variant B)."""
        (Path(self.ws) / ".git").mkdir()

        async def fake_worktree(cwd, unit, strict=False):
            self.calls["_worktree"] += 1
            await asyncio.sleep(0.01)
            return {"path": self.ws, "branch": ""}  # detached, so `refresh_base` is asked

        async def fake_refresh_base(cwd, unit, data_dir=None):
            self.calls["refresh_base"] += 1
            await asyncio.sleep(0.05)
            return None

        with (
            mock.patch.object(self.service, "_worktree", fake_worktree),
            mock.patch.object(worktrees, "refresh_base", fake_refresh_base),
        ):
            tasks, _ = await self.race()
            self.fake.release.set()
            final = await asyncio.gather(*tasks)
        self.assertEqual(sorted(r.status_code for r in final), [200] + [400] * (N - 1))
        self.assertEqual(
            (self.calls["gate"], self.calls["_worktree"], self.calls["refresh_base"]), (1, 1, 1)
        )

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


class AnyStageOfTheUnit(_OneUnit):
    async def test_another_stage_is_refused_while_the_winner_runs(self):
        """R2, the sequential form the spec allows: a `plan` whose gate is closed is refused
        as busy, not by the gate, and the gate is not asked -- the mark is asked first."""
        task = await self.one_running()
        gates = self.calls["gate"]
        refused = await self.post_run("plan")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("a spec step is running since ", refused.json()["error"])
        self.assertEqual(self.calls["gate"], gates)
        self.fake.release.set()
        self.assertEqual((await task).status_code, 200)


class TheMarkIsAlwaysReturned(_OneUnit):
    """R5: every road out of `run_step` gives the unit back."""

    async def test_after_a_gate_refusal(self):
        refused = await self.post_run("plan")
        self.assertEqual(refused.status_code, 400)
        self.assertNotIn("is busy:", refused.json()["error"])
        self.assertEqual(self.service._active, {})
        self.fake.release.set()
        self.assertEqual((await self.post_run("spec")).status_code, 200)

    async def test_after_a_cancel_while_waiting_on_the_gate(self):
        asked, never = asyncio.Event(), asyncio.Event()

        async def waiting_gate(*a, **kw):
            asked.set()
            await never.wait()

        with mock.patch.object(board_reader, "gate", waiting_gate):
            stream = self.service.run_step(self.ws, self.unit, "spec")
            task = asyncio.create_task(stream.__anext__())
            await asyncio.wait_for(asked.wait(), 10)

            second = await self.post_run("spec")
            self.assertEqual(second.status_code, 400)
            self.assertIn("a spec step is being prepared since ", second.json()["error"])
            # R7, spec C2: a hold is refused in this window too, with the same sentence.
            hold = await self.client.post("/api/units/hold", json={
                "cwd": self.ws, "unit": self.unit, "to": "paused", "reason": "r", "by": "Proof person",
            })
            self.assertEqual(hold.status_code, 400)
            self.assertIn(second.json()["error"], hold.json()["error"])

            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.service._active, {})
        self.assertEqual(await self.listed(), [])

    async def test_after_an_exception_before_the_step_starts(self):
        async def broken_gate(*a, **kw):
            raise RuntimeError("stand-in: the gate broke")

        with mock.patch.object(board_reader, "gate", broken_gate):
            with self.assertRaises(RuntimeError):
                async for _ in self.service.run_step(self.ws, self.unit, "spec"):
                    pass
        self.assertEqual(self.service._active, {})

    async def test_after_an_exception_between_the_listing_and_the_hand_over(self):
        """Review round 1, F1: past `claim`, the listing and the `0051` entry go back too."""
        def broken(base):
            raise RuntimeError("stand-in: describing the base broke")

        with mock.patch("coscc.service_steps.describe_base", broken):
            with self.assertRaises(RuntimeError):
                async for _ in self.service.run_step(self.ws, self.unit, "spec"):
                    pass
        self.assertEqual(self.service._active, {})
        self.assertEqual(self.service._running, {})
        self.assertEqual(await self.listed(), [])
        task = await self.one_running()
        self.fake.release.set()
        self.assertEqual((await task).status_code, 200)

    async def test_after_a_stop_that_cancels_the_step_before_it_began(self):
        """Review round 2, F2: a Stop whose turn comes before `_drive`'s first one cancels a
        task whose body never runs, so `_drive`'s `finally` cannot be what gives it back."""
        real_create_task = asyncio.create_task
        stops: list[asyncio.Task] = []

        def stop_first(coro, **kw):
            # The real Stop, queued ahead of `_drive`'s first turn: it finds the row `claim`
            # just listed, closes a handle with no client yet, and cancels the task.
            if getattr(coro, "__name__", "") == "_drive":
                stops.append(real_create_task(self.service._stop_running(self.key, self.unit, "Proof person")))
            return real_create_task(coro, **kw)

        async def drain():
            async for _ in self.service.run_step(self.ws, self.unit, "spec"):
                pass

        with mock.patch("asyncio.create_task", stop_first):
            with self.assertRaises(Invalid) as said:
                await asyncio.wait_for(drain(), 10)
        self.assertEqual(len(stops), 1)
        self.assertEqual((await stops[0])["stopped_by"], "Proof person")
        self.assertIn("before it began", str(said.exception))
        self.assertEqual(self.service._active, {})
        self.assertEqual(self.service._running, {})
        self.assertEqual(await self.listed(), [])
        self.assertEqual(self.records("start"), [])
        task = await self.one_running()
        self.fake.release.set()
        self.assertEqual((await task).status_code, 200)

    async def test_after_the_winner_ends_done_and_after_it_is_stopped(self):
        task = await self.one_running()
        stop = await self.client.post(
            "/api/board/stop", json={"cwd": self.ws, "unit": self.unit, "by": "Proof person"}
        )
        self.assertEqual(stop.status_code, 200, stop.text)
        await task
        self.assertEqual(self.service._active, {})
        self.assertNotIn("is busy:", (await self.post_run("plan")).text)

        task = await self.one_running()
        self.fake.release.set()
        self.assertEqual((await task).status_code, 200)
        self.assertEqual(self.service._active, {})
        self.assertNotIn("is busy:", (await self.post_run("plan")).text)


class WhatElseHoldsTheUnit(_OneUnit):
    async def test_a_step_is_refused_while_a_hold_or_an_integration_holds_the_unit(self):
        """R7, the other way round: a hold or an integration refuses a step, before the gate."""
        for kind, said in (("hold", "a hold is being recorded since "), ("integrate", "it is being integrated since ")):
            with self.subTest(kind=kind):
                mark = self.service._take(self.key, self.unit, kind)
                refused = await self.post_run("spec")
                self.assertEqual(refused.status_code, 400)
                self.assertIn(said + mark.started_at, refused.json()["error"])
                self.assertIs(self.service._active.get((self.key, self.unit)), mark)
                self.service._release(self.key, self.unit, mark)
        self.assertEqual(self.calls["gate"], 0)


if __name__ == "__main__":
    unittest.main()
