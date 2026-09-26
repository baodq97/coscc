"""The six screens, built from Python components.

`spec.md` R8: no hand-written HTML or CSS serves this app. Everything below is Python.

These screens began as a prototype and keep its shape, its spacing and most of its
words. What changed is where every value comes from: the prototype read `prototype_data.py`, and
nothing here reads anything but `StudioState`, which reads `Service`. That swap is the
whole of `spec.md` R12.

Since `0082` (`spec.md ## Answers, câu 5`) each action whose effect costs money or leaves
this machine keeps one sentence beside its button — `Service.CONSEQUENCE` — and nothing more.
The lists of limits these screens used to carry (what a grant reaches, who else holds the
password, that the app writes a prose stage's artifact) are in `.claude/docs/coscc-page-text.md`
and the rules and documents `.claude/rules/coscc-app.md` indexes, and the full grant warnings
are still in the API. Paths, full
shas, UUIDs and variable names sit only inside a closed `_details`
(`.claude/rules/ui-standard.md` S3).
"""

from __future__ import annotations

import reflex as rx
from reflex.style import set_color_mode

from coscc import hold as hold_rules
from coscc import models
from coscc import present, spend
from coscc import studio as s
from coscc.service import CONSEQUENCE
from coscc.state import (
    NAVIGATION,
    Activity,
    AnomalyRow,
    AutopilotStop,
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
    SpendRow,
    StudioState,
    TokenRow,
    WasteRow,
    WatchEvent,
    Workspace,
)

P = StudioState


def _details(key: str, label: str, *children, **props) -> rx.Component:
    """A closed-by-default `studio.details` keyed in `StudioState.open_details`."""
    return s.details(P.open_details.contains(key), P.toggle_details(key), label, *children, **props)


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
            rx.button("Settings", rx.icon("arrow-up-right", size=14),
                      on_click=P.navigate("settings"), variant="ghost", size="1", margin_top="12px"),
            padding="14px", background=rx.color("grass", 2),
        ),
        # `0070` R7. A same-origin `fetch`, not a `<form>`: how Reflex renders a form's
        # `action` was not measured, and a `fetch` to this origin carries the cookie and a
        # matching `Origin`. The guard ends the session whatever the page does next.
        rx.button("Log out", rx.icon("log-out", size=14), id="logout",
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
    """How many workspaces. The two roots are on Settings, inside *Details* (`0082` D26)."""
    return rx.flex(
        rx.spacer(),
        s.text(P.workspaces.length().to_string() + " workspace(s)", size="1",
               id="workspace-count"),
        width="100%", align="start", wrap="wrap", gap="16px", padding="12px 0 20px",
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
               "Waiting on an answer or a person's decision", "circle-dot", "amber"),
        s.stat("Tokens", P.usage_total_tokens, "Billed for this workspace, from the run log",
               "sparkles", "blue"),
        s.stat("Cost", P.usage_total_usd, P.usage_cost_note, "wallet", "grass"),
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
    # `0082` D63: the count, not the two paths.
    host_note = rx.text(
        "This repository's own .cos/ holds ", P.empty_host_units,
        " work units that this board does not list.",
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
                            "Add a workspace, or open one from Workspaces.",
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
                              rx.cond(w.label != "", s.text(w.label, size="1")),
                              spacing="0", align="start"),
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
        _details("ws-path-" + workspace.id, "Details",
                 rx.hstack(rx.icon("folder-git-2", size=14, color=s.MUTED),
                           s.text(workspace.path, size="1", overflow_wrap="anywhere"),
                           spacing="2", align="start"),
                 margin_top="20px"),
        rx.box(height="1px", background=s.LINE, margin="20px 0 16px"),
        rx.hstack(
            s.text(rx.cond(workspace.source == "env",
                           "Read only", "Stored"), size="1"),
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
                "No working folder is set, so workspaces cannot be added or removed here.",
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
            role="button", aria_label="Watch the running step", data_testid="card-watch",
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


def _unit_card(unit: rx.Var[Card], grouped: bool = False) -> rx.Component:
    """One card. `grouped` for a card in a collapsed group, which names its stage (`0100` R8):
    in a column, the column does."""
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
            # `0100` R9. The one state badge, as the service decided it. `attention_reason`
            # stays in the dialog (spec C3); a paused or dropped hold is this badge's word.
            s.badge(unit.state_label, unit.state_color),
            *([s.badge(unit.at, "gray")] if grouped else []),
            rx.cond(unit.has_problem, s.badge("problem", "red")),
            # `0016` R8. How many, beside the state they put the unit in.
            # `0082` R11: only while the service says the unit can still be answered.
            rx.cond((unit.open_questions > 0) & unit.answerable,
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
            # `0074`. Its place in the shortlist; changes nothing about the run button.
            rx.cond(unit.shortlist_rank > 0, s.badge("#" + unit.shortlist_rank.to_string(), "iris")),
            rx.spacer(),
            rx.center(rx.text(unit.owner, size="1", weight="medium"),
                      width="27px", height="27px", border_radius="50%",
                      background=rx.color("gray", 4), color=s.MUTED, flex_shrink="0"),
            # `0082` D57, R17: the badges wrap inside the card instead of running past it.
            width="100%", align="center", margin_top="18px", flex_wrap="wrap", row_gap="6px",
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


def _column(stage: rx.Var[str]) -> rx.Component:
    """`0100` R1. One stage's column. The stages are the board read's, never a list here."""
    count = P.stage_counts[stage]
    return rx.vstack(
        rx.hstack(
            rx.text(stage, size="2", weight="medium"),
            s.text(count.to_string(), size="1"),
            rx.spacer(), width="100%", align="center", padding="2px 4px 8px",
        ),
        # `0053` R8: every column walks the one `cards` list and draws its own shown ones
        # (`spike.md ## U2`), so no card reaches the page twice.
        rx.foreach(P.cards, lambda c: rx.cond(
            (c.at == stage) & P.board_ids.contains(c.id), _unit_card(c), rx.fragment())),
        rx.cond(count == 0,
                rx.center(s.text("Nothing here", size="1", text_align="center"),
                          padding="26px 10px", border=f"1px dashed {s.LINE}",
                          border_radius="10px", width="100%")),
        spacing="3", align="stretch", width="264px", min_width="264px", flex_shrink="0",
        padding="12px", border_radius="13px", background=s.SURFACE,
        # So a proof can ask which column a card is in (`0001_product-describes-a-state-it-
        # is-not-in` R4).
        data_testid="column-" + stage,
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
            s.text("Say the problem in your own words; it becomes the unit's idea.md.",
                   size="1", margin_top="2px"),
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
    refreshes it on a timer. No name is asked (`0082` R3): `stopped_by` records `owner`.
    """
    return rx.cond(
        P.running_steps.length() > 0,
        s.panel(
            s.eyebrow("RUNNING STEPS"),
            rx.foreach(P.running_steps, lambda r: rx.hstack(
                s.text(r.unit, size="1", font_family="ui-monospace, monospace"),
                s.badge(r.stage, "iris"),
                s.text(r.started_at, size="1"),
                rx.spacer(),
                # `0073` R10. Watching changes nothing; Stop, beside it, is what acts.
                rx.button(
                    rx.icon("eye", size=13), "Watch",
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


def _log_tail(key: str, text) -> rx.Component:
    """`0082` D67: a log's tail holds paths and commands, so it opens only on request."""
    return rx.cond(
        text != "",
        _details(f"log-{key}", "Log",
                 rx.text(text, size="1", font_family=_MONO, white_space="pre-wrap",
                         background=rx.color("gray", 2), padding="8px", width="100%", margin_top="6px")),
    )


def _update_actions(channel: str, line) -> rx.Component:
    """One channel's line and only the buttons `Service.update_status` lists (`0082` R9)."""
    return rx.hstack(
        s.text(line, size="1"),
        rx.spacer(),
        rx.cond(P.upd_actions.contains(f"apply-{channel}"),
                rx.button("Apply", id=f"update-apply-{channel}", on_click=P.apply_update(channel),
                          size="1", variant="soft")),
        rx.cond(P.upd_actions.contains(f"now-{channel}"),
                rx.button("Apply now…", id=f"update-now-{channel}", on_click=P.show_cut_list(channel),
                          size="1", variant="soft", color_scheme="red")),
        *([rx.cond(P.upd_actions.contains("build-local"),
                   rx.button("Build from origin/main", id="update-build-local", on_click=P.build_local,
                             size="1", variant="soft"))] if channel == "local" else []),
        width="100%", align="center", spacing="3", margin_top="10px", flex_wrap="wrap",
    )


def _update_panel() -> rx.Component:
    """`0068`, on Settings since `0082` R9. What runs, one line of state, and only the
    buttons that can be used. Every word and every button comes from `Service.update_status`.
    """
    return s.panel(
        s.section_head("Updates", rx.icon("download", size=18, color=s.MUTED)),
        rx.hstack(
            s.text("Running " + P.upd_version, size="2"),
            s.text(P.upd_commit, size="1", font_family=_MONO),
            rx.cond(P.upd_checked_at != "", s.text("checked " + P.upd_checked_at, size="1")),
            spacing="3", align="center", flex_wrap="wrap", id="update-running",
        ),
        rx.cond(
            ~P.upd_available,
            s.text(P.upd_line, size="1", margin_top="6px", id="update-unavailable"),
            rx.vstack(
                _update_actions("release", P.upd_line),
                _update_actions("local", P.upd_local_line),
                _log_tail("local", P.upd_local_tail),
                rx.cond(
                    P.update_pending,
                    rx.box(
                        rx.foreach(P.upd_waiting, lambda w: s.text("waiting for " + w, size="1")),
                        rx.cond(P.upd_actions.contains("cancel"),
                                rx.button("Stop waiting", id="update-cancel", on_click=P.cancel_update,
                                          size="1", variant="soft", margin_top="6px")),
                        id="update-pending", margin_top="10px",
                    ),
                ),
                rx.cond(
                    P.cut_open,
                    rx.box(
                        s.text("Applying now:", size="2"),
                        rx.cond(P.cut_items.length() == 0, s.text("stops nothing", size="1")),
                        rx.foreach(P.cut_items, lambda i: s.text(i, size="1")),
                        s.text(CONSEQUENCE["apply-now"], size="1", id="update-now-consequence"),
                        rx.hstack(
                            rx.button("Apply now", id="update-confirm-now",
                                      on_click=P.confirm_apply_now, size="1", color_scheme="red"),
                            rx.button("Cancel", on_click=P.close_cut_list, size="1", variant="soft"),
                            spacing="2", margin_top="6px",
                        ),
                        id="update-cut-list", margin_top="10px",
                    ),
                ),
                rx.cond(P.upd_error != "", rx.box(
                    s.text("The last update stopped: " + P.upd_error, size="1"),
                    _log_tail("error", P.upd_error_tail), id="update-error", width="100%",
                )),
                rx.cond(P.upd_last != "", rx.box(
                    s.text("Last update: " + P.upd_last, size="1"),
                    _log_tail("last", P.upd_last_tail), id="update-last", width="100%",
                )),
                width="100%", spacing="1", margin_top="6px", align="start",
            ),
        ),
        _details("update", "Details",
                 s.text("commit " + P.upd_commit_full, size="1", font_family=_MONO, overflow_wrap="anywhere"),
                 margin_top="8px"),
        id="update-panel",
    )


def _update_warning() -> rx.Component:
    """R9: starting work while an update waits is allowed, and pushes the update back."""
    return rx.cond(P.update_pending, s.text(P.update_warning, size="1", color=rx.color("amber", 11)))


# R14. Asks `/api/update` every 5 s outside Reflex's socket, and reloads the page when the
# build it answers for is not the one the page was loaded under. The overlay says
# "Restarting…" while nothing answers, and after 120 s says it could not reconnect.
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
        show("Could not reconnect. See docs/install.md, Update, to roll back.");
      } else {
        show("Restarting…");
      }
    });
  }
  tick();
  setInterval(tick, 5000);
})();
"""


def _autopilot_stop_row(stop: rx.Var[AutopilotStop]) -> rx.Component:
    return rx.flex(
        s.badge(stop.unit, "iris"),
        s.badge(stop.kind, "amber"),
        s.text(stop.reason, size="1", min_width="0", overflow_wrap="anywhere"),
        gap="8px", align="center", wrap="wrap", width="100%", data_testid="autopilot-stop",
    )


def _autopilot_strip() -> rx.Component:
    """`0043` R9. Shown while the autopilot is on: the cap line, and one row per stop."""
    return rx.cond(
        P.autopilot_on,
        rx.vstack(
            rx.hstack(
                rx.icon("bot", size=16, color=rx.color("iris", 11)),
                rx.text("Autopilot is on", size="2", weight="medium"),
                rx.spacer(),
                s.text(P.autopilot_cap, size="1"),
                width="100%", align="center", wrap="wrap",
            ),
            rx.cond(P.autopilot_refused != "", s.text(P.autopilot_refused, size="1")),
            rx.foreach(P.autopilot_stops, _autopilot_stop_row),
            padding="12px 16px", background=rx.color("iris", 3), border_radius="10px",
            spacing="2", width="100%", role="status", id="autopilot-strip",
        ),
    )


def _board() -> rx.Component:
    return rx.vstack(
        s.heading("Work board", "From an idea to something real. One clear step at a time."),
        rx.cond(P.has_workspace, _start_unit(), rx.fragment()),
        _autopilot_strip(),
        _running_steps(),
        rx.flex(
            rx.input(rx.input.slot(rx.icon("search", size=15)), id="work-search",
                     placeholder="Search work...", value=P.query, on_change=P.search_work,
                     aria_label="Search work",
                     width=rx.breakpoints(initial="100%", sm="240px")),
            rx.segmented_control.root(
                *[rx.segmented_control.item(label, value=label)
                  for label in ("All work", "Autonomous", "Needs you")],
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
                    # `0100` Design 6. One column per stage; wider than the screen, it
                    # scrolls sideways, as GitHub Projects' board does.
                    rx.hstack(
                        rx.foreach(P.stages, _column),
                        spacing="3", width="100%", align="start", overflow_x="auto",
                        padding_bottom="8px", id="board-grid",
                    ),
                    s.panel(
                        rx.foreach(P.cards, lambda u: rx.cond(P.shown_ids.contains(u.id), rx.button(
                            s.text(u.id, size="1", min_width="110px",
                                   font_family="ui-monospace, monospace"),
                            rx.text(u.title, size="2", weight="medium", text_align="left"),
                            rx.spacer(), s.badge(u.at, "gray"), s.badge(u.state_label, u.state_color),
                            on_click=P.open_unit(u.id), variant="ghost", color_scheme="gray",
                            width="100%", height="auto", padding="15px 8px", flex_wrap="wrap",
                            justify_content="flex-start", border_bottom=f"1px solid {s.LINE}",
                        ), rx.fragment())),
                        id="board-list",
                    ),
                ),
            ),
        ),
        _collapsed_groups(),
        spacing="5", width="100%",
    )


# --- sessions ----------------------------------------------------------------


def _message(message: rx.Var[Message], index: rx.Var[int]) -> rx.Component:
    is_user = message.role == "user"
    return rx.hstack(
        s.mark(rx.cond(is_user, "ME", "AI"), "gray", "30px"),
        rx.vstack(
            rx.text(rx.cond(is_user, "You", "Claude"), size="2", weight="medium"),
            # `0082` D17: the message as markdown. `use_raw=False`: Reflex's default passes
            # raw HTML through (`rehypeRaw`, measured in `screens_test.py`), and a message
            # carrying `<img onerror>` would then run on a page with a session (plan Risk 8).
            rx.box(rx.markdown(message.text, use_raw=False), width="100%", overflow_wrap="anywhere"),
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
                        rx.heading(rx.cond(P.session_title != "", P.session_title, "New conversation"),
                                   size="4", weight="medium"),
                        s.text(P.current_workspace.name, size="1"),
                        rx.cond(P.session_id != "",
                                _details("session", "Details",
                                         s.text(P.session_id, size="1", font_family=_MONO))),
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
                            s.text("Say something to start.")),
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
                        s.text("Each message spends account quota.", size="1"),
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
                s.text("Includes cache reads and writes, which are billed.", size="1", margin_top="18px"),
            ),
            columns=rx.breakpoints(initial="1", lg="2"), gap="16px", width="100%",
            align_items="start",
        ),
        spacing="5", width="100%",
    )


# --- cost (`0093`) -----------------------------------------------------------

OVER_BUDGET = f"Over ${spend.BUDGET_USD:g}"


def _table(headers: list[str], rows, render, empty: str, **props) -> rx.Component:
    """`0093`. A list as a table (S5), scrolling sideways on a phone rather than wrapping."""
    return rx.box(
        rx.table.root(
            rx.table.header(rx.table.row(*[rx.table.column_header_cell(h) for h in headers])),
            rx.table.body(rx.foreach(rows, render)),
            size="1", variant="ghost", width="100%",
        ),
        rx.cond(rows.length() == 0, s.text(empty, size="1", margin_top="8px")),
        overflow_x="auto", width="100%", **props,
    )


def _mono(value) -> rx.Component:
    return rx.text(value, size="1", font_family="ui-monospace, monospace", white_space="nowrap")


def _spend_row(row: rx.Var[SpendRow]) -> rx.Component:
    """R5: the steps whose cost is not known stand right beside the money."""
    return rx.table.row(
        rx.table.cell(rx.hstack(_mono(row.key), rx.cond(row.over, s.badge(OVER_BUDGET, "red")),
                                spacing="2", align="center")),
        rx.table.cell(_mono(row.usd)),
        rx.table.cell(s.text(row.unknown, size="1", color=rx.color("amber", 11))),
        rx.table.cell(s.text(row.steps, size="1")),
    )


SPEND_HEADERS = ["Cost", "Unknown", "Steps"]


def _token_row(row: rx.Var[TokenRow]) -> rx.Component:
    return rx.table.row(
        rx.table.cell(_mono(row.scope)),
        *[rx.table.cell(s.text(v, size="1", white_space="nowrap"))
          for v in (row.input, row.output, row.cache_read, row.cache_creation, row.total)],
    )


def _waste_row(row: rx.Var[WasteRow]) -> rx.Component:
    return rx.table.row(
        rx.table.cell(rx.cond(row.sub, s.text(row.label, size="1", padding_left="16px"),
                              rx.text(row.label, size="1", weight="medium"))),
        rx.table.cell(s.text(row.count, size="1")),
        rx.table.cell(_mono(row.usd)),
        rx.table.cell(s.text(row.unknown, size="1", color=rx.color("amber", 11))),
    )


def _anomaly_cells(row: rx.Var[AnomalyRow]) -> list[rx.Component]:
    return [
        rx.table.cell(_mono(row.stage)),
        rx.table.cell(s.text(row.ended, size="1", white_space="nowrap")),
        rx.table.cell(_mono(row.measured)),
        rx.table.cell(_mono(row.usd)),
    ]


def _anomaly_row(row: rx.Var[AnomalyRow]) -> rx.Component:
    return rx.table.row(rx.table.cell(s.badge(row.kind, "amber")), rx.table.cell(_mono(row.unit)),
                        *_anomaly_cells(row))


def _unit_anomaly_row(row: rx.Var[AnomalyRow]) -> rx.Component:
    """R11: the same row in the unit's own dialog, where the unit goes without saying."""
    return rx.table.row(rx.table.cell(s.badge(row.kind, "amber")), *_anomaly_cells(row))


def _cost() -> rx.Component:
    """`0093` R1–R10, in the order of the spec's `## Design` §4. Tables only (S1, S2, S5)."""
    return rx.vstack(
        s.heading("Cost", "Where this workspace's money went, from the run log."),
        rx.cond(
            ~P.cost_recording,
            rx.callout("No working folder is set, so nothing is being recorded.",
                       icon="info", color_scheme="amber", variant="surface", width="100%"),
        ),
        rx.grid(
            s.stat("Known cost", P.cost_total_usd, "Added from each finished step", "wallet", "grass"),
            s.stat("Unknown cost", rx.cond(P.cost_total_unknown != "", P.cost_total_unknown, "None"),
                   "Steps that ended without a cost", "triangle-alert", "amber"),
            s.stat("Finished steps", P.cost_total_steps, "Every step that ended", "layers"),
            columns=rx.breakpoints(initial="1", sm="3"), gap="12px", width="100%",
            id="cost-total",
        ),
        s.panel(s.section_head("By unit"),
                _table(["Unit"] + SPEND_HEADERS, P.cost_units, _spend_row,
                       "No step has finished here yet.", id="cost-by-unit")),
        s.panel(s.section_head("By stage"),
                _table(["Stage"] + SPEND_HEADERS, P.cost_stages, _spend_row,
                       "No step has finished here yet.", id="cost-by-stage")),
        s.panel(s.section_head("By day (" + P.cost_offset + ")"),
                _table(["Day"] + SPEND_HEADERS, P.cost_days, _spend_row,
                       "No step has finished here yet.", id="cost-by-day")),
        s.panel(s.section_head("Tokens by type"),
                _table(["Scope", "Input", "Output", "Cache read", "Cache write", "Total"],
                       P.cost_tokens, _token_row, "No tokens recorded yet.", id="cost-tokens")),
        s.panel(s.section_head("Waste"),
                _table(["Kind", "Count", "Cost", "Unknown"], P.cost_waste, _waste_row,
                       "No step has finished here yet.", id="cost-waste")),
        s.panel(s.section_head("Anomalies"),
                _table(["Kind", "Unit", "Stage", "Ended", "Measured", "Cost"], P.cost_anomalies,
                       _anomaly_row, "Nothing crossed a threshold.", id="cost-anomalies")),
        spacing="5", width="100%",
    )


def _unit_cost() -> rx.Component:
    """`0093` R11. The open unit's cost by stage and its anomalies; nothing when it has none."""
    return rx.fragment(
        rx.cond(
            P.unit_cost_stages.length() > 0,
            s.panel(s.section_head("Cost by stage"),
                    _table(["Stage"] + SPEND_HEADERS, P.unit_cost_stages, _spend_row, "",
                           id="unit-cost")),
        ),
        rx.cond(
            P.unit_anomalies.length() > 0,
            s.panel(s.section_head("Anomalies"),
                    _table(["Kind", "Stage", "Ended", "Measured", "Cost"], P.unit_anomalies,
                           _unit_anomaly_row, "", id="unit-anomalies")),
        ),
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


def _name_list(label: str, names: rx.Var[list[str]]) -> rx.Component:
    """One badge per tool or command, or "none"."""
    return rx.flex(
        s.text(label, size="1", weight="medium", width="80px", flex_shrink="0"),
        rx.cond(names.length() == 0, s.text("none", size="1"),
                rx.foreach(names, lambda n: s.badge(n, "gray"))),
        gap="6px", wrap="wrap", align="center", width="100%", margin_bottom="6px",
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
        s.text(grant.consequence, size="1", margin_top="6px"),
        # `0082` F2: a grant's tools and commands are lists (S5), shown only when opened, as
        # the next-step panel's are (D72).
        _details("grant-" + grant.stage, "What it may use",
                 _name_list("Tools", grant.tool_list), _name_list("Commands", grant.command_list),
                 margin_top="4px"),
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


def _autopilot_settings() -> rx.Component:
    """`0043` R2. This workspace's autopilot; the cap is the whole app's. A refusal comes
    back from `Service.set_autopilot` as the page's notice, verbatim."""
    return s.panel(
        s.section_head("Autopilot", rx.icon("bot", size=18, color=s.MUTED)),
        _settings_row(
            "Run the next stage", "Starts each unit's next stage without a press, and spends quota.",
            # S8: off loopback it cannot be turned on, and the reason stands in its place.
            rx.cond((P.ap_refused != "") & ~P.ap_on, s.text(P.ap_refused, size="1", max_width="320px"),
                    rx.switch(checked=P.ap_on, on_change=P.set_autopilot_on, id="autopilot-on",
                              aria_label="Autopilot")),
        ),
        _settings_row(
            "May ship", "Merges to main under this machine's gh login, with nobody looking.",
            rx.switch(checked=P.ap_may_ship, on_change=P.set_autopilot_may_ship, id="autopilot-ship",
                      aria_label="Autopilot may ship"),
        ),
        _settings_row(
            "Sessions at once", "Counts the steps a person starts too.",
            rx.hstack(
                rx.input(value=P.ap_max_parallel, on_change=P.edit_ap_max_parallel, size="1", width="72px",
                         aria_label="Sessions at once", id="autopilot-parallel"),
                rx.button("Save", on_click=P.save_ap_max_parallel, size="1"),
                align="center",
            ),
        ),
        _settings_row(
            "Daily cap (USD)", "One cap for every workspace; a person's press is never held.",
            rx.hstack(
                rx.input(value=P.ap_cap, on_change=P.edit_ap_cap, size="1", width="72px",
                         aria_label="Daily cap in USD", id="autopilot-cap"),
                rx.button("Save", on_click=P.save_ap_cap, size="1"),
                align="center",
            ),
        ),
        id="autopilot-panel",
    )


def _settings() -> rx.Component:
    return rx.vstack(
        s.heading("Make it feel like yours.",
                  "A considered default. A few thoughtful choices."),
        rx.cond(P.has_workspace, _autopilot_settings(), rx.fragment()),
        s.panel(
            s.section_head("Appearance", rx.icon("palette", size=18, color=s.MUTED)),
            _settings_row(
                "Color mode", "Kept by this browser.",
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
                "Kept for this machine.",
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
                _settings_row("Workspaces", "Where your projects live.",
                              s.badge(rx.cond(P.working_dir != "", "set", "not set"), "gray")),
                _settings_row("App data", "Backing it up does not back up your workspaces.",
                              s.badge("set", "gray")),
                _settings_row("Address",
                              rx.cond(P.loopback_only, "This machine only.",
                                      "Reachable from the network, behind the password."),
                              s.badge(rx.cond(P.loopback_only, "local", "network"),
                                      rx.cond(P.loopback_only, "grass", "amber"))),
                _settings_row("Fallback model", "Used by chat.", s.badge(P.model, "gray")),
                # `0082` D39, D40, D43: the paths, the address and the variable names.
                _details("where", "Details",
                         s.text("workspaces (COS_WORKING_DIR): "
                                + rx.cond(P.working_dir != "", P.working_dir, "not set"),
                                size="1", id="working-dir", font_family=_MONO, overflow_wrap="anywhere"),
                         s.text("data (COS_DATA_DIR): " + P.data_dir, size="1", id="data-dir",
                                font_family=_MONO, overflow_wrap="anywhere"),
                         s.text("address (COS_HOST, COS_PORT): " + P.host_port, size="1", font_family=_MONO),
                         s.text("fallback model: COS_MODEL", size="1", font_family=_MONO),
                         margin_top="10px", id="data-roots"),
            ),
            s.panel(
                s.section_head("What a chat session may do",
                               rx.icon("shield-check", size=18, color=s.MUTED)),
                s.text("Set when the app starts.", size="1"),
                rx.foreach(P.knobs, _knob_row),
                id="knobs-panel",
            ),
            columns=rx.breakpoints(initial="1", lg="2"), gap="16px", width="100%",
            align_items="start",
        ),
        # `0044` R8a. What Jera may cite besides earlier answers, one paragraph per entry.
        s.panel(
            s.section_head("Decision preferences", rx.icon("scroll-text", size=18, color=s.MUTED)),
            s.text("Jera reads this word for word as precedent, so keep company names out of it.",
                   size="1"),
            rx.text_area(
                value=P.decision_preferences, on_change=P.edit_decision_preferences,
                placeholder="One preference per paragraph.", aria_label="Decision preferences",
                rows="5", width="100%", margin_top="10px", id="decision-preferences",
            ),
            rx.button("Save", on_click=P.save_decision_preferences, size="1", margin_top="8px",
                      id="save-decision-preferences"),
            id="preferences-panel",
        ),
        s.panel(
            s.section_head("What a board step may do",
                           rx.icon("key-round", size=18, color=s.MUTED)),
            s.text("A stage not listed gets no tools.", size="1"),
            rx.foreach(P.grants, _grant_row),
            id="grants-panel",
        ),
        s.panel(
            s.section_head("Which model runs each stage",
                           rx.icon("cpu", size=18, color=s.MUTED)),
            s.text("A change applies to the next session a step or a chat starts.", size="1"),
            rx.foreach(P.model_problems,
                       lambda p: rx.callout(p, icon="circle_alert", color_scheme="red",
                                            variant="surface", size="1", margin_top="8px")),
            rx.foreach(P.model_rows, _model_row),
            id="models-panel",
        ),
        # `0082` R9: the update panel lives here now.
        _update_panel(),
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
                      rx.button(rx.icon("eye", size=13), "View",
                                on_click=P.open_watch(run.run, P.unit_id + " · " + run.stage, P.unit_id),
                                class_name="watch-run", variant="soft", size="1"),
                      spacing="2", wrap="wrap", align="center", width="100%"),
            s.text(rx.cond(run.ended != "", run.started + " → " + run.ended, run.started + " · running"),
                   size="1"),
            s.text(run.tokens + " tokens / " + run.usd, size="1"),
            rx.cond(run.session_id != "—",
                    _details("run-" + run.key, "Details",
                             s.text("session " + run.session_id, size="1",
                                    font_family="ui-monospace, monospace"))),
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
            rx.spacer(),
            # `0044` R10: whose answer is in force, or that Jera left it to a person.
            rx.cond(q.by_jera, s.badge("Answered by Jera", "iris")),
            rx.cond(q.needs_person, s.badge("Needs a person", "red")),
            width="100%", align="center",
        ),
        rx.box(rx.markdown(q.text), width="100%", margin_top="8px"),
        rx.cond(
            q.by_jera,
            rx.vstack(
                s.text("Jera's answer", size="1", weight="medium"),
                rx.box(rx.markdown(q.said), width="100%", data_testid="jera-said"),
                rx.flex(
                    s.text("Precedent", size="1", weight="medium"),
                    rx.foreach(q.cites, lambda c: s.badge(c, "gray")),
                    gap="6px", wrap="wrap", align="center", width="100%",
                ),
                spacing="1", width="100%", margin_top="8px", align="start",
            ),
        ),
        rx.cond(
            q.needs_person,
            rx.vstack(
                s.text("Jera's proposal", size="1", weight="medium"),
                rx.box(rx.markdown(q.proposal), width="100%"),
                s.text(q.reason, size="1", color=rx.color("red", 11)),
                spacing="1", width="100%", margin_top="8px", align="start",
            ),
        ),
        # `0056` R11: a dropped unit is read, not answered. `0082` R11: nor a finished or
        # closed one — the service's `answerable` says which.
        rx.cond(~P.current_unit.answerable, s.badge("Not answered", "gray")),
        rx.cond(
            ~P.unit_dropped & P.current_unit.answerable,
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
    """`0016` R2 and R10. What an answer is and is not is in `.claude/CLAUDE.md` (`0082`
    D12); the page says one sentence, and asks no name (R3)."""
    return rx.vstack(
        s.text(rx.cond(P.current_unit.answerable,
                       "Your answer is added under ## Answers; the next step reads it.",
                       "This unit is finished; its questions are shown to read."),
               size="1"),
        # `0044`. Hidden, not greyed, when there is nothing Jera may answer (S8).
        rx.cond(
            P.jera_can_ask & ~P.unit_dropped,
            rx.hstack(
                rx.button(
                    rx.icon("scroll-text", size=14), "Ask Jera",
                    on_click=P.ask_jera, loading=P.asking_jera, disabled=P.asking_jera,
                    size="1", id="ask-jera",
                ),
                s.text(CONSEQUENCE["precedent"], size="1", id="ask-jera-consequence"),
                spacing="2", align="center", wrap="wrap", width="100%",
            ),
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
            # `0082` R13: one state; the count only when there is one.
            rx.cond((u.integration_behind != "") & (u.integration_behind != "0"),
                    s.text(u.integration_behind + " commits behind main, as of the last fetch",
                           size="1", margin_top="8px", id="integration-behind")),
            rx.cond(u.integration_reason != "",
                    s.text(u.integration_reason, size="1", overflow_wrap="anywhere")),
            rx.foreach(u.integration_needs_person,
                       lambda n: s.text("[needs-person] " + n, size="1", color=rx.color("red", 11))),
            rx.cond(
                u.integrate_button,
                rx.vstack(
                    s.text(CONSEQUENCE["integrate"], size="1", id="integration-consequence"),
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
                    s.text("Adds an outcome block to intent.md.", size="1"),
                    rx.select(list(present.RESULT_LABEL.values()), value=P.outcome_result,
                              on_change=P.set_outcome_result, size="1", id="outcome-result"),
                    rx.input(placeholder="Source — where the figure came from",
                             value=P.outcome_source, on_change=P.set_outcome_source,
                             width="100%", id="outcome-source"),
                    rx.input(placeholder="Reason — why it could not be measured",
                             value=P.outcome_reason, on_change=P.set_outcome_reason,
                             width="100%", id="outcome-reason"),
                    rx.hstack(
                        s.text("Measured by", size="1"),
                        rx.select(list(present.MEASURER_LABEL.values()), value=P.outcome_measured_by,
                                  on_change=P.set_outcome_measured_by, size="1",
                                  aria_label="Measured by", id="outcome-measured-by"),
                        spacing="2", align="center",
                    ),
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
    `hold_moves`. The *Drop* sentence is on screen before the button, because a drop closes
    a pull request with this machine's `gh` login. No name is asked (`0082` R3).
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
            rx.cond(u.hold_moves.contains("dropped"), s.text(hold_rules.DROP_WARNING, size="1", id="hold-drop-warning")),
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


# On a phone the actions take their own line and *By* is not shown, so the unit keeps room.
_BACKLOG_ACTIONS = rx.breakpoints(initial="100%", md="260px")
_BACKLOG_WIDE = rx.breakpoints(initial="none", md="block")


def _backlog_row(row: rx.Var[BacklogRow], shortlisted: bool) -> rx.Component:
    """`0082` R10. One unit: its rank, estimate and who made it, and *Edit* in the row."""
    editing = P.backlog_editing == row.unit
    cell = lambda value, width, **kw: s.text(value, size="1", width=width, flex_shrink="0", **kw)  # noqa: E731
    return rx.box(
        rx.hstack(
            # `0082` F4: a unit with no estimate has no computed place, and reads "—" like its value.
            cell(rx.cond(row.rank > 0, "#" + row.rank.to_string(), "—"), "36px"),
            rx.text(row.unit, size="2", font_family=_MONO, flex="1", min_width="96px", overflow_wrap="anywhere"),
            cell(row.value, "44px"),
            cell(row.effort, "70px"),
            cell(row.by, "110px", overflow="hidden", text_overflow="ellipsis", white_space="nowrap",
                 display=_BACKLOG_WIDE),
            # The actions keep a fixed width, `_BACKLOG_ACTIONS`, so the cells line up with the head.
            rx.hstack(
                rx.cond(row.drift != "", s.badge(row.drift, "amber")),
                *([rx.hstack(
                    s.icon_button("arrow-up", "Move " + row.unit + " up", on_click=P.shortlist_move(row.unit, -1), size="1"),
                    s.icon_button("arrow-down", "Move " + row.unit + " down", on_click=P.shortlist_move(row.unit, 1), size="1"),
                    spacing="1",
                )] if shortlisted else []),
                rx.cond(P.shortlist_draft.contains(row.unit),
                        rx.button("Remove", on_click=P.shortlist_remove(row.unit), size="1", variant="ghost"),
                        rx.button("Add to shortlist", on_click=P.shortlist_add(row.unit), size="1", variant="ghost")),
                rx.button(rx.cond(editing, "Close", "Edit"), on_click=P.edit_backlog_row(row.unit),
                          size="1", variant="soft"),
                width=_BACKLOG_ACTIONS, flex_shrink="0", justify="end", align="center", spacing="3", flex_wrap="wrap",
            ),
            width="100%", align="center", spacing="3", flex_wrap="wrap",
        ),
        rx.cond(row.warnings != "", s.text(row.warnings, size="1", color=rx.color("amber", 11))),
        rx.cond(editing, _backlog_editor(row)),
        width="100%", padding="10px 0", border_bottom=f"1px solid {s.LINE}", data_testid="backlog-row",
    )


def _backlog_editor(row: rx.Var[BacklogRow]) -> rx.Component:
    """The open row's estimate and relation forms; the unit is the row's, never typed."""
    pick = lambda name, options, value: s.native_select(  # noqa: E731
        *options, value=value, on_change=lambda v: P.set_backlog_field(name, v))
    return rx.vstack(
        rx.cond(row.basis != "", s.text(row.basis, size="1", overflow_wrap="anywhere")),
        rx.cond(row.agent_differs != "", s.text(row.agent_differs, size="1", overflow_wrap="anywhere")),
        rx.hstack(
            pick("est_value", [rx.el.option("Value", value="")] + [rx.el.option(str(v), value=str(v)) for v in range(1, 6)],
                 P.est_value),
            pick("est_effort", [rx.el.option("Effort", value="")] + [rx.el.option(e, value=e) for e in ("S", "M", "L")],
                 P.est_effort),
            rx.input(placeholder="Basis", value=P.est_basis, size="1", flex="1",
                     on_change=lambda v: P.set_backlog_field("est_basis", v), aria_label="Basis for the estimate"),
            rx.button("Save estimate", on_click=P.save_estimate, size="1"),
            width="100%", flex_wrap="wrap", align="center",
        ),
        rx.hstack(
            pick("rel_type", [rx.el.option(label, value=key) for key, label in present.RELATION_LABEL.items()], P.rel_type),
            s.native_select(rx.el.option("Other unit", value=""),
                            rx.foreach(P.cards, lambda c: rx.cond(c.id != row.unit, rx.el.option(c.id, value=c.id),
                                                                  rx.fragment())),
                            value=P.rel_other, on_change=lambda v: P.set_backlog_field("rel_other", v)),
            pick("rel_op", [rx.el.option("add", value="add"), rx.el.option("remove", value="remove")], P.rel_op),
            rx.input(placeholder="Why", value=P.rel_reason, size="1", flex="1",
                     on_change=lambda v: P.set_backlog_field("rel_reason", v), aria_label="Why this relation"),
            rx.button("Save relation", on_click=P.save_relation, size="1"),
            width="100%", flex_wrap="wrap", align="center",
        ),
        rx.foreach(P.history_lines, lambda line: s.text(line, size="1", overflow_wrap="anywhere")),
        width="100%", spacing="2", padding="10px 0 4px 36px",
    )


def _backlog_screen() -> rx.Component:
    """`0074`, on its own route since `0082` R10: the shortlist and the rest as one table.

    Display only: nothing here reaches the run button, a gate or `next` (`0074` R15)."""
    head = rx.hstack(
        *(s.text(label, size="1", weight="medium", width=w, flex_shrink="0") for label, w in
          (("Rank", "36px"),)),
        s.text("Unit", size="1", weight="medium", flex="1", min_width="96px"),
        s.text("Value", size="1", weight="medium", width="44px", flex_shrink="0"),
        s.text("Effort", size="1", weight="medium", width="70px", flex_shrink="0"),
        s.text("By", size="1", weight="medium", width="110px", flex_shrink="0", display=_BACKLOG_WIDE),
        rx.box(width="260px", flex_shrink="0", display=_BACKLOG_WIDE),
        width="100%", spacing="3", flex_wrap="wrap", padding="0 0 6px", border_bottom=f"1px solid {s.LINE}",
    )
    return rx.vstack(
        s.heading("Backlog", "What to take next, in order."),
        s.panel(
            s.section_head("Shortlist",
                           rx.button("Take the first 7", on_click=P.fill_shortlist, size="1", variant="soft")),
            rx.cond(P.backlog_note != "", s.text(P.backlog_note, size="1")),
            rx.cond(P.backlog_measured != "", s.text(P.backlog_measured, size="1")),
            rx.cond(P.backlog_recorded != "", s.text(P.backlog_recorded, size="1", margin_bottom="8px")),
            head,
            rx.foreach(P.backlog_rows, lambda r: _backlog_row(r, True)),
            # `0082` F3: one sentence for an empty shortlist, whether or not one was ever saved.
            rx.cond(P.backlog_rows.length() == 0,
                    s.text(rx.cond(P.backlog_recorded != "", "No unit is on the saved shortlist.",
                                   "No shortlist saved yet."), size="1", padding="10px 0")),
            rx.hstack(
                s.text(P.shortlist_draft.length().to_string() + " of 7 chosen", size="1"),
                rx.input(placeholder="Why this order", value=P.shortlist_reason, size="1", flex="1",
                         on_change=lambda v: P.set_backlog_field("shortlist_reason", v),
                         aria_label="Why this order", id="backlog-shortlist_reason"),
                rx.button("Save shortlist", on_click=P.save_shortlist, size="1", id="backlog-save-shortlist"),
                width="100%", align="center", margin_top="12px", flex_wrap="wrap",
            ),
            rx.foreach(P.backlog_warnings, lambda w: s.text(w, size="1", color=rx.color("amber", 11))),
        ),
        s.panel(
            s.section_head("The rest, in computed order"),
            head,
            rx.foreach(P.backlog_rest, lambda r: _backlog_row(r, False)),
            rx.cond(P.backlog_rest.length() == 0, s.text("Nothing else waits.", size="1", padding="10px 0")),
        ),
        s.panel(
            rx.hstack(
                rx.button(rx.icon("sparkles", size=14), "Propose estimates", on_click=P.propose_estimates,
                          loading=P.proposing, disabled=P.proposing, size="1", variant="soft", id="backlog-propose"),
                s.text(P.propose_warning, size="1", id="backlog-propose-warning"),
                align="center", spacing="3", flex_wrap="wrap",
            ),
        ),
        spacing="5", width="100%", id="backlog-panel",
    )


def _collapsed_groups() -> rx.Component:
    """`0100` R8 (`intent.md ## Answers, câu 4`). Done, paused and dropped units, each in a
    closed group with its count at the foot of the board; a group of none is not drawn."""
    return rx.vstack(
        *(_collapsed_group(state, label) for state, label in
          (("done", "Done"), ("paused", "Paused"), ("dropped", "Dropped"))),
        spacing="3", width="100%",
    )


def _collapsed_group(state: str, label: str) -> rx.Component:
    count = P.group_counts[state]
    return rx.cond(
        count > 0,
        rx.el.details(
            rx.el.summary(s.text(label + " (" + count.to_string() + ")", size="2", as_="span"),
                          cursor="pointer"),
            rx.grid(
                rx.foreach(P.cards, lambda c: rx.cond(
                    c.state == state, _unit_card(c, grouped=True), rx.fragment())),
                columns=rx.breakpoints(initial="1", sm="2", lg="4"),
                gap="12px", width="100%", margin_top="12px",
            ),
            width="100%", id=f"{state}-group",
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
    """`0021` R4, R10. What a comment is not is `.claude/docs/not-built.md`'s since `0089`."""
    return rx.vstack(
        s.text("Each review round is posted to the pull request as one comment.", size="1"),
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
                    s.badge(P.current_unit.state_label, P.current_unit.state_color),
                    # `0082` R12, `0100` C3: what a unit waits on, beside its state.
                    rx.cond(P.current_unit.attention_reason != "",
                            s.badge(P.current_unit.attention_reason, "amber")),
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
                        + P.current_unit.hold_by + ", " + P.current_unit.hold_date + ".",
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
                                # `0082` D69: the findings as a list, not a comma run.
                                rx.hstack(
                                    s.text("Needs a person", size="2"),
                                    rx.foreach(P.run_waiting, lambda f: s.badge(f, "amber")),
                                    spacing="2", align="center", flex_wrap="wrap", id="next-waiting",
                                ),
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
                                _settings_row(
                                    "Mode",
                                    "",
                                    rx.segmented_control.root(
                                        rx.segmented_control.item("Manual", value="manual"),
                                        rx.segmented_control.item("Auto", value="autonomous"),
                                        value=P.next_cell.mode, on_change=P.set_mode, size="1",
                                    ),
                                ),
                                # `0082` D9: one line of what the step may do; the grant's
                                # tools and its full warning only inside *Details*.
                                rx.hstack(
                                    s.badge(P.next_stage, "iris"),
                                    s.text(rx.cond(P.next_cell.opens_tools, "with tools", "no tools"),
                                           size="1", id="next-grants"),
                                    _details("grant", "What it may use",
                                             s.text(P.next_cell.grants, size="1", overflow_wrap="anywhere"),
                                             rx.cond(P.next_cell.warning != "",
                                                     s.text(P.next_cell.warning, size="1", id="next-warning",
                                                            overflow_wrap="anywhere"))),
                                    align="center", spacing="3", flex_wrap="wrap", width="100%",
                                ),
                                rx.button(
                                    rx.icon("play", size=15),
                                    "Run " + P.next_stage + " — spends quota",
                                    id="run-step", on_click=P.run_step,
                                    disabled=P.running_here | ~P.recording,
                                    loading=P.running_here, width="100%",
                                ),
                                s.text(P.next_cell.consequence, size="1", id="run-consequence"),
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
                                s.text("Cuts the unit's branch from a fresh main, in its own worktree.",
                                       size="1"),
                                rx.cond(
                                    P.unit_tree != "",
                                    _details("tree", "Worktree",
                                             s.text(P.unit_tree, id="unit-tree", size="1",
                                                    overflow_wrap="anywhere")),
                                ),
                                rx.cond(
                                    ~P.recording,
                                    s.text("No working folder is set, so a run cannot be "
                                           "recorded and will not start.", size="1"),
                                ),
                                spacing="4", width="100%", align="start",
                            ),
                            s.text("No stage is ready to run; the line above says why."),
                        ),
                        # `0100` R7. What the board last heard from CI, and when.
                        rx.cond(P.current_unit.ci_line != "",
                                s.text(P.current_unit.ci_line, id="unit-ci-line", size="2")),
                        rx.cond(~P.unit_dropped, _integration_panel()),
                        rx.cond(~P.unit_dropped, _outcome_panel()),
                        _unit_cost(),
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
                        s.text("Every run of this unit, oldest first.", size="1"),
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
# anchors too; *Jump to latest* drops the anchor and goes to the bottom. `data-seq` is watched as
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
                    rx.button("Collapse", on_click=P.watch_collapse, size="1", variant="ghost",
                              class_name="watch-collapse"),
                    rx.button("Expand", on_click=P.watch_expand(e.seq), size="1", variant="ghost",
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
        rx.cond(e.collapsed & ~opened, s.text("… collapsed; press Expand to see all", size="1")),
        rx.cond(e.truncated,
                s.text("cut when stored: kept the first 64 000 of " + e.original_length.to_string() + " characters",
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
                rx.dialog.close(s.icon_button("x", "Close")),
                width="100%", align="center",
            ),
            rx.dialog.description(
                "This step's events, oldest first.",
                size="1", margin_top="6px",
            ),
            rx.cond(P.watch_note != "", rx.callout(P.watch_note, id="watch-note", size="1",
                                                   color_scheme="amber", margin_top="8px")),
            rx.button("Load older events", id="watch-older", on_click=P.watch_older,
                      disabled=~P.watch_has_older, variant="ghost", size="1", margin_top="8px"),
            rx.box(
                rx.box(id="watch-top", height="1px"),
                rx.foreach(P.watch_events, _watch_row),
                id="watch-list", max_height="62vh", overflow_y="auto", width="100%",
                style={"overflow_anchor": "none"},
            ),
            rx.hstack(
                rx.cond(P.watch_pending > 0,
                        s.text(P.watch_pending.to_string() + " new events", id="watch-pending", size="1")),
                rx.cond(P.watch_has_newer,
                        s.text("Newer events have left this view", id="watch-newer", size="1")),
                rx.cond(P.watch_has_newer | (~P.watch_following & (P.watch_status == "running")),
                        rx.button("Jump to latest", id="watch-live", on_click=P.watch_live, size="1")),
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
        ("backlog", _backlog_screen()),
        ("sessions", _sessions()),
        ("activity", _activity()),
        ("cost", _cost()),
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
