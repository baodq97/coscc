"""The pieces more than one screen draws with: a collapsible section, a table, monospace text.
Split from `coscc/screens.py` (`0095`), which re-exports every name.
"""

from __future__ import annotations

import reflex as rx

from coscc import studio as s
from coscc.state import StudioState


P = StudioState


def _details(key: str, label: str, *children, **props) -> rx.Component:
    """A closed-by-default `studio.details` keyed in `StudioState.open_details`."""
    return s.details(P.open_details.contains(key), P.toggle_details(key), label, *children, **props)


_MONO = "ui-monospace, monospace"


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
