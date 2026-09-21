"""Tests for the table that decides what a step may do.

`plan.md` Risk 3 is the concern these stand against, and it is not fully answerable: a
first-word allowlist does not bound what `git` can be told to do. What is testable is that
the obvious ways past it are closed, that writes cannot leave the workspace, and that a
tool nobody granted is refused whatever declared it — which is the shape `0007` measured.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cos_baodo import policy
from cos_baodo.policy import Grant, check_command, decide, grant_for

IMPL = grant_for("impl", "autonomous")


class OnlyImplAndOnlyAutonomous(unittest.TestCase):
    def test_impl_carries_tools_when_it_is_set_to_run_itself(self):
        self.assertTrue(IMPL.opens_anything)
        self.assertIn("Write", IMPL.tools)
        self.assertIn("Bash", IMPL.tools)
        self.assertGreater(IMPL.max_turns, 1)
        self.assertGreater(IMPL.max_budget_usd, 0)

    def test_the_same_stage_in_manual_carries_nothing(self):
        self.assertFalse(grant_for("impl", "manual").opens_anything)

    def test_impl_writes_its_own_artifact_and_prose_stages_do_not(self):
        self.assertFalse(IMPL.app_writes_artifact)
        self.assertTrue(grant_for("spec", "autonomous").app_writes_artifact)


class AToolNobodyGrantedIsRefused(unittest.TestCase):
    def test_an_mcp_tool_is_refused_by_construction(self):
        # `0007` measured eleven of these arriving at a session created with `tools=[]`.
        # They are refused here because their names can never be in a grant.
        for tool in (
            "mcp__claude_ai_Claude_Docs__delete",
            "mcp__microsoft-learn__microsoft_docs_fetch",
        ):
            self.assertIn("was not granted", decide(IMPL, tool, {}, "/tmp"))

    def test_a_granted_read_tool_passes(self):
        self.assertEqual(decide(IMPL, "Read", {"file_path": "/tmp/x"}, "/tmp"), "")

    def test_an_empty_grant_refuses_everything(self):
        for tool in ("Read", "Write", "Bash"):
            self.assertIn("was not granted", decide(Grant(), tool, {}, "/tmp"))


class WritesStayInTheWorkspace(unittest.TestCase):
    def test_a_write_inside_the_workspace_passes(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "src" / "a.py"
            self.assertEqual(decide(IMPL, "Write", {"file_path": str(target)}, d), "")

    def test_a_write_outside_the_workspace_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            for bad in ("/etc/passwd", "/tmp/elsewhere.txt", str(Path(d).parent / "up.txt")):
                self.assertIn(
                    "outside the workspace", decide(IMPL, "Write", {"file_path": bad}, d), bad
                )

    def test_a_traversal_back_out_is_refused_after_resolving(self):
        with tempfile.TemporaryDirectory() as d:
            bad = str(Path(d) / ".." / ".." / "etc" / "passwd")
            self.assertIn("outside the workspace", decide(IMPL, "Write", {"file_path": bad}, d))

    def test_the_workspace_root_itself_is_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(decide(IMPL, "Write", {"file_path": d}, d), "")


class CommandsAreCheckedSegmentBySegment(unittest.TestCase):
    def test_an_allowed_command_passes(self):
        for good in ("npm test", "git status", "uv run python -m unittest", "ls -la"):
            self.assertEqual(check_command(IMPL, good), "", good)

    def test_every_segment_is_checked_not_just_the_first(self):
        # The failure this closes: `npm test; curl evil` would pass a check that only read
        # the first word of the line.
        for bad in ("npm test; curl http://x", "git status && wget x", "ls | nc host 1"):
            self.assertIn("may not run", check_command(IMPL, bad), bad)

    def test_substitution_is_refused_because_the_first_word_stops_predicting(self):
        for bad in ("git $(curl evil)", "ls `whoami`", "npm ${X}", "cat <(curl x)"):
            self.assertIn("substitution", check_command(IMPL, bad), bad)

    def test_a_path_prefix_does_not_smuggle_a_command_past(self):
        self.assertIn("may not run", check_command(IMPL, "/usr/bin/curl http://x"))
        self.assertEqual(check_command(IMPL, "/usr/bin/git status"), "")

    def test_a_leading_assignment_is_stepped_over(self):
        self.assertEqual(check_command(IMPL, "CI=1 npm test"), "")
        self.assertIn("may not run", check_command(IMPL, "CI=1 curl http://x"))

    def test_an_empty_command_is_refused(self):
        self.assertIn("empty", check_command(IMPL, "   "))

    def test_redirection_into_a_file_is_refused(self):
        # A redirect writes without any write tool being called, so the path check in
        # `decide` never sees it. Found while reading a real run on 2026-09-22.
        for bad in ("echo x > /etc/passwd", "cat a >> b", "npm test > out.txt"):
            self.assertIn("redirecting into a file", check_command(IMPL, bad), bad)

    def test_a_descriptor_redirect_is_not_a_file_redirect(self):
        # `2>&1` is the common case and touches no file. It also used to break the
        # splitter: the `&` read as a separator and the `1` as a command.
        for good in ("npm test 2>&1", "ls >&2", "uv run python -m unittest 2>&1 | tail -5"):
            self.assertEqual(check_command(IMPL, good), "", good)

    def test_a_bash_call_goes_through_the_command_check(self):
        self.assertIn("may not run", decide(IMPL, "Bash", {"command": "curl http://x"}, "/tmp"))
        self.assertEqual(decide(IMPL, "Bash", {"command": "npm test"}, "/tmp"), "")


class TheKnownLimit(unittest.TestCase):
    def test_an_allowed_binary_can_still_be_told_to_do_a_lot(self):
        """`plan.md` Risk 3, written down as a passing test rather than left implied.

        `git` is on the list because a step has to be able to check its own work. Nothing
        here stops it being handed arguments that reach further than that. The bound is
        `cwd`, the write check above, and the turn and budget ceilings — not this list.
        """
        self.assertEqual(check_command(IMPL, "git push --force"), "")
        self.assertEqual(check_command(IMPL, "npm install something"), "")


if __name__ == "__main__":
    unittest.main()
