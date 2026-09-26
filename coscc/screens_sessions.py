"""The Sessions, Activity and Cost screens.
Split from `coscc/screens.py` (`0095`), which re-exports every name.
"""

from __future__ import annotations

import reflex as rx

from coscc import spend
from coscc import studio as s
from coscc.state import AnomalyRow, Message, SpendRow, TokenRow, WasteRow
from coscc.screens_common import P, _MONO, _details, _mono, _table
from coscc.screens_chrome import _event_row, _metrics
from coscc.screens_board import _update_warning


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
