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
                {"kind": "merge", "unit": u, "stage": "ship", "result": "shipped", "at": at}
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


class InsightsCountTheGradedOutcomes(unittest.TestCase):
    """KR2.1: of the units shipped a week ago or more, those graded within the next 7 days.
    KR2.2: of those graded, the share whose latest verdict is met; the rest are named."""

    def test_on_time_met_and_missed(self):
        now = datetime(2026, 10, 30, tzinfo=timezone.utc)
        ship = lambda u, at: {
            "kind": "merge",
            "unit": u,
            "stage": "ship",
            "result": "shipped",
            "at": f"2026-10-{at}T00:00:00+00:00",
        }
        graded = lambda u, at, verdict: {
            "kind": "end",
            "unit": u,
            "stage": "outcome",
            "at": f"2026-10-{at}T00:00:00+00:00",
            "cost_usd": 0.2,
            "outcome": "done",
            "verdict": verdict,
        }
        rows = [
            ship("0001_a", "01"),
            graded("0001_a", "09", "met"),
            ship("0002_b", "02"),
            graded("0002_b", "20", "not-met"),
            ship("0003_c", "03"),
            graded("0003_c", "11", "not-met"),
            graded("0003_c", "12", "unclear"),
            ship("0004_d", "04"),
            ship("0005_e", "28"),
        ]
        units = [
            {"name": f"000{n}_{c}", "why": "finished", "rounds": []}
            for n, c in ((1, "a"), (2, "b"), (3, "c"), (4, "d"), (5, "e"))
        ]
        service = _core()
        with mock.patch.object(service.activity, "_records_or_none", return_value=rows):
            got = service.activity.insights("w", units, days=30, now=now)
        o = got["outcomes"]
        # Due: the four shipped a week ago or more; one was graded late and one never.
        self.assertEqual((o["due"], o["on_time"], o["graded"], o["met"]), (4, 2, 3, 1))
        self.assertEqual(o["missed"], ["0002_b", "0003_c"])
        self.assertEqual(
            {s["unit"]: s["outcome"] for s in got["shipped"]},
            {"0001_a": "met", "0002_b": "not-met", "0003_c": "unclear", "0004_d": "", "0005_e": ""},
        )
