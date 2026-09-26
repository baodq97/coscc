"""Everything the page shows, and nothing it decides.

`spec.md` R10 is the rule this module answers to: the page and the JSON API are two
entry points to one capability, so no handler here validates anything, builds a path, or
decides what counts as a workspace. Each one calls `Service`, turns `Invalid` into a line
of text, and stops. A conditional about business state in this file is a bug in
`service.py`.

The dataclasses below exist because Reflex needs a declared shape to render a list against,
and `Service` returns dictionaries. They are a *view*: every field is something the screen
draws. Nothing is computed here that the service could have answered: since `0100` a card's
state is `service.unit_state`'s, and the page only lays `Running` over it through
`service.shown_state`.

The service instance is the same object the FastAPI app holds. Two instances would mean two
`Sessions` registries, and knob 4 ("resume only what this app created") would answer
differently depending on which door you came through.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses

import reflex as rx
from reflex_base.event.context import EventContext

from coscc import events as events_mod
from coscc import place, present
from coscc.api import build
from coscc.service import COLLAPSED_STATES, Invalid, describe_base

# `0095`: these moved to modules of their own. Every name is imported back, so
# `coscc.state.<name>` still resolves; a patch reaches only the module that looks it up.
from coscc.state_views import (
    NAVIGATION,
    SCREEN_TITLES,
    STATUS_COLOR,
    _cell_label,
    MARK_COLORS,
    tree_line,
    Workspace,
    Cell,
    Question,
    Round,
    Unit,
    Card,
    _card,
    _ci_line,
    _shown,
    UsageRow,
    SpendRow,
    TokenRow,
    WasteRow,
    AnomalyRow,
    WASTE_LABEL,
    ANOMALY_LABEL,
    NO_UNIT,
    _unknown,
    _spend_rows,
    _token_row,
    _waste_rows,
    _measured,
    _anomaly_rows,
    MESSAGE_CUT,
    BacklogRow,
    _estimated_by,
    _backlog_row,
    _relations_text,
    backlog_view,
    _outcome_fields,
    Activity,
    _activities,
    RUNNING_POLL,
    _POLLING,
    GONE_AFTER,
    _ASKING,
    _asking,
    _tab_gone,
    _hold_fields,
    _hold_detail,
    _integration_fields,
    Message,
    Conversation,
    AutopilotStop,
    RunningStep,
    Run,
    WatchEvent,
    WATCH_WINDOW,
    WATCH_GATHER,
    NO_RUN_NOTE,
    READ_ONLY_NOTE,
    AUTOPILOT_STOP_LABEL,
    _number,
    _watch_note,
    _watch_events,
    Event,
    Knob,
    ModelRow,
    GrantRow,
    _run_target,
    _run_waiting,
    _key_label,
    _tokens,
    _job_line,
    _channel_line,
    COST_NOTE,
    cost_note,
    _usd,
    _title_of,
    _initials,
    _questions,
    _rounds,
    _current_stage,
)
from coscc.state_workspaces import (
    WorkspacesMixin,
)
from coscc.state_watch import (
    WatchMixin,
)
from coscc.state_update import (
    UpdateMixin,
)
from coscc.state_answers import (
    AnswersMixin,
)
from coscc.state_backlog import (
    BacklogMixin,
)
from coscc.state_rerun import (
    RerunMixin,
)

API = build()
SERVICE = API.state.service


class StudioState(
    WorkspacesMixin,
    WatchMixin,
    UpdateMixin,
    AnswersMixin,
    BacklogMixin,
    RerunMixin,
    rx.State,
):
    """The whole page. No business state lives here — it is all read back from `Service`."""

    screen: str = "overview"
    # `0056`. The socket `session_id` of the last full read; another one is a new page
    # (`arrive`). Then the workspace and the unit last read, and the unit `load_next` last
    # answered for. Each is set once its read is done. Backend only.
    _loaded_sid: str = ""
    _read_cwd: str = ""
    _read_unit: str = ""
    _asked: str = ""
    # `0056` F2. Set by `arrive` for the `load_next` it chains, which then waits for an ask
    # already in flight; *Ask again*, a step's end and a hold move ask afresh.
    _ask_joins: bool = False
    loading: bool = False
    busy: bool = False
    error: str = ""
    notice: str = ""

    # -- board
    stages: list[str] = []
    # `0053`. Every unit of the last board read, whole, keyed by id in board order. Backend
    # only: the page gets `cards` and, for the one unit open, `current_unit`. Always
    # assigned a new dict, never changed in place (`spike.md ## U3` measured only that).
    _full: dict[str, Unit] = {}
    # `0053` R7, R8. The one list of cards the page receives; the stage columns, List, the
    # collapsed groups, the palette and Overview all filter it on the page.
    cards: list[Card] = []
    # `0053` R7. The open unit, whole, copied from `_full` by `_set_current` — never by a
    # computed var walking a list the page would then receive too.
    current_unit: Unit = Unit()
    board_note: str = ""
    # Set only when the board is empty (`0001_product-describes-a-state-it-is-not-in` R6):
    # the store the board read, the host repository, and how many units the host's own
    # `.cos/` holds that the board does not list.
    empty_store: str = ""
    empty_host: str = ""
    empty_host_units: int = 0
    recording: bool = False
    # `0051`. `Service.running`'s latest answer, kept so a board read that rebuilds every
    # card can put `live` back on at once instead of waiting for the next ask. Backend only.
    _running_read: dict = {}
    query: str = ""
    focus: str = "All work"
    board_view: str = "Board"
    density: str = "comfortable"

    # -- one unit
    unit_id: str = ""
    detail_tab: str = "overview"
    artifact: str = ""
    artifact_file: str = ""
    artifact_missing: bool = False
    runs: list[Run] = []
    # `0034`. Every step running in this workspace, as the service lists it. One per unit
    # -- the service refuses a second -- and any number of units at once. This page holds
    # no running flag of its own; the list is re-read, never patched.
    running_steps: list[RunningStep] = []
    # `0043` R9. The board's `autopilot` block, copied: whether it is on, its stops, the cap.
    autopilot_on: bool = False
    autopilot_stops: list[AutopilotStop] = []
    autopilot_cap: str = ""
    autopilot_refused: str = ""
    run_log: str = ""
    # The unit whose step this page is streaming into `run_log`, so another unit's
    # reply is never shown under the one now open.
    log_unit: str = ""
    # `0082` R7, R3: which *Details* are open, by key. Closed, their content is not in the DOM.
    open_details: list[str] = []
    # `0024`. The stage `cos.mjs next` names for the open unit, and what it said. Set only
    # by `load_next`, from `_run_target`; `next_stage` reads it and nothing computes it.
    run_stage: str = ""
    run_said: str = ""
    # `0028`. The findings `cos.mjs next` says a person is awaited on; set only by
    # `load_next`, from `_run_waiting`. Non-empty means the button offers nothing and the
    # page points at the Questions tab instead.
    run_waiting: list[str] = []

    # -- sessions
    conversations: list[Conversation] = []
    session_id: str = ""
    # `0053` R10. What the page shows: each message of `_history`, in the same order, cut
    # to `MESSAGE_CUT` characters; `open_message` puts one back whole.
    messages: list[Message] = []
    _history: list[Message] = []
    # `0053` R9. The workspace the conversation list was last read for, `""` when none:
    # Sessions reads it on arrival only when this differs from `cwd`.
    _sessions_cwd: str = ""
    prompt: str = ""
    sending: bool = False

    # -- activity and settings
    events: list[Event] = []
    usage_total_tokens: str = "—"
    usage_total_usd: str = "—"
    usage_cost_note: str = COST_NOTE
    # `0093`. The *Cost* screen, read only on arrival there (R12), and the open unit's part.
    cost_recording: bool = True
    cost_total_usd: str = "—"
    cost_total_steps: str = "0"
    cost_total_unknown: str = ""
    cost_offset: str = ""
    cost_units: list[SpendRow] = []
    cost_stages: list[SpendRow] = []
    cost_days: list[SpendRow] = []
    cost_tokens: list[TokenRow] = []
    cost_waste: list[WasteRow] = []
    cost_anomalies: list[AnomalyRow] = []
    unit_cost_stages: list[SpendRow] = []
    unit_anomalies: list[AnomalyRow] = []
    knobs: list[Knob] = []
    grants: list[GrantRow] = []
    data_dir: str = ""
    host_port: str = ""
    # Whether the bound address reaches this machine only. Since `0011` the default
    # is `0.0.0.0`, so the page may no longer state "loopback" as a fact -- it has to
    # read it. A page that claims a safety property it does not have is worse than a
    # page that says nothing, and `0007` exists because of exactly that.
    loopback_only: bool = True
    # `COS_MODEL`, the fallback for a row nothing else answers. Until `0004_no-setting-
    # says-which-model-runs-a-stage` it was the model of every session.
    model: str = ""
    # The model of each stage, then chat, and why a row fell back. The box being typed in
    # is `model_target`, the same one-box-at-a-time shape the answers use.
    model_rows: list[ModelRow] = []
    model_problems: list[str] = []
    # `0043` R2. One workspace's autopilot settings, as `Service.autopilot_settings` has them.
    ap_on: bool = False
    ap_may_ship: bool = False
    ap_max_parallel: str = ""
    ap_cap: str = ""
    ap_refused: str = ""
    model_target: str = ""
    model_text: str = ""
    saving_model: bool = False

    # -- chrome
    mobile_open: bool = False
    command_open: bool = False
    command_query: str = ""

    # -- computed ------------------------------------------------------------

    @rx.var
    def screen_title(self) -> str:
        return SCREEN_TITLES.get(self.screen, "Overview")

    @rx.var
    def session_title(self) -> str:
        """`0082` R14. The open conversation's title, as its row in the list shows it."""
        return next((c.title for c in self.conversations if c.id == self.session_id), "")

    @rx.var
    def current_workspace(self) -> Workspace:
        for w in self.workspaces:
            if w.id == self.cwd:
                return w
        return Workspace(name="No workspace", initials="--", color="gray")

    @rx.var
    def has_workspace(self) -> bool:
        return self.cwd != ""

    @rx.var
    def filtered_workspaces(self) -> list[Workspace]:
        q = self.workspace_query.strip().lower()
        if not q:
            return self.workspaces
        return [w for w in self.workspaces if q in w.name.lower() or q in w.label.lower()]

    # `0053` R8. The columns, the List, the collapsed groups, the palette and Overview each
    # filter `cards` on the page by one of the lists of ids below: a list of cards per column
    # would send every card again (`spike.md ## U2`). Order is always `cards`' order.

    @rx.var
    def shown_ids(self) -> list[str]:
        """The cards the search and the filter leave, the collapsed groups' included: the
        List shows each with its stage and state."""
        q = self.query.strip().lower()
        rows = list(self.cards)
        if q:
            rows = [c for c in rows if q in c.id.lower() or q in c.title.lower()]
        if self.focus == "Autonomous":
            rows = [c for c in rows if c.mode == "autonomous"]
        elif self.focus == "Needs you":
            # `0100` R10: the state, as the service decided it.
            rows = [c for c in rows if c.state == "needs-you"]
        return [c.id for c in rows]

    @rx.var
    def resume_id(self) -> str:
        """*Pick up where you left off*: the first card in board order — column, then place
        in it — that is `Running` or `Ready` (`0100` R10), `""` if none."""
        board = set(self.board_ids)
        order = {name: i for i, name in enumerate(self.stages)}
        rows = [(order.get(c.at, len(order)), i, c.id) for i, c in enumerate(self.cards)
                if c.id in board and c.state in ("running", "ready")]
        return min(rows)[2] if rows else ""

    @rx.var
    def active_count(self) -> int:
        """`0100` R10. Every unit outside the three collapsed groups."""
        return len([c for c in self.cards if c.state not in COLLAPSED_STATES])

    @rx.var
    def attention_count(self) -> int:
        """`0100` R10. Every unit whose state is *Needs you*."""
        return len([c for c in self.cards if c.state == "needs-you"])

    @rx.var
    def board_ids(self) -> list[str]:
        """`0100` R8. The shown cards the stage columns draw: every one outside the three
        collapsed groups."""
        shown = set(self.shown_ids)
        return [c.id for c in self.cards if c.id in shown and c.state not in COLLAPSED_STATES]

    @rx.var
    def stage_counts(self) -> dict[str, int]:
        """`0100` R1. How many cards each stage's column holds, keyed by the stages the board
        read returned, for its count and its *Nothing here*."""
        counts = {name: 0 for name in self.stages}
        board = set(self.board_ids)
        for c in self.cards:
            if c.id in board:
                counts[c.at] = counts.get(c.at, 0) + 1
        return counts

    @rx.var
    def group_counts(self) -> dict[str, int]:
        """`0100` R8. How many shown cards each collapsed group holds: the search and the
        filter narrow a group as they narrow a column (review F2)."""
        counts = {name: 0 for name in COLLAPSED_STATES}
        shown = set(self.shown_ids)
        for c in self.cards:
            if c.id in shown and c.state in counts:
                counts[c.state] += 1
        return counts

    @rx.var
    def ws_name(self) -> str:
        """`0056` R8. What `ws=` carries: the workspace's name, never its path."""
        return next((w.name for w in self.workspaces if w.id == self.cwd), "")

    @rx.var
    def unit_missing(self) -> bool:
        """`0056` R9. An address named a unit this workspace's board does not list."""
        return (self.unit_id != "" and not self.loading
                and not any(c.id == self.unit_id for c in self.cards))

    @rx.var
    def unit_dropped(self) -> bool:
        """`0056` R11. The open unit is dropped, so the dialog offers nothing that writes."""
        return self.current_unit.hold_state == "dropped"

    @rx.var
    def board_href(self) -> str:
        ws = next((w.name for w in self.workspaces if w.id == self.cwd), "")
        return place.href(place.Place("board", ws))

    @rx.var
    def open_questions_here(self) -> list[Question]:
        """`0016`. The open unit's unanswered questions, counted artifact's first. `0044`:
        then the ones Jera answered, so a person can read them and answer over them."""
        shown = [q for q in self.current_unit.questions if not q.answered or q.by_jera]
        return sorted(shown, key=lambda q: (q.answered, not q.counted))

    @rx.var
    def jera_can_ask(self) -> bool:
        """`0044` R2, as the page can see it: an unanswered question outside `review.md`.
        `Service.precedent` still decides; this only hides a button it would refuse (S8)."""
        return self.current_unit.answerable and any(
            not q.answered and q.artifact != "review.md" for q in self.current_unit.questions
        )

    @rx.var
    def next_stage(self) -> str:
        """The stage the run button would run: the one `cos.mjs next` named (`0024`)."""
        return self.run_stage

    @rx.var
    def next_cell(self) -> Cell:
        for cell in self.current_unit.cells:
            if cell.stage == self.next_stage:
                return cell
        return Cell()

    @rx.var
    def running_here(self) -> bool:
        return self.unit_id != "" and any(r.unit == self.unit_id for r in self.running_steps)

    @rx.var
    def log_here(self) -> bool:
        return self.run_log != "" and self.log_unit == self.unit_id

    @rx.var
    def command_ids(self) -> list[str]:
        q = self.command_query.strip().lower()
        if not q:
            return [c.id for c in self.cards[:5]]
        return [c.id for c in self.cards if q in c.title.lower() or q in c.id.lower()][:6]

    @rx.var
    def usage_rows(self) -> list[UsageRow]:
        """`0053` R11. Only while *Activity & usage* is shown; nothing elsewhere.

        `0092` R8 c: a unit whose steps all died before a token was counted still has a
        row, reading `unknown`, rather than leaving the table as if it cost nothing."""
        if self.screen != "activity":
            return []
        return [UsageRow(id=c.id, title=c.title, tokens=c.tokens, usd=c.usd,
                         token_count=c.token_count) for c in self.cards
                if c.token_count > 0 or c.usd not in ("", "—")]

    @rx.var
    def usage_scale(self) -> int:
        """The bar scale, taken from the largest real value rather than from a guess."""
        return max([c.token_count for c in self.cards] + [1])

    # -- plumbing ------------------------------------------------------------

    def _fail(self, e: Exception) -> None:
        """`Invalid` carries a reason a caller shows verbatim. Nothing rewrites it."""
        self.error = str(e)

    # -- loading -------------------------------------------------------------

    def _load_settings(self) -> None:
        data = SERVICE.settings()
        self.working_dir = data.get("working_dir") or ""
        self.data_dir = data.get("data_dir") or ""
        self.host_port = f"{data.get('host')}:{data.get('port')}"
        self.loopback_only = data.get("host") in ("127.0.0.1", "localhost", "::1")
        self.model = data.get("cos_model") or "unset"
        self.knobs = [
            Knob(name=k["name"], value=k["value"], detail=k["detail"], on=bool(k["on"]))
            for k in data.get("knobs") or []
        ]
        self.grants = [
            GrantRow(
                stage=g["stage"],
                tools=g["tools"],
                commands=g["commands"],
                turns=str(g["max_turns"]),
                budget=f"${g['max_budget_usd']:.2f}",
                warning=g["warning"],
                consequence=str(g.get("consequence") or ""),
                tool_list=list(g.get("tool_list") or []),
                command_list=list(g.get("command_list") or []),
            )
            for g in data.get("grants") or []
        ]

    def _show_models(self, data: dict) -> None:
        self.model_rows = [
            ModelRow(
                name=str(r["name"]),
                agents=int(r["agents"]),
                model=str(r["model"] or "SDK default"),
                source=str(r["source"]),
                # A `:novel` row can show `override` inherited from its base row; only
                # the service knows whether this row itself has one to reset.
                overridden=bool(r.get("overridden", r["source"] == "override")),
                effort=str(r.get("effort") or "SDK default"),
                effort_source=str(r.get("effort_source") or "none"),
                effort_overridden=bool(r.get("effort_overridden", r.get("effort_source") == "override")),
                has_effort=str(r["name"]) != "chat",
            )
            for r in data.get("rows") or []
        ]
        self.model_problems = [str(p) for p in data.get("problems") or []]

    async def _load_models(self) -> None:
        self._show_models(await SERVICE.stage_models())
        self._load_autopilot()

    def _show_autopilot_block(self, block: dict) -> None:
        """`0043` R9. Copied from the board; nothing here decides whether to stop."""
        self.autopilot_on = bool(block.get("on"))
        self.autopilot_refused = str(block.get("refused_because") or "")
        self.autopilot_stops = [
            AutopilotStop(unit=str(x.get("unit") or "the workspace"), kind=AUTOPILOT_STOP_LABEL.get(
                str(x.get("kind") or ""), str(x.get("kind") or "")), reason=str(x.get("reason") or ""))
            for x in block.get("stops") or []
        ]
        cap = block.get("cap") or {}
        self.autopilot_cap = (
            "" if not cap else
            f"Today: {cap.get('spent', 0):.2f} spent, "
            + (f"{cap.get('estimated', 0):.2f} of it estimated, " if cap.get("estimated_count") else "")
            + f"{cap.get('running', 0):.2f} running, cap {cap.get('limit', 0):.2f} USD"
        )

    def _show_autopilot(self, data: dict) -> None:
        self.ap_on = bool(data.get("autopilot"))
        self.ap_may_ship = bool(data.get("autopilot_may_ship"))
        self.ap_max_parallel = str(data.get("max_parallel") or "")
        cap = data.get("daily_cap_usd")
        self.ap_cap = f"{cap:g}" if isinstance(cap, (int, float)) else ""
        self.ap_refused = str(data.get("refused_because") or "")

    def _load_autopilot(self) -> None:
        if not self.cwd:
            return
        try:
            self._show_autopilot(SERVICE.autopilot_settings(self.cwd))
        except Invalid:
            return

    def _load_workspaces(self) -> None:
        data = SERVICE.workspaces()
        self.working_dir = data.get("working_dir") or ""
        rows = data.get("workspaces") or []
        self.workspaces = [
            Workspace(
                id=row["path"],
                name=row["name"],
                path=row["path"],
                label=row["label"],
                source=row["source"],
                missing=bool(row["missing"]),
                # An env workspace cannot be renamed or removed from here, and the page
                # has to be able to tell rather than offering a button that refuses.
                removable=row["source"] == "store",
                initials=_initials(row["name"]),
                color=MARK_COLORS[i % len(MARK_COLORS)],
            )
            for i, row in enumerate(rows)
        ]
        if self.cwd and not any(w.id == self.cwd for w in self.workspaces):
            self.cwd = ""
        if not self.cwd and self.workspaces:
            self.cwd = self.workspaces[0].id

    def _load_running(self) -> None:
        """`0034`. In memory and synchronous: no `gh`, no git, so a step's first chunk
        can ask it without waiting on anything a full board read waits on."""
        self.running_steps = []
        if not self.cwd:
            return
        try:
            self.running_steps = [RunningStep(**{**r, "started_at": present.when(r.get("started_at")),
                                                 "run": r.get("run") or ""})
                                  for r in SERVICE.running_steps(self.cwd)]
        except Invalid:
            self.running_steps = []

    def _set_current(self) -> None:
        """`0053`. `current_unit` from `_full` for `unit_id`: its own copy, lists and the
        rows in them included, so nothing shares an object with `_full` (review round 1,
        F1). Called wherever either of the two changes."""
        found = self.get_value("_full").get(self.unit_id)
        self.current_unit = copy.deepcopy(found) if found is not None else Unit()

    async def _load_board(self) -> None:
        self._full, self.cards, self.stages, self.board_note = {}, [], [], ""
        self._set_current()
        self.empty_store, self.empty_host, self.empty_host_units = "", "", 0
        self.branch = ""
        self._load_running()
        if not self.cwd:
            return
        try:
            self.branch = (await SERVICE.branch_here(self.cwd))["branch"]
        except Invalid:
            # A workspace that is not a git checkout still has a board. Saying nothing is
            # right here: there is no branch to show, and that is not an error to report.
            self.branch = ""
        try:
            data = await SERVICE.board(self.cwd)
        except Invalid as e:
            self.board_note = str(e)
            return

        self.stages = list(data["stages"])
        for name, value in backlog_view(data).items():
            setattr(self, name, value)
        self._backlog_history = dict((data.get("backlog") or {}).get("history") or {})
        self._trees = {
            u["name"]: tree_line(u.get("worktree")) for u in data.get("units") or []
        }
        self.recording = bool(data["recording"])
        self._show_autopilot_block(data.get("autopilot") or {})
        read_only = READ_ONLY_NOTE if data.get("read_only_because") else ""
        self.board_note = read_only or data.get("empty_because") or ""
        empty = data.get("empty") or {}
        self.empty_store = str(empty.get("store") or "")
        self.empty_host = str(empty.get("host") or "")
        self.empty_host_units = int(empty.get("host_units") or 0)
        if self.empty_host_units > 0:
            # `empty_because` speaks of the store's `.cos/`, and next to a host `.cos/`
            # that is full it reads as a claim about the wrong directory — the fault this
            # unit exists for. Only the read-only reason survives, appended by the page.
            self.board_note = read_only

        units: list[Unit] = []
        for u in data["units"]:
            cells = []
            for row in u["stages"]:
                label, color = _cell_label(row)
                cells.append(Cell(
                    stage=row["stage"],
                    status=row["status"],
                    label=label,
                    mode=row["mode"],
                    color=color,
                    started=row["status"] != "not started",
                    optional=bool(row.get("optional")),
                    grants=", ".join(row.get("grants") or []) or "no tools",
                    warning=row.get("warning") or "",
                    opens_tools=bool(row.get("grants")),
                    consequence=str(row.get("consequence") or ""),
                ))
            started = len([c for c in cells if c.started])
            stage = _current_stage(u, self.stages)
            count, shown = _tokens(u.get("cost") or {})
            # The file-only stage `cos.mjs` names, not the one the run button asks for: that
            # one can cost two `gh` calls, and this runs for every card (`0024` plan, Risk 7).
            nxt = next((c for c in cells if c.stage == (u.get("next_stage") or "")), None)
            mode = nxt.mode if nxt is not None else "manual"
            waiting, asked = _questions(u)
            decided = u.get("state") or {}
            units.append(
                Unit(
                    id=u["name"],
                    title=_title_of(u["name"]),
                    summary=u.get("next") or "",
                    stage=stage,
                    owner="AI" if mode == "autonomous" else "You",
                    mode=mode,
                    tokens=shown,
                    usd=_usd(u.get("cost") or {}),
                    token_count=count,
                    progress=int(started * 100 / len(cells)) if cells else 0,
                    problems="; ".join(u.get("problems") or []),
                    cells=cells,
                    open_questions=waiting,
                    questions=asked,
                    pr_url=str((u.get("pr") or {}).get("url") or ""),
                    rounds=_rounds(u),
                    **_integration_fields(u.get("integration")),
                    **_outcome_fields(u.get("outcome_label")),
                    live=_activities(u["name"], self._running_read),
                    **_hold_fields(u),
                    shortlist_rank=int((u.get("backlog") or {}).get("rank") or 0),
                    relations_text=_relations_text((u.get("backlog") or {}).get("relations")),
                    answerable=bool(u.get("answerable", True)),
                    attention_reason=str(u.get("attention_reason") or ""),
                    at=str(u.get("at") or ""),
                    ci_line=_ci_line(decided.get("ci")),
                    decided_state=str(decided.get("state") or ""),
                    decided_label=str(decided.get("label") or ""),
                    decided_color=str(decided.get("color") or "gray"),
                )
            )
        units = [dataclasses.replace(u, **_shown(u, self._running_read)) for u in units]
        self._full = {u.id: u for u in units}
        self.cards = [_card(u) for u in units]
        self._set_current()

    def _apply_running(self, read: dict) -> None:
        """`0051` R3. Put one `Service.running` answer on every card. Decides nothing.

        `0053` C4: the cards are sent again only when a card's `live` changed, and the open
        unit only when its own did — an ask that changes nothing sends neither. `0100`: a
        state that `Running` starts or stops covering is a change the same way."""
        self._running_read = read
        full, moved = {}, set()
        for key, unit in self.get_value("_full").items():
            live, shown = _activities(key, read), _shown(unit, read)
            if live != unit.live or shown["state"] != unit.state:
                unit = dataclasses.replace(unit, live=live, **shown)
                moved.add(key)
            full[key] = unit
        if not moved:
            return
        self._full = full
        self.cards = [_card(u) for u in full.values()]
        if self.unit_id in moved:
            self._set_current()

    def _load_sessions(self) -> None:
        self.conversations, self.messages, self._history = [], [], []
        if not self.cwd:
            self.session_id = ""
            return
        try:
            data = SERVICE.sessions_for(self.cwd, limit=40)
        except Invalid as e:
            self._fail(e)
            return
        self._sessions_cwd = self.cwd
        rows = [
            Conversation(
                id=row["session_id"],
                title=(row.get("summary") or row.get("session_id") or "")[:60] or "Untitled",
                subtitle=present.when(row.get("last_modified") or row.get("created_at")),
                resumable=bool(row.get("resumable")),
            )
            for row in data["sessions"]
        ]
        self.conversations = rows
        # A session this app created but the SDK has not listed would be invisible here.
        # Measured on 2026-09-22 against a real send: it does not happen — `sessions_for`
        # returned the new session on the first call after the reply finished. An earlier
        # version of this method inserted a placeholder row for that case; it was removed
        # once the measurement showed the case does not arise, because a branch nothing
        # reaches is a branch nobody maintains.
        if self.session_id and not any(c.id == self.session_id for c in rows):
            self.session_id = ""
        if not self.session_id and self.conversations:
            self.session_id = self.conversations[0].id
        self._load_history()

    def _load_history(self) -> None:
        if not (self.cwd and self.session_id):
            self.messages, self._history = [], []
            return
        try:
            data = SERVICE.history(self.cwd, self.session_id)
        except Invalid as e:
            self._fail(e)
            return
        rows = [
            Message(role=m.get("role") or "assistant", text=m.get("text") or "")
            for m in data["messages"]
            if (m.get("text") or "").strip()
        ]
        # A read that comes back empty while something is on screen is not a reason to
        # clear the screen: the exchange the user just had would be the thing thrown away,
        # and this app keeps no other copy of it.
        if not rows and self.messages:
            return
        self._history = rows
        self.messages = [
            Message(role=m.role, text=m.text[:MESSAGE_CUT], cut=max(len(m.text) - MESSAGE_CUT, 0))
            for m in rows
        ]

    def _keep_whole(self, shown: list[Message]) -> None:
        """`0053` review round 1, F3. After `send` reads the conversation again, a message
        the page showed whole just before — the reply that streamed, one opened with
        `open_message` — stays whole instead of shrinking under the reader. Matched on the
        text, not the index: the read may not line up with what streamed."""
        whole = {m.text for m in shown if not m.cut and len(m.text) > MESSAGE_CUT}
        full = self.get_value("_history")
        messages = list(self.get_value("messages"))
        kept = False
        for i, m in enumerate(messages):
            if m.cut and i < len(full) and full[i].text in whole:
                messages[i] = Message(role=full[i].role, text=full[i].text, cut=0)
                kept = True
        if kept:
            self.messages = messages

    def _load_activity(self) -> None:
        self.events = []
        self.usage_total_tokens, self.usage_total_usd = "—", "—"
        self.usage_cost_note = COST_NOTE
        if not self.cwd:
            return
        try:
            # One read for both halves of this screen; `activity` and `usage` on their own
            # would each scan and parse the identical rows.
            feed = SERVICE.activity_and_usage(self.cwd, limit=40)
        except Invalid as e:
            self._fail(e)
            return
        icons = {
            "mode": ("sliders-horizontal", "blue"),
            "start": ("zap", "iris"),
            "end": ("circle-check", "grass"),
            # `0019` plan step 7. What a stopped step left behind, captured just before
            # `end` — its own row, distinct from the `end` row that follows it.
            "attempt": ("camera", "amber"),
            # `0045` R10. A person paused, dropped or resumed a unit.
            "hold": ("pause", "amber"),
        }
        events: list[Event] = []
        for row in feed["events"]:
            icon, color = icons.get(row["kind"], ("dot", "gray"))
            if row["kind"] == "end" and row["outcome"] != "done":
                icon, color = "triangle-alert", "amber"
            title = {
                "mode": f"{row['stage']} set to {row['mode']}",
                "start": f"{row['stage']} started ({row['mode']})",
                "end": f"{row['stage']} {row['outcome']}",
                "attempt": f"{row['stage']} stopped — what it left was recorded",
                "hold": f"{row.get('from', '')} → {row.get('to', '')}",
            }.get(row["kind"], row["kind"])
            detail = f"{row['unit']}"
            if row["kind"] == "hold":
                detail += _hold_detail(row)
            if row["denials"]:
                detail += f" / {row['denials']} tool call(s) refused"
            if row["artifact"]:
                detail += f" / wrote {row['artifact']}"
            events.append(Event(title=title, detail=detail, icon=icon, color=color, time=present.when(row["at"])))
        self.events = events
        total = feed.get("total") or {}
        _, shown = _tokens(total)
        self.usage_total_tokens = shown
        self.usage_total_usd = _usd(total)
        self.usage_cost_note = cost_note(total)

    def _load_cost(self) -> None:
        """`0093`. The *Cost* screen, from one read of the run log (R12). The review rounds
        come from the board already read (R9); nothing here adds or decides a figure."""
        self.cost_units, self.cost_stages, self.cost_days = [], [], []
        self.cost_tokens, self.cost_waste, self.cost_anomalies = [], [], []
        self.cost_total_usd, self.cost_total_steps, self.cost_total_unknown = "—", "0", ""
        self.cost_offset, self.cost_recording = "", True
        if not self.cwd:
            return
        rounds = {key: [r.verdict for r in u.rounds] for key, u in self.get_value("_full").items()}
        try:
            data = SERVICE.cost(self.cwd, rounds)
        except Invalid as e:
            self._fail(e)
            return
        if not data.get("recording"):
            self.cost_recording = False
            return
        total = data["total"]
        self.cost_total_usd = present.money(total.get("usd"))
        self.cost_total_steps = f"{int(total.get('steps') or 0):,}"
        self.cost_total_unknown = _unknown(total.get("unknown"))
        self.cost_offset = data["offset"]
        self.cost_units = _spend_rows(data["by_unit"], unit=True)
        self.cost_stages = _spend_rows(data["by_stage"])
        self.cost_days = _spend_rows(data["by_day"])
        tokens = data["tokens"]
        self.cost_tokens = [_token_row("Workspace", tokens["workspace"])] + [
            _token_row(t["stage"] or "—", t) for t in tokens["by_stage"]
        ]
        self.cost_waste = _waste_rows(data["waste"])
        self.cost_anomalies = _anomaly_rows(data["anomalies"])

    def _load_unit_cost(self) -> None:
        """`0093` R11. The open unit's cost by stage and its anomalies."""
        self.unit_cost_stages, self.unit_anomalies = [], []
        if not (self.cwd and self.unit_id):
            return
        try:
            data = SERVICE.unit_cost(self.cwd, self.unit_id)
        except Invalid as e:
            self._fail(e)
            return
        self.unit_cost_stages = _spend_rows(data["by_stage"])
        self.unit_anomalies = _anomaly_rows(data["anomalies"])

    def _load_timeline(self) -> None:
        self.runs = []
        if not (self.cwd and self.unit_id):
            return
        try:
            data = SERVICE.timeline(self.cwd, self.unit_id)
        except Invalid as e:
            self._fail(e)
            return
        self.runs = [
            Run(
                stage=r.get("stage") or "",
                mode=r.get("mode") or "",
                # `0089` R12: a reader's time; `ended` is empty while the run is not over.
                started=present.when(r.get("started")),
                ended=present.when(r.get("ended")),
                outcome=r.get("outcome") or "—",
                session_id=(r.get("session_id") or "—")[:12],
                tokens=_tokens(r.get("cost") or {})[1],
                # `0092` R8 b: an ended run that reported no cost reads `unknown`, not `—`.
                usd=_usd({**(r.get("cost") or {}), "unknown": int(
                    r.get("ended") is not None and not r.get("reported", True)
                )}),
                color="grass" if r.get("outcome") == "done" else "amber",
                detail=r.get("detail") or "",
                run=r.get("run") or "",
                key=r.get("run") or f"{r.get('stage') or ''}-{i}",
            )
            for i, r in enumerate(data["runs"])
        ]

    def _load_artifact(self) -> None:
        self.artifact, self.artifact_file, self.artifact_missing = "", "", False
        if not (self.cwd and self.unit_id):
            return
        stage = self.current_unit.stage or (self.stages[0] if self.stages else "")
        if not stage:
            return
        try:
            data = SERVICE.artifact(self.cwd, self.unit_id, stage)
        except Invalid as e:
            self._fail(e)
            return
        self.artifact_file = data["file"]
        self.artifact_missing = not data["exists"]
        self.artifact = data["text"] or f"`{data['file']}` has not been written yet."

    # -- events --------------------------------------------------------------

    def _load_base(self) -> None:
        """The first half of what a page's first arrival reads: enough to pick a workspace."""
        try:
            self._load_settings()
            prefs = SERVICE.preferences()
            self.density = str(prefs.get("density") or "comfortable")
            self.board_view = str(prefs.get("board_view") or "Board")
            self.decision_preferences = str(prefs.get("decision_preferences") or "")
            self._load_workspaces()
        except Invalid as e:
            self._fail(e)

    async def _load_rest(self) -> None:
        await self._load_models()
        await self._load_board()
        # `0053` R9: no Sessions here. `arrive` reads them when Sessions is where it lands.
        self._load_activity()
        self._load_update()

    def _load_unit(self, forget: bool = True) -> None:
        """What opening a unit reads. Asked even of a unit the board does not list: the
        board may predate it, and whether it exists is the service's to say (R9)."""
        self.run_log = ""
        if forget:
            # `0071` R9: a message shown in the dialog is one made after it opened. Not on
            # a page's first arrival, whose messages are about the load itself.
            self.error, self.notice = "", ""
        self._load_timeline()
        self._load_unit_cost()
        self._load_artifact()

    # -- where the page is (`0056`) ------------------------------------------
    #
    # The address is the one source of `screen`, `cwd`, `unit_id` and `detail_tab`: a
    # button builds the address of where it goes and redirects there, and `arrive` — the
    # `on_load` of every route — is the only handler that sets the four (`spec.md` R12).

    def _address(self) -> tuple[str, str, str]:
        """The one place `router` is read: the path, the query, and the socket's id."""
        url = self.router.url
        return url.path, url.query, self.router.session.session_id

    def _name_of(self, cwd: str) -> str:
        return next((w.name for w in self.workspaces if w.id == cwd), "")

    @rx.event
    async def arrive(self):
        """`0056` R15, R16. Put the page where its address says, reading once.

        A page's first arrival — a load, a reload, an address typed into the bar — is told
        from a move inside the app by the socket's `session_id`: it is new on every such
        load and the same across `rx.redirect`, Back and Forward (`spike.md ## U4`). The
        token, and so this state, survives a reload, so an empty state cannot tell it.

        Reflex's `on_load_internal` supersedes: a newer navigation cancels whatever of the
        older one's chain is still running — this handler and what it chained. So what was
        read is recorded only once the read is done (`_loaded_sid`, `_read_cwd`,
        `_read_unit`, `_asked`), and an arrival cut short is read again by the next one.
        """
        path, query, sid = self._address()
        want = place.read(path, query)
        first = sid != self._loaded_sid
        if self.watch_run:
            # `0073`. The pane belongs to the page it was opened on; its loop stops.
            self._watch_reset("", "", "")
        if first:
            self.loading, self.error = True, ""
            # `0053` R9. A new page reads Sessions again when it gets there, as before.
            self._sessions_cwd = ""
            yield
            self._load_base()

        # Which workspace: the first one of that name (C6), else what `_load_workspaces`
        # left, which keeps the current one while it is still listed (R8).
        named = next((w.id for w in self.workspaces if want.ws and w.name == want.ws), "")
        cwd = named or self.cwd
        stray = bool(want.ws) and not named

        screen, unit, tab = want.screen, want.unit, want.tab
        if screen not in place.SCREENS and screen != "unit":
            screen = "overview"
        if screen == "unit" and not unit:
            screen = "board"  # R10
        if screen != "unit":
            unit, tab = "", "overview"
        elif tab not in place.TABS:
            tab = "overview"
        fixed = place.Place(screen, self._name_of(cwd), unit, tab)

        moved_ws = cwd != self._read_cwd
        moved_unit = unit != self._read_unit
        moved_screen = ("board" if screen == "unit" else screen) != self.screen
        self.cwd = cwd
        self.screen = "board" if screen == "unit" else screen
        if moved_ws or moved_unit or moved_screen:
            # Not on an arrival that moved nothing — the one a corrected address causes
            # may come after a person opened the menu (`verify_0071` at 390px).
            self.mobile_open = self.command_open = False
        if moved_ws and not first:
            self.session_id, self.query, self.error = "", "", ""
        # While a workspace's board is read, `cards` is still the last one's: a dialog open
        # over it would show that list's unit, or "not a unit", under this workspace's name.
        # So the unit opens once its own board is in (`0056` review round 1, F1).
        reading = first or moved_ws
        self.unit_id, self.detail_tab = ("", "overview") if reading else (unit, tab)
        self._set_current()
        if reading:
            # `_load_board` empties `cards` before its first await: a read cut short there
            # must leave nothing recorded as read, or going back finds no move and an empty
            # board (`0056` review round 2, F5).
            self._read_cwd = self._read_unit = ""

        # R16: one read, the one of the largest change.
        if first:
            yield
            await self._load_rest()
            self.loading = False
            self._loaded_sid = sid
        elif moved_ws:
            # The last read answered for the workspace just left; a unit of the same name
            # here must not show its session (`0051` review round 1, F1).
            try:
                self._running_read = SERVICE.running(cwd)
            except Invalid:
                self._running_read = {}
            yield
            await self._load_board()
            # `0053` R9. What Sessions showed was the last workspace's; it is read again
            # only if Sessions is where this arrival lands, just below.
            self.conversations, self.messages, self._history = [], [], []
            self._sessions_cwd = ""
            self._load_activity()
        elif (moved_unit and not unit) or (moved_screen and not moved_unit):
            self._load_update()
        if self.screen == "sessions" and self._sessions_cwd != cwd:
            self._load_sessions()
        if self.screen == "cost":
            # `0093` R12: every arrival here reads the run log once; no other screen does.
            self._load_cost()
        self._read_cwd = cwd
        self.unit_id, self.detail_tab = unit, tab
        self._set_current()
        # A unit is read after whatever the arrival read, which R16 does not list: a pasted
        # link or a reload at `/unit` would otherwise open a dialog with no timeline or
        # artifact (R4, R5). None of it calls `SERVICE.board` (R17).
        if unit and (moved_unit or moved_ws or first):
            self._load_unit(forget=not first)
            self._asked = ""
        self._read_unit = unit
        if stray:
            self.notice = "That workspace is not on the list."

        # R7, R8, R10: an address the page had to correct is replaced, not added to. Last,
        # and alone: the arrival it causes would cancel anything chained here, so that one
        # chains it instead, and finds nothing else to read.
        if place.href(fixed) != place.href(want):
            yield rx.redirect(place.href(fixed), replace=True)
            return
        if unit and self._asked != unit:
            self._ask_joins = True
            yield StudioState.load_next
        # A reload that finds the tab already on the Board: nothing else would start the loop.
        yield StudioState.poll_running

    @rx.event
    def navigate(self, screen: str):
        if screen not in SCREEN_TITLES:
            self.notice = "That screen does not exist."
            return
        self.mobile_open = False
        self.command_open = False
        return rx.redirect(place.href(place.Place(screen, self._name_of(self.cwd))))

    @rx.event
    def choose_workspace(self, path: str):
        if not any(w.id == path for w in self.workspaces):
            self.notice = "That workspace is not on the list."
            return
        # R13: a unit belongs to the workspace it was opened in, so leaving it goes to Board.
        screen = "board" if self.unit_id else self.screen
        return rx.redirect(place.href(place.Place(screen, self._name_of(path))))

    @rx.event
    def open_workspace(self, path: str):
        if not any(w.id == path for w in self.workspaces):
            self.notice = "That workspace is not on the list."
            return
        return rx.redirect(place.href(place.Place("board", self._name_of(path))))

    @rx.event(background=True)
    async def poll_running(self):
        """`0051` R3. Ask `Service.running` every `RUNNING_POLL` seconds while the Board shows.

        The one source for every card's `live`, whichever tab, route or process started
        the step (R8). One loop per tab: a second start while one lives returns at once.
        The loop ends when the tab leaves the Board, has no workspace, or has had no socket
        for `GONE_AFTER` asks in a row.
        """
        # The token the event came with: the one the socket server maps to this tab.
        # `router.session.client_token` is empty when no browser hydrated the state.
        token = EventContext.get().token
        if token in _POLLING:
            return
        _POLLING.add(token)
        missed = 0
        try:
            while True:
                missed = missed + 1 if _tab_gone(token) else 0
                if missed >= GONE_AFTER:
                    return
                async with self:
                    if self.screen != "board" or not self.cwd:
                        return
                    try:
                        read = SERVICE.running(self.cwd)
                    except Invalid:
                        read = {}
                    self._apply_running(read)
                    self._load_update()
                await asyncio.sleep(RUNNING_POLL)
        finally:
            _POLLING.discard(token)

    @rx.event
    def search_workspaces(self, value: str):
        self.workspace_query = value

    @rx.event
    def search_work(self, value: str):
        self.query = value

    @rx.event
    def filter_work(self, value: str | list[str]):
        # `rx.segmented_control` hands back a list when it is multi-select. This one is
        # not, and the prototype shipped a version that accepted a list and then indexed a string.
        if not isinstance(value, str) or value not in ("All work", "Autonomous", "Needs you"):
            self.notice = "Choose one of the work filters."
            return
        self.focus = value

    @rx.event
    def set_board_view(self, value: str | list[str]):
        if not isinstance(value, str) or value not in ("Board", "List"):
            self.notice = "Choose the board or the list."
            return
        self.board_view = value
        self._remember("board_view", value)

    @rx.event
    def edit_decision_preferences(self, value: str):
        self.decision_preferences = value

    @rx.event
    def save_decision_preferences(self):
        """`0044` R8a. Kept as typed; each paragraph becomes one `pref:<k>` Jera may cite."""
        self._remember("decision_preferences", self.decision_preferences)
        self.notice = "Decision preferences saved; Jera reads them on its next run."

    @rx.event
    def set_density(self, value: str):
        if value not in ("comfortable", "compact"):
            self.notice = "Choose comfortable or compact."
            return
        self.density = value
        self._remember("density", value)

    @rx.event
    def edit_model(self, name: str, value: str):
        """Typing into one row's box makes it the row being edited."""
        self.model_target = name
        self.model_text = value

    @rx.event
    async def save_model(self, name: str):
        """Set one row's model. Whether it may be set is `Service.set_stage_model`'s call."""
        if name != self.model_target or not self.model_text.strip():
            self.notice = "Type a model name in that row's box first."
            return
        await self._change_model(name, self.model_text)

    @rx.event
    async def reset_model(self, name: str):
        """Remove the override, so the row falls back to the default or `COS_MODEL`."""
        await self._change_model(name, None)

    async def _change_model(self, name: str, model: str | None) -> None:
        self.saving_model = True
        try:
            self._show_models(await SERVICE.set_stage_model(name, model))
        except Invalid as e:
            self.notice = str(e)
            return
        finally:
            self.saving_model = False
        self.model_target, self.model_text = "", ""
        self.notice = (
            f"{name} now runs on {model.strip()} from its next session."
            if model is not None
            else f"{name} is back on its default."
        )

    @rx.event
    def set_autopilot_on(self, value: bool):
        """`0043`. Whether it may be turned on is `Service.set_autopilot`'s call."""
        self._change_autopilot("autopilot", bool(value))

    @rx.event
    def set_autopilot_may_ship(self, value: bool):
        self._change_autopilot("autopilot_may_ship", bool(value))

    @rx.event
    def edit_ap_max_parallel(self, value: str):
        self.ap_max_parallel = value

    @rx.event
    def edit_ap_cap(self, value: str):
        self.ap_cap = value

    @rx.event
    def save_ap_max_parallel(self):
        self._change_autopilot("max_parallel", _number(self.ap_max_parallel, int))

    @rx.event
    def save_ap_cap(self):
        self._change_autopilot("daily_cap_usd", _number(self.ap_cap, float))

    def _change_autopilot(self, name: str, value) -> None:
        try:
            self._show_autopilot(SERVICE.set_autopilot(self.cwd, name, value))
        except Invalid as e:
            self.notice = str(e)
            self._load_autopilot()
            return
        self.notice = "Saved."

    @rx.event
    async def save_effort(self, name: str, effort: str):
        """`0033`. Set one row's effort. Whether it may be set is `Service.set_stage_effort`'s call."""
        await self._change_effort(name, effort)

    @rx.event
    async def reset_effort(self, name: str):
        """Remove the effort override, so the row falls back to its default or the SDK's."""
        await self._change_effort(name, None)

    async def _change_effort(self, name: str, effort: str | None) -> None:
        self.saving_model = True
        try:
            self._show_models(await SERVICE.set_stage_effort(name, effort))
        except Invalid as e:
            self.notice = str(e)
            return
        finally:
            self.saving_model = False
        self.notice = (
            f"{name} now runs at effort {effort} from its next session."
            if effort is not None
            else f"{name} effort is back on its default."
        )

    def _remember(self, key: str, value: str) -> None:
        try:
            SERVICE.set_preference(key, value)
        except Invalid as e:
            self._fail(e)

    @rx.event
    def dismiss_notice(self):
        self.notice, self.error = "", ""

    @rx.event
    def toggle_mobile(self, value: bool):
        self.mobile_open = value

    @rx.event
    def toggle_command(self, value: bool):
        self.command_open = value
        if value:
            self.command_query = ""

    @rx.event
    def search_commands(self, value: str):
        self.command_query = value

    # -- one unit ------------------------------------------------------------

    @rx.event
    def open_unit(self, unit: str):
        # `arrive` reads it (`_load_unit`); the dialog sits over the Board (R13, C9).
        return rx.redirect(place.href(place.Place("unit", self._name_of(self.cwd), unit)))

    @rx.event(background=True)
    async def load_next(self):
        """`0024`. Ask `cos.mjs next` which stage the run button may offer for the open unit.

        In the background because the answer can wait on `gh` for up to 60s (two calls,
        `board.GATE_TIMEOUT` each), and a handler holding the page's lock that long freezes
        every other control. Runs when a unit is opened, after a step ends, and when a
        person presses *Ask again* -- never on a timer: nothing here starts anything (R5).
        """
        async with self:
            unit, cwd = self.unit_id, self.cwd
            join, self._ask_joins = self._ask_joins, False
            self.run_stage = ""
            self.run_waiting = []
            self.run_said = "Asking cos.mjs what comes next…"
            self.rerun_stages, self.rerun_confirming = [], False
            if self._asked != unit:
                # A note written for another unit is not this one's.
                self.rerun_note = ""
        if not (unit and cwd):
            async with self:
                self.run_said = ""
            return
        waiting: list[str] = []
        try:
            stage, said = _run_target(found := await _asking(SERVICE.next_step, cwd, unit, join))
            waiting = _run_waiting(found)
        except Invalid as e:
            stage, said = "", str(e)
        # `0054` R1. Files only, after `next` has answered; a refusal offers nothing.
        try:
            offers = list((await SERVICE.rerun_offers(cwd, unit)).get("offers") or [])
        except Invalid:
            offers = []
        later = {str(o.get("stage") or ""): [str(x) for x in o.get("later") or []] for o in offers}
        async with self:
            # A unit opened while this was asking is not the unit this answer is about.
            if self.unit_id == unit and self.cwd == cwd:
                self.run_stage, self.run_said = stage, said
                self.run_waiting = waiting
                self._asked = unit
                self.rerun_stages, self.rerun_later = list(later), later
                self.rerun_confirming = False
                # The last one offered, the nearest the unit stands: the one that makes the
                # fewest later stages run again. Only a starting value for the select.
                if self.rerun_stage not in later:
                    self.rerun_stage = self.rerun_stages[-1] if self.rerun_stages else ""

    @rx.event
    def toggle_detail(self, value: bool):
        if not value:
            return rx.redirect(place.href(place.Place("board", self._name_of(self.cwd))))

    @rx.event
    def set_detail_tab(self, value: str):
        if value not in place.TABS:
            self.notice = "That tab does not exist."
            return
        # R7: replaced, so Back from a unit goes to where the unit was opened from.
        here = place.Place("unit", self._name_of(self.cwd), self.unit_id, value)
        return rx.redirect(place.href(here), replace=True)

    @rx.event
    def edit_answer(self, key: str, value: str):
        """Typing into one question's box makes it the one being answered."""
        if key != self.answer_target:
            self.answer_target = key
        self.answer_text = value

    @rx.event
    def toggle_details(self, key: str):
        """`0082`. Open or close one *Details*; nothing else reads which are open."""
        self.open_details = ([k for k in self.open_details if k != key]
                             if key in self.open_details else [*self.open_details, key])

    @rx.event

    @rx.event
    async def stop_step(self, unit: str):
        """`0034` R6. Stop one unit's running step. Every rule is `Service.stop_step`'s --
        a missing name, nothing running, a step already writing its artifact -- and its
        refusal is shown as its words. The streaming handler sees the `stopped` outcome."""
        self.error = ""
        try:
            done = await SERVICE.stop_step(self.cwd, unit, "")
            self.notice = f"Stopping {done['unit']} {done['stage']} (by {done['stopped_by']})."
        except Invalid as e:
            self._fail(e)
        finally:
            self._load_running()

    async def set_mode(self, value: str | list[str]):
        if not isinstance(value, str) or value not in ("manual", "autonomous"):
            self.notice = "Choose manual or autonomous."
            return
        stage = self.next_stage
        if not (self.unit_id and stage):
            self.notice = "There is no next step to set a mode on."
            return
        try:
            await SERVICE.set_mode(self.cwd, self.unit_id, stage, value)
        except Invalid as e:
            self._fail(e)
            return
        await self._load_board()
        self._load_activity()

    @rx.event(background=True)
    async def run_step(self):
        """Run the unit's next step, streaming what comes back.

        `background=True` is the difference between a board and a frozen page. A generator
        event handler holds the state lock for its whole life, so a step that takes minutes
        would lock every other control. A background handler takes the lock in short
        bursts, which is why every write below sits inside `async with self`.
        """
        async with self:
            # `0034`. No "already running" check of the page's own: whether this unit may
            # start a step is the service's to refuse, and its `Invalid` lands in `_fail`.
            unit, stage, cwd = self.unit_id, self.next_stage, self.cwd
            if not (unit and stage and cwd):
                self.notice = "There is no next step to run."
                return
            self.run_log = ""
            self.log_unit = unit
            self.error = ""

        listed = False
        try:
            async for kind, payload in SERVICE.run_step(cwd, unit, stage):
                async with self:
                    if not listed:
                        # The step is in the service's list from its first item on.
                        listed = True
                        self._load_running()
                    if kind == "chunk" and self.log_unit == unit:
                        self.run_log += payload
                    elif kind == "done" and isinstance(payload, dict):
                        if payload.get("error"):
                            self.error = payload["error"]
                        outcome = payload.get("outcome") or ""
                        written = payload.get("artifact") or ""
                        self.notice = (
                            f"{stage} {outcome}" + (f" — wrote {written}" if written else "")
                        )
                        # `0030_a-unit-branch-starts-from-a-stale-main`. `describe_base`
                        # is the one sentence saying a step's base may be stale; this page
                        # only appends the string the service already worked out, never
                        # its own reading of `payload["base"]`.
                        stale = describe_base(payload.get("base"))
                        if stale:
                            self.notice += " " + stale
        except Invalid as e:
            async with self:
                self._fail(e)
        finally:
            # Re-read rather than patch. The artifact on disk is the truth about a stage's
            # status, and this is the moment it changed.
            async with self:
                await self._load_board()
                self._load_timeline()
                self._load_artifact()
                self._load_activity()
        # `0024`. The stage that ran is behind the unit now; ask again what is next. This
        # names a stage and runs nothing — a person still presses the button (R5).
        return StudioState.load_next

    # -- sessions ------------------------------------------------------------

    @rx.event
    def choose_session(self, session_id: str):
        if not any(c.id == session_id for c in self.conversations):
            self.notice = "That conversation is not in this workspace."
            return
        self.session_id = session_id
        self.prompt = ""
        self._load_history()

    @rx.event
    def new_session(self):
        """A session exists once something has been said in it, so this only clears the view.

        The SDK owns session identity. Inventing one here would put an id on the page that
        nothing on disk has ever heard of.
        """
        if not self.cwd:
            self.notice = "Choose a workspace first."
            return
        self.session_id = ""
        self.messages, self._history = [], []
        self.prompt = ""
        self.notice = "New conversation. It is saved once you send the first message."

    @rx.event
    def open_message(self, index: int):
        """`0053` R10. Show one cut message whole, from the copy `_load_history` kept.

        Messages `send` adds are never cut and come after every message of `_history`, so
        an index into `messages` below its length is the same message there."""
        full = self.get_value("_history")
        shown = list(self.get_value("messages"))
        if not (0 <= index < len(full) and index < len(shown)):
            return
        shown[index] = Message(role=full[index].role, text=full[index].text, cut=0)
        self.messages = shown

    @rx.event
    def set_prompt(self, value: str):
        self.prompt = value

    @rx.event
    async def send(self):
        text, self.prompt = self.prompt.strip(), ""
        if not text:
            self.notice = "Write a message before sending."
            return
        self.sending, self.error = True, ""
        self.messages = [*self.messages, Message(role="user", text=text),
                         Message(role="assistant", text="")]
        yield
        try:
            SERVICE.check_send(self.cwd, text)
            async for kind, payload in SERVICE.stream(
                self.cwd, text, self.session_id or None
            ):
                if kind == "chunk":
                    self.messages[-1].text += payload
                    self.messages = list(self.messages)
                    yield
                elif kind == "done" and isinstance(payload, dict):
                    # The SDK resolves the id. Taking it from here rather than inventing
                    # one is what keeps the page's idea of a session and the store's idea
                    # of it the same thing.
                    self.session_id = payload.get("session_id") or self.session_id
        except Exception as e:
            self._fail(e)
        finally:
            self.sending = False
        yield
        shown = list(self.get_value("messages"))
        self._load_sessions()
        self._keep_whole(shown)
        self._load_activity()
