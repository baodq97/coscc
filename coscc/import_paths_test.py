"""`0095` R3: every name code or a test reached in the four largest modules still resolves.

`coscc/service.py`, `state.py`, `screens.py` and `runner.py` were split into modules of their
own. The names below are the ones something in this repository imported from those four,
patched on them, or cited under `.claude/`, collected on `5d161a2`, before the split. Each
of the four imports them back, so `from coscc.<module> import <name>` still works; a name
that was dropped by the split fails here rather than in whichever caller used it.
"""

from __future__ import annotations

import importlib
import unittest

NAMES = {
    "service": (
        "CI_REFRESH", "COLLAPSED_STATES", "CONSEQUENCE", "Invalid", "NotUpdatable",
        "OUTCOME_RESULTS", "RETAKE_REFUSED", "Runner", "STAGE_FILES", "STATE_COLOR",
        "STATE_LABEL", "Service", "StaleCutList", "Updating", "_now", "attention_reason",
        "date", "describe_base", "events", "integration_since_review", "outcome_label",
        "reason_beside", "shown_state", "step_cwd", "unit_state",
    ),
    "state": (
        "API", "Activity", "AnomalyRow", "AutopilotStop", "BacklogRow", "COST_NOTE", "Card",
        "Cell", "Event", "GONE_AFTER", "GrantRow", "Invalid", "Knob", "MESSAGE_CUT", "Message",
        "ModelRow", "NAVIGATION", "NO_RUN_NOTE", "Question", "READ_ONLY_NOTE", "RUNNING_POLL",
        "Round", "Run", "SERVICE", "STATUS_COLOR", "SpendRow", "StudioState", "TokenRow",
        "Unit", "UsageRow", "WATCH_WINDOW", "WasteRow", "WatchEvent", "Workspace", "_ASKING",
        "_POLLING", "_activities", "_asking", "_card", "_cell_label", "_hold_detail",
        "_hold_fields", "_outcome_fields", "_questions", "_relations_text", "_run_target",
        "_run_waiting", "_shown", "_tab_gone", "_tokens", "_usd", "_watch_note",
        "backlog_view", "cost_note", "events_mod", "rx",
    ),
    "screens": (
        "_activity", "_autopilot_settings", "_autopilot_strip", "_backlog_screen", "_banners",
        "_board", "_detail_dialog", "_empty_board", "_integration_panel", "_outcome_panel",
        "_overview", "_questions_tab", "_sessions", "_settings", "_unit_card",
        "_workspaces_screen", "index",
    ),
    "runner": (
        "ATTEMPT_EXCERPT", "CEILING_MARKERS", "CLAUDE_CODE_PRESET", "CLOSING_TIMEOUT",
        "COMMANDS_ADVICE", "COMMANDS_HEADING", "Denials", "KNOWLEDGE_ADVICE",
        "PLAN_MAP_ADVICE", "PLAN_MAP_HEADING", "PRIOR_FINDINGS_ADVICE",
        "PRIOR_FINDINGS_HEADING", "RunError", "Runner", "SESSIONS_PER_STEP", "STATUS_RE",
        "_POINTING", "_jera_answers", "_joined", "_rounds", "_tree_state", "_unfence",
        "_write_artifact", "answers_section", "build_prompt", "check_reply",
        "closing_round_problem", "compose_prompt", "describe_attempt", "from_title",
        "merge_review", "open_findings", "opening_problem", "opening_reason",
        "permission_gate", "sessions_mod", "skill_for", "snapshot", "strip_answers",
        "with_answers",
    ),
}


class EveryNameAModuleWasReachedByStillResolves(unittest.TestCase):
    def test_each_name_is_still_on_its_module(self):
        for module, names in NAMES.items():
            mod = importlib.import_module(f"coscc.{module}")
            for name in names:
                with self.subTest(module=module, name=name):
                    self.assertTrue(hasattr(mod, name), f"coscc.{module}.{name} no longer resolves")


if __name__ == "__main__":
    unittest.main()
