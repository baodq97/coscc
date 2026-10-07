// Made by `uv run python -m coscc.http > ui/src/api.gen.ts` from the app's routes. Do not edit.

export type AgentPage = {
  "rows": AgentRow[];
  "catalog": CatalogTool[];
  "problems": string[];
  "cos_model": string | null;
  "scope": "workspace" | "all";
};

export type AgentRow = {
  "key": string;
  "group": "stage" | "engine" | "helper" | "triggered";
  "row": RowFields;
  "builtin": RowFields;
  "edited": string[];
  "problems": string[];
  "editable": boolean;
  "skills": SkillText[];
  "row_hash": string;
  "config": ConfigRow;
  "novel": ConfigRow | null;
  "last": RunView | null;
  "runs_30d": number;
  "cost_30d": number;
  "chip": string;
  "groups": RunGroup[];
  "on": boolean | null;
};

export type AgentRun = {
  "run": string;
  "unit": string;
  "at": string;
  "usd": number | null;
  "outcome": string;
};

export type AgentSpend = {
  "agent": string;
  "usd": number | null;
  "steps": number;
  "unknown": number;
  "runs": AgentRun[];
};

export type Answer = {
  "artifact": string;
  "n": number;
  "question": string;
  "text": string;
  "by": "person" | "delegated";
  "name": string;
  "via": string;
  "date": string;
};

export type ArmStats = {
  "steps": number;
  "excluded": number;
  "units": number;
  "read_mean": number | null;
  "served_mean": number | null;
  "cost_mean": number | null;
  "changes_requested_mean": number | null;
};

export type Arms = {
  "on": ArmStats;
  "off": ArmStats;
};

export type AutopilotBrief = {
  "on": boolean;
  "may_ship": boolean;
  "max_parallel": number;
  "refused_because": string;
  "cap": Cap | null;
};

export type AutopilotSettings = {
  "cwd": string;
  "autopilot": boolean;
  "autopilot_may_ship": boolean;
  "max_parallel": number;
  "daily_cap_usd": number;
  "refused_because": string;
};

export type Build = {
  "state"?: string;
  "version"?: string;
  "commit"?: string;
  "started"?: string;
  "by"?: string;
  "workspace"?: string;
  "wheel"?: string;
  "sha256"?: string;
  "error"?: string | null;
  "log"?: string;
};

export type Cap = {
  "day": string;
  "limit": number;
  "spent": number;
  "known": number;
  "estimated": number;
  "estimated_count": number;
  "running": number;
};

export type Card = {
  "name": string;
  "number": number;
  "slug": string;
  "type": string;
  "phase": string;
  "next_stage": string;
  "why": string;
  "open": number;
  "state": CardState;
  "hold": HoldView | null;
  "pr": PullRequest | null;
  "cost_usd": number;
  "at": string;
  "updated": string;
  "attention_reason": string;
  "process": string;
  "missing": string[];
  "idea": string;
  "rank": number | null;
  "effort": string | null;
  "paused": Paused | null;
};

export type CardState = {
  "state": string;
  "label": string;
  "color": string;
};

export type Cards = {
  "workspace": string;
  "read_at": string;
  "units": Card[];
  "autopilot": AutopilotBrief | null;
  "running": Running[];
};

export type CatalogTool = {
  "name": string;
  "effect": string;
  "tier": string;
  "server": string;
  "feature": string;
  "on": boolean;
};

export type Ceilings = {
  "max_turns": number | null;
  "max_turns_source": string;
  "max_budget_usd": number | null;
  "max_budget_source": string;
};

export type ChatHistory = {
  "session_id": string;
  "messages": ChatMessage[];
};

export type ChatMessage = {
  "role": string;
  "text": string;
  "uuid": string;
};

export type ChatSession = {
  "session_id": string;
  "summary": string;
  "last_modified": number;
  "created_at": number | null;
  "git_branch": string | null;
  "resumable": boolean;
};

export type ChatSessions = {
  "cwd": string;
  "sessions": ChatSession[];
};

export type Check = {
  "name": string;
  "bucket": string;
};

export type Commit = {
  "sha": string;
  "subject": string;
};

export type Condition = {
  "field"?: string;
  "is"?: string;
  "guard"?: string;
};

export type ConfigRow = {
  "key": string;
  "label": string | null;
  "model": string | null;
  "model_source": string;
  "effort": string | null;
  "effort_source": string;
  "ceilings": Ceilings;
};

export type DaySpend = {
  "day": string;
  "usd": number | null;
  "steps": number;
};

export type Decided = {
  "unit": string;
  "artifact": string;
  "n": number;
  "question": string;
  "text": string;
  "name": string;
  "date": string;
};

export type Decision = {
  "kind": "rerun" | "more-rounds" | "outcome";
  "by": string;
  "date": string;
  "text": string;
};

export type Deleted = {
  "deleted": string;
};

export type Dependency = {
  "ref": string;
  "why": string;
  "merged": boolean;
};

export type Detail = {
  "card": Card;
  "stages": StageView[];
  "questions": Question[];
  "answers": Answer[];
  "rounds": Round[];
  "depends_on": Dependency[];
  "runs": UnitRun[];
  "worktree": Worktree | null;
  "hold_moves": string[];
  "outputs": OutputRecord[];
  "decisions": Decision[];
  "outcome": Outcome | null;
};

export type EstimateBrief = {
  "unit": string;
  "value": number | null;
  "effort": string | null;
  "effort_source": string;
  "similar": string[];
  "basis": string;
  "effort_basis": string;
  "by": string;
  "at": string;
};

export type EventsPage = {
  "run": string;
  "unit": string;
  "stage": string;
  "status": string;
  "events": StepEvent[];
  "first_seq": number | null;
  "has_older": boolean;
  "last_at": number | null;
  "events_lost": number;
  "purged_at": string | null;
};

export type HoldView = {
  "state": string;
  "by": string;
  "date": string;
  "reason": string;
};

export type Insights = {
  "days": number;
  "recording": boolean;
  "shipped": Shipped[];
  "targets": Target[];
  "by_day": DaySpend[];
  "by_agent": AgentSpend[];
  "waste": Waste[];
  "outcomes": Outcomes;
};

export type LastRun = {
  "outcome": string;
  "ended": string;
  "turns": number | null;
  "cost_usd": number | null;
};

export type Leaks = {
  "unit": string;
  "names": string[];
};

export type Meta = {
  "name": string;
  "tier": string;
  "description": string;
  "stages": string[];
  "modes": string[];
  "broker": boolean;
  "has_value": boolean;
  "granted": boolean | null;
};

export type NextStep = {
  "cwd": string;
  "unit": string;
  "stage": string | null;
  "action": string;
  "blocked": boolean;
  "waiting": string[];
  "dropped": string[];
  "hold"?: HoldView;
  "rerun"?: string;
  "continue"?: string;
  "reasons": string[];
  "gate"?: string;
};

export type Outcome = {
  "grader": string;
  "name": string;
  "usd": number | null;
  "verdict": Verdict | null;
  "proposals": OutcomeProposal[];
};

export type OutcomeProposal = {
  "id": number;
  "title": string;
  "state": string;
};

export type Outcomes = {
  "due": number;
  "on_time": number;
  "graded": number;
  "met": number;
  "target_graded": number;
  "target_met": number;
  "missed": string[];
};

export type OutputRecord = {
  "agent": string;
  "version": number;
  "at": string;
  "fields": Record<string, unknown>;
};

export type PackShown = {
  "name": string;
  "version": string;
  "description": string;
  "on": boolean;
  "process": string;
  "processes": ProcessShown[];
};

export type Paused = {
  "stage": string;
  "code": string;
  "ceiling": string;
  "usd": number | null;
  "max_usd": number | null;
  "turns": number | null;
  "max_turns": number | null;
};

export type ProcessShown = {
  "start": string;
  "end": string;
  "states": Record<string, State>;
  "ref": string;
  "name": string;
};

export type Proposal = {
  "id": number;
  "agent": string;
  "unit": string;
  "run": string;
  "type": string;
  "slug": string;
  "title": string;
  "problem": string;
  "sources": Source[];
  "state": string;
  "made": string;
  "by": string;
  "at": string;
  "decided": string;
  "reason": string;
};

export type ProposalRow = {
  "id": number;
  "agent": string;
  "unit": string;
  "run": string;
  "type": string;
  "slug": string;
  "title": string;
  "problem": string;
  "sources": Source[];
  "state": string;
  "made": string;
  "by": string;
  "at": string;
  "decided": string;
  "reason": string;
  "agent_name": string;
};

export type ProposalsView = {
  "proposals": ProposalRow[];
  "agents": ProposingAgent[];
};

export type ProposingAgent = {
  "key": string;
  "name": string;
  "on": boolean | null;
};

export type PullRequest = {
  "number": number;
  "url": string;
};

export type Question = {
  "artifact": string;
  "n": number;
  "text": string;
  "recommendation": string;
  "answered": boolean;
  "by": string;
  "name": string;
};

export type ReleaseUnit = {
  "name": string;
  "type": string;
  "pr": number | null;
  "sha": string;
  "subject": string;
};

export type ReleaseView = {
  "state": string;
  "reason": string;
  "last_tag": string;
  "units": ReleaseUnit[];
  "unmatched": Commit[];
  "count": number;
  "proposed": string;
  "version": string;
  "pr": number | null;
  "checks": Check[];
  "head": string;
  "button": string;
  "enabled": boolean;
  "disabled_reason": string;
  "warning": string;
  "consequence": string;
  "release_url": string;
  "workflow": string;
  "workflow_url": string;
  "main_version"?: string;
};

export type Report = {
  "window": Window;
  "arms": Arms;
  "excluded_units": number;
  "verdict": "pass" | "fail";
  "missed": string[];
};

export type Round = {
  "n": number;
  "verdict": string;
  "findings": number;
  "findings_open": number;
  "unfinished": boolean;
  "criteria": RoundCriterion[];
  "items": RoundFinding[];
};

export type RoundCriterion = {
  "criterion": string;
  "source": string;
  "met": "yes" | "no" | "unclear";
  "evidence": string;
};

export type RoundFinding = {
  "id": string;
  "label": string;
  "severity": string;
  "criterion": string;
  "place": string;
  "text": string;
};

export type RowFields = {
  "name"?: string;
  "glyph"?: string;
  "description"?: string;
  "model"?: Record<string, unknown>;
  "variants"?: Record<string, unknown>;
  "skills"?: string[];
  "tools"?: Record<string, string>;
  "helpers"?: string[];
  "input"?: Record<string, unknown>;
  "output"?: Record<string, unknown>;
  "trigger"?: TriggerFields;
  "default"?: string;
  "ceilings"?: Record<string, unknown>;
  "warning"?: string;
  "consequence"?: string;
  "body"?: string;
};

export type RunGroup = {
  "row_hash": string;
  "settings": Setting[];
  "runs": RunView[];
  "cost_usd": number;
  "turns": number;
};

export type RunPart = {
  "run": string;
  "ended": string;
  "cost_usd": number | null;
  "paused": Paused | null;
};

export type RunView = {
  "workspace": string;
  "unit": string;
  "outcome": string;
  "at": string;
  "turns": number | null;
  "cost_usd": number | null;
  "row_hash": string;
};

export type Running = {
  "unit": string;
  "stage": string;
  "agent": string;
  "started": string;
};

export type Saved = {
  "saved": string;
  "short": boolean;
};

export type Schedule = {
  "hours": number;
};

export type Secrets = {
  "workspace": string;
  "age": boolean;
  "name_pattern": string;
  "default_stages": string[];
  "stages": string[];
  "modes": string[];
  "secrets": Meta[];
  "globals": Meta[];
};

export type Setting = {
  "at": string;
  "field": string;
  "old": unknown;
  "new": unknown;
  "by": string;
};

export type Shipped = {
  "unit": string;
  "usd": number | null;
  "rounds": number;
  "at": string;
  "outcome": string;
};

export type ShortlistSaved = {
  "at": string | null;
  "n": number;
  "by": string;
  "reason": string;
};

export type Shortlisted = {
  "rank": number;
  "unit": string;
  "estimate": EstimateBrief | null;
  "agent_differs": EstimateBrief | null;
  "computed": number | null;
  "drift": boolean;
  "warnings": string[];
};

export type Shown = {
  "name": string;
  "state": "off" | "pilot" | "on";
  "pilot": boolean;
  "sentence": string;
  "locked": boolean;
  "summary"?: string;
};

export type SkillText = {
  "name": string;
  "text": string;
  "builtin": string;
  "edited": boolean;
};

export type Source = {
  "id": string;
  "kind": string;
  "unit": string;
  "at": string;
};

export type StageView = {
  "stage": string;
  "file": string;
  "status": string;
  "optional": boolean;
  "last_run": LastRun | null;
};

export type Started = {
  "agent": string;
  "started": boolean;
};

export type State = {
  "agent"?: string;
  "action"?: string;
  "optional"?: boolean;
  "hint"?: string;
  "label"?: string;
  "skip"?: string;
  "rerun"?: string[];
  "next"?: Way[];
  "when"?: Condition | Condition[];
};

export type StepEvent = {
  "run": string;
  "seq": number;
  "at": number;
  "kind": string;
  "agent_id"?: string;
  "role"?: string;
  "text"?: string;
  "thinking"?: string;
  "id"?: string;
  "name"?: string;
  "input"?: unknown;
  "tool_use_id"?: string;
  "is_error"?: boolean;
  "content"?: unknown;
  "tool"?: string;
  "reason"?: string;
  "lacked"?: string;
  "granted"?: string[];
  "n"?: number;
  "model"?: string | null;
  "effort"?: string | null;
  "num_turns"?: number;
  "cost_usd"?: number | null;
  "duration_ms"?: number;
  "terminal_reason"?: string | null;
  "outcome"?: string;
  "detail"?: string;
  "subtype"?: string | null;
  "truncated"?: boolean;
  "length"?: number;
  "truncated_fields"?: string[];
};

export type Suggested = {
  "unit": string;
  "computed": number;
  "estimate": EstimateBrief;
  "agent_differs": EstimateBrief | null;
};

export type Target = {
  "name": string;
  "value": number | null;
  "target": number;
  "over": string[];
};

export type TriggerEvent = {
  "name"?: string;
  "after_hours"?: number;
};

export type TriggerFields = {
  "state"?: string;
  "engine"?: string;
  "event"?: TriggerEvent;
  "schedule"?: Schedule;
  "manual"?: boolean;
  "leif"?: boolean;
};

export type UnitRun = {
  "stage": string;
  "agent": string;
  "model": string;
  "started": string;
  "ended": string;
  "outcome": string;
  "detail": string;
  "artifact": string;
  "cost_usd": number | null;
  "turns": number | null;
  "run": string;
  "envelope": string[];
  "paused": Paused | null;
  "parts": RunPart[];
  "raised_by": string;
};

export type UpNext = {
  "shortlist": Shortlisted[];
  "shortlist_record": ShortlistSaved | null;
  "order": Suggested[];
  "unestimated": string[];
  "warnings": unknown[];
  "max": number;
  "propose_warning": string;
};

export type UpdateError = {
  "message": string;
  "log": string;
  "log_tail"?: string;
};

export type UpdateStatus = {
  "version": string;
  "build_id": string;
  "commit": string;
  "commit_label": string;
  "install": string;
  "shape": string;
  "reason": string;
  "state"?: string;
  "window"?: boolean;
  "pending"?: Record<string, unknown> | null;
  "release"?: Build | null;
  "local"?: Build | null;
  "last"?: Record<string, unknown> | null;
  "checked_at"?: string | null;
  "error"?: UpdateError | null;
  "warning"?: string;
  "log"?: string;
  "line": string;
  "local_line": string;
  "actions": string[];
};

export type Verdict = {
  "agent": string;
  "run": string;
  "at": string;
  "judgement": string;
  "criteria": RoundCriterion[];
};

export type Waste = {
  "kind": string;
  "count": number;
  "usd": number | null;
  "unknown": number;
  "not_recorded": number;
};

export type Way = {
  "to": string;
  "when"?: Condition | Condition[];
};

export type Window = {
  "since": string | null;
  "until": string | null;
};

export type WorkspaceList = {
  "working_dir": string | null;
  "count": number;
  "workspaces": WorkspaceRow[];
  "paths": string[];
};

export type WorkspaceRow = {
  "name": string;
  "path": string;
  "label": string;
  "source": "env" | "store";
  "missing": boolean;
};

export type Worktree = {
  "branch": string;
  "path": string;
};

export type Get = {
  "/api/agents": AgentPage;
  "/api/backlog": UpNext;
  "/api/chat/history": ChatHistory;
  "/api/chat/sessions": ChatSessions;
  "/api/codegraph/report": Report;
  "/api/decided": Decided[];
  "/api/features/shown": Shown[];
  "/api/insights": Insights;
  "/api/packs": PackShown[];
  "/api/proposals": ProposalsView;
  "/api/release": ReleaseView | null;
  "/api/runs/{run}": EventsPage;
  "/api/settings/autopilot": AutopilotSettings;
  "/api/units": Cards;
  "/api/units/next": NextStep;
  "/api/units/{name}": Detail;
  "/api/update": UpdateStatus;
  "/api/vault/leaks": Leaks;
  "/api/vault/secrets": Secrets;
  "/api/workspaces": WorkspaceList;
};
