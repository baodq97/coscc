"""The page, built from Python components.

`spec.md` R8: no hand-written HTML or CSS serves this app. Everything below is Python, and
`cos_baodo/public/index.html` — 151 lines of it in `0002` — is gone.

`spec.md` R10 is the rule that matters more. Every handler here calls `Service` and does
nothing else: no validation, no path building, no decision about what counts as a
workspace. The proof command drives the same `Service` through `/api/*`, so the two entry
points cannot disagree about behaviour — only about presentation.

The service instance is deliberately the *same object* the FastAPI app holds. Two
instances would mean two `Sessions` registries, and knob 4 ("resume only what this app
created") would start answering differently depending on which door you came through.
"""

from __future__ import annotations

import dataclasses

import reflex as rx

from cos_baodo import ui
from cos_baodo.api import build
from cos_baodo.service import Invalid

# How a status reads at a glance. `not started` is deliberately the quietest: most cells on
# most boards are it, and a board where everything shouts says nothing.
STATUS_COLOR = {
    "accepted": "grass",
    "done": "iris",
    "draft": "amber",
    "skipped": "gray",
    "rejected": "red",
    "not started": "gray",
}


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


@dataclasses.dataclass
class UnitRow:
    name: str = ""
    next: str = ""
    blocked: bool = False
    tokens: str = ""
    usd: str = ""
    cells: list[Cell] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class Run:
    """One row of a unit's timeline (`spec.md` R15)."""

    stage: str = ""
    mode: str = ""
    started: str = ""
    ended: str = ""
    outcome: str = ""
    session_id: str = ""
    tokens: str = ""
    usd: str = ""

_api = build()
_service = _api.state.service


class State(rx.State):
    """Everything the page shows. No business state lives here — it is all read back."""

    rows: list[dict[str, str]] = []
    count: int = 0
    working_dir: str = ""
    error: str = ""
    busy: bool = False

    new_name: str = ""
    new_url: str = ""
    new_label: str = ""

    cwd: str = ""
    prompt: str = ""
    reply: str = ""

    # The board. `stages` comes from the backend rather than being written here, because
    # the eight stages are defined once, in `.claude/scripts/cos.mjs`.
    stages: list[str] = []
    units: list[UnitRow] = []
    board_note: str = ""
    recording: bool = False

    picked: str = ""
    runs: list[Run] = []

    # "<unit>/<stage>" while a step is running, empty otherwise. One at a time: two steps
    # writing into one unit would race on the same files.
    running: str = ""
    run_log: str = ""

    # Explicit setters. Reflex 0.9 dropped the implicit `set_<var>` handlers, and writing
    # them out is clearer anyway: every way the page can change state is a named method.
    @rx.event
    def on_name(self, v: str):
        self.new_name = v

    @rx.event
    def on_label(self, v: str):
        self.new_label = v

    @rx.event
    def on_url(self, v: str):
        self.new_url = v

    @rx.event
    def on_prompt(self, v: str):
        self.prompt = v

    @rx.event
    async def choose(self, path: str):
        self.cwd = path
        self.reply = ""
        self.picked = ""
        self.runs = []
        async for _ in self._board():
            yield

    # -- board ---------------------------------------------------------------

    @staticmethod
    def _tokens(cost: dict) -> str:
        """Every token the turn was billed for, as one number.

        Cache reads and cache writes are included because they are billed. Showing only
        input plus output would report a cache-heavy session as nearly free, which is the
        opposite of what a cost display is for.
        """
        total = sum(
            int(cost.get(name) or 0)
            for name in (
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "cache_creation_tokens",
            )
        )
        return f"{total:,}" if total else "—"

    @staticmethod
    def _usd(cost: dict) -> str:
        usd = float(cost.get("cost_usd") or 0.0)
        if not usd:
            return ""
        # Under a cent still has to read as a number, not as $0.00.
        return f"${usd:.4f}" if usd < 0.01 else f"${usd:.2f}"

    async def _board(self):
        """Read the board for the chosen workspace. Yields so the page can paint."""
        if not self.cwd:
            self.stages, self.units, self.board_note = [], [], ""
            return
        try:
            data = await _service.board(self.cwd)
        except Invalid as e:
            self.stages, self.units = [], []
            self.board_note = str(e)
            yield
            return

        self.stages = list(data["stages"])
        self.recording = bool(data["recording"])
        self.board_note = data.get("read_only_because") or data.get("empty_because") or ""
        self.units = [
            UnitRow(
                name=u["name"],
                next=u["next"],
                blocked=bool(u["blocked"]),
                tokens=self._tokens(u.get("cost") or {}),
                usd=self._usd(u.get("cost") or {}),
                cells=[
                    Cell(
                        stage=row["stage"],
                        status=row["status"],
                        mode=row["mode"],
                        color=STATUS_COLOR.get(row["status"], "gray"),
                        started=row["status"] != "not started",
                        grants=", ".join(row.get("grants") or []),
                        warning=row.get("warning") or "",
                    )
                    for row in u["stages"]
                ],
            )
            for u in data["units"]
        ]
        yield

    def _load_timeline(self) -> None:
        """Read the picked unit's runs. Plain method, so both handlers can call it."""
        self.runs = []
        if not self.picked:
            return
        try:
            data = _service.timeline(self.cwd, self.picked)
        except Invalid as e:
            self.error = str(e)
            return
        self.runs = [
            Run(
                stage=r.get("stage") or "",
                mode=r.get("mode") or "",
                started=r.get("started") or "",
                ended=r.get("ended") or "(running)",
                outcome=r.get("outcome") or "—",
                session_id=r.get("session_id") or "—",
                tokens=self._tokens(r.get("cost") or {}),
                usd=self._usd(r.get("cost") or {}),
            )
            for r in data["runs"]
        ]

    @rx.event
    def pick(self, unit: str):
        """Open one unit. Clicking the open one closes it again."""
        self.picked = "" if self.picked == unit else unit
        self._load_timeline()

    @rx.event(background=True)
    async def run_step(self, unit: str, stage: str):
        """Run one step, streaming what comes back.

        `background=True` is the difference between a board and a frozen page. A generator
        event handler holds the state lock for its whole life, so a step that takes minutes
        would lock every other control (`spec.md` R14). A background handler takes the lock
        in short bursts, which is why every write below sits inside `async with self`.
        """
        async with self:
            self.running = f"{unit}/{stage}"
            self.run_log = ""
            self.error = ""

        try:
            async for kind, payload in _service.run_step(self.cwd, unit, stage):
                async with self:
                    if kind == "chunk":
                        self.run_log += payload
                    elif payload.get("error"):
                        self.error = payload["error"]
        except Invalid as e:
            async with self:
                self.error = str(e)
        finally:
            # Re-read rather than patch. The artifact on disk is the truth about a stage's
            # status, and this is the moment it changed.
            async with self:
                self.running = ""
                async for _ in self._board():
                    pass
                self._load_timeline()

    @rx.event
    async def set_mode(self, unit: str, stage: str, mode: str):
        self.error = ""
        try:
            await _service.set_mode(self.cwd, unit, stage, mode)
        except Invalid as e:
            self.error = str(e)
            return
        async for _ in self._board():
            yield

    def _reload(self) -> None:
        data = _service.workspaces()
        self.working_dir = data.get("working_dir") or "(not set)"
        self.count = data["count"]
        self.rows = [
            {
                "name": r["name"],
                "path": r["path"],
                "label": r["label"],
                "source": r["source"],
                "missing": "yes" if r["missing"] else "",
            }
            for r in data["workspaces"]
        ]

    @rx.event
    async def load(self):
        self.error = ""
        self._reload()
        yield
        async for _ in self._board():
            yield

    @rx.event
    async def add(self):
        """Adopt or clone. Which one is decided by whether a URL was typed."""
        self.busy, self.error = True, ""
        yield
        try:
            await _service.add_workspace(
                self.new_name, label=self.new_label, repo_url=self.new_url or None
            )
            self.new_name = self.new_url = self.new_label = ""
            self._reload()
        except Invalid as e:
            self.error = str(e)
        finally:
            self.busy = False

    @rx.event
    def relabel(self, name: str, label: str):
        self.error = ""
        try:
            _service.set_label(name, label)
            self._reload()
        except Invalid as e:
            self.error = str(e)

    @rx.event
    def remove(self, name: str):
        """De-lists only. The directory stays on disk — `spec.md` R18."""
        self.error = ""
        try:
            _service.remove_workspace(name)
            self._reload()
        except Invalid as e:
            self.error = str(e)

    @rx.event
    async def pull(self, name: str):
        self.busy, self.error = True, ""
        yield
        try:
            await _service.pull_workspace(name)
            self._reload()
        except Invalid as e:
            # A failed fast-forward is normal and must be visible, not swallowed
            # (`spec.md` C7).
            self.error = str(e)
        finally:
            self.busy = False

    @rx.event
    async def send(self):
        self.busy, self.error, self.reply = True, "", ""
        yield
        try:
            _service.check_send(self.cwd, self.prompt)
            async for kind, payload in _service.stream(self.cwd, self.prompt):
                if kind == "chunk":
                    self.reply += payload
                    yield
        except Exception as e:
            self.error = str(e)
        finally:
            self.busy = False


def _row(row: rx.Var) -> rx.Component:
    """One workspace. The chosen one is marked on the row rather than somewhere else."""
    chosen = State.cwd == row["path"]
    return rx.table.row(
        rx.table.cell(
            rx.vstack(
                rx.hstack(
                    rx.text(row["name"], weight="bold", size="2"),
                    rx.cond(chosen, rx.badge("in use", color_scheme="iris", variant="solid")),
                    rx.cond(row["missing"], rx.badge("missing", color_scheme="red")),
                    rx.cond(
                        row["source"] == "env",
                        rx.badge("env", color_scheme="gray", variant="surface"),
                    ),
                    spacing="2",
                    align="center",
                    wrap="wrap",
                ),
                ui.mono(row["path"], size="1"),
                spacing="1",
                align="start",
            ),
            white_space="nowrap",
        ),
        rx.table.cell(
            rx.input(
                default_value=row["label"],
                placeholder="label",
                on_blur=lambda v: State.relabel(row["name"], v),
                disabled=row["source"] == "env",
                size="2",
                variant="soft",
            ),
            min_width="180px",
        ),
        rx.table.cell(
            rx.hstack(
                rx.button(
                    rx.cond(chosen, "in use", "use"),
                    on_click=State.choose(row["path"]),
                    variant=rx.cond(chosen, "solid", "soft"),
                    size="2",
                ),
                rx.button(
                    "pull",
                    on_click=State.pull(row["name"]),
                    disabled=row["source"] == "env",
                    variant="soft",
                    size="2",
                    loading=State.busy,
                ),
                rx.button(
                    "remove",
                    on_click=State.remove(row["name"]),
                    disabled=row["source"] == "env",
                    color_scheme="red",
                    variant="soft",
                    size="2",
                ),
                spacing="2",
                wrap="wrap",
            ),
            white_space="nowrap",
        ),
        background=rx.cond(chosen, rx.color("iris", 3), "transparent"),
    )


def _workspaces() -> rx.Component:
    add_form = rx.flex(
        rx.input(
            placeholder="name",
            value=State.new_name,
            on_change=State.on_name,
            size="2",
            flex="1 1 140px",
        ),
        rx.input(
            placeholder="label (optional)",
            value=State.new_label,
            on_change=State.on_label,
            size="2",
            flex="1 1 140px",
        ),
        rx.input(
            placeholder="https://… (leave empty to adopt a folder already there)",
            value=State.new_url,
            on_change=State.on_url,
            size="2",
            flex="2 1 240px",
        ),
        rx.button("add", on_click=State.add, loading=State.busy, size="2"),
        gap="2",
        width="100%",
        wrap="wrap",
        align="center",
    )

    return ui.section(
        "Workspaces",
        ui.card(
            rx.cond(
                State.count > 0,
                ui.scroll_x(
                    rx.table.root(
                        rx.table.header(
                            rx.table.row(
                                rx.table.column_header_cell("workspace"),
                                rx.table.column_header_cell("label"),
                                rx.table.column_header_cell(""),
                            )
                        ),
                        rx.table.body(rx.foreach(State.rows, _row)),
                        variant="ghost",
                        size="2",
                        width="100%",
                    )
                ),
                # An empty list is a state worth designing, not a blank area.
                rx.vstack(
                    rx.text("No workspaces yet.", weight="medium"),
                    ui.muted("Add a folder already under the working folder, or clone one by URL."),
                    spacing="1",
                    padding="10px 2px",
                ),
            ),
            rx.box(height="14px"),
            add_form,
        ),
        actions=rx.hstack(
            ui.muted("working folder"),
            ui.mono(State.working_dir),
            ui.muted("·"),
            # `scripts/verify_0004.py:248` waits for this exact phrasing. It is the one
            # number the app could not answer before `0003`, so the proof watches for it
            # on the page rather than only over HTTP — do not reword it casually.
            ui.muted(State.count.to_string() + " workspace(s)"),
            spacing="2",
            align="center",
            wrap="wrap",
        ),
    )


def _chat() -> rx.Component:
    return ui.section(
        "Chat",
        ui.card(
            rx.cond(
                State.cwd != "",
                rx.vstack(
                    rx.cond(
                        State.reply != "",
                        rx.box(
                            rx.text(State.reply, white_space="pre-wrap"),
                            width="100%",
                            padding="12px 14px",
                            border_radius="10px",
                            background=rx.color("gray", 3),
                        ),
                        ui.muted("Ask something about this workspace."),
                    ),
                    rx.flex(
                        rx.input(
                            placeholder="prompt",
                            value=State.prompt,
                            on_change=State.on_prompt,
                            size="2",
                            flex="1 1 240px",
                        ),
                        rx.button("send", on_click=State.send, loading=State.busy, size="2"),
                        gap="2",
                        width="100%",
                        wrap="wrap",
                    ),
                    spacing="3",
                    width="100%",
                ),
                rx.vstack(
                    rx.text("Pick a workspace first.", weight="medium"),
                    ui.muted("Press “use” on a row above — chat runs inside that folder."),
                    spacing="1",
                    padding="10px 2px",
                ),
            )
        ),
        actions=rx.cond(
            State.cwd != "",
            rx.hstack(ui.muted("cwd"), ui.mono(State.cwd), spacing="2", align="center", wrap="wrap"),
            rx.fragment(),
        ),
    )


def _cell(cell: rx.Var) -> rx.Component:
    """One stage of one unit: what it says, and whether it is set to run itself.

    The mode is shown as a mark on the cell rather than as a control in it. Sixty-four
    controls in a grid is not a board, it is a form — the controls live in the panel that
    opens when a unit is picked.
    """
    return rx.table.cell(
        rx.tooltip(
            rx.hstack(
                rx.cond(
                    cell.started,
                    rx.badge(cell.status, color_scheme=cell.color, variant="soft", size="1"),
                    # A stage nobody has reached is the common case — on a fresh unit it is
                    # seven cells out of eight. Spelling out "not started" eight times per
                    # row pushed the `next` column off the side of the card at 1280px,
                    # which is the column that says what to do. So it is a mark, and the
                    # words are in the tooltip.
                    rx.text("·", color=rx.color("gray", 8), size="2", weight="bold"),
                ),
                rx.cond(
                    cell.mode == "autonomous",
                    rx.icon("zap", size=12, color=rx.color("amber", 10)),
                ),
                spacing="1",
                align="center",
            ),
            content=cell.stage + ": " + cell.status + " · " + cell.mode,
        ),
        white_space="nowrap",
    )


def _unit_row(unit: rx.Var) -> rx.Component:
    picked = State.picked == unit.name
    return rx.table.row(
        rx.table.cell(
            rx.hstack(
                rx.icon(
                    "chevron-right",
                    size=14,
                    color=rx.color("gray", 10),
                    transform=rx.cond(picked, "rotate(90deg)", "none"),
                ),
                rx.text(unit.name, weight="medium", size="2"),
                spacing="2",
                align="center",
            ),
            white_space="nowrap",
        ),
        rx.foreach(unit.cells, _cell),
        rx.table.cell(
            rx.hstack(
                ui.muted(unit.tokens),
                rx.cond(unit.usd != "", ui.muted(unit.usd, color=rx.color("amber", 11))),
                spacing="2",
                align="center",
            ),
            white_space="nowrap",
        ),
        rx.table.cell(ui.muted(unit.next), white_space="nowrap"),
        on_click=State.pick(unit.name),
        cursor="pointer",
        background=rx.cond(picked, rx.color("iris", 3), "transparent"),
        _hover={"background": rx.color("gray", 3)},
    )


def _mode_control(cell: rx.Var) -> rx.Component:
    """Manual or autonomous, for one stage of the picked unit."""
    return rx.hstack(
        rx.text(cell.stage, size="2", weight="medium", width="72px"),
        rx.badge(
            cell.status,
            color_scheme=cell.color,
            variant=rx.cond(cell.started, "soft", "outline"),
            size="1",
        ),
        rx.spacer(),
        rx.segmented_control.root(
            rx.segmented_control.item("manual", value="manual"),
            rx.segmented_control.item("auto", value="autonomous"),
            value=cell.mode,
            on_change=lambda v: State.set_mode(State.picked, cell.stage, v),
            size="1",
            disabled=~State.recording,
        ),
        rx.button(
            rx.cond(State.running == State.picked + "/" + cell.stage, "running…", "run"),
            on_click=State.run_step(State.picked, cell.stage),
            # One step at a time, and never while another is going: two steps writing into
            # one unit would race on the same files.
            disabled=(State.running != "") | ~State.recording,
            variant="soft",
            size="1",
        ),
        # `spec.md` C4. What a step will be allowed to do is readable before it is
        # started, next to the button that starts it — not only in a design document.
        rx.cond(
            cell.grants != "",
            rx.tooltip(
                rx.icon("key-round", size=13, color=rx.color("amber", 10)),
                content=cell.grants,
            ),
        ),
        width="100%",
        align="center",
        gap="2",
        wrap="wrap",
    )


def _run_row(run: rx.Var) -> rx.Component:
    return rx.table.row(
        rx.table.cell(rx.text(run.stage, size="2", weight="medium"), white_space="nowrap"),
        rx.table.cell(ui.muted(run.mode), white_space="nowrap"),
        rx.table.cell(ui.muted(run.started), white_space="nowrap"),
        rx.table.cell(ui.muted(run.ended), white_space="nowrap"),
        rx.table.cell(rx.badge(run.outcome, size="1", variant="soft"), white_space="nowrap"),
        rx.table.cell(ui.mono(run.session_id, size="1"), white_space="nowrap"),
        rx.table.cell(ui.muted(run.tokens), white_space="nowrap"),
        rx.table.cell(ui.muted(run.usd, color=rx.color("amber", 11)), white_space="nowrap"),
    )


def _detail() -> rx.Component:
    """The picked unit: how each stage should run, and what has happened to it."""
    return ui.card(
        rx.vstack(
            rx.hstack(
                rx.heading(State.picked, size="2"),
                rx.spacer(),
                rx.button(
                    "close", on_click=State.pick(State.picked), variant="ghost", size="1"
                ),
                width="100%",
                align="center",
            ),
            rx.foreach(
                State.units,
                lambda u: rx.cond(
                    u.name == State.picked,
                    rx.vstack(
                        rx.foreach(u.cells, _mode_control),
                        rx.foreach(
                            u.cells,
                            lambda c: rx.cond(
                                c.warning != "",
                                rx.callout(
                                    c.stage + " — " + c.warning,
                                    icon="shield-alert",
                                    color_scheme="amber",
                                    variant="surface",
                                    size="1",
                                    width="100%",
                                ),
                            ),
                        ),
                        spacing="2",
                        width="100%",
                    ),
                ),
            ),
            # Live output, `spec.md` R13. It appears while a step runs and stays after it
            # so a failure can be read, rather than vanishing with the spinner.
            rx.cond(
                State.run_log != "",
                rx.box(
                    rx.hstack(
                        rx.cond(
                            State.running != "",
                            rx.hstack(
                                rx.spinner(size="1"),
                                ui.muted(State.running),
                                spacing="2",
                                align="center",
                            ),
                            ui.muted("last run"),
                        ),
                        width="100%",
                    ),
                    rx.text(
                        State.run_log,
                        white_space="pre-wrap",
                        size="1",
                        font_family="ui-monospace, SFMono-Regular, Menlo, monospace",
                    ),
                    width="100%",
                    max_height="260px",
                    overflow_y="auto",
                    padding="10px 12px",
                    border_radius="10px",
                    background=rx.color("gray", 3),
                ),
            ),
            rx.separator(width="100%"),
            rx.heading("Timeline", size="2"),
            rx.cond(
                State.runs.length() > 0,
                ui.scroll_x(
                    rx.table.root(
                        rx.table.header(
                            rx.table.row(
                                rx.table.column_header_cell("stage"),
                                rx.table.column_header_cell("mode"),
                                rx.table.column_header_cell("started"),
                                rx.table.column_header_cell("ended"),
                                rx.table.column_header_cell("outcome"),
                                rx.table.column_header_cell("session"),
                                rx.table.column_header_cell("tokens"),
                                rx.table.column_header_cell("cost"),
                            )
                        ),
                        rx.table.body(rx.foreach(State.runs, _run_row)),
                        variant="ghost",
                        size="1",
                        width="100%",
                    )
                ),
                ui.muted("Nothing has run for this unit yet."),
            ),
            spacing="3",
            width="100%",
        ),
        background=rx.color("gray", 1),
    )


def _board() -> rx.Component:
    return ui.section(
        "Board",
        rx.cond(
            State.board_note != "",
            rx.callout(State.board_note, icon="info", variant="surface", size="1", width="100%"),
        ),
        rx.cond(
            State.units.length() > 0,
            rx.vstack(
                ui.card(
                    ui.scroll_x(
                        rx.table.root(
                            rx.table.header(
                                rx.table.row(
                                    rx.table.column_header_cell("unit"),
                                    rx.foreach(
                                        State.stages,
                                        lambda s: rx.table.column_header_cell(s),
                                    ),
                                    rx.table.column_header_cell("tokens"),
                                    rx.table.column_header_cell("next"),
                                )
                            ),
                            rx.table.body(rx.foreach(State.units, _unit_row)),
                            variant="ghost",
                            size="1",
                            width="100%",
                        )
                    )
                ),
                rx.cond(State.picked != "", _detail()),
                spacing="3",
                width="100%",
            ),
            rx.cond(
                State.cwd != "",
                rx.fragment(),
                ui.card(
                    rx.vstack(
                        rx.text("Pick a workspace to see its board.", weight="medium"),
                        ui.muted("The board reads the .cos/ directory of the chosen workspace."),
                        spacing="1",
                    )
                ),
            ),
        ),
        actions=rx.cond(
            State.recording,
            rx.fragment(),
            rx.badge("read only", color_scheme="amber", variant="surface"),
        ),
    )


def index() -> rx.Component:
    return ui.page(
        rx.cond(
            State.error != "",
            rx.callout(
                State.error,
                icon="triangle_alert",
                color_scheme="red",
                variant="surface",
                width="100%",
            ),
        ),
        _workspaces(),
        _board(),
        _chat(),
        on_mount=State.load,
    )


# The theme is configured in `rxconfig.py` through `RadixThemesPlugin`, because 0.9.11
# deprecates `App(theme=...)` and removes it at 1.0. The global style still belongs here.
app = rx.App(api_transformer=_api, style=ui.GLOBAL_STYLE)
app.add_page(index, title="cos-baodo")
