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

from coscc import screens
from coscc.web import present

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

    def test_0043_the_autopilot_panel_and_its_settings(self):
        """`0043` R2, R9: Settings holds the four controls, and no variable name reaches the
        board's panel or Settings (S3). Since `0101` the board's strip is the guide panel."""
        settings = _render(screens._settings())
        for control in ("autopilot-panel", "autopilot-on", "autopilot-ship", "autopilot-parallel", "autopilot-cap"):
            self.assertIn(control, settings)
        self.assertNotIn("COS_", _render(screens._autopilot_settings()))
        self.assertNotIn("COS_", _render(screens._guide_panel()))

    def test_board_carries_the_guide_panel_in_place_of_the_strip(self):
        """`0101` R10: three lists, the cap line kept, one sentence and Settings when off."""
        board = _render(screens._board())
        self.assertIn("guide-panel", board)
        self.assertNotIn("autopilot-strip", board)
        panel = _render(screens._guide_panel())
        for said in ("RUNNING", "NEEDS YOU", "DECIDED FOR YOU", "guide-running", "guide-needs-you",
                     "guide-decided", "autopilot_cap", "Autopilot is on", "settings_href",
                     "The autopilot is off, so nothing starts on its own."):
            self.assertIn(said, panel)
        self.assertEqual(panel.count('"name": "\\"ul\\""'), 3, "each list is a list (S5)")
        self.assertNotIn("Leif", panel)

    def test_0101_the_proposal_button_and_the_rules_box(self):
        """`0101` R8, R9: the button fills the box and sends nothing; the rules box says which
        questions no rule removes (S1, S2)."""
        tab = _render(screens._questions_tab())
        self.assertIn("Use this proposal", tab)
        self.assertIn("use-proposal-", tab)
        settings = _render(screens._settings())
        for said in ("decision-rules-panel", "Decision rules", "Leave empty to use the default rules.",
                     "always go to you"):
            self.assertIn(said, settings)

    def test_0133_the_dialog_overview_carries_every_field_the_card_let_go(self):
        """`0133` R7: every field a card showed before `0133` is read by the dialog's header or
        its overview tab, which is open without a further click."""
        dialog = _render(screens._detail_dialog()).replace('\\"', '"')
        for field in ("open_questions", "answerable", "integration_state", "outcome_text",
                      "shortlist_rank", "owner", "relations_text", "mode", "problems",
                      "waits_for", "summary", "title", "state_label", "id"):
            self.assertRegex(dialog, r'current_unit\w*\?\.\["' + field + r'"\]', field)
        self.assertIn("waiting on you", dialog)
        self.assertIn("unit-badges", dialog)

    def test_0133_a_card_is_one_line_with_its_number_title_and_state_word(self):
        """`0133` R3, R4, R7."""
        from coscc.screens.common import P

        card = _render(screens._unit_card(P.cards[0])).replace('\\"', '"')
        self.assertRegex(card, r"data-state|dataState")
        self.assertRegex(card, r"text-overflow|textOverflow")
        self.assertIn('["state_label"]', card)
        for gone in ("Next: ", "waiting on you", "main: ", "outcome: ", "waits for ",
                     '["relations_text"]', '["live"]'):
            self.assertNotIn(gone, card)

    def test_0133_the_lanes_come_from_the_board_read_and_no_screen_names_a_stage(self):
        """`0133` R8. With `StudioStateStages` (`state_test.py`), which builds the board's
        counts on two made-up stages."""
        from pathlib import Path

        repo = Path(__file__).resolve().parents[2]
        mjs = (repo / ".claude" / "scripts" / "cos.mjs").read_text(encoding="utf-8")
        stages = re.findall(r"name: '(\w+)'", mjs.split("const STAGES = [", 1)[1].split("\n]", 1)[0])
        self.assertEqual(len(stages), 9)
        named = re.compile(r"""["'](%s)["']""" % "|".join(stages))
        found = []
        for path in sorted((repo / "coscc" / "screens").glob("*.py")):
            if path.name.endswith("_test.py"):
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                # A screen's name, not a stage's.
                if named.search(line) and '("idea", _idea_screen())' not in line:
                    found.append(f"{path.name}:{n}: {line.strip()}")
        self.assertEqual(found, [])
        board = _render(screens._board())
        self.assertRegex(board, r'"iterable": "[\w.]*\.stages_rx_state_"')
        self.assertIn("column-", board)
        self.assertIn("lane-label", board)

    def test_0133_the_board_puts_the_lanes_before_the_forms(self):
        """`0133` R5, R10, Design *the order of `/board`*."""
        board = _render(screens._board())
        order = ["guide-panel", "board-grid", "done-group", "running-steps", "new-unit-slug",
                 "new-idea-slug"]
        # Where each is drawn, not where the toolbar's buttons name it.
        at = [board.replace('\\"', '"').find(f'id:"{name}"') for name in order]
        self.assertNotIn(-1, at, dict(zip(order, at)))
        self.assertEqual(at, sorted(at), dict(zip(order, at)))
        for said in ("board-new-unit", "board-new-idea", "done-count"):
            self.assertIn(said, board)
        # An empty lane's; the empty board's "Nothing here yet." stays.
        for gone in ("paused-group", '"Nothing here"'):
            self.assertNotIn(gone, board.replace('\\"', '"'))

    def test_0133_the_guide_lists_sit_in_a_closed_part(self):
        """`0133` R11, on `0101`'s guide panel, which took the strip's place: a `details` is
        closed until opened."""
        panel = _render(screens._guide_panel())
        # Review F2: one item is "1 needs you", not "1 need you".
        for said in ("guide-lists", " need you ", " needs you ", "details", "guide-needs-you"):
            self.assertIn(said, panel)
        self.assertNotIn("open:", panel.replace('\\"', '"'))

    def test_0036_every_column_may_carry_its_agent_and_settings_lists_them(self):
        """`0036` R2, R5: the glyph is a button with its label for hover and screen readers,
        drawn in the system's runic fonts; Settings has one form per agent, Reset hidden (S8)."""
        board = _render(screens._board())
        for said in ("stage-glyph", "stage_glyphs", "stage_labels", "stage_notes", "Noto Sans Runic"):
            self.assertIn(said, board)
        settings = _render(screens._settings())
        for said in ("agents-panel", "agent-row", "A change applies to the next step that starts.",
                     "agent_problems", "Reset"):
            self.assertIn(said, settings)
        self.assertNotIn("COS_", _render(screens.settings._agent_row(screens.P.agent_rows[0])))

    def test_settings_has_the_decisions_and_names_panels(self):
        """`0137` R5, R6; S7: the form asks no name; S8: *Withdraw* only on a decision in force."""
        from coscc.screens import settings as page

        settings = _render(screens._settings())
        for said in ("decisions-panel", "names-panel", "add-decision", "decision-text", "decision-source",
                     "decision-until", "decision-workspace", "This was me", "Withdraw",
                     "No decision has been added yet."):
            self.assertIn(said, settings)
        form = _render(page._decisions_panel())
        self.assertNotRegex(form, r"(?i)your name|answered.by|decided.by")
        row = _render(page._decision_row(screens.P.decision_rows[0]))
        self.assertIn("in_force", row.split("Withdraw")[0])
        self.assertNotIn("COS_", _render(page._names_panel()) + form)

    def test_settings_has_the_import_report_as_a_table(self):
        """`0135` R4; S5: one row per field, with a sentence when there is none; S3: no path."""
        from coscc.screens import settings as page

        settings = _render(screens._settings())
        for said in ("import-panel", "Import report", "Workspace", "Field", "Every field of every unit was read."):
            self.assertIn(said, settings)
        panel = _render(page._import_panel())
        self.assertIn("import_rows", panel)
        self.assertNotIn("COS_", panel)
        self.assertNotRegex(panel, r"/home/|/tmp/")

    def test_f2_a_grants_tools_are_a_list_behind_details(self):
        settings = _render(screens._settings())
        self.assertNotIn("tools: ", settings)
        self.assertNotIn("commands: ", settings)
        self.assertIn("What it may use", settings)
        self.assertIn("tool_list", settings)


# The script's last statements, so a render that cut it short is not counted as carrying it.
_NOTICE_TAIL = "setInterval(refresh, 60000);\n  connect();\n})();"


class TheNoticeScript(unittest.TestCase):
    def test_the_shell_carries_the_notice_script_once_and_it_touches_no_reflex_state(self):
        """`0113` R9. What it does in a browser is `scripts/e2e.py`'s to show."""
        js = screens._NOTICE_JS
        self.assertEqual(_render(screens.index()).count("window.__coscc_notices = true;"), 1)
        self.assertIn(_NOTICE_TAIL, js)
        for said in ("__coscc_notices", "coscc_notice_after", "/api/notices/follow", 'aria-label", "Dismiss'):
            self.assertIn(said, js)
        for reflex in ("_rx_state_", "addEvents", "__reflex"):
            self.assertNotIn(reflex, js)
        self.assertNotIn("__", js.replace("__coscc_", "").replace("__proto__", ""),
                         "a colour placeholder was left in")


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


class TheIdeaScreensAreUiFiles(unittest.TestCase):
    """`0040` C7. A file the UI standard does not list is no UI file to the `ship` gate."""

    def test_ui_standard_md_paths_name_every_new_screen_file(self):
        from pathlib import Path

        text = (Path(__file__).resolve().parents[2] / ".claude" / "rules" / "ui-standard.md").read_text(encoding="utf-8")
        for path in ("coscc/screens/idea.py", "coscc/state/ideas.py", "coscc/service/ideas.py"):
            self.assertIn(f'  - "{path}"', text, path)

    def test_the_idea_page_is_drawn(self):
        from coscc.screens.idea import _idea_screen

        self.assertIsNotNone(_idea_screen())


if __name__ == "__main__":
    unittest.main()
