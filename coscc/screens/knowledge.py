"""The *Knowledge* screen: four tables and no button."""

from __future__ import annotations

import reflex as rx

from coscc.web import studio as s
from coscc.state.knowledge import KnowledgeEntry, KnowledgeGather, KnowledgeMetric, KnowledgeStep
from coscc.screens.common import P, _details, _mono, _table


def _entry_row(row: rx.Var[KnowledgeEntry]) -> rx.Component:
    return rx.table.row(
        rx.table.cell(_mono(row.id)),
        rx.table.cell(_mono(row.scope)),
        rx.table.cell(rx.vstack(
            s.text(row.statement, size="1"),
            _details("kn-" + row.id, "Workspace", s.text(row.slots, size="1")),
            spacing="1", min_width="260px",
        )),
        rx.table.cell(rx.vstack(rx.foreach(row.sources, lambda x: s.text(x, size="1", white_space="nowrap")),
                                spacing="1")),
        rx.table.cell(s.text(row.measured, size="1", white_space="nowrap")),
        # A reason may name a slot, a path or `PATH`: behind a closed detail.
        rx.table.cell(rx.vstack(s.badge(row.status, row.color),
                                rx.cond(row.reason != "", _details("kn-why-" + row.id, "Why",
                                                                   s.text(row.reason, size="1"))),
                                spacing="1")),
    )


def _gather_row(row: rx.Var[KnowledgeGather]) -> rx.Component:
    return rx.table.row(
        rx.table.cell(s.text(row.at, size="1", white_space="nowrap")),
        rx.table.cell(_mono(row.unit)),
        rx.table.cell(s.text(row.outcome, size="1")),
        rx.table.cell(_mono(row.cost)),
        rx.table.cell(rx.cond(row.reason != "", _details("kn-gather-why", "Why", s.text(row.reason, size="1")),
                              s.text("—", size="1"))),
    )


def _step_row(row: rx.Var[KnowledgeStep]) -> rx.Component:
    return rx.table.row(
        rx.table.cell(_mono(row.unit)),
        rx.table.cell(_mono(row.stage)),
        rx.table.cell(s.text(row.at, size="1", white_space="nowrap")),
        rx.table.cell(s.badge(row.arm, "iris")),
        rx.table.cell(_mono(row.carried)),
        rx.table.cell(rx.vstack(
            _mono(row.withheld),
            rx.cond(row.why.length() > 0, _details(row.key, "Why", rx.vstack(
                rx.foreach(row.why, lambda x: s.text(x, size="1")), spacing="1"))),
            spacing="1",
        )),
    )


def _metric_row(row: rx.Var[KnowledgeMetric]) -> rx.Component:
    return rx.table.row(
        rx.table.cell(s.text(row.metric, size="1")),
        rx.table.cell(_mono(row.on)),
        rx.table.cell(_mono(row.off)),
    )


def _badges(*children) -> rx.Component:
    """Beside a section's title, wrapping under it on a phone rather than past the panel."""
    return rx.flex(*children, wrap="wrap", gap="6px", width="100%")


def _knowledge() -> rx.Component:
    return rx.vstack(
        s.heading("Knowledge", "What earlier units measured, and whether it helps."),
        rx.cond(P.kn_log_note != "", rx.callout(P.kn_log_note, icon="info", color_scheme="amber",
                                                 variant="surface", width="100%")),
        s.panel(
            s.section_head("Entries"),
            _badges(s.badge(P.kn_checked, "gray")),
            rx.cond(
                P.kn_note != "",
                s.text(P.kn_note, size="1"),
                _table(["Id", "Scope", "Statement", "Sources", "Measured", "Status"], P.kn_entries,
                       _entry_row, "", id="knowledge-entries"),
            ),
        ),
        s.panel(s.section_head("Last gather"),
                _table(["When", "Unit", "Outcome", "Cost", "Reason"], P.kn_gathers, _gather_row,
                       "Nothing has been gathered here yet.", id="knowledge-gather")),
        s.panel(s.section_head("Recent steps"),
                _table(["Unit", "Stage", "When", "Arm", "Carried", "Withheld"], P.kn_steps, _step_row,
                       "No step has run with the store on yet.", id="knowledge-steps")),
        s.panel(
            s.section_head("Measure"),
            _badges(s.badge("Verdict: " + P.kn_verdict, "iris"), s.badge("Fewer turns: " + P.kn_reduction, "gray"),
                    s.badge("Due " + P.kn_deadline, "gray")),
            _table(["Metric", "On", "Off"], P.kn_metrics, _metric_row, "", id="knowledge-measure"),
        ),
        spacing="5", width="100%",
    )
