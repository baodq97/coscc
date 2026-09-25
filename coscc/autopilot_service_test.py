"""`0043`. The autopilot inside `Service`, with stand-in sessions: no quota is spent.

`OnTheRealLoop` runs the real `cos.mjs` gate and `next` over a workspace with no git, and
only the session is a stand-in. `Scripted` replaces the board, `next` and the step itself,
so each rule of R6–R10 can be set up on its own.
"""

from __future__ import annotations

import asyncio
import dataclasses
import tempfile
import unittest
from pathlib import Path

from coscc import autopilot
from coscc.config import Config
from coscc.journal import Journal
from coscc.service import Invalid, Service
from coscc.sessions import Sessions


class _Replies:
    """A session that writes an artifact: `accepted` for the first `accepted` steps, then
    `draft`, so a chain stops where the test wants it to."""

    def __init__(self, accepted: int = 1) -> None:
        self.accepted = accepted
        self.calls = 0
        self.release = asyncio.Event()
        self.release.set()

    async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
        self.calls += 1
        status = "accepted" if self.calls <= self.accepted else "draft"
        yield ("chunk", "# Title: x\n")
        await asyncio.wait_for(self.release.wait(), 20)
        yield ("chunk", f"Author: proof. Status: {status}.\n")
        yield ("done", {"session_id": f"s{self.calls}", "terminal_reason": "success",
                        "cost": {"output_tokens": 3, "turns": 1, "cost_usd": 0.01}})


class _Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        workspace = root / "work" / "proj"
        workspace.mkdir(parents=True)
        self.config = Config(
            workspaces=(str(workspace),), working_dir=str(root / "work"), data_dir=str(root / "data"),
            host="127.0.0.1",
        )
        self.service = Service(self.config, Sessions(self.config))
        self.ws = str(workspace)
        self.key = self.service._journal_key(self.ws)
        # A pass every 5 minutes would never come in a test; the loop's first pass does.
        self.addAsyncCleanup(self.service.shutdown)

    async def unit(self, slug: str, intent: str = "Status: accepted.") -> str:
        made = await self.service.create_unit(self.ws, slug, "words for the proof")
        (Path(made["path"]) / "intent.md").write_text(
            f"# Intent: x\nAuthor: proof. Type: fix. {intent}\n", encoding="utf-8"
        )
        return made["unit"]

    def starts(self) -> list[dict]:
        return Journal(self.config.working_dir, self.config.data_dir).records(kind="start")

    async def until(self, predicate, what: str) -> None:
        for _ in range(2000):
            if predicate():
                return
            await asyncio.sleep(0.01)
        self.fail(f"timed out waiting for {what}")

    async def settled(self) -> None:
        """Every pass, launch and step the autopilot started has ended."""
        async def busy():
            return (
                self.service._active
                or any(not t.done() for _, t in (self.service._autopilot_runs.get(self.key) or {}).values())
                or self.service._autopilot_pending
                or self.service._autopilot_locks.get(self.key, asyncio.Lock()).locked()
            )

        for _ in range(3000):
            if not await busy():
                await asyncio.sleep(0.05)
                if not await busy():
                    return
            await asyncio.sleep(0.01)
        self.fail("the autopilot did not settle")


class OnTheRealLoop(_Base):
    async def test_off_starts_nothing(self):
        self.service.sessions = _Replies(accepted=5)
        await self.unit("off")
        unit = (await self.service.board(self.ws))["units"][0]["name"]
        [_ async for _ in self.service.run_step(self.ws, unit, "spec")]
        await self.settled()
        self.assertEqual([s["started_by"] for s in self.starts()], ["person"])
        self.assertEqual(self.service._autopilot_tasks, {})
        self.assertFalse((await self.service.board(self.ws))["autopilot"]["on"])

    async def test_a_done_step_starts_the_next_stage_and_a_draft_stops_it(self):
        self.service.sessions = _Replies(accepted=1)
        unit = await self.unit("chain")
        self.service.set_autopilot(self.ws, "autopilot", True)
        await self.until(lambda: len(self.starts()) >= 2, "two steps")
        await self.settled()
        self.assertEqual([(s["stage"], s["started_by"]) for s in self.starts()],
                         [("spec", "autopilot"), ("plan", "autopilot")])
        block = (await self.service.board(self.ws))["autopilot"]
        self.assertTrue(block["on"])
        [stop] = block["stops"]
        self.assertEqual((stop["unit"], stop["kind"]), (unit, "f"))
        self.assertIn("plan.md", stop["reason"])
        self.assertEqual(block["cap"]["spent"], 0.02)
        logged = Journal(self.config.working_dir, self.config.data_dir).records(kind="autopilot-stop")
        self.assertEqual([(r["unit"], r["stop"]) for r in logged], [(unit, "f")])

    async def test_a_open_question_stops_it(self):
        self.service.sessions = _Replies(accepted=5)
        unit = await self.unit("asks", "Status: accepted.\n\n## Open questions\n\n1. Which one?")
        self.service.set_autopilot(self.ws, "autopilot", True)
        await self.settled()
        self.assertEqual(self.starts(), [])
        [stop] = (await self.service.board(self.ws))["autopilot"]["stops"]
        self.assertEqual((stop["unit"], stop["kind"]), (unit, "a"))
        self.assertIn("intent.md question 1", stop["reason"])

    async def test_e_a_failed_step_is_not_run_again(self):
        self.service.sessions = _Replies(accepted=5)
        unit = await self.unit("failed")
        Journal(self.config.working_dir, self.config.data_dir).finished(self.key, unit, "spec", "failed")
        self.service.set_autopilot(self.ws, "autopilot", True)
        await self.settled()
        self.assertEqual(self.starts(), [])
        [stop] = (await self.service.board(self.ws))["autopilot"]["stops"]
        self.assertEqual(stop["kind"], "e")

    async def test_the_cap_holds_the_autopilot_and_not_a_person(self):
        self.service.sessions = _Replies(accepted=5)
        unit = await self.unit("capped")
        self.service.set_autopilot(self.ws, "daily_cap_usd", 1.0)
        self.service.set_autopilot(self.ws, "autopilot", True)
        await self.settled()
        self.assertEqual(self.starts(), [])
        [stop] = (await self.service.board(self.ws))["autopilot"]["stops"]
        self.assertEqual((stop["unit"], stop["kind"]), (unit, "cap"))
        self.service.set_autopilot(self.ws, "autopilot", False)
        [_ async for _ in self.service.run_step(self.ws, unit, "spec")]
        self.assertEqual([s["started_by"] for s in self.starts()], ["person"])

    async def test_turning_it_off_cancels_the_loop(self):
        self.service.sessions = _Replies(accepted=0)
        await self.unit("off-again")
        self.service.set_autopilot(self.ws, "autopilot", True)
        task = self.service._autopilot_tasks[self.key]
        self.service.set_autopilot(self.ws, "autopilot", False)
        await asyncio.sleep(0)
        self.assertTrue(task.cancelled() or task.done())
        self.assertNotIn(self.key, self.service._autopilot_tasks)

    async def test_start_up_resumes_a_workspace_left_on(self):
        self.service.set_autopilot(self.ws, "autopilot", True)
        self.service.autopilot_stop(self.key)
        self.assertEqual(self.service.autopilot_resume(), [self.ws])
        self.assertIn(self.key, self.service._autopilot_tasks)


class Scripted(_Base):
    """The board, `next` and the step itself replaced, so each rule is set up on its own."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.units: dict[str, dict] = {}
        self.nexts: dict[str, dict] = {}
        self.launched: list[tuple[str, str, str]] = []
        self.release = asyncio.Event()

        async def board(cwd):
            return {"units": list(self.units.values())}

        async def next_step(cwd, unit):
            return self.nexts[unit]

        def fake(kind):
            async def go(cwd, unit, stage="integrate", started_by="person"):
                mark = self.service._take(self.key, unit, "integrate" if kind == "integrate" else "step", stage)
                self.launched.append((unit, stage, started_by))
                try:
                    yield ("chunk", "x")
                    await self.release.wait()
                    yield ("done", {"outcome": "done"})
                finally:
                    self.service._release(self.key, unit, mark)
            return go

        self.service.board = board
        self.service.next_step = next_step
        self.service.run_step = fake("step")
        self.service.integrate = lambda cwd, unit, started_by="person": fake("integrate")(cwd, unit, started_by=started_by)
        self.service._autopilot_cwd[self.key] = self.ws
        self.service.set_autopilot(self.ws, "autopilot", True)
        self.service.autopilot_stop(self.key)
        # A loop that never passes on its own: each test asks for a pass.
        self.service._autopilot_tasks[self.key] = asyncio.get_running_loop().create_future()
        self.service._autopilot_cwd[self.key] = self.ws
        self.addCleanup(self.release.set)

    def add(self, name, stage, action="", plan=None, **unit):
        self.units[name] = {"name": name, "next": action or f"write-{stage}", "questions": [], **unit}
        self.nexts[name] = {"stage": stage, "action": action or f"write-{stage}", "waiting": [], "hold": None}
        if plan is not None:
            d = self.service._unit_dir(self.ws, name)
            d.mkdir(parents=True, exist_ok=True)
            (d / "plan.md").write_text(f"# Plan\n\n## Files that change\n\n{plan}\n\n## Order\n", encoding="utf-8")

    async def pass_(self):
        await self.service._autopilot_pass(self.key)
        await asyncio.sleep(0.05)

    def stops(self):
        return {u: s["kind"] for u, s in self.service._autopilot_stops.get(self.key, {}).items()}

    async def test_max_parallel_one(self):
        self.service.set_autopilot(self.ws, "max_parallel", 1)
        self.service.autopilot_stop(self.key)
        self.service._autopilot_tasks[self.key] = asyncio.get_running_loop().create_future()
        self.add("0001_a", "spec")
        self.add("0002_b", "spec")
        await self.pass_()
        self.assertEqual(self.launched, [("0001_a", "spec", "autopilot")])
        await self.pass_()
        self.assertEqual(len(self.launched), 1)

    async def test_overlapping_impls_run_one_after_the_other(self):
        self.add("0001_a", "impl", plan="- `coscc/x.py`\n- `coscc/y.py`")
        self.add("0002_b", "impl", plan="- `coscc/y.py`")
        self.add("0003_c", "impl", plan="- `coscc/z.py`")
        await self.pass_()
        self.assertEqual([u for u, _, _ in self.launched], ["0001_a", "0003_c"])
        self.release.set()
        await self.settled()
        self.launched.clear()
        self.release.clear()
        del self.units["0001_a"], self.units["0003_c"]
        await self.pass_()
        self.assertEqual([u for u, _, _ in self.launched], ["0002_b"])

    async def test_turned_off_in_the_middle_of_a_pass_starts_nothing(self):
        read = self.service.board
        reading, go_on = asyncio.Event(), asyncio.Event()

        async def slow_board(cwd):
            reading.set()
            await go_on.wait()
            return await read(cwd)

        self.service.board = slow_board
        self.add("0001_a", "spec")
        passing = asyncio.get_running_loop().create_task(self.service._autopilot_pass(self.key))
        await reading.wait()
        self.service.set_autopilot(self.ws, "autopilot", False)
        go_on.set()
        await passing
        await asyncio.sleep(0.05)
        self.assertEqual((self.launched, self.stops()), ([], {}))

    async def test_turned_off_before_a_launch_runs_starts_nothing(self):
        self.add("0001_a", "spec")
        await self.service._autopilot_pass(self.key)
        self.service.set_autopilot(self.ws, "autopilot", False)
        await asyncio.sleep(0.05)
        self.assertEqual(self.launched, [])

    async def test_one_ship_at_a_time_and_only_when_allowed(self):
        self.add("0001_a", "ship")
        self.add("0002_b", "ship")
        await self.pass_()
        self.assertEqual((self.launched, self.stops()), ([], {"0001_a": "c", "0002_b": "c"}))
        self.service.set_autopilot(self.ws, "autopilot_may_ship", True)
        await self.pass_()
        self.assertEqual(self.launched, [("0001_a", "ship", "autopilot")])

    async def test_b_and_d_stop(self):
        self.add("0001_a", "", action="answer F1")
        self.nexts["0001_a"]["waiting"] = ["F1"]
        self.add("0002_b", "review")
        Journal(self.config.working_dir, self.config.data_dir).append({
            "kind": "integration", "workspace": self.key, "unit": "0002_b", "stage": "integrate",
            "outcome": "needs-person", "needs_person": ["x.py on both sides"],
        })
        await self.pass_()
        self.assertEqual(self.launched, [])
        self.assertEqual(self.stops(), {"0001_a": "b", "0002_b": "d"})

    async def test_integrate_when_behind_but_never_after_a_pass(self):
        self.add("0001_a", "", action="CI has not finished on #3: t — wait, then ask again",
                 integration={"state": "behind"}, rounds=[{"verdict": "changes-requested"}], between_pr_and_ship=True)
        self.add("0002_b", "ship", integration={"state": "behind"}, rounds=[{"verdict": "pass"}],
                 between_pr_and_ship=True)
        await self.pass_()
        self.assertEqual(self.launched, [("0001_a", "integrate", "autopilot")])
        self.assertEqual(self.stops(), {"0002_b": "c"})

    async def test_red_after_its_own_integration_is_not_integrated_again(self):
        for unit in ("0001_a", "0002_b"):
            self.add(unit, "impl", integration={"state": "red-after-integration"},
                     rounds=[{"verdict": "changes-requested"}], between_pr_and_ship=True)
        log = Journal(self.config.working_dir, self.config.data_dir)
        for unit, by in (("0001_a", "autopilot"), ("0002_b", "person")):
            log.append({
                "kind": "integration", "workspace": self.key, "unit": unit, "stage": "integrate",
                "outcome": "pushed", "mode": "agent", "started_by": by,
            })
        await self.pass_()
        self.assertEqual(self.launched, [("0002_b", "integrate", "autopilot")])
        self.assertEqual(self.stops(), {"0001_a": "e"})

    async def test_an_integration_before_its_mark_is_counted_against_the_cap(self):
        reading = asyncio.Event()
        self.addCleanup(reading.set)

        async def slow(cwd, unit, started_by="person"):
            # `integrate` fetches and asks `gh` before it takes its mark.
            await reading.wait()
            yield ("done", {})

        self.service.integrate = slow
        need = autopilot.reservation("integrate")
        self.service.set_autopilot(self.ws, "daily_cap_usd", need + autopilot.reservation("spec") / 2)
        self.add("0001_a", "", action="CI has not finished on #3: t — wait, then ask again",
                 integration={"state": "behind"}, rounds=[], between_pr_and_ship=True)
        await self.pass_()
        self.assertNotIn((self.key, "0001_a"), self.service._active)
        self.assertEqual(self.service._autopilot_cap([], 100.0)["running"], need)
        self.add("0002_b", "spec")
        await self.pass_()
        self.assertEqual((self.launched, self.stops()), ([], {"0002_b": "cap"}))

    async def test_an_unknown_cost_is_estimated_not_the_cap_reached(self):
        Journal(self.config.working_dir, self.config.data_dir).finished(self.key, "0009_z", "spec", "done")
        self.add("0001_a", "spec")
        await self.pass_()
        self.assertEqual(self.launched, [("0001_a", "spec", "autopilot")])
        self.assertNotIn("0001_a", self.stops())

    async def test_a_cap_stop_says_the_estimate(self):
        Journal(self.config.working_dir, self.config.data_dir).finished(self.key, "0009_z", "spec", "done")
        self.service.set_autopilot(
            self.ws, "daily_cap_usd", autopilot.estimate("spec") + autopilot.reservation("spec") - 0.01,
        )
        self.add("0001_a", "spec")
        await self.pass_()
        stop = self.service._autopilot_stops[self.key]["0001_a"]
        self.assertEqual((self.launched, stop["kind"]), ([], "cap"))
        self.assertIn("estimated", stop["reason"])
        self.assertNotIn("cost_usd", stop["reason"])
        self.assertNotIn("reached", stop["reason"])

    async def test_the_cap_block_carries_the_estimate(self):
        log = Journal(self.config.working_dir, self.config.data_dir)
        log.finished(self.key, "0009_z", "spec", "done")
        cap = self.service._autopilot_cap(log.records(), 80.0)
        self.assertEqual(set(cap), {"limit", "spent", "known", "estimated", "estimated_count", "running", "day"})
        self.assertEqual(cap["spent"], round(cap["known"] + cap["estimated"], 2))
        self.assertEqual((cap["estimated"], cap["estimated_count"]), (autopilot.estimate("spec"), 1))

    async def test_ci_pending_is_quiet(self):
        self.add("0001_a", "", action="CI has not finished on #3: t — wait, then ask again", between_pr_and_ship=True)
        await self.pass_()
        self.assertEqual((self.launched, self.stops()), ([], {}))

    async def test_a_gate_refusal_is_a_stop_f_with_its_words(self):
        async def refused(cwd, unit, stage, started_by="person"):
            raise Invalid("blocked: plan.md is draft")
            yield  # pragma: no cover

        self.service.run_step = refused
        self.add("0001_a", "impl", plan="- `a/b.py`")
        await self.pass_()
        self.assertEqual(self.service._autopilot_stops[self.key]["0001_a"],
                         {"unit": "0001_a", "kind": "f", "reason": "blocked: plan.md is draft"})

    async def test_off_loopback_nothing_runs_and_the_board_says_why(self):
        self.service.config = dataclasses.replace(self.config, host="0.0.0.0")
        self.add("0001_a", "spec")
        await self.pass_()
        self.assertEqual(self.launched, [])
        self.assertIn("127.0.0.1", self.service._autopilot_stops[self.key][""]["reason"])


class ResumedAtStartUp(unittest.TestCase):
    """R5 c. The Reflex lifespan task, since the real stack never runs `api.py`'s.

    Sending `lifespan.startup` through `served()` compiles the page (about 14 s), so the
    test checks the registration and the call; `impl.md` records the run through `served()`.
    """

    def test_the_app_resumes_the_autopilot_when_it_starts(self):
        # Synchronous: Reflex registers its states in a context an async test's task lacks.
        import coscc.coscc as composed
        from coscc.state import API

        self.assertIn(composed.resume_autopilot, composed.app._lifespan_tasks)
        calls: list[int] = []

        class Stand:
            def autopilot_resume(self):
                calls.append(1)
                return []

        real = API.state.service
        API.state.service = Stand()
        try:
            asyncio.run(composed.resume_autopilot())
        finally:
            API.state.service = real
        self.assertEqual(calls, [1])


if __name__ == "__main__":
    unittest.main()
