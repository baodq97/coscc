"""The screens, built from Python components, reading only `StudioState`.

Each action whose effect costs money or leaves this machine keeps one sentence beside its button (`Service.CONSEQUENCE`). Paths, full shas, UUIDs and variable names sit only inside a closed `_details`.
"""

from __future__ import annotations

import reflex as rx

from coscc import features
from coscc.plugin import KIT_JS
from coscc.screens import studio as s

# Every name is imported back so `coscc.screens.<name>` still resolves; a patch reaches only the module that looks it up.
from coscc.screens.common import P
from coscc.screens.chrome import _sidebar, _topbar, _banners
from coscc.screens.overview import _overview, _workspaces_screen
from coscc.screens.board import _RECONNECT_JS, _board
from coscc.screens.sessions import _sessions, _activity, _cost
from coscc.screens.agents import agents_screen
from coscc.screens.settings import _settings
from coscc.screens.unit import _detail_dialog
from coscc.screens.backlog import _backlog_screen
from coscc.screens.idea import (
    _idea_screen,
)
from coscc.screens.dialogs import (
    _WATCH_JS,
    _watch_dialog,
    _workspace_dialog,
    _remove_dialog,
    _command_dialog,
    _mobile_dialog,
)


# --- the page ----------------------------------------------------------------


def _screen() -> rx.Component:
    return rx.match(
        P.screen,
        ("overview", _overview()),
        ("workspaces", _workspaces_screen()),
        ("board", _board()),
        ("backlog", _backlog_screen()),
        ("sessions", _sessions()),
        ("activity", _activity()),
        ("cost", _cost()),
        ("agents", agents_screen()),
        ("settings", _settings()),
        ("idea", _idea_screen()),
        ("feature", _feature_screen()),
        rx.fragment(),
    )


def _feature_screen() -> rx.Component:
    """A feature's own page in a frame: what it sends (a secret, say) stays a plain request of
    that page, outside the studio's state and socket."""
    return rx.cond(
        P.feature_off,
        s.panel(
            s.text("This feature is off for this workspace."),
            rx.button(
                "Turn it on in Settings",
                on_click=P.navigate("settings"),
                variant="ghost",
                size="1",
                margin_top="8px",
            ),
        ),
        rx.el.iframe(
            src=P.feature_src,
            title=P.feature_label,
            id="feature-frame",
            width="100%",
            height=rx.breakpoints(initial="calc(100dvh - 120px)", md="calc(100dvh - 144px)"),
            border="none",
            display="block",
        ),
    )


def index() -> rx.Component:
    # Read now, so a test can patch `features.FEATURES`.
    pages = [(f.name, f.page) for f in features.FEATURES if f.page]
    return rx.box(
        rx.flex(
            _sidebar(pages),
            rx.box(
                _topbar(),
                rx.box(
                    _banners(),
                    _screen(),
                    padding=rx.breakpoints(
                        initial="20px 18px 32px", md="28px 32px 48px", xl="28px 40px 48px"
                    ),
                    width="100%",
                    max_width="1660px",
                    margin="0 auto",
                ),
                flex="1",
                min_width="0",
                width="100%",
            ),
            width="100%",
            min_height="100dvh",
            align="start",
        ),
        _detail_dialog(),
        _workspace_dialog(),
        _remove_dialog(),
        _command_dialog(),
        _mobile_dialog(pages),
        _watch_dialog(),
        rx.script(_RECONNECT_JS),
        rx.script(_WATCH_JS),
        # The page kit once, then each feature's page script, outside Reflex's state and socket; read now so a test can patch the list.
        rx.script(KIT_JS),
        *[rx.script(js) for f in features.FEATURES for js in f.scripts],
        # No `on_mount`: it runs again on every path change. The first read is `StudioState.arrive`, every route's `on_load`.
        id="studio-shell",
        data_density=P.density,
        background=s.CANVAS,
        color=s.INK,
        min_height="100dvh",
        style={"& button": {"cursor": "pointer"}, "& button:disabled": {"cursor": "not-allowed"}},
    )
