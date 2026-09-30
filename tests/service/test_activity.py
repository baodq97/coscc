"""Tests for `Activity` in `coscc/service/activity.py`, split from
`tests/service/test_service.py`."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coscc.agent.sessions import Sessions
from coscc.config import Config
from coscc.service import Service
from coscc.service.common import Invalid
from coscc.units import states
from tests.service.test_service import _service, create_sync


class EveryStageArtifactOpens(unittest.TestCase):
    """The unit's detail opens each artifact the harness names, `spike.md` among them (`0141`)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.cwd = str(root / "work" / "proj")
        Path(self.cwd).mkdir(parents=True)
        config = Config(
            workspaces=(self.cwd,), working_dir=str(root / "work"), data_dir=str(root / "data")
        )
        self.service = Service(config, Sessions(config))
        made = create_sync(self.service, self.cwd, "a-problem", "x")
        self.unit, self.dir = made["unit"], Path(made["path"])

    def test_spike_md_is_shown(self):
        (self.dir / "spike.md").write_text("# Spike: x\nStatus: accepted.\n", encoding="utf-8")
        got = self.service.activity.artifact(self.cwd, self.unit, "spike")
        self.assertEqual((got["file"], got["exists"]), ("spike.md", True))
        self.assertIn("# Spike: x", got["text"])

    def test_every_stage_of_the_state_set_opens(self):
        for stage in states.default().stages:
            (self.dir / stage.artifact).write_text(f"# {stage.name}\n", encoding="utf-8")
            got = self.service.activity.artifact(self.cwd, self.unit, stage.name)
            self.assertEqual(got["text"], f"# {stage.name}\n", stage.name)

    def test_a_name_that_is_no_stage_is_still_refused(self):
        with self.assertRaises(Invalid) as e:
            self.service.activity.artifact(self.cwd, self.unit, "deploy")
        self.assertEqual(str(e.exception), "no such stage: deploy")


class UsageCountsWhatItCouldNotAdd(unittest.TestCase):
    """The workspace's cost adds what is known and counts, per unit and in all, the `end` rows that
    carried no `cost_usd`."""

    def test_unknown_is_counted_per_unit_and_in_the_total(self):
        rows = [
            {"kind": "start", "unit": "0002_a"},
            {"kind": "end", "unit": "0002_a", "turns": 4, "cost_usd": 0.52},
            {"kind": "end", "unit": "0002_a", "turns": 109, "cost_unknown": True},
            {"kind": "end", "unit": "0004_b", "cost_unknown": True},
            {"kind": "end", "unit": "0005_c", "cost_usd": 1.0},
            {"kind": "attempt", "unit": "0005_c"},
        ]
        got = _service().activity._usage_of("w", rows)
        self.assertEqual((got["total"]["cost_usd"], got["total"]["unknown"]), (1.52, 2))
        self.assertEqual(got["total"]["turns"], 113)
        self.assertEqual(
            {u: b["unknown"] for u, b in got["per_unit"].items()},
            {"0002_a": 1, "0004_b": 1, "0005_c": 0},
        )
