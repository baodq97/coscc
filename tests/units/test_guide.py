"""The guide's two lists, with no service and no run log on disk."""

import unittest
from datetime import datetime, timedelta, timezone

from coscc.units import autopilot, guide
from coscc.state.place import TABS

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def at(days: float = 0.0, minutes: float = 0.0) -> str:
    return (NOW - timedelta(days=days, minutes=minutes)).isoformat()


class TheLists(unittest.TestCase):
    def test_running_names_each_agent_oldest_first(self):
        got = guide.running(
            {
                "0002_b": [
                    {
                        "stage": "spec",
                        "agent": {"glyph": "ᚲ", "name": "Kenaz"},
                        "started": at(minutes=1),
                    }
                ],
                "0001_a": [{"stage": "integrate", "agent": None, "started": at(minutes=5)}],
            }
        )
        self.assertEqual(
            got,
            [
                {"unit": "0001_a", "stage": "integrate", "agent": "", "started": at(minutes=5)},
                {"unit": "0002_b", "stage": "spec", "agent": "Kenaz", "started": at(minutes=1)},
            ],
        )

    def test_every_stop_kind_but_full_has_one_thing_to_do(self):
        self.assertEqual(set(guide.TODO), set(autopilot.STOP_KINDS) - {"full"})
        for kind, (do, screen, tab) in guide.TODO.items():
            self.assertTrue(do.endswith(".") and do.count(".") == 1, kind)
            self.assertIn(screen, ("unit", "settings", "backlog"), kind)
            self.assertIn(tab, TABS if screen == "unit" else ("",), kind)

    def test_a_workspace_stop_links_to_no_unit(self):
        [got] = guide.notes([{"unit": "", "kind": "f", "reason": "the pass failed"}])
        self.assertEqual((got["screen"], got["tab"]), ("board", ""))


def card(name: str, state: str, **kw) -> dict:
    return {"name": name, "state": {"state": state}, **kw}


def stop(unit: str, kind: str, reason: str = "") -> dict:
    return {"unit": unit, "kind": kind, "reason": reason or f"why {kind}"}


class TheThreeLists(unittest.TestCase):
    units = [
        card("0003_c", "needs-you", attention_reason="spec.md is a draft"),
        card("0001_a", "needs-you", open=2),
        card("0002_b", "ready"),
        card("0004_d", "error"),
        card("0005_e", "running"),
    ]

    def test_needs_you_has_one_item_per_card_with_that_state(self):
        # No stop names 0001 or 0003: both cards are outside the shortlist, as 0132 was.
        got = guide.needs_you(self.units, [])
        self.assertEqual([r["unit"] for r in got], ["0001_a", "0003_c"])
        self.assertEqual(len(got), sum(u["state"]["state"] == "needs-you" for u in self.units))
        self.assertEqual({r["kind"] for r in got}, {"needs-you"})
        by = {r["unit"]: r for r in got}
        self.assertEqual(
            (by["0001_a"]["screen"], by["0001_a"]["tab"]), ("unit", "questions"), "open questions"
        )
        self.assertEqual(
            (by["0003_c"]["screen"], by["0003_c"]["tab"], by["0003_c"]["reason"]),
            ("unit", "overview", "spec.md is a draft"),
        )
        for r in got:
            self.assertEqual(set(r), {"unit", "kind", "do", "reason", "screen", "tab"})
            self.assertTrue(r["do"].endswith("."))

    def test_a_stop_on_a_card_that_needs_you_says_what_to_do_in_its_words(self):
        got = guide.needs_you(self.units, [stop("0003_c", "b")])
        [item] = [r for r in got if r["unit"] == "0003_c"]
        self.assertEqual(
            (item["kind"], item["do"], item["reason"]),
            ("b", guide.TODO["b"][0], "why b"),
        )
        self.assertEqual(len(got), 2)

    def test_a_stop_of_a_card_that_does_not_need_you_is_held(self):
        stops = [stop("0001_a", "a"), stop("0004_d", "e"), stop("0005_e", "f"), stop("", "full")]
        got = guide.held(self.units, stops)
        self.assertEqual([(r["unit"], r["kind"]) for r in got], [("0004_d", "e"), ("0005_e", "f")])
        self.assertEqual(got[0]["do"], guide.TODO["e"][0])
        self.assertEqual(got[0]["reason"], "why e")
        # The stop on a card that needs you is in `needs_you`, once.
        self.assertEqual(
            [r["kind"] for r in guide.needs_you(self.units, stops) if r["unit"] == "0001_a"], ["a"]
        )

    def test_the_workspace_stops_are_notes_but_the_shortlist_one(self):
        stops = [
            stop("0004_d", "e"),
            stop("", "cap"),
            stop("", "f"),
            stop("", "shortlist"),
            stop("", "full"),
        ]
        self.assertEqual([r["kind"] for r in guide.notes(stops)], ["cap", "f"])
        self.assertEqual(guide.held(self.units, stops)[0]["unit"], "0004_d")
        self.assertEqual([r["kind"] for r in guide.held(self.units, stops)], ["e"])

    def test_no_card_that_needs_you_and_no_stop_make_three_empty_lists(self):
        self.assertEqual(
            (guide.needs_you([card("0002_b", "ready")], []), guide.held([], []), guide.notes([])),
            ([], [], []),
        )
