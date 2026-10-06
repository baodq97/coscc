"""Tests for `Watch` in `coscc/runner/watch.py`."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from coscc.bus import Bus
from coscc.runlog import events as events_mod
from coscc.config import Config
from coscc.kernel import Invalid
from coscc.http.app import Core
from tests.http.test_app import create_sync
from tests.units.test_meta import seed
from tests.units.test_submit import submits as _submits


class AStepCanBeWatched(unittest.TestCase):
    """`events_page` and `follow_events`, while a step runs and after."""

    N = 800  # refusals the stand-in session reports before it waits

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "work" / "proj"
        self.repo.mkdir(parents=True)
        self.release = None
        test = self

        class Reports:
            bus = Bus()

            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                recorder = kw["step"].recorder
                for i in range(test.N):
                    recorder.denied("Bash", {"command": f"c{i}"}, "not granted")
                await test.release.wait()
                yield ("chunk", "# Spec: a problem\nAuthor: t. Status: accepted.\n\n## Body\n")
                await _submits(kw)
                yield ("done", {"session_id": "sess-73", "cost": {}})

        self.core = Core(
            Config(
                workspaces=(str(self.repo),),
                working_dir=str(self.root / "work"),
                data_dir=str(self.root / "data"),
            ),
            Reports(),
        )
        self.made = create_sync(self.core, str(self.repo), "a-problem", "some words")
        self.unit = self.made["unit"]
        self.other = create_sync(self.core, str(self.repo), "another", "words")["unit"]
        (Path(self.made["path"]) / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: feat.\n", encoding="utf-8"
        )
        seed(
            self.core.ws.unit_meta(),
            self.core.ws.key(str(self.repo)),
            self.unit,
            statuses={"intent.md": "accepted"},
            type="feat",
        )

    def test_pages_and_following_while_running_and_after(self):
        ws = str(self.repo)

        async def go():
            self.release = asyncio.Event()
            reader = asyncio.create_task(self._drain())
            while (
                not self.core.steps.recorders
                # The `config` event, then the refusals.
                or next(iter(self.core.steps.recorders.values())).seq < self.N + 1
            ):
                await asyncio.sleep(0.01)
            [run] = list(self.core.steps.recorders)
            listed = self.core.boards.running(ws)["running"][self.unit][0]["run"]
            steps_run = self.core.steps.running_steps(ws)[0]["run"]
            live = self.core.watch.events_page(ws, self.unit, run)
            older = self.core.watch.events_page(
                ws, self.unit, run, before=live["first_seq"], limit=9999
            )
            one = self.core.watch.events_page(ws, self.unit, run, seq=7)
            with self.assertRaises(Invalid):
                self.core.watch.events_page(ws, self.other, run)
            followed: list[int] = []

            async def follow():
                async for kind, batch in self.core.watch.follow_events(
                    ws, self.unit, run, after=100
                ):
                    self.assertEqual(kind, "events")
                    followed.extend(e["seq"] for e in batch)

            # The follower is subscribed before the step goes on.
            recorder = self.core.steps.recorders[run]
            others = len(recorder.subscribers)
            follower = asyncio.create_task(follow())
            for _ in range(500):
                if len(recorder.subscribers) > others:
                    break
                await asyncio.sleep(0.01)
            self.release.set()
            await reader
            await asyncio.wait_for(follower, 10)
            after = self.core.watch.events_page(ws, self.unit, run)
            after_older = self.core.watch.events_page(
                ws, self.unit, run, before=live["first_seq"], limit=9999
            )
            ended = [i async for i in self.core.watch.follow_events(ws, self.unit, run, after=0)]
            return run, listed, steps_run, live, older, one, followed, after, after_older, ended

        run, listed, steps_run, live, older, one, followed, after, after_older, ended = asyncio.run(
            go()
        )
        self.assertEqual((listed, steps_run), (run, run))
        self.assertEqual(live["status"], "running")
        self.assertEqual([e["seq"] for e in live["events"]], list(range(self.N - 198, self.N + 2)))
        self.assertTrue(live["has_older"])
        self.assertEqual(len(older["events"]), events_mod.PAGE_MAX)
        self.assertEqual(older["events"][-1]["seq"], live["first_seq"] - 1)
        self.assertEqual([e["seq"] for e in one["events"]], [7])
        # Following from 100: every later event once, in order, ending with `end`.
        self.assertEqual(followed, list(range(101, self.N + 3)))
        self.assertEqual(after["status"], "ended")
        self.assertEqual(after["events"][-1]["kind"], "end")
        # The same pages from the table as from memory.
        self.assertEqual(after["events"][:-1], live["events"][1:])
        self.assertEqual(after_older["events"], older["events"])
        self.assertEqual([k for k, _ in ended], ["status"])
        self.assertEqual(ended[0][1]["status"], "ended")
        self.assertEqual(self.core.steps.recorders, {})
        [start] = [r for r in self.core.ws.journal().records(kind="start")]
        [end] = [r for r in self.core.ws.journal().records(kind="end")]
        self.assertEqual((start["run"], end["run"], end["events_lost"]), (run, run, 0))

    async def _drain(self):
        async for _ in self.core.steps.run_step(str(self.repo), self.unit, "spec"):
            pass

    def test_a_run_nobody_knows_is_refused_and_a_step_the_gate_closes_leaves_no_recorder(self):
        with self.assertRaises(Invalid):
            self.core.watch.events_page(str(self.repo), self.unit, "nope")
        with self.assertRaises(Invalid):
            self.core.watch.events_page(str(self.repo), self.unit, "")

        async def refused():
            async for _ in self.core.steps.run_step(str(self.repo), self.unit, "ship"):
                pass

        with self.assertRaises(Invalid):
            asyncio.run(refused())
        self.assertEqual(self.core.steps.recorders, {})
