"""The frame every screen sits in: the sidebar, the top bar, the status bar and the banners."""

from __future__ import annotations

from collections.abc import Sequence

import reflex as rx

from coscc.plugin import Page
from coscc.screens import studio as s
from coscc.state import Event
from coscc.state.views import NAVIGATION
from coscc.screens.common import P


# --- chrome ------------------------------------------------------------------


def _nav_entry(entry_id: str, label: str, icon: str, here, on_click, *extra) -> rx.Component:
    return rx.button(
        rx.icon(icon, size=18),
        rx.text(label, size="2", weight=rx.cond(here, "medium", "regular")),
        rx.spacer(),
        *extra,
        id=entry_id,
        on_click=on_click,
        variant="ghost",
        color_scheme="gray",
        width="100%",
        justify_content="flex-start",
        height="42px",
        padding="0 12px",
        border_radius="8px",
        background=rx.cond(here, rx.color("iris", 3), "transparent"),
        color=rx.cond(here, rx.color("iris", 11), s.MUTED),
        aria_current=rx.cond(here, "page", "false"),
        _hover={"background": rx.color("gray", 4)},
    )


def _nav(pages: Sequence[tuple[str, Page]], mobile: bool = False) -> rx.Component:
    """The built-in screens, then each feature's `page`: `pages` is `FEATURES`', read by the
    page shell, the one screen module that imports `features`."""
    at = "mobile-" if mobile else ""
    return rx.vstack(
        *[
            _nav_entry(
                f"{at}nav-{key}",
                label,
                icon,
                P.screen == key,
                P.navigate(key),
                rx.cond(
                    (key == "board") & (P.attention_count > 0),
                    s.badge(P.attention_count.to_string(), "amber"),
                ),
            )
            for key, label, icon in NAVIGATION
        ],
        *[
            _nav_entry(
                f"{at}nav-feature-{name}",
                page.label,
                page.icon,
                (P.screen == "feature") & (P.feature == name),
                P.open_feature(name),
            )
            for name, page in pages
        ],
        spacing="1",
        width="100%",
    )


def _brand() -> rx.Component:
    return rx.hstack(
        rx.center(
            rx.icon("orbit", size=21),
            width="34px",
            height="34px",
            border_radius="10px",
            background=s.ACCENT,
            color="white",
        ),
        rx.text("cos", size="5", weight="bold", letter_spacing="-0.06em"),
        s.text("studio", size="3"),
        spacing="2",
        align="center",
    )


def _workspace_select(**props) -> rx.Component:
    return s.native_select(
        rx.foreach(P.workspaces, lambda w: rx.el.option(w.name, value=w.id)),
        rx.cond(P.workspaces.length() == 0, rx.el.option("No workspace", value="")),
        value=P.cwd,
        on_change=P.choose_workspace,
        **props,
    )


def _sidebar(pages: Sequence[tuple[str, Page]]) -> rx.Component:
    return rx.vstack(
        rx.box(_brand(), padding="10px 10px 26px"),
        rx.box(
            s.eyebrow("WORKSPACE"),
            rx.hstack(
                s.mark(P.current_workspace.initials, P.current_workspace.color, size="32px"),
                _workspace_select(
                    id="workspace-switcher", aria_label="Active workspace", width="100%"
                ),
                width="100%",
                align="center",
                margin_top="12px",
            ),
            padding="0 8px 24px",
            width="100%",
        ),
        _nav(pages),
        rx.spacer(),
        s.panel(
            rx.hstack(
                rx.cond(
                    P.loopback_only,
                    rx.icon("shield-check", size=16, color=rx.color("grass", 11)),
                    rx.icon("shield-alert", size=16, color=rx.color("amber", 11)),
                ),
                rx.text(
                    rx.cond(P.loopback_only, "Local only", "Open on the network"),
                    size="2",
                    weight="medium",
                ),
            ),
            rx.button(
                "Settings",
                rx.icon("arrow-up-right", size=14),
                on_click=P.navigate("settings"),
                variant="ghost",
                size="1",
                margin_top="12px",
            ),
            padding="14px",
            background=rx.color("grass", 2),
        ),
        # A same-origin `fetch`, not a `<form>`: it carries the cookie and a matching `Origin`.
        # The guard ends the session whatever the page does next.
        rx.button(
            "Log out",
            rx.icon("log-out", size=14),
            id="logout",
            on_click=rx.call_script(
                "fetch('/logout',{method:'POST',credentials:'same-origin'})"
                ".finally(()=>{window.location.href='/login'})"
            ),
            variant="ghost",
            size="1",
            margin_top="10px",
        ),
        width="228px",
        min_width="228px",
        height="100dvh",
        position="sticky",
        top="0",
        padding="22px 14px",
        background=s.SURFACE,
        border_right=f"1px solid {s.LINE}",
        display=rx.breakpoints(initial="none", lg="flex"),
        spacing="2",
    )


def _topbar() -> rx.Component:
    return rx.flex(
        s.icon_button(
            "menu",
            "Open navigation",
            id="mobile-navigation",
            on_click=P.toggle_mobile(True),
            display=rx.breakpoints(initial="flex", lg="none"),
        ),
        rx.hstack(
            rx.icon("layers", size=15, color=s.MUTED),
            s.text(
                P.current_workspace.name,
                overflow="hidden",
                text_overflow="ellipsis",
                white_space="nowrap",
                max_width=rx.breakpoints(initial="90px", sm="180px"),
            ),
            s.text("/"),
            rx.text(P.screen_title, size="2", weight="medium"),
            spacing="3",
            align="center",
            min_width="0",
        ),
        rx.spacer(),
        rx.button(
            rx.icon("search", size=15),
            s.text("Search the studio", size="1"),
            id="open-search",
            on_click=P.toggle_command(True),
            variant="ghost",
            color_scheme="gray",
            aria_label="Search the studio",
            display=rx.breakpoints(initial="none", md="flex"),
        ),
        rx.cond(P.loading, rx.spinner(size="2")),
        # The mode has to be changeable from the page and survive a reload.
        rx.box(
            rx.color_mode.button(aria_label="Switch light or dark", title="Switch light or dark"),
            id="color-mode",
        ),
        # Empty: a feature's script renders into it.
        rx.box(id="slot-topbar"),
        align="center",
        gap="12px",
        width="100%",
        min_height="68px",
        padding=rx.breakpoints(initial="12px 18px", md="12px 32px"),
        border_bottom=f"1px solid {s.LINE}",
        background=s.CANVAS,
    )


def _banners(where: str = "page") -> rx.Component:
    """The one `notice` and one `error`, drawn where `where` says.

    Drawn twice: at the top of the page (`page-*`) and at the top of the unit dialog (`detail-*`), which covers the page's copy. Both read the same fields and dismiss through the same handler.
    """
    return rx.vstack(
        rx.cond(
            P.error != "",
            rx.callout(
                P.error,
                icon="triangle_alert",
                color_scheme="red",
                variant="surface",
                width="100%",
                role="alert",
                id=f"{where}-error",
            ),
        ),
        rx.cond(
            P.notice != "",
            rx.hstack(
                rx.icon("info", size=16, color=rx.color("iris", 11)),
                rx.text(P.notice, size="2"),
                rx.spacer(),
                s.icon_button("x", "Dismiss message", on_click=P.dismiss_notice),
                padding="12px 16px",
                background=rx.color("iris", 3),
                border_radius="10px",
                role="status",
                align="center",
                width="100%",
                id=f"{where}-notice",
            ),
        ),
        spacing="3",
        width="100%",
        margin_bottom=rx.cond(
            (P.error != "") | (P.notice != ""), "20px" if where == "page" else "0", "0"
        ),
    )


# --- shared pieces -----------------------------------------------------------


def _metrics() -> rx.Component:
    return rx.grid(
        s.stat(
            "Active work",
            P.active_count.to_string(),
            "Units with an artifact and no end yet",
            "layers",
        ),
        # A real button, so the keyboard reaches it; the board opens filtered to them.
        rx.el.button(
            s.stat(
                "Needs you",
                P.attention_count.to_string(),
                "Waiting on an answer or a decision",
                "circle-dot",
                "amber",
                height="100%",
                _hover={"border_color": rx.color("amber", 8)},
            ),
            on_click=P.open_needs_you,
            aria_label="Show the units that need you on the board",
            id="metric-needs-you",
            style={"all": "unset", "display": "block", "cursor": "pointer", "min_width": "0"},
            _focus_visible={"outline": f"2px solid {rx.color('iris', 8)}", "border_radius": "14px"},
        ),
        s.stat("Spent today", P.today_spent, P.today_note, "gauge", "blue"),
        s.stat("Spent in all", P.usage_total_usd, P.usage_cost_note, "wallet", "grass"),
        columns=rx.breakpoints(initial="2", lg="4"),
        gap="12px",
        width="100%",
        id="workspace-metrics",
    )


def _event_row(event: rx.Var[Event]) -> rx.Component:
    return rx.hstack(
        rx.center(
            rx.icon(event.icon, size=16, color=rx.color(event.color, 11)),
            width="34px",
            height="34px",
            border_radius="50%",
            background=rx.color(event.color, 3),
            flex_shrink="0",
        ),
        rx.vstack(
            rx.text(event.title, size="2", weight="medium"),
            s.text(event.detail, size="1", overflow_wrap="anywhere"),
            spacing="1",
            min_width="0",
        ),
        rx.spacer(),
        s.text(
            event.time,
            size="1",
            text_align="right",
            max_width="150px",
            white_space="nowrap",
            flex_shrink="0",
            font_family="ui-monospace, monospace",
        ),
        width="100%",
        align="center",
        padding="14px 0",
        border_bottom=f"1px solid {s.LINE}",
        data_testid="activity-row",
    )
