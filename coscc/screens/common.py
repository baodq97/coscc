"""The pieces more than one screen draws with: a collapsible section, a table, monospace text."""

from __future__ import annotations

import reflex as rx

from coscc.screens import studio as s
from coscc.state import StudioState


P = StudioState


def _details(key: str, label: str, *children, **props) -> rx.Component:
    """A closed-by-default `studio.details` keyed in `StudioState.open_details`."""
    return s.details(P.open_details.contains(key), P.toggle_details(key), label, *children, **props)


_MONO = "ui-monospace, monospace"
# The system's own runic fonts, no webfont: where none is installed the glyph is a box, and the stage's name beside it still says everything.
_RUNIC = '"Segoe UI Historic", "Noto Sans Runic", "Apple Symbols", sans-serif'


def _table(headers: list[str], rows, render, empty: str, **props) -> rx.Component:
    """A list as a table, scrolling sideways on a phone rather than wrapping."""
    return rx.box(
        rx.table.root(
            rx.table.header(rx.table.row(*[rx.table.column_header_cell(h) for h in headers])),
            rx.table.body(rx.foreach(rows, render)),
            size="1",
            variant="ghost",
            width="100%",
        ),
        rx.cond(rows.length() == 0, s.text(empty, size="1", margin_top="8px")),
        overflow_x="auto",
        width="100%",
        **props,
    )


def _mono(value) -> rx.Component:
    return rx.text(value, size="1", font_family="ui-monospace, monospace", white_space="nowrap")
