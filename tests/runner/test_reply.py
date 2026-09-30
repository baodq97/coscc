"""Tests for `coscc/runner/reply.py`, split from `tests/runner/test_step.py`.

A reply is checked before it becomes a file: its `Status:` line, its opening and its
fences."""

from __future__ import annotations

import unittest

from coscc.runner.reply import RunError, check_reply


class AReplyIsCheckedBeforeItBecomesAFile(unittest.TestCase):
    def test_a_reply_with_no_status_line_is_refused(self):
        with self.assertRaises(RunError):
            check_reply("# Spec: something\n\nNo status anywhere.")

    def test_an_empty_reply_is_refused(self):
        for bad in ("", "   \n\n"):
            with self.assertRaises(RunError):
                check_reply(bad)

    def test_a_fenced_reply_is_unwrapped_rather_than_rejected(self):
        got = check_reply("```markdown\n# Spec: x\nStatus: accepted.\n```")
        self.assertTrue(got.startswith("# Spec: x"))
        self.assertNotIn("```", got)

    def test_a_good_reply_comes_back_with_a_trailing_newline(self):
        got = check_reply("# Spec: x\nStatus: accepted.")
        self.assertTrue(got.endswith("\n"))


class TheOpeningIsCheckedBeforeAnArtifactIsWritten(unittest.TestCase):
    """Where the artifact starts in what a session said, and the two things its opening must carry."""

    PLAN = "# Plan: x\nIntent: intent.md. Status: accepted.\n\n## Body\n"

    def cut(self, text, artifact="plan.md"):
        from coscc.runner.reply import _unfence
        from coscc.runner.reply import from_title

        return from_title(_unfence(text), artifact)

    def problem(self, text, artifact="plan.md"):
        from coscc.runner.reply import opening_problem

        return opening_problem(text, artifact)

    def test_a_title_and_a_status_line_pass(self):
        self.assertIsNone(self.problem(self.PLAN))
        self.assertIsNone(self.problem("# Plan: x\n\nIntent: intent.md. Status: draft.\n"))

    def test_each_thing_missing_is_named(self):
        self.assertEqual(
            self.problem("## Plan: x\nIntent: i. Status: accepted.\n"), "no `# Plan:` title"
        )
        self.assertEqual(
            self.problem("# Plan: x\n\n## Body\nStatus: accepted.\n"),
            "no `Status:` line in its header",
        )
        self.assertEqual(
            self.problem("## Body\n\nR1.\n"),
            "no `# Plan:` title and no `Status:` line in its header",
        )

    def test_another_stages_title_is_no_title(self):
        self.assertEqual(self.problem("# Spec: x\nStatus: accepted.\n"), "no `# Plan:` title")
        self.assertIsNone(self.problem("# Spec: x\nStatus: accepted.\n", "spec.md"))

    def test_the_last_title_is_where_the_artifact_starts(self):
        text = "Nháp:\n# Plan: nháp\nStatus: draft.\n\nĐọc thêm.\n" + self.PLAN
        self.assertEqual(self.cut(text), self.PLAN.strip())

    def test_a_title_inside_a_fence_in_the_body_is_not_chosen(self):
        text = self.PLAN + "\n```markdown\n# Plan: <title>\nStatus: draft.\n```\n"
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

        got = opening_reason("plan.md", "no `Status:` line in its header", 3)
        for part in ("plan.md", "no `Status:` line", "3 blocks"):
            self.assertIn(part, got)
        self.assertIn("1 block)", opening_reason("plan.md", "x", 1))
        self.assertNotIn("(", opening_reason("plan.md", "x", None))


class ARepairTurnIsAskedForTheOpening(unittest.TestCase):
    """The typed refusal a repair turn follows, and its prompt."""

    PROBLEM = "no `# Plan:` title and no `Status:` line in its header"

    def test_an_opening_error_is_a_run_error_carrying_its_problem(self):
        from coscc.runner.reply import OpeningError

        e = OpeningError("plan.md lacks its opening: x", "x")
        self.assertIsInstance(e, RunError)
        self.assertEqual((str(e), e.problem), ("plan.md lacks its opening: x", "x"))

    def test_the_opening_prompt_names_what_was_missing_and_the_title(self):
        from coscc.runner.reply import opening_prompt

        prompt = opening_prompt("plan.md", self.PROBLEM)
        self.assertIn(self.PROBLEM, prompt)
        self.assertIn("`# Plan:`", prompt)
        self.assertIn("the whole of `plan.md`", prompt)
        self.assertIn("no code fence", prompt)

    def test_the_opening_prompt_suggests_no_status(self):
        import re
        from coscc.runner.reply import opening_prompt

        for artifact in ("idea.md", "intent.md", "spec.md", "plan.md", "review.md"):
            self.assertIsNone(
                re.search(r"Status:\s*[A-Za-z]", opening_prompt(artifact, self.PROBLEM)), artifact
            )

    def test_the_review_opening_prompt_asks_for_the_new_round_only(self):
        from coscc.runner.reply import opening_prompt

        prompt = opening_prompt("review.md", "no `# Review:` title")
        self.assertIn("new round only", prompt)
        self.assertIn("do not copy them", prompt)
        self.assertNotIn("the whole of", prompt)
