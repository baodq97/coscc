"""Tests for `coscc/runner/reply.py`, split from `tests/runner/test_step.py`.

A reply is checked before it becomes a file: its opening title and its fences."""

from __future__ import annotations

import unittest

from coscc.runner.reply import RunError, unfence


class AReplyIsUnfencedBeforeItBecomesAFile(unittest.TestCase):
    def test_an_empty_reply_is_refused(self):
        for bad in ("", "   \n\n"):
            with self.assertRaises(RunError):
                unfence(bad)

    def test_a_fenced_reply_is_unwrapped_rather_than_rejected(self):
        got = unfence("```markdown\n# Spec: x\nIntent: intent.md.\n```")
        self.assertTrue(got.startswith("# Spec: x"))
        self.assertNotIn("```", got)


class AReplyOpensWithItsTitleOnly(unittest.TestCase):
    """Where the artifact starts in what a session said, and the one thing its opening must carry."""

    PLAN = "# Plan: x\nIntent: intent.md.\n\n## Body\n"

    def cut(self, text, artifact="plan.md"):
        from coscc.runner.reply import unfence
        from coscc.runner.reply import from_title

        return from_title(unfence(text), artifact)

    def problem(self, text, artifact="plan.md"):
        from coscc.runner.reply import opening_problem

        return opening_problem(text, artifact)

    def test_a_title_passes_and_no_status_line_is_asked_for(self):
        self.assertIsNone(self.problem(self.PLAN))
        self.assertIsNone(self.problem("# Plan: x\n\n## Body\n"))
        self.assertIsNone(self.problem("# Plan: x\n"))

    def test_a_reply_with_no_title_is_refused_by_name(self):
        self.assertEqual(self.problem("## Plan: x\nIntent: i.\n"), "no `# Plan:` title")
        self.assertEqual(self.problem("## Body\n\nR1.\n"), "no `# Plan:` title")

    def test_another_stages_title_is_no_title(self):
        self.assertEqual(self.problem("# Spec: x\nIntent: i.\n"), "no `# Plan:` title")
        self.assertIsNone(self.problem("# Spec: x\nIntent: i.\n", "spec.md"))

    def test_the_last_title_is_where_the_artifact_starts(self):
        text = "Nháp:\n# Plan: nháp\nIntent: i.\n\nĐọc thêm.\n" + self.PLAN
        self.assertEqual(self.cut(text), self.PLAN.strip())

    def test_a_title_inside_a_fence_in_the_body_is_not_chosen(self):
        text = self.PLAN + "\n```markdown\n# Plan: <title>\nIntent: i.\n```\n"
        self.assertEqual(self.cut(text), text.strip())

    def test_a_reply_wrapped_whole_in_a_fence_is_unwrapped_then_cut(self):
        got = self.cut("```markdown\n" + self.PLAN + "```\n")
        self.assertEqual(got, self.PLAN.strip())
        self.assertIsNone(self.problem(got))

    def test_text_with_no_title_comes_back_whole(self):
        from coscc.runner.reply import from_title

        self.assertEqual(from_title("## Body\nR1.\n", "plan.md"), "## Body\nR1.\n")

    def test_the_reason_names_the_artifact_what_is_missing_and_the_blocks(self):
        from coscc.runner.reply import opening_reason

        got = opening_reason("plan.md", "no `# Plan:` title", 3)
        for part in ("plan.md", "no `# Plan:` title", "3 blocks"):
            self.assertIn(part, got)
        self.assertIn("1 block)", opening_reason("plan.md", "x", 1))
        self.assertNotIn("(", opening_reason("plan.md", "x", None))
