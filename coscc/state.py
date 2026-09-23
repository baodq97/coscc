"""Everything the page shows, and nothing it decides.

`spec.md` R10 is the rule this module answers to: the page and the JSON API are two
entry points to one capability, so no handler here validates anything, builds a path, or
decides what counts as a workspace. Each one calls `Service`, turns `Invalid` into a line
of text, and stops. A conditional about business state in this file is a bug in
`service.py`.

The dataclasses below exist because Reflex needs a declared shape to render a list against,
and `Service` returns dictionaries. They are a *view*: every field is something the screen
draws. Nothing is computed here that the service could have answered — the one exception is
`_lane`, which is named and explained where it sits.

The service instance is the same object the FastAPI app holds. Two instances would mean two
`Sessions` registries, and knob 4 ("resume only what this app created") would answer
differently depending on which door you came through.
"""

from __future__ import annotations

import dataclasses

import reflex as rx

from coscc.api import build
from coscc.journal import COST_USD, TOKEN_FIELDS
from coscc.service import Invalid

API = build()
SERVICE = API.state.service

NAVIGATION = (
    ("overview", "Overview", "house"),
    ("workspaces", "Workspaces", "layers"),
    ("board", "Board", "columns-3"),
    ("sessions", "Sessions", "messages-square"),
    ("activity", "Activity & usage", "chart-no-axes-combined"),
    ("settings", "Settings", "settings-2"),
)

SCREEN_TITLES = {key: label for key, label, _ in NAVIGATION}

# How a status reads at a glance. `not started` is deliberately the quietest: most cells on
# most boards are it, and a board where everything shouts says nothing.
STATUS_COLOR = {
    "accepted": "grass",
    "done": "iris",
    "draft": "amber",
    "skipped": "gray",
    "rejected": "red",
    # A reviewer looked and asked for changes: waiting on someone, like a draft, but not
    # the same colour, because it is not unfinished writing — it is a verdict.
    "changes-requested": "orange",
    "not started": "gray",
}

LANE_COLOR = {
    "Planned": "gray",
    "In progress": "iris",
    "Needs review": "amber",
    "Complete": "grass",
}

# Colours for the workspace marks, assigned by position so the same workspace keeps the
# same colour between loads. Nothing is stored; the list is the only state.
MARK_COLORS = ("iris", "grass", "blue", "amber", "plum", "cyan")


def tree_line(tree: dict | None) -> str:
    """`0017` R6. A unit's worktree and its preparation, as the page says it.

    A failure names the command and its exit code, never just *failed*: the person has to
    be able to run that command themselves.
    """
    if not tree or not tree.get("path"):
        return ""
    line = f"Worktree: {tree['path']}"
    if tree.get("branch"):
        line += f" on {tree['branch']}"
    prepared = tree.get("prepare")
    if prepared is None:
        line += " · not prepared yet"
    elif prepared.get("ok"):
        line += " · prepared"
    else:
        line += (f" · preparing failed: `{prepared.get('command')}` exited "
                 f"{prepared.get('exit_code')}")
    return line


# --- the view shapes ---------------------------------------------------------


@dataclasses.dataclass
class Workspace:
    id: str = ""  # the resolved path; env workspaces have no name to key on
    name: str = ""
    path: str = ""
    label: str = ""
    source: str = ""
    missing: bool = False
    removable: bool = False
    initials: str = ""
    color: str = "iris"


@dataclasses.dataclass
class Cell:
    """One stage of one unit, as the board shows it."""

    stage: str = ""
    status: str = ""
    mode: str = "manual"
    color: str = "gray"
    started: bool = False
    grants: str = ""
    warning: str = ""
    opens_tools: bool = False
    # `idea` is the one optional stage and it gates nothing. Carried here so the run
    # button can skip it: the board card says "Next: write-pr" and a button offering to
    # run `idea` beside it is two answers to one question.
    optional: bool = False


@dataclasses.dataclass
class Question:
    """`0016`. One numbered item under an artifact's `## Open questions`, as `cos.mjs`
    read it. Nothing here parses an artifact; every field is copied from `status --json`."""

    # `<artifact>#<n>`: one string the page can bind a text box to.
    key: str = ""
    artifact: str = ""
    number: int = 0
    text: str = ""
    answered: bool = False
    # Whether this is the artifact the unit's open count is taken from.
    counted: bool = False


@dataclasses.dataclass
class Round:
    """`0021`. One round of `review.md` and whether it is on the pull request as a comment.
    The round is `cos.mjs`'s; whether it is posted is `Service.board`'s reading of the run
    log. Nothing here decides either."""

    number: int = 0
    verdict: str = ""
    posted: bool = False
    url: str = ""
    # Why the latest attempt failed, or empty when none has been made.
    reason: str = ""


@dataclasses.dataclass
class Unit:
    id: str = ""
    title: str = ""
    summary: str = ""
    lane: str = ""
    stage: str = ""
    color: str = "gray"
    owner: str = "You"
    mode: str = "manual"
    tokens: str = ""
    usd: str = ""
    token_count: int = 0
    progress: int = 0
    # True when this unit sits in *Needs review*: an artifact is in draft, or the harness
    # reported a problem with the directory. Not the harness's `blocked` — see `_lane`.
    needs_attention: bool = False
    problems: str = ""
    cells: list[Cell] = dataclasses.field(default_factory=list)
    # `0016` R8. How many questions in the counted artifact nobody has answered, taken
    # from `cos.mjs` (`open`) and never recounted (R7). Shown as a badge, not a lane:
    # an open question does not move a unit into *Needs review*.
    open_questions: int = 0
    questions: list[Question] = dataclasses.field(default_factory=list)
    # `0021`. The pull request `pr.md` names, and every review round with its comment state.
    pr_url: str = ""
    rounds: list[Round] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class Message:
    role: str = ""
    text: str = ""


@dataclasses.dataclass
class Conversation:
    id: str = ""
    title: str = ""
    subtitle: str = ""
    resumable: bool = False


@dataclasses.dataclass
class Run:
    """One row of a unit's timeline."""

    stage: str = ""
    mode: str = ""
    started: str = ""
    ended: str = ""
    outcome: str = ""
    session_id: str = ""
    tokens: str = ""
    usd: str = ""
    color: str = "gray"
    # Why it ended this way, empty when it ended well. A run that says `failed` and nothing
    # else sends the only person who can fix it to the database -- and for a prose stage
    # this is where the reply it was paid for comes back (`coscc/runner.py`, `_with_reply`).
    detail: str = ""


@dataclasses.dataclass
class Event:
    title: str = ""
    detail: str = ""
    icon: str = "circle-check"
    color: str = "iris"
    time: str = ""


@dataclasses.dataclass
class Knob:
    name: str = ""
    value: str = ""
    detail: str = ""
    on: bool = False


@dataclasses.dataclass
class GrantRow:
    stage: str = ""
    mode: str = ""
    tools: str = ""
    commands: str = ""
    turns: str = ""
    budget: str = ""
    warning: str = ""


# --- formatting --------------------------------------------------------------


def _run_target(data: dict) -> tuple[str, str]:
    """`0024`. The stage the run button offers and the sentence beside it, copied from
    `Service.next_step` -- which is `cos.mjs next`'s answer.

    Until `0024` the page worked this out for itself: "the first required stage with no
    artifact". That was a second copy of the loop `.claude/CLAUDE.md` forbids, and once a
    review asked for changes both `impl.md` and `review.md` existed, so it offered `ship`,
    whose gate was closed, and nothing else. Nothing here reads `action` to pick a stage.
    """
    return str(data.get("stage") or ""), str(data.get("action") or "")


def _tokens(cost: dict) -> tuple[int, str]:
    """Every token the turn was billed for, as one number.

    Cache reads and writes are included because they are billed. Showing only input plus
    output would report a cache-heavy session as nearly free, which is the opposite of
    what a cost display is for.
    """
    total = sum(int(cost.get(name) or 0) for name in TOKEN_FIELDS)
    return total, (f"{total:,}" if total else "—")


def _usd(cost: dict) -> str:
    usd = float(cost.get(COST_USD) or 0.0)
    if not usd:
        return "—"
    # Under a cent still has to read as a number, not as $0.00.
    return f"${usd:.4f}" if usd < 0.01 else f"${usd:.2f}"


def _title_of(unit_name: str) -> str:
    """`0006_demo-data-and-no-durable-store` reads as `Demo data and no durable store`.

    The slug is the only human-written name a unit has before its artifacts are read, and
    reading eight files per card to find a better one would make opening the board cost a
    directory walk. The harness fixes the slug at creation for exactly this reason.
    """
    _, _, slug = unit_name.partition("_")
    words = (slug or unit_name).replace("-", " ").strip()
    return words[:1].upper() + words[1:] if words else unit_name


def _initials(name: str) -> str:
    letters = [part[0] for part in name.replace("_", "-").replace(".", "-").split("-") if part]
    return ("".join(letters[:2]) or name[:2] or "WS").upper()


def _questions(unit: dict) -> tuple[int, list[Question]]:
    """`0016` R7. The open count and the questions of one board unit, copied from what
    `cos.mjs` sent through `coscc/board.py`. Nothing is counted here: `open` is taken as
    sent, so the page and `status --json` cannot disagree."""
    return int(unit.get("open") or 0), [
        Question(
            key=f"{q['artifact']}#{q['n']}",
            artifact=str(q["artifact"]),
            number=int(q["n"]),
            text=str(q.get("text") or ""),
            answered=bool(q.get("answered")),
            counted=bool(q.get("counted")),
        )
        for q in unit.get("questions") or []
    ]


def _rounds(unit: dict) -> list[Round]:
    """`0021`. Each review round as `Service.board` sent it, with its comment state."""
    out = []
    for r in unit.get("rounds") or []:
        c = r.get("comment") or {}
        out.append(
            Round(
                number=int(r.get("n") or 0),
                verdict=str(r.get("verdict") or "unreadable"),
                posted=bool(c.get("posted")),
                url=str(c.get("url") or ""),
                reason=str(c.get("reason") or ""),
            )
        )
    return out


def _lane(unit: dict) -> str:
    """Which column a unit sits in.

    The only derivation in this module, and it is presentation. Nothing downstream reads
    it back, so it cannot drift into a second answer about progress.

    **It deliberately does not use the harness's `blocked`.** Measured on 2026-09-22 by
    reading `cos.mjs status --json` against this repository: `blocked` is `true` for every
    unit that is not finished — `.claude/scripts/cos.mjs:122-135` returns it for "the next
    stage has not been written yet" as readily as for "an artifact is sitting in draft".
    Mapping it onto a lane called *Needs review* put all six unfinished units there and
    left *Planned* and *In progress* permanently empty, which is a board that sorts nothing.

    So the lanes are read off the artifacts instead: a `draft` artifact is something a
    person wrote and has not accepted, and that is the thing worth surfacing.
    """
    action = str(unit.get("next") or "")
    rows = unit.get("stages") or []
    # First, before every other rule. A unit started from this page holds only an
    # `idea.md`, and that idea carries `Status: accepted` (`coscc/units.py` writes it so),
    # so the *In progress* rule below would otherwise claim it the moment its `problems`
    # went empty. `cos.mjs` decides what pre-intent means; this only places it.
    if unit.get("phase") == "pre-intent":
        return "Planned"
    if action == "finished" or action.startswith("closed"):
        return "Complete"
    # `changes-requested` sits with `draft`: the review found something and the unit waits
    # on a fix, which is the kind of thing this lane exists to surface (`0015`).
    if unit.get("problems") or any(
        r.get("status") in ("draft", "changes-requested") for r in rows
    ):
        return "Needs review"
    if any(r.get("status") != "not started" for r in rows):
        return "In progress"
    return "Planned"


def _current_stage(unit: dict, stages: list[str]) -> str:
    """The stage a person would say the unit is at: the last one with an artifact."""
    started = [r["stage"] for r in unit.get("stages") or [] if r.get("status") != "not started"]
    return started[-1] if started else (stages[0] if stages else "")


class StudioState(rx.State):
    """The whole page. No business state lives here — it is all read back from `Service`."""

    screen: str = "overview"
    loading: bool = False
    busy: bool = False
    error: str = ""
    notice: str = ""

    # -- workspaces
    workspaces: list[Workspace] = []
    cwd: str = ""
    working_dir: str = ""
    workspace_query: str = ""

    new_name: str = ""
    new_label: str = ""
    new_url: str = ""
    form_error: str = ""
    workspace_form: bool = False
    editing: str = ""
    remove_name: str = ""

    # -- board
    stages: list[str] = []
    units: list[Unit] = []
    board_note: str = ""
    # Set only when the board is empty (`0001_product-describes-a-state-it-is-not-in` R6):
    # the store the board read, the host repository, and how many units the host's own
    # `.cos/` holds that the board does not list.
    empty_store: str = ""
    empty_host: str = ""
    empty_host_units: int = 0
    recording: bool = False
    query: str = ""
    focus: str = "All work"
    board_view: str = "Board"
    density: str = "comfortable"

    # -- starting a unit (`0014` R8)
    new_slug: str = ""
    new_brief: str = ""
    starting: bool = False
    branch: str = ""
    # `0017`. Per unit: where its worktree is and what preparing it said, as one line.
    trees: dict[str, str] = {}

    @rx.var
    def unit_tree(self) -> str:
        return self.trees.get(self.unit_id, "")

    # -- one unit
    unit_id: str = ""
    detail_tab: str = "overview"
    artifact: str = ""
    artifact_file: str = ""
    artifact_missing: bool = False
    runs: list[Run] = []
    # "<unit>/<stage>" while a step is running, empty otherwise. One at a time: two steps
    # writing into one unit would race on the same files.
    running: str = ""
    run_log: str = ""
    # `0024`. The stage `cos.mjs next` names for the open unit, and what it said. Set only
    # by `load_next`, from `_run_target`; `next_stage` reads it and nothing computes it.
    run_stage: str = ""
    run_said: str = ""

    # -- answering a question (`0016`). One text box is live at a time: typing into a
    # question's box makes it the target, and the box of every other question reads empty.
    # `answer_by` stays in the page's state and is not stored as a preference, so the
    # settings store gains no key for a name nobody verified.
    answer_target: str = ""
    answer_text: str = ""
    answer_by: str = ""
    answering: bool = False
    # `0021`. The round being posted, 0 while none is.
    posting_round: int = 0

    # -- sessions
    conversations: list[Conversation] = []
    session_id: str = ""
    messages: list[Message] = []
    prompt: str = ""
    sending: bool = False

    # -- activity and settings
    events: list[Event] = []
    usage_total_tokens: str = "—"
    usage_total_usd: str = "—"
    knobs: list[Knob] = []
    grants: list[GrantRow] = []
    data_dir: str = ""
    host_port: str = ""
    # Whether the bound address reaches this machine only. Since `0011` the default
    # is `0.0.0.0`, so the page may no longer state "loopback" as a fact -- it has to
    # read it. A page that claims a safety property it does not have is worse than a
    # page that says nothing, and `0007` exists because of exactly that.
    loopback_only: bool = True
    model: str = ""

    # -- chrome
    mobile_open: bool = False
    command_open: bool = False
    command_query: str = ""

    # -- computed ------------------------------------------------------------

    @rx.var
    def screen_title(self) -> str:
        return SCREEN_TITLES.get(self.screen, "Overview")

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

    @rx.var
    def visible_units(self) -> list[Unit]:
        q = self.query.strip().lower()
        rows = self.units
        if q:
            rows = [u for u in rows if q in u.id.lower() or q in u.title.lower()]
        if self.focus == "Autonomous":
            rows = [u for u in rows if u.mode == "autonomous"]
        elif self.focus == "Needs review":
            rows = [u for u in rows if u.needs_attention]
        return rows

    @rx.var
    def planned(self) -> list[Unit]:
        return [u for u in self.visible_units if u.lane == "Planned"]

    @rx.var
    def in_progress(self) -> list[Unit]:
        return [u for u in self.visible_units if u.lane == "In progress"]

    @rx.var
    def needs_review(self) -> list[Unit]:
        return [u for u in self.visible_units if u.lane == "Needs review"]

    @rx.var
    def complete(self) -> list[Unit]:
        return [u for u in self.visible_units if u.lane == "Complete"]

    @rx.var
    def active_count(self) -> int:
        return len([u for u in self.units if u.lane == "In progress"])

    @rx.var
    def attention_count(self) -> int:
        return len([u for u in self.units if u.needs_attention])

    @rx.var
    def current_unit(self) -> Unit:
        for u in self.units:
            if u.id == self.unit_id:
                return u
        return Unit()

    @rx.var
    def open_questions_here(self) -> list[Question]:
        """`0016`. The open unit's unanswered questions, counted artifact's first."""
        waiting = [q for q in self.current_unit.questions if not q.answered]
        return sorted(waiting, key=lambda q: not q.counted)

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
    def is_running(self) -> bool:
        return self.running != ""

    @rx.var
    def running_here(self) -> bool:
        return self.running.startswith(self.unit_id + "/") and self.unit_id != ""

    @rx.var
    def command_units(self) -> list[Unit]:
        q = self.command_query.strip().lower()
        if not q:
            return self.units[:5]
        return [u for u in self.units if q in u.title.lower() or q in u.id.lower()][:6]

    @rx.var
    def usage_rows(self) -> list[Unit]:
        return [u for u in self.units if u.token_count > 0]

    @rx.var
    def usage_scale(self) -> int:
        """The bar scale, taken from the largest real value rather than from a guess."""
        return max([u.token_count for u in self.units] + [1])

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
        self.model = data.get("model") or "default"
        self.knobs = [
            Knob(name=k["name"], value=k["value"], detail=k["detail"], on=bool(k["on"]))
            for k in data.get("knobs") or []
        ]
        self.grants = [
            GrantRow(
                stage=g["stage"],
                mode=g["mode"],
                tools=g["tools"],
                commands=g["commands"],
                turns=str(g["max_turns"]),
                budget=f"${g['max_budget_usd']:.2f}",
                warning=g["warning"],
            )
            for g in data.get("grants") or []
        ]

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

    async def _load_board(self) -> None:
        self.units, self.stages, self.board_note = [], [], ""
        self.empty_store, self.empty_host, self.empty_host_units = "", "", 0
        self.branch = ""
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
        self.trees = {
            u["name"]: tree_line(u.get("worktree")) for u in data.get("units") or []
        }
        self.recording = bool(data["recording"])
        self.board_note = data.get("read_only_because") or data.get("empty_because") or ""
        empty = data.get("empty") or {}
        self.empty_store = str(empty.get("store") or "")
        self.empty_host = str(empty.get("host") or "")
        self.empty_host_units = int(empty.get("host_units") or 0)
        if self.empty_host_units > 0:
            # `empty_because` speaks of the store's `.cos/`, and next to a host `.cos/`
            # that is full it reads as a claim about the wrong directory — the fault this
            # unit exists for. Only the read-only reason survives, appended by the page.
            self.board_note = data.get("read_only_because") or ""

        units: list[Unit] = []
        for u in data["units"]:
            cells = [
                Cell(
                    stage=row["stage"],
                    status=row["status"],
                    mode=row["mode"],
                    color=STATUS_COLOR.get(row["status"], "gray"),
                    started=row["status"] != "not started",
                    optional=bool(row.get("optional")),
                    grants=", ".join(row.get("grants") or []) or "no tools",
                    warning=row.get("warning") or "",
                    opens_tools=bool(row.get("grants")),
                )
                for row in u["stages"]
            ]
            started = len([c for c in cells if c.started])
            lane = _lane(u)
            stage = _current_stage(u, self.stages)
            count, shown = _tokens(u.get("cost") or {})
            # The file-only stage `cos.mjs` names, not the one the run button asks for: that
            # one can cost two `gh` calls, and this runs for every card (`0024` plan, Risk 7).
            nxt = next((c for c in cells if c.stage == (u.get("next_stage") or "")), None)
            mode = nxt.mode if nxt is not None else "manual"
            waiting, asked = _questions(u)
            units.append(
                Unit(
                    id=u["name"],
                    title=_title_of(u["name"]),
                    summary=u.get("next") or "",
                    lane=lane,
                    stage=stage,
                    color=LANE_COLOR.get(lane, "gray"),
                    owner="AI" if mode == "autonomous" else "You",
                    mode=mode,
                    tokens=shown,
                    usd=_usd(u.get("cost") or {}),
                    token_count=count,
                    progress=int(started * 100 / len(cells)) if cells else 0,
                    needs_attention=lane == "Needs review",
                    problems="; ".join(u.get("problems") or []),
                    cells=cells,
                    open_questions=waiting,
                    questions=asked,
                    pr_url=str((u.get("pr") or {}).get("url") or ""),
                    rounds=_rounds(u),
                )
            )
        self.units = units

    def _load_sessions(self) -> None:
        self.conversations, self.messages = [], []
        if not self.cwd:
            self.session_id = ""
            return
        try:
            data = SERVICE.sessions_for(self.cwd, limit=40)
        except Invalid as e:
            self._fail(e)
            return
        rows = [
            Conversation(
                id=row["session_id"],
                title=(row.get("summary") or row.get("session_id") or "")[:60] or "Untitled",
                subtitle=str(row.get("last_modified") or row.get("created_at") or "")[:19],
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
            self.messages = []
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
        self.messages = rows

    def _load_activity(self) -> None:
        self.events = []
        self.usage_total_tokens, self.usage_total_usd = "—", "—"
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
            }.get(row["kind"], row["kind"])
            detail = f"{row['unit']}"
            if row["denials"]:
                detail += f" / {row['denials']} tool call(s) refused"
            if row["artifact"]:
                detail += f" / wrote {row['artifact']}"
            events.append(Event(title=title, detail=detail, icon=icon, color=color, time=row["at"]))
        self.events = events
        total = feed.get("total") or {}
        _, shown = _tokens(total)
        self.usage_total_tokens = shown
        self.usage_total_usd = _usd(total)

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
                started=r.get("started") or "",
                ended=r.get("ended") or "(still running)",
                outcome=r.get("outcome") or "—",
                session_id=(r.get("session_id") or "—")[:12],
                tokens=_tokens(r.get("cost") or {})[1],
                usd=_usd(r.get("cost") or {}),
                color="grass" if r.get("outcome") == "done" else "amber",
                detail=r.get("detail") or "",
            )
            for r in data["runs"]
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

    @rx.event
    async def load(self):
        self.loading, self.error = True, ""
        yield
        try:
            self._load_settings()
            prefs = SERVICE.preferences()
            self.density = str(prefs.get("density") or "comfortable")
            self.board_view = str(prefs.get("board_view") or "Board")
            self._load_workspaces()
        except Invalid as e:
            self._fail(e)
        yield
        await self._load_board()
        self._load_sessions()
        self._load_activity()
        self.loading = False

    @rx.event
    def navigate(self, screen: str):
        if screen not in SCREEN_TITLES:
            self.notice = "That screen does not exist."
            return
        self.screen = screen
        self.mobile_open = False
        self.command_open = False

    @rx.event
    async def choose_workspace(self, path: str):
        if not any(w.id == path for w in self.workspaces):
            self.notice = "That workspace is not on the list."
            return
        self.cwd = path
        self.unit_id, self.session_id = "", ""
        self.query, self.error = "", ""
        yield
        await self._load_board()
        self._load_sessions()
        self._load_activity()

    @rx.event
    async def open_workspace(self, path: str):
        async for _ in self.choose_workspace(path):
            yield
        self.screen = "board"

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
        if not isinstance(value, str) or value not in ("All work", "Autonomous", "Needs review"):
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

    # -- managing workspaces -------------------------------------------------

    @rx.event
    def edit_workspace(self, name: str):
        self.editing = name
        self.form_error = ""
        self.new_name = name
        self.new_url = ""
        self.new_label = next((w.label for w in self.workspaces if w.name == name), "")
        self.workspace_form = True

    @rx.event
    def toggle_workspace_form(self, value: bool):
        self.workspace_form = value
        if not value:
            self.form_error = ""

    @rx.event
    def set_new_name(self, value: str):
        self.new_name = value

    @rx.event
    def set_new_label(self, value: str):
        self.new_label = value

    @rx.event
    def set_new_url(self, value: str):
        self.new_url = value

    @rx.event
    async def save_workspace(self):
        """Adopt, clone, or relabel. Which one is `Service`'s decision, not this file's."""
        self.busy, self.form_error = True, ""
        yield
        try:
            if self.editing:
                SERVICE.set_label(self.editing, self.new_label)
            else:
                await SERVICE.add_workspace(
                    self.new_name, label=self.new_label, repo_url=self.new_url or None
                )
            self._load_workspaces()
            self.workspace_form = False
            self.new_name = self.new_url = self.new_label = ""
            self.editing = ""
        except Invalid as e:
            self.form_error = str(e)
        finally:
            self.busy = False
        yield
        await self._load_board()

    @rx.event
    def request_remove(self, name: str):
        self.remove_name = name

    @rx.event
    def toggle_remove(self, value: bool):
        if not value:
            self.remove_name = ""

    @rx.event
    async def remove_workspace(self):
        """De-lists only. The directory stays on disk — `spec.md` R18."""
        name, self.remove_name = self.remove_name, ""
        if not name:
            return
        try:
            SERVICE.remove_workspace(name)
            self._load_workspaces()
            self.notice = f"{name} is off the list. Its folder is untouched."
        except Invalid as e:
            self._fail(e)
        yield
        await self._load_board()

    @rx.event
    async def pull(self, name: str):
        self.busy, self.error = True, ""
        yield
        try:
            await SERVICE.pull_workspace(name)
            self.notice = f"{name} is up to date."
        except Invalid as e:
            # A failed fast-forward is normal and must be visible, not swallowed.
            self._fail(e)
        finally:
            self.busy = False

    # -- one unit ------------------------------------------------------------

    @rx.event
    def open_unit(self, unit: str):
        self.unit_id = unit
        self.detail_tab = "overview"
        self.run_log = ""
        self.error = ""
        self._load_timeline()
        self._load_artifact()
        return StudioState.load_next

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
            self.run_stage = ""
            self.run_said = "Asking cos.mjs what comes next…"
        if not (unit and cwd):
            async with self:
                self.run_said = ""
            return
        try:
            stage, said = _run_target(await SERVICE.next_step(cwd, unit))
        except Invalid as e:
            stage, said = "", str(e)
        async with self:
            # A unit opened while this was asking is not the unit this answer is about.
            if self.unit_id == unit and self.cwd == cwd:
                self.run_stage, self.run_said = stage, said

    @rx.event
    def toggle_detail(self, value: bool):
        if not value:
            self.unit_id = ""

    @rx.event
    def set_detail_tab(self, value: str):
        if value not in ("overview", "artifacts", "questions", "comments", "timeline"):
            self.notice = "That tab does not exist."
            return
        self.detail_tab = value

    @rx.event
    def edit_answer(self, key: str, value: str):
        """Typing into one question's box makes it the one being answered."""
        if key != self.answer_target:
            self.answer_target = key
        self.answer_text = value

    @rx.event
    def set_answer_by(self, value: str):
        self.answer_by = value

    @rx.event
    async def answer_question(self, key: str):
        """`0016` R2. Send one answer. Every rule about whether it may be written is
        `Service.answer`'s; a refusal arrives here as its words and is shown as they are."""
        if key != self.answer_target or not self.answer_text.strip():
            self.notice = "Write the answer in that question's box first."
            return
        artifact, _, number = key.rpartition("#")
        self.answering = True
        try:
            done = await SERVICE.answer(
                self.cwd, self.unit_id, artifact, number, self.answer_text, self.answer_by
            )
        except Invalid as e:
            self.notice = str(e)
            return
        finally:
            self.answering = False
        self.answer_target, self.answer_text = "", ""
        self.notice = (
            f"Answered question {done['question']} of {done['artifact']} as "
            f"{done['answered_by']}. Nothing was started; the next step reads it when it runs."
        )
        await self._load_board()
        self._load_artifact()

    @rx.event
    async def post_review_comment(self, number: int):
        """`0021` R8. Post one review round to the pull request. Whether it may, and whether
        it is already there, is `Service.post_review_comment`'s decision.

        The `yield` after raising `posting_round` is what sends it to the browser: without
        it the flag is set and cleared inside one delta, and the button never locks for the
        up to 60s `gh` may take (`0021` review, F1)."""
        if self.posting_round != 0:
            return
        self.posting_round = int(number)
        yield
        try:
            done = await SERVICE.post_review_comment(self.cwd, self.unit_id, number)
        except Invalid as e:
            self.notice = str(e)
            return
        finally:
            self.posting_round = 0
        if done["state"] == "failed":
            self.notice = f"Round {done['round']} is not on the PR: {done['reason']}"
        elif done["state"] == "already":
            self.notice = f"Round {done['round']} was already on the PR. Nothing was posted."
        else:
            self.notice = (
                f"Posted round {done['round']} to the PR as a comment. It is not an approval "
                "and no gate reads it."
            )
        await self._load_board()

    @rx.event
    def set_new_slug(self, value: str):
        self.new_slug = value

    @rx.event
    def set_new_brief(self, value: str):
        self.new_brief = value

    @rx.event
    async def create_unit(self):
        """`0014` R8. Start a work unit from the page.

        The slug grammar and the number are not decided here and not decided in
        `service.py` either — they come back from `cos.mjs`, and a bad slug arrives as its
        refusal, word for word. A handler that decided anything would be a bug in
        `service.py` (`.cos/0001_.../spec.md` R10).
        """
        slug = self.new_slug.strip()
        if not slug:
            self.notice = "Give the work a short name, like `board-cannot-say-what-happened`."
            return
        if not self.new_brief.strip():
            # Not a validation rule of the loop — a rule of this page. The brief becomes
            # `idea.md`, which is the only thing the intent step will have to work from,
            # and a unit started without one wastes a paid step on an empty prompt.
            self.notice = "Say what the problem is, in your own words. The intent step reads it."
            return
        self.starting = True
        try:
            made = await SERVICE.create_unit(self.cwd, slug, self.new_brief)
        except Invalid as e:
            self.notice = str(e)
            return
        finally:
            self.starting = False
        self.new_slug, self.new_brief = "", ""
        self.notice = f"Started {made['unit']}."
        await self._load_board()

    @rx.event
    async def start_branch(self):
        """`0014` R8. Cut the open unit's branch in the workspace.

        The one control on this page that writes to somebody else's git.
        `coscc/gitops.py` carries the list of what that may be.
        """
        if not self.unit_id:
            return
        try:
            cut = await SERVICE.start_branch(self.cwd, self.unit_id)
        except Invalid as e:
            self.notice = str(e)
            return
        # R3 of `0001_product-describes-a-state-it-is-not-in`: say where it was cut from,
        # from the fetched ref rather than the local `main`. Since `0017` it is cut in the
        # unit's own worktree, so the workspace's branch does not change and is not reset.
        said = f"{cut['branch']} cut from {cut['base']} at {cut['sha']}, in {cut['worktree']}."
        if cut.get("switched"):
            said += " The workspace was moved back to main to open it."
        prepared = cut.get("prepare") or {}
        if not prepared.get("ok", True):
            said += (f" Preparing it failed: `{prepared.get('command')}` exited "
                     f"{prepared.get('exit_code')} — impl will not run until it succeeds.")
        self.notice = said
        self.trees = {**self.trees, self.unit_id: tree_line(
            {"path": cut["worktree"], "branch": cut["branch"], "prepare": prepared})}

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
            if self.running:
                self.notice = "A step is already running."
                return
            unit, stage, cwd = self.unit_id, self.next_stage, self.cwd
            if not (unit and stage and cwd):
                self.notice = "There is no next step to run."
                return
            self.running = f"{unit}/{stage}"
            self.run_log = ""
            self.error = ""

        try:
            async for kind, payload in SERVICE.run_step(cwd, unit, stage):
                async with self:
                    if kind == "chunk":
                        self.run_log += payload
                    elif kind == "done" and isinstance(payload, dict):
                        if payload.get("error"):
                            self.error = payload["error"]
                        outcome = payload.get("outcome") or ""
                        written = payload.get("artifact") or ""
                        self.notice = (
                            f"{stage} {outcome}" + (f" — wrote {written}" if written else "")
                        )
        except Invalid as e:
            async with self:
                self._fail(e)
        finally:
            # Re-read rather than patch. The artifact on disk is the truth about a stage's
            # status, and this is the moment it changed.
            async with self:
                self.running = ""
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
        self.messages = []
        self.prompt = ""
        self.notice = "New conversation. It is saved once you send the first message."

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
        self._load_sessions()
        self._load_activity()
