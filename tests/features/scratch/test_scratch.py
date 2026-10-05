"""`coscc/features/scratch.py`: the block that says where an agent writes."""

from __future__ import annotations

import unittest
from unittest import mock

from coscc import features
from coscc.features import scratch
from coscc.kernel import Facts
from coscc.units.scratch import RAM_CAP


class TheBlockNamesBothDirectories(unittest.TestCase):
    def test_every_stage_gets_the_same_text_naming_both_and_the_cap(self):
        texts = {
            scratch.render(mock.Mock(spec=Facts, stage=stage))
            for stage in ("spec", "plan", "impl", "review", "ship")
        }
        self.assertEqual(len(texts), 1)
        (text,) = texts
        self.assertIn("COS_SCRATCH_RAM", text)
        self.assertIn("COS_SCRATCH_DISK", text)
        self.assertIn("TMPDIR", text)
        self.assertIn(f"{RAM_CAP // 2**20} MiB", text)

    def test_the_plugin_hands_the_agent_the_block(self):
        assert scratch.FEATURE.agent is not None
        parts = scratch.FEATURE.agent(mock.Mock())
        self.assertEqual([b.name for b in parts.blocks], ["scratch"])


class TheFeatureIsInTheList(unittest.TestCase):
    def test_it_is_carried(self):
        self.assertIn(scratch.FEATURE, features.FEATURES)


if __name__ == "__main__":
    unittest.main()
