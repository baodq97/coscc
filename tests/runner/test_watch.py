"""Tests for `Watch` in `coscc/runner/watch.py`."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from coscc.bus import Bus
from coscc.runlog import events as events_mod
from coscc.config import Config
from coscc.kernel import Invalid
from coscc.http.app import Core
from coscc.runner.run import LIVE
from coscc.store.db import Data
from coscc.agent.sessions import Sessions
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
                not self._live()
                # The `config` event, then the refusals.
                or next(iter(self._live().values())).seq < self.N + 1
            ):
                await asyncio.sleep(0.01)
            [run] = list(self._live())
            listed = self.core.boards.running(ws)["running"][self.unit][0]["run"]
            steps_run = self.core.steps.running_steps(ws)[0]["run"]
            live = self.core.watch.events_page(ws, run)
            older = self.core.watch.events_page(ws, run, before=live["first_seq"], limit=9999)
            one = self.core.watch.events_page(ws, run, seq=7)
            followed: list[int] = []

            async def follow():
                async for kind, batch in self.core.watch.follow_events(ws, run, after=100):
                    self.assertEqual(kind, "events")
                    followed.extend(e["seq"] for e in batch)

            # The follower is subscribed before the step goes on.
            recorder = LIVE[run]
            others = len(recorder.subscribers)
            follower = asyncio.create_task(follow())
            for _ in range(500):
                if len(recorder.subscribers) > others:
                    break
                await asyncio.sleep(0.01)
            self.release.set()
            await reader
            await asyncio.wait_for(follower, 10)
            after = self.core.watch.events_page(ws, run)
            after_older = self.core.watch.events_page(ws, run, before=live["first_seq"], limit=9999)
            ended = [i async for i in self.core.watch.follow_events(ws, run, after=0)]
            return run, listed, steps_run, live, older, one, followed, after, after_older, ended

        run, listed, steps_run, live, older, one, followed, after, after_older, ended = asyncio.run(
            go()
        )
        self.assertEqual((listed, steps_run), (run, run))
        self.assertEqual(
            (live["status"], live["unit"], after["unit"]), ("running", self.unit, self.unit)
        )
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
        self.assertEqual(self._live(), {})
        [start] = [r for r in self.core.ws.journal().records(kind="start")]
        [end] = [r for r in self.core.ws.journal().records(kind="end")]
        self.assertEqual((start["run"], end["run"], end["events_lost"]), (run, run, 0))

    def test_a_follower_of_a_run_abandoned_with_no_end_is_let_go(self):
        ws = str(self.repo)
        key = self.core.ws.key(ws)

        async def go():
            recorder = events_mod.Recorder(
                "r-stopped", Data(self.root / "data"), str(self.root / "work"), key, "", "scan"
            )
            LIVE[recorder.run] = recorder
            recorder.start()
            got = []

            async def follow():
                async for kind, _ in self.core.watch.follow_events(ws, recorder.run):
                    got.append(kind)

            follower = asyncio.create_task(follow())
            await asyncio.sleep(0.05)
            await recorder.abandon()
            LIVE.pop(recorder.run, None)
            await asyncio.wait_for(follower, 5)
            return got

        with mock.patch.object(events_mod, "IDLE_WAKE", 0.05):
            got = asyncio.run(go())
        self.assertEqual(got[-1], "status")

    def _live(self):
        """The recorders of this test's workspace that run now."""
        key = self.core.ws.key(str(self.repo))
        return {run: r for run, r in LIVE.items() if r.workspace == key}

    async def _drain(self):
        async for _ in self.core.steps.run_step(str(self.repo), self.unit, "spec"):
            pass

    def test_an_agent_run_a_press_handed_out_is_running_before_its_start_is_written(self):
        from coscc.runner import triggers

        ws = str(self.repo)
        key = self.core.ws.key(ws)
        with mock.patch.dict(triggers._RUNNING, {(key, "scan"): ("r77", "2026-10-07T00:00:00Z")}):
            page = self.core.watch.events_page(ws, "r77")
            self.assertEqual(
                (page["status"], page["stage"], page["events"]), ("running", "scan", [])
            )

            async def first():
                async for item in self.core.watch.follow_events(ws, "r77"):
                    return item

            # Its recorder opens a moment later: the follower waits for it, then says it is over.
            async def go():
                task = asyncio.create_task(first())
                await asyncio.sleep(0.25)
                triggers._RUNNING.clear()
                with self.assertRaises(Invalid):
                    await task

            asyncio.run(go())
        other = str(self.root / "work" / "other")
        with mock.patch.dict(triggers._RUNNING, {(other, "scan"): ("r78", "t")}):
            with self.assertRaises(Invalid):
                self.core.watch.events_page(ws, "r78")

    def test_a_run_nobody_knows_is_refused_and_a_step_the_gate_closes_leaves_no_recorder(self):
        with self.assertRaises(Invalid):
            self.core.watch.events_page(str(self.repo), "nope")
        with self.assertRaises(Invalid):
            self.core.watch.events_page(str(self.repo), "")

        async def refused():
            async for _ in self.core.steps.run_step(str(self.repo), self.unit, "ship"):
                pass

        with self.assertRaises(Invalid):
            asyncio.run(refused())
        self.assertEqual(self._live(), {})


class ARunsEndIsOnItsFirstPage(unittest.TestCase):
    """A run of no unit that has ended: how it ended, and Dagaz's draft, which only its `end` keeps."""

    def test_the_outcome_and_the_draft_come_from_its_end(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo = root / "work" / "proj"
        repo.mkdir(parents=True)
        config = Config(
            workspaces=(str(repo),), working_dir=str(root / "work"), data_dir=str(root / "data")
        )
        core = Core(config, Sessions(config))
        ws = core.ws.key(str(repo))
        journal = core.ws.journal()
        draft = {"why": "a reader", "process": {"name": "docs", "process": {}}}
        journal.started(ws, "", "dagaz", "manual", run="r9", started_by="person")
        page = core.watch.events_page(str(repo), "r9")
        self.assertEqual(page["started_by"], "person")
        self.assertNotIn("draft", page)
        journal.finished(ws, "", "dagaz", "done", run="r9", detail="", draft=draft)
        page = core.watch.events_page(str(repo), "r9")
        self.assertEqual((page["outcome"], page["draft"]), ("done", draft))
        self.assertNotIn("draft", core.watch.events_page(str(repo), "r9", before=5))


class ASkippedRunHasAPage(unittest.TestCase):
    def test_a_run_with_only_an_end_opens_a_page_not_no_such_run(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo = root / "work" / "proj"
        repo.mkdir(parents=True)
        config = Config(
            workspaces=(str(repo),), working_dir=str(root / "work"), data_dir=str(root / "data")
        )
        core = Core(config, Sessions(config))
        ws = core.ws.key(str(repo))
        core.ws.journal().finished(
            ws, "", "scan", "done", run="r5", skipped=True, started_by="manual"
        )
        page = core.watch.events_page(str(repo), "r5")
        self.assertEqual((page["events"], page["outcome"]), ([], "done"))
        with self.assertRaises(Invalid):
            core.watch.events_page(str(repo), "r6")
