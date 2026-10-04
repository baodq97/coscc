"""What the page's own components say, read from their render.

Nothing here opens a browser. `screens.index()` is rendered to Reflex's component dict and
searched as text: every literal, placeholder and aria-label the app writes is in it, and no
artifact content is (that arrives at run time)."""

from __future__ import annotations

import json
import re
import unittest

import reflex as rx

from coscc import screens
from coscc.state import present
from coscc.service.common import OUTCOME_LABEL
from coscc.screens.board import _guide_panel
from coscc.screens.board import _unit_card
from coscc.screens.overview import _empty_board
from coscc.screens.settings import _autopilot_settings
from coscc.screens.unit import _integration_panel
from coscc.screens.unit import _outcome_panel
from coscc.screens.unit import _questions_tab

VIETNAMESE = re.compile(
    r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", re.I
)


def _render(component) -> str:
    return json.dumps(component.render(), ensure_ascii=False, default=str)


class ThePage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = _render(screens.index())

    def test_no_input_asks_for_a_name(self):
        labels = re.findall(
            r'(?:placeholder|ariaLabel|aria_label)\s*:\s*\\?"((?:[^"\\]|\\.)*?)\\?"', self.page
        )
        # The slug of a new unit is a name for the problem, not for a person.
        named = [
            x
            for x in labels
            if re.search(r"\bname\b|\btên\b|your name", x, re.I)
            and x not in ("Name for the work unit", "short-name-for-the-problem")
        ]
        self.assertEqual(named, [])
        for gone in (
            "answer-by",
            "stop-by",
            "hold-by",
            "update-by",
            "outcome-recorded-by",
            "backlog-backlog_by",
        ):
            self.assertNotIn(f'"{gone}\\"', self.page)
            self.assertNotIn(f'\\"{gone}\\"', self.page)

    def test_the_apps_own_text_is_english(self):
        """No word of the page is left in Vietnamese; nothing waits for a later translation."""
        left = sorted(
            {
                m.group(0)
                for m in re.finditer(r"\S*" + VIETNAMESE.pattern[:-1] + r"]\S*", self.page, re.I)
            }
        )
        self.assertEqual(left, [])

    def test_the_label_tables_are_english(self):
        for table in (
            present.RELATION_LABEL,
            OUTCOME_LABEL,
            present.RESULT_LABEL,
            present.MEASURER_LABEL,
        ):
            self.assertFalse(any(VIETNAMESE.search(v) for v in table.values()))

    def test_the_outcome_form_says_one_thing(self):
        form = _render(_outcome_panel())
        for gone in (
            "Appended to intent.md",
            "no gate reads it",
            "Neither name",
            "person's name",
            "đạt",
            "trượt",
            "không đo được",
        ):
            self.assertNotIn(gone, form)
        self.assertIn("Adds an outcome block to intent.md.", form)
        self.assertIn("could not be measured", form)
        self.assertIn('"You"', form.replace('\\"', '"'))

    def test_a_timeline_row_keeps_its_session_behind_details(self):
        for gone in ('"Xem"', "/ session ", "Read from the run log", "(still running)"):
            self.assertNotIn(gone, self.page.replace('\\"', '"'))
        self.assertIn("Every run of this unit, oldest first.", self.page)
        self.assertIn("run-", self.page)

    def test_no_variable_name_or_path_until_opened(self):
        for part in (_empty_board(), screens._workspaces_screen(), screens._activity()):
            self.assertNotIn("COS_", _render(part))
        # A workspace's `path` field, read anywhere on the overview.
        self.assertNotIn('?.["path"]', _render(screens._overview()).replace("\\", ""))
        cards = _render(screens._workspaces_screen())
        self.assertIn("ws-path-", cards)
        self.assertIn("Set by the app's environment", cards)

    def test_one_sentence_and_no_limits(self):
        for gone in (
            "Nothing re-asks on its own",
            "Bars share",
            "not an approval",
            "verbatim",
            "Whoever holds the",
        ):
            self.assertNotIn(gone, self.page)

    def test_the_questions_tab_asks_no_name_and_no_agent_answers_for_the_person(self):
        tab = _render(_questions_tab())
        self.assertIn("Send this answer", tab)
        for gone in (
            "Ask Jera",
            "ask-jera",
            "Answered by Jera",
            "Jera's proposal",
            "use-proposal-",
            "jera-said",
        ):
            self.assertNotIn(gone, tab)
        settings = _render(screens._settings())
        for gone in (
            "Decision preferences",
            "decision-preferences",
            "decision-rules-panel",
            "names-panel",
        ):
            self.assertNotIn(gone, settings)

    def test_needs_review_is_gone(self):
        self.assertNotIn("Needs review", self.page)
        self.assertIn("Needs you", self.page)

    def test_the_backlog_has_its_route_and_the_board_lost_its_panels(self):
        board = _render(screens._board())
        self.assertNotIn("update-panel", board)
        self.assertNotIn("backlog-panel", board)
        self.assertIn("update-panel", _render(screens._settings()))
        self.assertIn("backlog-panel", _render(screens._backlog_screen()))

    def test_no_apply_now_button_is_drawn(self):
        # One Apply per channel, and no list of work it would cut.
        settings = _render(screens._settings())
        self.assertIn("update-apply-release", settings)
        for gone in ("update-now-", "Apply now", "update-cut-list", "update-confirm-now"):
            self.assertNotIn(gone, settings)

    def test_the_autopilot_panel_and_its_settings(self):
        """Settings holds the four controls, and no variable name reaches the board's panel or
        Settings (S3)."""
        settings = _render(screens._settings())
        for control in (
            "autopilot-panel",
            "autopilot-on",
            "autopilot-ship",
            "autopilot-parallel",
            "autopilot-cap",
        ):
            self.assertIn(control, settings)
        self.assertNotIn("COS_", _render(_autopilot_settings()))
        self.assertNotIn("COS_", _render(_guide_panel()))

    def test_board_carries_the_guide_panel_in_place_of_the_strip(self):
        """Four lists, the cap line kept, the empty shortlist and Backlog outside the closed
        part, one sentence and Settings when off."""
        board = _render(screens._board())
        self.assertIn("guide-panel", board)
        self.assertNotIn("autopilot-strip", board)
        panel = _render(_guide_panel())
        for said in (
            "RUNNING",
            "NEEDS YOU",
            "HELD BACK",
            "NOTES",
            "guide-running",
            "guide-needs-you",
            "guide-held",
            "guide-notes",
            "held back",
            "autopilot_cap",
            "Autopilot is on",
            "settings_href",
            "backlog_href",
            "Nothing is on the shortlist, so the autopilot starts nothing.",
            "The autopilot is off, so nothing starts on its own.",
        ):
            self.assertIn(said, panel)
        self.assertEqual(panel.count('"name": "\\"ul\\""'), 4, "each list is a list (S5)")
        self.assertLess(panel.index("guide-no-shortlist"), panel.index("guide-lists"))
        self.assertNotIn("Leif", panel)

    def test_the_dialog_overview_carries_every_field_the_card_let_go(self):
        """Every field the card let go is read by the dialog's header or its overview tab, which
        is open without a further click."""
        dialog = _render(screens._detail_dialog()).replace('\\"', '"')
        for field in (
            "open_questions",
            "answerable",
            "integration_state",
            "outcome_text",
            "shortlist_rank",
            "relations_text",
            "mode",
            "problems",
            "waits_for",
            "summary",
            "title",
            "state_label",
            "id",
        ):
            self.assertRegex(dialog, r'current_unit\w*\?\.\["' + field + r'"\]', field)
        self.assertIn("waiting on you", dialog)
        self.assertIn("unit-badges", dialog)

    def test_a_card_is_one_line_with_its_number_title_and_state_word(self):
        from coscc.screens.common import P

        card = _render(_unit_card(P.cards[0])).replace('\\"', '"')
        self.assertRegex(card, r"data-state|dataState")
        self.assertRegex(card, r"text-overflow|textOverflow")
        self.assertIn('["state_label"]', card)
        for gone in (
            "Next: ",
            "waiting on you",
            "main: ",
            "outcome: ",
            "waits for ",
            '["relations_text"]',
            '["live"]',
        ):
            self.assertNotIn(gone, card)

    def test_the_lanes_come_from_the_board_read_and_no_screen_names_a_stage(self):
        """With `StudioStateStages` (`test_state.py`), which builds the board's counts on two
        made-up stages."""
        from pathlib import Path

        repo = Path(__file__).resolve().parents[2]
        from coscc.loop import STAGE_NAMES as stages

        self.assertEqual(len(stages), 9)
        named = re.compile(r"""["'](%s)["']""" % "|".join(stages))
        found = []
        for path in sorted((repo / "coscc" / "screens").glob("*.py")):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                # A screen's name, not a stage's.
                if named.search(line) and '("idea", _idea_screen())' not in line:
                    found.append(f"{path.name}:{n}: {line.strip()}")
        self.assertEqual(found, [])
        board = _render(screens._board())
        self.assertRegex(board, r'"iterable": "[\w.]*\.stages_rx_state_"')
        self.assertIn("column-", board)
        self.assertIn("lane-label", board)

    def test_the_board_puts_the_lanes_before_the_forms(self):
        board = _render(screens._board())
        order = [
            "guide-panel",
            "board-grid",
            "done-group",
            "running-steps",
            "new-unit-slug",
            "new-idea-slug",
        ]
        # Where each is drawn, not where the toolbar's buttons name it.
        at = [board.replace('\\"', '"').find(f'id:"{name}"') for name in order]
        self.assertNotIn(-1, at, dict(zip(order, at)))
        self.assertEqual(at, sorted(at), dict(zip(order, at)))
        for said in ("board-new-unit", "board-new-idea", "done-count"):
            self.assertIn(said, board)
        # An empty lane's; the empty board's "Nothing here yet." stays.
        for gone in ("paused-group", '"Nothing here"'):
            self.assertNotIn(gone, board.replace('\\"', '"'))

    def test_the_guide_lists_sit_in_a_closed_part(self):
        """The guide panel's lists sit in a `details`, which is closed until opened."""
        panel = _render(_guide_panel())
        # One item is "1 needs you", not "1 need you".
        for said in ("guide-lists", " need you", " needs you", "details", "guide-needs-you"):
            self.assertIn(said, panel)
        self.assertNotIn("open:", panel.replace('\\"', '"'))

    def test_every_column_may_carry_its_agent_and_settings_lists_them(self):
        """The glyph is a button with its label for hover and screen readers, drawn in the system's
        runic fonts."""
        board = _render(screens._board())
        for said in (
            "stage-glyph",
            "stage_glyphs",
            "stage_labels",
            "stage_notes",
            "Noto Sans Runic",
        ):
            self.assertIn(said, board)
        # Settings no longer sets who an agent is: the Agents page does.
        self.assertNotIn("agents-panel", _render(screens._settings()))

    def test_settings_has_the_decisions_panel(self):
        """S7: the form asks no name; S8: *Withdraw* only on a decision in force."""
        from coscc.screens import settings as page

        settings = _render(screens._settings())
        for said in (
            "decisions-panel",
            "add-decision",
            "decision-text",
            "decision-source",
            "decision-until",
            "decision-workspace",
            "Withdraw",
            "No decision has been added yet.",
        ):
            self.assertIn(said, settings)
        form = _render(page._decisions_panel())
        self.assertNotRegex(form, r"(?i)your name|answered.by|decided.by")
        row = _render(page._decision_row(screens.P.decision_rows[0]))
        self.assertIn("in_force", row.split("Withdraw")[0])
        self.assertNotIn("COS_", form)

    def test_settings_has_the_import_report_as_a_table(self):
        """S5: one row per field, with a sentence when there is none; S3: no path."""
        from coscc.screens import settings as page

        settings = _render(screens._settings())
        for said in (
            "import-panel",
            "Import report",
            "Workspace",
            "Field",
            "Every field of every unit was read.",
        ):
            self.assertIn(said, settings)
        panel = _render(page._import_panel())
        self.assertIn("import_rows", panel)
        self.assertNotIn("COS_", panel)
        self.assertNotRegex(panel, r"/home/|/tmp/")

    def test_settings_lost_the_agents_models_and_grants_panels_and_their_links(self):
        """R10: no panel for who an agent is, what it runs on or what it may do, and no link in
        `settings-index` to one."""
        from coscc.screens import settings as page

        settings = _render(screens._settings())
        for gone in ("agents-panel", "models-panel", "grants-panel", "agent-row", "model-row"):
            self.assertNotIn(gone, settings)
        self.assertEqual(
            {t for _, t in page.SECTIONS} & {"agents-panel", "models-panel", "grants-panel"}, set()
        )
        for _, target in page.SECTIONS:
            self.assertIn(f'"{target}"', settings.replace('\\"', '"'), target)


class TheAgentsScreen(unittest.TestCase):
    """`/agents` on rows shaped like the capture fixture: a `spec` run that ended `done` for
    $0.52 and an `impl` run that ended `failed`."""

    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path

        from coscc.agent.sessions import Sessions
        from coscc.config import Config
        from coscc.service import Service
        from coscc.state.views import agent_views

        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        (root / "work").mkdir()
        config = Config(workspaces=(), working_dir=str(root / "work"), data_dir=str(root / "data"))
        cls.service = Service(config, Sessions(config))
        journal = cls.service.ws.journal()
        journal.finished("proj", "0002_u", "spec", "done", turns=4, cost_usd=0.52)
        journal.finished("proj", "0002_u", "impl", "failed", turns=109)
        cls.page = cls.service.agents.agent_page()
        cls.table, cls.details, cls.others = agent_views(cls.page)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_the_table_has_the_eight_agents_failed_first(self):
        by = {r.key: r for r in self.table}
        self.assertEqual(len(self.table), 8)
        self.assertEqual((by["impl"].chip, by["impl"].color), ("failed", "red"))
        self.assertEqual((by["spec"].chip, by["spec"].color), ("ok", "grass"))
        self.assertEqual(by["idea"].chip, "idle")
        self.assertEqual(self.table[0].key, "impl")
        self.assertEqual(by["spec"].outcome, "done")
        self.assertEqual(by["spec"].when, "just now")
        self.assertEqual(by["spec"].cost, "$0.520")
        self.assertEqual(by["idea"].outcome, "—")
        impl = next(r for r in self.page["rows"] if r["key"] == "impl")
        # Copied from the service, never worked out here.
        self.assertEqual(by["impl"].model, impl["config"]["model"])
        self.assertEqual(by["impl"].turns, "120")

    def test_other_sessions_keep_their_fields(self):
        by = {r.key: r for r in self.others}
        self.assertEqual(list(by), ["estimate", "chat"])
        self.assertTrue(by["estimate"].has_effort)
        self.assertFalse(by["chat"].has_effort)

    def test_a_drawer_holds_the_grant_the_boxes_and_the_runs(self):
        by = {d.key: d for d in self.details}
        impl, idea = by["impl"], by["idea"]
        self.assertEqual(impl.skill, "write-impl")
        self.assertEqual([f.field for f in impl.fields], ["model", "effort", "turns", "budget"])
        # The two ceilings of `impl:novel` are its own boxes, inside impl's drawer.
        self.assertEqual(
            [(f.row, f.field) for f in impl.variants],
            [
                ("impl:novel", "model"),
                ("impl:novel", "effort"),
                ("impl:novel", "turns"),
                ("impl:novel", "budget"),
            ],
        )
        self.assertEqual([r.outcome for r in impl.runs], ["failed"])
        self.assertEqual((impl.submits, impl.chip), ("yes", "failed"))
        budget = next(f for f in idea.fields if f.field == "budget")
        self.assertEqual((budget.value, budget.source, budget.overridden), ("none", "none", False))
        self.assertEqual((idea.chip, idea.runs, idea.variants), ("idle", [], []))

    def test_the_screen_draws_the_table_the_drawer_and_the_other_sessions(self):
        shown = _render(screens.agents.agents_screen()).replace('\\"', '"')
        for said in (
            "agents-table",
            "agent-row",
            "agent-drawer",
            "other-sessions",
            "agent-filter",
            "agent-problem",
            "Other sessions",
            "Cost, 30 days",
            "No agent has that status.",
            "No run yet.",
            "reset_agent_field",
            "agent-ceiling-note",
        ):
            self.assertIn(said, shown)
        # One sentence beside the boxes that raise what a step spends, and only one (S2).
        self.assertEqual(shown.count("lets each step spend more"), 1)

    def test_the_grant_is_drawn_and_never_written(self):
        """R4: no box, button or handler of the page takes a grant."""
        from coscc.screens.agents import _grant
        from coscc.state import StudioState

        grant = _render(_grant())
        for writes in ("Input", "input", "onSubmit", "Save", "reset_agent"):
            self.assertNotIn(writes, grant)
        # S3: a long list of commands is behind an open button.
        self.assertIn("agent-commands", grant)
        handlers = [n for n in StudioState.event_handlers if "grant" in n.lower()]
        self.assertEqual(handlers, [])

    def test_the_nav_lists_agents_on_every_page(self):
        from coscc.state.views import NAVIGATION

        self.assertIn("agents", [k for k, _, _ in NAVIGATION])
        page = _render(screens.index()).replace('\\"', '"')
        self.assertIn("nav-agents", page)
        self.assertIn("mobile-nav-agents", page)

    def test_the_drawer_has_an_address_of_its_own(self):
        from coscc.state import place

        p = place.Place("agents", "proj", agent="impl")
        self.assertEqual(place.href(p), "/agents?ws=proj&agent=impl")
        self.assertEqual(place.read("/agents", "ws=proj&agent=impl"), p)


class TheIntegrationPanel(unittest.TestCase):
    def test_a_zero_count_is_never_drawn(self):
        """`conflicting` with behind 0 used to read "0 commit(s) behind" beside it."""
        panel = _render(_integration_panel())
        self.assertNotIn("commit(s)", panel)
        self.assertIn("as of the last fetch", panel)
        # The count is drawn only when it is neither empty nor "0".
        self.assertIn(r"integration_behind\"]?.valueOf?.() === \"0\"", panel)


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
    """A file the UI standard does not list is no UI file to the `ship` gate."""

    def test_ui_standard_md_paths_name_every_new_screen_file(self):
        import fnmatch
        from pathlib import Path

        from coscc.agent.instructions import scoped_patterns

        text = (
            Path(__file__).resolve().parents[2] / ".claude" / "rules" / "ui-standard.md"
        ).read_text(encoding="utf-8")
        globs = scoped_patterns(text) or []
        for path in ("coscc/screens/idea.py", "coscc/state/ideas.py"):
            self.assertTrue(any(fnmatch.fnmatch(path, g) for g in globs), path)

    def test_the_idea_page_is_drawn(self):
        from coscc.screens.idea import _idea_screen

        self.assertIsNotNone(_idea_screen())


if __name__ == "__main__":
    unittest.main()
