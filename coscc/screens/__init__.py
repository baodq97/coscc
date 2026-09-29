"""The six screens, built from Python components, reading only `StudioState`.

Each action whose effect costs money or leaves this machine keeps one sentence beside its button (`Service.CONSEQUENCE`). Paths, full shas, UUIDs and variable names sit only inside a closed `_details`.
"""

from __future__ import annotations

import reflex as rx

from coscc.web import studio as s

# Every name is imported back so `coscc.screens.<name>` still resolves; a patch reaches only the module that looks it up.
from coscc.screens.common import (
    P,
    _details,
    _MONO,
    _table,
    _mono,
)
from coscc.screens.chrome import (
    _nav,
    _brand,
    _workspace_select,
    _sidebar,
    _topbar,
    _status_bar,
    _banners,
    _metrics,
    _event_row,
    _NOTICE_JS,
)
from coscc.screens.overview import (
    _empty_board,
    _overview,
    _workspace_card,
    _workspaces_screen,
    _activity_line,
    _activity_body,
)
from coscc.screens.board import (
    _unit_card,
    _lane,
    _start_unit,
    _running_steps,
    _log_tail,
    _update_actions,
    _update_panel,
    _update_warning,
    _RECONNECT_JS,
    _guide_row,
    _guide_list,
    _guide_panel,
    _board,
    _collapsed_groups,
    _collapsed_group,
)
from coscc.screens.sessions import (
    _message,
    _sessions,
    _activity,
    OVER_BUDGET,
    _spend_row,
    SPEND_HEADERS,
    _token_row,
    _waste_row,
    _anomaly_cells,
    _anomaly_row,
    _unit_anomaly_row,
    _cost,
    _unit_cost,
)
from coscc.screens.settings import (
    _settings_row,
    _knob_row,
    _name_list,
    _grant_row,
    _model_row,
    _autopilot_settings,
    _settings,
)
from coscc.screens.unit import (
    _cell_chip,
    _run_row,
    _question_row,
    _questions_tab,
    _integration_panel,
    _outcome_panel,
    _HOLD_BUTTONS,
    _rerun_panel,
    _hold_panel,
    _round_row,
    _comments_tab,
    _unit_not_found,
    _detail_dialog,
)
from coscc.screens.backlog import (
    _BACKLOG_ACTIONS,
    _BACKLOG_WIDE,
    _backlog_row,
    _backlog_editor,
    _backlog_screen,
)
from coscc.screens.knowledge import (
    _knowledge,
)
from coscc.screens.idea import (
    _idea_screen,
)
from coscc.screens.dialogs import (
    _WATCH_JS,
    _WATCH_COLOR,
    _watch_row,
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
        ("knowledge", _knowledge()),
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
                        width="100%", gap="8px", wrap="wrap", padding="36px 0 8px",
                    ),
                    padding=rx.breakpoints(initial="0 18px 20px", md="0 32px 24px",
                                           xl="0 40px 24px"),
                    width="100%", max_width="1660px", margin="0 auto",
                ),
                flex="1", min_width="0", width="100%",
            ),
            width="100%", min_height="100dvh", align="start",
        ),
        _detail_dialog(), _workspace_dialog(), _remove_dialog(),
        _command_dialog(), _mobile_dialog(), _watch_dialog(),
        rx.script(_RECONNECT_JS),
        rx.script(_WATCH_JS),
        # The notices, outside Reflex's state and socket (`chrome.py`).
        rx.script(_NOTICE_JS),
        # No `on_mount`: it runs again on every path change. The first read is `StudioState.arrive`, every route's `on_load`.
        id="studio-shell", data_density=P.density,
        background=s.CANVAS, color=s.INK, min_height="100dvh",
        style={"& button": {"cursor": "pointer"}, "& button:disabled": {"cursor": "not-allowed"}},
    )
