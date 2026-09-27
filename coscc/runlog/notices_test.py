"""Tests for `coscc/runlog/notices.py`: which records are notices, and what their sentence says (`0113` R2)."""

from __future__ import annotations

import json
import unittest

from coscc.units import autopilot
from coscc.runlog import notices

WS = "/home/someone/work/proj"
SHA = "0123456789abcdef0123456789abcdef01234567"


def _stop(unit: str, stop: str, reason: str = "") -> dict:
    return {"v": 1, "at": "2026-09-27T10:00:00Z", "kind": "autopilot-stop", "workspace": WS,
            "unit": unit, "stage": "", "stop": stop, "reason": reason}


def _end(outcome: str, unit: str = "0007_x", stage: str = "impl", **extra) -> dict:
    return {"v": 1, "at": "2026-09-27T10:00:00Z", "kind": "end", "workspace": WS, "unit": unit,
            "stage": stage, "outcome": outcome, **extra}


def _ship(result: str) -> dict:
    return {"v": 1, "at": "2026-09-27T10:00:00Z", "kind": "ship", "workspace": WS, "unit": "0007_x",
            "stage": "ship", "result": result}


QUESTIONS = {"v": 1, "at": "2026-09-27T10:00:00Z", "kind": "questions", "workspace": WS, "unit": "0007_x",
             "stage": "spec", "questions": [{"artifact": "spec.md", "n": 1}, {"artifact": "spec.md", "n": 2}]}

# One record of every kind R2 names, and every stop the autopilot writes, each with a reason
# and a detail that carry what S3 keeps off the screen.
EVERY = [
    *[_stop(u, s, f"at {SHA} under {WS}/x: gh said no") for s in autopilot.STOP_KINDS if s != "full"
      for u in ("0007_x", "")],
    _stop("0007_x", "someday"),
    QUESTIONS,
    *[_end(o, detail=f"{WS}/.cos/x failed at {SHA}", run="3f1c2a9e-1111-2222-3333-444455556666")
      for o in ("failed", "exhausted", "stopped", "cancelled", "odd")],
    _ship("shipped"),
    _ship("refused"),
]


class EachKindComesFromItsRecord(unittest.TestCase):
    def test_each_of_the_five_kinds_comes_from_its_record(self):
        got = {
            "autopilot-stop": notices.notice_of(1, _stop("0007_x", "a")),
            "questions": notices.notice_of(2, QUESTIONS),
            "step-ended": notices.notice_of(3, _end("failed")),
            "shipped": notices.notice_of(4, _ship("shipped")),
            "ship-refused": notices.notice_of(5, _ship("refused")),
        }
        self.assertEqual(set(got), set(notices.KINDS))
        for kind, n in got.items():
            self.assertIsNotNone(n, kind)
            self.assertEqual(n["kind"], kind)
            self.assertEqual(n["workspace"], WS)
            self.assertEqual(n["unit"], "0007_x")
        self.assertEqual(got["questions"]["record"], QUESTIONS)
        self.assertIn("2 open questions", got["questions"]["text"])
        self.assertEqual(got["step-ended"]["stage"], "impl")

    def test_a_workspace_stop_is_a_notice_with_no_unit(self):
        n = notices.notice_of(9, _stop("", "shortlist"))
        self.assertEqual((n["kind"], n["unit"]), ("autopilot-stop", ""))
        self.assertIn("proj", n["text"])
        self.assertIn("nothing is on the shortlist", n["text"])

    def test_a_cleared_stop_a_full_stop_a_done_end_and_a_precedent_end_are_no_notice(self):
        for record in (_stop("0007_x", ""), _stop("", ""), _stop("0007_x", "full"), _end("done"),
                       _end("failed", stage=autopilot.NOT_STEPS[0])):
            self.assertIsNone(notices.notice_of(1, record), record)

    def test_an_end_with_no_unit_and_a_ship_with_another_result_are_no_notice(self):
        for record in (_end("failed", unit=""), _ship(""), _ship("merged"), {"kind": "start", "unit": "u"}):
            self.assertIsNone(notices.notice_of(1, record), record)


class TheSentenceTellsAndNothingMore(unittest.TestCase):
    def test_text_carries_no_reason_detail_path_or_sha(self):
        for record in EVERY:
            n = notices.notice_of(1, record)
            self.assertIsNotNone(n, record)
            for leaked in (SHA, SHA[:7], "/home", "gh said", "3f1c2a9e", ".cos"):
                self.assertNotIn(leaked, n["text"], record)
            self.assertEqual(n["text"].count(". "), 0, f"more than one sentence: {n['text']}")

    def test_no_text_reads_as_an_approval(self):
        for record in EVERY:
            text = notices.notice_of(1, record)["text"].lower()
            for word in ("approv", "accept", "pass"):
                self.assertNotIn(word, text, text)

    def test_the_line_opens_with_type_then_id(self):
        line = json.dumps(notices.notice_of(42, _ship("shipped")))
        self.assertTrue(line.startswith('{"type": "notice", "id": 42, '), line)


if __name__ == "__main__":
    unittest.main()
