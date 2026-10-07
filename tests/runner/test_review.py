"""Tests for `coscc/runner/review.py`, split from `tests/runner/test_step.py`.

A round merged into `review.md` keeps the rounds before it."""

from __future__ import annotations

import unittest

from coscc.runner.review import merge_review, render_round, replace_new_rounds


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
                "criterion": "S3",
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

    def test_the_criteria_are_a_table_before_the_findings(self):
        obj = {
            **self.OBJ,
            "criteria": [
                {"criterion": "S3", "source": "no internals", "met": "no", "evidence": "a.py:1"}
            ],
        }
        out = render_round(self.SECTION, 2, "f" * 40, obj, self.SCREENS)
        self.assertIn(
            "### Criteria\n\n| Criterion | Met | Source | Evidence |\n|---|---|---|---|\n"
            "| S3 | no | no internals | a.py:1 |",
            out,
        )
        self.assertLess(out.index("### Criteria"), out.index("### Findings"))

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
