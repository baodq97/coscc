"""Tests for `WatchMixin` in `coscc/service_watch.py`, split from `coscc/service_test.py` (`0095`).
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from coscc import events as events_mod
from coscc.config import Config
from coscc.service_common import Invalid
from coscc.service import Service
from coscc.service_test import create_sync


class AStepCanBeWatched(unittest.TestCase):
    """`0073` step 5: `events_page` and `follow_events`, while a step runs and after."""

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
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                recorder = kw["step"].recorder
                for i in range(test.N):
                    recorder.denied("Bash", {"command": f"c{i}"}, "not granted")
                await test.release.wait()
                yield ("chunk", "# Spec: a problem\nAuthor: t. Status: accepted.\n\n## Body\n")
                yield ("done", {"session_id": "sess-73", "cost": {}})

        self.service = Service(
            Config(
                workspaces=(str(self.repo),),
                working_dir=str(self.root / "work"),
                data_dir=str(self.root / "data"),
            ),
            Reports(),
        )
        self.made = create_sync(self.service, str(self.repo), "a-problem", "some words")
        self.unit = self.made["unit"]
        self.other = create_sync(self.service, str(self.repo), "another", "words")["unit"]
        for made in (self.made["path"],):
            (Path(made) / "intent.md").write_text(
                "# Intent: a problem\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
            )

    def test_pages_and_following_while_running_and_after(self):
        ws = str(self.repo)

        async def go():
            self.release = asyncio.Event()
            reader = asyncio.create_task(self._drain())
            while not self.service._recorders or next(iter(self.service._recorders.values())).seq < self.N:
                await asyncio.sleep(0.01)
            [run] = list(self.service._recorders)
            listed = self.service.running(ws)["running"][self.unit][0]["run"]
            steps_run = self.service.running_steps(ws)[0]["run"]
            live = self.service.events_page(ws, self.unit, run)
            older = self.service.events_page(ws, self.unit, run, before=live["first_seq"], limit=9999)
            one = self.service.events_page(ws, self.unit, run, seq=7)
            with self.assertRaises(Invalid):
                self.service.events_page(ws, self.other, run)
            followed: list[int] = []

            async def follow():
                async for kind, batch in self.service.follow_events(ws, self.unit, run, after=100):
                    self.assertEqual(kind, "events")
                    followed.extend(e["seq"] for e in batch)

            follower = asyncio.create_task(follow())
            await asyncio.sleep(0.05)
            self.release.set()
            await reader
            await asyncio.wait_for(follower, 10)
            after = self.service.events_page(ws, self.unit, run)
            after_older = self.service.events_page(ws, self.unit, run, before=live["first_seq"], limit=9999)
            ended = [i async for i in self.service.follow_events(ws, self.unit, run, after=0)]
            return run, listed, steps_run, live, older, one, followed, after, after_older, ended

        run, listed, steps_run, live, older, one, followed, after, after_older, ended = asyncio.run(go())
        self.assertEqual((listed, steps_run), (run, run))
        self.assertEqual(live["status"], "running")
        self.assertEqual([e["seq"] for e in live["events"]], list(range(self.N - 199, self.N + 1)))
        self.assertTrue(live["has_older"])
        self.assertEqual(len(older["events"]), events_mod.PAGE_MAX)
        self.assertEqual(older["events"][-1]["seq"], live["first_seq"] - 1)
        self.assertEqual([e["seq"] for e in one["events"]], [7])
        # Following from 100: every later event once, in order, ending with `end`.
        self.assertEqual(followed, list(range(101, self.N + 2)))
        self.assertEqual(after["status"], "ended")
        self.assertEqual(after["events"][-1]["kind"], "end")
        # R7: the same pages from the table as from memory.
        self.assertEqual(after["events"][:-1], live["events"][1:])
        self.assertEqual(after_older["events"], older["events"])
        self.assertEqual([k for k, _ in ended], ["status"])
        self.assertEqual(ended[0][1]["status"], "ended")
        self.assertEqual(self.service._recorders, {})
        [start] = [r for r in self.service._journal().records(kind="start")]
        [end] = [r for r in self.service._journal().records(kind="end")]
        self.assertEqual((start["run"], end["run"], end["events_lost"]), (run, run, 0))

    async def _drain(self):
        async for _ in self.service.run_step(str(self.repo), self.unit, "spec"):
            pass

    def test_a_run_nobody_knows_is_refused_and_a_step_the_gate_closes_leaves_no_recorder(self):
        with self.assertRaises(Invalid):
            self.service.events_page(str(self.repo), self.unit, "nope")
        with self.assertRaises(Invalid):
            self.service.events_page(str(self.repo), self.unit, "")

        async def refused():
            async for _ in self.service.run_step(str(self.repo), self.unit, "ship"):
                pass

        with self.assertRaises(Invalid):
            asyncio.run(refused())
        self.assertEqual(self.service._recorders, {})

    def test_a_run_the_run_log_names_with_no_index_row_is_none(self):
        journal = self.service._journal()
        key = self.service._journal_key(str(self.repo))
        journal.started(key, self.unit, "spec", "manual", run="r-lost")
        page = self.service.events_page(str(self.repo), self.unit, "r-lost")
        self.assertEqual((page["status"], page["events"]), ("none", []))
