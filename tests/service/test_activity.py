"""Tests for `Activity` in `coscc/service/activity.py`, split from
`tests/service/test_service.py`."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from coscc.agent.sessions import Sessions
from coscc.config import Config
from coscc.service import Service
from coscc.kernel import Invalid
from coscc.units import states
from tests.service.test_service import _service, create_sync


class EveryStageArtifactOpens(unittest.TestCase):
    """The unit's detail opens each artifact the harness names, `spike.md` among them."""

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


class AQuestionsRowCountsWhatItAsked(unittest.TestCase):
    """Older `questions` rows record a count, newer ones the list; both are read."""

    def test_a_count_a_list_or_nothing(self):
        from coscc.service.activity import _count

        self.assertEqual([_count(3), _count([{"n": 1}, {"n": 2}]), _count(None)], [3, 2, 0])


class InsightsMeasureTheShippedUnitsAgainstTheTargets(unittest.TestCase):
    """A unit shipped in the window counts with its whole cost and its review rounds; the median
    of each is held against the owner's target, and the units past it are named."""

    def test_the_window_the_medians_and_who_is_over(self):
        now = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
        end = lambda unit, at, usd, stage="impl": {
            "kind": "end",
            "unit": unit,
            "stage": stage,
            "at": at,
            "cost_usd": usd,
            "outcome": "done",
        }
        rows = [
            end("0001_old", "2026-08-01T00:00:00+00:00", 9.0),
            end("0002_a", "2026-08-20T00:00:00+00:00", 10.0),
            end("0002_a", "2026-10-01T00:00:00+00:00", 8.0, "review"),
            end("0003_b", "2026-10-02T00:00:00+00:00", 4.0),
            end("0004_open", "2026-10-03T00:00:00+00:00", 2.0),
            # Shipped by the app: the oldest before the window, the others in it.
            *(
                {"kind": "ship", "unit": u, "stage": "ship", "result": "shipped", "at": at}
                for u, at in (
                    ("0001_old", "2026-08-01T01:00:00+00:00"),
                    ("0002_a", "2026-10-01T01:00:00+00:00"),
                    ("0003_b", "2026-10-02T01:00:00+00:00"),
                )
            ),
            # A late run on a unit shipped long ago does not bring it into the window.
            end("0001_old", "2026-10-03T00:00:00+00:00", 0.5, "pr"),
        ]
        rounds = lambda *v: [{"verdict": x} for x in v]
        units = [
            {"name": "0001_old", "why": "finished", "rounds": []},
            {
                "name": "0002_a",
                "why": "finished",
                "rounds": rounds("changes-requested", "approved"),
            },
            {"name": "0003_b", "why": "outdated-main", "rounds": rounds("approved")},
            {"name": "0004_open", "why": "impl", "rounds": []},
        ]
        service = _service()
        with mock.patch.object(service.activity, "_records_or_none", return_value=rows):
            got = service.activity.insights("w", units, days=30, now=now)
        self.assertEqual(
            [(s["unit"], s["usd"], s["rounds"]) for s in got["shipped"]],
            [
                ("0003_b", 4.0, 1),
                ("0002_a", 18.0, 2),
            ],
        )
        cost, rounds_ = got["targets"]
        self.assertEqual((cost["value"], cost["target"], cost["over"]), (11.0, 15.0, ["0002_a"]))
        self.assertEqual((rounds_["value"], rounds_["over"]), (1.5, ["0002_a"]))
        self.assertEqual(
            {d["day"] for d in got["by_day"]}, {"2026-10-01", "2026-10-02", "2026-10-03"}
        )
        self.assertEqual(
            {r["stage"]: r["usd"] for r in got["by_stage"]}, {"review": 8.0, "impl": 6.0, "pr": 0.5}
        )

    def test_a_workspace_with_no_run_log_says_so(self):
        service = _service()
        with mock.patch.object(service.activity, "_records_or_none", return_value=None):
            got = service.activity.insights("w", [])
        self.assertEqual((got["recording"], got["shipped"]), (False, []))
