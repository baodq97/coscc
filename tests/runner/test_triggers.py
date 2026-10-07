"""Every trigger runs a row: each kind starts it once, an off row waits, a run is refused before
spend, an empty input is skipped for nothing, and a run at its ceiling turns the row off."""

from __future__ import annotations

import asyncio
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from coscc.agent import pack
from coscc.bus import Bus
from coscc.kernel import Hooks, Parts, Run, Tool
from coscc.runner import run as run_mod
from coscc.runner import triggers
from coscc.store.db import Busy, Data
from coscc.store.journal import Intervention, Journal
from coscc.units import Invalid, proposals
from coscc.units.meta import UnitMeta

# The app's catalog holds every feature's tool, on or off: the grader names the code index.
HOOKS = Hooks(
    parts=(("codegraph", Parts(tools=(Tool("codegraph", "read", "low", when=lambda f: False),))),)
)

PROBLEM = "Steps stop and a person runs them again by hand. " * 6


def found(n: int) -> list[Intervention]:
    return [
        Intervention(
            f"rerun:runs:{i}", "rerun", f"2026-10-02T00:{i:02d}:00+00:00", "0001_a", "spec", "again"
        )
        for i in range(1, n + 1)
    ]


class _Core(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.ws = str(root / "ws")
        Path(self.ws).mkdir()
        self.data = Data(root / "data")
        patch = mock.patch.object(pack, "ROOT", str(root / "data"))
        patch.start()
        self.addCleanup(patch.stop)
        self.journal = Journal(root / "work", self.data)
        self.found: list[Intervention] = []
        self.given: list[run_mod.Input] = []
        self.reply = Run("done", {"proposals": []}, {"cost_usd": 0.3}, session="s1", run="r1")
        self.gate = asyncio.Event()
        self.gate.set()
        self.core = SimpleNamespace(
            config=SimpleNamespace(data_dir=root / "data"),
            ws=SimpleNamespace(
                check=lambda cwd: cwd,
                key=lambda cwd: cwd,
                journal=lambda: self.journal,
                units_root=lambda cwd: root / "units",
                unit_meta=lambda: None,
                all=lambda: {"paths": [self.ws]},
                unit_dir=self._unit_dir,
            ),
            holds=SimpleNamespace(attempts=None),
            sessions=None,
            models=SimpleNamespace(agent=lambda key, row: run_mod.Agent(key, row)),
            steps=SimpleNamespace(refuse_updating=lambda: None, hooks=HOOKS),
            autopilot=SimpleNamespace(today=lambda cwd: (0.0, 120.0)),
            updater=SimpleNamespace(job_ended=lambda: None),
            bus=Bus(),
        )
        for target, fake in (
            ("coscc.runner.triggers.interventions", self._interventions),
            ("coscc.runner.triggers.run_mod.run", self._run),
        ):
            p = mock.patch(target, fake)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(triggers._RUNNING.clear)

    def _unit_dir(self, cwd, unit):
        if not re.fullmatch(r"\d{4}_[a-z0-9-]+", unit):
            raise Invalid(f"not a work unit name: {unit!r}")
        return Path(cwd) / unit

    def _interventions(self, journal, meta, attempts, key, after, limit):
        return [i for i in self.found if i.at > after][:limit]

    async def _run(self, agent, given, *, ctx, finish=None):
        self.given.append(given)
        await self.gate.wait()
        got = Run(
            self.reply.status, self.reply.output, dict(self.reply.cost), session="s1", run="r1"
        )
        extra = dict(await finish(got)) if finish else {}
        self.journal.finished(
            given.workspace,
            given.unit,
            agent.key,
            run_mod.OUTCOME[got.status],
            agent=agent.key,
            **got.cost,
            **extra,
        )
        yield ("done", got)

    def set_row(self, field: str, value) -> None:
        pack.write("scan", field, value)

    def ends(self) -> list[dict]:
        return [
            r for r in self.journal.records(self.ws, kinds=("end",)) if r.get("stage") == "scan"
        ]

    async def settle(self) -> None:
        await asyncio.gather(*triggers._TASKS)


class APressRunsTheRow(_Core):
    async def test_manual_runs_once_with_its_prompt_and_keeps_its_proposals(self):
        self.found = found(3)
        self.reply = Run(
            "done",
            {
                "proposals": [
                    {
                        "type": "fix",
                        "slug": "steps-stop",
                        "title": "Steps stop",
                        "problem": PROBLEM,
                        "sources": ["rerun:runs:1"],
                    }
                ]
            },
        )
        run = await triggers.run(self.core, "scan", self.ws, by="manual")
        self.assertEqual(run, "r1")
        (given,) = self.given
        self.assertEqual((given.started_by, given.start["trigger"]), ("manual", "manual"))
        self.assertIn("rerun:runs:3", given.prompt)
        self.assertIn("# Proposals already made", given.prompt)
        (p,) = proposals.listed(self.data, self.ws)
        self.assertEqual((p["agent"], p["sources"][0]["unit"]), ("scan", "0001_a"))
        (end,) = self.ends()
        self.assertEqual((end["data_until"], end["proposals"]), (self.found[-1].at, 1))

    async def test_the_next_run_reads_past_where_the_last_stopped(self):
        self.found = found(2)
        await triggers.run(self.core, "scan", self.ws, by="manual")
        self.found = found(4)
        await triggers.run(self.core, "scan", self.ws, by="manual")
        self.assertNotIn("rerun:runs:2 ", self.given[1].prompt)
        self.assertIn("rerun:runs:3", self.given[1].prompt)

    async def test_nothing_new_is_skipped_at_no_cost_and_opens_no_session(self):
        self.assertEqual(await triggers.run(self.core, "scan", self.ws, by="manual"), "")
        self.assertEqual(self.given, [])
        (end,) = self.ends()
        self.assertEqual((end["skipped"], end["cost_usd"], end["outcome"]), (True, 0.0, "done"))

    async def test_a_second_run_of_the_same_agent_is_refused_before_spend(self):
        self.found = found(1)
        self.gate.clear()
        first = asyncio.create_task(triggers.run(self.core, "scan", self.ws, by="manual"))
        while not self.given:
            await asyncio.sleep(0)
        with self.assertRaises(Invalid) as e:
            await triggers.run(self.core, "scan", self.ws, by="manual")
        self.assertEqual(e.exception.reasons, ("unit-busy",))
        self.gate.set()
        await first
        self.assertEqual(len(self.given), 1)

    async def test_a_second_press_is_refused_at_once(self):
        self.found = found(1)
        triggers.start(self.core, "scan", self.ws, by="manual")
        with self.assertRaises(Invalid) as e:
            triggers.start(self.core, "scan", self.ws, by="manual")
        self.assertEqual(e.exception.reasons, ("unit-busy",))
        await self.settle()
        self.assertEqual(len(self.given), 1)

    async def test_the_daily_cap_and_a_row_with_no_such_trigger_are_refused(self):
        self.found = found(1)
        self.core.autopilot.today = lambda cwd: (120.0, 120.0)
        with self.assertRaises(Invalid) as e:
            await triggers.run(self.core, "scan", self.ws, by="manual")
        self.assertEqual(e.exception.reasons, ("budget-reached",))
        self.core.autopilot.today = lambda cwd: (0.0, 120.0)
        with self.assertRaises(Invalid) as e:
            await triggers.run(self.core, "estimate", self.ws, by="manual")
        self.assertEqual(e.exception.reasons, ("not-triggered",))
        self.assertEqual(self.given, [])


class ARefusalBeforeSpend(_Core):
    async def refused(self, **kw) -> tuple[str, ...]:
        with self.assertRaises(Invalid) as e:
            await triggers.run(self.core, "scan", self.ws, by="manual", **kw)
        self.assertEqual(self.given, [])
        return e.exception.reasons

    async def test_an_owner_file_giving_a_non_reading_tool_is_refused(self):
        owner = pack.owner_dir() / "agents"
        owner.mkdir(parents=True, exist_ok=True)
        (owner / "scan.md").write_text('---\ntools: {"vault": "allow"}\n---\n')
        self.found = found(1)
        self.assertEqual(await self.refused(), ("agent-invalid",))

    async def test_a_press_only_row_holding_a_write_tool_is_refused(self):
        owner = pack.owner_dir() / "agents"
        owner.mkdir(parents=True, exist_ok=True)
        (owner / "scan.md").write_text(
            '---\ntrigger: {"manual": true}\ntools: {"Write": "allow"}\n---\n'
        )
        self.found = found(1)
        self.assertEqual(await self.refused(), ("agent-invalid",))

    async def test_an_unreadable_spend_and_a_ceiling_past_the_cap_are_refused(self):
        self.found = found(1)
        self.core.autopilot.today = lambda cwd: None
        self.assertEqual(await self.refused(), ("unavailable",))
        self.core.autopilot.today = lambda cwd: (119.5, 120.0)
        self.assertEqual(await self.refused(), ("budget-reached",))

    async def test_a_unit_that_walks_out_is_refused(self):
        with mock.patch.object(triggers, "_unit_scoped", return_value=True):
            with self.assertRaises(Invalid) as e:
                await triggers.run(self.core, "scan", self.ws, "../..", by="manual")
        self.assertIn("not a work unit name", str(e.exception))

    async def test_a_busy_journal_skips_the_schedule_not_reads_it_as_never_run(self):
        self.found = found(1)
        pack.set_agent_on(self.data, "scan", self.ws, True)
        with mock.patch.object(self.journal, "records", side_effect=Busy("busy")):
            await triggers.tick(self.core)
        self.assertEqual(self.given, [])


class LeifRunsARowThatSaysLeif(_Core):
    async def test_its_reason_is_on_the_start(self):
        self.found = found(1)
        await triggers.run(self.core, "scan", self.ws, by="leif", reason="CI went red twice")
        self.assertEqual(
            (self.given[0].started_by, self.given[0].start["reason"]), ("leif", "CI went red twice")
        )
        with self.assertRaises(Invalid):
            await triggers.run(self.core, "scan", self.ws, by="leif", reason=" ")

    async def test_a_row_without_leif_is_refused_not_leif(self):
        self.set_row("trigger", {"schedule": {"hours": 24}, "manual": True})
        with self.assertRaises(Invalid) as e:
            await triggers.run(self.core, "scan", self.ws, by="leif", reason="why")
        self.assertEqual(e.exception.reasons, ("not-leif",))
        with self.assertRaises(Invalid) as e:
            await triggers.run(self.core, "impl", self.ws, by="leif", reason="why")
        self.assertEqual(e.exception.reasons, ("not-triggered",))

    async def test_the_tool_says_the_refusal(self):
        self.set_row("trigger", {"schedule": {"hours": 24}, "manual": True})
        triggers.leif_server(self.core, self.ws)
        said = await triggers.leif_call(self.core, self.ws, {"key": "scan", "reason": "r"})
        self.assertTrue(said["is_error"])
        self.assertIn("not-leif", said["content"][0]["text"])


class TheScheduleRunsItWhereItIsOn(_Core):
    async def test_off_runs_nothing_on_it_runs_once_until_the_hours_pass(self):
        self.found = found(1)
        await triggers.tick(self.core)
        self.assertEqual(self.given, [])
        pack.set_agent_on(self.data, "scan", self.ws, True)
        await triggers.tick(self.core)
        await self.settle()
        self.assertEqual([g.started_by for g in self.given], ["schedule"])
        await triggers.tick(self.core)
        await self.settle()
        self.assertEqual(len(self.given), 1)
        self.found = found(2)
        self.set_row("trigger", {"schedule": {"hours": 1}, "manual": True, "leif": True})
        with mock.patch.object(triggers, "_hours_since", return_value=1.5):
            await triggers.tick(self.core)
            await self.settle()
        self.assertEqual(len(self.given), 2)


class AnEventRunsItWhereItIsOn(_Core):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.set_row("default", "on")
        self.found = found(1)
        triggers.listen(self.core)
        self.shipped = {"workspace": self.ws, "unit": "0001_a", "sha": "abc", "at": "t"}

    async def test_without_a_delay_the_event_starts_it(self):
        self.set_row("trigger", {"event": {"name": "unit.shipped"}, "manual": True})
        self.core.bus.publish("unit.shipped", self.shipped)
        await self.settle()
        self.assertEqual([g.started_by for g in self.given], ["event"])

    async def test_off_the_event_starts_nothing(self):
        self.set_row("trigger", {"event": {"name": "unit.shipped"}})
        pack.set_agent_on(self.data, "scan", self.ws, False)
        self.core.bus.publish("unit.shipped", self.shipped)
        await self.settle()
        await triggers.tick(self.core)
        self.assertEqual(self.given, [])

    async def test_with_a_delay_a_due_row_survives_and_the_tick_runs_it_once(self):
        self.set_row("trigger", {"event": {"name": "unit.shipped", "after_hours": 2}})
        self.core.bus.publish("unit.shipped", self.shipped)
        await self.settle()
        await triggers.tick(self.core)
        self.assertEqual(self.given, [])
        # What a restart keeps is the table; the tick reads only it.
        with self.data.write() as conn:
            conn.execute("UPDATE trigger_due SET due_at = '2000-01-01T00:00:00+00:00'")
        await triggers.tick(self.core)
        await self.settle()
        await triggers.tick(self.core)
        await self.settle()
        self.assertEqual([g.started_by for g in self.given], ["event"])


class ARunAtItsCeilingTurnsTheRowOff(_Core):
    async def test_it_goes_off_here_and_the_run_log_says_why(self):
        pack.set_agent_on(self.data, "scan", self.ws, True)
        self.found = found(1)
        self.reply = Run(
            "paused-budget", None, {"cost_usd": 0.7}, detail="stopped at its ceiling: max_budget"
        )
        await triggers.run(self.core, "scan", self.ws, by="schedule")
        self.assertFalse(pack.agent_on(self.data, "scan", self.ws))
        (said,) = self.journal.records(self.ws, kinds=("agent-state",))
        self.assertEqual((said["on"], said["by"]), (False, "app"))
        self.assertIn("ceiling", said["reason"])
        self.assertEqual(self.ends()[-1].get("data_until"), None)


class TheGraderGradesWhatShipped(_Core):
    """The outcome row: a fresh session in the trunk tree, the unit's intent and idea in its
    prompt, its verdict the unit's `outputs` row, and a proposal for each criterion not met."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        root = Path(self.ws).parent
        self.unit = "0141_fast-lane"
        (root / "units" / self.unit).mkdir(parents=True)
        (root / "units" / self.unit / "intent.md").write_text("## Proposed outcome\nA fix ships.")
        self.trunk = root / "trunk"
        self.trunk.mkdir()
        self.meta = UnitMeta(root / "work", self.data)
        self.core.ws.unit_meta = lambda: self.meta
        self.core.ideas = SimpleNamespace(idea_note=lambda cwd, unit: "## Wanted\nFewer steps.")

        async def main_tree(workspace, data_dir=None):
            return self.trunk, "abc"

        p = mock.patch("coscc.runner.triggers.worktrees.main_tree", main_tree)
        p.start()
        self.addCleanup(p.stop)

    def criteria(self, *met: str) -> dict:
        return {
            "criteria": [
                {"criterion": f"O{n}", "source": f"sentence {n}", "met": m, "evidence": "a.py:1-2"}
                for n, m in enumerate(met, start=1)
            ]
        }

    async def test_a_press_grades_in_the_trunk_tree_and_proposes_each_no(self):
        self.reply = Run("done", self.criteria("yes", "no", "unclear", "no"))
        await triggers.run(self.core, "outcome", self.ws, self.unit, by="manual")
        (given,) = self.given
        self.assertEqual((given.cwd, given.workspace_dir), (str(self.trunk), self.ws))
        self.assertIn("A fix ships.", given.prompt)
        self.assertIn("Fewer steps.", given.prompt)
        verdict = self.meta.verdict(self.ws, self.unit)
        self.assertEqual((verdict["agent"], verdict["judgement"]), ("outcome", "not-met"))
        made = proposals.listed(self.data, self.ws, "outcome")
        self.assertEqual(sorted(p["title"] for p in made), ["sentence 2", "sentence 4"])
        self.assertTrue(all(p["type"] == "fix" and p["unit"] == self.unit for p in made))
        self.assertTrue(all(proposals.SLUG.match(p["slug"]) for p in made))
        (end,) = [
            r for r in self.journal.records(self.ws, kinds=("end",)) if r["stage"] == "outcome"
        ]
        self.assertEqual((end["verdict"], end["proposals"]), ("not-met", 2))

    async def test_unclear_alone_proposes_nothing(self):
        self.reply = Run("done", self.criteria("yes", "unclear"))
        await triggers.run(self.core, "outcome", self.ws, self.unit, by="manual")
        self.assertEqual(self.meta.verdict(self.ws, self.unit)["judgement"], "unclear")
        self.assertEqual(proposals.listed(self.data, self.ws, "outcome"), [])

    async def test_a_ship_is_graded_a_week_later_where_it_is_on(self):
        triggers.listen(self.core)
        shipped = {"workspace": self.ws, "unit": self.unit, "sha": "abc", "at": "t"}
        self.core.bus.publish("unit.shipped", shipped)
        with self.data.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM trigger_due").fetchone()[0], 0)
        pack.set_agent_on(self.data, "outcome", self.ws, True)
        self.core.bus.publish("unit.shipped", shipped)
        await self.settle()
        self.assertEqual(self.given, [])
        with self.data.write() as conn:
            (due,) = conn.execute("SELECT due_at FROM trigger_due").fetchone()
            self.assertGreater(due, "2026")
            conn.execute("UPDATE trigger_due SET due_at = '2000-01-01T00:00:00+00:00'")
        self.reply = Run("done", self.criteria("yes"))
        await triggers.tick(self.core)
        await self.settle()
        self.assertEqual([(g.started_by, g.unit) for g in self.given], [("event", self.unit)])

    async def test_no_trunk_tree_ends_failed_before_any_session(self):
        async def broken(workspace, data_dir=None):
            raise triggers.GitError("no origin")

        with mock.patch("coscc.runner.triggers.worktrees.main_tree", broken):
            await triggers.run(self.core, "outcome", self.ws, self.unit, by="manual")
        self.assertEqual(self.given, [])
        (end,) = self.journal.records(self.ws, self.unit, kinds=("end",))
        self.assertEqual(end["outcome"], "failed")


if __name__ == "__main__":
    unittest.main()
