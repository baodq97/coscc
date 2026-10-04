"""The Agents screen: the eight agents in one table, and one agent's drawer beside it.

The table says how each agent is set and how its last run went; the drawer, at
`/agents?agent=<stage>`, holds what the table leaves out and where the fields are set. What may
be set, and to what, is `Agents.set_agent_field`'s call. The grant is drawn and never written:
no box, button or handler on this screen touches it.
"""

from __future__ import annotations

import reflex as rx

from coscc.agent import models
from coscc.screens import studio as s
from coscc.screens.chrome import _banners
from coscc.screens.common import _RUNIC, P, _details, _mono, _table
from coscc.state.views import AgentListRow, AgentRunRow, OtherRow

# The sentence beside the boxes that raise what a step may spend (S2).
CEILING_NOTE = "A higher turn or cost ceiling lets each step spend more."
CHIPS = ("all", "failed", "costly", "idle", "ok")


def _source_badge(source, overridden) -> rx.Component:
    return s.badge(source, rx.cond(overridden, "amber", "gray"))


def _field_form(
    row,
    field,
    label,
    draft,
    source,
    overridden,
    select=False,
) -> rx.Component:
    """One box of one row: its value, a badge saying where the value came from, *Save*, and
    *Reset* only while an override is there to clear (S8). The plain element, not `rx.form`,
    which would add a Radix package to the bundle."""
    text_box = rx.input(
        name="value",
        default_value=draft,
        placeholder="Not set",
        aria_label=label,
        size="1",
        flex="1",
        min_width="120px",
    )
    pick = s.native_select(
        # Saved, it resets the override (`Agents.set_agent_field`).
        rx.el.option("Default", value=""),
        *[rx.el.option(e, value=e) for e in models.EFFORTS],
        name="value",
        default_value=draft,
        aria_label=label,
        flex="1",
    )
    control = (
        pick if select is True else text_box if select is False else rx.cond(select, pick, text_box)
    )
    return rx.el.form(
        rx.el.input(type="hidden", name="key", value=row),
        rx.el.input(type="hidden", name="field", value=field),
        rx.flex(
            rx.hstack(
                s.text(label, size="1", weight="medium"),
                _source_badge(source, overridden),
                spacing="2",
                align="center",
                width="190px",
                flex_shrink="0",
            ),
            control,
            rx.button("Save", type="submit", size="1"),
            rx.cond(
                overridden,
                rx.button(
                    "Reset",
                    type="button",
                    on_click=P.reset_agent_field(row, field),
                    size="1",
                    variant="soft",
                ),
            ),
            gap="8px",
            align="center",
            wrap="wrap",
            width="100%",
        ),
        on_submit=P.save_agent_field,
        reset_on_submit=False,
        # A new key after a save or a reset, so the box shows the value now in force.
        key=row + field + draft + source,
        width="100%",
        data_testid="agent-field",
    )


def _field_row(f) -> rx.Component:
    return _field_form(
        f.row, f.field, f.label, f.draft, f.source, f.overridden, select=f.field == "effort"
    )


# --- the table ---------------------------------------------------------------


def _agent_row(row: rx.Var[AgentListRow]) -> rx.Component:
    # The chip and the last run come first, so a phone shows what needs attention unscrolled.
    return rx.table.row(
        rx.table.cell(
            rx.hstack(
                rx.text(row.glyph, font_family=_RUNIC, size="4", aria_hidden="true"),
                rx.vstack(
                    rx.button(
                        row.name,
                        variant="ghost",
                        color_scheme="gray",
                        size="2",
                        aria_label="Open " + row.name,
                        padding="0",
                        height="auto",
                    ),
                    rx.hstack(
                        s.text(row.key, size="1"),
                        s.badge(row.chip, row.color),
                        spacing="2",
                        align="center",
                    ),
                    spacing="1",
                    align="start",
                ),
                align="center",
                spacing="3",
            )
        ),
        rx.table.cell(
            rx.vstack(
                rx.text(row.outcome, size="2", white_space="nowrap"),
                s.text(row.when, size="1", white_space="nowrap"),
                spacing="0",
                align="start",
            )
        ),
        rx.table.cell(_mono(row.cost)),
        rx.table.cell(_mono(row.model)),
        rx.table.cell(_mono(row.effort)),
        rx.table.cell(_mono(row.turns)),
        rx.table.cell(_mono(row.budget)),
        on_click=P.open_agent(row.key),
        cursor="pointer",
        _hover={"background": rx.color("gray", 3)},
        data_testid="agent-row",
        data_chip=row.chip,
        data_agent=row.key,
    )


def _problems() -> rx.Component:
    return rx.foreach(
        P.agent_problems,
        lambda p: rx.callout(
            p,
            icon="circle_alert",
            color_scheme="red",
            variant="surface",
            size="1",
            width="100%",
            data_testid="agent-problem",
        ),
    )


def _filter() -> rx.Component:
    return rx.segmented_control.root(
        *[rx.segmented_control.item(c.capitalize() if c == "all" else c, value=c) for c in CHIPS],
        value=rx.cond(P.agent_chip == "", "all", P.agent_chip),
        on_change=P.set_agent_chip,
        size="1",
        id="agent-filter",
    )


def _other_row(row: rx.Var[OtherRow]) -> rx.Component:
    """One session's boxes stacked under its name, which wrap on a phone where table cells
    would run off the screen."""
    return rx.vstack(
        rx.text(row.key, size="2", weight="medium"),
        _field_form(
            row.key,
            "model",
            "Model of " + row.key,
            row.model.draft,
            row.model.source,
            row.model.overridden,
        ),
        rx.cond(
            row.has_effort,
            _field_form(
                row.key,
                "effort",
                "Effort of " + row.key,
                row.effort.draft,
                row.effort.source,
                row.effort.overridden,
                select=True,
            ),
        ),
        spacing="3",
        width="100%",
        align="start",
        data_testid="other-row",
    )


def _others() -> rx.Component:
    return s.panel(
        s.section_head("Other sessions"),
        rx.vstack(rx.foreach(P.agent_others, _other_row), spacing="5", width="100%"),
        id="other-sessions",
    )


# --- the drawer --------------------------------------------------------------


def _names(label: str, names: rx.Var[list[str]]) -> rx.Component:
    """One badge per tool or command, or "none"."""
    return rx.flex(
        s.text(label, size="1", weight="medium", width="90px", flex_shrink="0"),
        rx.cond(
            names.length() == 0,
            s.text("none", size="1"),
            rx.foreach(names, lambda n: s.badge(n, "gray")),
        ),
        gap="6px",
        wrap="wrap",
        align="center",
        width="100%",
    )


def _run_row(run: rx.Var[AgentRunRow]) -> rx.Component:
    return rx.table.row(
        rx.table.cell(rx.text(run.outcome, size="1")),
        rx.table.cell(s.text(run.when, size="1", white_space="nowrap")),
        rx.table.cell(_mono(run.turns)),
        rx.table.cell(_mono(run.cost)),
        data_testid="agent-run",
    )


def _section(title: str, *children, **props) -> rx.Component:
    return rx.vstack(
        rx.heading(title, size="3", weight="medium"),
        *children,
        spacing="3",
        width="100%",
        align="start",
        **props,
    )


def _grant() -> rx.Component:
    d = P.agent_detail
    return _section(
        "What it may do",
        rx.flex(
            s.text("Skill", size="1", weight="medium", width="90px", flex_shrink="0"),
            _mono(d.skill),
            gap="6px",
            align="center",
            width="100%",
        ),
        _names("Tools", d.tools),
        _names("MCP tools", d.mcp),
        rx.flex(
            s.text("Submits", size="1", weight="medium", width="90px", flex_shrink="0"),
            s.badge(d.submits, "gray"),
            gap="6px",
            align="center",
            width="100%",
        ),
        # A long list stays closed until it is opened (S3).
        rx.cond(
            d.commands.length() == 0,
            _names("Commands", d.commands),
            _details(
                "agent-commands",
                "Commands (" + d.commands.length().to_string() + ")",
                _names("Commands", d.commands),
            ),
        ),
        rx.cond(
            d.warning != "",
            s.text(d.warning, size="1", color=rx.color("amber", 11), overflow_wrap="anywhere"),
        ),
        id="agent-grant",
    )


def _identity() -> rx.Component:
    d = P.agent_detail

    def box(field: str, value, source, width: str) -> rx.Component:
        return rx.vstack(
            rx.hstack(
                s.text(field.capitalize(), size="1", weight="medium"),
                _source_badge(source, source == "override"),
                rx.cond(
                    source == "override",
                    rx.button(
                        "Reset",
                        type="button",
                        on_click=P.reset_agent_field(d.key, field),
                        size="1",
                        variant="ghost",
                    ),
                ),
                align="center",
                spacing="2",
            ),
            rx.input(
                name=field,
                default_value=value,
                placeholder="Not set",
                aria_label=field.capitalize() + " of " + d.key,
                size="1",
                width="100%",
                font_family=_RUNIC if field == "glyph" else None,
            ),
            spacing="1",
            width=width,
            min_width=width if width != "100%" else "0",
            flex_grow="1",
        )

    return _section(
        "Who it is",
        rx.el.form(
            rx.el.input(type="hidden", name="key", value=d.key),
            rx.flex(
                box("glyph", d.glyph, d.glyph_source, "90px"),
                box("name", d.name, d.name_source, "160px"),
                box("meaning", d.meaning, d.meaning_source, "220px"),
                box("role", d.role, d.role_source, "100%"),
                gap="10px",
                wrap="wrap",
                width="100%",
            ),
            rx.button("Save", type="submit", size="1", margin_top="10px"),
            on_submit=P.save_agent_identity,
            reset_on_submit=False,
            key=d.key + d.glyph + d.name + d.meaning + d.role,
            width="100%",
            id="agent-identity",
        ),
    )


def _settings() -> rx.Component:
    d = P.agent_detail
    return _section(
        "Settings",
        rx.foreach(d.fields, _field_row),
        rx.cond(
            d.variants.length() > 0,
            rx.vstack(
                s.text("When the plan is novel", size="1", weight="medium"),
                rx.foreach(d.variants, _field_row),
                spacing="3",
                width="100%",
                align="start",
                id="agent-variants",
            ),
        ),
        s.text(CEILING_NOTE, size="1", id="agent-ceiling-note"),
        id="agent-settings",
    )


def _drawer() -> rx.Component:
    d = P.agent_detail
    return rx.dialog.root(
        rx.dialog.content(
            rx.box(
                _banners("agent"),
                position="sticky",
                top="0",
                z_index="2",
                background=s.CANVAS,
                padding=rx.cond((P.error != "") | (P.notice != ""), "12px 28px 12px", "0"),
            ),
            rx.vstack(
                rx.hstack(
                    rx.text(d.glyph, font_family=_RUNIC, size="6", aria_hidden="true"),
                    s.badge(d.key, "iris"),
                    s.badge(d.chip, d.color),
                    rx.spacer(),
                    rx.dialog.close(s.icon_button("x", "Close agent detail", flex_shrink="0")),
                    width="100%",
                    align="center",
                    spacing="3",
                ),
                rx.dialog.title(d.name, size="6", weight="medium", line_height="1.3"),
                rx.dialog.description(d.meaning, size="2", color=s.MUTED),
                _section(
                    "Last runs",
                    _table(["Outcome", "When", "Turns", "Cost"], d.runs, _run_row, "No run yet."),
                    id="agent-runs",
                ),
                _settings(),
                _grant(),
                _identity(),
                spacing="6",
                padding="28px",
                width="100%",
                align="start",
            ),
            position="fixed",
            right="0",
            top="0",
            left="auto",
            bottom="0",
            transform="none",
            width="min(620px, 100vw)",
            max_width="100vw",
            height="100dvh",
            max_height="100dvh",
            border_radius="0",
            padding="0",
            overflow_y="auto",
            background=s.CANVAS,
            aria_label="Agent detail",
            id="agent-drawer",
        ),
        open=P.agent_key != "",
        on_open_change=P.toggle_agent,
    )


# --- the screen --------------------------------------------------------------


def agents_screen() -> rx.Component:
    """The table, narrowed by the chip chosen, the other sessions below it, and the drawer."""
    return rx.vstack(
        s.heading("Agents", "How each agent is set, and how its last run went."),
        _problems(),
        s.panel(
            rx.hstack(
                _filter(),
                width="100%",
                justify="end",
                margin_bottom="12px",
            ),
            _table(
                [
                    "Agent",
                    "Last run",
                    "Cost, 30 days",
                    "Model",
                    "Effort",
                    "Turns",
                    "Cost ceiling",
                ],
                P.agent_shown,
                _agent_row,
                "No agent has that status.",
            ),
            id="agents-table",
        ),
        _others(),
        _drawer(),
        spacing="5",
        width="100%",
        id="agents-screen",
    )
