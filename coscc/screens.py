"""The six screens, built from Python components.

`spec.md` R8: no hand-written HTML or CSS serves this app. Everything below is Python.

These screens began as a prototype and keep its shape, its spacing and most of its
words. What changed is where every value comes from: the prototype read `prototype_data.py`, and
nothing here reads anything but `StudioState`, which reads `Service`. That swap is the
whole of `spec.md` R12.

Since `0082` (`spec.md ## Answers, câu 5`) each action whose effect costs money or leaves
this machine keeps one sentence beside its button — `Service.CONSEQUENCE` — and nothing more.
The lists of limits these screens used to carry (what a grant reaches, who else holds the
password, that the app writes a prose stage's artifact) are in `.claude/docs/coscc-page-text.md`
and the rules and documents `.claude/rules/coscc-app.md` indexes, and the full grant warnings
are still in the API. Paths, full
shas, UUIDs and variable names sit only inside a closed `_details`
(`.claude/rules/ui-standard.md` S3).
"""

from __future__ import annotations

import reflex as rx

from coscc import studio as s

# `0095`: these moved to modules of their own. Every name is imported back, so
# `coscc.screens.<name>` still resolves; a patch reaches only the module that looks it up.
from coscc.screens_common import (
    P,
    _details,
    _MONO,
    _table,
    _mono,
)
from coscc.screens_chrome import (
    _nav,
    _brand,
    _workspace_select,
    _sidebar,
    _topbar,
    _status_bar,
    _banners,
    _metrics,
    _event_row,
)
from coscc.screens_overview import (
    _empty_board,
    _overview,
    _workspace_card,
    _workspaces_screen,
    _activity_line,
    _activity_body,
)
from coscc.screens_board import (
    _unit_card,
    _column,
    _start_unit,
    _running_steps,
    _log_tail,
    _update_actions,
    _update_panel,
    _update_warning,
    _RECONNECT_JS,
    _autopilot_stop_row,
    _autopilot_strip,
    _board,
    _collapsed_groups,
    _collapsed_group,
)
from coscc.screens_sessions import (
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
from coscc.screens_settings import (
    _settings_row,
    _knob_row,
    _name_list,
    _grant_row,
    _model_row,
    _autopilot_settings,
    _settings,
)
from coscc.screens_unit import (
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
from coscc.screens_backlog import (
    _BACKLOG_ACTIONS,
    _BACKLOG_WIDE,
    _backlog_row,
    _backlog_editor,
    _backlog_screen,
)
from coscc.screens_dialogs import (
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
        ("settings", _settings()),
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
        # No `on_mount`: it runs again on every path change (`.cos/0056_*/spike.md ## U3`).
        # The first read is `StudioState.arrive`, every route's `on_load`.
        id="studio-shell", data_density=P.density,
        background=s.CANVAS, color=s.INK, min_height="100dvh",
        style={"& button": {"cursor": "pointer"}, "& button:disabled": {"cursor": "not-allowed"}},
    )
