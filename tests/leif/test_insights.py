"""`Activity.insights` in `coscc/leif/insights.py`."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest import mock

from tests.http.test_app import _core


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
            # A chat turn: no unit, and its agent named on its `end`, with the run it opens.
            {
                **end("", "2026-10-03T01:00:00+00:00", 0.25, ""),
                "agent": "chat",
                "run": "c1",
                "outcome": "done",
            },
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
        service = _core()
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
            {r["agent"]: r["usd"] for r in got["by_agent"]},
            {"review": 8.0, "impl": 6.0, "pr": 0.5, "chat": 0.25},
        )
        [chat] = [r for r in got["by_agent"] if r["agent"] == "chat"]
        self.assertEqual(
            chat["runs"],
            [
                {
                    "run": "c1",
                    "unit": "",
                    "at": "2026-10-03T01:00:00+00:00",
                    "usd": 0.25,
                    "outcome": "done",
                }
            ],
        )
