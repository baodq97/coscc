"""Tests for `coscc/service_common.py`, split from `coscc/service_test.py` (`0095`).
"""

from __future__ import annotations

import unittest
from datetime import date

from coscc.service_common import (
    STATE_COLOR,
    STATE_LABEL,
    attention_reason,
    outcome_label,
    reason_beside,
    shown_state,
    unit_state,
)


class TheOutcomeLabel(unittest.TestCase):
    """`0047` R8. One pure decision, every branch, with a fixed `today`."""

    TODAY = date(2026, 10, 8)

    def label(self, deadline="2026-10-07", result=None, finished=False, **over):
        outcome = {"deadline": deadline, "result": result, "by": None, "date": None,
                   "measured_by": None, "source": None, "reason": None, "note": None,
                   "invalid": 0, **over}
        return outcome_label(outcome, self.TODAY, finished=finished)

    def test_met_missed_and_unmeasurable_whatever_the_deadline(self):
        for deadline in ("2026-10-07", "2026-10-31", None):
            met, missed, unmeasurable = (
                self.label(deadline, r) for r in ("met", "missed", "unmeasurable")
            )
            self.assertEqual((met["text"], met["color"], met["counted"], met["hint"]),
                             ("đạt", "grass", True, ""))
            self.assertEqual((missed["text"], missed["color"], missed["counted"], missed["hint"]),
                             ("trượt", "red", True, "cân nhắc bỏ hoặc làm lại"))
            self.assertEqual((unmeasurable["text"], unmeasurable["color"], unmeasurable["counted"]),
                             ("không đo được", "amber", False))
            self.assertEqual(met["deadline"], deadline)

    def test_no_result_is_due_on_and_after_the_deadline_and_pending_before(self):
        self.assertEqual(self.label("2026-10-07")["text"], "tới hạn — chưa đo")
        self.assertEqual(self.label("2026-10-08")["text"], "tới hạn — chưa đo")
        self.assertEqual(self.label("2026-10-08")["color"], "amber")
        self.assertFalse(self.label("2026-10-08")["counted"])
        pending = self.label("2026-10-31")
        self.assertEqual((pending["text"], pending["color"], pending["counted"]),
                         ("chưa tới hạn", "gray", False))

    def test_no_deadline_and_no_result_is_no_label(self):
        self.assertIsNone(self.label(None))
        self.assertIsNone(outcome_label(None, self.TODAY))

    def test_the_form_follows_finished_and_the_block_fields_are_carried(self):
        got = self.label(result="met", finished=True, by="Linh", measured_by="agent",
                         source="npm test", invalid=2)
        self.assertTrue(got["form"])
        self.assertFalse(self.label()["form"])
        self.assertEqual((got["by"], got["measured_by"], got["source"], got["invalid"]),
                         ("Linh", "agent", "npm test", 2))


class TheStateOfAUnit(unittest.TestCase):
    """`0100` R3, R5, R13. One state per unit, the first rule that matches deciding it."""

    @staticmethod
    def _unit(**kw) -> dict:
        base = {
            "name": "0001_x", "why": "missing", "at": "plan", "open": 0, "problems": [],
            "hold": None, "between_pr_and_ship": False, "integration": None,
            "stages": [{"stage": "intent", "status": "accepted"}],
        }
        return {**base, **kw}

    def _is(self, unit: dict, last_end=None, ci=None) -> str:
        return unit_state(unit, last_end, ci)["state"]

    def test_every_state_has_a_case_and_its_own_label_and_colour(self):
        cases = {
            "done": self._unit(why="finished"),
            "dropped": self._unit(why="dropped", hold={"state": "dropped"}),
            "paused": self._unit(why="paused", hold={"state": "paused"}),
            "needs-you": self._unit(open=2),
            "error": self._unit(problems=["plan.md: no Status line"]),
            "awaiting": self._unit(at="review", between_pr_and_ship=True),
            "ready": self._unit(),
        }
        for want, unit in cases.items():
            got = unit_state(unit, None, None)
            self.assertEqual(got["state"], want, want)
            self.assertEqual((got["label"], got["color"]), (STATE_LABEL[want], STATE_COLOR[want]))
        running = shown_state(unit_state(self._unit(), None, None), [{"stage": "plan"}])
        self.assertEqual((running["state"], running["label"]), ("running", "Running"))
        self.assertEqual(sorted(STATE_LABEL), sorted(STATE_COLOR))
        self.assertEqual(len(set(STATE_COLOR.values())), len(STATE_COLOR), "no two states share a colour")

    def test_no_state_reads_as_approval(self):
        for label in STATE_LABEL.values():
            self.assertNotIn("Approved", label)
            self.assertNotIn("Accepted", label)

    def test_a_rejected_unit_is_dropped_and_names_the_stage(self):
        unit = self._unit(why="rejected", stages=[
            {"stage": "intent", "status": "accepted"}, {"stage": "spec", "status": "rejected"},
        ])
        got = unit_state(unit, None, None)
        self.assertEqual((got["state"], got["label"]), ("dropped", "Dropped — spec rejected"))

    def test_each_pair_of_neighbouring_rules_goes_to_the_earlier_one(self):
        # 1/2: finished and dropped at once.
        self.assertEqual(self._is(self._unit(why="finished", hold={"state": "dropped"})), "done")
        # 2/3: rejected with a pause still on file.
        self.assertEqual(self._is(self._unit(why="rejected", hold={"state": "paused"})), "dropped")
        # 3/4: a paused unit with a session listed is still paused.
        paused = unit_state(self._unit(why="paused", hold={"state": "paused"}), None, None)
        self.assertEqual(shown_state(paused, [{"stage": "plan"}])["state"], "paused")
        # 4/5: a running unit with an open question is running.
        asking = unit_state(self._unit(open=1), None, None)
        self.assertEqual(shown_state(asking, [{"stage": "plan"}])["state"], "running")
        self.assertEqual(shown_state(asking, [])["state"], "needs-you")
        # 5/6: an open question beats an error (C1).
        self.assertEqual(self._is(self._unit(open=1, problems=["x"])), "needs-you")
        self.assertEqual(self._is(self._unit(why="awaits-person", problems=["x"])), "needs-you")
        # 6/7: an error in the pr→ship window.
        self.assertEqual(
            self._is(self._unit(at="review", between_pr_and_ship=True, problems=["x"])), "error"
        )
        # 7/8: in the window and missing is awaiting, not ready.
        self.assertEqual(self._is(self._unit(at="review", between_pr_and_ship=True)), "awaiting")

    def test_each_cause_of_error(self):
        # (a)
        self.assertEqual(self._is(self._unit(problems=["x"])), "error")
        self.assertEqual(self._is(self._unit(why="unreadable")), "error")
        # (b)
        self.assertEqual(self._is(self._unit(), {"stage": "plan", "outcome": "failed"}), "error")
        self.assertEqual(self._is(self._unit(), {"stage": "plan", "outcome": "exhausted"}), "error")
        # (c)
        window = self._unit(at="review", between_pr_and_ship=True)
        self.assertEqual(self._is({**window, "integration": {"state": "red-after-integration"}}), "error")
        # (d)
        ci = {"head": "a", "checks": [{"name": "tests", "bucket": "fail"}], "at": "2026-09-26T00:00:00+00:00"}
        got = unit_state(window, None, ci)
        self.assertEqual(got["state"], "error")
        self.assertEqual(got["ci"], {"read": True, "red": ["tests"], "at": "2026-09-26T00:00:00+00:00"})
        cancelled = {**ci, "checks": [{"name": "lint", "bucket": "cancel"}]}
        self.assertEqual(self._is(window, None, cancelled), "error")

    def test_a_failure_at_another_stage_or_a_stop_is_not_an_error(self):
        self.assertEqual(self._is(self._unit(at="plan"), {"stage": "spec", "outcome": "failed"}), "ready")
        self.assertEqual(self._is(self._unit(at="plan"), {"stage": "plan", "outcome": "stopped"}), "ready")

    def test_a_stale_review_or_ship_in_the_window_is_awaiting_with_its_ci_line(self):
        """`0054` review F3. A rerun of `pr` leaves `review.md` stale; `next` sends it down
        the missing review's wait on CI, so the card and the dialog say so too."""
        for at in ("review", "ship"):
            got = unit_state(self._unit(at=at, why="stale", between_pr_and_ship=True), None, None)
            self.assertEqual((got["state"], got["ci"]), ("awaiting", {"read": False, "red": [], "at": ""}), at)
        # A stale `pr.md` is `pr` to run again, not a wait.
        self.assertEqual(self._is(self._unit(at="pr", why="stale", between_pr_and_ship=True)), "ready")

    def test_changes_requested_in_the_window_is_ready(self):
        """C6. It waits on an `impl`, not on CI."""
        self.assertEqual(self._is(self._unit(at="review", why="changes-requested", between_pr_and_ship=True)), "ready")

    def test_the_ci_line_says_read_not_red_or_not_read(self):
        window = self._unit(at="review", between_pr_and_ship=True)
        self.assertEqual(unit_state(window, None, None)["ci"], {"read": False, "red": [], "at": ""})
        green = {"head": "a", "checks": [{"name": "tests", "bucket": "pass"}], "at": "t"}
        self.assertEqual(unit_state(window, None, green)["ci"], {"read": True, "red": [], "at": "t"})
        failed = {"head": "a", "error": "gh: not logged in", "at": "t"}
        self.assertEqual(unit_state(window, None, failed)["ci"], {"read": False, "red": [], "at": "t"})
        self.assertIsNone(unit_state(self._unit(), None, None)["ci"], "no line outside the window")

    def test_the_dialog_shows_no_reason_its_state_contradicts(self):
        """Review F1. A unit with `problems` is `Error` (C3), yet `attention_reason` still
        reads "Needs a person"; a dropped unit with a draft still reads "Accept <stage>.md"."""
        broken = self._unit(problems=["plan.md: no Status line"])
        self.assertEqual(attention_reason(broken), "Needs a person")
        state = unit_state(broken, None, None)["state"]
        self.assertEqual(state, "error")
        self.assertEqual(reason_beside(attention_reason(broken), state), "")
        self.assertEqual(reason_beside("Needs a person", "running"), "")

        dropped = self._unit(why="dropped", hold={"state": "dropped"},
                             stages=[{"stage": "intent", "status": "accepted"}, {"stage": "spec", "status": "draft"}])
        self.assertEqual(attention_reason(dropped), "Accept spec.md")
        self.assertEqual(reason_beside(attention_reason(dropped), unit_state(dropped, None, None)["state"]), "")
        for state in ("done", "paused"):
            self.assertEqual(reason_beside("Changes requested", state), "")

        # Where they agree, the words are `0082`'s, unchanged (R12).
        self.assertEqual(reason_beside("Needs a person", "needs-you"), "Needs a person")
        self.assertEqual(reason_beside("Accept plan.md", "ready"), "Accept plan.md")
        self.assertEqual(reason_beside("Changes requested", "ready"), "Changes requested")

    def test_0112_a_ship_md_a_refused_merge_left_is_not_offered_for_acceptance(self):
        """`0112` review F1. `next` works that draft; one with no `Round` reads as before."""
        rows = [{"stage": "review", "status": "accepted"}, {"stage": "ship", "status": "draft"}]
        refused = self._unit(why="ship-refused", at="ship", between_pr_and_ship=True, stages=rows,
                             next="ship after review round 1 did not merge — the next step says what runs now")
        self.assertEqual(attention_reason(refused), "")
        self.assertEqual(unit_state(refused, None, None)["state"], "ready")
        old = self._unit(why="draft", at="ship", between_pr_and_ship=True, stages=rows, next="finish and accept ship.md")
        self.assertEqual(attention_reason(old), "Accept ship.md")


class WhatTheBoardSaysBesideAUnit(unittest.TestCase):
    """`0082` R9, R11, R12: the words and buttons the board derives from what it read."""

    def test_a_finished_closed_or_dropped_unit_invites_no_answer(self):
        from coscc.service import answerable

        self.assertTrue(answerable({"next": "spec"}))
        for unit in ({"next": "finished"}, {"next": "closed: intent.md rejected"},
                     {"next": "spec", "hold": {"state": "dropped"}}):
            self.assertFalse(answerable(unit), unit)
        self.assertTrue(answerable({"next": "spec", "hold": {"state": "paused"}}))

    def test_each_kind_of_wait_has_its_reason_and_a_calm_unit_none(self):
        fixtures = {
            "Accept intent.md": {"next": "accept intent.md", "stages": [{"stage": "intent", "status": "draft"}]},
            "Changes requested": {"next": "impl", "stages": [{"stage": "intent", "status": "accepted"},
                                                             {"stage": "review", "status": "changes-requested"}]},
            "Needs a person": {"next": "waiting", "stages": [{"stage": "review", "status": "changes-requested"}],
                               "person_findings": [{"id": "F1", "answered": False}]},
        }
        self.assertEqual({want: attention_reason(u) for want, u in fixtures.items()},
                         {want: want for want in fixtures})
        self.assertEqual(attention_reason({"next": "spec", "stages": [{"stage": "intent", "status": "accepted"}]}), "")

    def test_the_update_words_name_no_variable_and_offer_only_usable_buttons(self):
        import re

        from coscc.service import update_words

        base = {"shape": "service", "state": "idle", "version": "0.12.0", "commit": "a" * 40}
        states = {
            "unconfigured": ({"release": {"state": "up-to-date"},
                              "local": {"state": "unconfigured", "reason": "COS_UPDATE_LOCAL_FROM chưa đặt"}}, set()),
            "no working folder": ({"release": {"state": "up-to-date"},
                                   "local": {"state": "unconfigured", "reason": "chưa có working folder"}}, set()),
            "nothing newer": ({"release": {"state": "up-to-date"}, "local": {"state": "idle", "workspace": "p"}},
                              {"build-local"}),
            "newer ready": ({"release": {"state": "ready", "version": "0.13.0"},
                             "local": {"state": "idle", "workspace": "p"}},
                            {"apply-release", "now-release", "build-local"}),
        }
        vietnamese = re.compile(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", re.I)
        for name, (status, usable) in states.items():
            with self.subTest(name):
                words = update_words({**base, **status})
                self.assertTrue(words["line"])
                self.assertNotIn("COS_", words["line"])
                self.assertIsNone(vietnamese.search(words["line"]))
                self.assertEqual(set(words["actions"]), usable)
