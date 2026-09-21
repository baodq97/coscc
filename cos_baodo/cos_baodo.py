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
    return rx.table.row(
        rx.table.cell(
            rx.hstack(
                rx.text(row["name"], weight="bold"),
                rx.cond(row["missing"], rx.badge("missing", color_scheme="red")),
                rx.badge(row["source"]),
                spacing="2",
                align="center",
            )
        ),
        rx.table.cell(
            rx.input(
                default_value=row["label"],
                placeholder="label",
                on_blur=lambda v: State.relabel(row["name"], v),
                disabled=row["source"] == "env",
            )
        ),
        rx.table.cell(rx.text(row["path"], size="1", color_scheme="gray")),
        rx.table.cell(
            rx.hstack(
                rx.button("use", on_click=State.choose(row["path"]), variant="soft"),
                rx.button(
                    "pull",
                    on_click=State.pull(row["name"]),
                    disabled=row["source"] == "env",
                    variant="soft",
                ),
                rx.button(
                    "remove",
                    on_click=State.remove(row["name"]),
                    disabled=row["source"] == "env",
                    color_scheme="red",
                    variant="soft",
                ),
                spacing="2",
            )
        ),
    )


def index() -> rx.Component:
    return rx.container(
        rx.vstack(
            rx.heading("cos-baodo"),
            rx.text(
                "working folder: ",
                rx.code(State.working_dir),
                "  ·  ",
                State.count.to_string(),
                " workspace(s)",
                size="2",
            ),
            rx.cond(
                State.error != "",
                rx.callout(State.error, color_scheme="red", width="100%"),
            ),
            rx.heading("Workspaces", size="4"),
            rx.table.root(
                rx.table.header(
                    rx.table.row(
                        rx.table.column_header_cell("name"),
                        rx.table.column_header_cell("label"),
                        rx.table.column_header_cell("path"),
                        rx.table.column_header_cell(""),
                    )
                ),
                rx.table.body(rx.foreach(State.rows, _row)),
                width="100%",
            ),
            rx.hstack(
                rx.input(
                    placeholder="name",
                    value=State.new_name,
                    on_change=State.on_name,
                ),
                rx.input(
                    placeholder="label (optional)",
                    value=State.new_label,
                    on_change=State.on_label,
                ),
                rx.input(
                    placeholder="https://… (leave empty to adopt an existing folder)",
                    value=State.new_url,
                    on_change=State.on_url,
                    width="100%",
                ),
                rx.button("add", on_click=State.add, loading=State.busy),
                width="100%",
                spacing="2",
            ),
            rx.divider(),
            rx.heading("Chat", size="4"),
            rx.text("cwd: ", rx.code(State.cwd), size="2"),
            rx.hstack(
                rx.input(
                    placeholder="prompt",
                    value=State.prompt,
                    on_change=State.on_prompt,
                    width="100%",
                ),
                rx.button("send", on_click=State.send, loading=State.busy),
                width="100%",
            ),
            rx.cond(State.reply != "", rx.card(rx.text(State.reply), width="100%")),
            spacing="4",
            width="100%",
        ),
        size="4",
        on_mount=State.load,
    )


app = rx.App(api_transformer=_api)
app.add_page(index, title="cos-baodo")
