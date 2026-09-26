"""`0110` plan step 2: which findings of earlier reviews an `impl` step is handed."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coscc import priorfindings
from coscc.priorfindings import CAP_BYTES

PLAN = (
    "# Plan: x\nIntent: intent.md. Status: accepted.\n\n"
    "## Files that change\n\n- `coscc/service.py`. The call.\n- `coscc/runner.py:297-317`.\n\n"
    "## Order of work\n\n1. `coscc/state.py` is not a file this plan changes.\n"
)


def review(*rounds: list[str], answers: str = "") -> str:
    text = "# Review: x\nStatus: changes-requested.\n\n- F9 [open] coscc/service.py:1 — high — above every round\n"
    for i, lines in enumerate(rounds, 1):
        text += f"\n## Round {i}\nReviewed: abc.\n\n### Findings\n\n" + "\n".join(lines) + "\n"
    if answers:
        text += f"\n## Answers\n\n### F1\n{answers}\n"
    return text


class WhichLinesAreKept(unittest.TestCase):
    def test_a_line_naming_a_file_of_the_plan_is_kept_with_its_unit_and_round(self):
        section, record = priorfindings.select(
            {"0054_a": review(["- F3 [open] coscc/service.py:1842 — medium — a thing"])}, PLAN
        )
        self.assertEqual(section, "- 0054 Round 1 F3 [open] coscc/service.py:1842 — medium — a thing")
        self.assertEqual(record, {"bytes": len(section.encode()), "lines": 1, "units": 1, "dropped": 0})

    def test_the_path_is_matched_whole_and_backticks_are_dropped(self):
        section, _ = priorfindings.select({"0054_a": review([
            "- F1 [open] coscc/service.py.bak:3 — low — a longer path",
            "- F2 [fixed abc1234] `coscc/runner.py:12` — high — in backticks",
            "- F3 [open] coscc/state.py:4 — high — named only under Order of work",
            "- F4 [open] S3 — high — no path at all",
            "  - F5 [open] coscc/service.py:1 — high — indented, not a finding line",
        ])}, PLAN)
        self.assertEqual(section, "- 0054 Round 1 F2 [fixed abc1234] `coscc/runner.py:12` — high — in backticks")

    def test_only_the_finding_line_is_taken_not_what_follows_it(self):
        section, _ = priorfindings.select(
            {"0054_a": review(["- F1 [open] coscc/runner.py:1 — high — short", "  more words", "Why: long"])}, PLAN
        )
        self.assertEqual(section.splitlines(), ["- 0054 Round 1 F1 [open] coscc/runner.py:1 — high — short"])

    def test_a_line_outside_a_round_or_under_answers_is_not_read(self):
        section, record = priorfindings.select(
            {"0054_a": review(answers="- F1 [open] coscc/service.py:2 — high — in an answer")}, PLAN
        )
        self.assertEqual((section, record["lines"]), ("", 0))

    def test_a_plan_without_the_section_is_nothing_and_a_zero_record(self):
        reviews = {"0054_a": review(["- F1 [open] coscc/service.py:1 — high — x"])}
        self.assertEqual(
            priorfindings.select(reviews, "# Plan\n\n## Order of work\n\n- coscc/service.py\n"),
            ("", {"bytes": 0, "lines": 0, "units": 0, "dropped": 0}),
        )


class TheOrderAndTheCap(unittest.TestCase):
    def test_by_path_then_unit_then_round_and_a_repeat_is_kept_once(self):
        section, record = priorfindings.select({
            "0080_b": review(["- F1 [open] coscc/service.py:1 — high — b1"],
                             ["- F1 [open] coscc/runner.py:2 — high — b2",
                              "- F1 [open] coscc/runner.py:2 — high — b2"]),
            "0035_a": review(["- F2 [open] coscc/service.py:9 — low — a1"]),
        }, PLAN)
        self.assertEqual(section.splitlines(), [
            "- 0080 Round 2 F1 [open] coscc/runner.py:2 — high — b2",
            "- 0035 Round 1 F2 [open] coscc/service.py:9 — low — a1",
            "- 0080 Round 1 F1 [open] coscc/service.py:1 — high — b1",
        ])
        self.assertEqual((record["lines"], record["units"], record["dropped"]), (3, 2, 0))

    def test_over_the_cap_the_newest_units_whole_lines_are_kept_in_order(self):
        filler = "x" * 900  # about 960 bytes a line: six fit under the cap, seven do not
        reviews = {
            f"{n:04d}_u": review([f"- F1 [open] coscc/service.py:{n} — high — {filler}"]) for n in range(1, 11)
        }
        section, record = priorfindings.select(reviews, PLAN)
        lines = section.splitlines()
        self.assertLessEqual(len(section.encode()), CAP_BYTES)
        self.assertEqual([x[2:6] for x in lines], ["0005", "0006", "0007", "0008", "0009", "0010"])
        self.assertTrue(all(x.endswith(filler) for x in lines))
        self.assertEqual((record["lines"], record["units"], record["dropped"]), (6, 6, 4))
        self.assertEqual(record["bytes"], len(section.encode()))


class ReadingTheStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cos = Path(self.tmp.name) / ".cos"
        for name in ("0054_a", "0110_me"):
            (self.cos / name).mkdir(parents=True)
            (self.cos / name / "review.md").write_text(
                review([f"- F1 [open] coscc/service.py:1 — high — from {name}"]), encoding="utf-8"
            )
        (self.cos / "0060_no-review").mkdir()
        self.plan = self.cos / "0110_me" / "plan.md"
        self.plan.write_text(PLAN, encoding="utf-8")

    def test_the_unit_itself_and_a_unit_without_a_review_are_skipped(self):
        got = priorfindings.for_step(self.cos, ["0054_a", "0060_no-review", "0110_me"], self.plan, "0110_me")
        self.assertEqual(got["prior_findings"], "- 0054 Round 1 F1 [open] coscc/service.py:1 — high — from 0054_a")
        self.assertEqual(got["prior_findings_record"]["units"], 1)

    def test_a_review_that_cannot_be_read_is_an_error_in_the_record_never_a_raise(self):
        (self.cos / "0054_a" / "review.md").unlink()
        (self.cos / "0054_a" / "review.md").mkdir()
        got = priorfindings.for_step(self.cos, ["0054_a"], self.plan, "0110_me")
        self.assertEqual(got["prior_findings"], "")
        self.assertEqual(got["prior_findings_record"]["bytes"], 0)
        self.assertIn("error", got["prior_findings_record"])

    def test_a_plan_that_cannot_be_read_is_an_error_in_the_record(self):
        got = priorfindings.for_step(self.cos, ["0054_a"], self.cos / "missing" / "plan.md", "0110_me")
        self.assertEqual(got["prior_findings"], "")
        self.assertTrue(got["prior_findings_record"]["error"].startswith("FileNotFoundError"))


if __name__ == "__main__":
    unittest.main()
