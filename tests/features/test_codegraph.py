"""`coscc/features/codegraph/__init__.py`: the record of each run, the map block, the tools'
condition, the Settings sentence, the bus handler and the report, with a fake index."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc import features, kernel
from coscc.bus import Bus, Event
from coscc.data import Data, now
from coscc.features import codegraph
from coscc.features.codegraph import Ready, Status
from coscc.plugin import create_tables
from coscc.kernel import Ctx, arm_of

SHA = "a" * 40
KEY = "/w/proj"


class FakeIndexes:
    def __init__(self, home: Path):
        self.home = home
        self.got: Ready | str = Ready("/data/_main", SHA, 12)
        self.shown = Status("ready", SHA, now(), "")
        self._binary: Path | None = Path("/bin/node")
        self._installing = False
        self.locked = ""
        self.retried = 0
        self.scheduled: list[str] = []

    async def ensure(self, workspace: str) -> Ready | str:
        return self.got

    async def _engine(self) -> Path | str:
        return self._binary or "not installed"

    def status(self, key: str) -> Status:
        return self.shown

    def lock(self) -> str:
        return self.locked

    def retry(self) -> None:
        self.retried += 1

    def schedule(self, key: str) -> None:
        self.scheduled.append(key)


class Setup(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = "pilot"
        self.ctx = Ctx(
            lambda: None,
            lambda cwd: KEY,
            lambda f, w: self.state != "off",
            Bus(),
            Data(self.root / "data"),
            lambda f, w: self.state,
            lambda f, w, unit: arm_of(self.state, unit),
        )
        create_tables(self.ctx, codegraph.FEATURE.tables)
        self.idx = FakeIndexes(self.root / "data" / "codegraph")
        patch = mock.patch.object(codegraph, "_indexes", return_value=self.idx)
        patch.start()
        self.addCleanup(patch.stop)

    def facts(self, unit: str, stage: str = "impl", run: str = "r1") -> kernel.Facts:
        return kernel.facts(
            workspace="/w/proj",
            workspace_key=KEY,
            unit=unit,
            stage=stage,
            run=run,
            cwd=str(self.root),
            watch=None,
            directory=self.root,
            commands=("git", "node"),
            resumed=False,
        )

    def rows(self) -> list[tuple]:
        with self.ctx.data.connect() as conn:
            return [
                tuple(r)
                for r in conn.execute(
                    "SELECT run, unit, stage, arm, sha, map_chars, wait_ms, error "
                    "FROM codegraph_runs ORDER BY run"
                ).fetchall()
            ]

    def ready_row(self) -> None:
        with self.ctx.data.write() as conn:
            conn.execute(
                "INSERT INTO codegraph_index (workspace, path, state, sha, at, root) "
                "VALUES (?, '/w/proj', 'ready', ?, ?, '/data/_main')",
                (KEY, SHA, now()),
            )


class EachRunIsRecordedWithItsArm(Setup):
    async def test_off_writes_nothing_and_adds_nothing(self):
        self.state = "off"
        self.assertEqual(await codegraph._render(self.ctx, self.facts("0002_a")), "")
        self.assertEqual(self.rows(), [])

    async def test_an_odd_unit_of_a_pilot_gets_no_map_and_one_off_row(self):
        with mock.patch.object(codegraph, "_map", return_value="MAP") as made:
            self.assertEqual(await codegraph._render(self.ctx, self.facts("0003_a")), "")
        made.assert_not_called()
        self.assertEqual(self.rows(), [("r1", "0003_a", "impl", "off", SHA, 0, 0, "")])

    async def test_an_even_unit_gets_the_map_and_its_size_and_wait_are_kept(self):
        with mock.patch.object(codegraph, "_map", return_value="MAP"):
            got = await codegraph._render(self.ctx, self.facts("0002_a", "review"))
        self.assertEqual(got, "MAP")
        self.assertEqual(self.rows(), [("r1", "0002_a", "review", "on", SHA, 3, 12, "")])

    async def test_under_on_an_odd_unit_is_on_too(self):
        self.state = "on"
        with mock.patch.object(codegraph, "_map", return_value="MAP"):
            self.assertEqual(await codegraph._render(self.ctx, self.facts("0003_a")), "MAP")

    async def test_no_index_or_a_failing_map_adds_nothing_and_records_why(self):
        self.idx.got = "There is no code index yet."
        self.assertEqual(await codegraph._render(self.ctx, self.facts("0002_a", run="r1")), "")
        self.idx.got = Ready("/data/_main", SHA, 5)
        fails = mock.patch.object(codegraph, "_map", side_effect=codegraph.BridgeError("boom"))
        with fails:
            self.assertEqual(await codegraph._render(self.ctx, self.facts("0002_a", run="r2")), "")
        self.assertEqual(
            [(r[0], r[7]) for r in self.rows()],
            [("r1", "There is no code index yet."), ("r2", "boom")],
        )

    async def test_an_engine_found_broken_is_off_for_every_unit_and_records_nothing(self):
        self.idx.locked = "The Node that came with codegraph is 22.5.0."
        for unit in ("0002_a", "0003_a"):
            self.assertEqual(await codegraph._render(self.ctx, self.facts(unit)), "")
        self.assertEqual(self.rows(), [])

    async def test_a_stage_other_than_impl_or_review_is_not_recorded(self):
        self.assertEqual(await codegraph._render(self.ctx, self.facts("0002_a", "plan")), "")
        self.assertEqual(self.rows(), [])


class TheToolsGoOnlyToAnOnArmRunWithAReadyIndex(Setup):
    def test_the_condition(self):
        even, odd = self.facts("0002_a"), self.facts("0003_a")
        self.assertFalse(codegraph._ready_for(self.ctx, even))
        self.ready_row()
        self.assertTrue(codegraph._ready_for(self.ctx, even))
        self.assertFalse(codegraph._ready_for(self.ctx, odd))
        # After a restart nothing has checked the engine yet: a resumed run still gets them.
        self.idx._binary = None
        self.assertTrue(codegraph._ready_for(self.ctx, even))
        self.idx.locked = "The codegraph install has no Node binary for this machine."
        self.assertFalse(codegraph._ready_for(self.ctx, even))
        tool = codegraph.agent(self.ctx).tools[0]
        self.assertEqual((tool.stages, tool.names), (("impl",), ("find", "callers", "impact")))

    async def test_a_tool_answers_from_the_index_and_marks_the_changed_files(self):
        self.ready_row()
        tools = {t.name: t for t in codegraph.build_tools(self.ctx, self.facts("0002_a"))}
        with (
            mock.patch.object(codegraph, "call", return_value=[]),
            mock.patch.object(codegraph, "changed_files", return_value=set()),
        ):
            got = await tools["callers"].handler({"symbol": "ensure"})
        self.assertFalse(got["is_error"])
        self.assertEqual(got["content"], [{"type": "text", "text": mock.ANY}])
        self.assertIn(SHA[:12], json.dumps(got["content"]))

    async def test_a_tool_asks_for_the_engine_and_says_so_when_there_is_none(self):
        self.ready_row()
        self.idx._binary = None
        tools = {t.name: t for t in codegraph.build_tools(self.ctx, self.facts("0002_a"))}
        with mock.patch.object(codegraph, "call") as called:
            got = await tools["find"].handler({"query": "ensure"})
        called.assert_not_called()
        self.assertTrue(got["is_error"])


class SettingsSaysOneSentence(Setup):
    def test_each_state_has_its_sentence(self):
        self.state = "off"
        with mock.patch("shutil.which", return_value="/usr/bin/npm"):
            self.assertEqual(
                codegraph.status(self.ctx, "/w/proj"), ("Off in this workspace.", True)
            )
            self.state = "pilot"
            self.assertTrue(codegraph.status(self.ctx, "/w/proj")[0].startswith("Ready: "))
            self.idx.shown = Status("failed", SHA, now(), "git refused.")
            self.assertTrue(
                codegraph.status(self.ctx, "/w/proj")[0].startswith("Failed: git refused; ")
            )
            self.idx.shown = Status("", "", "", "")
            self.state = "on"
            self.assertIn("next impl", codegraph.status(self.ctx, "/w/proj")[0])

    def test_a_lock_is_the_sentence_and_forbids_choosing(self):
        self.idx.locked = codegraph.NO_NPM
        self.assertEqual(codegraph.status(self.ctx, "/w/proj"), (codegraph.NO_NPM, False))

    def test_the_feature_is_off_by_default_and_offers_a_pilot(self):
        self.assertIn(codegraph.FEATURE, features.FEATURES)
        self.assertEqual((codegraph.FEATURE.default, codegraph.FEATURE.pilot), ("off", True))


class AtPilotTheSentenceTellsTheSplit(Setup):
    def seed(self, runs: dict[str, str]) -> None:
        """One impl run per `(unit, arm)` in the run log and in `codegraph_runs`, in the report's form."""
        with self.ctx.data.write() as conn:
            for i, (unit, arm) in enumerate(runs.items()):
                run = f"r{i}"
                for kind, record in (("start", {}), ("end", {"run": run, "cost_usd": 1.0})):
                    conn.execute(
                        "INSERT INTO runs (at, root, workspace, unit, stage, kind, record) "
                        "VALUES ('2026-10-02T10:00:00', '/r', ?, ?, 'impl', ?, ?)",
                        (KEY, unit, kind, json.dumps({"kind": kind, **record})),
                    )
                conn.execute(
                    "INSERT INTO codegraph_runs VALUES (?, ?, ?, 'impl', ?, ?, 0, 0, '', ?)",
                    (run, KEY, unit, arm, SHA, "2026-10-02T10:00:00"),
                )

    def test_it_names_the_split_the_units_of_each_arm_and_the_scoring_day(self):
        # 0002 and 0004 on, 0003 off, and 0006 in both arms: counted in neither.
        self.seed({"0002_a": "on", "0003_b": "off", "0004_c": "on", "0005_e": "off"})
        with self.ctx.data.write() as conn:
            conn.execute(
                "INSERT INTO codegraph_runs VALUES ('r9', ?, '0004_c', 'review', 'off', ?, 0, 0, '', "
                "'2026-10-02T11:00:00')",
                (KEY, SHA),
            )
        self.idx.shown = Status("", "", "", "")
        sentence, may = codegraph.status(self.ctx, "/w/proj")
        self.assertTrue(may)
        self.assertIn("Even-numbered units use it, odd ones do not", sentence)
        self.assertIn(": 1 on, 2 off so far", sentence)
        self.assertIn("Nov 15", sentence)
        self.assertNotIn("Waiting", sentence)
        arms = codegraph.measured(self.ctx, KEY, (None, None))["arms"]
        self.assertEqual((arms["on"]["units"], arms["off"]["units"]), (1, 2))

    def test_the_counts_are_the_reports_for_units_in_one_arm(self):
        self.seed(
            {"0002_a": "on", "0003_b": "off", "0004_c": "on", "0005_e": "off", "0006_f": "on"}
        )
        self.idx.shown = Status("", "", "", "")
        arms = codegraph.measured(self.ctx, KEY, (None, None))["arms"]
        sentence = codegraph.status(self.ctx, "/w/proj")[0]
        self.assertIn(f"{arms['on']['units']} on, {arms['off']['units']} off so far", sentence)
        self.assertIn("3 on, 2 off so far", sentence)

    def test_a_run_that_failed_is_not_counted_as_the_report_does_not(self):
        self.seed({"0002_a": "on", "0003_b": "off", "0004_c": "on"})
        with self.ctx.data.write() as conn:
            conn.execute("UPDATE codegraph_runs SET error = 'no index' WHERE unit = '0004_c'")
        self.idx.shown = Status("", "", "", "")
        arms = codegraph.measured(self.ctx, KEY, (None, None))["arms"]
        self.assertEqual((arms["on"]["units"], arms["off"]["units"]), (1, 1))
        self.assertIn("1 on, 1 off so far", codegraph.status(self.ctx, "/w/proj")[0])

    def test_a_unit_with_only_a_review_run_is_not_counted(self):
        self.seed({"0002_a": "on", "0003_b": "off"})
        with self.ctx.data.write() as conn:
            conn.execute(
                "INSERT INTO codegraph_runs VALUES ('r9', ?, '0006_f', 'review', 'on', ?, 0, 0, '', "
                "'2026-10-02T11:00:00')",
                (KEY, SHA),
            )
        self.idx.shown = Status("", "", "", "")
        arms = codegraph.measured(self.ctx, KEY, (None, None))["arms"]
        self.assertEqual((arms["on"]["units"], arms["off"]["units"]), (1, 1))
        self.assertIn("1 on, 1 off so far", codegraph.status(self.ctx, "/w/proj")[0])

    def test_a_step_whose_events_were_purged_is_not_counted_as_the_report_does_not(self):
        self.seed({"0002_a": "on", "0003_b": "off", "0004_c": "on"})
        with self.ctx.data.write() as conn:
            conn.execute(
                "INSERT INTO step_runs (run, root, workspace, unit, stage, started_at, purged_at) "
                "VALUES ('r2', '/r', ?, '0004_c', 'impl', 0, '2026-10-03T00:00:00')",
                (KEY,),
            )
        self.idx.shown = Status("", "", "", "")
        arms = codegraph.measured(self.ctx, KEY, (None, None))["arms"]
        self.assertEqual((arms["on"]["units"], arms["off"]["units"]), (1, 1))
        self.assertIn("1 on, 1 off so far", codegraph.status(self.ctx, "/w/proj")[0])

    def test_it_reads_no_event_and_no_review(self):
        # Asked on every read of the panel: it must not run the report's parsing.
        self.seed({"0002_a": "on", "0003_b": "off"})
        self.idx.shown = Status("", "", "", "")
        with (
            mock.patch.object(codegraph, "turn_pairs", side_effect=AssertionError),
            mock.patch.object(codegraph, "read_chars", side_effect=AssertionError),
            mock.patch.object(codegraph, "_rounds", side_effect=AssertionError),
        ):
            sentence = codegraph.status(self.ctx, "/w/proj")[0]
        self.assertIn("1 on, 1 off so far", sentence)

    def test_an_install_state_comes_first(self):
        for state, head in (
            ("installing", "Installing the code index engine"),
            ("building", "Building the index of main"),
            ("failed", "Failed: git refused"),
        ):
            self.idx.shown = Status(state, SHA, now(), "git refused.")
            sentence = codegraph.status(self.ctx, "/w/proj")[0]
            self.assertTrue(sentence.startswith(head), sentence)
            self.assertIn("even-numbered units use it", sentence)
            self.assertIn("Nov 15", sentence)

    def test_only_a_pilot_has_it(self):
        self.idx.shown = Status("", "", "", "")
        self.state = "on"
        self.assertNotIn("Even-numbered", codegraph.status(self.ctx, "/w/proj")[0])

    def test_the_scoring_day_is_the_one_the_owner_gave(self):
        self.assertEqual(codegraph.SCORING_DAY.isoformat(), "2026-11-15")


class AnIntegrationSyncsAnIndexInUse(Setup):
    def test_only_a_workspace_with_an_index_and_not_off(self):
        codegraph.agent(self.ctx)
        self.ctx.bus.publish(Event("integration.ended", KEY, "0002_a"))
        self.assertEqual(self.idx.scheduled, [])
        self.ready_row()
        self.ctx.bus.publish(Event("integration.ended", KEY, "0002_a"))
        self.state = "off"
        self.ctx.bus.publish(Event("integration.ended", KEY, "0002_a"))
        self.assertEqual(self.idx.scheduled, [KEY])


class TheReportReadsTheRunLog(Setup):
    def test_one_counted_step_per_arm_and_a_fail_for_too_few_units(self):
        with self.ctx.data.write() as conn:
            for unit, run, arm, cost in (("0002_a", "r1", "on", 1.0), ("0003_b", "r2", "off", 2.0)):
                for kind, record in (("start", {}), ("end", {"run": run, "cost_usd": cost})):
                    conn.execute(
                        "INSERT INTO runs (at, root, workspace, unit, stage, kind, record) "
                        "VALUES ('2026-10-02T10:00:00', '/r', ?, ?, 'impl', ?, ?)",
                        (KEY, unit, kind, json.dumps({"kind": kind, **record})),
                    )
                conn.execute(
                    "INSERT INTO codegraph_runs VALUES (?, ?, ?, 'impl', ?, ?, 0, 0, '', ?)",
                    (run, KEY, unit, arm, SHA, "2026-10-02T10:00:00"),
                )
        got = codegraph.measured(self.ctx, KEY, ("2026-10-01", None))
        self.assertEqual((got["arms"]["on"]["steps"], got["arms"]["off"]["steps"]), (1, 1))
        self.assertEqual(got["arms"]["off"]["cost_mean"], 2.0)
        self.assertEqual(got["verdict"], "fail")


class PickingAStateStartsTheSetup(Setup):
    async def test_pilot_or_on_asks_for_the_index_and_off_does_nothing(self):
        asked = []

        async def ensure(workspace):
            asked.append(workspace)
            return "installing"

        self.idx.ensure = ensure
        codegraph.on_set(self.ctx, "/w/proj", "off")
        codegraph.on_set(self.ctx, "/w/proj", "pilot")
        await asyncio.gather(*codegraph._running)
        self.assertEqual((asked, self.idx.retried), (["/w/proj"], 1))


if __name__ == "__main__":
    unittest.main()
