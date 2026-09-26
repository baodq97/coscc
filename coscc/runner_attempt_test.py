"""Tests for `coscc/runner_attempt.py`, split from `coscc/runner_test.py` (`0095`).

What a failed attempt left, as the next step and the board are told it.
"""

from __future__ import annotations

import unittest

from coscc.runner_attempt import describe_attempt


class DescribeAttemptRendersTheRecord(unittest.TestCase):
    def test_a_full_attempt_names_outcome_turns_and_commits(self):
        found = {
            "attempt": {
                "outcome": "exhausted", "terminal": "max_turns", "error": None,
                "turns": 121, "cost_usd": 6.88, "session_id": "s-1",
                "head": "a" * 40, "branch": "fix/x", "base": "b" * 40,
                "base_ref": "refs/heads/main",
                "commits": [{"sha": "c" * 40, "subject": "did a thing"}],
                "status": [" M a.txt"], "excerpt": "hello", "excerpt_total_chars": 5,
                "snapshot_errors": None,
            },
            "latest": {"at": "t0", "outcome": "exhausted", "turns": 121, "cost_usd": 6.88},
            "earlier": [{"at": "t-1", "outcome": "exhausted", "turns": 60, "cost_usd": 4.91}],
        }
        text = describe_attempt(found)
        self.assertIn("Outcome: exhausted", text)
        self.assertIn("Terminal reason: max_turns", text)
        self.assertIn("121", text)
        self.assertIn("6.88", text)
        self.assertIn("did a thing", text)
        self.assertIn(" M a.txt", text)
        self.assertIn("hello", text)
        self.assertIn("60 turns", text)

    def test_a_missing_attempt_falls_back_to_the_end_record(self):
        found = {
            "attempt": None,
            "latest": {"at": "t0", "outcome": "failed", "turns": None, "cost_usd": None},
            "earlier": [],
        }
        text = describe_attempt(found)
        self.assertIn("No snapshot record was captured", text)
        self.assertIn("unknown — the session returned no result", text)


class ThePromptSaysWhatAReviewThatWroteNothingOpened(unittest.TestCase):
    """`0085` R11, as `describe_attempt` renders it."""

    LATEST = {"at": "t0", "outcome": "exhausted", "turns": 41, "cost_usd": 4.1}

    def render(self, opened):
        return describe_attempt({"attempt": None, "latest": self.LATEST, "earlier": [], "opened": opened})

    def test_the_paths_are_listed_as_opened_not_reviewed(self):
        text = self.render({"paths": ["/w/coscc/x.py", "/w/coscc/y.py"], "closing": True})
        self.assertIn("- /w/coscc/x.py", text)
        self.assertIn("- /w/coscc/y.py", text)
        self.assertIn("no conclusion about any of them was written", text)
        self.assertIn("the closing turn the app gave it wrote no round", text)
        self.assertIn("`review.md` holds nothing from it", text)

    def test_a_closing_turn_that_never_ran_is_not_told_of(self):
        # Review F2: no session id or no head, so the app gave it none.
        text = self.render({"paths": ["/w/coscc/x.py"], "closing": False})
        self.assertNotIn("the closing turn the app gave it", text)
        self.assertIn("the app could not give it a closing turn", text)

    def test_purged_events_are_said_to_be_purged(self):
        self.assertIn("its recorded events have been purged", self.render({"purged": True}))

    def test_an_unreadable_log_is_said_to_be_unreadable(self):
        self.assertIn("could not be read from its events: Busy: locked", self.render({"error": "Busy: locked"}))

    def test_no_opened_key_adds_nothing(self):
        text = describe_attempt({"attempt": None, "latest": self.LATEST, "earlier": []})
        self.assertNotIn("closing turn", text)
