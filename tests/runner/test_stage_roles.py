"""The runtime reads what a state is, never which state it is.

The process below gives the built-in agents other state names, so a rule that still named a state
(`impl`, `spike`, `review`, `pr`) would miss. One test per rule: `by: session` gets the unit's
branch, `by: scratch` its scratch directory, a review output kind withholds the transcript of a
stopped step, and `open-pr` opens no session. A state bound to no agent is refused `no-stage`.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.agent import pack
from coscc.agent.steps import Running
from coscc.runner import steps as steps_mod
from coscc.runner.reply import RunError
from coscc.runner.prompt import compose_prompt
from coscc.runner.queue import Refused
from coscc.runner.step import Runner, _bound
from coscc.runner.steps import _agent_key, step_cwd
from coscc.store.journal import Journal
from coscc.units import states
from tests.runner.test_step import UNIT, make_unit

ALT = "coscc-sdlc/alt"
PROCESS = {
    "start": "brief",
    "end": "shipped",
    "states": {
        "brief": {"agent": "intent", "next": [{"to": "build"}]},
        "build": {"agent": "impl", "next": [{"to": "probe"}]},
        "probe": {"agent": "spike", "next": [{"to": "look"}]},
        "look": {"agent": "review", "next": [{"to": "open"}]},
        "open": {"action": "open-pr", "next": [{"to": "land"}]},
        "land": {"action": "merge"},
        "gone": {},
        "write": {"agent": "spec", "next": [{"to": "build"}]},
    },
}


def with_alt():
    real = pack.processes()
    return mock.patch.object(pack, "processes", return_value={**real, ALT: PROCESS})


class ARoleIsWhatTheStateIs(unittest.TestCase):
    def setUp(self):
        patch = with_alt()
        patch.start()
        self.addCleanup(patch.stop)

    def test_by_session_gets_the_branch(self):
        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d), intent_md="Status: accepted.\nI")
            args = (d, Path(d) / ".cos" / UNIT, UNIT)
            built, _ = compose_prompt(*args, "build", "build.md", branch="feat/x", process=ALT)
            other, _ = compose_prompt(*args, "brief", "brief.md", branch="feat/x", process=ALT)
        self.assertIn("git push origin feat/x", built)
        self.assertNotIn("git push origin", other)

    def test_by_scratch_gets_the_scratch_directory(self):
        self.assertEqual(step_cwd("probe", "/w/tree", Path("/store/u"), "/data/s", ALT), "/data/s")
        self.assertEqual(step_cwd("build", "/w/tree", Path("/store/u"), "/data/s", ALT), "/w/tree")
        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d), intent_md="Status: accepted.\nI", spec_md="Status: accepted.\nS")
            args = (d, Path(d) / ".cos" / UNIT, UNIT)
            probe, _ = compose_prompt(*args, "probe", "probe.md", worktree="/w", process=ALT)
            build, _ = compose_prompt(*args, "build", "build.md", worktree="/w", process=ALT)
        self.assertIn("# Where you work", probe)
        self.assertIn("`probe.md`", probe)
        self.assertNotIn("# Where you work", build)

    def test_a_merge_runs_in_the_units_folder(self):
        self.assertEqual(step_cwd("land", "/w/tree", Path("/store/u"), None, ALT), "/store/u")

    def test_kind_review_is_told_its_commit_and_keeps_its_rounds(self):
        self.assertEqual(_bound(ALT, "look", ""), ("review", True))
        self.assertEqual(_bound(ALT, "build", ""), ("impl", False))
        self.assertEqual(steps_mod._rounds_before({"rounds": [{"n": 2}]}, "look", ALT), {2})
        self.assertIsNone(steps_mod._rounds_before({"rounds": [{"n": 2}]}, "build", ALT))
        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d), intent_md="Status: accepted.\nI")
            args = (d, Path(d) / ".cos" / UNIT, UNIT)
            look, _ = compose_prompt(*args, "look", "look.md", head="abc1234", process=ALT)
        self.assertIn("# The commit you are reviewing", look)

    def test_a_stopped_review_state_withholds_its_transcript(self):
        class Waits:
            def __init__(self):
                self.release = asyncio.Event()

            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                yield ("chunk", "thinking\n")
                await self.release.wait()

        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d), intent_md="Status: accepted.\nI", spec_md="Status: accepted.\nS")
            running = Running(d, UNIT, "look", "")
            journal = Journal(d, d)
            runner = Runner(sessions=Waits(), journal=journal)

            async def go():
                out = []

                async def drive():
                    async for item in runner.run(
                        workspace=d,
                        directory=Path(d) / ".cos" / UNIT,
                        journal_key=d,
                        unit=UNIT,
                        stage="look",
                        artifact="look.md",
                        mode="manual",
                        process=ALT,
                        running=running,
                    ):
                        out.append(item)

                running.task = asyncio.create_task(drive())
                while not out:
                    await asyncio.sleep(0)
                running.stop_requested, running.stopped_by = True, "Lan"
                await running.handle.close()
                running.task.cancel()
                try:
                    await running.task
                except asyncio.CancelledError:
                    pass

            asyncio.run(go())
            [end] = [r for r in journal.records() if r["kind"] == "end"]
        self.assertEqual((end["outcome"], end["review_md"]), ("stopped", "withheld"))

    def test_open_pr_is_an_action_and_opens_no_session(self):
        self.assertEqual(states.action_of(ALT, "open"), "open-pr")
        self.assertEqual(_agent_key(ALT, "open"), "")
        self.assertEqual(_agent_key(ALT, "land"), "")
        self.assertEqual(states.by_of(ALT, "open"), "")
        self.assertEqual(states.states_where(action="open-pr"), ("pr", "open"))

    def test_a_state_bound_to_no_agent_or_action_is_refused(self):
        with self.assertRaises(Refused) as caught:
            _agent_key(ALT, "gone")
        self.assertEqual(caught.exception.reasons, ("no-stage",))
        with self.assertRaises(Exception):
            _bound(ALT, "gone", "")


class AStateRunsTheRowItIsBoundTo(unittest.TestCase):
    """A process may bind a state to an agent of another name: the one row grants, ceilings,
    prompt and stamp."""

    def setUp(self):
        patch = with_alt()
        patch.start()
        self.addCleanup(patch.stop)

    def start_of(self, stage: str, process: str) -> tuple[dict, dict]:
        """The `start` record and the session's arguments of one step on `process`."""

        class Capture:
            seen: dict = {}

            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                self.seen = {"max_turns": max_turns, **kw}
                yield ("chunk", "# Spec: x\nStatus: accepted.\n")
                yield ("done", {"session_id": "s", "terminal_reason": "success", "cost": {}})

        with tempfile.TemporaryDirectory() as d:
            directory = make_unit(Path(d), intent_md="Status: accepted.\nI")
            journal = Journal(d, d)
            sessions = Capture()

            async def go():
                async for _ in Runner(sessions=sessions, journal=journal).run(
                    workspace=d,
                    directory=directory,
                    journal_key=d,
                    unit=UNIT,
                    stage=stage,
                    # The reply's title is read from the artifact's name, as for any prose step.
                    artifact="spec.md",
                    mode="manual",
                    process=process,
                ):
                    pass

            asyncio.run(go())
            [start] = [r for r in journal.records() if r["kind"] == "start"]
        return start, sessions.seen

    def test_the_bound_agents_ceilings_and_stamp_are_the_runs(self):
        own, own_kw = self.start_of("spec", pack.DEFAULT_PROCESS)
        bound, bound_kw = self.start_of("write", ALT)
        self.assertEqual(bound["row_hash"], pack.hash_of(pack.row("spec")))
        self.assertEqual(bound["process"], ALT)
        for field in ("row_hash", "max_turns", "max_budget_usd", "model", "effort"):
            self.assertEqual(bound[field], own[field], field)
        self.assertEqual(bound["grants"]["granted"], own["grants"]["granted"])
        self.assertEqual(bound_kw["max_turns"], own_kw["max_turns"])
        self.assertEqual(bound_kw.get("agent"), own_kw.get("agent"))

    def test_a_state_that_runs_nothing_is_a_run_error_not_an_empty_row(self):
        with tempfile.TemporaryDirectory() as d:
            directory = make_unit(Path(d), intent_md="Status: accepted.\nI")

            async def go():
                async for _ in Runner(sessions=mock.Mock(), journal=Journal(d, d)).run(
                    workspace=d,
                    directory=directory,
                    journal_key=d,
                    unit=UNIT,
                    stage="gone",
                    artifact="gone.md",
                    mode="manual",
                    process=ALT,
                ):
                    pass

            with self.assertRaises(RunError):
                asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
