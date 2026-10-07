"""A question about a run: refused before spend, resumed warm only when nothing it read or ran
with changed, else answered afresh from the run's summary; never with more than the run held,
never writing, never handing back an object; decided from the run log alone."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from coscc.agent import pack, policy
from coscc.agent.helpers import Gate
from coscc.bus import Bus
from coscc.kernel import Hooks, Parts, Run, Tool
from coscc.runner import ask, triggers
from coscc.runner import run as run_mod
from coscc.store.db import Data
from coscc.store.journal import Journal
from coscc.units import Invalid
from tests.units.test_submit import _draft_agent

HOOKS = Hooks(
    parts=(("codegraph", Parts(tools=(Tool("codegraph", "read", "low", when=lambda f: False),))),)
)


def grant_of(row, cwd, **kw):
    return run_mod.issue(row, None, cwd=cwd, **kw)


class _Asking(unittest.IsolatedAsyncioTestCase):
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
        self.runs: list[tuple[run_mod.Agent, run_mod.Input]] = []
        self.head = "h1"
        self.transcript = True
        self.core = self.make_core()
        for target, fake in (
            ("coscc.runner.ask.run_mod.run", self._run),
            ("coscc.runner.ask.triggers.tree_head", self._head),
            ("coscc.runner.ask.sessions_mod.exists", lambda sid, cwd: self.transcript),
        ):
            p = mock.patch(target, fake)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(ask._ASKING.clear)

    def make_core(self):
        """What the app holds, built again: a restart keeps only the run log."""
        return SimpleNamespace(
            config=SimpleNamespace(data_dir=self.data.root),
            ws=SimpleNamespace(
                check=lambda cwd: cwd,
                key=lambda cwd: cwd,
                name=lambda cwd: "proj",
                journal=lambda: self.journal,
                unit_dir=lambda cwd, unit: Path(cwd) / unit,
            ),
            sessions=None,
            models=SimpleNamespace(agent=lambda key, row: run_mod.Agent(key, row)),
            steps=SimpleNamespace(refuse_updating=lambda: None, hooks=HOOKS),
            autopilot=SimpleNamespace(today=lambda cwd: (0.0, 120.0)),
            bus=Bus(),
        )

    async def _head(self, tree):
        return self.head

    async def _run(self, agent, given, *, ctx, finish=None):
        self.runs.append((agent, given))
        grant = grant_of(agent.row, given.cwd, mcp=given.mcp, features=given.features)
        self.journal.started(
            given.workspace,
            given.unit,
            given.stage,
            "manual",
            started_by=given.started_by,
            agent=agent.key,
            run=given.run,
            grants=policy.record(grant),
            model=agent.model,
            **{**pack.stamp(agent.key), **given.start},
        )
        got = Run("done", "the answer", {"cost_usd": 0.03}, session=given.session_id or "s-new")
        extra = dict(await finish(got)) if finish else {}
        self.journal.finished(
            given.workspace,
            given.unit,
            given.stage,
            "done",
            agent=agent.key,
            run=given.run,
            session_id=got.session,
            cost_usd=0.03,
            cache_read_tokens=90_000,
            cache_creation_tokens=500,
            **extra,
        )
        yield ("done", got)

    def asked_run(self, run="p1", *, ws=None, stage="scan", ended=True, **start):
        """A run of `scan` that ended a moment ago, its grant as the row gives it now."""
        ws = ws or self.ws
        row = policy.row_for("scan")
        grant = policy.record(grant_of(row, ws))
        self.journal.started(
            ws,
            start.pop("unit", ""),
            stage,
            "manual",
            started_by="manual",
            agent=start.pop("agent", "scan"),
            run=run,
            **{
                "trigger": "manual",
                "grants": grant,
                "head": "h1",
                **pack.stamp("scan"),
                **start,
            },
        )
        if ended:
            self.journal.finished(
                ws,
                "",
                stage,
                "done",
                agent="scan",
                run=run,
                session_id="s1",
                cost_usd=0.4,
                cache_read_tokens=1000,
                cache_creation_tokens=60_000,
            )

    async def asked(self, text="why #31?", run="p1") -> ask.Asked:
        told = await ask.ask(self.core, self.ws, run, text)
        await asyncio.gather(*triggers._TASKS)
        return told

    async def refused(self, run="p1", text="why?") -> tuple[str, ...]:
        with self.assertRaises(Invalid) as e:
            await ask.ask(self.core, self.ws, run, text)
        self.assertEqual(self.runs, [])
        return getattr(e.exception, "reasons", ())


class AWarmRunIsResumed(_Asking):
    async def test_within_the_hour_it_goes_on_in_the_same_session_and_pays_only_its_turn(self):
        self.asked_run(scratch_as="b" * 32)
        told = await self.asked()
        self.assertEqual((told["resumed"], told["why"]), (True, ""))
        ((agent, given),) = self.runs
        self.assertEqual(given.session_id, "s1")
        # In the data root of the run that opened the session: the CLI's words stay the same.
        self.assertEqual(given.scratch_as, "b" * 32)
        self.assertTrue(given.prompt.startswith(ask.RESUMED))
        self.assertNotIn("- #", given.prompt)

    async def test_it_is_told_the_numbers_its_proposals_were_kept_under(self):
        from coscc.units import proposals

        self.asked_run()
        item = {"type": "fix", "slug": "pack-400s", "title": "Pack routes fail", "problem": "p"}
        (pid,) = proposals.add(self.data, self.ws, "scan", "", [item], run="p1")
        proposals.add(self.data, self.ws, "scan", "", [{**item, "slug": "x"}], run="other")
        await self.asked(f"why #{pid}?")
        ((_, given),) = self.runs
        self.assertIn(f"- #{pid} Pack routes fail\n", given.prompt)
        self.assertEqual(given.prompt.count("\n- #"), 1)
        self.assertTrue(given.prompt.endswith(f"why #{pid}?"))
        self.assertEqual(given.spent_before["cost_usd"], 0.4)
        self.assertEqual(given.spent_before["cache_creation_tokens"], 60_000)
        self.assertTrue(given.cache_hour)
        self.assertEqual((given.stage, given.start["parent_run"]), (ask.STAGE, "p1"))
        # A triggered row's follow-up holds its row's prompt, so `pack.stamp` names its skills.
        self.assertNotIn("skills", given.start)
        (end,) = [r for r in self.journal.records(self.ws, kinds=("end",)) if r["stage"] == "ask"]
        self.assertEqual((end["parent_run"], end["resumed"], end["agent"]), ("p1", True, "scan"))

    async def test_its_tools_are_offered_as_before_but_submit_and_any_write_are_refused(self):
        self.asked_run()
        await self.asked()
        ((agent, given),) = self.runs
        # The submit server stays mounted, so the cached tool list is the same.
        self.assertIn("cos", given.servers)
        grant = grant_of(agent.row, given.cwd, mcp=given.mcp, features=given.features)
        self.assertNotIn(policy.SUBMIT_TOOL, grant.mcp)
        self.assertEqual((grant.write, grant.branch, grant.helpers), ((), "", ()))
        gate = Gate(grant)
        for tool, args in (
            (policy.SUBMIT_TOOL, {"proposals": []}),
            ("Write", {"file_path": f"{self.ws}/x", "content": "x"}),
            ("Edit", {"file_path": f"{self.ws}/x", "old_string": "a", "new_string": "b"}),
            ("Agent", {"subagent_type": "worker", "prompt": "go"}),
        ):
            self.assertTrue(gate.refused(tool, args, None), tool)
        parent = policy.record(grant_of(policy.row_for("scan"), self.ws))
        self.assertLessEqual(set(grant.tools), set(parent["tools"]))
        self.assertLessEqual(set(grant.mcp), set(parent["mcp"]))

    async def test_a_restart_changes_nothing_it_decides_from(self):
        self.asked_run()
        self.core = self.make_core()
        ask._ASKING.clear()
        self.assertTrue((await self.asked())["resumed"])

    async def test_a_second_question_resumes_the_same_session_with_both_costs_before_it(self):
        self.asked_run()
        await self.asked()
        await self.asked("and #32?")
        given = self.runs[1][1]
        self.assertEqual(given.session_id, "s1")
        self.assertAlmostEqual(given.spent_before["cost_usd"], 0.43)
        thread = await ask.state(self.core, self.ws, "p1")
        self.assertEqual([f["question"] for f in thread["followups"]], ["why #31?", "and #32?"])
        self.assertEqual(thread["followups"][0]["cache_read_tokens"], 90_000)


class AnythingChangedStartsAfresh(_Asking):
    async def fresh(self) -> str:
        told = await self.asked()
        ((_, given),) = self.runs
        self.assertIsNone(given.session_id)
        self.assertIn("# The run you are asked about", given.prompt)
        self.assertIn("# The question\n\nwhy #31?", given.prompt)
        self.assertEqual(given.start["fresh_why"], told["why"])
        self.assertFalse(told["resumed"])
        return told["why"]

    async def test_over_55_minutes(self):
        self.asked_run()
        with mock.patch.object(ask, "WINDOW", timedelta(0)):
            self.assertIn("min ago", await self.fresh())

    async def test_the_row_edited(self):
        self.asked_run()
        pack.write("scan", "description", "Something else.")
        self.assertEqual(await self.fresh(), "the agent was edited since")

    async def test_the_grant_changed(self):
        self.asked_run(grants={"tools": ["Read"]})
        self.assertEqual(await self.fresh(), "its tools or grant changed since")

    async def test_the_head_moved(self):
        self.asked_run()
        self.head = "h2"
        self.assertEqual(await self.fresh(), "the code it read moved since")

    async def test_the_transcript_is_gone(self):
        self.asked_run()
        self.transcript = False
        self.assertEqual(await self.fresh(), "its transcript is gone")

    async def test_a_stopped_run_with_or_without_its_session(self):
        for run, session in (("p1", "s1"), ("p2", "")):
            self.asked_run(run, ended=False)
            self.journal.finished(
                self.ws, "", "scan", "cancelled", agent="scan", run=run, session_id=session
            )
            got = await ask.state(self.core, self.ws, run)
            self.assertEqual(
                (got["ask"]["may"], got["ask"]["resume"], got["ask"]["why"]),
                (True, False, "it was stopped before it ended"),
            )

    async def test_a_failed_run_with_a_session_starts_afresh_with_its_reason(self):
        self.asked_run("p1", ended=False)
        self.journal.finished(
            self.ws, "", "scan", "failed", agent="scan", run="p1", session_id="s1"
        )
        got = await ask.state(self.core, self.ws, "p1")
        self.assertEqual(
            (got["ask"]["may"], got["ask"]["resume"], got["ask"]["why"]),
            (True, False, "it failed before it ended"),
        )


class AStageStepIsAskedByAReader(_Asking):
    """A step's session could write: its own is never resumed, and its reader holds only reads."""

    def step(self):
        row = policy.row_for("impl")
        grant = policy.record(grant_of(row, self.ws, branch="feat/x"))
        self.journal.started(
            self.ws, "0001_a", "impl", "manual", agent="impl", run="p1", grants=grant, head="h1"
        )
        self.journal.finished(
            self.ws, "0001_a", "impl", "done", agent="impl", run="p1", session_id="s1"
        )
        return grant

    async def test_never_its_own_session_and_only_read_grep_glob(self):
        held = self.step()
        self.assertTrue(held["write"])
        told = await self.asked()
        ((agent, given),) = self.runs
        self.assertFalse(told["resumed"])
        self.assertIsNone(given.session_id)
        grant = grant_of(agent.row, given.cwd, mcp=given.mcp, features=given.features)
        self.assertEqual(grant.tools, policy.READ_TOOLS)
        self.assertEqual((grant.write, grant.branch, grant.helpers, grant.mcp), ((), "", (), ()))
        self.assertFalse(given.cache_hour)
        # It is told only `ASK_SYSTEM`, so its `start` names no skill: the stage's are not a use.
        self.assertEqual((agent.system, given.start["skills"]), (ask.ASK_SYSTEM, []))
        # Bash is not offered at all; what is offered and writes is refused.
        self.assertNotIn("Bash", grant.tools)
        for tool, args in (
            ("Write", {"file_path": f"{self.ws}/x", "content": "x"}),
            ("Edit", {"file_path": f"{self.ws}/x", "old_string": "a", "new_string": "b"}),
            (policy.SUBMIT_TOOL, {"artifact": "x"}),
        ):
            self.assertTrue(Gate(grant).refused(tool, args, None), tool)

    async def test_the_readers_own_session_is_resumed_by_the_next_question(self):
        self.step()
        await self.asked()
        await self.asked("and then?")
        self.assertEqual(self.runs[1][1].session_id, "s-new")


class ASandboxedBashNeverWritesTheTree(_Asking):
    """A triggered row holds Bash only in the OS sandbox (`pack._check_sandbox`); its runs and their
    follow-ups write only in the session's own data root. The refusals are the sandbox's, not the
    gate's words: `echo > f`, `tee`, `sed -i`, `mv`, `cp`, `git commit`, `git push` into the tree
    fail with "Read-only file system" under these settings, `git log` and `grep` run."""

    async def test_the_run_and_its_follow_up_deny_writes_in_the_workspace(self):
        from coscc.agent import sessions
        from coscc.config import Config

        base = policy.row_for("scan")
        boxed = replace(base, tools=(*base.tools, "Bash"), sandbox=("127.0.0.1:3000",))
        with mock.patch.object(policy, "row_for", lambda key, *a: boxed):
            self.asked_run()
            told = await self.asked()
        self.assertTrue(told["resumed"])
        ((agent, given),) = self.runs
        config = Config(data_dir=str(self.data.root), home="/home/o")
        for grant in (
            grant_of(boxed, self.ws),
            grant_of(agent.row, given.cwd, mcp=given.mcp, features=given.features),
        ):
            self.assertEqual((grant.write, grant.sandbox), ((), ("127.0.0.1:3000",)))
            with tempfile.TemporaryDirectory() as run:
                options = sessions._options(
                    config, self.ws, None, tools=list(grant.tools), gate=Gate(grant), data_dir=run
                )
                box = json.loads(options.settings or "")["sandbox"]
                self.assertEqual(box["filesystem"]["allowWrite"], [run])
            self.assertEqual(box["filesystem"]["denyWrite"], [self.ws])
            self.assertFalse(box["allowUnsandboxedCommands"])
            for line in ("git log --oneline -3", "grep -rn x .", "sqlite3 -readonly db 'select 1'"):
                self.assertFalse(Gate(grant).refused("Bash", {"command": line}, None), line)
            self.assertTrue(
                Gate(grant).refused("Write", {"file_path": f"{self.ws}/x", "content": "x"}, None)
            )

    async def test_a_triggered_row_cannot_hold_bash_outside_the_sandbox(self):
        problems = pack.check({"key": "x", "tools": {"Bash": "allow"}, "trigger": {"manual": True}})
        self.assertTrue(any("Bash" in p for p in problems), problems)


class ALookAtTheThread(_Asking):
    async def test_builds_no_server_and_reads_no_git(self):
        self.asked_run()
        heads = []

        async def head(tree):
            heads.append(tree)
            return self.head

        with (
            mock.patch("coscc.runner.ask.triggers.tree_head", head),
            mock.patch.object(ask.submit, "Collector") as collector,
        ):
            got = await ask.state(self.core, self.ws, "p1")
        self.assertEqual((got["ask"]["may"], got["ask"]["resume"]), (True, True))
        self.assertEqual((heads, collector.call_count), ([], 0))


class RefusedBeforeSpend(_Asking):
    async def test_no_such_run_another_workspaces_and_a_conversation(self):
        self.assertEqual(await self.refused("nope"), ("no-run",))
        self.asked_run("p2", ws="/elsewhere")
        self.assertEqual(await self.refused("p2"), ("no-run",))
        self.asked_run("p3", stage="chat", agent="leif")
        self.assertEqual(await self.refused("p3"), ("no-run",))

    async def test_a_run_still_going_and_a_question_already_being_answered(self):
        self.asked_run(ended=False)
        self.assertEqual(await self.refused(), ("unit-busy",))
        self.asked_run("p4")
        ask._ASKING.add("p4")
        self.assertEqual(await self.refused("p4"), ("unit-busy",))

    async def test_the_daily_cap_and_an_unreadable_spend(self):
        self.asked_run()
        self.core.autopilot.today = lambda cwd: (119.8, 120.0)
        self.assertEqual(await self.refused(), ("budget-reached",))
        self.core.autopilot.today = lambda cwd: None
        self.assertEqual(await self.refused(), ("unavailable",))

    async def test_no_words_and_too_many(self):
        self.asked_run()
        await self.refused(text=" ")
        await self.refused(text="x" * (ask.TEXT_MAX + 1))

    async def test_a_question_about_a_follow_up_is_asked_of_its_run(self):
        self.asked_run()
        await self.asked()
        child = self.runs[0][1].run
        await self.asked("more?", run=child)
        self.assertEqual(self.runs[1][1].start["parent_run"], "p1")


class OneRunIsStopped(_Asking):
    async def test_stop_cancels_that_run_alone(self):
        gate = asyncio.Event()

        async def held():
            await gate.wait()

        loop = asyncio.get_running_loop()
        triggers.spawn(loop, held(), "a", "/ws")
        triggers.spawn(loop, held(), "b", "/ws")
        self.assertFalse(triggers.stop_run("a", "/other"))
        self.assertFalse(triggers.stop_run("zzz", "/ws"))
        self.assertTrue(triggers.stop_run("a", "/ws"))
        await asyncio.sleep(0)
        names = {t.get_name(): t for t in triggers._TASKS}
        self.assertTrue(names["a"].cancelled() if "a" in names else True)
        self.assertFalse(names["b"].done())
        gate.set()
        await asyncio.gather(*triggers._TASKS, return_exceptions=True)


DRAFT = {"why": "a weekly reader", "agent": _draft_agent()}
ASKED = {"why": "which code?", "questions": [{"n": 1, "text": "?", "recommendation": "all"}]}


class ADraftsAnswersGoOnInItsSession(_Asking):
    """The answers to a draft's questions resume Dagaz's warm session with `submit` kept, and the
    new draft lands on that follow-up's `end`; cold, a new Dagaz run starts from task and answers."""

    async def _run(self, agent, given, *, ctx, finish=None):
        # As `run_mod._judge`: a session that submitted is done with its channel's object.
        if given.channel is not None:
            await given.channel.handle(DRAFT)

        async def judged(got):
            if given.channel is not None:
                got.output = given.channel.object()
            return await finish(got) if finish else {}

        async for item in super()._run(agent, given, ctx=ctx, finish=judged):
            yield item

    def asked_dagaz(self, run="d1", outcome="done", **start):
        row = policy.row_for("dagaz")
        self.journal.started(
            self.ws,
            "",
            "dagaz",
            "manual",
            started_by="manual",
            agent="dagaz",
            run=run,
            trigger="manual",
            grants=policy.record(grant_of(row, self.ws)),
            head="h1",
            **pack.stamp("dagaz"),
            **start,
        )
        self.journal.finished(
            self.ws,
            "",
            "dagaz",
            outcome,
            agent="dagaz",
            run=run,
            session_id="s1",
            cost_usd=0.18,
            **({"draft": ASKED} if outcome == "done" else {}),
        )

    async def answered(self, run="d1", task="code quality", text="1. All of it") -> ask.Asked:
        told = await ask.continue_draft(self.core, self.ws, run, task, text)
        await asyncio.gather(*triggers._TASKS)
        return told

    async def test_warm_it_resumes_with_submit_and_its_own_ceilings_and_keeps_the_draft(self):
        self.asked_dagaz()
        told = await self.answered()
        self.assertTrue(told["resumed"])
        ((agent, given),) = self.runs
        self.assertEqual(given.session_id, "s1")
        self.assertTrue(given.prompt.startswith(ask.ANSWERED))
        self.assertTrue(given.prompt.endswith("1. All of it"))
        grant = grant_of(agent.row, given.cwd, mcp=given.mcp, features=given.features)
        self.assertIn(policy.SUBMIT_TOOL, grant.mcp)
        self.assertEqual(agent.row, policy.row_for("dagaz"))
        (end,) = [r for r in self.journal.records(self.ws, kinds=("end",)) if r["stage"] == "ask"]
        self.assertEqual((end["parent_run"], end["draft"]), ("d1", DRAFT))
        (start,) = [
            r for r in self.journal.records(self.ws, kinds=("start",)) if r["stage"] == "ask"
        ]
        self.assertTrue(start["answers"])

    async def test_a_second_round_of_answers_resumes_the_first_rounds_session(self):
        self.asked_dagaz()
        await self.answered()
        await self.answered(text="2. Weekly")
        self.assertEqual(self.runs[1][1].session_id, "s1")

    async def test_cold_a_new_run_of_the_row_takes_the_task_and_the_answers(self):
        self.asked_dagaz()
        self.transcript = False
        began: list[tuple[str, str, str]] = []

        async def begin(core, key, workspace, unit="", *, by, reason="", text=""):
            began.append((key, by, text))
            return "r-new"

        with mock.patch.object(ask.triggers, "begin", begin):
            told = await self.answered()
        self.assertEqual((told["run"], told["resumed"]), ("r-new", False))
        self.assertEqual(
            began,
            [("dagaz", "manual", "code quality\n\n# Your questions, answered\n\n1. All of it")],
        )
        self.assertEqual(self.runs, [])

    async def test_a_failed_dagaz_run_or_one_that_drafted_without_asking_takes_no_answers(self):
        self.asked_dagaz(outcome="failed")
        with self.assertRaises(Invalid) as e:
            await ask.continue_draft(self.core, self.ws, "d1", "task", "1. yes")
        self.assertIn("asked no questions", str(e.exception))
        await self.answered_after_a_draft_without_questions()
        self.assertEqual(self.runs, [])

    async def answered_after_a_draft_without_questions(self):
        self.asked_dagaz(run="d2")
        await self.answered(run="d2")  # asks, so a follow-up drafts
        follow = self.runs[-1][1].run
        self.runs.clear()
        with self.assertRaises(Invalid):
            # The follow-up's own end holds a draft that asks nothing more.
            await ask.continue_draft(self.core, self.ws, follow, "task", "1. yes")

    async def test_a_run_that_drafts_nothing_takes_no_answers(self):
        self.asked_run()
        with self.assertRaises(Invalid):
            await ask.continue_draft(self.core, self.ws, "p1", "task", "1. yes")
        self.assertEqual(self.runs, [])

    async def test_refused_before_spend_without_words_or_over_the_cap(self):
        self.asked_dagaz()
        for task, text in (("", "1. yes"), ("task", " ")):
            with self.assertRaises(Invalid):
                await ask.continue_draft(self.core, self.ws, "d1", task, text)
        self.core.autopilot = SimpleNamespace(today=lambda cwd: (119.0, 120.0))
        with self.assertRaises(Invalid) as e:
            await ask.continue_draft(self.core, self.ws, "d1", "task", "1. yes")
        self.assertEqual(e.exception.reasons, ("budget-reached",))
        self.assertEqual(self.runs, [])
