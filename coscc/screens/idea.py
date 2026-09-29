"""The `/idea` page: one idea several units share, the units it lists, and opening another."""

from __future__ import annotations

import reflex as rx

from coscc.web import studio as s
from coscc.state.views import ChildRow
from coscc.screens.common import P


def _child(row: rx.Var[ChildRow]) -> rx.Component:
    """One unit of the idea: where it lives, where it stands, and what it waits on."""
    return rx.table.row(
        rx.table.cell(
            rx.cond(
                row.missing,
                rx.text(row.unit, size="2", font_family="ui-monospace, monospace"),
                rx.link(
                    row.unit,
                    href=row.href,
                    size="2",
                    data_testid="idea-child",
                    font_family="ui-monospace, monospace",
                    white_space="nowrap",
                ),
            )
        ),
        rx.table.cell(rx.text(row.repo, size="2")),
        rx.table.cell(rx.text(row.stage, size="2")),
        rx.table.cell(s.badge(row.state, rx.cond(row.missing, "red", "gray"))),
        rx.table.cell(
            rx.cond(row.waits_for != "", s.badge(row.waits_for, "cyan"), s.text("—", size="1"))
        ),
    )


def _open_child() -> rx.Component:
    """Open a unit of this idea in one of the workspaces, optionally after one already listed."""
    return s.panel(
        rx.vstack(
            rx.heading("Open a unit from this idea", size="4", weight="medium"),
            rx.flex(
                rx.select(
                    P.idea_workspaces,
                    value=P.child_ws,
                    on_change=P.set_child_ws,
                    aria_label="Workspace",
                    id="idea-child-ws",
                ),
                rx.input(
                    placeholder="this-side-in-a-few-words",
                    value=P.child_slug,
                    on_change=P.set_child_slug,
                    aria_label="Slug for the unit",
                    id="idea-child-slug",
                    flex_grow="1",
                ),
                rx.cond(
                    P.idea_depends.length() > 0,
                    rx.select(
                        P.idea_depends,
                        value=P.child_depends,
                        on_change=P.set_child_depends,
                        placeholder="Depends on (optional)",
                        aria_label="Depends on",
                        id="idea-child-depends",
                    ),
                ),
                spacing="2",
                width="100%",
                wrap="wrap",
            ),
            rx.button(
                rx.icon("plus", size=15),
                "Open the unit",
                on_click=P.open_child,
                id="idea-child-open",
                size="2",
            ),
            width="100%",
            align="start",
            spacing="3",
        ),
        width="100%",
    )


def _idea_screen() -> rx.Component:
    return rx.vstack(
        s.heading(P.idea_title, P.idea_ref),
        rx.cond(
            P.idea_note != "",
            rx.callout(
                P.idea_note, icon="triangle_alert", color_scheme="red", variant="surface", size="1"
            ),
        ),
        rx.cond(
            P.idea_brief != "",
            s.panel(rx.text(P.idea_brief, size="2", white_space="pre-wrap"), width="100%"),
        ),
        s.panel(
            rx.vstack(
                rx.heading("Units", size="4", weight="medium"),
                rx.cond(
                    P.idea_units.length() > 0,
                    rx.box(
                        rx.table.root(
                            rx.table.header(
                                rx.table.row(
                                    *[
                                        rx.table.column_header_cell(h)
                                        for h in ("Unit", "Repo", "Stage", "State", "Waits for")
                                    ]
                                )
                            ),
                            rx.table.body(rx.foreach(P.idea_units, _child)),
                            size="1",
                            width="100%",
                        ),
                        width="100%",
                        overflow_x="auto",
                    ),
                    s.text("No unit is open yet.", size="1"),
                ),
                width="100%",
                spacing="3",
            ),
            width="100%",
        ),
        rx.cond(P.idea_ref != "", _open_child()),
        width="100%",
        spacing="4",
        align="start",
    )
