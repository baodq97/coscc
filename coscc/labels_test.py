"""`coscc/labels.py`: the security surface, and every branch of `label_for`."""

from __future__ import annotations

import unittest
from pathlib import Path

from coscc import labels

HERE = Path(__file__).resolve().parent
STAGES = ["idea", "intent", "spec", "plan", "impl", "pr", "review", "ship"]


def plan(label: str | None, files: str = "- `coscc/board.py`: a change.") -> str:
    header = "# Plan: x\nIntent: intent.md. Status: accepted."
    if label is not None:
        header += f" Impl: {label}."
    return f"{header}\n\n## Files that change\n\n{files}\n\n## Order of work\n\n1. `coscc/policy.py` is only mentioned here.\n"


def ended(outcome: str, terminal: str | None = None, **extra) -> dict:
    rec = {"kind": "end", "stage": "impl", "outcome": outcome, **extra}
    if terminal is not None:
        rec["terminal"] = terminal
    return rec


class TheSecuritySurfaceIsOneConstant(unittest.TestCase):
    def test_exactly_these_four(self):
        # `0033 intent.md ## Answers, câu 2`.
        self.assertEqual(
            labels.SECURITY_SURFACE,
            ("coscc/policy.py", "coscc/sessions.py", ".claude/scripts/cos.mjs", ".claude/settings.json"),
        )

    def test_no_second_copy_in_the_code(self):
        # spec R2: a list that exists twice drifts.
        for path in sorted(HERE.glob("*.py")):
            if path.name.endswith("_test.py") or path.name == "labels.py":
                continue
            text = path.read_text(encoding="utf-8")
            self.assertFalse(all(p in text for p in labels.SECURITY_SURFACE), path.name)


class TheDeclaredLabel(unittest.TestCase):
    def test_the_two_words_and_everything_else(self):
        self.assertEqual(labels.declared(plan("routine")), "routine")
        self.assertEqual(labels.declared(plan("NOVEL")), "novel")
        self.assertEqual(labels.declared(plan("easy")), "missing")
        self.assertEqual(labels.declared(plan(None)), "missing")
        self.assertEqual(labels.declared(None), "missing")

    def test_only_the_header_is_read(self):
        text = "# Plan: x\nStatus: accepted.\n\n## Risks\n\nImpl: routine\n"
        self.assertEqual(labels.declared(text), "missing")


class TheListedPaths(unittest.TestCase):
    def test_normalised_and_bounded_by_the_next_heading(self):
        text = plan("routine", "- `./coscc/sessions.py`: `_options` (dòng 262-321).\n- `coscc/runner.py:737-1005`.")
        found = labels.listed_paths(text)
        self.assertIn("coscc/sessions.py", found)
        self.assertIn("coscc/runner.py", found)
        # Named only under `## Order of work`.
        self.assertNotIn("coscc/policy.py", found)

    def test_equality_not_substring(self):
        text = plan("routine", "- `coscc/policy_test.py`.\n- `.claude/settings.json.bak`.")
        self.assertEqual(labels.label_for("impl", STAGES, text, []), ("routine", "routine", "declared"))


class EveryBranchOfLabelFor(unittest.TestCase):
    def test_before_or_at_plan_there_is_no_label(self):
        for stage in ("idea", "intent", "spec", "plan"):
            self.assertEqual(labels.label_for(stage, STAGES, plan("routine"), []), (None, None, None), stage)

    def test_the_security_surface_forces_novel(self):
        for path in labels.SECURITY_SURFACE:
            text = plan("routine", f"- `{path}`: a change.")
            self.assertEqual(labels.label_for("impl", STAGES, text, []), ("routine", "novel", "forced"), path)

    def test_missing_runs_as_novel(self):
        self.assertEqual(labels.label_for("review", STAGES, plan(None), []), ("missing", "novel", "missing"))
        self.assertEqual(labels.label_for("impl", STAGES, None, []), ("missing", "novel", "missing"))

    def test_declared(self):
        self.assertEqual(labels.label_for("impl", STAGES, plan("routine"), []), ("routine", "routine", "declared"))
        self.assertEqual(labels.label_for("review", STAGES, plan("novel"), []), ("novel", "novel", "declared"))

    def test_a_max_turns_stop_escalates_impl(self):
        history = [{"kind": "start", "stage": "impl"}, ended("exhausted", "max_turns")]
        self.assertEqual(labels.label_for("impl", STAGES, plan("routine"), history),
                         ("routine", "novel", "escalated"))

    def test_only_impl_escalates(self):
        history = [{"kind": "start", "stage": "impl"}, ended("exhausted", "max_turns")]
        self.assertEqual(labels.label_for("review", STAGES, plan("routine"), history),
                         ("routine", "routine", "declared"))

    def test_a_budget_stop_does_not_escalate(self):
        # R4: a budget stop says nothing about the model.
        history = [{"kind": "start", "stage": "impl"}, ended("exhausted", "max_budget_usd")]
        self.assertEqual(labels.label_for("impl", STAGES, plan("routine"), history),
                         ("routine", "routine", "declared"))

    def test_an_old_end_reads_the_attempt_before_it(self):
        # An `end` written before `0033` has no `terminal`; `0019`'s `attempt` row does.
        history = [
            {"kind": "start", "stage": "impl"},
            {"kind": "attempt", "stage": "impl", "terminal": "max_turns"},
            ended("exhausted"),
        ]
        self.assertEqual(labels.label_for("impl", STAGES, plan("routine"), history),
                         ("routine", "novel", "escalated"))
        history[1]["terminal"] = "max_budget_usd"
        self.assertEqual(labels.label_for("impl", STAGES, plan("routine"), history)[2], "declared")

    def test_never_raises(self):
        self.assertEqual(labels.label_for("impl", STAGES, plan("routine"), [None]), ("missing", "novel", "missing"))


if __name__ == "__main__":
    unittest.main()
