"""The Overview and Workspaces screens.
Split from `coscc/screens.py` (`0095`), which re-exports every name.
"""

from __future__ import annotations

import reflex as rx

from coscc import studio as s
from coscc.state import Activity, Workspace
from coscc.screens_common import P, _details
from coscc.screens_chrome import _event_row, _metrics


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
