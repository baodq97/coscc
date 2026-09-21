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

import reflex as rx

from cos_baodo import ui
from cos_baodo.api import build
from cos_baodo.service import Invalid

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
    def choose(self, path: str):
        self.cwd = path
        self.reply = ""

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
    def load(self):
        self.error = ""
        self._reload()

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
        _chat(),
        on_mount=State.load,
    )


# The theme is configured in `rxconfig.py` through `RadixThemesPlugin`, because 0.9.11
# deprecates `App(theme=...)` and removes it at 1.0. The global style still belongs here.
app = rx.App(api_transformer=_api, style=ui.GLOBAL_STYLE)
app.add_page(index, title="cos-baodo")
