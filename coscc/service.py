"""The only place business logic lives.

`spec.md` R10: the page and the JSON API are two entry points to one capability, and two
implementations of one capability is the surest way to have one of them fixed and the other
not. So neither an HTTP route nor a Reflex event handler may decide anything — they
translate a request into a call here, and a result back into their own shape.

The rule that makes this checkable: nothing in this module imports a web framework, and
nothing above it branches on business state. A conditional in a route is a bug in this
file, not in the route.

`Invalid` is how this layer refuses. Callers map it to their own vocabulary — 400 for
HTTP, an error banner for the page — and neither gets to invent a different reason.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from coscc import events
from coscc.config import Config
from coscc.data import now as _now
from coscc.runner import Runner
from coscc.sessions import Sessions
from coscc.store import Store
from coscc import steps as steps_mod
from coscc import updater as updater_mod

# `0095`: these moved to modules of their own. Every name is imported back, so
# `coscc.service.<name>` still resolves; a patch reaches only the module that looks it up.
from coscc.service_common import (
    STAGE_FILES,
    BRANCH_REMOTE,
    BRANCH_TRUNK,
    Invalid,
    Updating,
    NotUpdatable,
    StaleCutList,
    _younger_than,
    step_cwd,
    describe_base,
    OUTCOME_RESULTS,
    MISSED_HINT,
    outcome_label,
    OWNER,
    MISSED_HINT_LABEL,
    CONSEQUENCE,
    consequence,
    attention_reason,
    STATE_LABEL,
    STATE_COLOR,
    COLLAPSED_STATES,
    _state,
    unit_state,
    shown_state,
    reason_beside,
)
from coscc.service_board import (
    UNKNOWN_END_FOR,
    _attach_comment_state,
    _attach_precedent,
    answerable,
    BoardMixin,
)
from coscc.service_steps import (
    RERUN_NOTE_MAX,
    RETAKE_REFUSED,
    _answers_kept,
    integration_since_review,
    CI_REFRESH,
    StepsMixin,
)
from coscc.service_update import (
    _as_invalid,
    _RELEASE_LINE,
    _LOCAL_LINE,
    update_words,
    UpdateMixin,
)
from coscc.service_models import (
    _whole_at_least_one,
    _positive_number,
    ModelsMixin,
)
from coscc.service_workspaces import (
    WorkspacesMixin,
)
from coscc.service_watch import (
    WatchMixin,
)
from coscc.service_answers import (
    AnswersMixin,
)
from coscc.service_backlog import (
    BacklogMixin,
)
from coscc.service_sessions import (
    SessionsMixin,
)
from coscc.service_autopilot import (
    AutopilotMixin,
)
from coscc.service_activity import (
    ActivityMixin,
)


@dataclass
class Service(
    WorkspacesMixin,
    BoardMixin,
    StepsMixin,
    WatchMixin,
    UpdateMixin,
    AnswersMixin,
    BacklogMixin,
    SessionsMixin,
    ModelsMixin,
    AutopilotMixin,
    ActivityMixin,
):
    config: Config
    sessions: Sessions
    store: Store | None = field(default=None, init=False)
    # `0016`. Held across read-check-append so two answers arriving together cannot
    # interleave their blocks. The page and the API share this instance (`state.py`
    # takes `API.state.service`), so one lock covers both.
    _answer_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    # `0021`. Held across read-comments-then-post, so two presses of *Post to PR* for one
    # round run one after the other and the second finds the first's marker. One process
    # only, like `pull` (`.claude/rules/coscc-app.md`).
    _comment_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    # `0111` R3. Held across one retake of a unit's screenshots, for the whole app: every
    # capture binds `127.0.0.1:18783` (`scripts/capture_screens.py:112`), so two at once fail.
    # A capture a session runs does not take it (spec C1). One process only, like `pull`.
    _screens_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    # `0111` review round 1, F3. The retake running now, if any, `{workspace, unit, started}`
    # by an id that never leaves this process: read only by `_update_jobs`.
    _retakes: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    # `0017` R8. Per workspace, created on first use.
    _create_locks: dict[str, asyncio.Lock] = field(default_factory=dict, init=False, repr=False)
    # `0035` R12. `(journal key, unit)` for every step, integration or hold holding its unit
    # now, and one lock per workspace held across an integration's check-and-mark. Since
    # `0050` each holds a `Mark` saying what and since when, and a step takes its own before
    # its first `await` (`_take`). One process only, like `pull`.
    _active: dict[tuple[str, str], steps_mod.Mark] = field(default_factory=dict, init=False, repr=False)
    _integrate_locks: dict[str, asyncio.Lock] = field(default_factory=dict, init=False, repr=False)
    # `0051` R1. What is running now, for the board to show: one entry per step or
    # integration, keyed by an id that never leaves this process. Added and removed beside
    # `_active`, read only by `running`. Display only: `_active` still does the refusing.
    _running: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    # `0100` R6. By `(journal key, unit)`: the last answer of `integrate.required_checks`,
    # `{head, checks | error, at}`, and the one background ask running for it. Memory only,
    # gone on a restart, and never waited on by a board read. One process only, like `pull`.
    _ci: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    _ci_asks: dict[tuple[str, str], asyncio.Task] = field(default_factory=dict, init=False, repr=False)
    # `0034`. The board steps running now, each as its own task, so a reader that goes
    # away does not take the step with it and a Stop has something to cancel.
    steps: steps_mod.Registry = field(default_factory=lambda: steps_mod.Registry(), init=False, repr=False)
    # `0073`. The recorder of every board step running in this process, by `run`: what
    # `events_page` reads and `follow_events` subscribes to. A step leaves it when `_drive`
    # ends, after its recorder has written what it holds; from then the tables answer.
    _recorders: dict[str, events.Recorder] = field(default_factory=dict, init=False, repr=False)
    # `0043`. The autopilot, per journal key: the lock every pass holds (the spec's
    # *Tuần tự hóa*), the poll loop of a workspace that has it on, the workspace directory
    # it was turned on for, the reader of each step it started, the stops the last pass
    # found by unit, and the passes scheduled but not yet run. One process only, like `pull`.
    _autopilot_locks: dict[str, asyncio.Lock] = field(default_factory=dict, init=False, repr=False)
    _autopilot_tasks: dict[str, asyncio.Task] = field(default_factory=dict, init=False, repr=False)
    _autopilot_cwd: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _autopilot_runs: dict[str, dict[str, tuple[str, asyncio.Task]]] = field(default_factory=dict, init=False, repr=False)
    _autopilot_stops: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict, init=False, repr=False)
    _autopilot_pending: set[asyncio.Task] = field(default_factory=set, init=False, repr=False)

    def __post_init__(self) -> None:
        # No working folder means no store, and the app behaves as it did before one existed.
        # That is what keeps `scripts/verify_0001.py` running unchanged (`spec.md` R6).
        self.store = (
            Store(self.config.working_dir, self.config.data_dir)
            if self.config.working_dir
            else None
        )
        # One question, asked in two places. See `Sessions.membership`.
        self.sessions.membership = self._is_member
        # `0068`. Told of every step, integration and chat turn that ends (R9).
        self.updater = updater_mod.Updater(self.config, self)
        self.sessions.on_turn_end = self.updater.job_ended
