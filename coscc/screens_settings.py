"""The Settings screen: knobs, grants, models and the autopilot's settings.
Split from `coscc/screens.py` (`0095`), which re-exports every name.
"""

from __future__ import annotations

import reflex as rx
from reflex.style import set_color_mode

from coscc import models
from coscc import studio as s
from coscc.state import GrantRow, Knob, ModelRow
from coscc.screens_common import P, _MONO, _details
from coscc.screens_board import _update_panel


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
