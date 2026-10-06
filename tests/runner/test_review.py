"""Tests for `coscc/runner/review.py`, split from `tests/runner/test_step.py`.

A round merged into `review.md` keeps the rounds before it, and a closing round is
checked before it is written."""

from __future__ import annotations

import unittest

from coscc.runner.review import merge_review, render_round, replace_new_rounds
from tests.runner.test_step import REVIEW_R1, incomplete_reply


class ARoundIsWrittenFromItsObject(unittest.TestCase):
    """The verdict line, `### Findings` and `### Screens` are the app's; the rest of the round is
    the session's, in its places."""

    SECTION = (
        "## Round 7\n\nReviewed: 0000000. Verdict: pass.\n\n### What was checked\n\nall of it\n\n"
        "### Findings\n\n- F1 [fixed 1234567] a.py:1 — low — gone\n\n### What was not reviewed\n\nnothing\n"
    )
    OBJ = {
        "verdict": "changes-requested",
        "findings": [
            {
                "id": "F1",
                "state": "open",
                "fixed_in": "",
                "severity": "high",
                "rule": "S3",
                "path": "a.py",
                "lines": "1-4",
                "text": "still broken\nsecond line",
            },
        ],
        "screens": [
            {
                "path": ".screens/b.png",
                "size": "390x844",
                "address": "/board",
                "result": "no violation",
            }
        ],
    }
    SCREENS = {
        "taken": "e" * 40,
        "standard": ".claude/rules/ui-standard.md",
        "by": "Ansuz (agent, review)",
    }

    def test_the_decisions_are_the_objects_and_the_prose_is_kept_in_place(self):
        out = render_round(self.SECTION, 2, "f" * 40, self.OBJ, self.SCREENS)
        self.assertTrue(
            out.startswith(
                f"## Round 2\n\nReviewed: {'f' * 40}. Verdict: changes-requested.\n\n### What was checked"
            )
        )
        self.assertIn("- F1 [open] a.py:1-4 — high — S3 still broken\n  second line", out)
        self.assertNotIn("fixed 1234567", out)
        self.assertNotIn("0000000", out)
        self.assertLess(out.index("### Findings"), out.index("### What was not reviewed"))
        self.assertIn(
            f"Taken at: {'e' * 40}. Standard: .claude/rules/ui-standard.md. Looked at by: Ansuz (agent, review), from screenshots.",
            out,
        )
        self.assertIn("- .screens/b.png — 390×844 — /board — no violation", out)

    def test_only_the_new_rounds_are_replaced(self):
        text = "# Review: x\nStatus: draft.\n\n## Round 1\n\nold\n\n## Round 5\n\nnew\n\n## Answers\n\n### Câu 1\nkept\n"
        out = replace_new_rounds(text, {1}, "## Round 2\n\nrendered")
        self.assertEqual(
            out,
            "# Review: x\nStatus: draft.\n\n## Round 1\n\nold\n\n## Round 2\n\nrendered\n\n## Answers\n\n### Câu 1\nkept\n",
        )

    def test_the_header_stays_byte_for_byte(self):
        """The app writes no `Status:`: a header's own line is prose it never touches."""
        text = (
            "# Review: x\nPR: pr.md. Status: accepted.\n\n## Round 1\n\nStatus: draft.\n\n"
            "## Round 2\n\nStatus: accepted.\n"
        )
        out = replace_new_rounds(text, {1}, "## Round 2\n\nrendered")
        self.assertEqual(
            out,
            "# Review: x\nPR: pr.md. Status: accepted.\n\n## Round 1\n\nStatus: draft.\n\n"
            "## Round 2\n\nrendered\n",
        )
        self.assertEqual(out.split("## Round 1")[0], text.split("## Round 1")[0])


class MergeReview(unittest.TestCase):
    def test_the_first_round_needs_nothing_on_disk(self):
        body = merge_review("", "# Review: x\nStatus: accepted.\n\n## Round 1\n\nok\n")
        self.assertEqual(body, "# Review: x\nStatus: accepted.\n\n## Round 1\n\nok\n")

    def test_the_header_is_the_replys(self):
        body = merge_review(
            "# Review: x\nStatus: changes-requested.\n\n## Round 1\n\nF1\n",
            "# Review: x\nStatus: accepted.\n\n## Round 2\n\nok\n",
        )
        self.assertEqual(
            body, "# Review: x\nStatus: accepted.\n\n## Round 1\n\nF1\n\n## Round 2\n\nok\n"
        )


class AClosingRoundIsCheckedBeforeItIsWritten(unittest.TestCase):
    """Only an incomplete round, for the head the step ran on, whatever its header says."""

    HEAD = "b" * 40

    def problem(self, reply, existing=REVIEW_R1):
        from coscc.runner.review import closing_round_problem

        return closing_round_problem(existing, reply, self.HEAD)

    def test_the_shape_names_is_accepted(self):
        self.assertIsNone(self.problem(incomplete_reply(self.HEAD)))
        self.assertIsNone(self.problem("```\n" + incomplete_reply(self.HEAD) + "```\n"))
        self.assertIsNone(self.problem(incomplete_reply(self.HEAD, number=1), existing=""))

    def test_the_header_line_is_read_as_leniently_as_the_loop_reads_it(self):
        line = f"Reviewed: {self.HEAD}. Verdict: incomplete."
        for written in (line.rstrip("."), line.lower(), line.replace(": ", ":  ")):
            with self.subTest(written=written):
                self.assertIsNone(self.problem(incomplete_reply(self.HEAD).replace(line, written)))
        self.assertIsNotNone(self.problem(incomplete_reply("c" * 40)))

    def test_a_round_with_no_status_line_passes(self):
        reply = incomplete_reply(self.HEAD).replace("Status: draft.", "").replace("Status: ", "")
        self.assertNotIn("Status:", reply)
        self.assertIsNone(self.problem(reply))

    def test_only_the_round_after_the_last_one_on_disk(self):
        # Round 5 on one round would be written, and `ship` would call it renumbered for good.
        self.assertIn("## Round 2", self.problem(incomplete_reply(self.HEAD, number=5)))

    def test_everything_else_is_refused(self):
        for why, reply in (
            ("pass", incomplete_reply(self.HEAD, verdict="pass")),
            ("changes-requested", incomplete_reply(self.HEAD, verdict="changes-requested")),
            (
                "a section missing",
                incomplete_reply(self.HEAD, sections=("Reviewed so far", "Findings")),
            ),
            (
                "out of order",
                incomplete_reply(
                    self.HEAD, sections=("Findings", "Reviewed so far", "What was not reviewed")
                ),
            ),
            ("another head", incomplete_reply("c" * 40)),
            (
                "two rounds",
                incomplete_reply(self.HEAD)
                + "\n"
                + incomplete_reply(self.HEAD, number=3).split("\n\n", 1)[1],
            ),
            ("no round", "# Review: x\n"),
            ("nothing", "Tôi hết lượt."),
        ):
            with self.subTest(why=why):
                self.assertIsNotNone(self.problem(reply))
