"""One step per unit, however many ask at once.

The intent's outcome, measured in `npm test`: ten `POST /api/board/run` at the same moment,
one process, one `(workspace, unit, stage)`, the gate open — one step starts, nine are
refused with a reason, and the run log has one `start`. Driven in-process over ASGI, the
way `scripts/verify_0034.py` drives it; the gate is the real the loop, and only the session
is a stand-in. Two processes are not measured here, and nothing claims they are
(`intent.md ## Answers`, câu 4)."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

import httpx

from coscc.units import board as board_reader
from coscc.units import worktrees
from coscc.api import build
from coscc.config import Config
from coscc.kernel import Invalid
from tests.units.test_submit import submits as _submits
from tests.service.test_service import use_sessions

N = 10


class _Client:
    """Stands in for the SDK client a real step hands its `StepHandle`, so a Stop has one to close."""

    async def disconnect(self) -> None:
        pass


class _Sessions:
    """A session that sends one chunk, then waits until the test lets it end.

    A copy of `scripts/verify_0034.py`'s `_Sessions`, cut to one unit: nothing under `coscc/`
    imports from `scripts/`. `entered` is this copy's alone: it is set once the step's session is
    open, so a Stop after it is one `Runner.run` catches."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.entered = asyncio.Event()

    async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
        step = kw.get("step")
        if step is not None:
            step.client = _Client()
        self.entered.set()
        try:
            yield ("chunk", "# Spec: a problem\n")
            await asyncio.wait_for(self.release.wait(), 20)
            yield ("chunk", "Author: proof. Status: accepted.\n")
            await _submits(kw)
            yield (
                "done",
                {
                    "session_id": "s-raced",
                    "terminal_reason": "success",
                    "cost": {"output_tokens": 3, "turns": 1, "cost_usd": 0.01},
                },
            )
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
        self.app = build(
            Config(
                workspaces=(str(workspace),),
                working_dir=str(root / "work"),
                data_dir=str(root / "data"),
            )
        )
        self.service = self.app.state.service
        self.fake = _Sessions()
        use_sessions(self.service, self.fake)
        self.ws = str(workspace)
        made = await self.service.answers.create_unit(self.ws, "raced", "words for the proof")
        (Path(made["path"]) / "intent.md").write_text(
            "# Intent: x\nAuthor: proof. Type: fix. Status: accepted.\n", encoding="utf-8"
        )
        self.unit = made["unit"]
        self.key = self.service.ws.key(self.ws)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://proof"
        )

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
        return self.client.post(
            "/api/board/run", json={"cwd": self.ws, "unit": self.unit, "stage": stage}
        )

    async def listed(self) -> list[dict]:
        return (await self.client.get("/api/board/steps", params={"cwd": self.ws})).json()

    async def until(self, predicate, what: str) -> None:
        for _ in range(1000):
            if await predicate():
                return
            await asyncio.sleep(0.01)
        self.fail(f"timed out waiting for {what}")

    def nothing_held(self) -> None:
        """No attempt holds a unit, and nothing live is kept for one."""
        self.assertEqual(self.service.attempts.unfinished(), [])
        self.assertEqual(self.service.steps.tasks, {})

    async def one_running(self) -> asyncio.Task:
        """One `spec` step, left waiting in its session once it is `running`."""
        task = asyncio.create_task(self.post_run())

        async def running_once():
            return [r["state"] for r in await self.listed()] == ["running"]

        await self.until(running_once, "the step to be running")
        return task

    def records(self, kind: str) -> list[dict]:
        return [
            r
            for r in self.service.ws.journal().records()
            if r["kind"] == kind and r["unit"] == self.unit
        ]

    async def race(self) -> tuple[list[asyncio.Task], list[httpx.Response]]:
        """Ten runs at once; returns once nine have answered and the tenth is listed.

        `ASGITransport` reads the whole body before it returns, so the winner's response only
        comes back when its stream ends."""
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
        # The stage, and the one start time the Board lists -- whether the loser met the winner
        # still preparing or already running (`plan.md` step 3i). `unit-busy`, with the
        # state of the attempt that holds the unit.
        t = listed[0]["started_at"]
        for r in refused:
            said = r.json().get("error", "")
            self.assertTrue(said.startswith(f"{self.unit} is busy: a spec step is "), said)
            self.assertTrue(
                f"is running since {t};" in said or f"is being prepared since {t};" in said, said
            )

    async def test_losers_run_no_gate_and_no_git(self):
        """A loser is refused before the worktree, the fetch or the gate: without the attempt all
        three ran ten times for ten requests."""
        (Path(self.ws) / ".git").mkdir()

        async def fake_worktree(cwd, unit, strict=False):
            self.calls["_worktree"] += 1
            return {"path": self.ws, "branch": ""}  # detached, so `refresh_base` is asked

        async def fake_refresh_base(cwd, unit, data_dir=None):
            self.calls["refresh_base"] += 1
            return None

        with (
            mock.patch.object(self.service.steps, "worktree", fake_worktree),
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
        winners = [t for t in tasks if not t.done()]
        self.assertEqual(len(winners), 1)
        # Listed is not yet driven: a Stop before `drive`'s first turn cancels a step that never
        # began, and that road writes no `end` by design.
        try:
            await asyncio.wait_for(self.fake.entered.wait(), 20)
        except asyncio.TimeoutError:
            self.fail("timed out waiting for the winning step's session to open")
        stop = await self.client.post(
            "/api/board/stop", json={"cwd": self.ws, "unit": self.unit, "by": "Proof person"}
        )
        self.assertEqual(stop.status_code, 200, stop.text)
        await asyncio.gather(*tasks)
        self.assertEqual(winners[0].result().status_code, 200, winners[0].result().text)
        self.assertEqual(len(self.records("start")), 1)
        [end] = self.records("end")
        self.assertEqual(end.get("outcome"), "stopped")
        self.assertEqual(end.get("stopped_by"), "Proof person")


class AnyStageOfTheUnit(_OneUnit):
    async def test_another_stage_is_refused_while_the_winner_runs(self):
        """Sequentially: a `plan` whose gate is closed is refused as busy, not by the gate, and the
        gate is not asked -- the attempt is asked first."""
        task = await self.one_running()
        gates = self.calls["gate"]
        refused = await self.post_run("plan")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("a spec step is running since ", refused.json()["error"])
        self.assertEqual(self.calls["gate"], gates)
        self.fake.release.set()
        self.assertEqual((await task).status_code, 200)


class TheUnitIsAlwaysGivenBack(_OneUnit):
    """Every road out of `run_step` gives the unit back."""

    async def test_after_a_gate_refusal(self):
        refused = await self.post_run("plan")
        self.assertEqual(refused.status_code, 400)
        self.assertNotIn("is busy:", refused.json()["error"])
        self.nothing_held()
        self.fake.release.set()
        self.assertEqual((await self.post_run("spec")).status_code, 200)

    async def test_after_a_cancel_while_waiting_on_the_gate(self):
        asked, never = asyncio.Event(), asyncio.Event()

        async def waiting_gate(*a, **kw):
            asked.set()
            await never.wait()

        with mock.patch.object(board_reader, "gate", waiting_gate):
            stream = self.service.steps.run_step(self.ws, self.unit, "spec")
            task = asyncio.create_task(stream.__anext__())
            await asyncio.wait_for(asked.wait(), 10)

            second = await self.post_run("spec")
            self.assertEqual(second.status_code, 400)
            self.assertIn("a spec step is being prepared since ", second.json()["error"])
            # A hold is refused in this window too, with the same sentence.
            hold = await self.client.post(
                "/api/units/hold",
                json={
                    "cwd": self.ws,
                    "unit": self.unit,
                    "to": "paused",
                    "reason": "r",
                    "by": "Proof person",
                },
            )
            self.assertEqual(hold.status_code, 400)
            self.assertIn(second.json()["error"], hold.json()["error"])

            # The reader going away takes its queue and nothing else: the attempt is still
            # `preparing`, held, until a Stop ends it.
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            [held] = self.service.attempts.unfinished()
            self.assertEqual(held["state"], "preparing")
            self.assertEqual((await self.listed())[0]["state"], "preparing")
            stop = await self.client.post(
                "/api/board/stop", json={"cwd": self.ws, "unit": self.unit, "by": "Proof person"}
            )
            self.assertEqual(stop.status_code, 200, stop.text)

            async def ended():
                return self.service.attempts.unfinished() == []

            await self.until(ended, "the stop to end the attempt")
        # a Stop at `preparing` ends `stopped`.
        end = self.service.attempts.get(held["id"])
        self.assertEqual((end["state"], end["outcome"]), ("ended", "stopped"))
        self.nothing_held()
        self.assertEqual(await self.listed(), [])

    async def test_after_an_exception_before_the_step_starts(self):
        async def broken_gate(*a, **kw):
            raise RuntimeError("stand-in: the gate broke")

        with mock.patch.object(board_reader, "gate", broken_gate):
            with self.assertRaises(RuntimeError):
                async for _ in self.service.steps.run_step(self.ws, self.unit, "spec"):
                    pass
        self.nothing_held()

    async def test_after_an_exception_between_the_listing_and_the_hand_over(self):
        """Past the open, the attempt and its live part go back too: it ends `failed`."""

        def broken(base):
            raise RuntimeError("stand-in: describing the base broke")

        with mock.patch("coscc.runner.steps.describe_base", broken):
            with self.assertRaises(RuntimeError):
                async for _ in self.service.steps.run_step(self.ws, self.unit, "spec"):
                    pass
        self.nothing_held()
        self.assertEqual(await self.listed(), [])
        task = await self.one_running()
        self.fake.release.set()
        self.assertEqual((await task).status_code, 200)

    async def test_after_a_stop_that_cancels_the_step_before_it_began(self):
        """A Stop whose turn comes before `drive`'s first one cancels a task whose body never runs,
        so `drive`'s `finally` cannot be what gives it back."""
        real_create_task = asyncio.create_task
        stops: list[asyncio.Task] = []

        def stop_first(coro, **kw):
            # The real Stop, queued ahead of `drive`'s first turn: it finds the attempt
            # `_launch` just moved to `running`, closes a handle with no client yet, and
            # cancels the task.
            if getattr(coro, "__name__", "") == "drive":
                stops.append(
                    real_create_task(
                        self.service.steps.stop_running(self.key, self.unit, "Proof person")
                    )
                )
            return real_create_task(coro, **kw)

        async def drain():
            async for _ in self.service.steps.run_step(self.ws, self.unit, "spec"):
                pass

        with mock.patch("asyncio.create_task", stop_first):
            with self.assertRaises(Invalid) as said:
                await asyncio.wait_for(drain(), 10)
        self.assertEqual(len(stops), 1)
        self.assertEqual((await stops[0])["stopped_by"], "Proof person")
        self.assertIn("before it began", str(said.exception))
        self.nothing_held()
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
        self.nothing_held()
        self.assertNotIn("is busy:", (await self.post_run("plan")).text)

        task = await self.one_running()
        self.fake.release.set()
        self.assertEqual((await task).status_code, 200)
        self.nothing_held()
        self.assertNotIn("is busy:", (await self.post_run("plan")).text)


class WhatElseHoldsTheUnit(_OneUnit):
    async def test_a_step_is_refused_while_a_hold_or_an_integration_holds_the_unit(self):
        """A hold or an integration refuses a step, before the gate."""
        for machine, stage, said in (
            ("hold", "", "a hold is being recorded since "),
            ("integration", "integrate", "it is being integrated since "),
        ):
            with self.subTest(kind=machine):
                held = self.service.attempts.open(
                    machine, self.key, self.unit, stage, state="running"
                )
                refused = await self.post_run("spec")
                self.assertEqual(refused.status_code, 400)
                self.assertIn(said + held["since"], refused.json()["error"])
                self.assertEqual(
                    self.service.attempts.holding(self.key, self.unit)["id"], held["id"]
                )
                self.service.attempts.move(held["id"], "ended", "done")
        self.assertEqual(self.calls["gate"], 0)


if __name__ == "__main__":
    unittest.main()
