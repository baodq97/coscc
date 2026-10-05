"""The pure half of allowing one more review round: the block and the refusals."""

from __future__ import annotations

import unittest

from coscc.units import more_rounds
from coscc.runner.queue import describe

STUCK = {"name": "0001_q", "more_rounds": True}


class TheBlock(unittest.TestCase):
    def test_the_block_is_the_bytes_the_loop_reads(self):
        # The same bytes `tests/loop/test_model_rebase.py` `MORE` holds, so the two sides cannot
        # drift apart.
        self.assertEqual(
            more_rounds.block("owner", "2026-09-27"),
            "\n### More rounds\nDecided by: owner. Date: 2026-09-27. Via: product.\nRounds: 1\n",
        )


class TheRefusals(unittest.TestCase):
    def test_nothing_is_refused_for_a_unit_out_of_rounds(self):
        self.assertEqual(more_rounds.refusal(STUCK, "owner", ""), "")

    def test_the_first_reason_wins_in_spec_order(self):
        busy = describe(
            "0001_q", {"machine": "step", "state": "running", "stage": "review", "since": "t"}
        )
        self.assertEqual(
            more_rounds.refusal(None, "#x\ny", busy), "no such work unit in this workspace"
        )
        said = more_rounds.refusal({"name": "0001_q"}, "#x\ny", busy)
        self.assertIn("0001_q has not used all its review rounds", said)
        self.assertEqual(more_rounds.refusal(STUCK, "a\nb", busy), "the name must be one line")
        self.assertEqual(more_rounds.refusal(STUCK, " # a", busy), "the name may not start with #")
        said = more_rounds.refusal(STUCK, "owner", busy)
        self.assertTrue(said.startswith("0001_q is busy: a review step is running"), said)
        self.assertTrue(said.endswith("; allowing a round does not stop anything itself"), said)

    def test_a_round_being_allowed_is_described_as_such(self):
        said = describe(
            "0001_q", {"machine": "rounds", "state": "running", "stage": "", "since": "t"}
        )
        self.assertIn("a review round is being allowed since", said)


if __name__ == "__main__":
    unittest.main()
