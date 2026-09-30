"""The six screens, built from Python components, reading only `StudioState`.

Each action whose effect costs money or leaves this machine keeps one sentence beside its button (`Service.CONSEQUENCE`). Paths, full shas, UUIDs and variable names sit only inside a closed `_details`.
"""

from __future__ import annotations

import reflex as rx

from coscc import features
from coscc.screens import studio as s

# Every name is imported back so `coscc.screens.<name>` still resolves; a patch reaches only the module that looks it up.
from coscc.screens.common import P
from coscc.screens.chrome import _sidebar, _topbar, _status_bar, _banners
from coscc.screens.overview import _overview, _workspaces_screen
from coscc.screens.board import _RECONNECT_JS, _board
from coscc.screens.sessions import _sessions, _activity, _cost
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
        ("settings", _settings()),
        ("idea", _idea_screen()),
        rx.fragment(),
    )


def index() -> rx.Component:
    return rx.box(
        rx.flex(
            _sidebar(),
            rx.box(
                _topbar(),
                rx.box(
                    _status_bar(),
                    _banners(),
                    _screen(),
                    rx.flex(
                        s.text("COS STUDIO", size="1", letter_spacing="0.07em"),
                        rx.spacer(),
                        s.text("Built with Python. Designed around your work.", size="1"),
                        width="100%",
                        gap="8px",
                        wrap="wrap",
                        padding="36px 0 8px",
                    ),
                    padding=rx.breakpoints(
                        initial="0 18px 20px", md="0 32px 24px", xl="0 40px 24px"
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
        _mobile_dialog(),
        _watch_dialog(),
        rx.script(_RECONNECT_JS),
        rx.script(_WATCH_JS),
        # Each feature's page script, outside Reflex's state and socket; read now so a test can patch the list.
        *[rx.script(js) for f in features.FEATURES for js in f.scripts],
        # No `on_mount`: it runs again on every path change. The first read is `StudioState.arrive`, every route's `on_load`.
        id="studio-shell",
        data_density=P.density,
        background=s.CANVAS,
        color=s.INK,
        min_height="100dvh",
        style={"& button": {"cursor": "pointer"}, "& button:disabled": {"cursor": "not-allowed"}},
    )
