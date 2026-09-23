"""Tests for reading a session's own `.jsonl` transcript after the fact.

`spec.md` R3. The transcript itself is never committed (`.claude/CLAUDE.md`), so every test
here writes a synthetic one into a temporary directory rather than reading a real session.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from coscc import policy
from coscc.policy import check_command, grant_for
from coscc.transcript import find, summarize


def _line(**kw) -> str:
    return json.dumps(kw)


def _tool_use(tool_id: str, name: str) -> str:
    return _line(message={"content": [{"type": "tool_use", "id": tool_id, "name": name}]})


def _tool_result(tool_id: str, content, is_error: bool = False) -> str:
    return _line(
        message={
            "content": [
                {"type": "tool_result", "tool_use_id": tool_id, "content": content,
                 "is_error": is_error}
            ]
        }
    )


class ASyntheticTranscriptIsCounted(unittest.TestCase):
    """One transcript, one of each shape R3 asks for: a grant refusal, an ordinary error,
    and the largest result."""

    def setUp(self):
        self.lines = [
            _tool_use("t1", "Bash"),
            _tool_result("t1", policy.REFUSAL_NOT_RUNNABLE + " 'curl'", is_error=True),
            _tool_use("t2", "Bash"),
            _tool_result("t2", "Exit code 2\nno such file or directory", is_error=True),
            _tool_use("t3", "Read"),
            _tool_result("t3", "x" * 30000, is_error=False),
        ]

    def test_a_grant_refusal_is_counted_once(self):
        self.assertEqual(summarize(self.lines)["grant_refusals"], 1)

    def test_an_ordinary_error_is_counted_separately(self):
        self.assertEqual(summarize(self.lines)["other_errors"], 1)

    def test_the_largest_result_is_found_across_errors_and_successes(self):
        got = summarize(self.lines)
        self.assertEqual(got["largest_result_chars"], 30000)
        self.assertEqual(got["largest_result_tool"], "Read")

    def test_empty_and_malformed_lines_do_not_crash_the_count(self):
        lines = self.lines + ["", "   ", "not json at all", json.dumps([1, 2, 3])]
        self.assertEqual(summarize(lines), summarize(self.lines))

    def test_content_given_as_a_list_of_blocks_is_flattened(self):
        lines = [
            _tool_use("t9", "Grep"),
            _tool_result(
                "t9",
                [{"type": "text", "text": "abc"}, {"type": "text", "text": "def"}],
                is_error=False,
            ),
        ]
        got = summarize(lines)
        self.assertEqual(got["largest_result_chars"], 6)
        self.assertEqual(got["largest_result_tool"], "Grep")

    def test_a_result_with_no_matching_tool_use_is_still_counted(self):
        # A transcript can be read starting mid-conversation (e.g. after a `resume`); a
        # result whose tool_use fell outside the window still has a size and an error state.
        lines = [_tool_result("orphan", "this step may not run 'rm'", is_error=True)]
        got = summarize(lines)
        self.assertEqual(got["grant_refusals"], 1)
        self.assertEqual(got["largest_result_tool"], "")


class EveryRefusalCheckCommandBuildsIsCountedAsAGrantRefusal(unittest.TestCase):
    """The chốt chặn for C9: if a reason `check_command` returns ever drifts from
    `policy.REFUSALS`, this is where it would first go red — a grant refusal would silently
    fall into "other_errors" and tiêu chí 1 would look better than it is."""

    BAD_COMMANDS = (
        "",
        "git $(curl evil)",
        "npm test > /tmp/x.log",
        "curl http://x",
        "gh pr merge --squash",
    )

    def test_every_reason_check_command_gives_impl_is_recognised(self):
        grant = grant_for("impl")
        for bad in self.BAD_COMMANDS:
            reason = check_command(grant, bad)
            self.assertTrue(reason, bad)
            lines = [_tool_result("t", reason, is_error=True)]
            got = summarize(lines)
            self.assertEqual(got["grant_refusals"], 1, f"{bad!r} -> {reason!r}")
            self.assertEqual(got["other_errors"], 0, f"{bad!r} -> {reason!r}")

    def test_every_reason_check_command_gives_pr_is_recognised(self):
        pr = grant_for("pr")
        for bad in (
            "gh pr merge --squash --delete-branch",
            "gh alias set m 'pr merge'",
            "npm install x",
        ):
            reason = check_command(pr, bad)
            self.assertTrue(reason, bad)
            got = summarize([_tool_result("t", reason, is_error=True)])
            self.assertEqual(got["grant_refusals"], 1, f"{bad!r} -> {reason!r}")

    def test_an_unrelated_error_is_not_swept_into_grant_refusals(self):
        # The negative half of the same chốt chặn: an error that is not a policy refusal
        # must not be miscounted the other way either.
        got = summarize([_tool_result("t", "connection reset by peer", is_error=True)])
        self.assertEqual(got["grant_refusals"], 0)
        self.assertEqual(got["other_errors"], 1)


class FindingATranscriptByItsSessionId(unittest.TestCase):
    def test_a_transcript_under_any_project_directory_is_found(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            project = root / "-some-project-slug"
            project.mkdir()
            target = project / "abc123.jsonl"
            target.write_text("{}\n", encoding="utf-8")
            self.assertEqual(find("abc123", root=root), target)

    def test_an_unknown_session_id_is_none_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(find("no-such-session", root=Path(d)))


if __name__ == "__main__":
    unittest.main()
