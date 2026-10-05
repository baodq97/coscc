"""Tests for `coscc/runner/review.py`, split from `tests/runner/test_step.py`.

A round merged into `review.md` keeps the rounds before it, and a closing round is
checked before it is written."""

from __future__ import annotations

import unittest

from coscc.runner.review import merge_review, render_round, replace_new_rounds
from tests.runner.test_step import REVIEW_R1, _REVIEW_TWO_ROUNDS, incomplete_reply


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

    def test_the_header_takes_the_status_the_object_gave(self):
        """Not the session's word, and only the header's."""
        text = (
            "# Review: x\nPR: pr.md. Status: accepted.\n\n## Round 1\n\nStatus: draft.\n\n"
            "## Round 2\n\nStatus: accepted.\n"
        )
        out = replace_new_rounds(text, {1}, "## Round 2\n\nrendered", "changes-requested")
        self.assertEqual(
            out,
            "# Review: x\nPR: pr.md. Status: changes-requested.\n\n## Round 1\n\nStatus: draft.\n\n"
            "## Round 2\n\nrendered\n",
        )


class MergeReview(unittest.TestCase):
    def test_the_first_round_needs_nothing_on_disk(self):
        body = merge_review("", "# Review: x\nStatus: accepted.\n\n## Round 1\n\nok\n")
        self.assertEqual(body, "# Review: x\nStatus: accepted.\n\n## Round 1\n\nok\n")


class OpenFindings(unittest.TestCase):
    """The header, the last round's number and what it left open."""

    def test_the_last_round_only_and_its_open_findings(self):
        from coscc.runner.review import open_findings

        header, number, findings = open_findings(_REVIEW_TWO_ROUNDS)
        self.assertEqual(header, "PR: pr.md. Concluded by: agent. Status: changes-requested.")
        self.assertEqual(number, 2)
        self.assertEqual(
            findings,
            (
                "- F2 [answered] b.py:2 — low — ROUND-TWO-F2-ANSWERED\n"
                "- F3 [open] c.py:3 — high — ROUND-TWO-F3-OPEN\n"
                "  CONTINUATION-OF-F3\n"
                "- F4 [needs-person] d.py:4 — medium — ROUND-TWO-F4-PERSON"
            ),
        )

    def test_only_fixed_with_a_sha_is_left_out(self):
        # the loop counts `[answered]` closed only with a block under `## Answers`, and `[fixed]`
        # only with a sha, so both stay in the prompt.
        from coscc.runner.review import open_findings

        text = (
            "# Review: x\nStatus: changes-requested.\n\n## Round 1\n\n"
            "- F1 [fixed abc1234] a — high — GONE-1\n  GONE-1-MORE\n"
            "- F2 [answered] b — low — KEPT-2\n"
            "- F3 [claim-rejected] c — high — KEPT-3\n"
            "- F4 [who knows] d — high — KEPT-4\n"
            "- F5 [fixed] e — high — KEPT-5\n"
            "- F6 [Fixed 0123456789abcdef0123456789abcdef01234567] f — low — GONE-6\n"
        )
        _, _, findings = open_findings(text)
        self.assertNotIn("GONE", findings)
        for kept in ("KEPT-2", "KEPT-3", "KEPT-4", "KEPT-5"):
            self.assertIn(kept, findings)

    def test_a_status_quoted_in_a_round_is_not_the_header(self):
        from coscc.runner.review import open_findings

        text = (
            "# Review: x\nPR: pr.md. Status: accepted.\n\n## Round 1\n\n"
            "The header read\nStatus: changes-requested.\n\n- F1 [open] a — low — L\n"
        )
        header, number, _ = open_findings(text)
        self.assertEqual(header, "PR: pr.md. Status: accepted.")
        self.assertEqual(number, 1)


class AClosingRoundIsCheckedBeforeItIsWritten(unittest.TestCase):
    """Only an incomplete round, under `draft`, for the head the step ran on."""

    HEAD = "b" * 40

    def problem(self, reply, existing=REVIEW_R1):
        from coscc.runner.review import closing_round_problem

        return closing_round_problem(existing, reply, self.HEAD)

    def test_the_shape_names_is_accepted(self):
        self.assertIsNone(self.problem(incomplete_reply(self.HEAD)))
        self.assertIsNone(self.problem("```\n" + incomplete_reply(self.HEAD) + "```\n"))
        self.assertIsNone(self.problem(incomplete_reply(self.HEAD, number=1), existing=""))

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
            ("header", incomplete_reply(self.HEAD, status="changes-requested")),
            ("another head", incomplete_reply("c" * 40)),
            (
                "two rounds",
                incomplete_reply(self.HEAD)
                + "\n"
                + incomplete_reply(self.HEAD, number=3).split("\n\n", 1)[1],
            ),
            ("no round", "# Review: x\nStatus: draft.\n"),
            ("no status", "Tôi hết lượt."),
        ):
            with self.subTest(why=why):
                self.assertIsNotNone(self.problem(reply))
