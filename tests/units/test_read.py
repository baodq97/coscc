"""Tests for `Board` in `coscc.units.read.py`, split from `tests/http/test_app.py`."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.bus import Bus
from coscc.config import Config
from coscc.github import integrate
from coscc.kernel import Invalid
from coscc.http.app import Core
from coscc.agent.sessions import Sessions
from coscc.units import scratch
from tests.leif.test_answers import REVIEW_ONE
from tests.http.test_app import create_sync, seed_unit
from tests.github.test_integration import PR, SLUG, StandIn, git
from tests.units.test_submit import submits as _submits


class WhatIsRunningIsKeptWhileItRuns(unittest.TestCase):
    """One unfinished attempt per step while it runs, none however the step ends."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "work" / "proj"
        self.repo.mkdir(parents=True)
        self.seen: list[list[dict]] = []
        test = self

        class Looks:
            bus = Bus()

            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                test.seen.append(test.held())
                yield ("chunk", "# Spec: a problem\nAuthor: t. Status: accepted.\n\n## Body\n")
                await _submits(kw)
                yield ("done", {"session_id": "sess-51", "cost": {}})

        self.core = Core(
            Config(
                workspaces=(str(self.repo),),
                working_dir=str(self.root / "work"),
                data_dir=str(self.root / "data"),
            ),
            Looks(),
        )
        self.made = create_sync(self.core, str(self.repo), "a-problem", "some words")
        self.unit = self.made["unit"]
        (Path(self.made["path"]) / "intent.md").write_text(
            "# Intent: a problem\n", encoding="utf-8"
        )
        seed_unit(
            self.core, str(self.repo), self.unit, statuses={"intent.md": "accepted"}, type="feat"
        )

    def held(self) -> list[dict]:
        """What the board shows as running in this workspace now, one row per attempt."""
        key = self.core.ws.key(str(self.repo))
        running = self.core.boards.running_here(key)
        return [{**row, "unit": unit} for unit, rows in running.items() for row in rows]

    def _run(self, stage: str, stop_after: int | None = None, until_ended: bool = False):
        async def go():
            out = []
            agen = self.core.steps.run_step(str(self.repo), self.unit, stage)
            try:
                async for item in agen:
                    out.append(item)
                    if stop_after is not None and len(out) >= stop_after:
                        break
            finally:
                await agen.aclose()
            if until_ended:
                # The step is its attempt's own task: it goes on when its reader has gone.
                for _ in range(500):
                    if not self.core.attempts.unfinished():
                        break
                    await asyncio.sleep(0.01)
            return out

        return asyncio.run(go())

    def test_one_entry_while_the_step_runs_and_none_after_done(self):
        self._run("spec")
        [[entry]] = self.seen
        self.assertEqual(
            (entry["unit"], entry["stage"], entry["kind"]), (self.unit, "spec", "step")
        )
        self.assertIsNone(entry["turns"])
        self.assertIsNone(entry["cost_usd"])
        self.assertEqual(self.core.attempts.unfinished(), [])

    def test_none_after_a_run_error(self):
        from coscc.runner.reply import RunError

        async def fails(*a, **kw):
            self.seen.append(self.held())
            raise RunError("stand-in")
            yield  # pragma: no cover

        with mock.patch("coscc.runner.step.Runner.run", fails):
            with self.assertRaises(Invalid):
                self._run("spec")
        self.assertEqual(len(self.seen[0]), 1)
        self.assertEqual(self.core.attempts.unfinished(), [])

    def test_none_after_the_caller_goes_away_mid_step(self):
        # (the attempt, not its reader, is the step's life): a reader that goes away takes its queue with it and nothing else, so the attempt
        # runs on to its own end, and then none is left.
        self._run("spec", stop_after=1, until_ended=True)
        self.assertEqual(len(self.seen[0]), 1)
        self.assertEqual(self.core.attempts.unfinished(), [])
        self.assertEqual(self.core.steps.tasks, {})

    def test_a_step_the_gate_refuses_leaves_none(self):
        with self.assertRaises(Invalid):
            self._run("ship")
        self.assertEqual(self.core.attempts.unfinished(), [])

    def test_a_step_refused_as_busy_leaves_none_and_keeps_the_other(self):
        key = self.core.ws.key(str(self.repo))
        attempts = self.core.attempts
        # The unit's own is being prepared; another unit's is running.
        mine = attempts.open("step", key, self.unit, "spec")
        attempts.move(mine["id"], "preparing")
        other = attempts.open("step", key, "0002_other", "spec", state="running")
        with self.assertRaises(Invalid) as caught:
            self._run("spec")
        self.assertIn("a spec step is being prepared", str(caught.exception))
        self.assertEqual([r["id"] for r in attempts.unfinished()], [mine["id"], other["id"]])


class RunningAnswersFromMemoryAndTheRunLog(unittest.TestCase):
    """`running` and `unknown_end`, and what keeps them apart."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.repo = root / "work" / "proj"
        self.other = root / "work" / "other"
        self.repo.mkdir(parents=True)
        self.other.mkdir(parents=True)
        config = Config(
            workspaces=(str(self.repo), str(self.other)),
            working_dir=str(root / "work"),
            data_dir=str(root / "data"),
        )
        self.core = Core(config, Sessions(config))
        self.cwd = str(self.repo)
        self.key = self.core.ws.key(self.cwd)
        self.journal = self.core.ws.journal()

    def test_an_entry_and_its_own_start_show_only_as_running(self):
        self.core.attempts.open("step", self.key, "0009_x", "impl", state="running")
        self.journal.started(self.key, "0009_x", "impl", "manual")
        got = self.core.boards.running(self.cwd)
        [row] = got["running"]["0009_x"]
        self.assertEqual(row["stage"], "impl")
        self.assertEqual(row["agent"], {"glyph": "ᚢ", "name": "Uruz"})
        self.assertEqual(row["kind"], "step")
        self.assertIsNone(row["turns"])
        self.assertIsNone(row["cost_usd"])
        self.assertEqual(got["unknown_end"], {})

    def test_an_orphan_start_shows_as_ended_unknown(self):
        rec = self.journal.started(self.key, "0009_x", "plan", "manual")
        got = self.core.boards.running(self.cwd)
        self.assertEqual(got["running"], {})
        # A start with no `agent` is named from its stage.
        self.assertEqual(
            got["unknown_end"],
            {"0009_x": [{"stage": "plan", "started": rec["at"], "agent": "Raidho"}]},
        )

    def test_a_later_start_retires_the_orphan(self):
        self.journal.append(
            {
                "kind": "start",
                "workspace": self.key,
                "unit": "0009_x",
                "stage": "plan",
                "mode": "manual",
                "at": "2026-09-24T01:00:00+00:00",
            }
        )
        self.journal.started(self.key, "0009_x", "impl", "manual")
        self.journal.finished(self.key, "0009_x", "impl", "done")
        self.assertEqual(self.core.boards.running(self.cwd)["unknown_end"], {})

    def test_an_orphan_older_than_a_day_is_not_shown(self):
        self.journal.append(
            {
                "kind": "start",
                "workspace": self.key,
                "unit": "0009_x",
                "stage": "plan",
                "mode": "manual",
                "at": "2020-01-01T00:00:00+00:00",
            }
        )
        self.assertEqual(self.core.boards.running(self.cwd)["unknown_end"], {})

    def test_two_workspaces_do_not_mix(self):
        other_key = self.core.ws.key(str(self.other))
        self.core.attempts.open("step", other_key, "0009_x", "spec", state="running")
        self.journal.started(other_key, "0010_y", "spec", "manual")
        self.assertEqual(self.core.boards.running(self.cwd), {"running": {}, "unknown_end": {}})
        got = self.core.boards.running(str(self.other))
        self.assertEqual(list(got["running"]), ["0009_x"])
        self.assertEqual(list(got["unknown_end"]), ["0010_y"])

    def test_gebo_is_named_and_a_rebase_is_not(self):
        gebo = self.core.attempts.open(
            "integration", self.key, "0009_x", "integrate", state="running"
        )
        self.core.attempts.set_road(gebo["id"], "gebo")
        self.core.attempts.open("integration", self.key, "0010_y", "integrate", state="running")
        got = self.core.boards.running(self.cwd)["running"]
        self.assertEqual(got["0009_x"][0]["agent"], {"glyph": "ᚷ", "name": "Gebo"})
        self.assertIsNone(got["0010_y"][0]["agent"])
        self.assertEqual(got["0010_y"][0]["kind"], "rebase")

    def test_a_workspace_outside_the_list_is_refused(self):
        with self.assertRaises(Invalid):
            self.core.boards.running("/nonexistent/elsewhere")

    def test_a_busy_run_log_is_a_note_not_a_refusal(self):
        from coscc.store.journal import Journal
        from coscc.store.db import Busy

        self.core.attempts.open("step", self.key, "0009_x", "impl", state="running")
        with mock.patch.object(Journal, "open_starts", side_effect=Busy("locked")):
            got = self.core.boards.running(self.cwd)
        self.assertEqual(got["note"], "locked")
        self.assertEqual(list(got["running"]), ["0009_x"])
        self.assertEqual(got["unknown_end"], {})

    def test_no_working_folder_means_no_unknown_end(self):
        config = Config(workspaces=(self.cwd,), data_dir=str(Path(self._tmp.name) / "bare"))
        core = Core(config, Sessions(config))
        self.assertEqual(core.boards.running(self.cwd), {"running": {}, "unknown_end": {}})


class TheGuide(unittest.TestCase):
    """`guide_block` on memory."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        (root / "work" / "proj").mkdir(parents=True)
        config = Config(
            workspaces=(str(root / "work" / "proj"),),
            working_dir=str(root / "work"),
            data_dir=str(root / "data"),
        )
        self.core = Core(config, Sessions(config))
        self.cwd = str(root / "work" / "proj")
        self.key = self.core.ws.key(self.cwd)
        self.journal = self.core.ws.journal()

    def block(self, units=()) -> dict:
        with mock.patch("coscc.leif.autopilot.autopilot_values", return_value={"autopilot": True}):
            return self.core.autopilot.guide_block(self.key, units)

    def test_guide_lists_running_steps(self):
        self.core.attempts.open("step", self.key, "0009_x", "impl", state="running")
        self.core.attempts.open("step", self.key, "0010_y", "spec", state="running")
        self.core.attempts.open("step", "/elsewhere", "0011_z", "spec", state="running")
        got = self.block()["running"]
        self.assertEqual(
            [(r["unit"], r["stage"], r["agent"]) for r in got],
            [("0009_x", "impl", "Uruz"), ("0010_y", "spec", "Kenaz")],
        )
        self.assertTrue(all(r["started"] for r in got))

    def test_guide_turns_every_stop_but_full_into_one_thing_to_do(self):
        kinds = ("a", "b", "c", "d", "e", "f", "cap", "reruns", "full")
        self.core.autopilot.stops[self.key] = {
            f"00{n:02d}_u": {"unit": f"00{n:02d}_u", "kind": k, "reason": f"why {k}"}
            for n, k in enumerate(kinds, 1)
        }
        self.core.autopilot.stops[self.key][""] = {
            "unit": "",
            "kind": "shortlist",
            "reason": "none",
        }
        self.core.autopilot.stops[self.key]["0099_w"] = {
            "unit": "",
            "kind": "cap",
            "reason": "spent",
        }
        asking = {"name": "0001_u", "state": {"state": "needs-you"}}
        block = self.block([asking, {"name": "0002_u", "state": {"state": "ready"}}])
        self.assertEqual([r["kind"] for r in block["needs_you"]], ["a"])
        # The rest are held back: their cards do not say `Needs you`.
        self.assertEqual([r["kind"] for r in block["held"]], list(kinds[1:-1]))
        # The workspace's own stops; the empty shortlist is said outside the lists.
        self.assertEqual([r["kind"] for r in block["notes"]], ["cap"])
        a = block["needs_you"][0]
        self.assertEqual(
            (a["unit"], a["screen"], a["tab"], a["reason"]),
            ("0001_u", "unit", "questions", "why a"),
        )
        self.assertEqual(block["notes"][0]["screen"], "settings")
        self.assertTrue(block["shortlist_empty"])

    def test_a_needs_you_unit_with_a_step_running_is_counted_as_running(self):
        """The card lays `Running` over `Needs you`; the guide counts what the card says."""
        self.core.attempts.open("step", self.key, "0001_u", "impl", state="running")
        got = self.block([{"name": "0001_u", "state": {"state": "needs-you"}}])
        self.assertEqual((got["needs_you"], len(got["running"])), ([], 1))


class AUnitThatEndedLosesItsScratch(unittest.TestCase):
    """A board read removes the two scratch directories of a `finished` or `rejected` unit."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name).resolve()
        (root / "tmp").mkdir()
        patch = mock.patch.object(tempfile, "tempdir", str(root / "tmp"))
        patch.start()
        self.addCleanup(patch.stop)
        self.repo = root / "work" / "proj"
        self.repo.mkdir(parents=True)
        self.data = str(root / "data")
        config = Config(
            workspaces=(str(self.repo),), working_dir=str(root / "work"), data_dir=self.data
        )
        self.core = Core(config, Sessions(config))

    def made(self, unit: str) -> tuple[Path, Path]:
        ram, disk = scratch.ensure(self.repo, unit, self.data)
        (disk / "big").write_text("x")
        return ram, disk

    def read(self, rows: list[dict]) -> None:
        asyncio.run(self.core.boards._attach_worktrees(str(self.repo), rows))

    def test_finished_and_rejected_lose_both_and_a_running_unit_keeps_them(self):
        places = {
            "0001_done": self.made("0001_done"),
            "0002_no": self.made("0002_no"),
            "0003_live": self.made("0003_live"),
        }
        self.read(
            [
                {"name": "0001_done", "why": "finished"},
                {"name": "0002_no", "why": "rejected"},
                {"name": "0003_live", "why": "running"},
            ]
        )
        for unit in ("0001_done", "0002_no"):
            for where in places[unit]:
                self.assertFalse(where.exists(), where)
        for where in places["0003_live"]:
            self.assertTrue(where.exists(), where)
        # A second read of a unit already cleaned is not an error.
        self.read([{"name": "0001_done", "why": "finished"}])


class TheBoardIsHeld(unittest.IsolatedAsyncioTestCase):
    """A board read once is answered from memory, one read per workspace runs at a time, and no
    read waits on `gh`."""

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.workspace = root / "work" / "proj"
        self.workspace.mkdir(parents=True)
        git(self.workspace, "init", "-q", "-b", "main")
        git(self.workspace, "commit", "-q", "--allow-empty", "-m", "seed")
        self.cwd = str(self.workspace)
        config = Config(
            workspaces=(self.cwd,), working_dir=str(root / "work"), data_dir=str(root / "data")
        )
        self.core = Core(config, StandIn(None))
        self.key = self.core.ws.key(self.cwd)
        made = await self.core.answers.create_unit(self.cwd, SLUG, "fixture")
        self.unit, directory = made["unit"], Path(made["path"])
        for name in ("intent.md", "spec.md", "plan.md", "impl.md"):
            (directory / name).write_text("# X: fixture\n", encoding="utf-8")
        (directory / "pr.md").write_text(
            f"# PR: fixture\nPR: https://github.com/o/r/pull/{PR}.\n", encoding="utf-8"
        )
        seed_unit(
            self.core,
            self.cwd,
            self.unit,
            statuses=dict.fromkeys(
                ("intent.md", "spec.md", "plan.md", "impl.md", "pr.md"), "accepted"
            ),
            type="feat",
        )
        (directory / "review.md").write_text(REVIEW_ONE, encoding="utf-8")
        self.core.ws.unit_meta().history.record(
            self.key, self.unit, "pr.md", "accepted", source="prmachine:opened",
            guard="branch-named", authority="code",
            inputs={"number": PR, "url": f"https://github.com/o/r/pull/{PR}", "head": "a" * 40},
        )  # fmt: skip
        # Set, `gh` hangs until `released` is: a read that waited on it would never return.
        self.hang = False
        self.released = asyncio.Event()
        self.calls: list[list[str]] = []
        patch = mock.patch.object(integrate, "_gh", self._gh)
        patch.start()
        self.addCleanup(patch.stop)

    async def asyncTearDown(self):
        await self.core.shutdown()

    async def _gh(self, argv, cwd):
        self.calls.append(argv[:2])
        if self.hang:
            await self.released.wait()
        if argv[:2] == ["pr", "list"]:
            row = {"number": PR, "headRefOid": "a" * 40, "headRefName": f"feat/{SLUG}"}
            return 0, json.dumps([{**row, "mergeable": "MERGEABLE"}]), ""
        return 0, "[]", ""

    def counted(self) -> list[str]:
        """Every `Board.read` from now on, by cwd."""
        reads: list[str] = []
        real = self.core.boards.read

        async def read(cwd, fresh=False):
            reads.append(cwd)
            return await real(cwd, fresh)

        self.core.boards.read = read
        return reads

    async def ended(self) -> None:
        """Every board read and CI ask running now ended, and the reads an answer starts."""
        while running := [
            *self.core.boards.reads.values(),
            *self.core.integration.ci_asks.values(),
        ]:
            await asyncio.gather(*running)
            for _ in range(5):
                await asyncio.sleep(0)

    async def test_a_gh_that_hangs_holds_neither_the_held_board_nor_a_read(self):
        first = await self.core.board(self.cwd)
        self.assertEqual(first["units"][0]["integration"]["pr_head"], "a" * 40)
        self.hang = True
        held = await self.core.board(self.cwd, "held")
        self.assertEqual(held["read_at"], first["read_at"])
        # The read it started takes the held list and asks `gh` again in the background.
        await asyncio.wait_for(self.ended(), 5)
        self.assertEqual(
            self.core.boards.held[self.key]["data"]["units"][0]["integration"]["pr_head"],
            "a" * 40,
        )
        self.assertEqual(len(self.core.boards.prs.asks), 1)

    async def test_two_asks_while_a_read_runs_start_one_read(self):
        await self.core.board(self.cwd)
        await self.ended()
        reads = self.counted()
        await asyncio.gather(self.core.board(self.cwd, "held"), self.core.board(self.cwd, "held"))
        await self.ended()
        self.assertEqual(reads, [self.cwd])

    async def test_a_new_ask_while_a_read_runs_reads_once_more_after_it(self):
        await self.core.board(self.cwd)
        await self.ended()
        reads = self.counted()
        await self.core.board(self.cwd, "held")
        await self.core.board(self.cwd)
        self.assertEqual(reads, [self.cwd, self.cwd])

    async def test_a_change_the_app_makes_starts_a_read_and_next_waits_for_it(self):
        await self.core.board(self.cwd)
        await self.ended()
        reads = self.counted()
        waiting = asyncio.ensure_future(self.core.board(self.cwd, "next"))
        await asyncio.sleep(0)
        self.assertFalse(waiting.done())
        self.assertEqual(reads, [])
        await self.core.steps.set_mode(self.cwd, self.unit, "impl", "autonomous")
        await asyncio.wait_for(waiting, 5)
        self.assertEqual(reads, [self.cwd])

    async def test_a_change_before_any_read_starts_none(self):
        reads = self.counted()
        self.core.bus.publish("answer.written", {"workspace": self.key, "unit": self.unit})
        self.assertEqual((reads, self.core.boards.reads), ([], {}))


class TheUnitPageCarriesItsOutputs(unittest.TestCase):
    def test_what_each_agent_handed_back_reaches_the_page(self):
        from coscc.units.read import detail

        outputs = [
            {"agent": "spec", "version": 1, "at": "t", "fields": {"judgement": "ready"}},
        ]
        unit = {
            "name": "0001_x",
            "number": 1,
            "slug": "x",
            "state": {"state": "ready", "label": "Ready", "color": "gray"},
        }
        got = detail(unit, [], outputs)
        self.assertEqual(got["outputs"], outputs)

    def test_the_page_carries_the_persons_words_of_the_brief(self):
        from coscc.units.read import detail

        unit = {
            "name": "0001_x",
            "number": 1,
            "slug": "x",
            "state": {"state": "a", "label": "A", "color": "gray"},
        }
        idea = "# Idea: x\nAuthor: the originator.\n\n## In their own words\n\nMake it faster.\n"
        self.assertEqual(detail(unit, [], [], brief=idea)["brief"], "Make it faster.")
        self.assertEqual(detail(unit, [], [])["brief"], "")


class TheUnitPageCarriesItsDecisions(unittest.TestCase):
    UNIT = {
        "name": "0001_x",
        "number": 1,
        "slug": "x",
        "state": {"state": "ready", "label": "Ready", "color": "gray"},
    }

    def test_each_decision_reads_as_a_sentence_oldest_first(self):
        from coscc.units.read import detail

        rows = [
            {
                "kind": "rerun",
                "fields": {"stage": "spec", "stale": {}},
                "by": "owner",
                "date": "d1",
            },
            {"kind": "more-rounds", "fields": {"rounds": 1}, "by": "owner", "date": "d2"},
            {"kind": "outcome", "fields": {"result": "met"}, "by": "Leif", "date": "d3"},
        ]
        got = detail(self.UNIT, [], [], rows)
        self.assertEqual(
            got["decisions"],
            [
                {"kind": "rerun", "by": "owner", "date": "d1", "text": "asked spec to run again"},
                {
                    "kind": "more-rounds",
                    "by": "owner",
                    "date": "d2",
                    "text": "allowed one more review round",
                },
                {
                    "kind": "outcome",
                    "by": "Leif",
                    "date": "d3",
                    "text": "recorded the outcome: met",
                },
            ],
        )

    def test_a_unit_with_none_has_an_empty_list(self):
        from coscc.units.read import detail

        self.assertEqual(detail(self.UNIT, [], [])["decisions"], [])


class AUnitPageShowsItsRuns(unittest.TestCase):
    def test_a_run_whose_cost_was_never_reported_says_unknown_not_zero(self):
        from coscc.units.read import detail

        unit = {
            "name": "0001_x",
            "number": 1,
            "slug": "x",
            "state": {"state": "ready", "label": "Ready", "color": "gray"},
            "questions": [{"artifact": "spec.md", "n": 1, "text": "Which?", "answered": True}],
            "answers": [
                {
                    "artifact": "spec.md",
                    "n": 1,
                    "text": "This one",
                    "by": "delegated",
                    "name": "Leif",
                }
            ],
            "worktree": {"branch": "fix/x", "path": "/w/x", "prepare": None},
        }
        timeline = [
            {
                "stage": "spec",
                "started": "t0",
                "ended": "t1",
                "outcome": "done",
                "cost": {"cost_usd": 0.0, "turns": 3},
                "reported": False,
                "turns_reported": True,
            },
            {"stage": "plan", "started": "t2", "ended": None, "outcome": None, "cost": {}},
        ]
        got = detail(unit, timeline, [])
        self.assertEqual((got["runs"][0]["cost_usd"], got["runs"][0]["turns"]), (None, 3))
        self.assertEqual(got["runs"][1]["ended"], "")
        self.assertEqual(
            (got["answers"][0]["by"], got["answers"][0]["name"]), ("delegated", "Leif")
        )
        self.assertEqual(got["worktree"], {"branch": "fix/x", "path": "/w/x"})
