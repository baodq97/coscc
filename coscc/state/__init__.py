"""Everything the page shows, and nothing it decides.

No handler here validates anything or builds a path: each calls `Service`, turns `Invalid`
into a line of text, and stops. The dataclasses are a view Reflex can render a list against;
a card's state is `service.unit_state`'s, and the page only lays `Running` over it.

The service instance is the one the FastAPI app holds: two instances would mean two
`Sessions` registries, and "resume only what this app created" would differ by door.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
import logging
from urllib.parse import urlencode

import reflex as rx
from reflex_base.event.context import EventContext

from coscc import plugin
from coscc.state import app, place, present
from coscc.service.agents import AgentPage
from coscc.service.common import COLLAPSED_STATES
from coscc.service.common import FOLDED_STATES
from coscc.service.common import Invalid
from coscc.service.common import describe_base

# Re-exported so `coscc.state.<name>` resolves; a patch reaches only the module that looks it up.
from coscc.state.views import (
    link_fields,
    SCREEN_TITLES,
    _cell_label,
    MARK_COLORS,
    tree_line,
    Workspace,
    Cell,
    Question,
    Unit,
    Card,
    _card,
    _ci_line,
    _shown,
    SpendRow,
    TokenRow,
    WasteRow,
    AnomalyRow,
    _unknown,
    _spend_rows,
    _token_row,
    _waste_rows,
    _anomaly_rows,
    MESSAGE_CUT,
    _relations_text,
    backlog_view,
    _outcome_fields,
    _activities,
    RUNNING_POLL,
    _POLLING,
    ATTEMPT_WAKES,
    listen_to_attempts,
    GONE_AFTER,
    _asking,
    _tab_gone,
    _hold_fields,
    _hold_detail,
    _integration_fields,
    Message,
    Conversation,
    AutopilotStop,
    GuideItem,
    RunningStep,
    Run,
    Move,
    _moves,
    READ_ONLY_NOTE,
    AUTOPILOT_STOP_LABEL,
    _number,
    Event,
    Knob,
    knob,
    FeatureRow,
    schedule_label,
    ImportRow,
    AgentListRow,
    AgentDetail,
    OtherRow,
    agent_views,
    _run_target,
    _run_waiting,
    _run_dropped,
    _tokens,
    COST_NOTE,
    cost_note,
    per_merged_unit,
    _usd,
    _title_of,
    _initials,
    _questions,
    _rounds,
    _current_stage,
)
from coscc.state.workspaces import (
    WorkspacesMixin,
)
from coscc.state.watch import (
    WatchMixin,
)
from coscc.state.update import (
    UpdateMixin,
)
from coscc.state.answers import (
    AnswersMixin,
)
from coscc.state.backlog import (
    BacklogMixin,
)
from coscc.state.rerun import (
    RerunMixin,
)
from coscc.state.ideas import (
    IdeasMixin,
)
from coscc.state.release import ReleaseMixin

log = logging.getLogger(__name__)

# How long a tab's board watch waits on one read before it looks at whether the tab is still
# on the Board; a wait that times out just starts again.
BOARD_WAIT = 5
# The tabs (by token) whose `watch_board` loop is running.
_WATCHING: set[str] = set()


def _fingerprint(data: dict) -> int:
    """What a board read says, apart from when it was read: two reads of one board agree.
    Taken at once, because the service lays the autopilot's block on the held dict in place."""
    return hash(
        json.dumps({k: v for k, v in data.items() if k != "read_at"}, sort_keys=True, default=str)
    )


class StudioState(
    WorkspacesMixin,
    WatchMixin,
    UpdateMixin,
    AnswersMixin,
    BacklogMixin,
    RerunMixin,
    IdeasMixin,
    ReleaseMixin,
    rx.State,
):
    """The whole page. No business state lives here — it is all read back from `Service`."""

    screen: str = "overview"
    # The socket `session_id` of the last full read; another one is a new page (`arrive`).
    # Then the workspace and the unit last read, and the unit `load_next` last answered
    # for. Each is set once its read is done. Backend only.
    _loaded_sid: str = ""
    _read_cwd: str = ""
    _read_unit: str = ""
    _asked: str = ""
    # Set by `arrive` for the `load_next` it chains, which then waits for an ask already in
    # flight; *Ask again*, a step's end and a hold move ask afresh.
    _ask_joins: bool = False
    loading: bool = False
    busy: bool = False
    error: str = ""
    notice: str = ""

    # -- board
    stages: list[str] = []
    # Each column's agent, by stage, as `Service.board` resolved it: the glyph,
    # `<Name> (agent, <stage>)`, and its meaning and role one per line.
    stage_glyphs: dict[str, str] = {}
    stage_labels: dict[str, str] = {}
    stage_notes: dict[str, str] = {}
    # Every unit of the last board read, whole, keyed by id in board order. Backend only:
    # the page gets `cards` and, for the one unit open, `current_unit`. Always assigned a
    # new dict, never changed in place.
    _full: dict[str, Unit] = {}
    # The one list of cards the page receives; the stage columns, List, the collapsed
    # groups, the palette and Overview all filter it on the page.
    cards: list[Card] = []
    # The open unit, whole, copied from `_full` by `_set_current`, never by a computed var
    # walking a list the page would then receive too.
    current_unit: Unit = Unit()
    board_note: str = ""
    # Set only when the board is empty: the store the board read, the host repository, and
    # how many units the host's own `.cos/` holds that the board does not list.
    empty_store: str = ""
    empty_host: str = ""
    empty_host_units: int = 0
    recording: bool = False
    # `Board.running`'s latest answer, kept so a board read that rebuilds every card can
    # put `live` back on at once. Backend only.
    _running_read: dict = {}
    # `_fingerprint` of the board last put on the page, so `watch_board` sends nothing for a
    # read that says the same. Backend only.
    _board_seen: int = 0
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
    # The unit's transitions, beside its runs on the Timeline tab.
    moves: list[Move] = []
    # Every step running in this workspace, as the service lists it: one per unit, any
    # number of units. The list is re-read, never patched.
    running_steps: list[RunningStep] = []
    # The board's `autopilot` block, copied: whether it is on, its stops, the cap.
    autopilot_on: bool = False
    autopilot_stops: list[AutopilotStop] = []
    autopilot_cap: str = ""
    autopilot_refused: str = ""
    # The board's `guide` block, copied: what runs, what needs a person (one per `Needs you`
    # card), what the autopilot holds back on other cards, the workspace's own notes, and
    # whether the shortlist has no unit.
    guide_running: list[GuideItem] = []
    guide_needs_you: list[GuideItem] = []
    guide_held: list[GuideItem] = []
    guide_notes: list[GuideItem] = []
    guide_shortlist_empty: bool = False
    run_log: str = ""
    # The unit whose step this page is streaming into `run_log`, so another unit's reply
    # is never shown under the one now open.
    log_unit: str = ""
    # Which *Details* are open, by key. Closed, their content is not in the DOM.
    open_details: list[str] = []
    # The stage `coscc.loop next` names for the open unit, and what it said. Set only by
    # `load_next`, from `_run_target`.
    run_stage: str = ""
    run_said: str = ""
    # The findings `coscc.loop next` says a person is awaited on; set only by `load_next`.
    # Non-empty means the button offers nothing and the page points at the Questions tab.
    run_waiting: list[str] = []
    # The ids `coscc.loop next` says the last review round left out; set only by `load_next`.
    run_dropped: list[str] = []

    # -- sessions
    conversations: list[Conversation] = []
    session_id: str = ""
    # What the page shows: each message of `_history`, in order, cut to `MESSAGE_CUT`
    # characters; `open_message` puts one back whole.
    messages: list[Message] = []
    _history: list[Message] = []
    # The workspace the conversation list was last read for, `""` when none: Sessions
    # reads it on arrival only when this differs from `cwd`.
    _sessions_cwd: str = ""
    prompt: str = ""
    sending: bool = False

    # -- activity and settings
    events: list[Event] = []
    usage_total_usd: str = "—"
    usage_cost_note: str = COST_NOTE
    # What every workspace spent today, and against which cap.
    today_spent: str = "—"
    today_note: str = ""
    # The *Cost* screen, read only on arrival there, and the open unit's part.
    cost_recording: bool = True
    cost_total_usd: str = "—"
    cost_total_steps: str = "0"
    cost_total_unknown: str = ""
    cost_offset: str = ""
    cost_per_merged: str = "—"
    cost_merged: int = 0
    cost_units: list[SpendRow] = []
    cost_stages: list[SpendRow] = []
    cost_days: list[SpendRow] = []
    cost_tokens: list[TokenRow] = []
    cost_waste: list[WasteRow] = []
    cost_anomalies: list[AnomalyRow] = []
    unit_cost_stages: list[SpendRow] = []
    unit_anomalies: list[AnomalyRow] = []
    knobs: list[Knob] = []
    features: list[FeatureRow] = []
    # The fields an import could not read, and why the report itself could not be.
    import_rows: list[ImportRow] = []
    import_problem: str = ""
    data_dir: str = ""
    host_port: str = ""
    # Whether the bound address reaches this machine only. The default is `0.0.0.0`, so
    # the page must read this rather than state "loopback" as a fact.
    loopback_only: bool = True
    # `COS_MODEL`, the fallback for a row nothing else answers.
    model: str = ""
    # The Agents page, as `Agents.agent_page` had it on arriving and after the last save: the
    # table, one drawer's worth per agent, the "Other sessions" rows and what was skipped.
    agent_list: list[AgentListRow] = []
    agent_details: list[AgentDetail] = []
    agent_others: list[OtherRow] = []
    agent_problems: list[str] = []
    # The chip the table is narrowed to, `""` for all; the agent whose drawer is open.
    agent_chip: str = ""
    agent_key: str = ""
    # One workspace's autopilot settings, as `Autopilot.settings` has them.
    ap_on: bool = False
    ap_may_ship: bool = False
    ap_max_parallel: str = ""
    ap_cap: str = ""
    ap_refused: str = ""
    # One workspace's `allow` and `block` for `impl`, saved and as typed (space-separated).
    impl_allow: list[str] = []
    impl_block: list[str] = []
    impl_allow_text: str = ""
    impl_block_text: str = ""

    # -- chrome
    mobile_open: bool = False
    command_open: bool = False
    command_query: str = ""
    # The feature page `/feature` frames (`_show_feature`).
    feature: str = ""
    feature_label: str = ""
    feature_src: str = ""
    feature_off: bool = False

    # -- computed ------------------------------------------------------------

    @rx.var
    def screen_title(self) -> str:
        # `/idea` and `/feature` are no screens of the navigation, and their titles are their own.
        if self.screen == "feature":
            return self.feature_label
        return "Idea" if self.screen == "idea" else SCREEN_TITLES.get(self.screen, "Overview")

    @rx.var
    def session_title(self) -> str:
        """The open conversation's title, as its row in the list shows it."""
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

    # The columns, the List, the collapsed groups, the palette and Overview each filter
    # `cards` on the page by one of the lists of ids below: a list of cards per column would
    # send every card again. Order is always `cards`' order.

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
            # The state, as the service decided it.
            rows = [c for c in rows if c.state == "needs-you"]
        return [c.id for c in rows]

    @rx.var
    def resume_id(self) -> str:
        """*Pick up where you left off*: a `Running` card, else `Ready` with an artifact past its
        idea (`begun`), each in board order — column, then place in it; `""` if none. *Needs
        you* is listed above it, and a unit that only has its idea is not work left off."""
        board = set(self.board_ids)
        order = {name: i for i, name in enumerate(self.stages)}
        rank = {"running": 0, "ready": 1}
        rows = [
            (rank[c.state], order.get(c.at, len(order)), i, c.id)
            for i, c in enumerate(self.cards)
            if c.id in board and c.state in rank and (c.state != "ready" or c.begun)
        ]
        return min(rows)[3] if rows else ""

    @rx.var
    def active_count(self) -> int:
        """Every unit that is not done, paused or dropped."""
        return len([c for c in self.cards if c.state not in COLLAPSED_STATES])

    @rx.var
    def attention_count(self) -> int:
        """Every unit whose state is *Needs you*."""
        return len([c for c in self.cards if c.state == "needs-you"])

    @rx.var
    def board_ids(self) -> list[str]:
        """The shown cards the stage lanes draw: every one outside the done and dropped
        groups (a paused one stays in its lane)."""
        shown = set(self.shown_ids)
        return [c.id for c in self.cards if c.id in shown and c.state not in FOLDED_STATES]

    @rx.var
    def stage_counts(self) -> dict[str, int]:
        """How many cards each stage's lane holds, keyed by the stages the board read returned."""
        counts = {name: 0 for name in self.stages}
        board = set(self.board_ids)
        for c in self.cards:
            if c.id in board:
                counts[c.at] = counts.get(c.at, 0) + 1
        return counts

    @rx.var
    def group_counts(self) -> dict[str, int]:
        """How many shown cards each collapsed group holds: the search and the filter narrow
        a group as they narrow a lane."""
        counts = {name: 0 for name in FOLDED_STATES}
        shown = set(self.shown_ids)
        for c in self.cards:
            if c.id in shown and c.state in counts:
                counts[c.state] += 1
        return counts

    @rx.var
    def ws_name(self) -> str:
        """What `ws=` carries: the workspace's name, never its path."""
        return next((w.name for w in self.workspaces if w.id == self.cwd), "")

    @rx.var
    def unit_missing(self) -> bool:
        """An address named a unit this workspace's board does not list."""
        return (
            self.unit_id != ""
            and not self.loading
            and not any(c.id == self.unit_id for c in self.cards)
        )

    @rx.var
    def unit_dropped(self) -> bool:
        """The open unit is dropped, so the dialog offers nothing that writes."""
        return self.current_unit.hold_state == "dropped"

    @rx.var
    def board_href(self) -> str:
        ws = next((w.name for w in self.workspaces if w.id == self.cwd), "")
        return place.href(place.Place("board", ws))

    @rx.var
    def settings_href(self) -> str:
        """Where the guide sends a person to turn the autopilot on."""
        ws = next((w.name for w in self.workspaces if w.id == self.cwd), "")
        return place.href(place.Place("settings", ws))

    @rx.var
    def backlog_href(self) -> str:
        """Where the guide sends a person when no unit is on the shortlist."""
        ws = next((w.name for w in self.workspaces if w.id == self.cwd), "")
        return place.href(place.Place("backlog", ws))

    @rx.var
    def open_questions_here(self) -> list[Question]:
        """The open unit's unanswered questions, the counted artifact's first."""
        shown = [q for q in self.current_unit.questions if not q.answered]
        return sorted(shown, key=lambda q: not q.counted)

    @rx.var
    def answer_first(self) -> bool:
        """The dialog's first action is answering: the open unit has questions it can still
        answer, and the next step reads the answers (a run's prompt carries them)."""
        return (
            bool(self.open_questions_here)
            and self.current_unit.answerable
            and self.current_unit.hold_state != "dropped"
        )

    @rx.var
    def next_stage(self) -> str:
        """The stage the run button would run: the one `coscc.loop next` named."""
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
    def agent_shown(self) -> list[AgentListRow]:
        """The table's rows, narrowed to the chip chosen."""
        return [r for r in self.agent_list if not self.agent_chip or r.chip == self.agent_chip]

    @rx.var
    def agent_detail(self) -> AgentDetail:
        """The open drawer's agent; nothing while no drawer is open."""
        return next((d for d in self.agent_details if d.key == self.agent_key), AgentDetail())

    @rx.var
    def cost_over_count(self) -> int:
        return len([r for r in self.cost_units if r.over])

    # -- plumbing ------------------------------------------------------------

    def _fail(self, e: Exception) -> None:
        """`Invalid` carries a reason a caller shows verbatim. Nothing rewrites it."""
        self.error = str(e)

    # -- loading -------------------------------------------------------------

    def _load_settings(self) -> None:
        data = app.SERVICE.activity.settings()
        self.working_dir = data.get("working_dir") or ""
        self.data_dir = data.get("data_dir") or ""
        self.host_port = f"{data.get('host')}:{data.get('port')}"
        self.loopback_only = data.get("host") in ("127.0.0.1", "localhost", "::1")
        self.model = data.get("cos_model") or "unset"
        self.knobs = [knob(k) for k in data.get("knobs") or []]
        report = data.get("import_report") or {}
        self.import_rows = [
            ImportRow(
                workspace=str(r["workspace"]),
                unit=str(r["unit"]),
                artifact=str(r["artifact"]),
                field=str(r["field"]),
                reason=str(r["reason"]),
            )
            for r in report.get("rows") or []
        ]
        self.import_problem = str(report.get("problem") or "")

    def _show_agents(self, page: AgentPage) -> None:
        """What `Agents.agent_page` returned: the table, each drawer and the other sessions."""
        self.agent_list, self.agent_details, self.agent_others = agent_views(page)
        self.agent_problems = [str(p) for p in page["problems"]]

    def _load_agents(self) -> None:
        self._show_agents(app.SERVICE.agents.agent_page())

    async def _load_workspace_settings(self) -> None:
        self._load_autopilot()
        self._load_command_lists()
        self._load_features()

    def _load_features(self) -> None:
        """Each feature `api.build` loaded, its state in this workspace and its sentence."""
        if not self.cwd:
            self.features = []
            return
        self.features = [
            FeatureRow(
                f.name,
                f.state,
                f.pilot,
                f.sentence,
                f.locked,
                schedule_label(f.schedule) if f.schedule is not None else "",
                [schedule_label(h) for h in f.hours],
                f.summary,
            )
            for f in plugin.shown(app.API.state.ctx, app.API.state.plugins, self.cwd)
        ]

    @rx.event
    def set_feature(self, name: str, state: str):
        """The same call as `POST /api/features`: it writes the pref and tells the feature."""
        try:
            plugin.set_state(
                app.SERVICE, app.API.state.ctx, app.API.state.plugins, name, self.cwd, state
            )
        except Invalid as e:
            self.notice = str(e)
        self._load_features()

    @rx.event
    def set_feature_schedule(self, name: str, label: str):
        """The same call as `POST /api/features` with `schedule`."""
        hours = [
            h
            for f in app.API.state.plugins
            if f.name == name and f.schedule
            for h in f.schedule.hours
            if schedule_label(h) == label
        ]
        try:
            plugin.set_schedule_of(
                app.SERVICE, app.API.state.plugins, name, self.cwd, hours[0] if hours else label
            )
        except Invalid as e:
            self.notice = str(e)
        self._load_features()

    def _show_command_lists(self, data: dict) -> None:
        self.impl_allow = [str(n) for n in data.get("allow") or []]
        self.impl_block = [str(n) for n in data.get("block") or []]
        self.impl_allow_text = " ".join(self.impl_allow)
        self.impl_block_text = " ".join(self.impl_block)

    def _load_command_lists(self) -> None:
        if not self.cwd:
            return
        try:
            self._show_command_lists(app.SERVICE.ws.command_lists(app.SERVICE.ws.check(self.cwd)))
        except Invalid:
            return

    def _show_autopilot_block(self, block: dict) -> None:
        """Copied from the board; nothing here decides whether to stop."""
        self.autopilot_on = bool(block.get("on"))
        self.autopilot_refused = str(block.get("refused_because") or "")
        self.autopilot_stops = [
            AutopilotStop(
                unit=str(x.get("unit") or "the workspace"),
                kind=AUTOPILOT_STOP_LABEL.get(str(x.get("kind") or ""), str(x.get("kind") or "")),
                reason=str(x.get("reason") or ""),
            )
            for x in block.get("stops") or []
        ]
        cap = block.get("cap") or {}
        self.autopilot_cap = (
            ""
            if not cap
            else f"Today: {cap.get('spent', 0):.2f} spent, "
            + (
                f"{cap.get('estimated', 0):.2f} of it estimated, "
                if cap.get("estimated_count")
                else ""
            )
            + f"{cap.get('running', 0):.2f} running, cap {cap.get('limit', 0):.2f} USD"
        )

    def _show_guide(self, block: dict) -> None:
        """Copied from the board; every word of `needs_you`, `held` and `notes` is the
        service's."""
        ws = self._name_of(self.cwd)

        def link(screen: str, unit: str = "", tab: str = "overview") -> str:
            if screen == "unit" and not unit:
                return ""
            return place.href(place.Place(screen, ws, unit, tab or "overview"))

        def todo(r: dict) -> GuideItem:
            return GuideItem(
                unit=str(r.get("unit") or ""),
                what=str(r.get("do") or ""),
                detail=str(r.get("reason") or ""),
                href=link(
                    str(r.get("screen") or ""), str(r.get("unit") or ""), str(r.get("tab") or "")
                ),
            )

        self.guide_running = [
            GuideItem(
                unit=str(r.get("unit") or ""),
                what=" · ".join(
                    x for x in (str(r.get("stage") or ""), str(r.get("agent") or "")) if x
                ),
                detail="started " + present.when(r.get("started")),
                href=link("unit", str(r.get("unit") or "")),
            )
            for r in block.get("running") or []
        ]
        self.guide_needs_you = [todo(r) for r in block.get("needs_you") or []]
        self.guide_held = [todo(r) for r in block.get("held") or []]
        self.guide_notes = [todo(r) for r in block.get("notes") or []]
        self.guide_shortlist_empty = bool(block.get("shortlist_empty"))

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
            self._show_autopilot(app.SERVICE.autopilot.settings(self.cwd))
        except Invalid:
            return

    def _load_workspaces(self) -> None:
        data = app.SERVICE.ws.all()
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
        """In memory and synchronous: no `gh`, no git, so a step's first chunk can ask it
        without waiting on what a full board read waits on."""
        self.running_steps = []
        if not self.cwd:
            return
        try:
            self.running_steps = [
                RunningStep(
                    **{
                        **r,
                        "started_at": present.when(r.get("started_at")),
                        "run": r.get("run") or "",
                    }
                )
                for r in app.SERVICE.steps.running_steps(self.cwd)
            ]
        except Invalid:
            self.running_steps = []

    def _set_current(self) -> None:
        """`current_unit` from `_full` for `unit_id`: its own deep copy, so nothing shares an
        object with `_full`. Called wherever either of the two changes."""
        found = self.get_value("_full").get(self.unit_id)
        self.current_unit = copy.deepcopy(found) if found is not None else Unit()

    async def _load_board(self) -> None:
        """The board held for this workspace: at once when one was read, else when the first
        read ends. The next read, if anything changed, arrives through `watch_board`."""
        self._full, self.cards, self.stages, self.board_note = {}, [], [], ""
        self._set_current()
        self.empty_store, self.empty_host, self.empty_host_units = "", "", 0
        self.branch = ""
        self._board_seen = 0
        self._load_running()
        if not self.cwd:
            return
        try:
            self.branch = (await app.SERVICE.backlog.branch_here(self.cwd))["branch"]
        except Invalid:
            # A workspace that is not a git checkout still has a board. Saying nothing is
            # right here: there is no branch to show, and that is not an error to report.
            self.branch = ""
        try:
            data = await app.SERVICE.board(self.cwd, "held")
        except Invalid as e:
            self.board_note = str(e)
            return
        self._board_seen = _fingerprint(data)
        self._show_board(data)

    def _show_board(self, data: dict) -> None:
        """Put one board read on the page: every var `_load_board` fills from it."""
        self.stages = list(data["stages"])
        found = data.get("stage_agents") or {}
        self.stage_glyphs = {k: str(v.get("glyph") or "") for k, v in found.items()}
        self.stage_labels = {k: str(v.get("label") or "") for k, v in found.items()}
        self.stage_notes = {
            k: "\n".join(str(v.get(f) or "") for f in ("meaning", "role") if v.get(f))
            for k, v in found.items()
        }
        self._show_ideas(data)
        for name, value in backlog_view(data).items():
            setattr(self, name, value)
        self._backlog_history = dict((data.get("backlog") or {}).get("history") or {})
        self._trees = {u["name"]: tree_line(u.get("worktree")) for u in data.get("units") or []}
        self.recording = bool(data["recording"])
        self._show_autopilot_block(data.get("autopilot") or {})
        self._show_guide(data.get("guide") or {})
        self._show_release(data.get("release"))
        read_only = READ_ONLY_NOTE if data.get("read_only_because") else ""
        self.board_note = read_only or data.get("empty_because") or ""
        empty = data.get("empty") or {}
        self.empty_store = str(empty.get("store") or "")
        self.empty_host = str(empty.get("host") or "")
        self.empty_host_units = int(empty.get("host_units") or 0)
        if self.empty_host_units > 0:
            # `empty_because` speaks of the store's `.cos/`; next to a full host `.cos/` it
            # would read as a claim about the wrong directory. Only the read-only reason survives.
            self.board_note = read_only

        units: list[Unit] = []
        for u in data["units"]:
            cells = []
            for row in u["stages"]:
                label, color = _cell_label(row)
                cells.append(
                    Cell(
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
                    )
                )
            started = len([c for c in cells if c.started])
            stage = _current_stage(u, self.stages)
            count, shown = _tokens(u.get("cost") or {})
            # The file-only stage the loop names, not the one the run button asks for: that
            # one can cost two `gh` calls, and this runs for every card.
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
                    mode=mode,
                    tokens=shown,
                    usd=_usd(u.get("cost") or {}),
                    token_count=count,
                    progress=int(started * 100 / len(cells)) if cells else 0,
                    begun=any(c.started for c in cells if c.stage != "idea"),
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
                    more_rounds=bool(u.get("more_rounds")),
                    shortlist_rank=int((u.get("backlog") or {}).get("rank") or 0),
                    relations_text=_relations_text((u.get("backlog") or {}).get("relations")),
                    answerable=bool(u.get("answerable", True)),
                    attention_reason=str(u.get("attention_reason") or ""),
                    at=str(u.get("at") or ""),
                    ci_line=_ci_line(decided.get("ci")),
                    decided_state=str(decided.get("state") or ""),
                    decided_label=str(decided.get("label") or ""),
                    decided_color=str(decided.get("color") or "gray"),
                    **link_fields(u, self._name_of(self.cwd)),
                    held=str(u.get("held") or ""),
                )
            )
        units = [dataclasses.replace(u, **_shown(u, self._running_read)) for u in units]
        self._full = {u.id: u for u in units}
        self.cards = [_card(u) for u in units]
        self._set_current()

    def _apply_running(self, read: dict) -> None:
        """Put one `Board.running` answer on every card. Decides nothing.

        The cards are sent again only when a card's `live` or covered state changed, and the
        open unit only when its own did: an ask that changes nothing sends neither."""
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
            data = app.SERVICE.chat.sessions_for(self.cwd, limit=40)
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
        # This page's own conversations first; the read-only ones (terminal sessions, the
        # app's estimates) after them, each group newest first as listed.
        rows.sort(key=lambda c: not c.resumable)
        self.conversations = rows
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
            data = app.SERVICE.chat.history(self.cwd, self.session_id)
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
        """After `send` reads the conversation again, a message the page showed whole (the
        streamed reply, one opened with `open_message`) stays whole. Matched on the text,
        not the index: the read may not line up with what streamed."""
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
        self.usage_total_usd, self.usage_cost_note = "—", COST_NOTE
        self.today_spent, self.today_note = "—", ""
        if not self.cwd:
            return
        try:
            today = app.SERVICE.autopilot.today(self.cwd)
        except Invalid:
            today = None
        if today is not None:
            spent, limit = today
            self.today_spent = f"${spent:.2f}"
            self.today_note = f"Of the ${limit:g} daily cap, across every workspace"
        try:
            # One read for both halves of this screen; `activity` and `usage` on their own
            # would each scan and parse the identical rows.
            feed = app.SERVICE.activity.activity_and_usage(self.cwd, limit=40)
        except Invalid as e:
            self._fail(e)
            return
        icons = {
            "mode": ("sliders-horizontal", "blue"),
            "start": ("zap", "iris"),
            "end": ("circle-check", "grass"),
            # What a stopped step left behind, captured just before `end`.
            "attempt": ("camera", "amber"),
            # A person paused, dropped or resumed a unit.
            "hold": ("pause", "amber"),
            # A release press, refused ones included.
            "release": ("tag", "iris"),
            "transition": ("arrow-right", "gray"),
            "questions": ("circle-help", "amber"),
        }
        events: list[Event] = []
        for row in feed["events"]:
            icon, color = icons.get(row["kind"], ("dot", "gray"))
            if (row["kind"] == "end" and row["outcome"] != "done") or (
                row["kind"] == "release" and row["outcome"] in ("refused", "failed")
            ):
                icon, color = "triangle-alert", "amber"
            title = {
                "mode": f"{row['stage']} set to {row['mode']}",
                "start": f"{row['stage']} started ({row['mode']})",
                "end": f"{row['stage']} {row['outcome']}",
                "attempt": f"{row['stage']} stopped — what it left was recorded",
                "hold": f"{row.get('from', '')} → {row.get('to', '')}",
                "release": f"release {row.get('version', '')} {row['outcome']}",
                "transition": f"{row['artifact']} {row.get('to_state') or 'changed'}",
                "questions": f"{row.get('asked', 0)} question(s) for you",
                "ship": "shipped" if row.get("result") == "shipped" else "ship refused",
            }.get(row["kind"], row["kind"].replace("-", " "))
            detail = f"{row['unit']}"
            if row["kind"] == "hold":
                detail += _hold_detail(row)
            if row["kind"] == "release":
                detail = str(row.get("detail") or "")
            if row["denials"]:
                detail += f" / {row['denials']} tool call(s) refused"
            if row["artifact"] and row["kind"] != "transition":
                detail += f" / wrote {row['artifact']}"
            events.append(
                Event(
                    title=title, detail=detail, icon=icon, color=color, time=present.when(row["at"])
                )
            )
        self.events = events
        total = feed.get("total") or {}
        # The known part; the caption counts the runs without a cost.
        self.usage_total_usd = _usd({**total, "unknown": 0})
        self.usage_cost_note = cost_note(total)

    def _load_cost(self) -> None:
        """The *Cost* screen, from one read of the run log. The review rounds come from the
        board already read; nothing here adds or decides a figure."""
        self.cost_units, self.cost_stages, self.cost_days = [], [], []
        self.cost_tokens, self.cost_waste, self.cost_anomalies = [], [], []
        self.cost_total_usd, self.cost_total_steps, self.cost_total_unknown = "—", "0", ""
        self.cost_offset, self.cost_recording = "", True
        self.cost_per_merged, self.cost_merged = "—", 0
        if not self.cwd:
            return
        rounds = {key: [r.verdict for r in u.rounds] for key, u in self.get_value("_full").items()}
        try:
            data = app.SERVICE.activity.cost(self.cwd, rounds)
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
        done = {c.id for c in self.cards if c.state == "done"}
        self.cost_per_merged, self.cost_merged = per_merged_unit(data["by_unit"], done)
        self.cost_stages = _spend_rows(data["by_stage"])
        self.cost_days = _spend_rows(data["by_day"])
        tokens = data["tokens"]
        self.cost_tokens = [_token_row("Workspace", tokens["workspace"])] + [
            _token_row(t["stage"] or "—", t) for t in tokens["by_stage"]
        ]
        self.cost_waste = _waste_rows(data["waste"])
        # Over budget is the units table's red figure; here it would be one row per unit again.
        self.cost_anomalies = _anomaly_rows(
            [a for a in data["anomalies"] if a["kind"] != "over-budget"]
        )

    def _load_unit_cost(self) -> None:
        """The open unit's cost by stage and its anomalies."""
        self.unit_cost_stages, self.unit_anomalies = [], []
        if not (self.cwd and self.unit_id):
            return
        try:
            data = app.SERVICE.activity.unit_cost(self.cwd, self.unit_id)
        except Invalid as e:
            self._fail(e)
            return
        self.unit_cost_stages = _spend_rows(data["by_stage"])
        self.unit_anomalies = _anomaly_rows(data["anomalies"])

    def _load_timeline(self) -> None:
        self.runs, self.moves = [], []
        if not (self.cwd and self.unit_id):
            return
        try:
            data = app.SERVICE.backlog.timeline(self.cwd, self.unit_id)
        except Invalid as e:
            self._fail(e)
            return
        self.runs = [
            Run(
                stage=r.get("stage") or "",
                mode=r.get("mode") or "",
                # A reader's time; `ended` is empty while the run is not over.
                started=present.when(r.get("started")),
                ended=present.when(r.get("ended")),
                outcome=r.get("outcome") or "—",
                session_id=(r.get("session_id") or "—")[:12],
                tokens=_tokens(r.get("cost") or {})[1],
                # An ended run that reported no cost reads `unknown`, not `—`.
                usd=_usd(
                    {
                        **(r.get("cost") or {}),
                        "unknown": int(r.get("ended") is not None and not r.get("reported", True)),
                    }
                ),
                color="grass" if r.get("outcome") == "done" else "amber",
                detail=r.get("detail") or "",
                run=r.get("run") or "",
                key=r.get("run") or f"{r.get('stage') or ''}-{i}",
            )
            for i, r in enumerate(data["runs"])
        ]
        self.moves = _moves(data.get("transitions") or [])

    def _load_artifact(self) -> None:
        self.artifact, self.artifact_file, self.artifact_missing = "", "", False
        if not (self.cwd and self.unit_id):
            return
        stage = self.current_unit.stage or (self.stages[0] if self.stages else "")
        if not stage:
            return
        try:
            data = app.SERVICE.activity.artifact(self.cwd, self.unit_id, stage)
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
            prefs = app.SERVICE.activity.preferences()
            self.density = str(prefs.get("density") or "comfortable")
            self.board_view = str(prefs.get("board_view") or "Board")
            self._load_workspaces()
        except Invalid as e:
            self._fail(e)

    def _load_unit(self, forget: bool = True) -> None:
        """What opening a unit reads. Asked even of a unit the board does not list: the
        board may predate it, and whether it exists is the service's to say."""
        self.run_log = ""
        if forget:
            # A message shown in the dialog is one made after it opened. Not on a page's
            # first arrival, whose messages are about the load itself.
            self.error, self.notice = "", ""
        self._load_timeline()
        self._load_unit_cost()
        self._load_artifact()

    # -- where the page is ------------------------------------------
    #
    # The address is the one source of `screen`, `cwd`, `unit_id` and `detail_tab`: a
    # button redirects to the address of where it goes, and `arrive` (the `on_load` of
    # every route) is the only handler that sets the four.

    def _address(self) -> tuple[str, str, str]:
        """The one place `router` is read: the path, the query, and the socket's id."""
        url = self.router.url
        return url.path, url.query, self.router.session.session_id

    def _name_of(self, cwd: str) -> str:
        return next((w.name for w in self.workspaces if w.id == cwd), "")

    @rx.event
    async def arrive(self):  # noqa: C901, PLR0915 - still to split
        """Put the page where its address says, reading once.

        A first arrival (a load, a reload, a typed address) is told from a move inside the
        app by the socket's `session_id`: new on every such load, the same across
        `rx.redirect`, Back and Forward. The token, and so this state, survives a reload, so
        an empty state cannot tell it.

        A newer navigation cancels whatever of the older one's chain is still running, so
        what was read is recorded only once the read is done (`_loaded_sid`, `_read_cwd`,
        `_read_unit`, `_asked`), and an arrival cut short is read again by the next one.
        """
        path, query, sid = self._address()
        want = place.read(path, query)
        first = sid != self._loaded_sid
        if self.watch_run:
            # The pane belongs to the page it was opened on; its loop stops.
            self._watch_reset("", "", "")
        if first:
            self.loading, self.error = True, ""
            # A new page reads Sessions again when it gets there.
            self._sessions_cwd = ""
            yield
            self._load_base()

        # Which workspace: the first one of that name, else what `_load_workspaces` left,
        # which keeps the current one while it is still listed.
        named = next((w.id for w in self.workspaces if want.ws and w.name == want.ws), "")
        cwd = named or self.cwd
        stray = bool(want.ws) and not named

        screen, unit, tab = want.screen, want.unit, want.tab
        if screen not in place.SCREENS and screen not in ("unit", "idea", "feature"):
            screen = "overview"
        if (screen == "unit" and not unit) or (screen == "idea" and not want.idea):
            screen = "board"
        if screen == "feature" and want.feature not in app.API.state.pages:
            screen = "overview"
        if screen != "unit":
            unit, tab = "", "overview"
        elif tab not in place.TABS:
            tab = "overview"
        # An idea is read after the board, every arrival, like a unit's dialog.
        idea = want.idea if screen == "idea" else ""
        feature = want.feature if screen == "feature" else ""
        agent = want.agent if screen == "agents" else ""
        fixed = place.Place(
            screen, self._name_of(cwd), unit, tab, idea=idea, feature=feature, agent=agent
        )

        moved_ws = cwd != self._read_cwd
        moved_unit = unit != self._read_unit
        moved_screen = ("board" if screen == "unit" else screen) != self.screen
        self.cwd = cwd
        self.screen = "board" if screen == "unit" else screen
        if moved_ws or moved_unit or moved_screen:
            # Not on an arrival that moved nothing: the one a corrected address causes may
            # come after a person opened the menu.
            self.mobile_open = self.command_open = False
        if moved_ws and not first:
            self.session_id, self.query, self.error = "", "", ""
        # While a workspace's board is read, `cards` is still the last one's: a dialog open
        # over it would show that list's unit, or "not a unit", under this workspace's name.
        # So the unit opens once its own board is in.
        reading = first or moved_ws
        self.unit_id, self.detail_tab = ("", "overview") if reading else (unit, tab)
        self._set_current()
        if reading:
            # `_load_board` empties `cards` before its first await: a read cut short there
            # must leave nothing recorded as read, or going back finds no move and an empty board.
            self._read_cwd = self._read_unit = ""

        # One read, the one of the largest change.
        if first:
            # The cards first: they are what the page is for, and the rest waits behind them.
            try:
                self._running_read = app.SERVICE.boards.running(cwd)
            except Invalid:
                self._running_read = {}
            yield
            await self._load_board()
            yield
            await self._load_workspace_settings()
            # No Sessions here: this arrival reads them when Sessions is where it lands.
            self._load_activity()
            self._load_update()
            self.loading = False
            self._loaded_sid = sid
        elif moved_ws:
            # The last read answered for the workspace just left; a unit of the same name
            # here must not show its session.
            try:
                self._running_read = app.SERVICE.boards.running(cwd)
            except Invalid:
                self._running_read = {}
            yield
            await self._load_board()
            # What Sessions showed was the last workspace's; it is read again only if
            # Sessions is where this arrival lands, just below.
            self.conversations, self.messages, self._history = [], [], []
            self._sessions_cwd = ""
            self._load_activity()
        elif (moved_unit and not unit) or (moved_screen and not moved_unit):
            self._load_update()
        if self.screen == "sessions" and self._sessions_cwd != cwd:
            self._load_sessions()
        if self.screen == "cost":
            # Every arrival here reads the run log once; no other screen does.
            self._load_cost()
        if self.screen == "settings":
            self._load_decisions()
        if self.screen == "agents":
            # Once on entering the page; opening a drawer moves nothing and reads nothing.
            if first or moved_screen:
                self._load_agents()
            if agent not in {d.key for d in self.agent_details}:
                agent = ""
                fixed = dataclasses.replace(fixed, agent="")
        self.agent_key = agent
        self._show_feature(feature, cwd)
        self._read_cwd = cwd
        self.unit_id, self.detail_tab = unit, tab
        self._set_current()
        # A unit is read after whatever the arrival read: a pasted link or a reload at
        # `/unit` would otherwise open a dialog with no timeline or artifact. None of it
        # calls `app.SERVICE.board`.
        if unit and (moved_unit or moved_ws or first):
            self._load_unit(forget=not first)
            self._asked = ""
        self._read_unit = unit
        if idea:
            await self._load_idea(idea)
        if stray:
            self.notice = "That workspace is not on the list."

        # An address the page had to correct is replaced, not added to. Last, and alone: the
        # arrival it causes would cancel anything chained here, so that one chains it instead.
        if place.href(fixed) != place.href(want):
            yield rx.redirect(place.href(fixed), replace=True)
            return
        if unit and self._asked != unit:
            self._ask_joins = True
            yield StudioState.load_next
        # A reload that finds the tab already on the Board: nothing else would start the loops.
        yield StudioState.poll_running
        yield StudioState.watch_board
        yield StudioState.watch_attempts

    @rx.event
    def navigate(self, screen: str):
        if screen not in SCREEN_TITLES:
            self.notice = "That screen does not exist."
            return
        self.mobile_open = False
        self.command_open = False
        return rx.redirect(place.href(place.Place(screen, self._name_of(self.cwd))))

    @rx.event
    def open_feature(self, name: str):
        self.mobile_open = False
        return rx.redirect(
            place.href(place.Place("feature", self._name_of(self.cwd), feature=name))
        )

    def _show_feature(self, name: str, cwd: str) -> None:
        """The framed page's title and address, and whether the feature is off here."""
        page = app.API.state.pages.get(name)
        self.feature = name if page else ""
        self.feature_label = page.label if page else ""
        self.feature_src = f"{page.path}?{urlencode({'cwd': cwd})}" if page and cwd else ""
        self.feature_off = bool(page and cwd) and not app.API.state.ctx.enabled(name, cwd)

    @rx.event
    def choose_workspace(self, path: str):
        if not any(w.id == path for w in self.workspaces):
            self.notice = "That workspace is not on the list."
            return
        # A unit belongs to the workspace it was opened in, so leaving it goes to Board.
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
        """Ask `Board.running` every `RUNNING_POLL` seconds while the Board shows.

        The one source for every card's `live`, whichever tab, route or process started the
        step. One loop per tab: a second start while one lives returns at once. It ends when
        the tab leaves the Board, has no workspace, or has had no socket for `GONE_AFTER`
        asks in a row.
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
                        read = app.SERVICE.boards.running(self.cwd)
                    except Invalid:
                        read = {}
                    self._apply_running(read)
                    self._load_update()
                await asyncio.sleep(RUNNING_POLL)
        finally:
            _POLLING.discard(token)

    @rx.event(background=True)
    async def watch_board(self):
        """Put each board read on the page while the Board shows, and send nothing for a read
        that says what the page already shows.

        It waits on the service's next read (`board(cwd, "next")`), which the app's own changes
        and `gh`'s late answers start, and starts none itself. One loop per tab, ended like
        `poll_running`'s: the tab leaves the Board, has no workspace, or has no socket for
        `GONE_AFTER` waits in a row.
        """
        token = EventContext.get().token
        if token in _WATCHING:
            return
        _WATCHING.add(token)
        missed = 0
        try:
            while True:
                missed = missed + 1 if _tab_gone(token) else 0
                if missed >= GONE_AFTER:
                    return
                async with self:
                    if self.screen != "board" or not self.cwd:
                        return
                    cwd = self.cwd
                try:
                    data = await asyncio.wait_for(app.SERVICE.board(cwd, "next"), BOARD_WAIT)
                except TimeoutError:
                    continue
                except Invalid:
                    await asyncio.sleep(BOARD_WAIT)
                    continue
                seen = _fingerprint(data)
                async with self:
                    # The tab may have moved on while the read ran; the loop asks again.
                    if self.screen != "board" or self.cwd != cwd or seen == self._board_seen:
                        continue
                    self._board_seen = seen
                    self._load_running()
                    self._show_board(data)
        finally:
            _WATCHING.discard(token)

    @rx.event(background=True)
    async def watch_attempts(self):
        """Put the unfinished attempts on the page the moment one moves, while the Board shows.

        The service's attempt events (queued, preparing, a Stop asked, ended, ...) wake it
        through an `asyncio.Event`; it then reads `running_steps` and sends it, with no board
        read behind it. Where `watch_board` waits on a read of the whole board, this waits on
        one attempt. One loop per tab, ended like `watch_board`'s.
        """
        token = EventContext.get().token
        if token in ATTEMPT_WAKES:
            return
        woken = asyncio.Event()
        ATTEMPT_WAKES[token] = (asyncio.get_running_loop(), woken)
        listen_to_attempts(app.SERVICE.bus)
        missed = 0
        # The first pass reads too: a move between the arrival's read and the subscription.
        heard = True
        try:
            while True:
                missed = missed + 1 if _tab_gone(token) else 0
                if missed >= GONE_AFTER:
                    return
                async with self:
                    if self.screen != "board" or not self.cwd:
                        return
                    if heard:
                        # Cleared before the read: a move during it wakes the next pass.
                        woken.clear()
                        self._load_running()
                try:
                    await asyncio.wait_for(woken.wait(), BOARD_WAIT)
                    heard = True
                except TimeoutError:
                    heard = False
        finally:
            ATTEMPT_WAKES.pop(token, None)

    @rx.event
    def search_workspaces(self, value: str):
        self.workspace_query = value

    @rx.event
    def search_work(self, value: str):
        self.query = value

    @rx.event
    def open_needs_you(self):
        """The board, filtered to *Needs you*."""
        self.focus = "Needs you"
        return rx.redirect(place.href(place.Place("board", self._name_of(self.cwd))))

    @rx.event
    def filter_work(self, value: str | list[str]):
        # `rx.segmented_control` may hand back a list when multi-select; this one is not.
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
    def set_density(self, value: str):
        if value not in ("comfortable", "compact"):
            self.notice = "Choose comfortable or compact."
            return
        self.density = value
        self._remember("density", value)

    @rx.event
    def set_agent_chip(self, value: str | list[str]):
        """Narrow the table to one chip, or to every agent with `all`."""
        chip = value if isinstance(value, str) else (value or [""])[0]
        self.agent_chip = "" if chip == "all" else chip

    @rx.event
    def open_agent(self, key: str):
        # `arrive` reads it: the drawer sits over the table, at an address of its own.
        return rx.redirect(place.href(place.Place("agents", self._name_of(self.cwd), agent=key)))

    @rx.event
    def toggle_agent(self, value: bool):
        if not value:
            return rx.redirect(place.href(place.Place("agents", self._name_of(self.cwd))))

    @rx.event
    def save_agent_field(self, form: dict):
        """One box as typed. Whether it may be saved is `Agents.set_agent_field`'s call."""
        key, field = str(form.get("key") or ""), str(form.get("field") or "")
        self._change_agent(key, field, str(form.get("value") or "").strip())

    @rx.event
    def save_agent_identity(self, form: dict):
        """The four identity boxes of one agent; a field left as it was is not sent."""
        key = str(form.get("key") or "")
        row = next((d for d in self.agent_details if d.key == key), None)
        if row is None:
            return
        changed = {
            f: str(form.get(f) or "").strip()
            for f in ("glyph", "name", "meaning", "role")
            if f in form and str(form.get(f) or "").strip() != getattr(row, f)
        }
        if not changed:
            self.notice = "Nothing changed."
            return
        for field, value in changed.items():
            if not self._change_agent(key, field, value):
                return

    @rx.event
    def reset_agent_field(self, key: str, field: str):
        """Remove the override, so the field falls back to its default."""
        self._change_agent(key, field, None)

    def _change_agent(self, key: str, field: str, value: str | None) -> bool:
        """Save or reset one field and read the page again (`_show_agents`). `False` when the
        service refused it, with its reason as the page's error."""
        self.error = ""
        try:
            self._show_agents(app.SERVICE.agents.set_agent_field(key, field, value))
        except Invalid as e:
            self._fail(e)
            return False
        self.notice = "Saved; the next step that starts uses it."
        return True

    @rx.event
    def set_autopilot_on(self, value: bool):
        """Whether it may be turned on is `Autopilot.set_setting`'s call."""
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

    @rx.event
    def edit_impl_allow(self, value: str):
        self.impl_allow_text = value

    @rx.event
    def edit_impl_block(self, value: str):
        self.impl_block_text = value

    @rx.event
    def save_command_lists(self):
        """Whether a name is a command's is `Workspaces.set_command_lists`'s call."""
        try:
            self._show_command_lists(
                app.SERVICE.ws.set_command_lists(
                    self.cwd, self.impl_allow_text.split(), self.impl_block_text.split()
                )
            )
        except Invalid as e:
            self.notice = str(e)
            return
        self.notice = "Saved; the next impl step here runs with these commands."

    def _change_autopilot(self, name: str, value) -> None:
        try:
            self._show_autopilot(app.SERVICE.autopilot.set_setting(self.cwd, name, value))
        except Invalid as e:
            self.notice = str(e)
            self._load_autopilot()
            return
        self.notice = "Saved."

    def _remember(self, key: str, value: str) -> None:
        try:
            app.SERVICE.activity.set_preference(key, value)
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
        # `arrive` reads it (`_load_unit`); the dialog sits over the Board.
        return rx.redirect(place.href(place.Place("unit", self._name_of(self.cwd), unit)))

    @rx.event(background=True)
    async def load_next(self):
        """Ask `coscc.loop next` which stage the run button may offer for the open unit.

        In the background because the answer can wait on `gh` for up to 60s (two calls,
        `board.GATE_TIMEOUT` each), and a handler holding the page's lock that long freezes
        every other control. Runs when a unit is opened, after a step ends, and on *Ask
        again*; never on a timer, and it starts nothing.
        """
        async with self:
            unit, cwd = self.unit_id, self.cwd
            join, self._ask_joins = self._ask_joins, False
            self.run_stage = ""
            self.run_waiting = []
            self.run_dropped = []
            self.run_said = "Asking the loop what comes next…"
            self.rerun_stages, self.rerun_confirming = [], False
            if self._asked != unit:
                # A note written for another unit is not this one's.
                self.rerun_note = ""
        if not (unit and cwd):
            async with self:
                self.run_said = ""
            return
        waiting: list[str] = []
        dropped: list[str] = []
        try:
            stage, said = _run_target(
                found := await _asking(app.SERVICE.steps.next_step, cwd, unit, join)
            )
            waiting = _run_waiting(found)
            dropped = _run_dropped(found)
        except Invalid as e:
            stage, said = "", str(e)
        # Files only, after `next` has answered; a refusal offers nothing.
        try:
            offers = list((await app.SERVICE.steps.rerun_offers(cwd, unit)).get("offers") or [])
        except Invalid:
            offers = []
        later = {str(o.get("stage") or ""): [str(x) for x in o.get("later") or []] for o in offers}
        async with self:
            # A unit opened while this was asking is not the unit this answer is about.
            if self.unit_id == unit and self.cwd == cwd:
                self.run_stage, self.run_said = stage, said
                self.run_waiting = waiting
                self.run_dropped = dropped
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
        # Replaced, so Back from a unit goes to where the unit was opened from.
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
        """Open or close one *Details*; nothing else reads which are open."""
        self.open_details = (
            [k for k in self.open_details if k != key]
            if key in self.open_details
            else [*self.open_details, key]
        )

    @rx.event
    @rx.event
    async def stop_step(self, unit: str):
        """Stop one unit's running step; every rule is `Steps.stop_step`'s. The streaming
        handler sees the `stopped` outcome."""
        self.error = ""
        try:
            done = await app.SERVICE.steps.stop_step(self.cwd, unit, "")
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
            await app.SERVICE.steps.set_mode(self.cwd, self.unit_id, stage, value)
        except Invalid as e:
            self._fail(e)
            return
        await self._load_board()
        self._load_activity()

    @rx.event(background=True)
    async def run_step(self):
        """Run the unit's next step, streaming what comes back.

        `background=True` matters: a generator handler holds the state lock for its whole
        life, so a step that takes minutes would lock every other control. A background
        handler takes the lock in short bursts, so every write below sits in `async with self`.
        """
        async with self:
            # No "already running" check of the page's own: whether this unit may start a
            # step is the service's to refuse, and its `Invalid` lands in `_fail`.
            unit, stage, cwd = self.unit_id, self.next_stage, self.cwd
            if not (unit and stage and cwd):
                self.notice = "There is no next step to run."
                return
            self.run_log = ""
            self.log_unit = unit
            self.error = ""

        listed = False
        try:  # noqa: PLR1702 - still to split
            async for kind, payload in app.SERVICE.steps.run_step(cwd, unit, stage):
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
                        self.notice = f"{stage} {outcome}" + (
                            f" — wrote {written}" if written else ""
                        )
                        # `describe_base` is the one sentence saying a step's base may be
                        # stale; the page only appends it.
                        stale = describe_base(payload.get("base"))
                        if stale:
                            self.notice += " " + stale
        except Invalid as e:
            async with self:
                self._fail(e)
        finally:
            # Re-read rather than patch: the artifact on disk is the truth about a stage's status.
            async with self:
                await self._load_board()
                self._load_timeline()
                self._load_artifact()
                self._load_activity()
        # The stage that ran is behind the unit now; ask again what is next. This names a
        # stage and runs nothing.
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
        """A session exists once something has been said in it, so this only clears the view;
        the SDK owns session identity."""
        if not self.cwd:
            self.notice = "Choose a workspace first."
            return
        self.session_id = ""
        self.messages, self._history = [], []
        self.prompt = ""
        self.notice = "New conversation. It is saved once you send the first message."

    @rx.event
    def open_message(self, index: int):
        """Show one cut message whole, from the copy `_load_history` kept.

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
        self.messages = [
            *self.messages,
            Message(role="user", text=text),
            Message(role="assistant", text=""),
        ]
        yield
        try:
            app.SERVICE.chat.check_send(self.cwd, text)
            async for kind, payload in app.SERVICE.chat.stream(
                self.cwd, text, self.session_id or None
            ):
                if kind == "chunk":
                    self.messages[-1].text += payload
                    self.messages = list(self.messages)
                    yield
                elif kind == "done" and isinstance(payload, dict):
                    # The SDK resolves the id; take it from here rather than inventing one.
                    self.session_id = payload.get("session_id") or self.session_id
        except Exception as e:
            log.exception("the chat turn failed")
            self._fail(e)
        finally:
            self.sending = False
        yield
        shown = list(self.get_value("messages"))
        self._load_sessions()
        self._keep_whole(shown)
        self._load_activity()
