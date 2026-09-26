"""Tests for `ActivityMixin` in `coscc/service_activity.py`, split from `coscc/service_test.py` (`0095`).
"""

from __future__ import annotations

import unittest

from coscc.service_test import _service


class UsageCountsWhatItCouldNotAdd(unittest.TestCase):
    """`0092` R7. The workspace's cost adds what is known and counts, per unit and in all, the
    `end` rows that carried no `cost_usd`."""

    def test_unknown_is_counted_per_unit_and_in_the_total(self):
        rows = [
            {"kind": "start", "unit": "0002_a"},
            {"kind": "end", "unit": "0002_a", "turns": 4, "cost_usd": 0.52},
            {"kind": "end", "unit": "0002_a", "turns": 109, "cost_unknown": True},
            {"kind": "end", "unit": "0004_b", "cost_unknown": True},
            {"kind": "end", "unit": "0005_c", "cost_usd": 1.0},
            {"kind": "attempt", "unit": "0005_c"},
        ]
        got = _service()._usage_of("w", rows)
        self.assertEqual((got["total"]["cost_usd"], got["total"]["unknown"]), (1.52, 2))
        self.assertEqual(got["total"]["turns"], 113)
        self.assertEqual({u: b["unknown"] for u, b in got["per_unit"].items()},
                         {"0002_a": 1, "0004_b": 1, "0005_c": 0})
