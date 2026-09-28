"""`0101` R10. The guide's three lists, with no service and no run log on disk."""

import unittest
from datetime import datetime, timedelta, timezone

from coscc.units import autopilot, guide
from coscc.web.place import TABS

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def at(days: float = 0.0, minutes: float = 0.0) -> str:
    return (NOW - timedelta(days=days, minutes=minutes)).isoformat()


class TheLists(unittest.TestCase):
    def test_running_names_each_agent_oldest_first(self):
        got = guide.running({
            "0002_b": [{"stage": "precedent", "agent": {"glyph": "ᛃ", "name": "Jera"}, "started": at(minutes=1)}],
            "0001_a": [{"stage": "integrate", "agent": None, "started": at(minutes=5)}],
        })
        self.assertEqual(got, [
            {"unit": "0001_a", "stage": "integrate", "agent": "", "started": at(minutes=5)},
            {"unit": "0002_b", "stage": "precedent", "agent": "Jera", "started": at(minutes=1)},
        ])

    def test_every_stop_kind_but_full_has_one_thing_to_do(self):
        self.assertEqual(set(guide.TODO), set(autopilot.STOP_KINDS) - {"full"})
        for kind, (do, screen, tab) in guide.TODO.items():
            self.assertTrue(do.endswith(".") and do.count(".") == 1, kind)
            self.assertIn(screen, ("unit", "settings", "backlog"), kind)
            self.assertIn(tab, TABS if screen == "unit" else ("",), kind)

    def test_a_workspace_stop_links_to_no_unit(self):
        [got] = guide.needs_you([{"unit": "", "kind": "f", "reason": "the pass failed"}])
        self.assertEqual((got["screen"], got["tab"]), ("board", ""))

    def test_decided_keeps_only_written_answers_of_the_window(self):
        def row(days, **kw):
            return {"kind": "precedent", "unit": "0001_a", "artifact": "spec.md", "n": 1, "verdict": "answer",
                    "written": True, "at": at(days), **kw}

        rows = [row(8), row(1, verdict="needs-person", written=False), row(1, written=False), row(2, n=2),
                {"kind": "answer", "at": at(0)}]
        self.assertEqual(guide.decided(rows, NOW), [
            {"unit": "0001_a", "artifact": "spec.md", "n": 2, "at": at(2), "tab": "questions"},
        ])
