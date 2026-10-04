"""Tests for `Board` in `coscc/service/board.py`, split from `tests/service/test_service.py`."""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from coscc.bus import Bus, Event
from coscc.config import Config
from coscc.github import integrate
from coscc.service.common import Invalid
from coscc.service import Service
from coscc.agent.sessions import Sessions
from coscc.units import scratch
from tests.service.test_answers import REVIEW_ONE
from tests.service.test_service import create_sync
from tests.service.test_steps_integrate import PR, SLUG, StandIn, git
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

        self.service = Service(
            Config(
                workspaces=(str(self.repo),),
                working_dir=str(self.root / "work"),
                data_dir=str(self.root / "data"),
            ),
            Looks(),
        )
        self.made = create_sync(self.service, str(self.repo), "a-problem", "some words")
        self.unit = self.made["unit"]
        (Path(self.made["path"]) / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
        )

    def held(self) -> list[dict]:
        """What the board shows as running in this workspace now, one row per attempt."""
        key = self.service.ws.key(str(self.repo))
        running = self.service.boards.running_here(key, {})
        return [{**row, "unit": unit} for unit, rows in running.items() for row in rows]

    def _run(self, stage: str, stop_after: int | None = None, until_ended: bool = False):
        async def go():
            out = []
            agen = self.service.steps.run_step(str(self.repo), self.unit, stage)
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
                    if not self.service.attempts.unfinished():
                        break
                    await asyncio.sleep(0.01)
            return out

        return asyncio.run(go())

    def test_every_stage_column_carries_its_glyph_and_label(self):
        # Every stage the board read names, from the table, overrides included.
        self.service.agents.set_agent("review", {"name": "Judge"})
        data = asyncio.run(self.service.board(str(self.repo)))
        self.assertEqual(set(data["stage_agents"]), set(data["stages"]) - {"pr", "ship"})
        self.assertEqual(
            data["stage_agents"]["plan"],
            {
                "glyph": "ᚱ",
                "label": "Raidho (agent, plan)",
                "meaning": "journey: the right road in the right order",
                "role": "Orders the work and names its proof, and writes no code.",
            },
        )
        self.assertEqual(data["stage_agents"]["review"]["label"], "Judge (agent, review)")
        self.assertEqual(data["stage_agents"]["spike"]["meaning"], "")

    def test_one_entry_while_the_step_runs_and_none_after_done(self):
        self._run("spec")
        [[entry]] = self.seen
        self.assertEqual(
            (entry["unit"], entry["stage"], entry["kind"]), (self.unit, "spec", "step")
        )
        self.assertIsNone(entry["turns"])
        self.assertIsNone(entry["cost_usd"])
        self.assertEqual(self.service.attempts.unfinished(), [])

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
        self.assertEqual(self.service.attempts.unfinished(), [])

    def test_none_after_the_caller_goes_away_mid_step(self):
        # (the attempt, not its reader, is the step's life): a reader that goes away takes its queue with it and nothing else, so the attempt
        # runs on to its own end, and then none is left.
        self._run("spec", stop_after=1, until_ended=True)
        self.assertEqual(len(self.seen[0]), 1)
        self.assertEqual(self.service.attempts.unfinished(), [])
        self.assertEqual(self.service.steps.tasks, {})

    def test_a_step_the_gate_refuses_leaves_none(self):
        with self.assertRaises(Invalid):
            self._run("ship")
        self.assertEqual(self.service.attempts.unfinished(), [])

    def test_a_step_refused_as_busy_leaves_none_and_keeps_the_other(self):
        key = self.service.ws.key(str(self.repo))
        attempts = self.service.attempts
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
        self.service = Service(config, Sessions(config))
        self.cwd = str(self.repo)
        self.key = self.service.ws.key(self.cwd)
        self.journal = self.service.ws.journal()

    def test_an_entry_and_its_own_start_show_only_as_running(self):
        self.service.attempts.open("step", self.key, "0009_x", "impl", state="running")
        self.journal.started(self.key, "0009_x", "impl", "manual")
        got = self.service.boards.running(self.cwd)
        [row] = got["running"]["0009_x"]
        self.assertEqual(row["stage"], "impl")
        self.assertEqual(row["agent"], {"glyph": "ᚢ", "name": "Uruz"})
        self.assertEqual(row["kind"], "step")
        self.assertIsNone(row["turns"])
        self.assertIsNone(row["cost_usd"])
        self.assertEqual(got["unknown_end"], {})

    def test_an_orphan_start_shows_as_ended_unknown(self):
        rec = self.journal.started(self.key, "0009_x", "plan", "manual")
        got = self.service.boards.running(self.cwd)
        self.assertEqual(got["running"], {})
        # A start with no `agent` is named from its stage.
        self.assertEqual(
            got["unknown_end"],
            {"0009_x": [{"stage": "plan", "started": rec["at"], "agent": "Raidho"}]},
        )

    def test_an_orphan_start_keeps_the_name_it_was_written_with(self):
        self.journal.started(self.key, "0009_x", "plan", "manual", agent="Wayfarer")
        [row] = self.service.boards.running(self.cwd)["unknown_end"]["0009_x"]
        self.assertEqual(row["agent"], "Wayfarer")

    def test_an_override_reaches_the_running_line(self):
        # The running line reads the one lookup, overrides included.
        self.service.agents.set_agent("impl", {"name": "Builder", "glyph": "ᛒ"})
        self.service.attempts.open("step", self.key, "0009_x", "impl", state="running")
        [row] = self.service.boards.running(self.cwd)["running"]["0009_x"]
        self.assertEqual(row["agent"], {"glyph": "ᛒ", "name": "Builder"})

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
        self.assertEqual(self.service.boards.running(self.cwd)["unknown_end"], {})

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
        self.assertEqual(self.service.boards.running(self.cwd)["unknown_end"], {})

    def test_two_workspaces_do_not_mix(self):
        other_key = self.service.ws.key(str(self.other))
        self.service.attempts.open("step", other_key, "0009_x", "spec", state="running")
        self.journal.started(other_key, "0010_y", "spec", "manual")
        self.assertEqual(self.service.boards.running(self.cwd), {"running": {}, "unknown_end": {}})
        got = self.service.boards.running(str(self.other))
        self.assertEqual(list(got["running"]), ["0009_x"])
        self.assertEqual(list(got["unknown_end"]), ["0010_y"])

    def test_gebo_is_named_and_a_rebase_is_not(self):
        gebo = self.service.attempts.open(
            "integration", self.key, "0009_x", "integrate", state="running"
        )
        self.service.attempts.set_road(gebo["id"], "gebo")
        self.service.attempts.open("integration", self.key, "0010_y", "integrate", state="running")
        got = self.service.boards.running(self.cwd)["running"]
        self.assertEqual(got["0009_x"][0]["agent"], {"glyph": "ᚷ", "name": "Gebo"})
        self.assertIsNone(got["0010_y"][0]["agent"])
        self.assertEqual(got["0010_y"][0]["kind"], "rebase")

    def test_a_workspace_outside_the_list_is_refused(self):
        with self.assertRaises(Invalid):
            self.service.boards.running("/nonexistent/elsewhere")

    def test_a_busy_run_log_is_a_note_not_a_refusal(self):
        from coscc.runlog.journal import Journal
        from coscc.data import Busy

        self.service.attempts.open("step", self.key, "0009_x", "impl", state="running")
        with mock.patch.object(Journal, "open_starts", side_effect=Busy("locked")):
            got = self.service.boards.running(self.cwd)
        self.assertEqual(got["note"], "locked")
        self.assertEqual(list(got["running"]), ["0009_x"])
        self.assertEqual(got["unknown_end"], {})

    def test_no_working_folder_means_no_unknown_end(self):
        config = Config(workspaces=(self.cwd,), data_dir=str(Path(self._tmp.name) / "bare"))
        service = Service(config, Sessions(config))
        self.assertEqual(service.boards.running(self.cwd), {"running": {}, "unknown_end": {}})


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
        self.service = Service(config, Sessions(config))
        self.cwd = str(root / "work" / "proj")
        self.key = self.service.ws.key(self.cwd)
        self.journal = self.service.ws.journal()

    def block(self, units=()) -> dict:
        with mock.patch(
            "coscc.service.autopilot.autopilot_values", return_value={"autopilot": True}
        ):
            return self.service.autopilot.guide_block(self.key, units)

    def test_guide_lists_running_steps(self):
        self.service.attempts.open("step", self.key, "0009_x", "impl", state="running")
        self.service.attempts.open("step", self.key, "0010_y", "spec", state="running")
        self.service.attempts.open("step", "/elsewhere", "0011_z", "spec", state="running")
        got = self.block()["running"]
        self.assertEqual(
            [(r["unit"], r["stage"], r["agent"]) for r in got],
            [("0009_x", "impl", "Uruz"), ("0010_y", "spec", "Kenaz")],
        )
        self.assertTrue(all(r["started"] for r in got))

    def test_guide_turns_every_stop_but_full_into_one_thing_to_do(self):
        kinds = ("a", "b", "c", "d", "e", "f", "cap", "reruns", "full")
        self.service.autopilot.stops[self.key] = {
            f"00{n:02d}_u": {"unit": f"00{n:02d}_u", "kind": k, "reason": f"why {k}"}
            for n, k in enumerate(kinds, 1)
        }
        self.service.autopilot.stops[self.key][""] = {
            "unit": "",
            "kind": "shortlist",
            "reason": "none",
        }
        self.service.autopilot.stops[self.key]["0099_w"] = {
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
        self.service.attempts.open("step", self.key, "0001_u", "impl", state="running")
        got = self.block([{"name": "0001_u", "state": {"state": "needs-you"}}])
        self.assertEqual((got["needs_you"], len(got["running"])), ([], 1))

    def test_guide_says_only_that_the_autopilot_is_off_when_it_is(self):
        self.service.attempts.open("step", self.key, "0009_x", "impl", state="running")
        self.assertEqual(self.service.autopilot.guide_block(self.key), {"on": False})
        self.assertEqual(asyncio.run(self.service.board(self.cwd))["guide"], {"on": False})


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
        self.service = Service(config, Sessions(config))

    def made(self, unit: str) -> tuple[Path, Path]:
        ram, disk = scratch.ensure(self.repo, unit, self.data)
        (disk / "big").write_text("x")
        return ram, disk

    def read(self, rows: list[dict]) -> None:
        asyncio.run(self.service.boards._attach_worktrees(str(self.repo), rows))

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
        self.service = Service(config, StandIn(None))
        self.key = self.service.ws.key(self.cwd)
        made = await self.service.answers.create_unit(self.cwd, SLUG, "fixture")
        self.unit, directory = made["unit"], Path(made["path"])
        for name in ("intent.md", "spec.md", "plan.md", "impl.md"):
            extra = " Type: feat." if name == "intent.md" else ""
            (directory / name).write_text(
                f"# X: fixture\nAuthor: t.{extra} Status: accepted.\n", encoding="utf-8"
            )
        (directory / "pr.md").write_text(
            f"# PR: fixture\nPR: https://github.com/o/r/pull/{PR}. Status: accepted.\n",
            encoding="utf-8",
        )
        (directory / "review.md").write_text(REVIEW_ONE, encoding="utf-8")
        self.hang = False
        self.calls: list[list[str]] = []
        patch = mock.patch.object(integrate, "_gh", self._gh)
        patch.start()
        self.addCleanup(patch.stop)

    async def asyncTearDown(self):
        await self.service.shutdown()

    async def _gh(self, argv, cwd):
        self.calls.append(argv[:2])
        if self.hang:
            await asyncio.sleep(30)
        if argv[:2] == ["pr", "list"]:
            row = {"number": PR, "headRefOid": "a" * 40, "headRefName": f"feat/{SLUG}"}
            return 0, json.dumps([{**row, "mergeable": "MERGEABLE"}]), ""
        return 0, "[]", ""

    def counted(self) -> list[str]:
        """Every `Board.read` from now on, by cwd."""
        reads: list[str] = []
        real = self.service.boards.read

        async def read(cwd, fresh=False):
            reads.append(cwd)
            return await real(cwd, fresh)

        self.service.boards.read = read
        return reads

    async def ended(self) -> None:
        """Every board read and CI ask running now ended, and the reads an answer starts."""
        while running := [
            *self.service.boards.reads.values(),
            *self.service.steps.ci_asks.values(),
        ]:
            await asyncio.gather(*running)
            for _ in range(5):
                await asyncio.sleep(0)

    async def test_a_round_on_the_board_carries_no_text(self):
        [u] = (await self.service.board(self.cwd))["units"]
        [rnd] = u["rounds"]
        self.assertNotIn("text", rnd)
        self.assertEqual([sorted(f) for f in rnd["found"]], [["fixed_by", "id", "label"]] * 2)
        self.assertEqual((rnd["verdict"], rnd["open_ids"]), ("changes-requested", ["F1", "F2"]))

    async def test_a_gh_that_hangs_holds_neither_the_held_board_nor_a_read(self):
        first = await self.service.board(self.cwd)
        self.assertEqual(first["units"][0]["integration"]["pr_head"], "a" * 40)
        self.hang = True
        began = time.monotonic()
        held = await self.service.board(self.cwd, "held")
        self.assertLessEqual(time.monotonic() - began, 0.5)
        self.assertEqual(held["read_at"], first["read_at"])
        # The read it started takes the held list and asks `gh` again in the background.
        await asyncio.wait_for(self.ended(), 5)
        self.assertEqual(
            self.service.boards.held[self.key]["data"]["units"][0]["integration"]["pr_head"],
            "a" * 40,
        )
        self.assertEqual(len(self.service.boards.prs.asks), 1)

    async def test_two_asks_while_a_read_runs_start_one_read(self):
        await self.service.board(self.cwd)
        await self.ended()
        reads = self.counted()
        await asyncio.gather(
            self.service.board(self.cwd, "held"), self.service.board(self.cwd, "held")
        )
        await self.ended()
        self.assertEqual(reads, [self.cwd])

    async def test_a_new_ask_while_a_read_runs_reads_once_more_after_it(self):
        await self.service.board(self.cwd)
        await self.ended()
        reads = self.counted()
        await self.service.board(self.cwd, "held")
        await self.service.board(self.cwd)
        self.assertEqual(reads, [self.cwd, self.cwd])

    async def test_a_change_the_app_makes_starts_a_read_and_next_waits_for_it(self):
        await self.service.board(self.cwd)
        await self.ended()
        reads = self.counted()
        waiting = asyncio.ensure_future(self.service.board(self.cwd, "next"))
        await asyncio.sleep(0)
        self.assertFalse(waiting.done())
        self.assertEqual(reads, [])
        await self.service.steps.set_mode(self.cwd, self.unit, "impl", "autonomous")
        data = await asyncio.wait_for(waiting, 5)
        self.assertEqual(reads, [self.cwd])
        [u] = data["units"]
        self.assertEqual(next(r["mode"] for r in u["stages"] if r["stage"] == "impl"), "autonomous")

    async def test_a_change_before_any_read_starts_none(self):
        reads = self.counted()
        self.service.bus.publish(Event("answer.written", self.key, self.unit))
        self.assertEqual((reads, self.service.boards.reads), ([], {}))

    async def test_every_read_logs_how_long_each_part_took(self):
        with self.assertLogs("coscc.service.board", "INFO") as logs:
            await self.service.board(self.cwd)
        [line] = [m for m in logs.output if " read in " in m]
        for part in (
            "snapshot",
            "loop",
            "import",
            "run log",
            "worktree",
            "integration",
            "release",
        ):
            self.assertIn(f"{part} ", line)
