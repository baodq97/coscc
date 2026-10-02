"""Modules talk through three declared channels, so a feature is added or removed without reaching in.

The channels are calls (public names, typed), events (`coscc/bus.py`) and data (one module owns
each table and runs its SQL). Each rule below is a ratchet over `coscc/`: today's findings are
listed in a literal, a new finding fails, and a listed finding that is gone fails too, so the
lists only shrink. `DICT_ANY` is keyed by `module:qualname`: a function that moves across modules is
an explicit replacement of its entry, and one that moves inside its module keeps it. `tests/` is not checked. Each check takes parsed trees, so a test can feed it
a planted case.
"""

from __future__ import annotations

import ast
import re
import unittest
from collections import Counter

from tests.test_layers import ROOT, _files

DATA = "coscc.data"

PRIVATE_IMPORTS: set[tuple[str, str, str]] = {
    ("coscc/runner/attempt.py", "coscc.runner.reply", "_unfence"),
    ("coscc/runner/prompt.py", "coscc.runner.review", "_ROUND_RE"),
    ("coscc/runner/prompt.py", "coscc.runner.review", "_header_status"),
    ("coscc/runner/prompt.py", "coscc.runner.review", "_round_meta"),
    ("coscc/runner/prompt.py", "coscc.runner.review", "_round_number"),
    ("coscc/runner/prompt.py", "coscc.runner.review", "_rounds"),
    ("coscc/runner/step.py", "coscc.runner.attempt", "_head_of"),
    ("coscc/runner/step.py", "coscc.runner.attempt", "_tree_state"),
    ("coscc/runner/step.py", "coscc.runner.attempt", "_write_artifact"),
    ("coscc/runner/step.py", "coscc.runner.prompt", "_read"),
    ("coscc/runner/step.py", "coscc.runner.reply", "_Stopped"),
    ("coscc/runner/step.py", "coscc.runner.reply", "_after_tool"),
    ("coscc/runner/step.py", "coscc.runner.reply", "_hit_ceiling"),
    ("coscc/runner/step.py", "coscc.runner.reply", "_joined"),
    ("coscc/runner/step.py", "coscc.runner.reply", "_title"),
    ("coscc/runner/step.py", "coscc.runner.reply", "_with_reply"),
    ("coscc/runner/step.py", "coscc.runner.review", "_round_number"),
    ("coscc/runner/step.py", "coscc.runner.review", "_rounds"),
    ("coscc/screens/__init__.py", "coscc.screens.backlog", "_backlog_screen"),
    ("coscc/screens/__init__.py", "coscc.screens.board", "_RECONNECT_JS"),
    ("coscc/screens/__init__.py", "coscc.screens.board", "_board"),
    ("coscc/screens/__init__.py", "coscc.screens.chrome", "_banners"),
    ("coscc/screens/__init__.py", "coscc.screens.chrome", "_sidebar"),
    ("coscc/screens/__init__.py", "coscc.screens.chrome", "_topbar"),
    ("coscc/screens/__init__.py", "coscc.screens.dialogs", "_WATCH_JS"),
    ("coscc/screens/__init__.py", "coscc.screens.dialogs", "_command_dialog"),
    ("coscc/screens/__init__.py", "coscc.screens.dialogs", "_mobile_dialog"),
    ("coscc/screens/__init__.py", "coscc.screens.dialogs", "_remove_dialog"),
    ("coscc/screens/__init__.py", "coscc.screens.dialogs", "_watch_dialog"),
    ("coscc/screens/__init__.py", "coscc.screens.dialogs", "_workspace_dialog"),
    ("coscc/screens/__init__.py", "coscc.screens.idea", "_idea_screen"),
    ("coscc/screens/__init__.py", "coscc.screens.overview", "_overview"),
    ("coscc/screens/__init__.py", "coscc.screens.overview", "_workspaces_screen"),
    ("coscc/screens/__init__.py", "coscc.screens.sessions", "_activity"),
    ("coscc/screens/__init__.py", "coscc.screens.sessions", "_cost"),
    ("coscc/screens/__init__.py", "coscc.screens.sessions", "_sessions"),
    ("coscc/screens/__init__.py", "coscc.screens.settings", "_settings"),
    ("coscc/screens/__init__.py", "coscc.screens.unit", "_detail_dialog"),
    ("coscc/screens/backlog.py", "coscc.screens.common", "_MONO"),
    ("coscc/screens/board.py", "coscc.screens.common", "_MONO"),
    ("coscc/screens/board.py", "coscc.screens.common", "_RUNIC"),
    ("coscc/screens/board.py", "coscc.screens.common", "_details"),
    ("coscc/screens/board.py", "coscc.screens.overview", "_empty_board"),
    ("coscc/screens/dialogs.py", "coscc.screens.chrome", "_nav"),
    ("coscc/screens/dialogs.py", "coscc.screens.chrome", "_workspace_select"),
    ("coscc/screens/dialogs.py", "coscc.screens.common", "_MONO"),
    ("coscc/screens/overview.py", "coscc.screens.chrome", "_event_row"),
    ("coscc/screens/overview.py", "coscc.screens.chrome", "_metrics"),
    ("coscc/screens/overview.py", "coscc.screens.common", "_details"),
    ("coscc/screens/sessions.py", "coscc.screens.board", "_update_warning"),
    ("coscc/screens/sessions.py", "coscc.screens.chrome", "_event_row"),
    ("coscc/screens/sessions.py", "coscc.screens.chrome", "_metrics"),
    ("coscc/screens/sessions.py", "coscc.screens.common", "_MONO"),
    ("coscc/screens/sessions.py", "coscc.screens.common", "_details"),
    ("coscc/screens/sessions.py", "coscc.screens.common", "_mono"),
    ("coscc/screens/sessions.py", "coscc.screens.common", "_table"),
    ("coscc/screens/settings.py", "coscc.screens.board", "_update_panel"),
    ("coscc/screens/settings.py", "coscc.screens.common", "_MONO"),
    ("coscc/screens/settings.py", "coscc.screens.common", "_RUNIC"),
    ("coscc/screens/settings.py", "coscc.screens.common", "_details"),
    ("coscc/screens/settings.py", "coscc.screens.common", "_table"),
    ("coscc/screens/unit.py", "coscc.screens.board", "_update_warning"),
    ("coscc/screens/unit.py", "coscc.screens.chrome", "_banners"),
    ("coscc/screens/unit.py", "coscc.screens.common", "_details"),
    ("coscc/screens/unit.py", "coscc.screens.sessions", "_unit_cost"),
    ("coscc/screens/unit.py", "coscc.screens.settings", "_settings_row"),
    ("coscc/service/board.py", "coscc.service.common", "_younger_than"),
    ("coscc/service/steps.py", "coscc.service.common", "_younger_than"),
    ("coscc/state/__init__.py", "coscc.state.views", "_POLLING"),
    ("coscc/state/__init__.py", "coscc.state.views", "_activities"),
    ("coscc/state/__init__.py", "coscc.state.views", "_anomaly_rows"),
    ("coscc/state/__init__.py", "coscc.state.views", "_asking"),
    ("coscc/state/__init__.py", "coscc.state.views", "_card"),
    ("coscc/state/__init__.py", "coscc.state.views", "_cell_label"),
    ("coscc/state/__init__.py", "coscc.state.views", "_ci_line"),
    ("coscc/state/__init__.py", "coscc.state.views", "_current_stage"),
    ("coscc/state/__init__.py", "coscc.state.views", "_hold_detail"),
    ("coscc/state/__init__.py", "coscc.state.views", "_hold_fields"),
    ("coscc/state/__init__.py", "coscc.state.views", "_initials"),
    ("coscc/state/__init__.py", "coscc.state.views", "_integration_fields"),
    ("coscc/state/__init__.py", "coscc.state.views", "_moves"),
    ("coscc/state/__init__.py", "coscc.state.views", "_number"),
    ("coscc/state/__init__.py", "coscc.state.views", "_outcome_fields"),
    ("coscc/state/__init__.py", "coscc.state.views", "_questions"),
    ("coscc/state/__init__.py", "coscc.state.views", "_relations_text"),
    ("coscc/state/__init__.py", "coscc.state.views", "_rounds"),
    ("coscc/state/__init__.py", "coscc.state.views", "_run_dropped"),
    ("coscc/state/__init__.py", "coscc.state.views", "_run_target"),
    ("coscc/state/__init__.py", "coscc.state.views", "_run_waiting"),
    ("coscc/state/__init__.py", "coscc.state.views", "_shown"),
    ("coscc/state/__init__.py", "coscc.state.views", "_spend_rows"),
    ("coscc/state/__init__.py", "coscc.state.views", "_tab_gone"),
    ("coscc/state/__init__.py", "coscc.state.views", "_title_of"),
    ("coscc/state/__init__.py", "coscc.state.views", "_token_row"),
    ("coscc/state/__init__.py", "coscc.state.views", "_tokens"),
    ("coscc/state/__init__.py", "coscc.state.views", "_unknown"),
    ("coscc/state/__init__.py", "coscc.state.views", "_usd"),
    ("coscc/state/__init__.py", "coscc.state.views", "_waste_rows"),
    ("coscc/state/answers.py", "coscc.state.views", "_key_label"),
    ("coscc/state/update.py", "coscc.state.views", "_channel_line"),
    ("coscc/state/update.py", "coscc.state.views", "_job_line"),
    ("coscc/state/watch.py", "coscc.state.views", "_watch_events"),
    ("coscc/state/watch.py", "coscc.state.views", "_watch_note"),
    ("coscc/units/backlog.py", "coscc.units.hold", "_line_problem"),
    ("coscc/units/ideas.py", "coscc.units", "_cos"),
}

OWNERS: dict[str, str] = {
    "auth": "coscc.data",
    "auth_sessions": "coscc.data",
    "decisions": "coscc.data",
    "idea_meta": "coscc.units.meta",
    "impl_claims": "coscc.units.meta",
    "migrations": "coscc.data",
    "outputs": "coscc.units.history",
    "prefs": "coscc.data",
    "pull_requests": "coscc.github.prmachine",
    "review_findings": "coscc.units.meta",
    "review_rounds": "coscc.units.meta",
    "runs": "coscc.runlog.journal",
    "stage_results": "coscc.units.meta",
    "step_events": "coscc.data",
    "step_runs": "coscc.data",
    "transitions": "coscc.units.meta",
    "unit_answers": "coscc.units.meta",
    "unit_holds": "coscc.units.meta",
    "unit_links": "coscc.units.meta",
    "unit_meta": "coscc.units.meta",
    "unit_questions": "coscc.units.meta",
    "unit_seen": "coscc.units.meta",
    "unit_unknowns": "coscc.units.meta",
    "workspaces": "coscc.service.store",
}

FOREIGN_SQL: set[tuple[str, str]] = {
    ("coscc.github.prmachine", "review_rounds"),
    ("coscc.github.prmachine", "transitions"),
    ("coscc.run", "unit_meta"),
    ("coscc.run", "workspaces"),
    ("coscc.service.steps", "transitions"),
    ("coscc.units.history", "transitions"),
    ("coscc.units.prose_import", "review_rounds"),
    ("coscc.units.turnstats", "runs"),
    ("coscc.units.turnstats", "step_events"),
    ("coscc.units.turnstats", "step_runs"),
    ("coscc.units.turnstats", "transitions"),
}

DICT_ANY: set[str] = {
    "coscc.agent.agents:address",
    "coscc.agent.agents:agent_for",
    "coscc.agent.agents:identity_section",
    "coscc.agent.agents:label",
    "coscc.agent.agents:of_record",
    "coscc.agent.agents:resolve",
    "coscc.agent.agents:settings_json",
    "coscc.agent.agents:table",
    "coscc.agent.labels:label_for",
    "coscc.agent.models:table",
    "coscc.agent.sessions:Sessions.send",
    "coscc.agent.sessions:Sessions.stream",
    "coscc.agent.sessions:Sessions.suspend_all",
    "coscc.agent.sessions:history",
    "coscc.agent.sessions:list_for_directory",
    "coscc.agent.transcript:ceilings_left",
    "coscc.agent.transcript:cut",
    "coscc.data:Data.decisions",
    "coscc.data:Data.prefs",
    "coscc.data:Data.step_event",
    "coscc.data:Data.step_events_add",
    "coscc.data:Data.step_events_page",
    "coscc.data:Data.step_run",
    "coscc.data:Data.step_runs_open",
    "coscc.data:Data.step_tool_uses",
    "coscc.features.notices:Notices.follow_notices",
    "coscc.features.notices:notice_of",
    "coscc.git.drift:compute",
    "coscc.git.drift:describe",
    "coscc.git.drift:plan_head",
    "coscc.git.fetches:Fetches.fetch",
    "coscc.git.fetches:fetch",
    "coscc.github.integrate:build_prompt",
    "coscc.github.integrate:classify",
    "coscc.github.integrate:describe_for_review",
    "coscc.github.integrate:needs_person_of",
    "coscc.github.integrate:record",
    "coscc.github.integrate:run_gebo",
    "coscc.github.prmachine:Machine.open_of",
    "coscc.github.prmachine:Machine.view",
    "coscc.github.prmachine:Outcome.as_dict",
    "coscc.github.prmachine:ci_held",
    "coscc.github.prmachine:last_round",
    "coscc.github.prmachine:open_prs",
    "coscc.github.prmachine:state",
    "coscc.github.prmachine:watched",
    "coscc.github.prscope:compare",
    "coscc.github.prscope:read",
    "coscc.github.release:classify",
    "coscc.github.release:record",
    "coscc.plugin:body",
    "coscc.plugin:line",
    "coscc.runlog.events:Recorder.subscribe",
    "coscc.runlog.events:collapse",
    "coscc.runlog.events:full_text",
    "coscc.runlog.journal:Journal.append",
    "coscc.runlog.journal:Journal.append_checked",
    "coscc.runlog.journal:Journal.append_with",
    "coscc.runlog.journal:Journal.attempted",
    "coscc.runlog.journal:Journal.failed_attempts",
    "coscc.runlog.journal:Journal.finished",
    "coscc.runlog.journal:Journal.notice_rows",
    "coscc.runlog.journal:Journal.open_starts",
    "coscc.runlog.journal:Journal.records",
    "coscc.runlog.journal:Journal.resumed",
    "coscc.runlog.journal:Journal.set_mode",
    "coscc.runlog.journal:Journal.started",
    "coscc.runlog.journal:Journal.suspended",
    "coscc.runlog.journal:Journal.timeline",
    "coscc.runlog.journal:Journal.timelines",
    "coscc.runlog.journal:Journal.unresumed",
    "coscc.runlog.journal:add_cost",
    "coscc.runlog.journal:last_runs",
    "coscc.runlog.journal:timelines_of",
    "coscc.runlog.journal:totals_of",
    "coscc.runlog.journal:zero_cost",
    "coscc.runlog.spend:_anomalies.row",
    "coscc.runlog.spend:model",
    "coscc.runner.attempt:describe_attempt",
    "coscc.runner.attempt:snapshot",
    "coscc.runner.prompt:_row_blocks.block",
    "coscc.runner.prompt:answers_for",
    "coscc.runner.prompt:compose_prompt",
    "coscc.runner.prompt:with_rows",
    "coscc.runner.review:finding_line",
    "coscc.runner.review:render_round",
    "coscc.runner.step:Runner.run",
    "coscc.service.activity:Activity.activity",
    "coscc.service.activity:Activity.activity_and_usage",
    "coscc.service.activity:Activity.artifact",
    "coscc.service.activity:Activity.cost",
    "coscc.service.activity:Activity.preferences",
    "coscc.service.activity:Activity.set_preference",
    "coscc.service.activity:Activity.settings",
    "coscc.service.activity:Activity.unit_cost",
    "coscc.service.activity:Activity.usage",
    "coscc.service.agents:Agents.agent",
    "coscc.service.agents:Agents.agent_table",
    "coscc.service.agents:Agents.set_agent",
    "coscc.service.answers:Answers.add_decision",
    "coscc.service.answers:Answers.answer",
    "coscc.service.answers:Answers.create_unit",
    "coscc.service.answers:Answers.decisions_table",
    "coscc.service.answers:Answers.hold",
    "coscc.service.answers:Answers.ingest",
    "coscc.service.answers:Answers.more_rounds",
    "coscc.service.answers:Answers.post_new_rounds",
    "coscc.service.answers:Answers.post_review_comment",
    "coscc.service.answers:Answers.record_outcome",
    "coscc.service.answers:Answers.sync_pr",
    "coscc.service.answers:Answers.withdraw_decision",
    "coscc.service.answers:Answers.worktree",
    "coscc.service.autopilot:Autopilot.cap",
    "coscc.service.autopilot:Autopilot.guide_block",
    "coscc.service.autopilot:Autopilot.nudge",
    "coscc.service.autopilot:Autopilot.run_pass",
    "coscc.service.autopilot:Autopilot.set_setting",
    "coscc.service.autopilot:Autopilot.settings",
    "coscc.service.autopilot:Autopilot.show",
    "coscc.service.autopilot:autopilot_values",
    "coscc.service.backlog:Backlog._append_checked.refuse",
    "coscc.service.backlog:Backlog.branch_here",
    "coscc.service.backlog:Backlog.propose_estimates",
    "coscc.service.backlog:Backlog.record_estimate",
    "coscc.service.backlog:Backlog.record_relation",
    "coscc.service.backlog:Backlog.record_shortlist",
    "coscc.service.backlog:Backlog.timeline",
    "coscc.service.backlog:Backlog.unit_history",
    "coscc.service.backlog:Backlog.units_with_history",
    "coscc.service.board:Board.read",
    "coscc.service.board:Board.running",
    "coscc.service.board:Board.running_here",
    "coscc.service.board:answerable",
    "coscc.service.board:waits_for",
    "coscc.service.common:attention_reason",
    "coscc.service.common:describe_base",
    "coscc.service.common:open_prs_once.prs",
    "coscc.service.common:outcome_label",
    "coscc.service.common:shown_state",
    "coscc.service.common:unit_state",
    "coscc.service.ideas:Ideas.create_idea",
    "coscc.service.ideas:Ideas.idea",
    "coscc.service.ideas:Ideas.idea_link",
    "coscc.service.models:Models.findings_added",
    "coscc.service.models:Models.set_stage_effort",
    "coscc.service.models:Models.set_stage_model",
    "coscc.service.models:Models.stage_config",
    "coscc.service.models:Models.stage_models",
    "coscc.service.release:Release._release_press.write",
    "coscc.service.release:Release.attach_release",
    "coscc.service.resume:Resume.resume_after_update",
    "coscc.service.resume:Resume.resume_chat",
    "coscc.service.resume:Resume.resume_integration",
    "coscc.service.resume:Resume.resume_integration.write",
    "coscc.service.resume:Resume.resume_step",
    "coscc.service.resume:Resume.resume_step.end_fields",
    "coscc.service.resume:check",
    "coscc.service.resume:moved_on",
    "coscc.service.resume:resume_kwargs",
    "coscc.service.resume:resume_message",
    "coscc.service.sessions:Chat.history",
    "coscc.service.sessions:Chat.sessions_for",
    "coscc.service.sessions:Chat.stream",
    "coscc.service.steps:Steps._end_fields.answers_kept",
    "coscc.service.steps:Steps._end_fields.findings_added",
    "coscc.service.steps:Steps.attach_integration",
    "coscc.service.steps:Steps.cleanup",
    "coscc.service.steps:Steps.drive",
    "coscc.service.steps:Steps.integrate.write",
    "coscc.service.steps:Steps.integrate_gebo",
    "coscc.service.steps:Steps.mechanical",
    "coscc.service.steps:Steps.next_step",
    "coscc.service.steps:Steps.reconcile_prs",
    "coscc.service.steps:Steps.rerun_offers",
    "coscc.service.steps:Steps.running_steps",
    "coscc.service.steps:Steps.set_mode",
    "coscc.service.steps:Steps.shipped",
    "coscc.service.steps:Steps.stop_running",
    "coscc.service.steps:Steps.stop_step",
    "coscc.service.steps:integration_since_review",
    "coscc.service.update:update_words",
    "coscc.service.watch:Watch.events_page",
    "coscc.service.workspaces:Workspaces.add",
    "coscc.service.workspaces:Workspaces.all",
    "coscc.service.workspaces:Workspaces.meta_of",
    "coscc.service.workspaces:Workspaces.pull",
    "coscc.service.workspaces:Workspaces.remove",
    "coscc.service.workspaces:Workspaces.set_label",
    "coscc.service.workspaces:Workspaces.snapshot",
    "coscc.service:Service.board",
    "coscc.service:Service.settle_after_suspend",
    "coscc.service:Service.suspend_sessions",
    "coscc.service:Service.update_apply",
    "coscc.service:Service.update_build_local",
    "coscc.service:Service.update_cancel",
    "coscc.service:Service.update_status",
    "coscc.units.autopilot:after_own_integration",
    "coscc.units.autopilot:answer_completes",
    "coscc.units.autopilot:answered_since_start",
    "coscc.units.autopilot:exhausted_of",
    "coscc.units.autopilot:is_step",
    "coscc.units.autopilot:measure",
    "coscc.units.autopilot:measure.blank",
    "coscc.units.autopilot:open_questions",
    "coscc.units.autopilot:open_starts",
    "coscc.units.autopilot:pick",
    "coscc.units.autopilot:reason_for",
    "coscc.units.autopilot:reruns_of",
    "coscc.units.autopilot:reserved",
    "coscc.units.autopilot:since_integration",
    "coscc.units.autopilot:skips_exhausted",
    "coscc.units.autopilot:spent_on",
    "coscc.units.autopilot:spent_today",
    "coscc.units.autopilot:started_by",
    "coscc.units.autopilot:stop_for",
    "coscc.units.autopilot:unopened_of",
    "coscc.units.backfill:run",
    "coscc.units.backfill:scan",
    "coscc.units.backlog:build_prompt",
    "coscc.units.backlog:check_relation",
    "coscc.units.backlog:check_shortlist",
    "coscc.units.backlog:computed_order",
    "coscc.units.backlog:effort_from",
    "coscc.units.backlog:estimates_of",
    "coscc.units.backlog:fold",
    "coscc.units.backlog:in_backlog",
    "coscc.units.backlog:measured",
    "coscc.units.backlog:parse_proposal",
    "coscc.units.backlog:relations_of",
    "coscc.units.backlog:shortlist_of",
    "coscc.units.backlog:stamp",
    "coscc.units.backlog:undetermined",
    "coscc.units.board:gate",
    "coscc.units.board:next_step",
    "coscc.units.board:pr_text",
    "coscc.units.board:read",
    "coscc.units.board:rerun",
    "coscc.units.board:screens",
    "coscc.units.guide:needs_you",
    "coscc.units.guide:running",
    "coscc.units.history:History.add_output",
    "coscc.units.history:History.outputs",
    "coscc.units.history:History.record",
    "coscc.units.history:History.record_in",
    "coscc.units.history:History.record_many",
    "coscc.units.history:History.sessions_of",
    "coscc.units.history:History.transitions",
    "coscc.units.history:settled_edits",
    "coscc.units.hold:record",
    "coscc.units.hold:refusal",
    "coscc.units.ideas:create_idea",
    "coscc.units.ideas:read_units",
    "coscc.units.meta:UnitMeta.import_store",
    "coscc.units.meta:UnitMeta.ingest",
    "coscc.units.meta:UnitMeta.snapshot",
    "coscc.units.meta:UnitMeta.snapshot.artifact",
    "coscc.units.meta:UnitMeta.snapshot.entry",
    "coscc.units.meta:UnitMeta.unknowns",
    "coscc.units.meta:read",
    "coscc.units.more_rounds:refusal",
    "coscc.units.planmap:for_step",
    "coscc.units.planmap:select",
    "coscc.units.prose_import:finding_of",
    "coscc.units.prose_import:round_of",
    "coscc.units.retake:describe_for_review",
    "coscc.units.retake:judge",
    "coscc.units.retake:read_manifest",
    "coscc.units.retake:record",
    "coscc.units.retake:take",
    "coscc.units.submit:Channel.handle",
    "coscc.units.submit:Channel.inputs",
    "coscc.units.submit:Collector.handle",
    "coscc.units.submit:Collector.object",
    "coscc.units.submit:refusal",
    "coscc.units.submit:schema_for",
    "coscc.units.submit:stage_result_schema",
    "coscc.units.turnstats:event_fields",
    "coscc.units.turnstats:file_fields",
    "coscc.units.turnstats:measure",
    "coscc.units.turnstats:outcome",
    "coscc.units.turnstats:pairs",
    "coscc.units.turnstats:quality_fields",
    "coscc.units.turnstats:step_fields",
    "coscc.units.worktrees:describe_failure",
    "coscc.units.worktrees:ensure",
    "coscc.units.worktrees:prepare",
    "coscc.units.worktrees:read_prepare",
    "coscc.units.worktrees:refresh_base",
    "coscc.units.worktrees:remove_if_finished",
    "coscc.units:branch_name",
    "coscc.units:create",
    "coscc.update.updater:Updater.apply",
    "coscc.update.updater:Updater.build_local",
    "coscc.update.updater:Updater.cancel",
    "coscc.update.updater:Updater.me",
    "coscc.update.updater:Updater.status",
    "coscc.update.updater:Updater.waited",
    "coscc.update:fetch_into",
    "coscc.update:identity",
    "coscc.update:read_json",
    "coscc.update:verified_wheel",
    "coscc.update:write_json",
}

CREATE = re.compile(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", re.IGNORECASE)
SQL_USE = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+(\w+)", re.IGNORECASE)
# A string is a statement only when a line of it opens with an upper-case DML keyword, so
# prose such as "open one from Workspaces." names no table.
STATEMENT = re.compile(r"^\s*(?:SELECT|INSERT|UPDATE|DELETE|WITH|REPLACE)\b", re.MULTILINE)


def _trees() -> dict[str, ast.AST]:
    """Every module of `coscc/` by its path from the repository root, parsed."""
    return {str(p.relative_to(ROOT.parent)): ast.parse(p.read_text()) for p in _files()}


def _dotted(path: str) -> str:
    return path.removesuffix(".py").removesuffix("/__init__").replace("/", ".")


def private_imports(trees: dict[str, ast.AST]) -> set[tuple[str, str, str]]:
    """`(importing file, source module, name)` of each `from coscc.x import _name` outside x."""
    found = set()
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ImportFrom) and node.module and not node.level):
                continue
            if not node.module.startswith("coscc") or node.module == _dotted(path):
                continue
            for a in node.names:
                if a.name.startswith("_") and not a.name.startswith("__"):
                    found.add((path, node.module, a.name))
    return found


def private_import_problems(
    trees: dict[str, ast.AST], frozen: set[tuple[str, str, str]]
) -> list[str]:
    now = private_imports(trees)
    out = []
    for path, module, name in sorted(now - frozen):
        src = module.replace(".", "/") + ".py"
        out.append(
            f"{path} imports `{name}` from {module}: a leading underscore means only {src} uses it. "
            f"Drop the underscore in {src}, or keep the name in the one module that uses it."
        )
    for path, module, name in sorted(frozen - now):
        out.append(
            f"PRIVATE_IMPORTS lists `{name}` imported by {path} from {module}, which is gone. "
            f"Delete that entry."
        )
    return out


def _sql_strings(tree: ast.AST):
    """Each string constant in the tree, an f-string as the join of its literal parts."""
    fstring_parts = {
        id(v)
        for n in ast.walk(tree)
        if isinstance(n, ast.JoinedStr)
        for v in n.values
        if isinstance(v, ast.Constant)
    }
    for n in ast.walk(tree):
        if isinstance(n, ast.JoinedStr):
            yield "".join(v.value for v in n.values if isinstance(v, ast.Constant))
        elif (
            isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in fstring_parts
        ):
            yield n.value


def feature_owners(trees: dict[str, ast.AST]) -> dict[str, str]:
    """Each table a module under `coscc/features/` creates, owned by that module."""
    return {
        name: _dotted(path)
        for path, tree in trees.items()
        if path.startswith("coscc/features/")
        for s in _sql_strings(tree)
        for name in CREATE.findall(s)
    }


def table_names(trees: dict[str, ast.AST]) -> set[str]:
    """Tables created in `coscc/data.py` and in the modules under `coscc/features/`."""
    found = set(feature_owners(trees))
    for s in _sql_strings(trees["coscc/data.py"]):
        found.update(CREATE.findall(s))
    return found


def table_uses(trees: dict[str, ast.AST], tables: set[str]) -> Counter[tuple[str, str]]:
    """Statements per `(module, table)` that read or write the table; DDL does not count."""
    uses: Counter[tuple[str, str]] = Counter()
    for path, tree in trees.items():
        for s in _sql_strings(tree):
            if not STATEMENT.search(s):
                continue
            for t in SQL_USE.findall(s):
                if t.lower() in tables:
                    uses[(_dotted(path), t.lower())] += 1
    return uses


def table_problems(
    trees: dict[str, ast.AST],
    tables: set[str],
    owners: dict[str, str],
    frozen: set[tuple[str, str]],
) -> list[str]:
    out = []
    for t in sorted(tables - set(owners)):
        out.append(
            f"Table `{t}` has no owner. Add it to OWNERS with the one module that runs its SQL."
        )
    for t in sorted(set(owners) - tables):
        out.append(f"OWNERS lists `{t}`, which is no table. Delete that entry.")
    now = {(m, t) for (m, t) in table_uses(trees, tables) if owners.get(t) not in (None, m)}
    for m, t in sorted(now - frozen):
        owner = owners[t]
        out.append(
            f"{m.replace('.', '/')}.py runs SQL on `{t}`, which {owner.replace('.', '/')}.py owns. "
            f"Add a function to {owner.replace('.', '/')}.py that does it, and call that."
        )
    for m, t in sorted(frozen - now):
        out.append(f"FOREIGN_SQL lists {m} on `{t}`, which is gone. Delete that entry.")
    return out


def dict_any_keys(trees: dict[str, ast.AST]) -> set[str]:
    """`module:qualname` of each public function or method with `dict[str, Any]` in a parameter
    or the return type; the qualname includes the enclosing classes and functions."""
    found: set[str] = set()

    def visit(node: ast.AST, module: str, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, module, (*scope, child.name))
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                if not child.name.startswith("_") and _takes_dict_any(child):
                    found.add(f"{module}:{'.'.join((*scope, child.name))}")
                visit(child, module, (*scope, child.name))
            else:
                visit(child, module, scope)

    for path, tree in trees.items():
        visit(tree, _dotted(path), ())
    return found


def _takes_dict_any(f: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    args = f.args
    notes = [a.annotation for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]]
    notes += [args.vararg and args.vararg.annotation, args.kwarg and args.kwarg.annotation]
    notes.append(f.returns)
    return any(a is not None and "dict[str, Any]" in ast.unparse(a) for a in notes)


def dict_any_problems(found: set[str], listed: set[str]) -> list[str]:
    out = [
        f"{key} takes or returns dict[str, Any]: type it: a dataclass, a TypedDict or a Literal."
        for key in sorted(found - listed)
    ]
    out += [
        f"DICT_ANY lists {key}, which is gone: delete that entry." for key in sorted(listed - found)
    ]
    return out


def _parse(**sources: str) -> dict[str, ast.AST]:
    return {f"coscc/{name}.py": ast.parse(src) for name, src in sources.items()}


class NoPrivateNameCrossesAModule(unittest.TestCase):
    """A name with one leading underscore is imported only by its own module."""

    def test_no_new_private_import_and_no_stale_entry(self):
        self.assertEqual(private_import_problems(_trees(), PRIVATE_IMPORTS), [])

    def test_a_planted_private_import_says_the_fix(self):
        trees = _parse(a="from coscc.b import _x, __y__, z\n", b="from coscc.b import _own\n")
        self.assertEqual(
            private_import_problems(trees, set()),
            [
                "coscc/a.py imports `_x` from coscc.b: a leading underscore means only coscc/b.py "
                "uses it. Drop the underscore in coscc/b.py, or keep the name in the one module "
                "that uses it."
            ],
        )

    def test_a_type_checking_import_counts(self):
        trees = _parse(a="if TYPE_CHECKING:\n    from coscc.b import _x\n")
        self.assertEqual(private_imports(trees), {("coscc/a.py", "coscc.b", "_x")})

    def test_a_listed_import_that_is_gone_says_to_delete_it(self):
        (msg,) = private_import_problems(_parse(a="pass\n"), {("coscc/a.py", "coscc.b", "_x")})
        self.assertIn("Delete that entry.", msg)


class EveryTableHasOneOwner(unittest.TestCase):
    """Only a table's owner module runs SQL (FROM, JOIN, INTO, UPDATE) on it."""

    def test_every_table_has_an_owner_and_no_new_foreign_sql(self):
        trees = _trees()
        owners = OWNERS | feature_owners(trees)
        self.assertEqual(table_problems(trees, table_names(trees), owners, FOREIGN_SQL), [])

    def test_a_planted_foreign_statement_says_the_fix(self):
        trees = _parse(
            a='q = "SELECT * FROM t WHERE x = 1"\np = "Add one, or open one from t."\n',
            b='q = f"UPDATE t SET {col} = 1"\nr = "CREATE INDEX i ON t(x)"\n',
        )
        self.assertEqual(
            table_problems(trees, {"t"}, {"t": "coscc.b"}, set()),
            [
                "coscc/a.py runs SQL on `t`, which coscc/b.py owns. Add a function to "
                "coscc/b.py that does it, and call that."
            ],
        )

    def test_a_feature_table_is_owned_by_its_module_and_foreign_sql_names_the_owner(self):
        trees = {
            "coscc/data.py": ast.parse("pass\n"),
            "coscc/features/x.py": ast.parse(
                'PLUGIN = Plugin(tables=("CREATE TABLE IF NOT EXISTS t (a INTEGER)",))\n'
            ),
            "coscc/other.py": ast.parse('q = "SELECT a FROM t"\n'),
        }
        owners = feature_owners(trees)
        self.assertEqual(owners, {"t": "coscc.features.x"})
        self.assertEqual(table_names(trees), {"t"})
        self.assertEqual(
            table_problems(trees, {"t"}, owners, set()),
            [
                "coscc/other.py runs SQL on `t`, which coscc/features/x.py owns. Add a function "
                "to coscc/features/x.py that does it, and call that."
            ],
        )

    def test_a_table_without_an_owner_says_the_fix(self):
        (msg,) = table_problems(_parse(a="pass\n"), {"t"}, {}, set())
        self.assertIn("Add it to OWNERS", msg)

    def test_a_listed_use_that_is_gone_says_to_delete_it(self):
        (msg,) = table_problems(_parse(a="pass\n"), {"t"}, {"t": "coscc.b"}, {("coscc.a", "t")})
        self.assertIn("Delete that entry.", msg)

    def test_ddl_does_not_count_as_a_use(self):
        trees = _parse(a='q = "ALTER TABLE t ADD COLUMN c; DROP TABLE t; CREATE INDEX i ON t(c)"\n')
        self.assertEqual(table_uses(trees, {"t"}), Counter())


class CallsAreTyped(unittest.TestCase):
    """Public functions do not take or return a bare `dict[str, Any]`."""

    def test_no_new_dict_any_and_no_stale_entry(self):
        self.assertEqual(dict_any_problems(dict_any_keys(_trees()), DICT_ANY), [])

    def test_a_planted_public_function_counts_but_a_private_one_does_not(self):
        src = (
            "def a(x: dict[str, Any]): ...\n"
            "async def b() -> list[dict[str, Any]]: ...\n"
            "def _c(x: dict[str, Any]): ...\n"
            "def d(x: dict[str, int]): ...\n"
        )
        self.assertEqual(dict_any_keys(_parse(m=src)), {"coscc.m:a", "coscc.m:b"})

    def test_a_new_method_of_the_same_name_fails_and_a_stale_entry_says_to_delete_it(self):
        src = (
            "class A:\n    def read(self) -> dict[str, Any]: ...\n"
            "class B:\n    def read(self) -> dict[str, Any]: ...\n"
        )
        found = dict_any_keys(_parse(m=src))
        self.assertEqual(found, {"coscc.m:A.read", "coscc.m:B.read"})
        (msg,) = dict_any_problems(found, {"coscc.m:A.read"})
        self.assertEqual(
            msg,
            "coscc.m:B.read takes or returns dict[str, Any]: type it: a dataclass, a TypedDict "
            "or a Literal.",
        )
        (msg,) = dict_any_problems(set(), {"coscc.m:A.read"})
        self.assertIn("delete that entry", msg)

    def test_the_qualname_includes_enclosing_functions(self):
        src = "def f():\n    def g() -> dict[str, Any]: ...\n"
        self.assertEqual(dict_any_keys(_parse(m=src)), {"coscc.m:f.g"})


if __name__ == "__main__":
    unittest.main()
