"""Tests for the board, run against the only real set of work units there is.

This repository's own `.cos/` is the fixture. That is deliberate: the thing most likely to
break here is not the parsing but the *agreement* between this module and
`.claude/scripts/cos.mjs`, and a hand-built fixture would keep passing after the two drift
apart. `spec.md` C8 is the concern these tests stand against.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from coscc import board, harness
from coscc.board import Unavailable

REPO = Path(__file__).resolve().parent.parent

STAGES = ["idea", "intent", "spec", "plan", "impl", "pr", "review", "ship"]


def run(coro):
    return asyncio.run(coro)


def _by_stage(u) -> dict:
    return {row["stage"]: row["status"] for row in u["stages"]}


class TheRepositoryReadsAsABoard(unittest.TestCase):
    def test_every_unit_carries_all_eight_stages(self):
        data = run(board.read(REPO))
        self.assertEqual(data["stages"], STAGES)
        self.assertEqual(data["count"], len([d for d in (REPO / ".cos").iterdir() if d.is_dir()]))
        for unit in data["units"]:
            got = [row["stage"] for row in unit["stages"]]
            self.assertEqual(got, STAGES, f"{unit['name']} is missing a stage")

    def test_a_stage_with_no_artifact_reads_as_not_started(self):
        data = run(board.read(REPO))
        # Chosen by shape rather than by number: any unit closed under the old three-stage
        # loop will do, and naming one pins the test to a numbering that can change.
        unit = next(u for u in data["units"] if _by_stage(u)["plan"] == "done")
        by_stage = _by_stage(u=unit)
        self.assertEqual(by_stage["intent"], "accepted")
        self.assertEqual(by_stage["plan"], "done")
        for stage in ("impl", "pr", "review", "ship"):
            self.assertEqual(by_stage[stage], "not started")

    def test_the_next_action_is_carried_through_not_recomputed(self):
        # Two answers to "what next" is exactly the drift `board.py` exists to avoid, so
        # the value must be the script's, verbatim.
        data = run(board.read(REPO))
        unit = next(u for u in data["units"] if _by_stage(u)["plan"] == "done")
        self.assertEqual(unit["next"], "finished")
        self.assertFalse(unit["blocked"])

    def test_idea_is_marked_optional_so_a_reader_need_not_know_the_rule(self):
        data = run(board.read(REPO))
        first = data["units"][0]["stages"][0]
        self.assertEqual(first["stage"], "idea")
        self.assertTrue(first["optional"])


class AnEmptyWorkspaceIsAnAnswerNotAFailure(unittest.TestCase):
    def test_a_directory_with_no_cos_reports_why_rather_than_raising(self):
        with tempfile.TemporaryDirectory() as d:
            data = run(board.read(d))
            self.assertEqual(data["units"], [])
            self.assertEqual(data["count"], 0)
            self.assertIn("no .cos/", data["empty_because"])

    def test_a_cos_directory_with_no_units_says_something_different(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".cos").mkdir()
            data = run(board.read(d))
            self.assertEqual(data["units"], [])
            self.assertIn("holds no work units", data["empty_because"])

    def test_a_directory_that_is_not_there_names_itself(self):
        data = run(board.read("/nonexistent-workspace-for-a-test"))
        self.assertEqual(data["units"], [])
        self.assertIn("no such directory", data["empty_because"])

    def test_a_populated_board_leaves_empty_because_unset(self):
        data = run(board.read(REPO))
        self.assertIsNone(data["empty_because"])


class TheWorkspaceCopyOfTheHarnessIsNeverRun(unittest.TestCase):
    def test_a_script_planted_in_the_workspace_is_ignored(self):
        """A workspace is a cloned repository, so its `.claude/` is someone else's code.

        The planted script writes a file and exits non-zero. If the board ever ran the
        copy it found in the workspace, both the marker and the failure would show.
        """
        with tempfile.TemporaryDirectory() as d:
            workspace = Path(d)
            scripts = workspace / ".claude" / "scripts"
            scripts.mkdir(parents=True)
            marker = workspace / "PLANTED"
            (scripts / "cos.mjs").write_text(
                "import { writeFileSync } from 'node:fs'\n"
                f"writeFileSync({str(marker)!r}, 'ran')\n"
                "process.exit(3)\n"
            )
            (workspace / ".cos").mkdir()

            data = run(board.read(workspace))

            self.assertFalse(marker.exists(), "the workspace's own cos.mjs was executed")
            self.assertEqual(data["units"], [])
            self.assertIn("holds no work units", data["empty_because"])


class AnUnreadableBoardRaisesRatherThanReturningEmpty(unittest.TestCase):
    def test_a_missing_harness_script_is_not_reported_as_an_empty_board(self):
        # "No units" and "I could not look" are different answers, and a page that shows
        # the first when it means the second is the failure this test names.
        # Both roots are moved, not just one: `harness.root()` falls back to the
        # checkout, so blanking only the packaged side would still find a real script.
        originals = (harness.PACKAGE_HARNESS, harness.CHECKOUT_HARNESS)
        harness.PACKAGE_HARNESS = Path("/nonexistent/packaged")
        harness.CHECKOUT_HARNESS = Path("/nonexistent/checkout")
        try:
            with self.assertRaises(Unavailable) as caught:
                run(board.read(REPO))
            self.assertIn("harness script is missing", str(caught.exception))
        finally:
            harness.PACKAGE_HARNESS, harness.CHECKOUT_HARNESS = originals

    def test_the_child_environment_carries_no_secrets(self):
        env = board._child_env()
        self.assertEqual(set(env), {"PATH", "HOME", "LC_ALL", "NO_COLOR"})


if __name__ == "__main__":
    unittest.main()
