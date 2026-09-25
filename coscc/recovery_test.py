"""Tests for ending, at start, the steps the app went down under (`0092` plan step 5).

The step is built the way the app leaves one: a real `Recorder` that took three turns and
then `abandon()`ed, and a `start` naming its `run` and the `pid` of a process that is gone.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from claude_agent_sdk import AssistantMessage, TextBlock

from coscc import events, recovery
from coscc.data import Data
from coscc.journal import Journal

UNIT = "0009_x"


def dead_pid() -> int:
    """A pid that was a process a moment ago and is not one now."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


class AStepTheAppWentDownUnder(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = self._tmp.name
        self.data = Data(self.d)
        self.journal = Journal(self.d, self.data)

    def tearDown(self):
        self._tmp.cleanup()

    def abandoned(self, run="r-1", pid=None, turns=3, start=True):
        """The records and rows a step leaves when the app dies under it."""
        rec = events.Recorder(run, self.data, str(self.journal.working_dir), "ws", UNIT, "impl")

        async def go():
            for n in range(turns):
                rec.message(AssistantMessage(content=[TextBlock(str(n))], model="m", message_id=f"m{n}"))
            await rec.abandon()

        asyncio.run(go())
        if start:
            self.journal.started(
                "ws", UNIT, "impl", "autonomous", run=run, **({"pid": pid} if pid is not None else {}),
            )
        return rec

    def ends(self):
        return self.journal.records("ws", UNIT, kind="end")

    def test_a_dead_pid_gets_a_failed_end_with_its_turns(self):
        rec = self.abandoned(pid=dead_pid())
        last_at = max(e["at"] for e in rec.events)
        self.assertEqual(recovery.recover(self.data), 1)
        [end] = self.ends()
        self.assertEqual(end["outcome"], "failed")
        self.assertEqual(end["detail"], recovery.DETAIL)
        self.assertEqual((end["turns"], end["run"]), (3, "r-1"))
        self.assertIs(end["cost_unknown"], True)
        self.assertIs(end["recovered"], True)
        self.assertNotIn("cost_usd", end)
        self.assertEqual(end["stage"], "impl")
        self.assertEqual(self.data.step_run("r-1")["ended_at"], last_at)
        [row] = self.journal.timeline("ws", UNIT)
        self.assertEqual((row["outcome"], row["run"]), ("failed", "r-1"))
        self.assertEqual(self.journal.open_starts("ws"), {})

    def test_a_run_with_no_turns_gets_no_turns_and_closes_at_its_start(self):
        rec = self.abandoned(pid=dead_pid(), turns=0)
        self.assertEqual(recovery.recover(self.data), 1)
        [end] = self.ends()
        self.assertNotIn("turns", end)
        self.assertEqual(self.data.step_run("r-1")["ended_at"], rec.started_at)

    def test_a_live_pid_is_left_alone(self):
        self.abandoned(pid=os.getpid())
        self.assertEqual(recovery.recover(self.data), 0)
        self.assertEqual(self.ends(), [])
        self.assertIsNone(self.data.step_run("r-1")["ended_at"])

    def test_someone_elses_process_is_alive(self):
        with mock.patch.object(recovery.os, "kill", side_effect=PermissionError()):
            self.assertTrue(recovery._alive(1))
        with mock.patch.object(recovery.os, "kill", side_effect=ProcessLookupError()):
            self.assertFalse(recovery._alive(1))

    def test_a_start_with_no_pid_is_left_alone(self):
        self.abandoned(pid=None)
        self.assertEqual(recovery.recover(self.data), 0)
        self.assertEqual(self.ends(), [])

    def test_a_run_with_no_start_is_left_alone(self):
        self.abandoned(start=False)
        self.assertEqual(recovery.recover(self.data), 0)
        self.assertEqual(self.ends(), [])

    def test_a_run_that_already_has_its_end_is_left_alone(self):
        self.abandoned(pid=dead_pid())
        self.journal.finished("ws", UNIT, "impl", "failed", run="r-1")
        self.assertEqual(recovery.recover(self.data), 0)
        self.assertEqual(len(self.ends()), 1)

    def test_a_second_recovery_writes_nothing_more(self):
        self.abandoned(pid=dead_pid())
        self.assertEqual(recovery.recover(self.data), 1)
        self.assertEqual(recovery.recover(self.data), 0)
        self.assertEqual(len(self.ends()), 1)

    def test_one_runs_error_does_not_stop_the_others(self):
        self.abandoned(run="r-1", pid=dead_pid())
        self.abandoned(run="r-2", pid=dead_pid())
        real = recovery._recover_one

        def first_breaks(data, row):
            if row["run"] == "r-1":
                raise RuntimeError("broken")
            return real(data, row)

        with mock.patch.object(recovery, "_recover_one", first_breaks):
            self.assertEqual(recovery.recover(self.data), 1)
        self.assertEqual([e["run"] for e in self.ends()], ["r-2"])

    def test_on_start_reads_the_configured_data_root(self):
        self.abandoned(pid=dead_pid())
        self.assertEqual(recovery.recover_on_start(mock.Mock(data_dir=self.d)), 1)


if __name__ == "__main__":
    unittest.main()
