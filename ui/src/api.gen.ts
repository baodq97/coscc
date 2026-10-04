// Made by `uv run python -m coscc.api > ui/src/api.gen.ts` from the app's routes. Do not edit.

export type AgentPage = {
  "rows": AgentRow[];
  "others": ConfigRow[];
  "problems": string[];
  "cos_model": string | null;
};

export type AgentRow = {
  "key": string;
  "glyph": string;
  "name": string;
  "meaning": string;
  "role": string;
  "identity_source": Record<string, string>;
  "config": ConfigRow;
  "variants": ConfigRow[];
  "skill": string;
  "grant": GrantView;
  "last": RunView | null;
  "runs": RunView[];
  "runs_30d": number;
  "cost_30d": number;
  "chip": string;
};

export type Answer = {
  "artifact": string;
  "n": number;
  "question": string;
  "text": string;
  "by": string;
  "authority": string;
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
  "idea": string;
  "repo": string;
  "rank": number | null;
  "effort": string | null;
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

export type ConfigRow = {
  "key": string;
  "fields": string[];
  "model": string | null;
  "model_source": string;
  "effort": string | null;
  "effort_source": string;
  "ceilings": Ceilings;
  "overridden": Record<string, boolean>;
};

export type Decided = {
  "unit": string;
  "artifact": string;
  "n": number;
  "question": string;
  "text": string;
  "by": string;
  "authority": string;
  "date": string;
};

export type Decision = {
  "id": string;
  "kind": string;
  "text": string;
  "source": string;
  "workspace": string;
  "workspace_name": string;
  "agent": string;
  "covers": string;
  "from_day": string;
  "until_day": string;
  "withdrawn": string;
  "created_at": string;
  "state": string;
};

export type DecisionTable = {
  "rows": Decision[];
  "workspaces": string[];
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

export type GrantView = {
  "tools": string[];
  "commands": string[];
  "mcp": string[];
  "submits": boolean;
  "warning": string;
};

export type HoldView = {
  "state": string;
  "by": string;
  "date": string;
  "reason": string;
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
};

export type Proposal = {
  "id": number;
  "run": number;
  "type": string;
  "slug": string;
  "title": string;
  "problem": string;
  "sources": Source[];
  "state": string;
  "unit": string;
  "by": string;
  "at": string;
  "decided": string;
  "reason": string;
};

export type PullRequest = {
  "number": number;
  "url": string;
};

export type Question = {
  "artifact": string;
  "n": number;
  "text": string;
  "answered": boolean;
  "by": string;
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
  "reviewed": string;
  "unfinished": boolean;
};

export type Run = {
  "id": number;
  "at": string;
  "by": string;
  "outcome": string;
  "cost_usd": number;
  "taken": number;
  "cut": number;
  "rejected": string[];
  "stopped": boolean;
  "detail": string;
};

export type RunView = {
  "workspace": string;
  "unit": string;
  "outcome": string;
  "at": string;
  "turns": number | null;
  "cost_usd": number | null;
};

export type Running = {
  "unit": string;
  "stage": string;
  "agent": string;
  "started": string;
};

export type Secrets = {
  "workspace": string;
  "secrets": Meta[];
  "globals": Meta[];
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
  "schedule"?: number | null;
  "hours"?: number[];
  "summary"?: string;
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
  "mode": string;
  "last_run": LastRun | null;
};

export type StepEvent = {
  "run": string;
  "seq": number;
  "at": number;
  "kind": string;
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
};

export type UpNext = {
  "shortlist": Shortlisted[];
  "shortlist_record": ShortlistSaved | null;
  "order": Suggested[];
  "unestimated": string[];
  "warnings": unknown[];
  "max": number;
  "propose_warning": string;
  "propose_consequence": string;
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
  "error"?: string | null;
  "warning"?: string;
  "log"?: string;
  "line": string;
  "local_line": string;
  "actions": string[];
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
  "/api/decisions": DecisionTable;
  "/api/features/shown": Shown[];
  "/api/settings/autopilot": AutopilotSettings;
  "/api/units": Cards;
  "/api/units/next": NextStep;
  "/api/units/{name}": Detail;
  "/api/units/{name}/runs/{run}": EventsPage;
  "/api/update": UpdateStatus;
  "/api/vault/leaks": Leaks;
  "/api/vault/secrets": Secrets;
  "/api/workspaces": WorkspaceList;
};
