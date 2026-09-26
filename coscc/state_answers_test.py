"""Tests for `AnswersMixin` in `coscc/state_answers.py`, split from `coscc/state_test.py` (`0095`).
"""

from __future__ import annotations

import unittest


class TheOutcomeIsCopiedFromTheService(unittest.TestCase):
    """`0047` plan step 7. The page shows `Service.board`'s `outcome_label` and decides
    nothing; a refusal from `Service.record_outcome` reaches the page as its words."""

    def test_the_fields_map_from_the_label(self):
        from coscc.state import _outcome_fields

        self.assertEqual(_outcome_fields(None), {})
        got = _outcome_fields({
            "kind": "missed", "text": "trượt", "color": "red", "counted": True,
            "hint": "cân nhắc bỏ hoặc làm lại", "deadline": "2026-10-07", "by": "Linh",
            "date": "2026-10-08", "measured_by": "agent", "source": "board", "reason": None,
            "note": "ghi chú", "invalid": 2, "form": True,
        })
        self.assertEqual(got, {
            "outcome_text": "trượt", "outcome_color": "red", "outcome_detail": "board — ghi chú",
            "outcome_by": "Linh", "outcome_date": "2026-10-08", "outcome_measured_by": "agent",
            "outcome_deadline": "2026-10-07", "outcome_hint": "cân nhắc bỏ hoặc làm lại",
            "outcome_invalid": 2, "outcome_form": True,
        })
        self.assertEqual(_outcome_fields({"text": "không đo được", "reason": "no script"})["outcome_detail"],
                         "no script")

    def test_the_handler_shows_the_services_refusal_verbatim(self):
        import asyncio
        from types import SimpleNamespace
        from unittest import mock

        from coscc import state
        from coscc.service import Invalid

        from coscc.screens_test import VIETNAMESE

        async def refuse(*args):
            refuse.args = args
            raise Invalid("the result needs a source: where the figure it rests on came from")

        async def never():
            raise AssertionError("a refused outcome must not reload the board")

        fields = state.StudioState.get_fields()
        self.assertEqual((fields["outcome_result"].default, fields["outcome_measured_by"].default),
                         ("met", "Agent"))
        page = SimpleNamespace(
            cwd="/w", unit_id="0001_x", outcome_result="met", outcome_measured_by="Agent",
            outcome_source="", outcome_reason="", outcome_note="",
            recording_outcome=False, notice="", _load_board=never,
        )
        with mock.patch.object(state, "SERVICE", SimpleNamespace(record_outcome=refuse)):
            asyncio.run(state.StudioState.record_outcome.fn(page))
        self.assertEqual(page.notice, "the result needs a source: where the figure it rests on came from")
        self.assertIsNone(VIETNAMESE.search(page.notice))
        self.assertFalse(page.recording_outcome)
        self.assertEqual(refuse.args, ("/w", "0001_x", "đạt", "agent", "", "", "", ""))

    def test_0089_the_labels_go_down_as_the_stored_words(self):
        """`0089` R1, R4. The page holds English labels; the service gets the stored words."""
        import asyncio
        from types import SimpleNamespace
        from unittest import mock

        from coscc import state
        from coscc.screens_test import VIETNAMESE

        sent = []

        async def record(*args):
            sent.append(args)
            return {"unit": args[1], "result": args[2], "measured_by": args[3], "recorded_by": "owner"}

        async def board():
            return None

        for label, word, who, stored in (("met", "đạt", "Agent", "agent"),
                                         ("missed", "trượt", "You", "owner"),
                                         ("could not be measured", "không đo được", "You", "owner")):
            page = SimpleNamespace(
                cwd="/w", unit_id="0001_x", outcome_result=label, outcome_measured_by=who,
                outcome_source="s", outcome_reason="r", outcome_note="",
                recording_outcome=False, notice="", _load_board=board, _load_artifact=lambda: None,
            )
            with mock.patch.object(state, "SERVICE", SimpleNamespace(record_outcome=record)):
                asyncio.run(state.StudioState.record_outcome.fn(page))
            self.assertEqual(sent[-1][2:4], (word, stored))
            self.assertEqual(page.notice, f"Recorded {label} for 0001_x, measured by {who}.")
            self.assertIsNone(VIETNAMESE.search(page.notice))


class AnsweringAlwaysSaysSomething(unittest.TestCase):
    """`0071` R1, R3–R6, R9. Every press of *Send this answer* ends in a block written or a
    reason shown; none ends in nothing. The handler runs in process on a stand-in page, as
    `TheOutcomeIsCopiedFromTheService` does."""

    def page(self, **over):
        from types import SimpleNamespace

        from coscc import state

        async def loaded():
            page.reloaded = True

        page = SimpleNamespace(
            cwd="/w", unit_id="0001_x", answer_target="intent.md#1", answer_text="yes",
            answering_key="", notice="old notice", error="old error",
            reloaded=False, _load_board=loaded, _load_artifact=lambda: None,
        )
        page._fail = lambda e: state.StudioState._fail(page, e)
        for k, v in over.items():
            setattr(page, k, v)
        return page

    def press(self, page, key, answer):
        """Run the handler to its end with `answer` standing in for `Service.answer`.
        Returns what `answering_key` was at each `yield`."""
        import asyncio
        from types import SimpleNamespace
        from unittest import mock

        from coscc import state

        seen = []

        async def drive():
            async for _ in state.StudioState.answer_question.fn(page, key):
                seen.append(page.answering_key)

        with mock.patch.object(state, "SERVICE", SimpleNamespace(answer=answer)):
            asyncio.run(drive())
        return seen

    @staticmethod
    def never():
        async def answer(*args):
            raise AssertionError("a press with nothing to send must not reach the service")
        return answer

    @staticmethod
    def wrote(question="1", artifact="intent.md"):
        async def answer(*args):
            answer.args = args
            return {"question": question, "artifact": artifact, "answered_by": "Phong"}
        return answer

    def test_text_in_another_questions_box_is_named_and_kept(self):
        page = self.page()
        self.press(page, "intent.md#2", self.never())
        self.assertIn("question 2 of intent.md", page.error)
        self.assertIn("question 1 of intent.md", page.error)
        self.assertEqual((page.answer_target, page.answer_text), ("intent.md#1", "yes"))
        self.assertEqual(page.notice, "old notice")
        self.assertEqual(page.answering_key, "")

    def test_an_empty_box_names_the_button_pressed(self):
        page = self.page(answer_target="", answer_text="")
        self.press(page, "review.md#F2", self.never())
        self.assertIn("finding F2 of review.md", page.error)
        self.assertIn("empty", page.error)
        self.assertEqual(page.notice, "old notice")

    def test_a_second_press_queued_behind_the_first_keeps_what_the_first_wrote(self):
        """Review round 1, F1: a double click whose second press reaches the queue before
        the loading state reaches the browser runs after the first, on an emptied box."""
        page = self.page()
        self.press(page, "intent.md#1", self.wrote())
        self.press(page, "intent.md#1", self.never())
        self.assertTrue(page.notice.startswith("Answered question 1 of intent.md as Phong."),
                        page.notice)
        self.assertIn("Nothing was sent", page.error)
        self.assertEqual(page.answering_key, "")

    def test_a_refusal_is_shown_verbatim_and_the_text_is_kept(self):
        from coscc.service import Invalid

        async def refuse(*args):
            raise Invalid("say who is answering, on one line")

        async def never_reload():
            raise AssertionError("a refused answer must not reload the board")

        page = self.page(_load_board=never_reload)
        self.press(page, "intent.md#1", refuse)
        self.assertEqual(page.error, "say who is answering, on one line")
        self.assertEqual((page.answer_target, page.answer_text), ("intent.md#1", "yes"))
        self.assertEqual(page.notice, "")
        self.assertEqual(page.answering_key, "")

    def test_an_unexpected_failure_says_it_is_not_known_whether_it_was_written(self):
        async def broken(*args):
            raise RuntimeError("disk")

        page = self.page()
        self.press(page, "intent.md#1", broken)
        for part in ("RuntimeError", "disk", "intent.md", "not known whether"):
            self.assertIn(part, page.error)
        self.assertNotIn("Nothing was written", page.error)
        self.assertEqual(page.answer_text, "yes")
        self.assertEqual(page.answering_key, "")

    def test_a_written_answer_is_reported_even_when_the_reload_fails(self):
        async def broken_reload():
            raise RuntimeError("board unreadable")

        page = self.page(_load_board=broken_reload)
        self.press(page, "intent.md#1", self.wrote())
        self.assertTrue(page.notice.startswith("Answered question 1 of intent.md as Phong."),
                        page.notice)
        self.assertIn("board unreadable", page.notice + page.error)
        self.assertEqual((page.answer_target, page.answer_text), ("", ""))

    def test_a_finding_is_answered_like_a_question(self):
        answer = self.wrote("F2", "review.md")
        page = self.page(answer_target="review.md#F2")
        self.press(page, "review.md#F2", answer)
        self.assertIn("Answered finding F2 of review.md", page.notice)
        self.assertEqual(page.error, "")
        self.assertEqual(answer.args, ("/w", "0001_x", "review.md", "F2", "yes", ""))
        self.assertEqual((page.answer_target, page.answer_text), ("", ""))
        self.assertTrue(page.reloaded)

    def test_the_sending_key_reaches_the_browser_before_the_service_is_called(self):
        async def answer(*args):
            answer.called_with_key = page.answering_key
            return {"question": "1", "artifact": "intent.md", "answered_by": "Phong"}

        page = self.page()
        seen = self.press(page, "intent.md#1", answer)
        self.assertEqual(seen[:1], ["intent.md#1"])
        self.assertEqual(answer.called_with_key, "intent.md#1")
        self.assertEqual(page.answering_key, "")

    def test_the_first_yield_comes_before_the_service_is_called(self):
        import asyncio
        from types import SimpleNamespace
        from unittest import mock

        from coscc import state

        page = self.page()
        answer = self.wrote()
        answer.args = None

        async def first_step():
            gen = state.StudioState.answer_question.fn(page, "intent.md#1")
            await gen.__anext__()
            at_yield = (page.answering_key, answer.args)
            await gen.aclose()
            return at_yield

        with mock.patch.object(state, "SERVICE", SimpleNamespace(answer=answer)):
            self.assertEqual(asyncio.run(first_step()), ("intent.md#1", None))

    def test_opening_a_unit_clears_the_last_notice(self):
        from types import SimpleNamespace

        from coscc import state

        # `0056`: what opening a unit reads is `_load_unit`, which `arrive` runs once the
        # address names another unit.
        page = SimpleNamespace(
            unit_id="0002_y", detail_tab="questions", run_log="x", error="e", notice="old",
            units=[], _load_timeline=lambda: None, _load_artifact=lambda: None,
            _load_unit_cost=lambda: None,
        )
        state.StudioState._load_unit(page)
        self.assertEqual((page.unit_id, page.notice, page.error), ("0002_y", "", ""))
