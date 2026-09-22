"""The preview must remain useful without becoming a second business backend."""

import ast
import inspect
import unittest

import reflex as rx

from cos_baodo import prototype, prototype_data
from cos_baodo.prototype import PrototypeState


class DemoStateTests(unittest.TestCase):
    def setUp(self):
        self.state = PrototypeState(_reflex_internal_init=True)

    def test_component_tree_accepts_installed_reflex_event_signatures(self):
        self.assertIsInstance(prototype.index(), rx.Component)

    def test_fixtures_are_fresh_and_references_are_valid(self):
        first = prototype_data.workspaces()
        first[0].name = "Changed"
        self.assertEqual(prototype_data.workspaces()[0].name, "Atlas")
        workspace_ids = {w.id for w in self.state.workspaces}
        self.assertTrue(all(w.workspace in workspace_ids for w in self.state.work))
        self.assertTrue(all(c.workspace in workspace_ids for c in self.state.conversations))
        self.assertEqual(len({w.id for w in self.state.work}), len(self.state.work))

    def test_usage_is_derived_from_selected_workspace(self):
        self.assertEqual(self.state.token_total, "78,900")
        self.assertEqual(self.state.cost_total, "$1.17")
        self.state.choose_workspace("fieldnotes")
        self.assertEqual(self.state.token_total, "4,300")
        self.assertEqual(self.state.cost_total, "$0.07")

    def test_workspace_switch_clears_context_but_preserves_conversations(self):
        self.state.open_unit("COS-014")
        self.state.query = "home"
        self.state.prompt = "Unsent"
        self.state.choose_workspace("fieldnotes")
        self.assertEqual(self.state.unit_id, "")
        self.assertEqual(self.state.query, "")
        self.assertEqual(self.state.prompt, "")
        self.assertEqual(self.state.session_id, "fieldnotes-1")
        self.assertTrue(all(w.workspace == "fieldnotes" for w in self.state.visible_work))
        self.state.open_unit("COS-014")
        self.assertIn("not in the selected workspace", self.state.notice)

    def test_workspace_create_rename_remove_and_validation(self):
        state = self.state
        state.edit_workspace("")
        state.save_workspace()
        self.assertIn("name", state.form_error)
        state.workspace_name = "atlas"
        state.save_workspace()
        self.assertIn("already", state.form_error)
        state.workspace_name = "New workspace"
        state.save_workspace()
        workspace_id = state.workspaces[-1].id
        state.choose_workspace(workspace_id)
        self.assertEqual(state.workspace_work, [])
        self.assertEqual(state.workspace_sessions, [])
        state.edit_workspace(workspace_id)
        state.workspace_name = "Renamed"
        state.save_workspace()
        self.assertEqual(state.current_workspace.name, "Renamed")
        state.request_remove(workspace_id)
        state.remove_workspace()
        self.assertEqual(state.workspace_id, "atlas")
        self.assertEqual(len(state.workspaces), 3)

    def test_removal_cascades_only_within_demo_and_handles_last_workspace(self):
        state = self.state
        for workspace_id in ("atlas", "fieldnotes", "orbit"):
            state.request_remove(workspace_id)
            state.remove_workspace()
        self.assertEqual(state.workspaces, [])
        self.assertEqual(state.work, [])
        self.assertEqual(state.conversations, [])
        self.assertEqual(state.activities, [])
        self.assertEqual(state.current_workspace.name, "No workspace")
        state.new_session()
        self.assertIn("Create a workspace", state.notice)

    def test_search_and_filter_are_composable(self):
        state = self.state
        state.search_work("home")
        self.assertEqual([w.id for w in state.visible_work], ["COS-014"])
        state.filter_work("Needs review")
        self.assertEqual(state.visible_work, [])
        state.search_work("")
        self.assertEqual(len(state.visible_work), 2)

    def test_demo_chat_history_is_scoped_and_not_lost_on_switch(self):
        state = self.state
        state.prompt = "Test message"
        state.send_message()
        self.assertEqual(state.current_session.messages[-1].role, "demo")
        self.assertIn("no model was called", state.current_session.messages[-1].text)
        state.choose_session("atlas-2")
        self.assertNotIn("Test message", [m.text for m in state.current_session.messages])
        state.choose_session("atlas-1")
        self.assertEqual(state.current_session.messages[-2].text, "Test message")
        state.choose_workspace("fieldnotes")
        state.choose_session("atlas-1")
        self.assertEqual(state.session_id, "fieldnotes-1")
        self.assertIn("not in the selected workspace", state.notice)

    def test_new_work_and_session_stay_in_current_workspace(self):
        state = self.state
        state.choose_workspace("orbit")
        state.new_work_title = "An example outcome"
        state.create_work()
        self.assertEqual(state.workspace_work[0].stage, "intent")
        self.assertEqual(state.workspace_work[0].lane, "Planned")
        state.new_session()
        state.prompt = "A new conversation"
        state.send_message()
        self.assertEqual(state.current_session.title, "A new conversation")
        self.assertEqual(state.current_session.workspace, "orbit")

    def test_invalid_ui_values_are_reported(self):
        state = self.state
        for event in (state.navigate, state.preview_scenario, state.set_density,
                      state.filter_work, state.set_board_view, state.set_mode):
            state.notice = ""
            event("not-a-choice")
            self.assertTrue(state.notice)

    def test_single_choice_controls_reject_multiple_selection(self):
        state = self.state
        state.open_unit("COS-014")
        for event, value in (
            (state.filter_work, ["All work"]),
            (state.set_board_view, ["List"]),
            (state.set_mode, ["manual"]),
        ):
            state.notice = ""
            event(value)
            self.assertTrue(state.notice)
        self.assertEqual(state.focus, "All work")
        self.assertEqual(state.board_view, "Board")
        self.assertEqual(state.current_unit.mode, "autonomous")

    def test_presentation_modules_do_not_import_business_backends(self):
        for module in (prototype, prototype_data):
            tree = ast.parse(inspect.getsource(module))
            imports = [
                node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            ] + [
                name.name for node in ast.walk(tree) if isinstance(node, ast.Import) for name in node.names
            ]
            for forbidden in ("service", "sessions", "runner", "gitops", "subprocess", "claude_agent_sdk"):
                self.assertFalse(any(forbidden in name.split(".") for name in imports), forbidden)


if __name__ == "__main__":
    unittest.main()
