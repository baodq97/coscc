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

from coscc import backlog
from coscc import hold as hold_rules
from coscc import models
from coscc import studio as s
from coscc.state import (
    LANE_COLOR,
    NAVIGATION,
    Activity,
    BacklogRow,
    Cell,
    Event,
    GrantRow,
    Knob,
    Card,
    ModelRow,
    Message,
    Question,
    Round,
    Run,
    StudioState,
    WatchEvent,
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
                    "Anyone who can reach this port gets the login page; whoever holds the "
                    "password or a live session can do everything here. Chat sessions still "
                    "have no tools by default.",
                ),
                size="1", margin_top="8px"),
            rx.button("What this can do", rx.icon("arrow-up-right", size=14),
                      on_click=P.navigate("settings"), variant="ghost", size="1", margin_top="12px"),
            padding="14px", background=rx.color("grass", 2),
        ),
        # `0070` R7. A same-origin `fetch`, not a `<form>`: how Reflex renders a form's
        # `action` was not measured, and a `fetch` to this origin carries the cookie and a
        # matching `Origin`. The guard ends the session whatever the page does next.
        rx.button("Đăng xuất", rx.icon("log-out", size=14), id="logout",
                  on_click=rx.call_script(
                      "fetch('/logout',{method:'POST',credentials:'same-origin'})"
                      ".finally(()=>{window.location.href='/login'})"
                  ),
                  variant="ghost", size="1", margin_top="10px"),
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


def _banners(where: str = "page") -> rx.Component:
    """The one `notice` and one `error`, drawn where `where` says.

    `0071` R7, R8: drawn twice — at the top of the page (`page-*`, which the older proofs
    read) and at the top of the unit dialog (`detail-*`), which covers the page's copy.
    Both read the same two fields and dismiss through the same handler, so they cannot
    disagree.
    """
    return rx.vstack(
        rx.cond(
            P.error != "",
            rx.callout(P.error, icon="triangle_alert", color_scheme="red", variant="surface",
                       width="100%", role="alert", id=f"{where}-error"),
        ),
        rx.cond(
            P.notice != "",
            rx.hstack(
                rx.icon("info", size=16, color=rx.color("iris", 11)),
                rx.text(P.notice, size="2"), rx.spacer(),
                s.icon_button("x", "Dismiss message", on_click=P.dismiss_notice),
                padding="12px 16px", background=rx.color("iris", 3), border_radius="10px",
                role="status", align="center", width="100%", id=f"{where}-notice",
            ),
        ),
        spacing="3", width="100%",
        margin_bottom=rx.cond((P.error != "") | (P.notice != ""),
                              "20px" if where == "page" else "0", "0"),
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
    """Why the board is empty, said about the directory that is actually empty.

    Two branches (`0001_product-describes-a-state-it-is-not-in` R6, R7):

    - The host repository's own `.cos/` holds units (`P.empty_host_units > 0`). The board
      reads only the product's store, so it names both directories and the count, and
      says the board does not list those units. The read-only reason, if any, follows.
    - Otherwise the sentence from before this unit: `board_note`, or the default. There is
      nothing in the host to explain, and the old words are true there.

    Until that unit this docstring claimed `0014` `spec.md` C1 was met here. It was not:
    no sentence on the page said so, and the one that did speak named the store's `.cos/`
    while the reader was looking at the repository's.
    """
    host_note = rx.text(
        P.empty_host, "/.cos/ holds ", P.empty_host_units,
        " work units, and this board does not list them. Units started in this app live "
        "in its own store, ", P.empty_store, ", and the board reads only that.",
        rx.cond(P.board_note != "", rx.fragment(" ", P.board_note), rx.fragment()),
        size="2", color=s.MUTED, text_align="center", max_width="420px", id="board-note",
    )
    return s.panel(
        rx.vstack(
            rx.center(rx.icon("sprout", size=30, color=rx.color("iris", 10)),
                      width="76px", height="76px", border_radius="24px",
                      background=rx.color("iris", 3), margin_bottom="6px"),
            rx.heading(rx.cond(P.has_workspace, "Nothing here yet.", "No workspace chosen."),
                       size="6", weight="medium"),
            rx.cond(
                P.empty_host_units > 0,
                host_note,
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
                    P.resume_id != "",
                    # `0053` R8: the one card `resume_id` names, picked out of `cards`.
                    rx.foreach(P.cards, lambda u: rx.cond(u.id == P.resume_id, rx.vstack(
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
                    ), rx.fragment())),
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


def _activity_line(line: rx.Var[Activity], unit_id=None) -> rx.Component:
    """`0051` R6. `running` and `ended, unknown` differ in colour, icon and words, so one
    is never read as the other. `rebasing` looks like `running` without an agent.

    `0073` R10: a board step's line opens the watch pane on its `run` rather than the unit."""
    plain = _activity_body(line)
    if unit_id is None:
        return plain
    return rx.cond(
        line.run != "",
        rx.box(
            _activity_body(line, watchable=True),
            on_click=P.open_watch(line.run, unit_id + " · " + line.stage, unit_id).stop_propagation,
            role="button", aria_label="Xem step đang chạy", data_testid="card-watch",
            cursor="pointer", width="100%",
        ),
        plain,
    )


def _activity_body(line: rx.Var[Activity], watchable: bool = False) -> rx.Component:
    unknown = line.label == "ended, unknown"
    return rx.hstack(
        rx.cond(
            unknown,
            rx.icon("circle-help", size=13, color=rx.color("gray", 9)),
            rx.box(width="8px", height="8px", border_radius="50%",
                   background=rx.color("iris", 9), flex_shrink="0"),
        ),
        rx.text(
            rx.cond(
                unknown,
                line.stage + " · ended, unknown · started " + line.started,
                rx.cond(line.agent != "", line.agent + "  ", "")
                + rx.cond(line.label == "rebasing", "rebasing", line.stage)
                + " · since " + line.started
                + rx.cond(line.turns != "", " · " + line.turns + " turns", "")
                + rx.cond(line.cost != "", " · " + line.cost, ""),
            ),
            size="1",
            color=rx.cond(unknown, rx.color("gray", 10), rx.color("iris", 11)),
        ),
        *([rx.icon("eye", size=13, color=rx.color("iris", 10))] if watchable else []),
        data_testid=rx.cond(unknown, "card-unknown-end", "card-running"),
        width="100%", align="center", spacing="2", margin_top="10px",
    )


def _unit_card(unit: rx.Var[Card]) -> rx.Component:
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
            rx.cond(unit.has_problem, s.badge("problem", "red")),
            # `0016` R8. A number, not a lane: an open question does not stop the loop.
            rx.cond(unit.open_questions > 0,
                    s.badge(unit.open_questions.to_string() + " waiting on you", "amber")),
            # `0035` R1. Only for a unit in the window; the button lives on the unit screen.
            # `0052`: `current` has a button too, and still reads gray on the card.
            rx.cond(unit.integration_state != "",
                    s.badge("main: " + unit.integration_state,
                            rx.cond(unit.integrate_button & (unit.integration_state != "current"),
                                    "amber", "gray"))),
            # `0047` R8. The label is the service's; the page only shows it.
            rx.cond(unit.outcome_text != "",
                    s.badge("outcome: " + unit.outcome_text, unit.outcome_color)),
            # `0045` R14. `cos.mjs`'s hold, never worked out on the page.
            rx.cond(unit.hold_state != "", s.badge(unit.hold_state, "amber")),
            # `0074`. Its place in the shortlist; changes nothing about the run button.
            rx.cond(unit.shortlist_rank > 0, s.badge("#" + unit.shortlist_rank.to_string(), "iris")),
            rx.spacer(),
            rx.center(rx.text(unit.owner, size="1", weight="medium"),
                      width="27px", height="27px", border_radius="50%",
                      background=rx.color("gray", 4), color=s.MUTED),
            width="100%", align="center", margin_top="18px",
        ),
        # `0074` R9. Its relations, on both units' cards.
        rx.cond(unit.relations_text != "",
                s.text(unit.relations_text, size="1", margin_top="7px", overflow_wrap="anywhere")),
        # `0051`. One line per session on this unit, from `Service.running` alone (R8).
        rx.foreach(unit.live, lambda line: _activity_line(line, unit.id)),
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


def _lane(title: str, color: str) -> rx.Component:
    count = P.lane_counts[title]
    return rx.vstack(
        rx.hstack(
            rx.box(width="7px", height="7px", border_radius="50%", background=rx.color(color, 9)),
            rx.text(title, size="2", weight="medium"),
            s.text(count.to_string(), size="1"),
            rx.spacer(), width="100%", align="center", padding="2px 4px 8px",
        ),
        # `0053` R8: every lane walks the one `cards` list and draws its own shown ones
        # (`spike.md ## U2`), so no card reaches the page twice.
        rx.foreach(P.cards, lambda c: rx.cond(
            (c.lane == title) & P.shown_ids.contains(c.id), _unit_card(c), rx.fragment())),
        rx.cond(count == 0,
                rx.center(s.text("Nothing here", size="1", text_align="center"),
                          padding="26px 10px", border=f"1px dashed {s.LINE}",
                          border_radius="10px", width="100%")),
        spacing="3", align="stretch", width="100%", min_width="0",
        padding="12px", border_radius="13px", background=s.SURFACE,
        # So a proof can ask which lane a card is in (`0001_product-describes-a-state-it-
        # is-not-in` R4). `title` is a Python string here, one of `LANE_COLOR`'s keys.
        data_testid=f"lane-{title}",
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


def _running_steps() -> rx.Component:
    """`0034` R6, R13. Every step running in this workspace, one Stop each.

    The list is the service's, re-read on each board load and after each Stop; nothing
    refreshes it on a timer. The name is a claim, not a login -- it is what the run log's
    `stopped_by` will say, unless the cancel lands before the step's first turn, which
    leaves no record at all.
    """
    return rx.cond(
        P.running_steps.length() > 0,
        s.panel(
            s.eyebrow("RUNNING STEPS"),
            rx.input(placeholder="Your name, to stop a step", value=P.stop_by,
                     on_change=P.set_stop_by, size="1", margin_top="10px",
                     aria_label="Your name, to stop a step", id="stop-by"),
            rx.foreach(P.running_steps, lambda r: rx.hstack(
                s.text(r.unit, size="1", font_family="ui-monospace, monospace"),
                s.badge(r.stage, "iris"),
                s.text(r.started_at, size="1"),
                rx.spacer(),
                # `0073` R10. Watching changes nothing; Stop, beside it, is what acts.
                rx.button(
                    rx.icon("eye", size=13), "Xem",
                    on_click=P.open_watch(r.run, r.unit + " · " + r.stage, r.unit),
                    class_name="watch-step", variant="soft", size="1",
                ),
                rx.button(
                    rx.icon("square", size=13),
                    rx.cond(r.stopping, "Stopping", "Stop"),
                    id="stop-step", on_click=P.stop_step(r.unit), disabled=r.stopping,
                    color_scheme="red", variant="soft", size="1",
                ),
                width="100%", align="center", spacing="3", margin_top="10px", flex_wrap="wrap",
            )),
            id="running-steps", padding="16px",
        ),
    )


_MONO = "ui-monospace, monospace"


def _log_tail(text) -> rx.Component:
    return rx.cond(
        text != "",
        rx.text(text, size="1", font_family=_MONO, white_space="pre-wrap",
                background=rx.color("gray", 2), padding="8px", width="100%", margin_top="6px"),
    )


def _update_channel(label: str, channel: str, line, ready, extra: rx.Component | None = None) -> rx.Component:
    return rx.hstack(
        s.text(label, size="1", font_family=_MONO),
        s.text(line, size="1"),
        rx.spacer(),
        *([extra] if extra is not None else []),
        rx.button("Áp dụng", id=f"update-apply-{channel}", on_click=P.apply_update(channel),
                  disabled=~ready | P.update_pending, size="1", variant="soft"),
        rx.button("Áp dụng ngay…", id=f"update-now-{channel}", on_click=P.show_cut_list(channel),
                  disabled=~ready, size="1", variant="soft", color_scheme="red"),
        width="100%", align="center", spacing="3", margin_top="10px", flex_wrap="wrap",
    )


def _update_panel() -> rx.Component:
    """`0068`. What runs, whether a newer build is ready, and one press to apply it.

    Every string and every enabled button comes from `Service.update_status`. The name
    typed here is what the run log's `by` says, a claim and not an identity: one password
    stands in front of the page (`0070`), and it names nobody.
    """
    return s.panel(
        s.eyebrow("CẬP NHẬT"),
        rx.hstack(
            s.text("Đang chạy " + P.upd_version, size="2"),
            s.text(P.upd_commit, size="1", font_family=_MONO),
            spacing="3", align="center", margin_top="8px", flex_wrap="wrap", id="update-running",
        ),
        rx.cond(
            ~P.upd_available,
            s.text(P.upd_reason, size="1", margin_top="6px", id="update-unavailable"),
            rx.vstack(
                s.text("Kiểm tra thành công gần nhất: "
                       + rx.cond(P.upd_checked_at != "", P.upd_checked_at, "chưa có"), size="1"),
                rx.input(placeholder="Tên của bạn, để áp dụng, huỷ chờ hoặc build", value=P.update_by,
                         on_change=P.set_update_by, size="1", aria_label="Tên của bạn, để cập nhật",
                         id="update-by"),
                _update_channel("release", "release", P.upd_release, P.upd_release_ready),
                _update_channel(
                    "local", "local", P.upd_local, P.upd_local_ready,
                    rx.button("Build từ origin/main", id="update-build-local", on_click=P.build_local,
                              disabled=~P.upd_local_configured, size="1", variant="soft"),
                ),
                _log_tail(P.upd_local_tail),
                rx.cond(
                    P.update_pending,
                    rx.box(
                        s.text("sẽ áp dụng khi không còn việc chạy", size="2"),
                        s.text(P.upd_pending_reason, size="1"),
                        rx.foreach(P.upd_waiting, lambda w: s.text("đang chờ: " + w, size="1")),
                        rx.button("Huỷ chờ", id="update-cancel", on_click=P.cancel_update,
                                  size="1", variant="soft", margin_top="6px"),
                        id="update-pending", margin_top="10px",
                    ),
                ),
                rx.cond(
                    P.cut_open,
                    rx.box(
                        s.text("Áp dụng ngay sẽ:", size="2"),
                        rx.cond(P.cut_items.length() == 0, s.text("không cắt việc nào", size="1")),
                        rx.foreach(P.cut_items, lambda i: s.text(i, size="1")),
                        rx.hstack(
                            rx.button("Xác nhận, áp dụng ngay", id="update-confirm-now",
                                      on_click=P.confirm_apply_now, size="1", color_scheme="red"),
                            rx.button("Thôi", on_click=P.close_cut_list, size="1", variant="soft"),
                            spacing="2", margin_top="6px",
                        ),
                        id="update-cut-list", margin_top="10px",
                    ),
                ),
                rx.cond(P.upd_error != "", rx.box(
                    s.text("Lần áp dụng vừa rồi dừng lại: " + P.upd_error, size="1"),
                    _log_tail(P.upd_error_tail), id="update-error", width="100%",
                )),
                rx.cond(P.upd_last != "", rx.box(
                    s.text("Lần cập nhật trước: " + P.upd_last, size="1"),
                    _log_tail(P.upd_last_tail), id="update-last", width="100%",
                )),
                width="100%", spacing="1", margin_top="6px", align="start",
            ),
        ),
        id="update-panel", padding="16px",
    )


def _update_warning() -> rx.Component:
    """R9: starting work while an update waits is allowed, and pushes the update back."""
    return rx.cond(P.update_pending, s.text(P.update_warning, size="1", color=rx.color("amber", 11)))


# R14. Asks `/api/update` every 5 s outside Reflex's socket, and reloads the page when the
# build it answers for is not the one the page was loaded under. The overlay says
# `đang khởi động lại` while nothing answers, and after 120 s says it could not reconnect.
# A `401` is not a restart: the session ended (expiry, logout elsewhere, `reset-password`),
# so the page goes to `/login` rather than point at a rollback (`0070` review F3).
_RECONNECT_JS = """
(function () {
  if (window.__coscc_update_watch) return;
  window.__coscc_update_watch = true;
  var first = null, failing = null, log = "";
  var box = document.createElement("div");
  box.id = "update-reconnect";
  box.setAttribute("role", "status");
  box.style.cssText = "display:none;position:fixed;top:0;left:0;right:0;z-index:9999;" +
    "padding:10px 16px;background:#7a4a00;color:#fff;font:14px system-ui,sans-serif";
  function show(text) {
    if (!box.parentNode && document.body) document.body.appendChild(box);
    box.textContent = text;
    box.style.display = "block";
  }
  function tick() {
    fetch("/api/update", {cache: "no-store"}).then(function (r) {
      if (r.status === 401) { window.location.replace("/login"); return null; }
      if (!r.ok) throw new Error(String(r.status));
      return r.json();
    }).then(function (u) {
      if (u === null) return;
      if (u.log) log = u.log;
      if (first === null) { first = u.build_id; }
      else if (u.build_id !== first) { window.location.reload(); return; }
      failing = null;
      box.style.display = "none";
    }).catch(function () {
      if (failing === null) failing = Date.now();
      if (Date.now() - failing > 120000) {
        show("không kết nối lại được. Log của lần cập nhật: " + (log || "(không rõ)") +
             ". Lệnh quay về tay nằm trong log đó, và trong docs/install.md ## Update.");
      } else {
        show("đang khởi động lại…");
      }
    });
  }
  tick();
  setInterval(tick, 5000);
})();
"""


def _board() -> rx.Component:
    return rx.vstack(
        s.heading("Work board", "From an idea to something real. One clear step at a time."),
        _update_panel(),
        rx.cond(P.has_workspace, _start_unit(), rx.fragment()),
        rx.cond(P.has_workspace, _backlog_panel(), rx.fragment()),
        _running_steps(),
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
            P.cards.length() == 0,
            _empty_board(),
            rx.cond(
                P.shown_ids.length() == 0,
                s.panel(rx.heading("No matching work", size="4"),
                        s.text("Try a different search or choose All work.", margin_top="8px")),
                rx.cond(
                    P.board_view == "Board",
                    rx.grid(
                        *(
                            _lane(name, LANE_COLOR[name])
                            for name in ("Planned", "In progress", "Needs review", "Complete")
                        ),
                        columns=rx.breakpoints(initial="1", sm="2", lg="4"),
                        gap="12px", width="100%", align_items="start", id="board-grid",
                    ),
                    s.panel(
                        rx.foreach(P.cards, lambda u: rx.cond(P.shown_ids.contains(u.id), rx.button(
                            s.text(u.id, size="1", min_width="110px",
                                   font_family="ui-monospace, monospace"),
                            rx.text(u.title, size="2", weight="medium", text_align="left"),
                            rx.spacer(), s.badge(u.stage, u.color), s.text(u.lane, size="1"),
                            on_click=P.open_unit(u.id), variant="ghost", color_scheme="gray",
                            width="100%", height="auto", padding="15px 8px", flex_wrap="wrap",
                            justify_content="flex-start", border_bottom=f"1px solid {s.LINE}",
                        ), rx.fragment())),
                        id="board-list",
                    ),
                ),
            ),
        ),
        _dropped_group(),
        rx.hstack(
            rx.icon("info", size=13, color=s.MUTED),
            s.text("Lanes organise attention. Stage statuses come from each artifact's "
                   "Status line, never from the run log.", size="1"),
            spacing="2", align="center",
        ),
        spacing="5", width="100%",
    )


# --- sessions ----------------------------------------------------------------


def _message(message: rx.Var[Message], index: rx.Var[int]) -> rx.Component:
    is_user = message.role == "user"
    return rx.hstack(
        s.mark(rx.cond(is_user, "ME", "AI"), "gray", "30px"),
        rx.vstack(
            rx.text(rx.cond(is_user, "You", "Claude"), size="2", weight="medium"),
            rx.text(message.text, size="2", white_space="pre-wrap", line_height="1.9",
                    overflow_wrap="anywhere"),
            # `0053` R10. The rest of a long message comes down only when asked for.
            rx.cond(
                message.cut > 0,
                rx.hstack(
                    s.text("… " + message.cut.to_string() + " more characters", size="1"),
                    rx.button("Show full message", on_click=P.open_message(index),
                              size="1", variant="soft", data_testid="message-open"),
                    spacing="3", align="center", wrap="wrap",
                ),
            ),
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
                    rx.foreach(P.messages, lambda m, i: _message(m, i)),
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
                    _update_warning(),
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
            s.badge(grant.stage, "iris"),
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


def _model_row(row: rx.Var[ModelRow]) -> rx.Component:
    """`0004_no-setting-says-which-model-runs-a-stage`. One stage, or chat: how many agents
    run it, on what model, and where that model came from."""
    return rx.box(
        rx.hstack(
            s.badge(row.name, "iris"),
            s.text(row.agents.to_string() + " agent", size="1"),
            rx.spacer(),
            s.text(row.model, size="1", font_family="ui-monospace, monospace"),
            s.badge(row.source, rx.cond(row.overridden, "amber", "gray")),
            width="100%", align="center", wrap="wrap",
        ),
        rx.hstack(
            rx.input(
                value=rx.cond(P.model_target == row.name, P.model_text, ""),
                on_change=lambda v: P.edit_model(row.name, v),
                placeholder="model id, e.g. claude-sonnet-5",
                aria_label="Model for " + row.name, size="1", width="100%",
            ),
            rx.button("Save", on_click=P.save_model(row.name), size="1",
                      loading=P.saving_model),
            rx.cond(
                row.overridden,
                rx.button("Reset", on_click=P.reset_model(row.name), size="1",
                          variant="soft", loading=P.saving_model),
            ),
            width="100%", align="center", margin_top="8px",
        ),
        # `0033`: the effort, with its own source and its own override. Chat has none.
        rx.cond(
            row.has_effort,
            rx.hstack(
                s.text("effort", size="1"),
                rx.spacer(),
                s.text(row.effort, size="1", font_family="ui-monospace, monospace"),
                s.badge(row.effort_source, rx.cond(row.effort_overridden, "amber", "gray")),
                rx.select(
                    list(models.EFFORTS),
                    placeholder="set effort",
                    value="",
                    on_change=lambda v: P.save_effort(row.name, v),
                    size="1",
                    aria_label="Effort for " + row.name,
                ),
                rx.cond(
                    row.effort_overridden,
                    rx.button("Reset", on_click=P.reset_effort(row.name), size="1",
                              variant="soft", loading=P.saving_model),
                ),
                width="100%", align="center", margin_top="8px", wrap="wrap",
            ),
        ),
        padding="12px 0", border_bottom=f"1px solid {s.LINE}", width="100%",
        data_testid="model-row",
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
                                  "Reachable from any machine that can route here; the "
                                  "master password stands in front, and over plain HTTP "
                                  "it crosses the network readable. Set "
                                  "COS_HOST=127.0.0.1 to bind this machine only.",
                              ),
                              s.text(P.host_port, size="1",
                                     font_family="ui-monospace, monospace")),
                _settings_row("COS_MODEL (fallback)",
                              "Used only by a row below that has neither an override nor a "
                              "shipped default — today, chat.",
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
            s.section_head("Which model runs each stage",
                           rx.icon("cpu", size=18, color=s.MUTED)),
            s.text("The stages come from cos.mjs; chat is last. A model is taken from the "
                   "override set here, else the default shipped with coscc, else "
                   "COS_MODEL. A change applies to the next session a step or a chat "
                   "starts. It opens no gate and starts nothing.", size="1"),
            s.text("Effort is looked up the same way, with no COS_MODEL step: unset means "
                   "the SDK's default. A :novel row is what a stage after plan runs on when "
                   "the plan's label is novel — declared, forced by a file on the security "
                   "surface, missing, or escalated after impl ran out of turns. max is taken "
                   "only from an override set here.", size="1", margin_top="8px"),
            s.text("Default là điểm xuất phát, sẽ chỉnh theo số đo, không phải kết luận.",
                   size="1", margin_top="8px"),
            rx.callout("Anyone holding the password or a live session can change these. "
                       "Every change is written to the run log with its old and new "
                       "value.", icon="triangle_alert", color_scheme="amber",
                       variant="surface", size="1", margin_top="12px"),
            rx.foreach(P.model_problems,
                       lambda p: rx.callout(p, icon="circle_alert", color_scheme="red",
                                            variant="surface", size="1", margin_top="8px")),
            rx.foreach(P.model_rows, _model_row),
            id="models-panel",
        ),
        s.panel(
            rx.hstack(rx.icon("info", size=19, color=rx.color("iris", 11)),
                      rx.heading("Five stages write their artifact from the reply.", size="4",
                                 weight="medium")),
            s.text("idea, intent, spec, plan and review cannot write a file, so this app "
                   "writes the artifact from what the session says. spec, plan and review "
                   "may read, and only inside the unit's worktree and its own folder in "
                   "the store; idea and intent get no tools. The mode does not change "
                   "this. impl, pr and ship write their own.",
                   margin_top="12px", max_width="800px", line_height="1.8"),
            background=rx.color("iris", 2),
        ),
        spacing="5", width="100%",
    )


# --- the unit drawer ---------------------------------------------------------


def _cell_chip(cell: rx.Var[Cell]) -> rx.Component:
    # `0019_a-failed-step-destroys-the-work-that-succeeded` plan step 7. `cell.label` is
    # `cell.status` except when the artifact is absent and the last run of this stage
    # failed, in which case it names that run instead of the bare word "not started".
    return rx.vstack(
        s.text(cell.stage, size="1"),
        s.badge(cell.label, cell.color),
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
                      rx.spacer(),
                      # `0073` R10, R13: a run from before `0073` opens the pane on its note.
                      rx.button(rx.icon("eye", size=13), "Xem",
                                on_click=P.open_watch(run.run, P.unit_id + " · " + run.stage, P.unit_id),
                                class_name="watch-run", variant="soft", size="1"),
                      spacing="2", wrap="wrap", align="center", width="100%"),
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


def _question_row(q: rx.Var[Question]) -> rx.Component:
    """`0016` R2. One unanswered question and the box its answer goes in."""
    return s.panel(
        rx.hstack(
            s.badge(q.artifact, rx.cond(q.counted, "amber", "gray")),
            # `0028`: a finding a person is awaited on reads as one, by its `F<n>`.
            s.text(rx.cond(q.number == 0, "finding ", "question ") + q.label, size="1"),
            width="100%", align="center",
        ),
        rx.box(rx.markdown(q.text), width="100%", margin_top="8px"),
        # `0056` R11: a dropped unit is read, not answered.
        rx.cond(
            ~P.unit_dropped,
            rx.fragment(
                rx.text_area(
                    placeholder="Your answer.",
                    value=rx.cond(P.answer_target == q.key, P.answer_text, ""),
                    on_change=lambda v: P.edit_answer(q.key, v),
                    aria_label="Answer to " + q.key, width="100%", rows="3", margin_top="8px",
                ),
                rx.button(
                    rx.icon("send", size=14), "Send this answer",
                    on_click=P.answer_question(q.key), loading=P.answering_key == q.key,
                    size="1", margin_top="8px", id="answer-" + q.key,
                ),
            ),
        ),
        width="100%",
    )


def _questions_tab() -> rx.Component:
    """`0016` R2 and R10, said on the page: what an answer is and what it is not."""
    return rx.vstack(
        s.text(
            "Answer an item under ## Open questions. The answer is appended to the end of "
            "the artifact under ## Answers, with the name you type here; nothing above it "
            "changes. This is not an approval and it starts no step — the next step reads "
            "it when someone runs it. The name is not checked: one password stands in front "
            "of this app and it names nobody, so whoever holds it or a live session can type "
            "any name. A row named finding F<n> is a review "
            "finding the last review round confirmed needs a person; its answer goes into "
            "review.md, and unlike a question's it is read: cos.mjs offers review again "
            "once every such finding has one, and the ship gate counts a finding the review "
            "then marks [answered] as closed.",
            size="1", line_height="1.8",
        ),
        rx.input(
            placeholder="Your name", value=P.answer_by, on_change=P.set_answer_by,
            aria_label="Who is answering", id="answer-by", width="100%",
        ),
        rx.foreach(P.open_questions_here, _question_row),
        rx.cond(P.open_questions_here.length() == 0,
                s.text("No question in this unit is waiting for an answer.")),
        spacing="3", padding="26px", width="100%", align="start", id="questions-body",
    )


def _integration_panel() -> rx.Component:
    """`0035` R1, R3, R8, R13. A separate component from the run button, which still offers
    only the stage `cos.mjs next` names. Hidden for a unit outside the window."""
    u = P.current_unit
    return rx.cond(
        u.integration_state != "",
        s.panel(
            rx.hstack(
                s.eyebrow("INTEGRATION WITH MAIN"),
                rx.spacer(),
                s.badge(u.integration_state,
                        rx.cond(u.integrate_button & (u.integration_state != "current"), "amber", "gray")),
                width="100%", align="center",
            ),
            rx.cond(u.integration_behind != "",
                    s.text(u.integration_behind + " commit(s) behind origin/main "
                           + u.integration_origin, size="1", margin_top="8px")),
            rx.cond(u.integration_reason != "",
                    s.text(u.integration_reason, size="1", overflow_wrap="anywhere")),
            # `0052` R3: the board does not fetch, so `current` may be against a stale ref.
            rx.cond(u.integration_state == "current",
                    s.text("Counted against origin/main " + u.integration_origin
                           + " as the last fetch left it. Integrate fetches first, then decides.",
                           size="1", margin_top="8px")),
            rx.foreach(u.integration_needs_person,
                       lambda n: s.text("[needs-person] " + n, size="1", color=rx.color("red", 11))),
            rx.cond(
                u.integrate_button,
                rx.vstack(
                    rx.foreach(u.integration_warnings, lambda w: s.text(w, size="1", line_height="1.7")),
                    rx.button(
                        rx.icon("git-pull-request-arrow", size=14), "Integrate",
                        on_click=P.integrate,
                        loading=P.integrating,
                        disabled=P.integrating,
                        size="1", id="integrate-button",
                    ),
                    spacing="2", margin_top="8px", align="start",
                ),
            ),
            width="100%", id="integration-panel",
        ),
    )


def _outcome_panel() -> rx.Component:
    """`0047` R8. The outcome of the open unit against its intent's deadline, and on a
    finished unit a form to record one. Hidden when the service gives no label."""
    u = P.current_unit
    return rx.cond(
        u.outcome_text != "",
        s.panel(
            rx.hstack(
                s.eyebrow("OUTCOME"),
                rx.spacer(),
                s.badge(u.outcome_text, u.outcome_color),
                width="100%", align="center",
            ),
            rx.cond(u.outcome_deadline != "",
                    s.text("Deadline read from intent.md: " + u.outcome_deadline,
                           size="1", margin_top="8px")),
            rx.cond(u.outcome_detail != "",
                    s.text(u.outcome_detail, size="1", overflow_wrap="anywhere")),
            rx.cond(u.outcome_by != "",
                    s.text("Recorded by " + u.outcome_by + " on " + u.outcome_date
                           + ", measured by " + u.outcome_measured_by, size="1")),
            rx.cond(u.outcome_hint != "",
                    s.text(u.outcome_hint, size="1", color=rx.color("red", 11))),
            rx.cond(u.outcome_invalid > 0,
                    s.text(u.outcome_invalid.to_string()
                           + " ### Outcome block(s) in intent.md could not be read and were skipped.",
                           size="1", color=rx.color("amber", 11))),
            rx.cond(
                u.outcome_form,
                rx.vstack(
                    s.text(
                        "Appended to intent.md under ## Answers as ### Outcome; nothing above it "
                        "changes, a later block replaces an earlier one, and no gate reads it. "
                        "Neither name is checked: the password names nobody.",
                        size="1", line_height="1.7",
                    ),
                    rx.select(["đạt", "trượt", "không đo được"], value=P.outcome_result,
                              on_change=P.set_outcome_result, size="1", id="outcome-result"),
                    rx.input(placeholder="Source — where the figure came from",
                             value=P.outcome_source, on_change=P.set_outcome_source,
                             width="100%", id="outcome-source"),
                    rx.input(placeholder="Reason — why it could not be measured",
                             value=P.outcome_reason, on_change=P.set_outcome_reason,
                             width="100%", id="outcome-reason"),
                    rx.input(placeholder="Measured by — agent, or a person's name",
                             value=P.outcome_measured_by, on_change=P.set_outcome_measured_by,
                             width="100%", id="outcome-measured-by"),
                    rx.input(placeholder="Your name", value=P.answer_by, on_change=P.set_answer_by,
                             width="100%", id="outcome-recorded-by"),
                    rx.text_area(placeholder="Note (optional)", value=P.outcome_note,
                                 on_change=P.set_outcome_note, width="100%", id="outcome-note"),
                    rx.button(
                        rx.icon("flag", size=14), "Record outcome",
                        on_click=P.record_outcome,
                        loading=P.recording_outcome,
                        disabled=P.recording_outcome,
                        size="1", id="outcome-button",
                    ),
                    spacing="2", margin_top="8px", align="start", width="100%",
                ),
            ),
            width="100%", id="outcome-panel",
        ),
    )


# `0045`. The button each move gets, keyed by the value `cos.mjs` puts in `holdMoves`.
_HOLD_BUTTONS = (
    ("paused", "Pause", "pause"),
    ("dropped", "Drop", "circle-x"),
    ("active", "Resume", "play"),
)


def _hold_panel() -> rx.Component:
    """`0045` R14. The unit's hold as `cos.mjs` read it, and one button per move it allows.

    Nothing here decides which moves exist: a button shows only when its value is in
    `hold_moves`. The *Drop* warning is always on screen before the button, because a drop
    closes a pull request with this machine's `gh` login.
    """
    u = P.current_unit
    return rx.cond(
        u.hold_moves.length() > 0,
        s.panel(
            rx.hstack(
                s.eyebrow("PAUSE OR DROP"),
                rx.spacer(),
                rx.cond(u.hold_state != "", s.badge(u.hold_state, "amber")),
                width="100%", align="center",
            ),
            rx.cond(
                u.hold_state != "",
                s.text(u.hold_reason + " — " + u.hold_by + ", " + u.hold_date,
                       size="1", margin_top="8px", overflow_wrap="anywhere"),
            ),
            rx.input(placeholder="Why, in one line", value=P.hold_reason, on_change=P.set_hold_reason,
                     aria_label="Reason for this change", id="hold-reason", width="100%", margin_top="8px"),
            rx.input(placeholder="Your name", value=P.hold_by, on_change=P.set_hold_by,
                     aria_label="Who decides this", id="hold-by", width="100%"),
            rx.cond(u.hold_moves.contains("dropped"), s.text(hold_rules.DROP_WARNING, size="1", line_height="1.7")),
            rx.hstack(
                *(
                    rx.cond(
                        u.hold_moves.contains(value),
                        rx.button(rx.icon(icon, size=14), label, on_click=P.set_hold(value),
                                  loading=P.holding, disabled=P.holding, size="1",
                                  variant="soft", id=f"hold-{value}"),
                    )
                    for value, label, icon in _HOLD_BUTTONS
                ),
                spacing="2", margin_top="4px",
            ),
            width="100%", id="hold-panel",
        ),
    )


def _backlog_line(row: rx.Var[BacklogRow]) -> rx.Component:
    return rx.box(
        rx.hstack(
            s.text("#" + row.rank.to_string(), size="1", min_width="28px"),
            rx.button(row.unit, on_click=P.show_backlog_history(row.unit), variant="ghost", size="1",
                      font_family="ui-monospace, monospace"),
            s.badge("value " + row.value, "iris"),
            s.badge("effort " + row.effort, "gray"),
            rx.cond(row.drift != "", s.badge(row.drift, "amber")),
            s.text(row.by, size="1"),
            spacing="2", align="center", flex_wrap="wrap",
        ),
        rx.cond(row.basis != "", s.text(row.basis, size="1", line_height="1.6", overflow_wrap="anywhere")),
        rx.cond(row.warnings != "", s.text("⚠ " + row.warnings, size="1", color=rx.color("amber", 11))),
        rx.cond(row.agent_differs != "", s.text(row.agent_differs, size="1", overflow_wrap="anywhere")),
        width="100%", padding="8px 0", border_bottom=f"1px solid {s.LINE}",
    )


def _backlog_panel() -> rx.Component:
    """`0074`. The shortlist, the rest in computed order, and the three write forms.

    Display only: nothing here reaches the run button, a gate or `next` (R15). The
    proposal's warning stands above its button, always (R19).
    """
    field = lambda name, value, label, **kw: rx.input(  # noqa: E731
        placeholder=label, value=value, on_change=lambda v: P.set_backlog_field(name, v),
        aria_label=label, id=f"backlog-{name}", size="1", **kw,
    )
    return rx.el.details(
        rx.el.summary(s.text("Backlog — shortlist, estimates and relations", size="2"), cursor="pointer"),
        s.panel(
            s.text(P.backlog_note, size="1"),
            s.text(P.backlog_recorded, size="1", margin_top="4px"),
            s.eyebrow("SHORTLIST"),
            rx.foreach(P.backlog_rows, _backlog_line),
            rx.foreach(P.backlog_warnings, lambda w: s.text("⚠ " + w, size="1", color=rx.color("amber", 11))),
            s.eyebrow("THE REST, IN COMPUTED ORDER"),
            rx.foreach(P.backlog_rest, _backlog_line),
            rx.cond(P.backlog_unestimated.length() > 0,
                    s.text("Chưa có ước lượng: " + P.backlog_unestimated.join(", "), size="1")),
            rx.cond(
                P.history_unit != "",
                rx.box(s.eyebrow("HISTORY OF " + P.history_unit),
                       rx.foreach(P.history_lines, lambda line: s.text(line, size="1", overflow_wrap="anywhere"))),
            ),
            field("backlog_by", P.backlog_by, "Your name", width="100%", margin_top="8px"),
            rx.hstack(
                field("shortlist_input", P.shortlist_input, "Shortlist: unit names in order, at most 7",
                      flex="1"),
                field("shortlist_reason", P.shortlist_reason, "Why, in one line", flex="1"),
                rx.button("Take the first 7", on_click=P.fill_shortlist, size="1", variant="soft"),
                rx.button("Save shortlist", on_click=P.save_shortlist, size="1", id="backlog-save-shortlist"),
                width="100%", flex_wrap="wrap",
            ),
            rx.hstack(
                field("est_unit", P.est_unit, "Unit"), field("est_value", P.est_value, "Value 1–5", width="90px"),
                field("est_effort", P.est_effort, "S / M / L", width="80px"),
                field("est_basis", P.est_basis, "Basis, your own words", flex="1"),
                rx.button("Save estimate", on_click=P.save_estimate, size="1"),
                width="100%", flex_wrap="wrap",
            ),
            rx.hstack(
                field("rel_unit", P.rel_unit, "Unit"),
                rx.select(list(backlog.RELATIONS), value=P.rel_type,
                          on_change=lambda v: P.set_backlog_field("rel_type", v), size="1"),
                field("rel_other", P.rel_other, "Other unit"),
                rx.select(list(backlog.OPS), value=P.rel_op,
                          on_change=lambda v: P.set_backlog_field("rel_op", v), size="1"),
                field("rel_reason", P.rel_reason, "Why, in one line", flex="1"),
                rx.button("Save relation", on_click=P.save_relation, size="1"),
                width="100%", flex_wrap="wrap",
            ),
            s.text(P.propose_warning, size="1", line_height="1.7", margin_top="8px", id="backlog-propose-warning"),
            rx.button(rx.icon("sparkles", size=14), "Propose estimates", on_click=P.propose_estimates,
                      loading=P.proposing, disabled=P.proposing, size="1", variant="soft", id="backlog-propose"),
            width="100%", id="backlog-panel",
        ),
        width="100%",
    )


def _dropped_group() -> rx.Component:
    """`0045` (`spec.md ## Answers, câu 1`). Dropped units, collapsed at the foot of the board."""
    return rx.cond(
        P.dropped_count > 0,
        rx.el.details(
            rx.el.summary(
                s.text("Dropped (" + P.dropped_count.to_string() + ")", size="2"),
                cursor="pointer",
            ),
            rx.grid(
                rx.foreach(P.cards, lambda c: rx.cond(
                    c.hold_state == "dropped", _unit_card(c), rx.fragment())),
                columns=rx.breakpoints(initial="1", sm="2", lg="4"),
                gap="12px", width="100%", margin_top="12px",
            ),
            width="100%", id="dropped-group",
        ),
    )


def _round_row(r: rx.Var[Round]) -> rx.Component:
    """`0021` R7, R8. One review round: on the PR with its link, or not and a button."""
    return s.panel(
        rx.hstack(
            s.text("Round " + r.number.to_string(), size="2"),
            s.badge(r.verdict, "gray"),
            rx.spacer(),
            rx.cond(r.posted, s.badge("on the PR", "grass"),
                    s.badge("comment not on the PR", "amber")),
            width="100%", align="center",
        ),
        rx.cond(
            r.posted,
            rx.cond(r.url != "",
                    rx.link(r.url, href=r.url, is_external=True, size="1", margin_top="8px")),
            rx.vstack(
                rx.cond(r.reason != "",
                        s.text("Last attempt: " + r.reason, size="1", overflow_wrap="anywhere")),
                rx.cond(
                    ~P.unit_dropped,  # `0056` R11
                    rx.button(
                        rx.icon("send", size=14), "Post to PR",
                        on_click=P.post_review_comment(r.number),
                        loading=P.posting_round == r.number,
                        disabled=P.posting_round != 0,
                        size="1", id="post-round-" + r.number.to_string(),
                    ),
                ),
                spacing="2", margin_top="8px", align="start",
            ),
        ),
        width="100%",
    )


def _comments_tab() -> rx.Component:
    """`0021` R4, R10, said on the page: what a comment is and what it is not."""
    return rx.vstack(
        s.text(
            "Each round of review.md goes to the pull request as one ordinary comment, "
            "posted under this machine's gh login and marked as written by an agent "
            "session. It is not an approval, and no gate reads it. A round the board ran "
            "is posted when it is written; a round written at a terminal waits here until "
            "someone presses Post to PR. The text goes up verbatim. Whoever holds the "
            "password or a live session can press the button.",
            size="1", line_height="1.8",
        ),
        rx.cond(P.current_unit.pr_url != "",
                rx.link(P.current_unit.pr_url, href=P.current_unit.pr_url,
                        is_external=True, size="1"),
                s.text("pr.md names no pull request yet.", size="1")),
        rx.foreach(P.current_unit.rounds, _round_row),
        rx.cond(P.current_unit.rounds.length() == 0,
                s.text("review.md holds no round yet.")),
        spacing="3", padding="26px", width="100%", align="start", id="comments-body",
    )


def _unit_not_found() -> rx.Component:
    """`0056` R9. An address named a unit this workspace's board does not list: say so,
    rather than draw an empty unit as if it were one."""
    return rx.vstack(
        rx.hstack(
            rx.dialog.title("Not found", size="6", weight="medium"),
            rx.spacer(),
            rx.dialog.close(s.icon_button("x", "Close work detail")),
            width="100%", align="center",
        ),
        rx.dialog.description(P.unit_id + " is not a unit of " + P.ws_name + ".", size="2"),
        rx.link("Back to the board", href=P.board_href, size="2"),
        rx.cond(P.board_note != "", s.text(P.board_note, size="1", overflow_wrap="anywhere")),
        spacing="4", padding="28px", width="100%", align="start", id="unit-not-found",
    )


def _detail_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            # `0071` R2, R7. Sticky at the top of the content, which is what scrolls here
            # (`overflow_y` below), so a message is in view however far down the tab is.
            rx.box(
                _banners("detail"),
                position="sticky", top="0", z_index="2", background=s.CANVAS,
                padding=rx.cond((P.error != "") | (P.notice != ""), "12px 28px 12px", "0"),
                id="detail-messages",
            ),
            rx.cond(P.unit_missing, _unit_not_found(), rx.fragment(
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
                rx.cond(
                    P.unit_dropped,
                    rx.callout(
                        "Dropped: " + P.current_unit.hold_reason + " — "
                        + P.current_unit.hold_by + ", " + P.current_unit.hold_date
                        + ". Shown to be read; nothing here writes to it but the hold panel.",
                        icon="circle-x", color_scheme="gray", variant="surface", size="1",
                        margin_top="20px", id="unit-dropped",
                    ),
                ),
                padding="28px",
            ),
            rx.tabs.root(
                rx.tabs.list(
                    rx.tabs.trigger("Overview", value="overview"),
                    rx.tabs.trigger("Artifact", value="artifacts"),
                    rx.tabs.trigger("Questions", value="questions"),
                    rx.tabs.trigger("PR comments", value="comments"),
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
                        # `0024`. What `cos.mjs next` said about this unit, verbatim. The
                        # stage below is its answer; the page works nothing out itself.
                        rx.hstack(
                            s.text(P.run_said, size="1", overflow_wrap="anywhere",
                                   id="next-said"),
                            rx.button(
                                rx.icon("refresh-cw", size=13), "Ask again",
                                id="ask-next", on_click=P.load_next, variant="soft",
                                size="1", disabled=P.running_here,
                            ),
                            justify="between", align="center", width="100%", spacing="3",
                        ),
                        # `0028`. `cos.mjs next` named findings a person must act on, and
                        # offers no stage. Say which, and point at where they are answered.
                        rx.cond(
                            (P.next_stage == "") & (P.run_waiting.length() > 0),
                            rx.hstack(
                                s.text("Needs a person: " + P.run_waiting.join(", "),
                                       size="2", id="next-waiting"),
                                rx.button(
                                    rx.icon("message-square", size=13),
                                    "Open Questions",
                                    id="open-questions",
                                    on_click=P.set_detail_tab("questions"),
                                    variant="soft", size="1",
                                ),
                                justify="between", align="center", width="100%", spacing="3",
                            ),
                        ),
                        # `0056` R11: nothing that writes, for a dropped unit.
                        rx.cond(
                            ~P.unit_dropped & (P.next_stage != ""),
                            rx.vstack(
                                s.text("Running this starts a real Claude session in this "
                                       "workspace and spends account quota.",
                                       line_height="1.8"),
                                _settings_row(
                                    "Mode",
                                    "Recorded in the run log. It does not change what the "
                                    "step may do: each stage has one grant.",
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
                                    disabled=P.running_here | ~P.recording,
                                    loading=P.running_here, width="100%",
                                ),
                                _update_warning(),
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
                                    "from it, named by the Type: in intent.md, in this "
                                    "unit's own worktree — the workspace stays on main. "
                                    "If that fetch fails, nothing is cut. The app never "
                                    "pushes, merges or commits.",
                                    size="1",
                                ),
                                rx.cond(
                                    P.unit_tree != "",
                                    s.text(P.unit_tree, id="unit-tree", size="1"),
                                ),
                                rx.cond(
                                    ~P.recording,
                                    s.text("No working folder is set, so a run cannot be "
                                           "recorded and will not start.", size="1"),
                                ),
                                spacing="4", width="100%", align="start",
                            ),
                            s.text("cos.mjs names no stage to run now — the line above "
                                   "says why. Nothing re-asks on its own: press Ask again "
                                   "once that has changed."),
                        ),
                        rx.cond(~P.unit_dropped, _integration_panel()),
                        rx.cond(~P.unit_dropped, _outcome_panel()),
                        # Kept for a dropped unit: its one move (`paused`, `cos.mjs`
                        # `HOLD_MOVES`) is the board's way back (`spec.md ## Answers, câu 1`).
                        _hold_panel(),
                        rx.cond(
                            P.log_here,
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
                rx.tabs.content(_questions_tab(), value="questions"),
                rx.tabs.content(_comments_tab(), value="comments"),
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
            )),
            position="fixed", right="0", top="0", left="auto", bottom="0",
            transform="none", width="min(620px, 100vw)", max_width="100vw", height="100dvh",
            max_height="100dvh", border_radius="0", padding="0", overflow_y="auto",
            background=s.CANVAS, aria_label="Work detail",
        ),
        open=P.unit_id != "", on_open_change=P.toggle_detail,
    )


# --- watching a step (`0073`) -------------------------------------------------

# Two observers, and nothing else: the first row coming into view presses *older*, and a
# list that was at its bottom before a change is put back there. Every rule about what the
# list holds is `StudioState`'s; this only scrolls.
#
# Away from the bottom, the row being read stays where it was whatever changed the list:
# the first row in view is remembered by its `data-seq` and offset on every scroll, and
# brought back to that offset after every change. Rows are drawn by position, so a page
# prepended, a full list dropping its newest rows (`review.md` F5) and a live batch
# dropping its oldest while following (F6 a) all rewrite rows in place, and neither the
# scroll height nor the row nodes say where the row went; the seq does. The anchor is
# never spent on the first change, so a live batch landing between *older* and its page
# does not leave the page to arrive with none (F6 b). *Older* pressed at the bottom
# anchors too; *Về cuối* drops the anchor and goes to the bottom. `data-seq` is watched as
# an attribute because a list that stays at `WATCH_WINDOW` rows changes no child at all.
# The browser's own scroll anchoring is off on `#watch-list`, so this is the one thing
# that moves it.
_WATCH_JS = """
(function () {
  if (window.__coscc_watch) return;
  window.__coscc_watch = true;
  var atBottom = true, anchor = null, observed = null;
  function rows(list) { return list.querySelectorAll(".watch-ev"); }
  function mark(list) {
    var all = rows(list), box = list.getBoundingClientRect();
    for (var i = 0; i < all.length; i++) {
      var r = all[i].getBoundingClientRect();
      if (r.bottom > box.top) { anchor = {seq: +all[i].dataset.seq, offset: r.top - box.top}; return; }
    }
    anchor = null;
  }
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (!e.isIntersecting) return;
      var older = document.getElementById("watch-older");
      if (older && !older.disabled) older.click();
    });
  });
  document.addEventListener("click", function (ev) {
    var t = ev.target && ev.target.closest ? ev.target : null;
    var list = document.getElementById("watch-list");
    if (!t || !list) return;
    var older = t.closest("#watch-older");
    if (older && !older.disabled) { atBottom = false; mark(list); }
    else if (t.closest("#watch-live")) { atBottom = true; anchor = null; list.scrollTop = list.scrollHeight; }
  }, true);
  document.addEventListener("scroll", function (ev) {
    var list = ev.target;
    if (!list || list.id !== "watch-list") return;
    atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
    if (atBottom) anchor = null; else mark(list);
  }, true);
  new MutationObserver(function () {
    var list = document.getElementById("watch-list");
    if (!list) { atBottom = true; anchor = null; observed = null; return; }
    var top = document.getElementById("watch-top");
    if (top && top !== observed) {
      if (observed) io.unobserve(observed);
      io.observe(top);
      observed = top;
    }
    if (anchor && !atBottom) {
      // The row itself, or the oldest one still after it when it has left from the top.
      var row = null, all = rows(list);
      for (var i = 0; i < all.length && !row; i++) if (+all[i].dataset.seq >= anchor.seq) row = all[i];
      if (row) {
        list.scrollTop += row.getBoundingClientRect().top - list.getBoundingClientRect().top - anchor.offset;
      }
    } else if (atBottom) {
      list.scrollTop = list.scrollHeight;
    }
  }).observe(document.documentElement,
             {subtree: true, childList: true, attributes: true, attributeFilter: ["data-seq"]});
})();
"""

_WATCH_COLOR = {"denied": "red", "result": "grass", "end": "iris", "turn": "amber", "tool_use": "blue"}


def _watch_row(e: rx.Var[WatchEvent]) -> rx.Component:
    """R10, R12. One event: when, kind, what it says, and its body collapsed unless opened."""
    opened = P.watch_open_seq == e.seq
    return rx.box(
        rx.hstack(
            s.text(e.when, size="1", font_family=_MONO),
            rx.match(e.kind, *[(k, s.badge(k, c)) for k, c in _WATCH_COLOR.items()], s.badge(e.kind, "gray")),
            rx.text(e.label, size="1", weight="medium", overflow_wrap="anywhere"),
            rx.spacer(),
            rx.cond(
                e.collapsed,
                rx.cond(
                    opened,
                    rx.button("Thu gọn", on_click=P.watch_collapse, size="1", variant="ghost",
                              class_name="watch-collapse"),
                    rx.button("Mở", on_click=P.watch_expand(e.seq), size="1", variant="ghost",
                              class_name="watch-expand"),
                ),
            ),
            width="100%", align="center", spacing="2", flex_wrap="wrap",
        ),
        rx.cond(
            e.body != "",
            rx.el.pre(
                rx.cond(opened, P.watch_open_text, e.body),
                class_name=rx.cond(opened, "watch-body watch-open", "watch-body"),
                style={"white_space": "pre-wrap", "overflow_wrap": "anywhere", "margin": "4px 0 0",
                       "font_size": "12px", "font_family": _MONO},
            ),
        ),
        rx.cond(e.collapsed & ~opened, s.text("… đã thu gọn; bấm Mở để xem hết", size="1")),
        rx.cond(e.truncated,
                s.text("đã cắt khi lưu: chỉ giữ 64 000 ký tự đầu của " + e.original_length.to_string() + " ký tự",
                       size="1", color=rx.color("amber", 11))),
        rx.cond(e.persisted != "", s.text(e.persisted, size="1", color=rx.color("amber", 11))),
        class_name="watch-ev", custom_attrs={"data-seq": e.seq, "data-at": e.at},
        padding="8px 0", border_bottom=f"1px solid {s.LINE}", width="100%",
    )


def _watch_dialog() -> rx.Component:
    """`0073` R10-R13. One step's events, oldest at the top; opens at the bottom."""
    return rx.dialog.root(
        rx.dialog.content(
            rx.hstack(
                rx.dialog.title(P.watch_title, size="4", weight="medium"),
                s.badge(rx.cond(P.watch_status != "", P.watch_status, "—"), "iris"),
                rx.spacer(),
                rx.dialog.close(s.icon_button("x", "Đóng khung xem")),
                width="100%", align="center",
            ),
            rx.dialog.description(
                "Chỉ xem: không mở gate nào, không chạy gì, không đổi step. Ai giữ mật khẩu hoặc "
                "một session còn sống đọc được mọi lệnh, đường dẫn, suy nghĩ và đầu ra tool ở đây.",
                size="1", margin_top="6px",
            ),
            rx.cond(P.watch_note != "", rx.callout(P.watch_note, id="watch-note", size="1",
                                                   color_scheme="amber", margin_top="8px")),
            rx.button("Tải sự kiện cũ hơn", id="watch-older", on_click=P.watch_older,
                      disabled=~P.watch_has_older, variant="ghost", size="1", margin_top="8px"),
            rx.box(
                rx.box(id="watch-top", height="1px"),
                rx.foreach(P.watch_events, _watch_row),
                id="watch-list", max_height="62vh", overflow_y="auto", width="100%",
                style={"overflow_anchor": "none"},
            ),
            rx.hstack(
                rx.cond(P.watch_pending > 0,
                        s.text(P.watch_pending.to_string() + " sự kiện mới", id="watch-pending", size="1")),
                rx.cond(P.watch_has_newer,
                        s.text("Các sự kiện mới hơn đã rời khung xem", id="watch-newer", size="1")),
                rx.cond(P.watch_has_newer | (~P.watch_following & (P.watch_status == "running")),
                        rx.button("Về cuối", id="watch-live", on_click=P.watch_live, size="1")),
                spacing="3", align="center", margin_top="8px",
            ),
            id="watch-pane", max_width="min(960px, 96vw)", width="96vw",
        ),
        open=P.watch_run != "", on_open_change=P.toggle_watch,
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
                rx.foreach(P.cards, lambda u: rx.cond(P.command_ids.contains(u.id), rx.button(
                    rx.icon("file-text", size=16), u.title, on_click=P.open_unit(u.id),
                    variant="ghost", color_scheme="gray", width="100%",
                    justify_content="flex-start", height="auto", padding="10px",
                    white_space="normal",
                ), rx.fragment())),
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
        _command_dialog(), _mobile_dialog(), _watch_dialog(),
        rx.script(_RECONNECT_JS),
        rx.script(_WATCH_JS),
        # No `on_mount`: it runs again on every path change (`.cos/0056_*/spike.md ## U3`).
        # The first read is `StudioState.arrive`, every route's `on_load`.
        id="studio-shell", data_density=P.density,
        background=s.CANVAS, color=s.INK, min_height="100dvh",
        style={"& button": {"cursor": "pointer"}, "& button:disabled": {"cursor": "not-allowed"}},
    )
