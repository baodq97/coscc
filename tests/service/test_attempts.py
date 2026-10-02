"""The attempt machine (0150): one move function, one row and one event per move, a scheduler
that never holds more than N slots, `unit-busy` the only refusal, and what a restart leaves."""

from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from unittest import mock

from coscc.bus import Bus
from coscc.service.attempts import Attempts, Illegal
from coscc.service.common import Refused


class _Store(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.bus = Bus()
        self.events: list[str] = []
        self.attempts = Attempts(
            self._tmp.name, self.bus, lambda _ws, slot: 2 if slot == "agent" else 1
        )
        for machine, states in (
            (
                "step",
                ("queued", "preparing", "running", "ending", "ended", "refused", "stop-asked"),
            ),
            ("integration", ("queued", "running", "ending", "ended", "refused", "stop-asked")),
        ):
            for state in states:
                self.bus.subscribe(
                    f"{machine}.{state}", lambda e, n=f"{machine}.{state}": self.events.append(n)
                )

    def rows(self, attempt: int) -> int:
        return len(self.attempts.moves(attempt))


class EveryMoveIsOneRowAndOneEvent(_Store):
    def test_a_legal_move_writes_one_row_and_publishes_one_event(self):
        a = self.attempts.open("step", "/w", "0001_a", "spec")["id"]
        self.assertEqual((self.rows(a), self.events), (1, ["step.queued"]))
        for n, to in enumerate(("preparing", "running", "ending"), start=2):
            self.attempts.move(a, to)
            self.assertEqual(
                (self.rows(a), self.events[-1], len(self.events)), (n, f"step.{to}", n)
            )
        self.attempts.move(a, "ended", "done")
        self.assertEqual((self.rows(a), self.events[-1]), (5, "step.ended"))
        self.assertEqual(self.attempts.get(a)["outcome"], "done")

    def test_an_illegal_move_writes_nothing_and_publishes_nothing(self):
        a = self.attempts.open("integration", "/w", "0001_a", "integrate")["id"]
        before = list(self.events)
        for to, outcome in (("preparing", ""), ("ending", ""), ("ended", ""), ("nowhere", "")):
            with self.assertRaises(Illegal):
                self.attempts.move(a, to, outcome)
        self.assertEqual((self.rows(a), self.events), (1, before))
        self.attempts.move(a, "ended", "stopped")
        with self.assertRaises(Illegal):
            self.attempts.move(a, "running")
        self.assertEqual(self.rows(a), 2)

    def test_unit_busy_is_the_only_refusal_and_writes_nothing(self):
        held = self.attempts.open("step", "/w", "0001_a", "spec")
        with self.assertRaises(Refused) as caught:
            self.attempts.open("integration", "/w", "0001_a", "integrate")
        self.assertEqual(caught.exception.reasons, ("unit-busy",))
        self.assertIn("a spec step is queued since", str(caught.exception))
        self.assertEqual([r["id"] for r in self.attempts.unfinished()], [held["id"]])
        # Another unit, or the same unit once the first ended, is not refused.
        self.attempts.open("step", "/w", "0002_b", "spec")
        self.attempts.move(held["id"], "ended", "stopped")
        self.attempts.open("step", "/w", "0001_a", "plan")

    def test_a_stop_is_a_column_recorded_once_with_the_first_name(self):
        a = self.attempts.open("step", "/w", "0001_a", "spec")["id"]
        self.attempts.ask_stop(a, "Lan")
        self.attempts.ask_stop(a, "Minh")
        row = self.attempts.get(a)
        self.assertEqual((row["state"], row["stop_asked_by"]), ("queued", "Lan"))
        self.assertEqual(self.events.count("step.stop-asked"), 1)
        self.assertEqual(self.rows(a), 1)


class TheSchedulerNeverHoldsMoreThanNSlots(_Store):
    def test_n_plus_three_clicks_hold_n_and_a_freed_slot_is_taken_at_once(self):
        launched: list[int] = []
        self.attempts.launchers["step"] = lambda row: launched.append(row["id"])
        self.attempts.launchers["integration"] = lambda row: launched.append(row["id"])

        def holding(slot: str) -> int:
            return sum(
                1
                for r in self.attempts.unfinished("/w")
                if r["slot"] == slot and r["state"] in ("preparing", "running", "ending")
            )

        async def go():
            ids = [self.attempts.open("step", "/w", f"000{i}_u", "spec")["id"] for i in range(5)]
            heavy = [
                self.attempts.open("integration", "/w", f"001{i}_i", "integrate")["id"]
                for i in range(4)
            ]
            self.assertEqual((holding("agent"), holding("heavy")), (2, 1))
            self.assertEqual(launched, [ids[0], ids[1], heavy[0]])
            # The oldest queued of that kind takes the slot before `move` returns.
            t0 = time.monotonic()
            self.attempts.move(ids[0], "ended", "done")
            self.assertLess(time.monotonic() - t0, 1.0)
            self.assertEqual(self.attempts.get(ids[2])["state"], "preparing")
            self.attempts.move(heavy[0], "ended", "done")
            self.assertEqual(self.attempts.get(heavy[1])["state"], "running")
            self.assertEqual((holding("agent"), holding("heavy")), (2, 1))
            # A stop at `queued` frees nothing and takes nothing.
            self.attempts.move(ids[4], "ended", "stopped")
            self.assertEqual(self.attempts.get(ids[3])["state"], "queued")

        asyncio.run(go())


class AStepIsStoppedAtEveryState(unittest.IsolatedAsyncioTestCase):
    """Through `Steps.stop_running`, on attempts the scheduler does not launch."""

    async def asyncSetUp(self):
        from coscc.api import build
        from coscc.config import Config

        self._tmp = tempfile.TemporaryDirectory()
        self.service = build(Config(workspaces=(), data_dir=self._tmp.name)).state.service
        self.attempts = self.service.attempts

    async def asyncTearDown(self):
        self._tmp.cleanup()

    async def test_queued_ends_stopped_at_once_and_ending_ends_stop_late(self):
        # No free slot, so the click stays queued and nothing is launched.
        self.attempts.capacity = lambda _ws, _slot: 0
        queued = self.attempts.open("step", "/w", "0001_a", "spec")["id"]
        self.assertEqual(self.attempts.get(queued)["state"], "queued")
        await self.service.steps.stop_running("/w", "0001_a", "Lan")
        self.assertEqual(
            (self.attempts.get(queued)["state"], self.attempts.get(queued)["outcome"]),
            ("ended", "stopped"),
        )
        ending = self.attempts.open("step", "/w", "0002_b", "spec", state="running")["id"]
        self.attempts.move(ending, "ending")
        said = await self.service.steps.stop_running("/w", "0002_b", "Lan")
        self.assertEqual(said["stopped_by"], "Lan")
        self.assertEqual(self.attempts.get(ending)["state"], "ending")
        self.service.steps.end_attempt(ending, "done")
        self.assertEqual(self.attempts.get(ending)["outcome"], "stop_late")


class ARestartEndsWhatItCannotGoOnWith(unittest.IsolatedAsyncioTestCase):
    """what each unfinished state comes to at the next start."""

    async def test_each_state_comes_to_its_end(self):
        from coscc.api import build
        from coscc.config import Config
        from coscc.units import worktrees

        with tempfile.TemporaryDirectory() as tmp:
            service = build(Config(workspaces=(), data_dir=tmp)).state.service
            a = service.attempts
            # What a process that went down left: rows only, no task of this one.
            queued = a.open("hold", "/w", "0001_q")["id"]  # a queued with no slot to wake
            preparing = a.open("step", "/w", "0002_p", "spec", state="running")["id"]
            running = a.open("step", "/w", "0003_r", "spec", state="running")["id"]
            integrating = a.open("integration", "/w", "0004_i", "integrate", state="running")["id"]
            ending = a.open("step", "/w", "0005_e", "spec", state="running")["id"]
            a.move(ending, "ending")
            stopping = a.open("step", "/w", "0006_s", "spec", state="running")["id"]
            a.ask_stop(stopping, "Lan")
            # `preparing` is reached only from `queued`; written as the last process would have.
            with a.data.write() as conn:
                conn.execute(
                    "UPDATE attempt_moves SET moved_to = 'preparing' WHERE attempt = ?",
                    (preparing,),
                )
            service.steps.tasks.clear()
            discarded = []

            async def discard(ws, unit, data_dir=None):
                discarded.append(unit)
                return True

            with mock.patch.object(worktrees, "discard_half", discard):
                await service.resume.resume_after_update()
            got = {
                i: (a.get(i)["state"], a.get(i)["outcome"])
                for i in (queued, preparing, running, integrating, ending, stopping)
            }
            self.assertEqual(got[queued], ("queued", ""))
            self.assertEqual(got[preparing], ("ended", "interrupted"))
            self.assertEqual(discarded, ["0002_p"])
            # No suspend row took them up, so Resume did not: interrupted.
            self.assertEqual(got[running], ("ended", "interrupted"))
            self.assertEqual(got[integrating], ("ended", "interrupted"))
            self.assertEqual(got[ending], ("ended", "interrupted"))
            self.assertEqual(got[stopping], ("ended", "stopped"))
            # Every unfinished attempt left is queued: none holds a slot with no task.
            self.assertEqual([r["state"] for r in a.unfinished()], ["queued"])
