"""`0092` R12. Every way a step dies, after three turns, and what the run log says about it.

Each test runs a real `Runner.run` with a real recorder on a temporary `cos.db`, feeding it
three `AssistantMessage`s with three `message_id`s, then kills the step its own way: an SDK
error, a Stop, a ceiling, a timeout, a killed CLI. The app going down under a step is the
one case the runner cannot write, so that one is `recovery.recover` at the next start.

A test fails when the step's `end` is missing; when its `turns` differs from the `turn` rows
of its `run` in `step_events`; when it has a `cost_usd` although no `done` came; when it says
`done`; when the board's chip or cost reads other than the `end`; or when the backlog puts
the unit on the wrong side of measured and undetermined. `test_done_unchanged` is R10.
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import unittest
from pathlib import Path

from claude_agent_sdk import AssistantMessage, TextBlock

from coscc import backlog, events, recovery, steps
from coscc.data import Data
from coscc.journal import Journal, last_runs, timelines_of, totals_of
from coscc.runner import Runner
from coscc.state import _cell_label, _usd

UNIT = "0009_a-step-that-dies"
STAGES = ["idea", "intent", "spec", "spike", "plan", "impl", "pr", "review", "ship"]
TURNS = 3
REPLY = "# Spec: x\nStatus: accepted.\n"


def _turns(recorder) -> None:
    for n in range(TURNS):
        recorder.message(AssistantMessage(content=[TextBlock(f"turn {n}")], model="m", message_id=f"m{n}"))


class Dies:
    """Three turns, one chunk, then `then`: an exception to raise, a `done` payload to send,
    or `"wait"` for a release a Stop never gives."""

    def __init__(self, then):
        self.then = then
        self.release = asyncio.Event()

    async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
        _turns(kw["step"].recorder)
        # Its own line: since `0099` narration run into the title is no artifact.
        yield ("chunk", "thinking\n")
        if self.then == "wait":
            await self.release.wait()
            return
        if isinstance(self.then, BaseException):
            raise self.then
        if self.then.get("terminal_reason") == "success":
            yield ("chunk", REPLY)
        yield ("done", self.then)


class EveryWayAStepDies(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = self._tmp.name
        (Path(self.d) / ".cos" / UNIT).mkdir(parents=True)
        (Path(self.d) / ".cos" / UNIT / "intent.md").write_text("Status: accepted.\nI", encoding="utf-8")
        self.data = Data(self.d)
        self.journal = Journal(self.d, self.data)

    def tearDown(self):
        self._tmp.cleanup()

    def _step(self, then, act=None):
        """Run the step to its end; `act(registry, running, sessions)` runs once it has begun."""
        registry = steps.Registry()
        running = registry.claim(self.d, UNIT, "spec")
        recorder = events.Recorder("run-1", self.data, str(self.journal.working_dir), self.d, UNIT, "spec")
        running.handle.recorder = recorder
        sessions = Dies(then)

        async def go():
            out = []

            async def drive():
                async for item in Runner(sessions=sessions, journal=self.journal).run(
                    workspace=self.d, directory=Path(self.d) / ".cos" / UNIT, journal_key=self.d,
                    unit=UNIT, stage="spec", artifact="spec.md", stages=STAGES, mode="manual",
                    running=running,
                ):
                    out.append(item)

            running.task = asyncio.create_task(drive())
            while not out:
                await asyncio.sleep(0)
            if act is not None:
                await act(registry, running, sessions)
            await running.task

        asyncio.run(go())
        return self._end()

    def _end(self):
        ends = self.journal.records(self.d, UNIT, kind="end")
        self.assertEqual(len(ends), 1, "the step has no end, or more than one")
        return ends[0]

    def _says_what_the_end_says(self, end, cost_known: bool) -> None:
        """R12's failure list, checked against one `end`."""
        self.assertNotEqual(end["outcome"], "done")
        self.assertEqual(end["turns"], self.data.step_turns("run-1"))
        self.assertEqual(end["turns"], TURNS)
        self.assertEqual(end["run"], "run-1")
        if cost_known:
            self.assertNotIn("cost_unknown", end)
        else:
            self.assertNotIn("cost_usd", end)
            self.assertIs(end["cost_unknown"], True)

        rows = timelines_of(self.journal.records(self.d))[UNIT]
        last = last_runs(rows)["spec"]
        said_cost = f"${end['cost_usd']:.2f}" if cost_known else "cost unknown"
        self.assertEqual(
            _cell_label({"status": "not started", "last_run": last}),
            (f"not started · {end['outcome']} · {TURNS} turns · {said_cost}", "amber"),
        )
        self.assertEqual(_usd(totals_of(rows)), f"${end['cost_usd']:.2f}" if cost_known else "unknown")

        units = [{"name": UNIT, "next": "finished"}]
        by_unit = timelines_of(self.journal.records(self.d))
        self.assertEqual(UNIT in backlog.measured(by_unit, units), cost_known)
        self.assertEqual(backlog.undetermined(by_unit, units), [] if cost_known else [UNIT])

    # -- R4 a --------------------------------------------------------------------------

    def test_a_sdk_error(self):
        try:
            from claude_agent_sdk._errors import CLIJSONDecodeError

            error: Exception = CLIJSONDecodeError('{"type": "assist', ValueError("unterminated string"))
        except (ImportError, TypeError):
            error = RuntimeError("the stream broke")
        end = self._step(error)
        self.assertEqual(end["outcome"], "failed")
        self.assertIn(type(error).__name__, end["detail"])
        self._says_what_the_end_says(end, cost_known=False)

    # -- R4 b --------------------------------------------------------------------------

    def test_b_stop(self):
        async def stop(registry, running, sessions):
            registry.request_stop(running.workspace, running.unit, "owner")
            await running.handle.close()
            running.task.cancel()

        end = self._step("wait", stop)
        self.assertEqual((end["outcome"], end["stopped_by"]), ("stopped", "owner"))
        self._says_what_the_end_says(end, cost_known=False)

    # -- R4 c --------------------------------------------------------------------------

    def test_c_exhausted(self):
        end = self._step({"session_id": "s", "terminal_reason": "error_max_turns",
                          "cost": {"turns": 7, "cost_usd": 0.4}})
        self.assertEqual(end["outcome"], "exhausted")
        self.assertEqual((end["cli_turns"], end["cost_usd"]), (7, 0.4))
        self._says_what_the_end_says(end, cost_known=True)

    def test_c_timeout(self):
        end = self._step(TimeoutError("the CLI did not answer"))
        self.assertEqual(end["outcome"], "failed")
        self.assertIn("TimeoutError", end["detail"])
        self._says_what_the_end_says(end, cost_known=False)

    # -- R4 d --------------------------------------------------------------------------

    def test_d_killed(self):
        from claude_agent_sdk._errors import ProcessError

        # What `spike.md ## U1` measured the SDK to raise 0.01-0.03 s after a SIGKILL.
        end = self._step(ProcessError("Command failed with exit code -9", exit_code=-9))
        self.assertEqual(end["outcome"], "failed")
        self.assertIn("ProcessError", end["detail"])
        self._says_what_the_end_says(end, cost_known=False)

    # -- R4 e --------------------------------------------------------------------------

    def test_e_app_went_down(self):
        recorder = events.Recorder("run-1", self.data, str(self.journal.working_dir), self.d, UNIT, "spec")

        async def went_down():
            _turns(recorder)
            await recorder.abandon()

        asyncio.run(went_down())
        gone = subprocess.Popen(["true"])
        gone.wait()
        self.journal.started(self.d, UNIT, "spec", "manual", run="run-1", pid=gone.pid)
        self.assertEqual(recovery.recover(self.data), 1)
        end = self._end()
        self.assertEqual((end["outcome"], end["recovered"]), ("failed", True))
        self._says_what_the_end_says(end, cost_known=False)

    # -- R10 ---------------------------------------------------------------------------

    def test_done_unchanged(self):
        end = self._step({"session_id": "s", "terminal_reason": "success",
                          "cost": {"turns": 2, "cost_usd": 0.25}})
        self.assertEqual(end["outcome"], "done")
        # The CLI's `num_turns`, not the three the recorder stored, and nothing new beside it.
        self.assertEqual((end["turns"], end["cost_usd"]), (2, 0.25))
        for field in ("cli_turns", "cost_unknown", "turns_from", "recovered"):
            self.assertNotIn(field, end)


if __name__ == "__main__":
    unittest.main()
