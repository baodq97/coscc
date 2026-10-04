"""The Settings screen: knobs, the autopilot's settings and the rest of what the app keeps. The agents are `agents.py`."""

from __future__ import annotations

import reflex as rx
from reflex.style import set_color_mode

from coscc.screens import studio as s
from coscc.service.common import CONSEQUENCE
from coscc.state import ImportRow, Knob
from coscc.state.views import DecisionRow
from coscc.screens.common import P, _MONO, _details, _table
from coscc.screens.board import _update_panel


# --- settings ----------------------------------------------------------------


def _settings_row(label, description, control: rx.Component, then=None) -> rx.Component:
    """`then`, when given, is one more line under `description`."""
    lines = [s.text(d, size="1", max_width="440px") for d in (description, then) if d is not None]
    return rx.flex(
        rx.vstack(
            rx.text(label, size="2", weight="medium"),
            *lines,
            spacing="1",
            min_width="0",
        ),
        rx.spacer(),
        control,
        width="100%",
        gap="16px",
        align="center",
        wrap="wrap",
        padding="20px 0",
        border_bottom=f"1px solid {s.LINE}",
    )


def _knob_row(knob: rx.Var[Knob]) -> rx.Component:
    return _settings_row(
        knob.label,
        knob.detail,
        s.badge(knob.value, rx.cond(knob.on, "amber", "grass")),
    )


def _name_list(label: str, names: rx.Var[list[str]]) -> rx.Component:
    """One badge per name, or "none"."""
    return rx.flex(
        s.text(label, size="1", weight="medium", width="80px", flex_shrink="0"),
        rx.cond(
            names.length() == 0,
            s.text("none", size="1"),
            rx.foreach(names, lambda n: s.badge(n, "gray")),
        ),
        gap="6px",
        wrap="wrap",
        align="center",
        width="100%",
        margin_bottom="6px",
    )


def _autopilot_settings() -> rx.Component:
    """This workspace's autopilot; the cap is the whole app's. A refusal comes back from `Autopilot.set_setting` as the page's notice, verbatim."""
    return s.panel(
        s.section_head("Autopilot", rx.icon("bot", size=18, color=s.MUTED)),
        _settings_row(
            "Run the next stage",
            "Starts each unit's next stage without a press, and spends quota.",
            # Off loopback it cannot be turned on, and the reason stands in its place.
            rx.cond(
                (P.ap_refused != "") & ~P.ap_on,
                s.text(P.ap_refused, size="1", max_width="320px"),
                rx.switch(
                    checked=P.ap_on,
                    on_change=P.set_autopilot_on,
                    id="autopilot-on",
                    aria_label="Autopilot",
                ),
            ),
        ),
        _settings_row(
            "May ship",
            "Merges to main under this machine's gh login, with nobody looking.",
            rx.switch(
                checked=P.ap_may_ship,
                on_change=P.set_autopilot_may_ship,
                id="autopilot-ship",
                aria_label="Autopilot may ship",
            ),
        ),
        _settings_row(
            "Sessions at once",
            "Counts the steps a person starts too.",
            rx.hstack(
                rx.input(
                    value=P.ap_max_parallel,
                    on_change=P.edit_ap_max_parallel,
                    size="1",
                    width="72px",
                    aria_label="Sessions at once",
                    id="autopilot-parallel",
                ),
                rx.button("Save", on_click=P.save_ap_max_parallel, size="1"),
                align="center",
            ),
        ),
        _settings_row(
            "Daily cap (USD)",
            "One cap for every workspace; a person's press is never held.",
            rx.hstack(
                rx.input(
                    value=P.ap_cap,
                    on_change=P.edit_ap_cap,
                    size="1",
                    width="72px",
                    aria_label="Daily cap in USD",
                    id="autopilot-cap",
                ),
                rx.button("Save", on_click=P.save_ap_cap, size="1"),
                align="center",
            ),
        ),
        id="autopilot-panel",
    )


def _release_panel() -> rx.Component:
    """What `main` holds since the last release, and the one button its state has, from the board's `release` block."""
    row = dict(gap="8px", align="center", wrap="wrap", width="100%")
    return rx.cond(
        P.rel_state != "",
        s.panel(
            rx.hstack(
                s.eyebrow("RELEASE"),
                rx.cond(P.rel_last_tag != "", s.text("last " + P.rel_last_tag, size="1")),
                rx.spacer(),
                s.badge(P.rel_state, rx.cond(P.rel_button != "", "amber", "gray")),
                width="100%",
                align="center",
            ),
            rx.cond(
                P.rel_reason != "",
                s.text(P.rel_reason, size="1", margin_top="6px", id="release-reason"),
            ),
            rx.cond(
                P.rel_units.length() > 0,
                rx.vstack(
                    s.text(
                        P.rel_units.length().to_string() + " units since " + P.rel_last_tag,
                        size="2",
                    ),
                    rx.foreach(
                        P.rel_units,
                        lambda u: rx.flex(
                            s.text(u.sha, size="1", font_family=_MONO),
                            s.badge(u.type, "gray"),
                            s.text(u.name, size="1"),
                            s.text(u.pr, size="1"),
                            **row,
                        ),
                    ),
                    spacing="1",
                    margin_top="8px",
                    width="100%",
                    id="release-units",
                ),
            ),
            rx.cond(
                P.rel_unmatched.length() > 0,
                rx.vstack(
                    s.text(
                        P.rel_unmatched.length().to_string() + " commits with no unit", size="2"
                    ),
                    rx.foreach(
                        P.rel_unmatched,
                        lambda c: rx.flex(
                            s.text(c.sha, size="1", font_family=_MONO),
                            s.text(c.subject, size="1", overflow_wrap="anywhere"),
                            **row,
                        ),
                    ),
                    spacing="1",
                    margin_top="8px",
                    width="100%",
                    id="release-unmatched",
                ),
            ),
            rx.cond(
                P.rel_workflow != "",
                rx.flex(
                    s.text("Release workflow: " + P.rel_workflow, size="1"),
                    rx.link("run", href=P.rel_workflow_url, is_external=True, size="1"),
                    rx.cond(
                        P.rel_release_url != "",
                        rx.link("release", href=P.rel_release_url, is_external=True, size="1"),
                    ),
                    margin_top="8px",
                    **row,
                ),
            ),
            rx.cond(
                P.rel_button != "",
                rx.vstack(
                    s.text(CONSEQUENCE["release"], size="1", id="release-consequence"),
                    rx.flex(
                        rx.input(
                            value=P.rel_version,
                            on_change=P.set_rel_version,
                            aria_label="Version",
                            size="1",
                            width="120px",
                            id="release-version",
                            disabled=P.rel_phase == "publish",
                        ),
                        rx.button(
                            rx.icon("tag", size=14),
                            P.rel_button,
                            on_click=P.press_release,
                            loading=P.releasing,
                            disabled=P.releasing | ~P.rel_enabled,
                            size="1",
                            id="release-button",
                        ),
                        gap="8px",
                        align="center",
                    ),
                    rx.cond(
                        ~P.rel_enabled & (P.rel_disabled_reason != ""),
                        s.text(P.rel_disabled_reason, size="1", id="release-disabled"),
                    ),
                    spacing="2",
                    margin_top="10px",
                    align="start",
                ),
            ),
            width="100%",
            id="release-panel",
        ),
    )


def _feature_choice(f) -> rx.Component:
    """`off` / `on`, with `pilot` between them for a feature that has one; only `off` while
    locked, the row's sentence saying why. One width for every row, its items splitting it evenly."""
    pilot = rx.segmented_control.item("Pilot", value="pilot", disabled=f.locked)
    return rx.segmented_control.root(
        rx.segmented_control.item("Off", value="off"),
        rx.cond(f.pilot, pilot, rx.fragment()),
        rx.segmented_control.item("On", value="on", disabled=f.locked),
        value=f.state,
        on_change=lambda state: P.set_feature(f.name, state),
        size="1",
        width="240px",
        flex_shrink="0",
        aria_label="Feature " + f.name,
    )


def _feature_schedule(f) -> rx.Component:
    """How often a feature with a schedule runs on its own here."""
    return rx.select(
        f.schedules,
        value=f.schedule,
        on_change=lambda label: P.set_feature_schedule(f.name, label),
        size="1",
        aria_label="Schedule of " + f.name,
    )


def _features_panel() -> rx.Component:
    """Each feature's state in this workspace. Off silences it here and starts nothing."""
    return s.panel(
        s.section_head("Features", rx.icon("puzzle", size=18, color=s.MUTED)),
        s.text("Turned off here, a feature does nothing in this workspace.", size="1"),
        rx.foreach(
            P.features,
            lambda f: _settings_row(
                f.name,
                f.summary,
                rx.hstack(
                    rx.cond(f.schedules.length() > 0, _feature_schedule(f), rx.fragment()),
                    _feature_choice(f),
                    spacing="3",
                    align="center",
                    flex_wrap="wrap",
                ),
                f.sentence,
            ),
        ),
        rx.cond(P.features.length() == 0, s.text("No feature is installed.", size="1")),
        id="features-panel",
    )


def _command_list_field(label: str, value, on_change, field_id: str) -> rx.Component:
    return rx.input(
        value=value,
        on_change=on_change,
        placeholder="e.g. curl psql",
        size="1",
        width="220px",
        aria_label=label,
        id=field_id,
    )


def _command_lists() -> rx.Component:
    """What `impl` runs in this workspace beyond, or short of, its own commands."""
    return s.panel(
        s.section_head("Commands impl may run here", rx.icon("terminal", size=18, color=s.MUTED)),
        _settings_row(
            "Allow",
            "Added to impl's commands in this workspace.",
            _command_list_field("Allow", P.impl_allow_text, P.edit_impl_allow, "impl-allow"),
        ),
        _settings_row(
            "Block",
            "Taken out, even when allowed.",
            _command_list_field("Block", P.impl_block_text, P.edit_impl_block, "impl-block"),
        ),
        _name_list("Allowed", P.impl_allow),
        _name_list("Blocked", P.impl_block),
        rx.button(
            "Save", on_click=P.save_command_lists, size="1", margin_top="8px", id="save-impl-lists"
        ),
        id="impl-lists-panel",
    )


def _decision_row(row: rx.Var[DecisionRow]) -> rx.Component:
    """One decision, its days for a reader, its workspace by name."""
    return rx.table.row(
        rx.table.cell(s.badge(row.id, "iris")),
        rx.table.cell(rx.text(row.kind, size="1")),
        rx.table.cell(
            rx.cond(
                row.kind == "delegation",
                s.text(row.agent + " decides: " + row.covers, size="1", weight="medium"),
            ),
            rx.text(row.text, size="1", white_space="pre-wrap"),
            min_width="220px",
        ),
        rx.table.cell(rx.text(row.source, size="1")),
        rx.table.cell(rx.text(row.workspace, size="1", white_space="nowrap")),
        rx.table.cell(rx.text(row.from_day, size="1", white_space="nowrap")),
        rx.table.cell(rx.text(row.until, size="1", white_space="nowrap")),
        rx.table.cell(s.badge(row.state, rx.cond(row.in_force, "grass", "gray"))),
        # Only a decision in force can be withdrawn; the others show no button.
        rx.table.cell(
            rx.cond(
                row.in_force,
                rx.button(
                    "Withdraw", on_click=P.withdraw_decision(row.id), size="1", variant="soft"
                ),
            )
        ),
        data_testid="decision-row",
    )


def _decision_field(label: str, control: rx.Component) -> rx.Component:
    return rx.vstack(
        s.text(label, size="1", weight="medium"),
        control,
        spacing="1",
        min_width="140px",
        flex_grow="1",
    )


def _decisions_panel() -> rx.Component:
    """The person's decisions and delegations. No name is asked: what is typed here is recorded as the person's."""
    form = P.decision_form
    return s.panel(
        s.section_head("Decisions", rx.icon("stamp", size=18, color=s.MUTED)),
        s.text(
            "An agent may answer under a delegation in force; its answers are recorded as delegated.",
            size="1",
        ),
        _table(
            ["Id", "Kind", "Text", "Source", "Workspace", "From", "Until", "State", ""],
            P.decision_rows,
            _decision_row,
            "No decision has been added yet.",
            margin_top="10px",
        ),
        rx.flex(
            _decision_field(
                "Kind",
                rx.select(
                    ["decision", "delegation"],
                    value=form["kind"],
                    on_change=lambda v: P.edit_decision("kind", v),
                    size="1",
                    aria_label="Kind",
                    id="decision-kind",
                ),
            ),
            _decision_field(
                "Workspace",
                rx.select(
                    P.decision_workspaces,
                    value=form["workspace"],
                    on_change=lambda v: P.edit_decision("workspace", v),
                    size="1",
                    aria_label="Workspace",
                    id="decision-workspace",
                ),
            ),
            _decision_field(
                "Until",
                rx.input(
                    value=form["until"],
                    on_change=lambda v: P.edit_decision("until", v),
                    placeholder="YYYY-MM-DD, empty until withdrawn",
                    size="1",
                    aria_label="Until",
                    id="decision-until",
                ),
            ),
            rx.cond(
                form["kind"] == "delegation",
                rx.fragment(
                    _decision_field(
                        "Agent",
                        rx.select(
                            ["Leif"],
                            value=form["agent"],
                            on_change=lambda v: P.edit_decision("agent", v),
                            size="1",
                            aria_label="Agent",
                            id="decision-agent",
                        ),
                    ),
                    _decision_field(
                        "Covers",
                        rx.input(
                            value=form["covers"],
                            on_change=lambda v: P.edit_decision("covers", v),
                            placeholder="The kind of question it may decide",
                            size="1",
                            aria_label="Covers",
                            id="decision-covers",
                        ),
                    ),
                ),
            ),
            gap="10px",
            wrap="wrap",
            width="100%",
            margin_top="14px",
        ),
        rx.text_area(
            value=form["text"],
            on_change=lambda v: P.edit_decision("text", v),
            placeholder="What was decided.",
            aria_label="Decision text",
            rows="3",
            width="100%",
            margin_top="10px",
            id="decision-text",
        ),
        rx.input(
            value=form["source"],
            on_change=lambda v: P.edit_decision("source", v),
            placeholder="Where it was decided, on one line",
            aria_label="Source",
            size="1",
            width="100%",
            margin_top="8px",
            id="decision-source",
        ),
        rx.button("Add", on_click=P.add_decision, size="1", margin_top="8px", id="add-decision"),
        id="decisions-panel",
    )


def _import_row(row: rx.Var[ImportRow]) -> rx.Component:
    return rx.table.row(
        rx.table.cell(rx.text(row.workspace, size="1", white_space="nowrap")),
        rx.table.cell(rx.text(row.unit, size="1", white_space="nowrap")),
        rx.table.cell(rx.text(row.artifact, size="1", white_space="nowrap")),
        rx.table.cell(rx.text(row.field, size="1")),
        rx.table.cell(rx.text(row.reason, size="1", min_width="200px")),
        data_testid="import-row",
    )


def _import_panel() -> rx.Component:
    """The fields the import could not read, which the board treats as unknown."""
    return s.panel(
        s.section_head("Import report", rx.icon("file_question", size=18, color=s.MUTED)),
        s.text("The board treats each field listed here as unknown.", size="1"),
        rx.cond(
            P.import_problem != "",
            rx.callout(
                P.import_problem,
                icon="circle_alert",
                color_scheme="red",
                variant="surface",
                size="1",
                margin_top="8px",
            ),
        ),
        _table(
            ["Workspace", "Unit", "Artifact", "Field", "Why"],
            P.import_rows,
            _import_row,
            "Every field of every unit was read.",
            margin_top="10px",
        ),
        id="import-panel",
    )


# The index at the top: each panel's label and id. Autopilot and the command lists are drawn
# only with a workspace chosen; their links go with them.
SECTIONS = (
    ("Autopilot", "autopilot-panel"),
    ("Appearance", "appearance-panel"),
    ("Where things live", "where-panel"),
    ("Chat sessions", "knobs-panel"),
    ("Features", "features-panel"),
    ("Decisions", "decisions-panel"),
    ("Import report", "import-panel"),
    ("Commands", "impl-lists-panel"),
    ("Updates", "update-panel"),
)
NEEDS_WORKSPACE = ("autopilot-panel", "features-panel", "impl-lists-panel")


def _section_index() -> rx.Component:
    def link(label: str, target: str) -> rx.Component:
        button = rx.button(
            label, on_click=rx.scroll_to(target), variant="soft", color_scheme="gray", size="1"
        )
        return rx.cond(P.has_workspace, button) if target in NEEDS_WORKSPACE else button

    return rx.flex(
        *[link(label, target) for label, target in SECTIONS],
        gap="6px",
        wrap="wrap",
        width="100%",
        id="settings-index",
    )


def _settings() -> rx.Component:
    return rx.vstack(
        rx.heading("Settings", size="7", weight="medium", letter_spacing="-0.045em"),
        _section_index(),
        rx.cond(P.has_workspace, _autopilot_settings(), rx.fragment()),
        s.panel(
            s.section_head("Appearance", rx.icon("palette", size=18, color=s.MUTED)),
            _settings_row(
                "Color mode",
                "Kept by this browser.",
                rx.hstack(
                    rx.button(
                        rx.icon("sun", size=16),
                        "Light",
                        id="mode-light",
                        on_click=set_color_mode("light"),
                        variant=rx.color_mode_cond("solid", "soft"),
                    ),
                    rx.button(
                        rx.icon("moon", size=16),
                        "Dark",
                        id="mode-dark",
                        on_click=set_color_mode("dark"),
                        variant=rx.color_mode_cond("soft", "solid"),
                    ),
                    spacing="2",
                ),
            ),
            _settings_row(
                "Board density",
                "Kept for this machine.",
                rx.hstack(
                    rx.button(
                        "Comfortable",
                        id="density-comfortable",
                        on_click=P.set_density("comfortable"),
                        size="2",
                        variant=rx.cond(P.density == "comfortable", "solid", "soft"),
                    ),
                    rx.button(
                        "Compact",
                        id="density-compact",
                        on_click=P.set_density("compact"),
                        size="2",
                        variant=rx.cond(P.density == "compact", "solid", "soft"),
                    ),
                    spacing="2",
                ),
            ),
            id="appearance-panel",
        ),
        rx.grid(
            s.panel(
                s.section_head("Where things live", rx.icon("monitor", size=18, color=s.MUTED)),
                _settings_row(
                    "Workspaces",
                    "Where your projects live.",
                    s.badge(rx.cond(P.working_dir != "", "set", "not set"), "gray"),
                ),
                _settings_row(
                    "App data",
                    "Backing it up does not back up your workspaces.",
                    s.badge("set", "gray"),
                ),
                _settings_row(
                    "Address",
                    rx.cond(
                        P.loopback_only,
                        "This machine only.",
                        "Reachable from the network, behind the password.",
                    ),
                    s.badge(
                        rx.cond(P.loopback_only, "local", "network"),
                        rx.cond(P.loopback_only, "grass", "amber"),
                    ),
                ),
                _settings_row("Fallback model", "Used by chat.", s.badge(P.model, "gray")),
                # The paths, the address and the variable names.
                _details(
                    "where",
                    "Details",
                    s.text(
                        "workspaces (COS_WORKING_DIR): "
                        + rx.cond(P.working_dir != "", P.working_dir, "not set"),
                        size="1",
                        id="working-dir",
                        font_family=_MONO,
                        overflow_wrap="anywhere",
                    ),
                    s.text(
                        "data (COS_DATA_DIR): " + P.data_dir,
                        size="1",
                        id="data-dir",
                        font_family=_MONO,
                        overflow_wrap="anywhere",
                    ),
                    s.text(
                        "address (COS_HOST, COS_PORT): " + P.host_port, size="1", font_family=_MONO
                    ),
                    s.text("fallback model: COS_MODEL", size="1", font_family=_MONO),
                    margin_top="10px",
                    id="data-roots",
                ),
                id="where-panel",
            ),
            s.panel(
                s.section_head(
                    "What a chat session may do", rx.icon("shield-check", size=18, color=s.MUTED)
                ),
                s.text("Set when the app starts.", size="1"),
                rx.foreach(P.knobs, _knob_row),
                # The variables that set them.
                _details(
                    "knobs",
                    "Details",
                    rx.foreach(
                        P.knobs,
                        lambda k: s.text(k.label + ": " + k.variable, size="1", font_family=_MONO),
                    ),
                    margin_top="10px",
                ),
                id="knobs-panel",
            ),
            columns=rx.breakpoints(initial="1", lg="2"),
            gap="16px",
            width="100%",
            align_items="start",
        ),
        rx.cond(P.has_workspace, _features_panel(), rx.fragment()),
        _decisions_panel(),
        _import_panel(),
        rx.cond(P.has_workspace, _command_lists(), rx.fragment()),
        # The update panel, and what `main` holds since the last release.
        _update_panel(),
        _release_panel(),
        spacing="5",
        width="100%",
    )
