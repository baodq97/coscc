"""An interactive design preview. All mutations stop at this Reflex state."""

from __future__ import annotations

import asyncio

import reflex as rx
from reflex.style import set_color_mode

from cos_baodo import prototype_data as demo
from cos_baodo import studio as s
from cos_baodo.prototype_data import Activity, Conversation, Message, Work, Workspace

NAVIGATION = (
    ("overview", "Overview", "house"),
    ("workspaces", "Workspaces", "layers"),
    ("board", "Board", "columns-3"),
    ("sessions", "Sessions", "messages-square"),
    ("activity", "Activity & usage", "chart-no-axes-combined"),
    ("settings", "Settings", "settings-2"),
)


class PrototypeState(rx.State):
    screen: str = "overview"
    workspace_id: str = "atlas"
    workspaces: list[Workspace] = demo.workspaces()
    work: list[Work] = demo.work()
    conversations: list[Conversation] = demo.conversations()
    activities: list[Activity] = demo.activities()
    workspace_query: str = ""
    query: str = ""
    focus: str = "All work"
    board_view: str = "Board"
    scenario: str = "populated"
    density: str = "comfortable"
    notice: str = ""
    mobile_open: bool = False
    command_open: bool = False
    command_query: str = ""
    workspace_form: bool = False
    editing_workspace: str = ""
    workspace_name: str = ""
    workspace_description: str = ""
    form_error: str = ""
    remove_id: str = ""
    new_work_open: bool = False
    new_work_title: str = ""
    unit_id: str = ""
    detail_tab: str = "overview"
    run_log: list[str] = []
    run_unit: str = ""
    log_unit: str = ""
    session_id: str = "atlas-1"
    prompt: str = ""
    serial: int = 100

    @rx.var
    def current_workspace(self) -> Workspace:
        return next((w for w in self.workspaces if w.id == self.workspace_id),
                    Workspace("", "No workspace", "Create a demo workspace to get started."))

    @rx.var
    def screen_title(self) -> str:
        return next(label for key, label, _ in NAVIGATION if key == self.screen)

    @rx.var
    def filtered_workspaces(self) -> list[Workspace]:
        q = self.workspace_query.casefold().strip()
        return [w for w in self.workspaces if q in (w.name + " " + w.description).casefold()]

    @rx.var
    def workspace_work(self) -> list[Work]:
        return [w for w in self.work if w.workspace == self.workspace_id]

    @rx.var
    def visible_work(self) -> list[Work]:
        q = self.query.casefold().strip()
        return [
            w for w in self.workspace_work
            if q in f"{w.id} {w.title} {w.summary}".casefold()
            and (self.focus != "Autonomous" or w.mode == "autonomous")
            and (self.focus != "Needs review" or w.lane == "Needs review")
        ]

    @rx.var
    def planned(self) -> list[Work]:
        return [w for w in self.visible_work if w.lane == "Planned"]

    @rx.var
    def in_progress(self) -> list[Work]:
        return [w for w in self.visible_work if w.lane == "In progress"]

    @rx.var
    def needs_review(self) -> list[Work]:
        return [w for w in self.visible_work if w.lane == "Needs review"]

    @rx.var
    def complete(self) -> list[Work]:
        return [w for w in self.visible_work if w.lane == "Complete"]

    @rx.var
    def active_count(self) -> int:
        return sum(w.lane != "Complete" for w in self.workspace_work)

    @rx.var
    def attention_count(self) -> int:
        return sum(w.lane == "Needs review" for w in self.workspace_work)

    @rx.var
    def token_total(self) -> str:
        return f"{sum(w.tokens for w in self.workspace_work):,}"

    @rx.var
    def cost_total(self) -> str:
        return f"${sum(w.cost for w in self.workspace_work):.2f}"

    @rx.var
    def current_unit(self) -> Work:
        return next((w for w in self.workspace_work if w.id == self.unit_id),
                    Work("", "", "", "", "", "", "gray"))

    @rx.var
    def artifact(self) -> str:
        unit = self.current_unit
        return (
            f"# {unit.title}\n\n"
            "> Illustrative artifact. Not read from disk and not an approval.\n\n"
            f"## Outcome\n\n{unit.summary}\n\n"
            "## Design notes\n\n"
            "- Keep the current workspace visible.\n"
            "- Reveal detail progressively, without losing the board.\n"
            "- Make the next action and its permission scope explicit.\n\n"
            "## Still open\n\nHuman feedback on this direction has not been recorded."
        )

    @rx.var
    def workspace_sessions(self) -> list[Conversation]:
        return [c for c in self.conversations if c.workspace == self.workspace_id]

    @rx.var
    def current_session(self) -> Conversation:
        return next((c for c in self.workspace_sessions if c.id == self.session_id),
                    Conversation("", self.workspace_id, "A new conversation", "Demo session"))

    @rx.var
    def workspace_activity(self) -> list[Activity]:
        return [a for a in self.activities if a.workspace == self.workspace_id]

    @rx.var
    def command_work(self) -> list[Work]:
        q = self.command_query.casefold().strip()
        return [w for w in self.workspace_work if q in f"{w.id} {w.title}".casefold()][:5]

    @rx.event
    def navigate(self, screen: str):
        if screen not in {key for key, _, _ in NAVIGATION}:
            self.notice = "That preview screen does not exist."
            return
        self.screen = screen
        self.mobile_open = False
        self.command_open = False
        self.scenario = "populated"

    @rx.event
    def choose_workspace(self, workspace_id: str):
        if not any(w.id == workspace_id for w in self.workspaces):
            self.notice = "That demo workspace no longer exists."
            return
        self.workspace_id = workspace_id
        self.unit_id = ""
        self.query = ""
        self.focus = "All work"
        self.prompt = ""
        self.session_id = next(
            (c.id for c in self.conversations if c.workspace == workspace_id), ""
        )
        self.scenario = "populated"
        self.mobile_open = False

    @rx.event
    def open_workspace(self, workspace_id: str):
        self.choose_workspace(workspace_id)
        self.screen = "board"

    @rx.event
    def search_workspaces(self, value: str):
        self.workspace_query = value

    @rx.event
    def search_work(self, value: str):
        self.query = value

    @rx.event
    def filter_work(self, value: str):
        if value not in ("All work", "Autonomous", "Needs review"):
            self.notice = "Unknown work filter."
            return
        self.focus = value

    @rx.event
    def set_board_view(self, value: str):
        if value in ("Board", "List"):
            self.board_view = value
        else:
            self.notice = "Unknown board view."

    @rx.event
    def preview_scenario(self, value: str):
        if value in ("populated", "empty", "loading", "error"):
            self.scenario = value
        else:
            self.notice = "Unknown preview state."

    @rx.event
    def set_density(self, value: str):
        if value in ("comfortable", "compact"):
            self.density = value
        else:
            self.notice = "Unknown display density."

    @rx.event
    def dismiss_notice(self):
        self.notice = ""

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

    @rx.event
    def edit_workspace(self, workspace_id: str):
        self.form_error = ""
        self.editing_workspace = workspace_id
        if workspace_id:
            workspace = next((w for w in self.workspaces if w.id == workspace_id), None)
            if workspace is None:
                self.notice = "That demo workspace no longer exists."
                return
            self.workspace_name = workspace.name
            self.workspace_description = workspace.description
        else:
            self.workspace_name = ""
            self.workspace_description = ""
        self.workspace_form = True

    @rx.event
    def toggle_workspace_form(self, value: bool):
        self.workspace_form = value

    @rx.event
    def set_workspace_name(self, value: str):
        self.workspace_name = value

    @rx.event
    def set_workspace_description(self, value: str):
        self.workspace_description = value

    @rx.event
    def save_workspace(self):
        name = self.workspace_name.strip()
        if not name:
            self.form_error = "Give your workspace a name."
            return
        if len(name) > 60:
            self.form_error = "Keep the workspace name to 60 characters or fewer."
            return
        if any(w.name.casefold() == name.casefold() and w.id != self.editing_workspace
               for w in self.workspaces):
            self.form_error = "A demo workspace already has that name."
            return
        if self.editing_workspace:
            for workspace in self.workspaces:
                if workspace.id == self.editing_workspace:
                    workspace.name = name
                    workspace.description = self.workspace_description.strip()
                    workspace.initials = name[:2].upper()
                    break
            else:
                self.form_error = "This workspace was removed. Close this form and try again."
                return
            self.workspaces = list(self.workspaces)
        else:
            self.serial += 1
            self.workspaces = [*self.workspaces, Workspace(
                f"demo-{self.serial}", name, self.workspace_description.strip() or
                "A fresh space for your next idea.", initials=name[:2].upper(),
            )]
        self.workspace_form = False
        self.workspace_query = ""
        self.notice = "Saved in the demo only. No folder was created or changed."

    @rx.event
    def request_remove(self, workspace_id: str):
        if not any(w.id == workspace_id for w in self.workspaces):
            self.notice = "That demo workspace no longer exists."
            return
        self.remove_id = workspace_id

    @rx.event
    def toggle_remove(self, value: bool):
        if not value:
            self.remove_id = ""

    @rx.event
    def remove_workspace(self):
        target = self.remove_id
        if not target or not any(w.id == target for w in self.workspaces):
            self.notice = "Select a demo workspace before removing it."
            return
        if any(w.id == self.run_unit and w.workspace == target for w in self.work):
            self.notice = "Wait for this workspace's simulation to finish before removing it."
            self.remove_id = ""
            return
        self.workspaces = [w for w in self.workspaces if w.id != target]
        self.work = [w for w in self.work if w.workspace != target]
        self.conversations = [c for c in self.conversations if c.workspace != target]
        self.activities = [a for a in self.activities if a.workspace != target]
        if self.workspace_id == target:
            if self.workspaces:
                self.choose_workspace(self.workspaces[0].id)
            else:
                self.workspace_id = self.session_id = self.unit_id = self.prompt = ""
        self.remove_id = ""
        self.notice = "Removed from the demo. Nothing on disk was deleted."

    @rx.event
    def open_unit(self, unit_id: str):
        if not any(w.id == unit_id for w in self.workspace_work):
            self.notice = "That work item is not in the selected workspace."
            return
        self.unit_id = unit_id
        self.detail_tab = "overview"
        self.command_open = False

    @rx.event
    def toggle_detail(self, value: bool):
        if not value:
            self.unit_id = ""

    @rx.event
    def set_detail_tab(self, value: str):
        if value in ("overview", "artifacts", "timeline"):
            self.detail_tab = value
        else:
            self.notice = "Unknown detail tab."

    @rx.event
    def set_mode(self, value: str):
        if value not in ("manual", "autonomous"):
            self.notice = "Choose manual or autonomous for this simulation."
            return
        if not self.unit_id:
            self.notice = "Open a work item first."
            return
        for unit in self.work:
            if unit.id == self.unit_id and unit.workspace == self.workspace_id:
                unit.mode = value
        self.work = list(self.work)

    @rx.event(background=True)
    async def simulate_run(self):
        async with self:
            if self.run_unit:
                self.notice = "A demo run is already in progress."
                return
            if not self.unit_id or self.current_unit.stage == "ship":
                self.notice = "Ship is not enabled. This preview cannot publish or approve work."
                return
            unit_id, workspace_id = self.unit_id, self.workspace_id
            self.run_unit = unit_id
            self.log_unit = unit_id
            self.run_log = ["Starting a simulation. No agent or tools are connected."]
        for line in (
            "Reading example context...",
            "Exploring the proposed next step...",
            "Preparing an illustrative result...",
            "Simulation complete. No files changed.",
        ):
            await asyncio.sleep(0.45)
            async with self:
                self.run_log = [*self.run_log, line]
        async with self:
            self.activities = [Activity(
                workspace_id, "Demo run completed", f"{unit_id} / No files changed, no tokens spent",
                "flask-conical", "iris", "Just now (simulation)",
            ), *self.activities]
            self.run_unit = ""

    @rx.event
    def toggle_new_work(self, value: bool):
        self.new_work_open = value
        self.form_error = ""
        if value:
            self.new_work_title = ""

    @rx.event
    def set_new_work_title(self, value: str):
        self.new_work_title = value

    @rx.event
    def create_work(self):
        title = self.new_work_title.strip()
        if not title or len(title) > 120:
            self.form_error = "Use a title between 1 and 120 characters."
            return
        if not self.workspace_id:
            self.form_error = "Create a workspace first."
            return
        self.serial += 1
        self.work = [*self.work, Work(
            f"DEMO-{self.serial}", self.workspace_id, title,
            "An example work item. No intent file has been created.",
            "Planned", "intent", "gray",
        )]
        self.query, self.focus = "", "All work"
        self.new_work_open = False
        self.screen = "board"
        self.notice = "Demo work added. No artifact was written."

    @rx.event
    def choose_session(self, session_id: str):
        if not any(c.id == session_id for c in self.workspace_sessions):
            self.notice = "That conversation is not in the selected workspace."
            return
        self.session_id = session_id
        self.prompt = ""

    @rx.event
    def new_session(self):
        if not self.workspace_id:
            self.notice = "Create a workspace before starting a demo conversation."
            return
        self.serial += 1
        self.session_id = f"session-{self.serial}"
        self.conversations = [Conversation(
            self.session_id, self.workspace_id, "Untitled conversation", "Just started (demo)",
            [Message("assistant", "What are you thinking about? This is a scripted preview, "
                     "not a connected AI session.")],
        ), *self.conversations]
        self.prompt = ""

    @rx.event
    def set_prompt(self, value: str):
        self.prompt = value

    @rx.event
    def send_message(self):
        prompt = self.prompt.strip()
        if not prompt:
            self.notice = "Write a message before sending."
            return
        conversation = next((c for c in self.workspace_sessions if c.id == self.session_id), None)
        if conversation is None:
            self.notice = "Start a demo conversation first."
            return
        conversation.messages = [*conversation.messages, Message("you", prompt), Message(
            "demo", "This is a scripted response so you can try the conversation flow. "
            "Your message stays in this preview session; no model was called and no quota was used. "
            "In the connected product, this is where the workspace-aware answer would appear.",
        )]
        if conversation.title == "Untitled conversation":
            conversation.title = prompt[:42]
        self.conversations = list(self.conversations)
        self.prompt = ""


P = PrototypeState


def _nav(mobile: bool = False) -> rx.Component:
    return rx.vstack(
        *[
            rx.button(
                rx.icon(icon, size=18),
                rx.text(label, size="2", weight=rx.cond(P.screen == key, "medium", "regular")),
                rx.spacer(),
                rx.cond(
                    (key == "board") & (P.attention_count > 0),
                    s.badge(P.attention_count.to_string(), "iris"),
                ),
                id=f"{'mobile-' if mobile else ''}nav-{key}",
                on_click=P.navigate(key), variant="ghost", color_scheme="gray",
                width="100%", justify_content="flex-start", height="42px",
                padding="0 12px", border_radius="8px",
                background=rx.cond(P.screen == key, rx.color("iris", 3), "transparent"),
                color=rx.cond(P.screen == key, rx.color("iris", 11), s.MUTED),
                aria_current=rx.cond(P.screen == key, "page", "false"),
                _hover={"background": rx.color("gray", 4)},
            )
            for key, label, icon in NAVIGATION
        ],
        spacing="1", width="100%",
    )


def _brand() -> rx.Component:
    return rx.hstack(
        rx.center(rx.icon("orbit", size=21), width="34px", height="34px",
                  border_radius="10px", background=s.ACCENT, color="white"),
        rx.text("cos", size="5", weight="bold", letter_spacing="-0.06em"),
        s.text("studio", size="3"),
        spacing="2", align="center",
    )


def _sidebar() -> rx.Component:
    return rx.vstack(
        rx.box(_brand(), padding="10px 10px 26px"),
        rx.box(
            s.eyebrow("PERSONAL SPACE"),
            rx.hstack(s.mark(P.current_workspace.initials, size="32px"),
                      s.native_select(
                          rx.foreach(P.workspaces, lambda w: rx.el.option(w.name, value=w.id)),
                          rx.cond(P.workspaces.length() == 0, rx.el.option("No workspace", value="")),
                          value=P.workspace_id, on_change=P.choose_workspace,
                          id="workspace-switcher", aria_label="Active workspace", width="100%",
                      ), width="100%", align="center", margin_top="12px"),
            padding="0 8px 24px", width="100%",
        ),
        _nav(),
        rx.spacer(),
        s.panel(
            rx.hstack(rx.icon("flask-conical", size=16, color=rx.color("iris", 11)),
                      rx.text("Room to explore", size="2", weight="medium")),
            s.text("A working sketch of what's next. Make yourself at home.", size="1", margin_top="8px"),
            rx.button("About this preview", rx.icon("arrow-up-right", size=14),
                      on_click=P.navigate("settings"), variant="ghost", size="1", margin_top="12px"),
            padding="14px", background=rx.color("iris", 2),
        ),
        rx.hstack(
            s.mark("ME", "gray", "32px"),
            rx.vstack(rx.text("Personal workspace", size="1", weight="medium"),
                      s.text("Local-first / no account", size="1"), spacing="0"),
            padding="16px 6px 4px", align="center",
        ),
        width="228px", min_width="228px", height="100dvh", position="sticky", top="0",
        padding="22px 14px", background=s.SURFACE, border_right=f"1px solid {s.LINE}",
        display=rx.breakpoints(initial="none", lg="flex"), spacing="2",
    )


def _topbar() -> rx.Component:
    return rx.flex(
        s.icon_button("menu", "Open navigation", id="mobile-navigation",
                      on_click=P.toggle_mobile(True),
                      display=rx.breakpoints(initial="flex", lg="none")),
        rx.hstack(
            rx.icon("layers", size=15, color=s.MUTED),
            s.text(P.current_workspace.name, overflow="hidden", text_overflow="ellipsis",
                   white_space="nowrap", max_width=rx.breakpoints(initial="90px", sm="180px")),
            s.text("/"), rx.text(P.screen_title, size="2", weight="medium"),
            spacing="3", align="center", min_width="0",
        ),
        rx.spacer(),
        rx.button(rx.icon("search", size=15), s.text("Search the studio", size="1"),
                  id="open-search", on_click=P.toggle_command(True), variant="ghost",
                  color_scheme="gray", aria_label="Search the studio",
                  display=rx.breakpoints(initial="none", md="flex")),
        s.badge("PROTOTYPE", "iris"),
        align="center", gap="3", width="100%", min_height="68px",
        padding=rx.breakpoints(initial="12px 18px", md="12px 32px"),
        border_bottom=f"1px solid {s.LINE}", background=s.CANVAS,
    )


def _preview_bar() -> rx.Component:
    return rx.flex(
        rx.hstack(
            rx.box(width="6px", height="6px", border_radius="50%", background=rx.color("iris", 9)),
            s.text("Demo data. Real interactions. Nothing touches your repos.", size="1"),
            spacing="2", align="center",
        ),
        rx.spacer(),
        rx.hstack(
            s.text("Preview state", size="1"),
            s.native_select(
                rx.el.option("Populated", value="populated"),
                rx.el.option("Empty", value="empty"),
                rx.el.option("Loading", value="loading"),
                rx.el.option("Error", value="error"),
                id="preview-scenario", aria_label="Preview state", value=P.scenario,
                on_change=P.preview_scenario,
            ),
            spacing="2", align="center",
        ),
        width="100%", align="center", wrap="wrap", gap="2",
        padding="12px 0 22px",
    )


def _metrics() -> rx.Component:
    return rx.grid(
        s.stat("Active work", P.active_count.to_string(), "Ideas becoming something real", "layers"),
        s.stat("Needs attention", P.attention_count.to_string(), "Ready for a human perspective",
               "circle-dot", "amber"),
        s.stat("Example tokens", P.token_total, "Illustrative usage, not live billing", "sparkles", "blue"),
        s.stat("Example cost", P.cost_total, "Sum of the sample work items", "wallet", "grass"),
        columns=rx.breakpoints(initial="2", xl="4"), gap="3", width="100%",
    )


def _activity_row(activity: rx.Var[Activity]) -> rx.Component:
    return rx.hstack(
        rx.center(rx.icon(activity.icon, size=16, color=rx.color(activity.color, 11)),
                  width="34px", height="34px", border_radius="50%",
                  background=rx.color(activity.color, 3), flex_shrink="0"),
        rx.vstack(rx.text(activity.title, size="2", weight="medium"),
                  s.text(activity.detail, size="1", overflow_wrap="anywhere"),
                  spacing="1", min_width="0"),
        rx.spacer(),
        s.text(activity.time, size="1", text_align="right", max_width="120px"),
        width="100%", align="center", padding="14px 0",
        border_bottom=f"1px solid {s.LINE}",
    )


def _empty() -> rx.Component:
    return s.panel(
        rx.vstack(
            rx.center(rx.icon("sprout", size=30, color=rx.color("iris", 10)),
                      width="76px", height="76px", border_radius="24px",
                      background=rx.color("iris", 3), margin_bottom="6px"),
            rx.heading("A fresh start.", size="6", weight="medium"),
            s.text("Good work starts with a little space. Add something worth exploring.",
                   text_align="center", max_width="340px"),
            rx.flex(
                rx.button("Create demo work", rx.icon("plus", size=15),
                          on_click=P.toggle_new_work(True), disabled=P.workspace_id == ""),
                rx.button("Manage workspaces", on_click=P.navigate("workspaces"),
                          variant="soft", color_scheme="gray"),
                gap="3", justify="center", wrap="wrap", margin_top="12px",
            ),
            rx.cond(P.scenario != "populated",
                    rx.button("Back to populated preview", id="restore-preview",
                              on_click=P.preview_scenario("populated"), variant="ghost")),
            spacing="3", align="center", padding="64px 10px", width="100%",
        ),
    )


def _overview() -> rx.Component:
    return rx.vstack(
        rx.box(s.eyebrow("YOUR WORK, IN FOCUS"), margin_top="6px"),
        s.heading("Build with intent.", "Less context switching. More meaningful progress.",
                  rx.button("Open board", rx.icon("arrow-right", size=16),
                            on_click=P.navigate("board"), size="2")),
        _metrics(),
        rx.grid(
            s.panel(
                s.section_head("Pick up where you left off", s.badge("IN MOTION", "iris")),
                rx.cond(
                    P.workspace_work.length() > 0,
                    rx.foreach(P.workspace_work[:1], lambda w: rx.vstack(
                        s.text(w.id + " / " + w.stage, size="1"),
                        rx.heading(w.title, size="6", weight="medium", letter_spacing="-0.025em"),
                        s.text(w.summary, max_width="460px", line_height="1.75"),
                        rx.box(
                            rx.progress(value=w.progress, max=100, color_scheme="iris", size="1"),
                            rx.hstack(s.text("Sample lifecycle progress", size="1"), rx.spacer(),
                                      s.text(w.progress.to_string() + "%", size="1"),
                                      margin_top="9px", width="100%"),
                            width="100%", margin_top="12px",
                        ),
                        rx.hstack(
                            rx.button("Explore this work", rx.icon("arrow-up-right", size=15),
                                      on_click=P.open_unit(w.id), variant="soft"),
                            s.badge(w.mode, "gray"), width="100%", margin_top="12px", wrap="wrap",
                        ),
                        spacing="3", width="100%", align="start",
                    )),
                    s.text("No work yet. Your next idea belongs here."),
                ),
                background=f"linear-gradient(135deg, {rx.color('iris', 2)}, {s.SURFACE})",
                padding=rx.breakpoints(initial="22px", md="28px"),
            ),
            s.panel(
                s.section_head("Make space for good work", rx.icon("sparkles", size=18, color=s.MUTED)),
                rx.heading("You set the direction.\nAI helps with the distance.", size="5",
                           weight="medium", white_space="pre-line", line_height="1.4"),
                s.text("Keep decisions human, actions visible, and every next step grounded "
                       "in what came before.", margin_top="16px", line_height="1.8"),
                rx.box(height="22px"),
                rx.button("Start a conversation", rx.icon("arrow-right", size=15),
                          on_click=P.navigate("sessions"), variant="ghost"),
                padding=rx.breakpoints(initial="22px", md="28px"),
            ),
            columns=rx.breakpoints(initial="1", xl="2"), gap="4", width="100%",
        ),
        rx.grid(
            s.panel(
                s.section_head("Recent activity",
                               rx.button("View all", on_click=P.navigate("activity"),
                                         variant="ghost", size="1")),
                rx.foreach(P.workspace_activity[:3], _activity_row),
                rx.cond(P.workspace_activity.length() == 0, s.text("Nothing has happened here yet.")),
            ),
            s.panel(
                s.section_head("Your workspaces",
                               rx.button("Manage", on_click=P.navigate("workspaces"),
                                         variant="ghost", size="1")),
                rx.foreach(P.workspaces, lambda w: rx.button(
                    s.mark(w.initials, w.color, "36px"),
                    rx.vstack(rx.text(w.name, size="2", weight="medium"),
                              s.text(w.repository, size="1"), spacing="0", align="start"),
                    rx.spacer(), rx.icon("arrow-up-right", size=15, color=s.MUTED),
                    on_click=P.open_workspace(w.id), variant="ghost", color_scheme="gray",
                    width="100%", height="auto", padding="13px 4px", justify_content="flex-start",
                )),
            ),
            columns=rx.breakpoints(initial="1", xl="2"), gap="4", width="100%",
        ),
        spacing="5", width="100%", align="start",
    )


def _workspace_card(workspace: rx.Var[Workspace]) -> rx.Component:
    return s.panel(
        rx.hstack(
            s.mark(workspace.initials, workspace.color, "44px"), rx.spacer(),
            rx.cond(P.workspace_id == workspace.id, s.badge("Current", "iris")),
            s.icon_button("pencil", "Edit " + workspace.name,
                          on_click=P.edit_workspace(workspace.id), size="1"),
            s.icon_button("trash-2", "Remove " + workspace.name,
                          on_click=P.request_remove(workspace.id), size="1"),
            width="100%", align="center",
        ),
        rx.heading(workspace.name, size="5", weight="medium", margin_top="22px",
                   overflow_wrap="anywhere"),
        s.text(workspace.description, margin_top="8px", min_height="44px", overflow_wrap="anywhere"),
        rx.hstack(rx.icon("git-branch", size=14, color=s.MUTED),
                  s.text(workspace.repository, size="1"), spacing="2", margin_top="20px"),
        rx.box(height="1px", background=s.LINE, margin="20px 0 16px"),
        rx.hstack(
            s.text("Demo workspace", size="1"), rx.spacer(),
            rx.button("Open workspace", rx.icon("arrow-right", size=14),
                      aria_label="Open " + workspace.name, on_click=P.open_workspace(workspace.id),
                      variant="ghost", size="1"),
            width="100%",
        ),
        data_testid="workspace-card",
    )


def _workspaces() -> rx.Component:
    return rx.vstack(
        s.heading("A home for every project.", "Separate contexts. One place to bring it all together.",
                  rx.button(rx.icon("plus", size=16), "New workspace", id="new-workspace",
                            on_click=P.edit_workspace(""))),
        rx.input(rx.input.slot(rx.icon("search", size=15)), placeholder="Find a workspace...",
                 value=P.workspace_query, on_change=P.search_workspaces, aria_label="Find a workspace",
                 max_width="360px", width="100%", variant="surface"),
        rx.cond(
            P.filtered_workspaces.length() > 0,
            rx.grid(rx.foreach(P.filtered_workspaces, _workspace_card),
                    columns=rx.breakpoints(initial="1", md="2", xl="3"), gap="4", width="100%"),
            s.panel(rx.heading("No workspaces found", size="4"),
                    s.text("Try a different search, or create your first demo workspace.", margin_top="8px")),
        ),
        s.text("This preview manages examples only. Adding a workspace does not clone or create a folder.",
               size="1"),
        spacing="5", width="100%",
    )


def _work_card(work: rx.Var[Work]) -> rx.Component:
    return rx.el.button(
        rx.hstack(
            s.text(work.id, size="1", font_family="ui-monospace, monospace"),
            rx.spacer(),
            rx.cond(work.mode == "autonomous", rx.icon("sparkles", size=14, color=rx.color("iris", 10))),
            width="100%",
        ),
        rx.text(work.title, size="2", weight="medium", line_height="1.6",
                color=s.INK, margin_top="13px"),
        s.text(work.summary, size="1", line_height="1.7", margin_top="7px",
               display=rx.cond(P.density == "compact", "none", "block")),
        rx.hstack(
            s.badge(work.stage, work.color),
            rx.spacer(),
            rx.center(rx.text(work.owner, size="1", weight="medium"),
                      width="27px", height="27px", border_radius="50%",
                      background=rx.color("gray", 4), color=s.MUTED),
            width="100%", align="center", margin_top="18px",
        ),
        id="unit-" + work.id, data_testid="work-card", type="button",
        aria_label="Open " + work.title, on_click=P.open_unit(work.id),
        padding=rx.cond(P.density == "compact", "12px", "17px"),
        background=s.CANVAS, border=f"1px solid {s.LINE}", border_radius="11px",
        width="100%", cursor="pointer", text_align="left", font_family="inherit",
        box_shadow=f"0 2px 3px {rx.color('gray', 3)}",
        transition="border-color 150ms ease, transform 150ms ease",
        _hover={"border_color": rx.color("iris", 7), "transform": "translateY(-2px)"},
        _focus_visible={"outline": f"2px solid {s.ACCENT}", "outline_offset": "3px"},
    )


def _lane(title: str, items, color: str) -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.box(width="7px", height="7px", border_radius="50%", background=rx.color(color, 9)),
            rx.text(title, size="2", weight="medium"),
            s.text(items.length().to_string(), size="1"),
            rx.spacer(), width="100%", align="center", padding="2px 4px 8px",
        ),
        rx.foreach(items, _work_card),
        rx.cond(items.length() == 0,
                rx.center(s.text("A little room for what's next", size="1", text_align="center"),
                          padding="26px 10px", border=f"1px dashed {s.LINE}",
                          border_radius="10px", width="100%")),
        spacing="3", align="stretch", width="100%", min_width="0",
        padding="12px", border_radius="13px", background=s.SURFACE,
    )


def _board() -> rx.Component:
    return rx.vstack(
        s.heading("Work board", "From an idea to something real. One clear step at a time.",
                  rx.button(rx.icon("plus", size=16), "New work",
                            on_click=P.toggle_new_work(True), disabled=P.workspace_id == "")),
        rx.flex(
            rx.input(rx.input.slot(rx.icon("search", size=15)), id="work-search",
                     placeholder="Search work...", value=P.query, on_change=P.search_work,
                     aria_label="Search work", width=rx.breakpoints(initial="100%", sm="240px")),
            rx.segmented_control.root(
                *[rx.segmented_control.item(label, value=label)
                  for label in ("All work", "Autonomous", "Needs review")],
                value=P.focus, on_change=P.filter_work, size="1",
            ),
            rx.spacer(),
            rx.segmented_control.root(
                rx.segmented_control.item(rx.icon("columns-3", size=14), "Board", value="Board"),
                rx.segmented_control.item(rx.icon("list", size=14), "List", value="List"),
                value=P.board_view, on_change=P.set_board_view, size="1",
            ),
            width="100%", align="center", gap="3", wrap="wrap",
        ),
        rx.cond(
            P.workspace_work.length() == 0,
            _empty(),
            rx.cond(
                P.visible_work.length() == 0,
                s.panel(rx.heading("No matching work", size="4"),
                        s.text("Try a different search or choose All work.", margin_top="8px")),
                rx.cond(
                    P.board_view == "Board",
                    rx.grid(
                        _lane("Planned", P.planned, "gray"),
                        _lane("In progress", P.in_progress, "iris"),
                        _lane("Needs review", P.needs_review, "amber"),
                        _lane("Complete", P.complete, "grass"),
                        columns=rx.breakpoints(initial="1", sm="2", xl="4"),
                        gap="3", width="100%", align_items="start",
                    ),
                    s.panel(
                        rx.foreach(P.visible_work, lambda w: rx.button(
                            s.text(w.id, size="1", min_width="75px"),
                            rx.text(w.title, size="2", weight="medium", text_align="left"),
                            rx.spacer(), s.badge(w.stage, w.color), s.text(w.lane, size="1"),
                            on_click=P.open_unit(w.id), variant="ghost", color_scheme="gray",
                            width="100%", height="auto", padding="15px 8px", flex_wrap="wrap",
                            justify_content="flex-start", border_bottom=f"1px solid {s.LINE}",
                        )),
                    ),
                ),
            ),
        ),
        rx.hstack(
            rx.icon("info", size=13, color=s.MUTED),
            s.text("Lanes organize attention. Artifact stages track the lifecycle. Demo cards are not approvals.",
                   size="1"), spacing="2", align="center",
        ),
        spacing="5", width="100%",
    )


def _message(message: rx.Var[Message]) -> rx.Component:
    is_user = message.role == "you"
    return rx.hstack(
        s.mark(rx.cond(is_user, "ME", "AI"), "gray", "30px"),
        rx.vstack(
            rx.hstack(
                rx.text(rx.cond(is_user, "You", "Studio assistant"), size="2", weight="medium"),
                rx.cond(~is_user, s.badge(rx.cond(message.role == "demo", "Simulated reply", "Sample message"))),
                wrap="wrap", align="center",
            ),
            rx.text(message.text, size="2", white_space="pre-wrap", line_height="1.9",
                    overflow_wrap="anywhere"),
            spacing="2", width="100%", min_width="0",
        ),
        width="100%", align="start", spacing="3", padding="18px 0",
    )


def _sessions() -> rx.Component:
    return rx.vstack(
        s.heading("Think it through.", "A conversation with context. A little more room to explore.",
                  rx.button(rx.icon("plus", size=16), "New conversation",
                            on_click=P.new_session, disabled=P.workspace_id == "")),
        rx.grid(
            rx.vstack(
                s.eyebrow("CONVERSATIONS"),
                rx.foreach(P.workspace_sessions, lambda c: rx.button(
                    rx.vstack(
                        rx.hstack(rx.icon("message-square", size=14),
                                  rx.text(c.title, size="2", weight="medium", text_align="left"),
                                  align="start"),
                        s.text(c.subtitle, size="1", text_align="left"),
                        spacing="2", width="100%", align="start",
                    ),
                    id="session-" + c.id, on_click=P.choose_session(c.id),
                    variant="ghost", color_scheme="gray", width="100%", height="auto",
                    padding="14px", border_radius="10px",
                    background=rx.cond(P.session_id == c.id, rx.color("iris", 3), "transparent"),
                )),
                rx.cond(P.workspace_sessions.length() == 0, s.text("No conversations yet.", size="1")),
                spacing="3", padding="18px", background=s.SURFACE, border_radius="14px",
                min_width="0", width="100%", align="stretch",
            ),
            s.panel(
                rx.hstack(
                    rx.vstack(rx.heading(P.current_session.title, size="4", weight="medium"),
                              s.text(P.current_workspace.name + " / chat-only preview", size="1"),
                              spacing="1"),
                    rx.spacer(), s.badge("No tools", "grass"), width="100%", align="center",
                    padding_bottom="20px", border_bottom=f"1px solid {s.LINE}",
                ),
                rx.box(
                    rx.foreach(P.current_session.messages, _message),
                    rx.cond(P.session_id == "", s.text("Start a demo conversation to try the chat flow.")),
                    min_height="300px", max_height="480px", overflow_y="auto",
                    padding="10px 0", role="log", aria_label="Conversation messages",
                ),
                rx.box(
                    rx.text_area(
                        id="chat-prompt", placeholder="Think out loud...",
                        value=P.prompt, on_change=P.set_prompt, aria_label="Message",
                        width="100%", min_height="90px", variant="soft",
                        disabled=P.session_id == "",
                    ),
                    rx.hstack(
                        s.text("Scripted preview / no tokens spent", size="1"), rx.spacer(),
                        rx.button("Send", rx.icon("arrow-up", size=15), id="send-demo",
                                  on_click=P.send_message, disabled=(P.session_id == "") | (P.prompt == "")),
                        width="100%", align="center", margin_top="12px", wrap="wrap",
                    ),
                    border_top=f"1px solid {s.LINE}", padding_top="18px",
                ),
                padding=rx.breakpoints(initial="16px", md="24px"),
            ),
            grid_template_columns=rx.breakpoints(initial="1fr", md="240px minmax(0, 1fr)"),
            gap="4", width="100%", align_items="start",
        ),
        spacing="5", width="100%",
    )


def _activity() -> rx.Component:
    return rx.vstack(
        s.heading("Nothing behind the curtain.", "A readable trail of the work, and what it took to get here."),
        _metrics(),
        rx.grid(
            s.panel(
                s.section_head("Workspace timeline", s.badge("Sample events")),
                rx.foreach(P.workspace_activity, _activity_row),
                rx.cond(P.workspace_activity.length() == 0, s.text("No activity in this demo workspace yet.")),
            ),
            s.panel(
                s.section_head("Usage by work item", s.badge("Illustrative", "blue")),
                rx.foreach(P.workspace_work, lambda w: rx.box(
                    rx.hstack(s.text(w.id, size="1"), rx.spacer(),
                              s.text(w.tokens.to_string() + " tokens", size="1"), width="100%"),
                    rx.progress(value=w.tokens, max=40000, size="1", color_scheme="iris", margin_top="10px"),
                    s.text(w.title, size="1", margin_top="7px"),
                    padding="12px 0",
                )),
                s.text("Fixture values only. Bars share a 40,000-token scale; no usage is measured "
                       "and running a simulation adds no tokens.", size="1", margin_top="18px"),
            ),
            columns=rx.breakpoints(initial="1", xl="2"), gap="4", width="100%", align_items="start",
        ),
        spacing="5", width="100%",
    )


def _settings_row(label: str, description: str, control: rx.Component) -> rx.Component:
    return rx.flex(
        rx.vstack(rx.text(label, size="2", weight="medium"),
                  s.text(description, size="1", max_width="440px"), spacing="1"),
        rx.spacer(), control, width="100%", gap="4", align="center", wrap="wrap",
        padding="20px 0", border_bottom=f"1px solid {s.LINE}",
    )


def _settings() -> rx.Component:
    return rx.vstack(
        s.heading("Make it feel like yours.", "A considered default. A few thoughtful choices."),
        s.panel(
            s.section_head("Appearance", rx.icon("palette", size=18, color=s.MUTED)),
            _settings_row(
                "Color mode", "Choose the light you like to work in. Saved by Reflex in this browser.",
                rx.hstack(
                    rx.button(rx.icon("sun", size=16), "Light", on_click=set_color_mode("light"),
                              variant=rx.color_mode_cond("solid", "soft")),
                    rx.button(rx.icon("moon", size=16), "Dark", on_click=set_color_mode("dark"),
                              variant=rx.color_mode_cond("soft", "solid")), spacing="2",
                ),
            ),
            _settings_row(
                "Board density", "Give ideas some breathing room, or bring more into view.",
                rx.hstack(
                    rx.button("Comfortable", on_click=P.set_density("comfortable"), size="2",
                              variant=rx.cond(P.density == "comfortable", "solid", "soft")),
                    rx.button("Compact", on_click=P.set_density("compact"), size="2",
                              variant=rx.cond(P.density == "compact", "solid", "soft")),
                    spacing="2",
                ),
            ),
        ),
        rx.grid(
            s.panel(
                s.section_head("Local by design", rx.icon("monitor", size=18, color=s.MUTED)),
                s.text("Illustrative environment, not a live machine report.", size="1"),
                _settings_row("Frontend", "Python components, compiled by Reflex.", s.badge("Reflex")),
                _settings_row("Access", "The real app binds to loopback only.", s.badge("Local-first", "grass")),
                _settings_row("Working root", "Configured outside the UI via COS_WORKING_DIR.",
                              s.badge("Read only")),
            ),
            s.panel(
                s.section_head("Clear boundaries", rx.icon("shield-check", size=18, color=s.MUTED)),
                s.text("Baseline policy reference, not a claim that runtime isolation has been verified.",
                       size="1"),
                _settings_row("Chat", "No tools granted by default.", s.badge("Chat only", "grass")),
                _settings_row("Autonomous work", "Real grants are bounded by stage; the preview grants nothing.",
                              s.badge("Demo only", "iris")),
                _settings_row("Review & ship", "Skill behavior is a separate open task. Ship is not authorized.",
                              s.badge("Not connected", "amber")),
            ),
            columns=rx.breakpoints(initial="1", xl="2"), gap="4", width="100%",
        ),
        s.panel(
            rx.hstack(rx.icon("flask-conical", size=19, color=rx.color("iris", 11)),
                      rx.heading("A prototype, on purpose.", size="4", weight="medium")),
            s.text("This is a space to judge the experience before connecting real operations. "
                   "Workspace changes, work items, conversations and runs are examples held in UI state. "
                   "They are not durable; reloading may restore the sample data. "
                   "Nothing here means the design has been approved or shipped.",
                   margin_top="12px", max_width="800px", line_height="1.8"),
            rx.link("Open the existing app", href="/", margin_top="16px", display="inline-block", size="2"),
            background=rx.color("iris", 2),
        ),
        spacing="5", width="100%",
    )


def _detail_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.box(
                rx.hstack(
                    s.text(P.current_unit.id, size="1", font_family="ui-monospace, monospace"),
                    s.badge(P.current_unit.stage, P.current_unit.color),
                    rx.spacer(),
                    rx.dialog.close(s.icon_button("x", "Close work detail")),
                    width="100%", align="center",
                ),
                rx.dialog.title(P.current_unit.title, size="6", weight="medium",
                                margin_top="24px", line_height="1.3"),
                rx.dialog.description(P.current_unit.summary, size="2", color=s.MUTED, margin_top="12px"),
                padding="28px",
            ),
            rx.tabs.root(
                rx.tabs.list(
                    rx.tabs.trigger("Overview", value="overview"),
                    rx.tabs.trigger("Artifacts", value="artifacts"),
                    rx.tabs.trigger("Timeline", value="timeline"),
                    width="100%", padding="0 24px",
                ),
                rx.tabs.content(
                    rx.vstack(
                        rx.flex(
                            s.badge(P.current_unit.lane, P.current_unit.color),
                            s.badge("Example work item"), gap="2", wrap="wrap",
                        ),
                        rx.heading("The next meaningful step", size="4", weight="medium"),
                        s.text("Explore this step in the preview. Running it simulates visible progress, "
                               "but does not read your repository, generate files or accept an artifact.",
                               line_height="1.8"),
                        _settings_row(
                            "Execution mode", "A UI choice only. No tools are granted.",
                            rx.segmented_control.root(
                                rx.segmented_control.item("Manual", value="manual"),
                                rx.segmented_control.item("Auto", value="autonomous"),
                                value=P.current_unit.mode, on_change=P.set_mode, size="1",
                            ),
                        ),
                        rx.callout(
                            "Agent readiness is not human approval. Review and ship behavior is still "
                            "being defined in separate work.",
                            icon="info", color_scheme="iris", variant="soft", size="1",
                        ),
                        rx.button(
                            rx.icon("play", size=15), "Run simulation", id="run-demo",
                            on_click=P.simulate_run,
                            disabled=(P.run_unit != "") | (P.current_unit.stage == "ship"),
                            loading=P.run_unit == P.unit_id, width="100%",
                        ),
                        rx.cond(P.current_unit.stage == "ship",
                                s.text("Ship is not authorized in this preview.", size="1")),
                        rx.cond((P.run_log.length() > 0) & (P.log_unit == P.unit_id),
                                s.panel(
                                    s.eyebrow("SIMULATED OUTPUT / LATEST DEMO RUN"),
                                    rx.foreach(P.run_log, lambda line: rx.text(
                                        line, size="1", font_family="ui-monospace, monospace",
                                        margin_top="12px", overflow_wrap="anywhere",
                                    )),
                                    role="log", aria_label="Simulation output",
                                )),
                        spacing="4", padding="26px", width="100%", align="start",
                    ),
                    value="overview",
                ),
                rx.tabs.content(
                    rx.vstack(
                        rx.hstack(rx.icon("file-text", size=17, color=s.MUTED),
                                  rx.text(P.current_unit.stage + ".md", size="2", weight="medium"),
                                  rx.spacer(), s.badge("Demo artifact", "amber"), width="100%"),
                        rx.markdown(P.artifact, width="100%"),
                        spacing="5", padding="26px", width="100%",
                    ),
                    value="artifacts",
                ),
                rx.tabs.content(
                    rx.vstack(
                        s.text("Illustrative history, not a live audit log.", size="1"),
                        *[
                            rx.hstack(
                                rx.center(rx.icon(icon, size=16, color=rx.color("iris", 11)),
                                          width="34px", height="34px", border_radius="50%",
                                          background=rx.color("iris", 3), flex_shrink="0"),
                                rx.vstack(rx.text(title, size="2", weight="medium"),
                                          s.text(description, size="1"), spacing="1"),
                                align="start", spacing="3", padding="12px 0",
                            )
                            for icon, title, description in (
                                ("lightbulb", "An observation became an intent", "Sample / origin recorded"),
                                ("file-check", "Intent accepted by agent", "Sample / readiness, not human approval"),
                                ("git-branch", "A direction took shape", "Sample / spec and plan explored"),
                                ("circle-dot", "Your perspective belongs here", "No human approval is recorded"),
                            )
                        ],
                        spacing="4", padding="26px", width="100%", align="start",
                    ),
                    value="timeline",
                ),
                value=P.detail_tab, on_change=P.set_detail_tab, width="100%",
            ),
            position="fixed", right="0", top="0", left="auto", bottom="0",
            transform="none", width="min(620px, 100vw)", max_width="100vw", height="100dvh",
            max_height="100dvh", border_radius="0", padding="0", overflow_y="auto",
            background=s.CANVAS, aria_label="Work detail",
        ),
        open=P.unit_id != "", on_open_change=P.toggle_detail,
    )


def _workspace_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title(rx.cond(P.editing_workspace == "", "A new place to build.", "Make it yours.")),
            rx.dialog.description("This changes the demo only. No folders or repositories are touched.",
                                  size="2", margin_top="8px"),
            rx.vstack(
                rx.el.label("Workspace name", html_for="workspace-name", font_size="13px"),
                rx.input(id="workspace-name", placeholder="e.g. My next big idea",
                         value=P.workspace_name, on_change=P.set_workspace_name, width="100%", max_length=60),
                rx.el.label("A short description", html_for="workspace-description", font_size="13px"),
                rx.text_area(id="workspace-description", placeholder="What are you making?",
                             value=P.workspace_description, on_change=P.set_workspace_description,
                             width="100%"),
                rx.cond(P.form_error != "", rx.callout(P.form_error, color_scheme="red", role="alert")),
                rx.hstack(
                    rx.dialog.close(rx.button("Cancel", variant="soft", color_scheme="gray")),
                    rx.button("Save workspace", id="save-workspace", on_click=P.save_workspace),
                    justify="end", width="100%",
                ),
                spacing="3", width="100%", margin_top="24px",
            ),
            max_width="480px",
        ),
        open=P.workspace_form, on_open_change=P.toggle_workspace_form,
    )


def _remove_dialog() -> rx.Component:
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Remove this demo workspace?"),
            rx.alert_dialog.description(
                "Its example work and conversations will disappear from this preview. "
                "No local folder or real session will be deleted.",
            ),
            rx.hstack(
                rx.alert_dialog.cancel(rx.button("Keep workspace", variant="soft", color_scheme="gray")),
                rx.button("Remove from demo", color_scheme="red", on_click=P.remove_workspace),
                justify="end", spacing="3", margin_top="24px",
            ),
            max_width="460px",
        ),
        open=P.remove_id != "", on_open_change=P.toggle_remove,
    )


def _new_work_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("What is worth working on?"),
            rx.dialog.description("Start with the outcome, not the implementation. Demo only."),
            rx.el.label("Work title", html_for="new-work-title", display="block", margin_top="22px",
                        font_size="13px"),
            rx.input(id="new-work-title", value=P.new_work_title, on_change=P.set_new_work_title,
                     placeholder="e.g. Make the next step easier to find", width="100%",
                     margin_top="8px", max_length=120),
            rx.cond(P.form_error != "", rx.callout(P.form_error, color_scheme="red", margin_top="12px",
                                                 role="alert")),
            rx.hstack(
                rx.dialog.close(rx.button("Not yet", variant="soft", color_scheme="gray")),
                rx.button("Create demo work", on_click=P.create_work),
                justify="end", margin_top="24px", spacing="3",
            ),
            max_width="500px",
        ),
        open=P.new_work_open, on_open_change=P.toggle_new_work,
    )


def _command_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("Find your next step.", size="5"),
            rx.dialog.description("Search screens and work in the selected demo workspace.", size="2"),
            rx.input(rx.input.slot(rx.icon("search", size=17)),
                     id="command-query", placeholder="Where would you like to go?",
                     aria_label="Search screens and work", value=P.command_query, on_change=P.search_commands,
                     margin="20px 0", width="100%"),
            rx.vstack(
                *[
                    rx.cond(
                        rx.Var.create(label.lower()).contains(P.command_query.lower()),
                        rx.button(rx.icon(icon, size=16), label, rx.spacer(),
                                  rx.icon("arrow-up-right", size=14),
                                  aria_label="Go to " + label, on_click=P.navigate(key),
                                  variant="ghost", color_scheme="gray", width="100%",
                                  justify_content="flex-start"),
                    ) for key, label, icon in NAVIGATION
                ],
                rx.foreach(P.command_work, lambda w: rx.button(
                    rx.icon("file-text", size=16), w.title, on_click=P.open_unit(w.id),
                    variant="ghost", color_scheme="gray", width="100%",
                    justify_content="flex-start", height="auto", padding="10px", white_space="normal",
                )),
                spacing="2", width="100%",
            ),
            rx.dialog.close(rx.button("Close", variant="soft", color_scheme="gray", margin_top="20px")),
            max_width="560px",
        ),
        open=P.command_open, on_open_change=P.toggle_command,
    )


def _mobile_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.hstack(rx.dialog.title("COS Studio", size="5"), rx.spacer(),
                      rx.dialog.close(s.icon_button("x", "Close navigation")), width="100%"),
            rx.dialog.description("Your workspace, your next step.", size="2"),
            s.native_select(
                rx.foreach(P.workspaces, lambda w: rx.el.option(w.name, value=w.id)),
                rx.cond(P.workspaces.length() == 0, rx.el.option("No workspace", value="")),
                value=P.workspace_id, on_change=P.choose_workspace,
                aria_label="Mobile active workspace", width="100%", margin="24px 0",
            ),
            _nav(mobile=True),
            rx.button(rx.icon("search", size=16), "Search the studio",
                      on_click=[P.toggle_mobile(False), P.toggle_command(True)],
                      variant="soft", width="100%", margin_top="20px"),
            max_width="360px",
        ),
        open=P.mobile_open, on_open_change=P.toggle_mobile,
    )


def _scenario() -> rx.Component:
    return rx.match(
        P.scenario,
        ("empty", _empty()),
        ("loading", s.panel(rx.vstack(
            rx.spinner(size="3"), rx.heading("Getting things ready", size="5"),
            s.text("A preview of the loading state. No request is running."),
            rx.skeleton(width="80%", height="18px"), rx.skeleton(width="60%", height="18px"),
            rx.button("Back to populated preview", id="restore-preview",
                      on_click=P.preview_scenario("populated"), variant="soft"),
            align="center", spacing="4", padding="60px 10px", width="100%",
        ))),
        ("error", s.panel(rx.vstack(
            rx.icon("unplug", size=34, color=rx.color("amber", 10)),
            rx.heading("Something didn't connect.", size="5"),
            s.text("A simulated error. Your real work is untouched.", text_align="center"),
            rx.button(rx.icon("rotate-cw", size=15), "Try again", id="restore-preview",
                      on_click=P.preview_scenario("populated")),
            align="center", spacing="4", padding="60px 10px", width="100%",
        ))),
        rx.match(
            P.screen,
            ("overview", _overview()), ("workspaces", _workspaces()), ("board", _board()),
            ("sessions", _sessions()), ("activity", _activity()), ("settings", _settings()),
            _overview(),
        ),
    )


def index() -> rx.Component:
    return rx.box(
        rx.flex(
            _sidebar(),
            rx.box(
                _topbar(),
                rx.box(
                    _preview_bar(),
                    rx.cond(P.notice != "",
                            rx.hstack(
                                rx.icon("info", size=16, color=rx.color("iris", 11)),
                                rx.text(P.notice, size="2"), rx.spacer(),
                                s.icon_button("x", "Dismiss message", on_click=P.dismiss_notice),
                                padding="12px 16px", background=rx.color("iris", 3),
                                border_radius="10px", margin_bottom="20px", role="status", align="center",
                            )),
                    _scenario(),
                    rx.flex(
                        s.text("COS STUDIO / CONCEPT 01", size="1", letter_spacing="0.07em"),
                        rx.spacer(), s.text("Built with Python. Designed around your work.", size="1"),
                        width="100%", gap="2", wrap="wrap", padding="36px 0 8px",
                    ),
                    padding=rx.breakpoints(initial="0 18px 20px", md="0 32px 24px", xl="0 40px 24px"),
                    width="100%", max_width="1660px", margin="0 auto",
                ),
                flex="1", min_width="0", width="100%",
            ),
            width="100%", min_height="100dvh", align="start",
        ),
        _detail_dialog(), _workspace_dialog(), _remove_dialog(), _new_work_dialog(),
        _command_dialog(), _mobile_dialog(),
        id="prototype-shell", data_density=P.density,
        background=s.CANVAS, color=s.INK, min_height="100dvh",
        style={"& button": {"cursor": "pointer"}, "& button:disabled": {"cursor": "not-allowed"}},
    )
