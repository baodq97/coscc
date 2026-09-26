"""Tests for `coscc/runner_review.py`, split from `coscc/runner_test.py` (`0095`).

A round merged into `review.md` keeps the rounds before it, and a closing round is
checked before it is written.
"""

from __future__ import annotations

import unittest

from coscc.runner_review import merge_review
from coscc.runner_test import REVIEW_R1, _REVIEW_TWO_ROUNDS, incomplete_reply


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


class OpenFindings(unittest.TestCase):
    """`0094` plan step 3: the header, the last round's number and what it left open."""

    def test_the_last_round_only_and_its_open_findings(self):
        from coscc.runner import open_findings

        header, number, findings = open_findings(_REVIEW_TWO_ROUNDS)
        self.assertEqual(header, "PR: pr.md. Concluded by: agent. Status: changes-requested.")
        self.assertEqual(number, 2)
        self.assertEqual(findings, (
            "- F2 [answered] b.py:2 — low — ROUND-TWO-F2-ANSWERED\n"
            "- F3 [open] c.py:3 — high — ROUND-TWO-F3-OPEN\n"
            "  CONTINUATION-OF-F3\n"
            "- F4 [needs-person] d.py:4 — medium — ROUND-TWO-F4-PERSON"
        ))

    def test_only_fixed_with_a_sha_is_left_out(self):
        # Review round 1 F6: `cos.mjs` counts `[answered]` closed only with a block under
        # `## Answers`, and `[fixed]` only with a sha, so both stay in the prompt.
        from coscc.runner import open_findings

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

    def test_no_round_reads_the_file_below_its_header_up_to_answers(self):
        from coscc.runner import open_findings

        text = (
            "# Review: x\nPR: pr.md. Status: changes-requested.\n\n## Findings\n\n"
            "- F1 [open] a.py:3 — high — ONE\n\n## Answers\n\n- F9 [open] not a finding\n"
        )
        header, number, findings = open_findings(text)
        self.assertEqual(header, "PR: pr.md. Status: changes-requested.")
        self.assertIsNone(number)
        self.assertEqual(findings, "- F1 [open] a.py:3 — high — ONE")

    def test_a_status_quoted_in_a_round_is_not_the_header(self):
        from coscc.runner import open_findings

        text = (
            "# Review: x\nPR: pr.md. Status: accepted.\n\n## Round 1\n\n"
            "The header read\nStatus: changes-requested.\n\n- F1 [open] a — low — L\n"
        )
        header, number, _ = open_findings(text)
        self.assertEqual(header, "PR: pr.md. Status: accepted.")
        self.assertEqual(number, 1)


class AClosingRoundIsCheckedBeforeItIsWritten(unittest.TestCase):
    """`0085` R4, R5: only an incomplete round, under `draft`, for the head the step ran on."""

    HEAD = "b" * 40

    def problem(self, reply, existing=REVIEW_R1):
        from coscc.runner import closing_round_problem
        return closing_round_problem(existing, reply, self.HEAD)

    def test_the_shape_r4_names_is_accepted(self):
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
            ("a section missing", incomplete_reply(self.HEAD, sections=("Reviewed so far", "Findings"))),
            ("out of order", incomplete_reply(self.HEAD, sections=("Findings", "Reviewed so far", "What was not reviewed"))),
            ("header", incomplete_reply(self.HEAD, status="changes-requested")),
            ("another head", incomplete_reply("c" * 40)),
            ("two rounds", incomplete_reply(self.HEAD) + "\n" + incomplete_reply(self.HEAD, number=3).split("\n\n", 1)[1]),            ("no round", "# Review: x\nStatus: draft.\n"),
            ("no status", "Tôi hết lượt."),
        ):
            with self.subTest(why=why):
                self.assertIsNotNone(self.problem(reply))
