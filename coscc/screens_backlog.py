"""The Backlog screen: its rows, the editor and the shortlist.
Split from `coscc/screens.py` (`0095`), which re-exports every name.
"""

from __future__ import annotations

import reflex as rx

from coscc import present
from coscc import studio as s
from coscc.state import BacklogRow
from coscc.screens_common import P, _MONO


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
