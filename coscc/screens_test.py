"""`0082` plan step 13: what the page's own components say, read from their render.

Nothing here opens a browser. `screens.index()` is rendered to Reflex's component dict and
searched as text: every literal, placeholder and aria-label the app writes is in it, and no
artifact content is (that arrives at run time).
"""

from __future__ import annotations

import json
import re
import unittest

import reflex as rx

from coscc import present, screens

VIETNAMESE = re.compile(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", re.I)


def _render(component) -> str:
    return json.dumps(component.render(), ensure_ascii=False, default=str)


class ThePage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = _render(screens.index())

    def test_r3_no_input_asks_for_a_name(self):
        labels = re.findall(r'(?:placeholder|ariaLabel|aria_label)\s*:\s*\\?"((?:[^"\\]|\\.)*?)\\?"', self.page)
        # The slug of a new unit is a name for the problem, not for a person.
        named = [x for x in labels if re.search(r"\bname\b|\btên\b|your name", x, re.I)
                 and x not in ("Name for the work unit", "short-name-for-the-problem")]
        self.assertEqual(named, [])
        for gone in ("answer-by", "stop-by", "hold-by", "update-by", "outcome-recorded-by", "backlog-backlog_by"):
            self.assertNotIn(f'"{gone}\\"', self.page)
            self.assertNotIn(f'\\"{gone}\\"', self.page)

    def test_r5_the_apps_own_text_is_english(self):
        """Since `0089` nothing is left for unit B: `LEFT_TO_B` emptied and went."""
        left = sorted({m.group(0) for m in re.finditer(r"\S*" + VIETNAMESE.pattern[:-1] + r"]\S*", self.page, re.I)})
        self.assertEqual(left, [])

    def test_r5_the_label_tables_are_english(self):
        for table in (present.RELATION_LABEL, present.OUTCOME_LABEL, present.RESULT_LABEL,
                      present.MEASURER_LABEL):
            self.assertFalse(any(VIETNAMESE.search(v) for v in table.values()))

    def test_0089_the_outcome_form_says_one_thing(self):
        """`0089` R1, R3, R4 (D23, D46, D47)."""
        form = _render(screens._outcome_panel())
        for gone in ("Appended to intent.md", "no gate reads it", "Neither name", "person's name",
                     "đạt", "trượt", "không đo được"):
            self.assertNotIn(gone, form)
        self.assertIn("Adds an outcome block to intent.md.", form)
        self.assertIn("could not be measured", form)
        self.assertIn('"You"', form.replace('\\"', '"'))

    def test_0089_a_timeline_row_keeps_its_session_behind_details(self):
        """`0089` R12, R13, R15 (D60, D61, D64)."""
        for gone in ('"Xem"', "/ session ", "Read from the run log", "(still running)"):
            self.assertNotIn(gone, self.page.replace('\\"', '"'))
        self.assertIn("Every run of this unit, oldest first.", self.page)
        self.assertIn("run-", self.page)

    def test_0089_no_variable_name_or_path_until_opened(self):
        """`0089` R5, R7, R8, R11 (D51, D53, D54, D55)."""
        for part in (screens._empty_board(), screens._workspaces_screen(), screens._activity()):
            self.assertNotIn("COS_", _render(part))
        # A workspace's `path` field, read anywhere on the overview.
        self.assertNotIn('?.["path"]', _render(screens._overview()).replace("\\", ""))
        cards = _render(screens._workspaces_screen())
        self.assertIn("ws-path-", cards)
        self.assertIn("Read only", cards)

    def test_0089_one_sentence_and_no_limits(self):
        """`0089` R2, R9, R10 (D44, D56, D59)."""
        for gone in ("Nothing re-asks on its own", "Bars share", "not an approval", "verbatim",
                     "Whoever holds the"):
            self.assertNotIn(gone, self.page)

    def test_0044_the_questions_tab_says_whose_answer_and_asks_no_name(self):
        """`0044` R10, S7, S8: the labels, the sentence beside *Ask Jera*, and the button hidden
        behind `jera_can_ask` rather than greyed."""
        from coscc.service import CONSEQUENCE

        tab = _render(screens._questions_tab())
        for said in ("Ask Jera", "ask-jera", CONSEQUENCE["precedent"], "Answered by Jera",
                     "Needs a person", "Jera's proposal", "Precedent", "jera_can_ask",
                     "Jera's answer", "jera-said"):
            self.assertIn(said, tab)
        self.assertIn("Send this answer", tab, "a person can still answer over Jera")
        settings = _render(screens._settings())
        for said in ("Decision preferences", "decision-preferences", "company names"):
            self.assertIn(said, settings)

    def test_r12_needs_review_is_gone(self):
        self.assertNotIn("Needs review", self.page)
        self.assertIn("Needs you", self.page)

    def test_the_backlog_has_its_route_and_the_board_lost_its_panels(self):
        board = _render(screens._board())
        self.assertNotIn("update-panel", board)
        self.assertNotIn("backlog-panel", board)
        self.assertIn("update-panel", _render(screens._settings()))
        self.assertIn("backlog-panel", _render(screens._backlog_screen()))

    def test_0043_the_autopilot_strip_and_its_settings(self):
        """`0043` R2, R9: the board's strip lists each stop as a row; Settings holds the four
        controls, and no variable name reaches either (S3)."""
        board = _render(screens._board())
        self.assertIn("autopilot-strip", board)
        self.assertIn("autopilot-stop", board)
        settings = _render(screens._settings())
        for control in ("autopilot-panel", "autopilot-on", "autopilot-ship", "autopilot-parallel", "autopilot-cap"):
            self.assertIn(control, settings)
        self.assertNotIn("COS_", _render(screens._autopilot_settings()))
        self.assertNotIn("COS_", _render(screens._autopilot_strip()))

    def test_f2_a_grants_tools_are_a_list_behind_details(self):
        settings = _render(screens._settings())
        self.assertNotIn("tools: ", settings)
        self.assertNotIn("commands: ", settings)
        self.assertIn("What it may use", settings)
        self.assertIn("tool_list", settings)


class TheIntegrationPanel(unittest.TestCase):
    def test_r13_a_zero_count_is_never_drawn(self):
        """`conflicting` with behind 0 used to read "0 commit(s) behind" beside it."""
        panel = _render(screens._integration_panel())
        self.assertNotIn("commit(s)", panel)
        self.assertIn("as of the last fetch", panel)
        # The count is drawn only when it is neither empty nor "0".
        self.assertIn(r'integration_behind\"]?.valueOf?.() === \"0\"', panel)


class Markdown(unittest.TestCase):
    def test_risk_8_reflex_passes_raw_html_by_default(self):
        """Measured, not assumed: `rx.markdown` loads `rehypeRaw` unless told not to."""
        self.assertIn("rehypeRaw", _render(rx.markdown("<script>alert(1)</script>")))

    def test_risk_8_a_chat_message_is_rendered_without_raw_html(self):
        rendered = _render(screens._sessions())
        plugins = re.findall(r"rehypePlugins:\[[^\]]*\]", rendered)
        self.assertTrue(plugins)
        self.assertFalse(any("rehypeRaw" in p for p in plugins), plugins)


if __name__ == "__main__":
    unittest.main()
