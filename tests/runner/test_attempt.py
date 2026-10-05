"""Tests for `coscc/runner/attempt.py`, split from `tests/runner/test_step.py`.

What a failed attempt left, as the next step and the board are told it."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from types import SimpleNamespace

from coscc.agent.policy import BACKGROUND_REFUSAL, grant_for
from coscc.runner.attempt import Denials, permission_gate


class DenialsCountTheBackgroundRuns(unittest.TestCase):
    def test_a_background_refusal_is_counted_apart_from_the_rest(self):
        denials = Denials()
        denials.record("Bash", f"run_in_background is refused: {BACKGROUND_REFUSAL}")
        denials.record(
            "Bash", f"`&` at character 13 runs a command in the background: {BACKGROUND_REFUSAL}"
        )
        denials.record("Bash", "this step may not run 'curl'")
        self.assertEqual((denials.count, denials.background), (3, 2))


class AReplyWithoutItsOpeningIsRefusedByItsClass(unittest.TestCase):
    """The refusal a repair turn follows is told apart by its class, and says what it said
    before."""

    def test_a_reply_without_its_opening_raises_an_opening_error(self):
        import tempfile
        from pathlib import Path

        from coscc.runner.attempt import _write_artifact
        from coscc.runner.reply import OpeningError, RunError

        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(OpeningError) as caught:
                _write_artifact(
                    Path(d), "plan.md", "## Files that change\n\nStatus: accepted.\n", blocks=2
                )
            self.assertEqual(caught.exception.problem, "no `# Plan:` title")
            self.assertEqual(
                str(caught.exception),
                "plan.md lacks its opening: no `# Plan:` title (the session replied in 2 blocks)",
            )
            # The other refusals keep their plain class.
            with self.assertRaises(RunError) as other:
                _write_artifact(Path(d), "plan.md", "  ")
            self.assertNotIsInstance(other.exception, OpeningError)
            self.assertFalse((Path(d) / "plan.md").exists())


class AHelpersCallIsDecidedAsTheSessionsOwn(unittest.TestCase):
    """A helper's tool call reaches the same callback, marked only by `agent_id`."""

    def test_a_refused_command_is_refused_from_a_helper_too(self):
        with tempfile.TemporaryDirectory() as ws:
            denials = Denials()
            gate = permission_gate(grant_for("impl"), ws, denials)
            call = {"command": "curl https://example.com"}
            for context in (SimpleNamespace(agent_id=None), SimpleNamespace(agent_id="a1")):
                with self.subTest(agent_id=context.agent_id):
                    result = asyncio.run(gate("Bash", call, context))
                    self.assertEqual(type(result).__name__, "PermissionResultDeny")
            allowed = asyncio.run(
                gate("Bash", {"command": "npm test"}, SimpleNamespace(agent_id="a1"))
            )
            self.assertEqual(type(allowed).__name__, "PermissionResultAllow")
            self.assertEqual(denials.count, 2)
