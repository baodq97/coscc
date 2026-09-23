"""The six screens, built from Python components.

`spec.md` R8: no hand-written HTML or CSS serves this app. Everything below is Python.

These screens began as a prototype and keep its shape, its spacing and most of its
words. What changed is where every value comes from: the prototype read `prototype_data.py`, and
nothing here reads anything but `StudioState`, which reads `Service`. That swap is the
whole of `spec.md` R12.

Three things are said on the page rather than only in a document, and each is a
requirement rather than a flourish:

- **R17.** Before a step can be started, the screen shows the tools that step would be
  granted and the warning attached to them. A capability that comes from this machine's own
  `gh` login is invisible in an app unless somebody puts it next to the button.
- **C8.** The prototype's equivalent button ran a simulation and cost nothing. This one calls a
  real model. It says so before it is pressed, not after.
- **C7.** The six prose stages get no tools, so the app writes the artifact from the reply.
  The screen says that plainly rather than letting it look like the agent wrote the file.
"""

from __future__ import annotations

import reflex as rx
from reflex.style import set_color_mode

from coscc import studio as s
from coscc.state import (
    LANE_COLOR,
    NAVIGATION,
    Cell,
    Event,
    GrantRow,
    Knob,
    Message,
    Run,
    StudioState,
    Unit,
    Workspace,
)

P = StudioState


# --- chrome ------------------------------------------------------------------


def _nav(mobile: bool = False) -> rx.Component:
    return rx.vstack(
        *[
            rx.button(
                rx.icon(icon, size=18),
                rx.text(label, size="2", weight=rx.cond(P.screen == key, "medium", "regular")),
                rx.spacer(),
                rx.cond(
                    (key == "board") & (P.attention_count > 0),
                    s.badge(P.attention_count.to_string(), "amber"),
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


def _workspace_select(**props) -> rx.Component:
    return s.native_select(
        rx.foreach(P.workspaces, lambda w: rx.el.option(w.name, value=w.id)),
        rx.cond(P.workspaces.length() == 0, rx.el.option("No workspace", value="")),
        value=P.cwd, on_change=P.choose_workspace, **props,
    )


def _sidebar() -> rx.Component:
    return rx.vstack(
        rx.box(_brand(), padding="10px 10px 26px"),
        rx.box(
            s.eyebrow("WORKSPACE"),
            rx.hstack(
                s.mark(P.current_workspace.initials, P.current_workspace.color, size="32px"),
                _workspace_select(id="workspace-switcher", aria_label="Active workspace",
                                  width="100%"),
                width="100%", align="center", margin_top="12px",
            ),
            padding="0 8px 24px", width="100%",
        ),
        _nav(),
        rx.spacer(),
        s.panel(
            rx.hstack(
                rx.cond(
                    P.loopback_only,
                    rx.icon("shield-check", size=16, color=rx.color("grass", 11)),
                    rx.icon("shield-alert", size=16, color=rx.color("amber", 11)),
                ),
                rx.text(rx.cond(P.loopback_only, "Local only", "Open on the network"),
                        size="2", weight="medium")),
            s.text(
                rx.cond(
                    P.loopback_only,
                    "Loopback, and chat sessions with no tools by default.",
                    "Anyone who can reach this port can use this app. There is no login. "
                    "Chat sessions still have no tools by default.",
                ),
                size="1", margin_top="8px"),
            rx.button("What this can do", rx.icon("arrow-up-right", size=14),
                      on_click=P.navigate("settings"), variant="ghost", size="1", margin_top="12px"),
            padding="14px", background=rx.color("grass", 2),
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
        rx.cond(P.loading, rx.spinner(size="2")),
        # `spec.md` R23: the mode has to be changeable from the page and survive a reload.
        # `scripts/verify_0003.py` looks for this id.
        rx.box(rx.color_mode.button(), id="color-mode"),
        align="center", gap="12px", width="100%", min_height="68px",
        padding=rx.breakpoints(initial="12px 18px", md="12px 32px"),
        border_bottom=f"1px solid {s.LINE}", background=s.CANVAS,
    )


def _status_bar() -> rx.Component:
    """Where the data is. `spec.md` C1: two roots, and a backup of one is not both."""
    return rx.flex(
        rx.hstack(
            rx.icon("folder", size=13, color=s.MUTED),
            s.text("workspaces", size="1"),
            s.text(rx.cond(P.working_dir != "", P.working_dir, "not set"),
                   size="1", id="working-dir", font_family="ui-monospace, monospace"),
            spacing="2", align="center", min_width="0",
        ),
        rx.hstack(
            rx.icon("database", size=13, color=s.MUTED),
            s.text("data", size="1"),
            s.text(P.data_dir, size="1", id="data-dir",
                   font_family="ui-monospace, monospace"),
            spacing="2", align="center", min_width="0",
        ),
        rx.spacer(),
        s.text(P.workspaces.length().to_string() + " workspace(s)", size="1",
               id="workspace-count"),
        width="100%", align="center", wrap="wrap", gap="16px", padding="12px 0 20px",
    )


def _banners() -> rx.Component:
    return rx.vstack(
        rx.cond(
            P.error != "",
            rx.callout(P.error, icon="triangle_alert", color_scheme="red", variant="surface",
                       width="100%", role="alert", id="page-error"),
        ),
        rx.cond(
            P.notice != "",
            rx.hstack(
                rx.icon("info", size=16, color=rx.color("iris", 11)),
                rx.text(P.notice, size="2"), rx.spacer(),
                s.icon_button("x", "Dismiss message", on_click=P.dismiss_notice),
                padding="12px 16px", background=rx.color("iris", 3), border_radius="10px",
                role="status", align="center", width="100%", id="page-notice",
            ),
        ),
        spacing="3", width="100%",
        margin_bottom=rx.cond((P.error != "") | (P.notice != ""), "20px", "0"),
    )


# --- shared pieces -----------------------------------------------------------


def _metrics() -> rx.Component:
    return rx.grid(
        s.stat("Active work", P.active_count.to_string(), "Units with an artifact and no end yet",
               "layers"),
        s.stat("Needs attention", P.attention_count.to_string(),
               "The gate is closed on the next step", "circle-dot", "amber"),
        s.stat("Tokens", P.usage_total_tokens, "Billed for this workspace, from the run log",
               "sparkles", "blue"),
        s.stat("Cost", P.usage_total_usd, "Added up from each finished run", "wallet", "grass"),
        columns=rx.breakpoints(initial="2", lg="4"), gap="12px", width="100%",
        id="workspace-metrics",
    )


def _event_row(event: rx.Var[Event]) -> rx.Component:
    return rx.hstack(
        rx.center(rx.icon(event.icon, size=16, color=rx.color(event.color, 11)),
                  width="34px", height="34px", border_radius="50%",
                  background=rx.color(event.color, 3), flex_shrink="0"),
        rx.vstack(rx.text(event.title, size="2", weight="medium"),
                  s.text(event.detail, size="1", overflow_wrap="anywhere"),
                  spacing="1", min_width="0"),
        rx.spacer(),
        s.text(event.time, size="1", text_align="right", max_width="150px",
               font_family="ui-monospace, monospace"),
        width="100%", align="center", padding="14px 0",
        border_bottom=f"1px solid {s.LINE}", data_testid="activity-row",
    )


def _empty_board() -> rx.Component:
    """Two reasons a board is empty, and they must not look the same (`spec.md` C5).

    `0014` added a third, and it is the one that looks most like breakage: a workspace with
    a `.cos/` of its own now shows **nothing**, because work units moved into the product's
    own store. That is `0013`'s decision arriving — nothing of coscc's goes into a
    repository a team shares — and `0014` `spec.md` C1 says it has to be said here rather
    than left to look like a fault. `_start_unit` above is the answer to it: there is
    nothing here yet because nothing has been started here yet.
    """
    return s.panel(
        rx.vstack(
            rx.center(rx.icon("sprout", size=30, color=rx.color("iris", 10)),
                      width="76px", height="76px", border_radius="24px",
                      background=rx.color("iris", 3), margin_bottom="6px"),
            rx.heading(rx.cond(P.has_workspace, "Nothing here yet.", "No workspace chosen."),
                       size="6", weight="medium"),
            s.text(
                rx.cond(
                    P.board_note != "",
                    P.board_note,
                    rx.cond(
                        P.has_workspace,
                        "This workspace has no work units.",
                        "Add a workspace, or set COS_WORKING_DIR and restart.",
                    ),
                ),
                text_align="center", max_width="420px", id="board-note",
            ),
            rx.button("Manage workspaces", on_click=P.navigate("workspaces"),
                      variant="soft", color_scheme="gray", margin_top="12px"),
            spacing="3", align="center", padding="64px 10px", width="100%",
        ),
        data_testid="empty-board",
    )


# --- overview ----------------------------------------------------------------


def _overview() -> rx.Component:
    return rx.vstack(
        rx.box(s.eyebrow("YOUR WORK, IN FOCUS"), margin_top="6px"),
        s.heading("Build with intent.", "Less context switching. More meaningful progress.",
                  rx.button("Open board", rx.icon("arrow-right", size=16),
                            on_click=P.navigate("board"), size="2", id="open-board")),
        _metrics(),
        rx.grid(
            s.panel(
                s.section_head("Pick up where you left off", s.badge("IN MOTION", "iris")),
                rx.cond(
                    P.in_progress.length() > 0,
                    rx.foreach(P.in_progress[:1], lambda u: rx.vstack(
                        s.text(u.id, size="1", font_family="ui-monospace, monospace"),
                        rx.heading(u.title, size="6", weight="medium", letter_spacing="-0.025em"),
                        s.text("Next: " + u.summary, max_width="460px", line_height="1.75"),
                        rx.box(
                            rx.progress(value=u.progress, max=100, color_scheme="iris", size="1"),
                            rx.hstack(s.text("Stages with an artifact", size="1"), rx.spacer(),
                                      s.text(u.progress.to_string() + "%", size="1"),
                                      margin_top="9px", width="100%"),
                            width="100%", margin_top="12px",
                        ),
                        rx.hstack(
                            rx.button("Open this work", rx.icon("arrow-up-right", size=15),
                                      on_click=P.open_unit(u.id), variant="soft"),
                            s.badge(u.mode, "gray"), width="100%", margin_top="12px", wrap="wrap",
                        ),
                        spacing="3", width="100%", align="start",
                    )),
                    s.text("Nothing is in progress in this workspace."),
                ),
                background=f"linear-gradient(135deg, {rx.color('iris', 2)}, {s.SURFACE})",
                padding=rx.breakpoints(initial="22px", md="28px"),
            ),
            s.panel(
                s.section_head("Make space for good work",
                               rx.icon("sparkles", size=18, color=s.MUTED)),
                rx.heading("You set the direction.\nAI helps with the distance.", size="5",
                           weight="medium", white_space="pre-line", line_height="1.4"),
                s.text("Keep decisions human, actions visible, and every next step grounded "
                       "in what came before.", margin_top="16px", line_height="1.8"),
                rx.box(height="22px"),
                rx.button("Start a conversation", rx.icon("arrow-right", size=15),
                          on_click=P.navigate("sessions"), variant="ghost"),
                padding=rx.breakpoints(initial="22px", md="28px"),
            ),
            columns=rx.breakpoints(initial="1", lg="2"), gap="16px", width="100%",
        ),
        rx.grid(
            s.panel(
                s.section_head("Recent activity",
                               rx.button("View all", on_click=P.navigate("activity"),
                                         variant="ghost", size="1")),
                rx.foreach(P.events[:3], _event_row),
                rx.cond(P.events.length() == 0,
                        s.text("Nothing has been run in this workspace yet.")),
            ),
            s.panel(
                s.section_head("Your workspaces",
                               rx.button("Manage", on_click=P.navigate("workspaces"),
                                         variant="ghost", size="1")),
                rx.foreach(P.workspaces, lambda w: rx.button(
                    s.mark(w.initials, w.color, "36px"),
                    rx.vstack(rx.text(w.name, size="2", weight="medium"),
                              s.text(w.path, size="1"), spacing="0", align="start"),
                    rx.spacer(),
                    rx.cond(w.missing, s.badge("missing", "red")),
                    rx.icon("arrow-up-right", size=15, color=s.MUTED),
                    on_click=P.open_workspace(w.id), variant="ghost", color_scheme="gray",
                    width="100%", height="auto", padding="13px 4px",
                    justify_content="flex-start",
                )),
                rx.cond(P.workspaces.length() == 0, s.text("No workspaces yet.")),
            ),
            columns=rx.breakpoints(initial="1", lg="2"), gap="16px", width="100%",
        ),
        spacing="5", width="100%", align="start",
    )


# --- workspaces --------------------------------------------------------------


def _workspace_card(workspace: rx.Var[Workspace]) -> rx.Component:
    return s.panel(
        rx.hstack(
            s.mark(workspace.initials, workspace.color, "44px"), rx.spacer(),
            rx.cond(P.cwd == workspace.id, s.badge("Current", "iris")),
            rx.cond(workspace.missing, s.badge("Missing", "red")),
            rx.cond(workspace.source == "env", s.badge("env", "gray")),
            rx.cond(
                workspace.removable,
                rx.hstack(
                    s.icon_button("pencil", "Edit " + workspace.name,
                                  on_click=P.edit_workspace(workspace.name), size="1"),
                    s.icon_button("refresh-cw", "Pull " + workspace.name,
                                  on_click=P.pull(workspace.name), size="1", loading=P.busy),
                    s.icon_button("trash-2", "Remove " + workspace.name,
                                  on_click=P.request_remove(workspace.name), size="1"),
                    spacing="1",
                ),
            ),
            width="100%", align="center", wrap="wrap",
        ),
        rx.heading(workspace.name, size="5", weight="medium", margin_top="22px",
                   overflow_wrap="anywhere"),
        s.text(rx.cond(workspace.label != "", workspace.label, "No label yet."),
               margin_top="8px", min_height="44px", overflow_wrap="anywhere"),
        rx.hstack(rx.icon("folder-git-2", size=14, color=s.MUTED),
                  s.text(workspace.path, size="1", overflow_wrap="anywhere"),
                  spacing="2", margin_top="20px", align="start"),
        rx.box(height="1px", background=s.LINE, margin="20px 0 16px"),
        rx.hstack(
            s.text(rx.cond(workspace.source == "env",
                           "From COS_WORKSPACES — read only here", "Stored"), size="1"),
            rx.spacer(),
            rx.button("Open workspace", rx.icon("arrow-right", size=14),
                      aria_label="Open " + workspace.name,
                      on_click=P.open_workspace(workspace.id), variant="ghost", size="1"),
            width="100%", wrap="wrap",
        ),
        data_testid="workspace-card",
    )


def _workspaces_screen() -> rx.Component:
    return rx.vstack(
        s.heading("A home for every project.",
                  "Separate contexts. One place to bring it all together.",
                  rx.button(rx.icon("plus", size=16), "New workspace", id="new-workspace",
                            on_click=P.edit_workspace(""),
                            disabled=P.working_dir == "")),
        rx.cond(
            P.working_dir == "",
            rx.callout(
                "No working folder is set, so no workspace can be added or removed. "
                "Set COS_WORKING_DIR and restart — it is deliberately not settable here.",
                icon="info", color_scheme="amber", variant="surface", width="100%",
            ),
        ),
        rx.input(rx.input.slot(rx.icon("search", size=15)), placeholder="Find a workspace...",
                 value=P.workspace_query, on_change=P.search_workspaces,
                 aria_label="Find a workspace", max_width="360px", width="100%",
                 variant="surface"),
        rx.cond(
            P.filtered_workspaces.length() > 0,
            rx.grid(rx.foreach(P.filtered_workspaces, _workspace_card),
                    columns=rx.breakpoints(initial="1", sm="2", lg="3"), gap="16px",
                    width="100%", id="workspace-grid"),
            s.panel(rx.heading("No workspaces found", size="4"),
                    s.text("Try a different search, or add one.", margin_top="8px")),
        ),
        s.text("Removing a workspace takes it off this list. The folder on disk is never "
               "deleted.", size="1"),
        spacing="5", width="100%",
    )


# --- board -------------------------------------------------------------------


def _unit_card(unit: rx.Var[Unit]) -> rx.Component:
    return rx.el.button(
        rx.hstack(
            s.text(unit.id, size="1", font_family="ui-monospace, monospace"),
            rx.spacer(),
            rx.cond(unit.mode == "autonomous",
                    rx.icon("sparkles", size=14, color=rx.color("iris", 10))),
            width="100%",
        ),
        rx.text(unit.title, size="2", weight="medium", line_height="1.6",
                color=s.INK, margin_top="13px"),
        s.text("Next: " + unit.summary, size="1", line_height="1.7", margin_top="7px",
               display=rx.cond(P.density == "compact", "none", "block")),
        rx.hstack(
            s.badge(unit.stage, unit.color),
            rx.cond(unit.problems != "", s.badge("problem", "red")),
            rx.spacer(),
            rx.center(rx.text(unit.owner, size="1", weight="medium"),
                      width="27px", height="27px", border_radius="50%",
                      background=rx.color("gray", 4), color=s.MUTED),
            width="100%", align="center", margin_top="18px",
        ),
        id="unit-" + unit.id, data_testid="work-card", type="button",
        aria_label="Open " + unit.title, on_click=P.open_unit(unit.id),
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
        rx.foreach(items, _unit_card),
        rx.cond(items.length() == 0,
                rx.center(s.text("Nothing here", size="1", text_align="center"),
                          padding="26px 10px", border=f"1px dashed {s.LINE}",
                          border_radius="10px", width="100%")),
        spacing="3", align="stretch", width="100%", min_width="0",
        padding="12px", border_radius="13px", background=s.SURFACE,
    )


def _start_unit() -> rx.Component:
    """`0014` R8. The control that was missing entirely.

    Until `0014` a work unit could only be made by typing `cos.mjs new-path` in a terminal
    and creating the directory by hand, so the Board could list work but never start any —
    every unit it had ever shown was made outside the app.

    The brief is not optional here and `state.create_unit` says why: it becomes the unit's
    `idea.md`, which is the only thing the intent step has to work from. A unit started
    without one spends a paid step on an empty prompt.
    """
    return s.panel(
        rx.vstack(
            rx.hstack(
                rx.icon("plus", size=16, color=rx.color("iris", 10)),
                rx.heading("Start a work unit", size="4", weight="medium"),
                rx.spacer(),
                rx.cond(
                    P.branch != "",
                    rx.hstack(
                        rx.icon("git-branch", size=14),
                        s.text(P.branch, size="1"),
                        spacing="2", align="center",
                    ),
                    rx.fragment(),
                ),
                width="100%", align="center", spacing="2",
            ),
            s.text(
                "The number comes from the harness. Say the problem in your own words — "
                "that becomes the unit's idea.md, and the intent step reads it.",
                size="1", margin_top="2px",
            ),
            rx.input(
                placeholder="short-name-for-the-problem",
                value=P.new_slug, on_change=P.set_new_slug,
                aria_label="Name for the work unit", id="new-unit-slug",
                width="100%", margin_top="8px",
            ),
            rx.text_area(
                placeholder="What is wrong, in your own words.",
                value=P.new_brief, on_change=P.set_new_brief,
                aria_label="What the problem is", id="new-unit-brief",
                width="100%", rows="3",
            ),
            rx.button(
                rx.icon("sprout", size=15), "Start it",
                on_click=P.create_unit, loading=P.starting,
                id="new-unit-start", size="2",
            ),
            width="100%", align="start", spacing="2",
        ),
        width="100%",
    )


def _board() -> rx.Component:
    return rx.vstack(
        s.heading("Work board", "From an idea to something real. One clear step at a time."),
        rx.cond(P.has_workspace, _start_unit(), rx.fragment()),
        rx.flex(
            rx.input(rx.input.slot(rx.icon("search", size=15)), id="work-search",
                     placeholder="Search work...", value=P.query, on_change=P.search_work,
                     aria_label="Search work",
                     width=rx.breakpoints(initial="100%", sm="240px")),
            rx.segmented_control.root(
                *[rx.segmented_control.item(label, value=label)
                  for label in ("All work", "Autonomous", "Needs review")],
                value=P.focus, on_change=P.filter_work, size="1",
            ),
            rx.spacer(),
            rx.segmented_control.root(
                rx.segmented_control.item(
                    rx.hstack(rx.icon("columns-3", size=14), rx.text("Board"),
                              spacing="2", align="center"),
                    value="Board",
                ),
                rx.segmented_control.item(
                    rx.hstack(rx.icon("list", size=14), rx.text("List"),
                              spacing="2", align="center"),
                    value="List",
                ),
                value=P.board_view, on_change=P.set_board_view, size="1",
            ),
            width="100%", align="center", gap="12px", wrap="wrap",
        ),
        rx.cond(
            P.units.length() == 0,
            _empty_board(),
            rx.cond(
                P.visible_units.length() == 0,
                s.panel(rx.heading("No matching work", size="4"),
                        s.text("Try a different search or choose All work.", margin_top="8px")),
                rx.cond(
                    P.board_view == "Board",
                    rx.grid(
                        *(
                            _lane(name, units, LANE_COLOR[name])
                            for name, units in (
                                ("Planned", P.planned),
                                ("In progress", P.in_progress),
                                ("Needs review", P.needs_review),
                                ("Complete", P.complete),
                            )
                        ),
                        columns=rx.breakpoints(initial="1", sm="2", lg="4"),
                        gap="12px", width="100%", align_items="start", id="board-grid",
                    ),
                    s.panel(
                        rx.foreach(P.visible_units, lambda u: rx.button(
                            s.text(u.id, size="1", min_width="110px",
                                   font_family="ui-monospace, monospace"),
                            rx.text(u.title, size="2", weight="medium", text_align="left"),
                            rx.spacer(), s.badge(u.stage, u.color), s.text(u.lane, size="1"),
                            on_click=P.open_unit(u.id), variant="ghost", color_scheme="gray",
                            width="100%", height="auto", padding="15px 8px", flex_wrap="wrap",
                            justify_content="flex-start", border_bottom=f"1px solid {s.LINE}",
                        )),
                        id="board-list",
                    ),
                ),
            ),
        ),
        rx.hstack(
            rx.icon("info", size=13, color=s.MUTED),
            s.text("Lanes organise attention. Stage statuses come from each artifact's "
                   "Status line, never from the run log.", size="1"),
            spacing="2", align="center",
        ),
        spacing="5", width="100%",
    )


# --- sessions ----------------------------------------------------------------


def _message(message: rx.Var[Message]) -> rx.Component:
    is_user = message.role == "user"
    return rx.hstack(
        s.mark(rx.cond(is_user, "ME", "AI"), "gray", "30px"),
        rx.vstack(
            rx.text(rx.cond(is_user, "You", "Claude"), size="2", weight="medium"),
            rx.text(message.text, size="2", white_space="pre-wrap", line_height="1.9",
                    overflow_wrap="anywhere"),
            spacing="2", width="100%", min_width="0",
        ),
        width="100%", align="start", spacing="3", padding="18px 0",
        data_testid="chat-message",
    )


def _sessions() -> rx.Component:
    return rx.vstack(
        s.heading("Think it through.",
                  "A conversation with context. A little more room to explore.",
                  rx.button(rx.icon("plus", size=16), "New conversation", id="new-session",
                            on_click=P.new_session, disabled=~P.has_workspace)),
        rx.grid(
            rx.vstack(
                s.eyebrow("CONVERSATIONS"),
                rx.foreach(P.conversations, lambda c: rx.button(
                    rx.vstack(
                        rx.hstack(rx.icon("message-square", size=14),
                                  rx.text(c.title, size="2", weight="medium", text_align="left"),
                                  align="start"),
                        rx.hstack(
                            s.text(c.subtitle, size="1", text_align="left"),
                            rx.cond(~c.resumable, s.badge("read only", "gray")),
                            spacing="2", wrap="wrap",
                        ),
                        spacing="2", width="100%", align="start",
                    ),
                    id="session-" + c.id, on_click=P.choose_session(c.id),
                    variant="ghost", color_scheme="gray", width="100%", height="auto",
                    padding="14px", border_radius="10px", data_testid="session-row",
                    background=rx.cond(P.session_id == c.id, rx.color("iris", 3), "transparent"),
                )),
                rx.cond(P.conversations.length() == 0,
                        s.text("No conversations in this workspace yet.", size="1")),
                spacing="3", padding="18px", background=s.SURFACE, border_radius="14px",
                min_width="0", width="100%", align="stretch",
            ),
            s.panel(
                rx.hstack(
                    rx.vstack(
                        rx.heading(rx.cond(P.session_id != "", P.session_id, "New conversation"),
                                   size="4", weight="medium",
                                   font_family="ui-monospace, monospace"),
                        s.text(P.current_workspace.name, size="1"),
                        spacing="1", min_width="0",
                    ),
                    rx.spacer(),
                    s.badge("No tools", "grass"),
                    width="100%", align="center", padding_bottom="20px",
                    border_bottom=f"1px solid {s.LINE}", wrap="wrap",
                ),
                rx.box(
                    rx.foreach(P.messages, _message),
                    rx.cond(P.messages.length() == 0,
                            s.text("Say something to start. The session is created on the "
                                   "first message and saved by the SDK, not by this app.")),
                    min_height="300px", max_height="480px", overflow_y="auto",
                    padding="10px 0", role="log", aria_label="Conversation messages",
                    id="chat-log",
                ),
                rx.box(
                    rx.text_area(
                        id="chat-prompt", placeholder="Think out loud...",
                        value=P.prompt, on_change=P.set_prompt, aria_label="Message",
                        width="100%", min_height="90px", variant="soft",
                        disabled=~P.has_workspace,
                    ),
                    rx.hstack(
                        s.text("Chat only — no tools. Each message spends account quota.",
                               size="1"),
                        rx.spacer(),
                        rx.button("Send", rx.icon("arrow-up", size=15), id="send-message",
                                  on_click=P.send, loading=P.sending,
                                  disabled=~P.has_workspace | (P.prompt == "")),
                        width="100%", align="center", margin_top="12px", wrap="wrap",
                    ),
                    border_top=f"1px solid {s.LINE}", padding_top="18px",
                ),
                padding=rx.breakpoints(initial="16px", md="24px"),
            ),
            grid_template_columns=rx.breakpoints(initial="1fr", md="240px minmax(0, 1fr)"),
            gap="16px", width="100%", align_items="start",
        ),
        spacing="5", width="100%",
    )


# --- activity ----------------------------------------------------------------


def _activity() -> rx.Component:
    return rx.vstack(
        s.heading("Nothing behind the curtain.",
                  "A readable trail of the work, and what it took to get here."),
        _metrics(),
        rx.cond(
            ~P.recording,
            rx.callout("No working folder is set, so nothing is being recorded.",
                       icon="info", color_scheme="amber", variant="surface", width="100%"),
        ),
        rx.grid(
            s.panel(
                s.section_head("Workspace timeline", s.badge("From the run log")),
                rx.foreach(P.events, _event_row),
                rx.cond(P.events.length() == 0,
                        s.text("Nothing has been run in this workspace yet.")),
                id="activity-panel",
            ),
            s.panel(
                s.section_head("Usage by work unit", s.badge("Billed", "blue")),
                rx.foreach(P.usage_rows, lambda u: rx.box(
                    rx.hstack(s.text(u.id, size="1",
                                     font_family="ui-monospace, monospace"),
                              rx.spacer(),
                              s.text(u.tokens + " tokens", size="1"),
                              s.text(u.usd, size="1"), width="100%", wrap="wrap"),
                    rx.progress(value=u.token_count, max=P.usage_scale, size="1",
                                color_scheme="iris", margin_top="10px"),
                    s.text(u.title, size="1", margin_top="7px"),
                    padding="12px 0",
                )),
                rx.cond(P.usage_rows.length() == 0,
                        s.text("No run has cost anything here yet.")),
                s.text("Bars share the largest value on this board. Cache reads and writes "
                       "are counted, because they are billed.", size="1", margin_top="18px"),
            ),
            columns=rx.breakpoints(initial="1", lg="2"), gap="16px", width="100%",
            align_items="start",
        ),
        spacing="5", width="100%",
    )


# --- settings ----------------------------------------------------------------


def _settings_row(label, description, control: rx.Component) -> rx.Component:
    return rx.flex(
        rx.vstack(rx.text(label, size="2", weight="medium"),
                  s.text(description, size="1", max_width="440px"), spacing="1", min_width="0"),
        rx.spacer(), control, width="100%", gap="16px", align="center", wrap="wrap",
        padding="20px 0", border_bottom=f"1px solid {s.LINE}",
    )


def _knob_row(knob: rx.Var[Knob]) -> rx.Component:
    return _settings_row(
        knob.name, knob.detail,
        s.badge(knob.value, rx.cond(knob.on, "amber", "grass")),
    )


def _grant_row(grant: rx.Var[GrantRow]) -> rx.Component:
    return rx.box(
        rx.hstack(
            s.badge(grant.stage + " / " + grant.mode, "iris"),
            rx.spacer(),
            s.text(grant.turns + " turns", size="1"),
            s.text("max " + grant.budget, size="1"),
            width="100%", align="center", wrap="wrap",
        ),
        s.text("tools: " + grant.tools, size="1", margin_top="10px", overflow_wrap="anywhere"),
        s.text("commands: " + grant.commands, size="1", margin_top="4px",
               overflow_wrap="anywhere"),
        rx.cond(
            grant.warning != "",
            rx.callout(grant.warning, icon="triangle_alert", color_scheme="amber",
                       variant="surface", size="1", margin_top="12px"),
        ),
        padding="16px 0", border_bottom=f"1px solid {s.LINE}", width="100%",
        data_testid="grant-row",
    )


def _settings() -> rx.Component:
    return rx.vstack(
        s.heading("Make it feel like yours.",
                  "A considered default. A few thoughtful choices."),
        s.panel(
            s.section_head("Appearance", rx.icon("palette", size=18, color=s.MUTED)),
            _settings_row(
                "Color mode", "Choose the light you like to work in. Kept by this browser.",
                rx.hstack(
                    rx.button(rx.icon("sun", size=16), "Light", id="mode-light",
                              on_click=set_color_mode("light"),
                              variant=rx.color_mode_cond("solid", "soft")),
                    rx.button(rx.icon("moon", size=16), "Dark", id="mode-dark",
                              on_click=set_color_mode("dark"),
                              variant=rx.color_mode_cond("soft", "solid")), spacing="2",
                ),
            ),
            _settings_row(
                "Board density",
                "Give work some breathing room, or bring more into view. Kept in the "
                "database, so it is a setting for this machine rather than this browser.",
                rx.hstack(
                    rx.button("Comfortable", id="density-comfortable",
                              on_click=P.set_density("comfortable"), size="2",
                              variant=rx.cond(P.density == "comfortable", "solid", "soft")),
                    rx.button("Compact", id="density-compact",
                              on_click=P.set_density("compact"), size="2",
                              variant=rx.cond(P.density == "compact", "solid", "soft")),
                    spacing="2",
                ),
            ),
        ),
        rx.grid(
            s.panel(
                s.section_head("Where things live", rx.icon("monitor", size=18, color=s.MUTED)),
                _settings_row("Workspaces", "Built from COS_WORKING_DIR on every read. "
                              "Not settable here, and not settable over HTTP.",
                              s.text(rx.cond(P.working_dir != "", P.working_dir, "not set"),
                                     size="1", font_family="ui-monospace, monospace")),
                _settings_row("App data", "The database and the object folder. Backing this "
                              "up does not back up your workspaces.",
                              s.text(P.data_dir, size="1",
                                     font_family="ui-monospace, monospace")),
                _settings_row("Address",
                              rx.cond(
                                  P.loopback_only,
                                  "Loopback only.",
                                  "Reachable from any machine that can route here, and "
                                  "coscc has no login. Set COS_HOST=127.0.0.1 to bind "
                                  "this machine only.",
                              ),
                              s.text(P.host_port, size="1",
                                     font_family="ui-monospace, monospace")),
                _settings_row("Model", "What a session is created with.",
                              s.badge(P.model, "gray")),
            ),
            s.panel(
                s.section_head("What a chat session may do",
                               rx.icon("shield-check", size=18, color=s.MUTED)),
                s.text("Read from the running configuration. This screen cannot change any "
                       "of it — the only way in is the environment this process started "
                       "with.", size="1"),
                rx.foreach(P.knobs, _knob_row),
                id="knobs-panel",
            ),
            columns=rx.breakpoints(initial="1", lg="2"), gap="16px", width="100%",
            align_items="start",
        ),
        s.panel(
            s.section_head("What a board step may do",
                           rx.icon("key-round", size=18, color=s.MUTED)),
            s.text("These come from the grant table, not from the configuration above. "
                   "Everything not listed here gets nothing: no tools, no commands, one "
                   "turn, no budget.", size="1"),
            rx.foreach(P.grants, _grant_row),
            id="grants-panel",
        ),
        s.panel(
            rx.hstack(rx.icon("info", size=19, color=rx.color("iris", 11)),
                      rx.heading("Six stages write their artifact from the reply.", size="4",
                                 weight="medium")),
            s.text("idea, intent, spec, plan, review and ship get no tools in either mode. "
                   "A session with no tools cannot write a file, so this app writes the "
                   "artifact from what the session says. impl and pr write their own.",
                   margin_top="12px", max_width="800px", line_height="1.8"),
            background=rx.color("iris", 2),
        ),
        spacing="5", width="100%",
    )


# --- the unit drawer ---------------------------------------------------------


def _cell_chip(cell: rx.Var[Cell]) -> rx.Component:
    return rx.vstack(
        s.text(cell.stage, size="1"),
        s.badge(cell.status, cell.color),
        spacing="1", align="center", min_width="0",
    )


def _run_row(run: rx.Var[Run]) -> rx.Component:
    return rx.hstack(
        rx.center(rx.icon("play", size=15, color=rx.color(run.color, 11)),
                  width="32px", height="32px", border_radius="50%",
                  background=rx.color(run.color, 3), flex_shrink="0"),
        rx.vstack(
            rx.hstack(rx.text(run.stage, size="2", weight="medium"),
                      s.badge(run.mode, "gray"), s.badge(run.outcome, run.color),
                      spacing="2", wrap="wrap", align="center"),
            s.text(run.started + " → " + run.ended, size="1",
                   font_family="ui-monospace, monospace"),
            s.text(run.tokens + " tokens / " + run.usd + " / session " + run.session_id,
                   size="1"),
            rx.cond(
                run.detail != "",
                rx.box(
                    s.text(run.detail, size="1", overflow_wrap="anywhere",
                           white_space="pre-wrap",
                           font_family="ui-monospace, monospace"),
                    padding="8px 10px", border_radius="6px", margin_top="4px",
                    background=rx.color("amber", 2),
                    border=f"1px solid {rx.color('amber', 5)}",
                    max_height="220px", overflow_y="auto", width="100%",
                ),
            ),
            spacing="1", min_width="0", width="100%",
        ),
        align="start", spacing="3", padding="14px 0", width="100%",
        border_bottom=f"1px solid {s.LINE}", data_testid="run-row",
    )


def _detail_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.box(
                rx.hstack(
                    s.text(P.current_unit.id, size="1",
                           font_family="ui-monospace, monospace"),
                    s.badge(P.current_unit.lane, P.current_unit.color),
                    rx.spacer(),
                    rx.dialog.close(s.icon_button("x", "Close work detail")),
                    width="100%", align="center",
                ),
                rx.dialog.title(P.current_unit.title, size="6", weight="medium",
                                margin_top="24px", line_height="1.3"),
                rx.dialog.description("Next: " + P.current_unit.summary, size="2",
                                      color=s.MUTED, margin_top="12px"),
                rx.flex(rx.foreach(P.current_unit.cells, _cell_chip),
                        gap="14px", wrap="wrap", margin_top="20px"),
                padding="28px",
            ),
            rx.tabs.root(
                rx.tabs.list(
                    rx.tabs.trigger("Overview", value="overview"),
                    rx.tabs.trigger("Artifact", value="artifacts"),
                    rx.tabs.trigger("Timeline", value="timeline"),
                    width="100%", padding="0 24px",
                ),
                rx.tabs.content(
                    rx.vstack(
                        rx.cond(
                            P.current_unit.problems != "",
                            rx.callout(P.current_unit.problems, icon="triangle_alert",
                                       color_scheme="red", variant="surface", size="1"),
                        ),
                        rx.heading("The next step", size="4", weight="medium"),
                        rx.cond(
                            P.next_stage != "",
                            rx.vstack(
                                s.text("Running this starts a real Claude session in this "
                                       "workspace and spends account quota.",
                                       line_height="1.8"),
                                _settings_row(
                                    "Mode",
                                    "Which grant the step runs under. Recorded in the run "
                                    "log before anything starts.",
                                    rx.segmented_control.root(
                                        rx.segmented_control.item("Manual", value="manual"),
                                        rx.segmented_control.item("Auto", value="autonomous"),
                                        value=P.next_cell.mode, on_change=P.set_mode, size="1",
                                    ),
                                ),
                                # `spec.md` R17: what the step may do, before it runs.
                                s.panel(
                                    s.eyebrow("WHAT THIS STEP WOULD BE ALLOWED TO DO"),
                                    rx.hstack(
                                        s.badge(P.next_stage, "iris"),
                                        s.badge(P.next_cell.mode, "gray"),
                                        margin_top="10px", wrap="wrap",
                                    ),
                                    s.text("tools: " + P.next_cell.grants, size="1",
                                           margin_top="10px", overflow_wrap="anywhere",
                                           id="next-grants"),
                                    rx.cond(
                                        P.next_cell.warning != "",
                                        rx.callout(P.next_cell.warning, icon="triangle_alert",
                                                   color_scheme="amber", variant="surface",
                                                   size="1", margin_top="12px",
                                                   id="next-warning"),
                                    ),
                                    rx.cond(
                                        ~P.next_cell.opens_tools,
                                        s.text("No tools. This app writes the artifact from "
                                               "the reply; the session does not write it.",
                                               size="1", margin_top="12px"),
                                    ),
                                    padding="16px", background=rx.color("gray", 2),
                                ),
                                rx.button(
                                    rx.icon("play", size=15),
                                    "Run " + P.next_stage + " — spends quota",
                                    id="run-step", on_click=P.run_step,
                                    disabled=P.is_running | ~P.recording,
                                    loading=P.running_here, width="100%",
                                ),
                                # `0014` R8. The one control on this page that writes to
                                # the repository's git. It is separate from Run and stays
                                # separate: cutting a branch is a decision about where the
                                # work lands, and Run is a decision to spend money.
                                rx.button(
                                    rx.icon("git-branch", size=15),
                                    "Cut this unit's branch",
                                    id="cut-branch", on_click=P.start_branch,
                                    variant="soft", width="100%",
                                ),
                                s.text(
                                    "Fetches main from origin, then cuts <type>/<slug> "
                                    "from it, named by the Type: in intent.md. If that "
                                    "fetch fails, nothing is cut. The app does this and "
                                    "nothing else to git — it never pushes, merges or "
                                    "commits.",
                                    size="1",
                                ),
                                rx.cond(
                                    ~P.recording,
                                    s.text("No working folder is set, so a run cannot be "
                                           "recorded and will not start.", size="1"),
                                ),
                                spacing="4", width="100%", align="start",
                            ),
                            s.text("Every step this unit is waiting on has an artifact. "
                                   "There is no next step to run."),
                        ),
                        rx.cond(
                            P.run_log != "",
                            s.panel(
                                s.eyebrow("OUTPUT / LATEST RUN"),
                                rx.text(P.run_log, size="1",
                                        font_family="ui-monospace, monospace",
                                        white_space="pre-wrap", margin_top="12px",
                                        overflow_wrap="anywhere"),
                                role="log", aria_label="Run output", id="run-output",
                            ),
                        ),
                        spacing="4", padding="26px", width="100%", align="start",
                    ),
                    value="overview",
                ),
                rx.tabs.content(
                    rx.vstack(
                        rx.hstack(rx.icon("file-text", size=17, color=s.MUTED),
                                  rx.text(P.artifact_file, size="2", weight="medium"),
                                  rx.spacer(),
                                  rx.cond(P.artifact_missing,
                                          s.badge("not written", "gray"),
                                          s.badge("on disk", "grass")),
                                  width="100%"),
                        rx.box(rx.markdown(P.artifact), width="100%", id="artifact-body"),
                        spacing="5", padding="26px", width="100%",
                    ),
                    value="artifacts",
                ),
                rx.tabs.content(
                    rx.vstack(
                        s.text("Every run of every step of this unit, oldest first. Read "
                               "from the run log.", size="1"),
                        rx.foreach(P.runs, _run_row),
                        rx.cond(P.runs.length() == 0,
                                s.text("No step of this unit has been run from here.")),
                        spacing="3", padding="26px", width="100%", align="start",
                        id="timeline-body",
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


# --- the other dialogs -------------------------------------------------------


def _workspace_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title(rx.cond(P.editing == "", "Add a workspace", "Rename the label")),
            rx.dialog.description(
                rx.cond(
                    P.editing == "",
                    "A name is one path segment under the working folder. Leave the URL "
                    "empty to adopt a folder that is already there, or give one to clone.",
                    "Only the label changes. The folder and its name stay as they are.",
                ),
                size="2", margin_top="8px",
            ),
            rx.vstack(
                rx.el.label("Name", html_for="workspace-name", font_size="13px"),
                rx.input(id="workspace-name", placeholder="e.g. my-project",
                         value=P.new_name, on_change=P.set_new_name, width="100%",
                         max_length=64, disabled=P.editing != ""),
                rx.el.label("Label", html_for="workspace-label", font_size="13px"),
                rx.input(id="workspace-label", placeholder="What is this for?",
                         value=P.new_label, on_change=P.set_new_label, width="100%",
                         max_length=200),
                rx.cond(
                    P.editing == "",
                    rx.fragment(
                        rx.el.label("Repository URL (optional)", html_for="workspace-url",
                                    font_size="13px"),
                        rx.input(id="workspace-url", placeholder="https://…",
                                 value=P.new_url, on_change=P.set_new_url, width="100%"),
                    ),
                ),
                rx.cond(P.form_error != "",
                        rx.callout(P.form_error, color_scheme="red", role="alert",
                                   id="workspace-form-error")),
                rx.hstack(
                    rx.dialog.close(rx.button("Cancel", variant="soft", color_scheme="gray")),
                    rx.button("Save", id="save-workspace", on_click=P.save_workspace,
                              loading=P.busy),
                    justify="end", width="100%", spacing="3",
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
            rx.alert_dialog.title("Remove this workspace from the list?"),
            rx.alert_dialog.description(
                "It disappears from this app. The folder on disk, and anything "
                "uncommitted in it, is left exactly where it is.",
            ),
            rx.hstack(
                rx.alert_dialog.cancel(rx.button("Keep it", variant="soft",
                                                 color_scheme="gray")),
                rx.button("Remove from list", id="confirm-remove", color_scheme="red",
                          on_click=P.remove_workspace),
                justify="end", spacing="3", margin_top="24px",
            ),
            max_width="460px",
        ),
        open=P.remove_name != "", on_open_change=P.toggle_remove,
    )


def _command_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("Find your next step.", size="5"),
            rx.dialog.description("Search screens and work in this workspace.", size="2"),
            rx.input(rx.input.slot(rx.icon("search", size=17)),
                     id="command-query", placeholder="Where would you like to go?",
                     aria_label="Search screens and work", value=P.command_query,
                     on_change=P.search_commands, margin="20px 0", width="100%"),
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
                rx.foreach(P.command_units, lambda u: rx.button(
                    rx.icon("file-text", size=16), u.title, on_click=P.open_unit(u.id),
                    variant="ghost", color_scheme="gray", width="100%",
                    justify_content="flex-start", height="auto", padding="10px",
                    white_space="normal",
                )),
                spacing="2", width="100%",
            ),
            rx.dialog.close(rx.button("Close", variant="soft", color_scheme="gray",
                                      margin_top="20px")),
            max_width="560px",
        ),
        open=P.command_open, on_open_change=P.toggle_command,
    )


def _mobile_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.hstack(rx.dialog.title("CoS Studio", size="5"), rx.spacer(),
                      rx.dialog.close(s.icon_button("x", "Close navigation")), width="100%"),
            rx.dialog.description("Your workspace, your next step.", size="2"),
            _workspace_select(aria_label="Mobile active workspace", width="100%",
                              margin="24px 0"),
            _nav(mobile=True),
            rx.button(rx.icon("search", size=16), "Search the studio",
                      on_click=[P.toggle_mobile(False), P.toggle_command(True)],
                      variant="soft", width="100%", margin_top="20px"),
            max_width="360px",
        ),
        open=P.mobile_open, on_open_change=P.toggle_mobile,
    )


# --- the page ----------------------------------------------------------------


def _screen() -> rx.Component:
    return rx.match(
        P.screen,
        ("overview", _overview()),
        ("workspaces", _workspaces_screen()),
        ("board", _board()),
        ("sessions", _sessions()),
        ("activity", _activity()),
        ("settings", _settings()),
        rx.fragment(),
    )


def index() -> rx.Component:
    return rx.box(
        rx.flex(
            _sidebar(),
            rx.box(
                _topbar(),
                rx.box(
                    _status_bar(),
                    _banners(),
                    _screen(),
                    rx.flex(
                        s.text("COS STUDIO", size="1", letter_spacing="0.07em"),
                        rx.spacer(),
                        s.text("Built with Python. Designed around your work.", size="1"),
                        width="100%", gap="8px", wrap="wrap", padding="36px 0 8px",
                    ),
                    padding=rx.breakpoints(initial="0 18px 20px", md="0 32px 24px",
                                           xl="0 40px 24px"),
                    width="100%", max_width="1660px", margin="0 auto",
                ),
                flex="1", min_width="0", width="100%",
            ),
            width="100%", min_height="100dvh", align="start",
        ),
        _detail_dialog(), _workspace_dialog(), _remove_dialog(),
        _command_dialog(), _mobile_dialog(),
        id="studio-shell", data_density=P.density,
        on_mount=P.load,
        background=s.CANVAS, color=s.INK, min_height="100dvh",
        style={"& button": {"cursor": "pointer"}, "& button:disabled": {"cursor": "not-allowed"}},
    )
