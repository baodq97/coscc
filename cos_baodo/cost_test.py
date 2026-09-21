"""Tests for the one number that looks measured whether or not it is right.

`plan.md` Risk 4 names the failure these stand against: `ResultMessage.model_usage` is
**cumulative over the session**, so adding it up per turn double-counts, and the total
comes out wrong while looking exactly as authoritative as a correct one. Measured on
2026-09-21 over two turns on one client: `cacheReadInputTokens` read 1608 then 5512, and
`total_cost_usd` 0.0169 then 0.0363.

The numbers below are that measurement, replayed.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import dataclass, field
from typing import Any

from cos_baodo import sessions
from cos_baodo.journal import Journal


@dataclass
class FakeResult:
    """Only the fields `_cumulative` reads. The SDK's own class needs a dozen more."""

    total_cost_usd: float = 0.0
    model_usage: dict[str, Any] = field(default_factory=dict)
    num_turns: int = 1
    duration_ms: int = 0
    session_id: str = "s-1"


def usage(inp, out, cache_read, cache_create):
    return {
        "claude-opus-5": {
            "inputTokens": inp,
            "outputTokens": out,
            "cacheReadInputTokens": cache_read,
            "cacheCreationInputTokens": cache_create,
        }
    }


# The two readings from the real session, in order.
TURN_1 = FakeResult(total_cost_usd=0.016909, model_usage=usage(1171, 18, 1608, 980))
TURN_2 = FakeResult(total_cost_usd=0.036336, model_usage=usage(1173, 21, 5512, 2719))


class CumulativeIsReadAsCumulative(unittest.TestCase):
    def test_the_second_reading_is_the_session_not_the_turn(self):
        first = sessions._cumulative(TURN_1)
        second = sessions._cumulative(TURN_2)
        self.assertEqual(first["cache_read_tokens"], 1608)
        self.assertEqual(second["cache_read_tokens"], 5512)
        self.assertGreater(second["cost_usd"], first["cost_usd"])

    def test_a_turns_own_cost_is_the_difference(self):
        first = sessions._cumulative(TURN_1)
        second = sessions._cumulative(TURN_2)
        turn_2 = {k: second[k] - first[k] for k in second}
        self.assertEqual(turn_2["cache_read_tokens"], 3904)
        self.assertEqual(turn_2["cache_creation_tokens"], 1739)
        self.assertAlmostEqual(turn_2["cost_usd"], 0.019427, places=6)

    def test_adding_the_readings_up_would_have_been_wrong(self):
        """The bug this is all here to prevent, stated as a number.

        Summing the two readings gives 7120 cache-read tokens where the session used
        5512 — 29% too high, and nothing about the figure would have looked odd.
        """
        naive = (
            sessions._cumulative(TURN_1)["cache_read_tokens"]
            + sessions._cumulative(TURN_2)["cache_read_tokens"]
        )
        self.assertEqual(naive, 7120)
        self.assertNotEqual(naive, 5512)

    def test_several_models_in_one_turn_are_added_together(self):
        two = FakeResult(
            model_usage={
                "a": {"inputTokens": 10, "outputTokens": 1},
                "b": {"inputTokens": 5, "outputTokens": 2},
            }
        )
        got = sessions._cumulative(two)
        self.assertEqual(got["input_tokens"], 15)
        self.assertEqual(got["output_tokens"], 3)

    def test_a_result_with_no_usage_at_all_is_zero_rather_than_a_crash(self):
        got = sessions._cumulative(FakeResult())
        self.assertEqual(got["input_tokens"], 0)
        self.assertEqual(got["cost_usd"], 0.0)


class TheDeltasSumBackToTheSessionTotal(unittest.TestCase):
    def test_per_turn_deltas_add_up_to_the_last_cumulative_reading(self):
        """`spec.md` R17 in its smallest form: the parts equal the whole."""
        spent: dict[str, float] = {}
        turns = []
        for result in (TURN_1, TURN_2):
            total = sessions._cumulative(result)
            turns.append({k: total[k] - spent.get(k, 0.0) for k in total})
            spent = total

        for name in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"):
            self.assertEqual(
                sum(t[name] for t in turns), spent[name], f"{name} did not add back up"
            )
        self.assertAlmostEqual(sum(t["cost_usd"] for t in turns), spent["cost_usd"], places=6)


class TheJournalKeepsMoneyAsMoney(unittest.TestCase):
    def test_sub_cent_costs_are_not_rounded_away(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d)
            for _ in range(3):
                j.started("w", "0009_x", "impl", "autonomous")
                j.finished("w", "0009_x", "impl", "done", cost_usd=0.004, input_tokens=1)
            total = j.totals("w", "0009_x")["total"]
            # Three turns at 0.4 cents each is 1.2 cents, not zero.
            self.assertAlmostEqual(total["cost_usd"], 0.012, places=6)
            self.assertEqual(total["input_tokens"], 3)

    def test_the_unit_total_still_equals_the_sum_of_its_stages(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(d)
            for stage, usd in (("spec", 0.01), ("impl", 0.25)):
                j.started("w", "0009_x", stage, "autonomous")
                j.finished("w", "0009_x", stage, "done", cost_usd=usd)
            totals = j.totals("w", "0009_x")
            summed = sum(b["cost_usd"] for b in totals["per_stage"].values())
            self.assertAlmostEqual(summed, totals["total"]["cost_usd"], places=6)
            self.assertAlmostEqual(totals["total"]["cost_usd"], 0.26, places=6)


if __name__ == "__main__":
    unittest.main()
