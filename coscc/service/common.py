"""What more than one part of `Service` uses, and what code outside it imports: the errors a
request is refused with, and the words and states the board shows beside a unit."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from coscc.kernel import Invalid
from coscc.git import gitops
from coscc.github import integrate
from coscc.runlog.journal import BadRecord, Journal
from coscc.units import states
from coscc.units.autopilot import SHIP_UNRECORDED
from coscc.units.guards import REASONS
from coscc.data import Busy
from coscc.data import now as _now

if TYPE_CHECKING:
    from coscc.service.attempts import Attempts


# The stage names, in stage order, from the state set the loop is checked against: a copy
# kept here by hand once left `spike` out, and the unit's detail could not open `spike.md`.
STAGE_FILES = states.default().stage_names

# Where a unit's branch is cut from: the trunk as this remote has it. Constants, not request
# fields, so a caller cannot point the fetch at another remote or branch.
BRANCH_REMOTE = "origin"
BRANCH_TRUNK = gitops.TRUNK


class Refused(Invalid):
    """The gate refused a step. `reasons` are its codes (`guards.REASONS`), which the
    autopilot reads instead of the words."""

    def __init__(self, said: str, reasons: tuple[str, ...] = ()) -> None:
        super().__init__(said)
        for code in reasons:
            if code not in REASONS:
                raise ValueError(f"no reason code {code!r}")
        self.reasons = tuple(reasons)


class Holds:
    """What holds each unit now: its unfinished attempt, in `cos.db` (`attempts`), the only
    thing a refusal of a busy unit reads. `finishing`: a step's `after_end`, run in its
    attempt's `ending`, by attempt id; an Apply's settle and `shutdown` wait for it.
    """

    def __init__(self, attempts: Attempts) -> None:
        self.attempts = attempts
        self.finishing: dict[int, tuple[dict[str, Any], asyncio.Task]] = {}

    def busy(self, key: str, unit: str) -> str:
        """What holds this unit, in the one sentence every refusal carries, or `""`."""
        return self.attempts.busy(key, unit)


def log_setting(journal: Journal | None, key: str, old: Any, new: Any) -> None:
    """One `setting` record of a changed setting, its old and new value."""
    if journal is None:
        return
    try:
        journal.append(
            {
                "kind": "setting",
                "workspace": "",
                "unit": "",
                "stage": "",
                "name": key,
                "old": old,
                "new": new,
            }
        )
    except (BadRecord, Busy) as e:
        raise Invalid(f"the setting was saved but not logged: {e}") from e


async def _open_prs(cwd: str) -> list[dict[str, Any]] | str:
    try:
        return await integrate.open_prs(str(Path(cwd).expanduser().resolve()))
    except integrate.IntegrateError as e:
        return str(e)


def open_prs_once(cwd: str):
    """`integrate.open_prs` for `cwd`, asked at most once however often it is awaited;
    `gh`'s error as a string."""
    held: list[Any] = []

    async def prs() -> list[dict[str, Any]] | str:
        if not held:
            held.append(await _open_prs(cwd))
        return held[0]

    return prs


def open_prs_held(asked: Asked, cwd: str, fresh: bool = False):
    """`open_prs_once`, answered from what `asked` holds for `cwd`: `gh` is waited on only
    when nothing is held yet, or when `fresh`."""
    got: list[Any] = []

    async def prs():
        if not got:
            got.append(await asked.get((cwd, "prs"), lambda: _open_prs(cwd), fresh))
        return got[0]

    return prs


class Asked:
    """What a slow read (`gh`) last answered, by a key whose first part is the workspace,
    with when; and the one background ask running for each key. This process only.

    `get` answers from memory and starts the next ask in the background, so a board read
    never waits on the network once a key was answered once; only the first `get` of a key
    waits, or a `fresh` one. An ask that brings a different answer tells `changed` its key.
    `fn` never raises: an error is its answer, as a string.
    """

    def __init__(self, changed: Callable[[tuple[str, ...]], None] | None = None) -> None:
        self.held: dict[tuple[str, ...], tuple[Any, str]] = {}
        self.asks: dict[tuple[str, ...], asyncio.Task] = {}
        self.changed = changed

    def ask(
        self, key: tuple[str, ...], fn: Callable[[], Awaitable[Any]], tell: bool = True
    ) -> asyncio.Task:
        """The ask running for `key`, or a new one. A new one that is waited on (`tell`
        off) tells `changed` nothing: whoever waits reads its answer."""
        loop = asyncio.get_running_loop()
        task = self.asks.get(key)
        if task is not None and not task.done() and task.get_loop() is loop:
            return task

        async def run() -> Any:
            value = await fn()
            before = self.held.get(key)
            self.held[key] = (value, _now())
            if tell and before is not None and before[0] != value and self.changed is not None:
                self.changed(key)
            return value

        task = loop.create_task(run())
        self.asks[key] = task
        task.add_done_callback(
            lambda t: self.asks.pop(key, None) if self.asks.get(key) is t else None
        )
        return task

    async def get(
        self, key: tuple[str, ...], fn: Callable[[], Awaitable[Any]], fresh: bool = False
    ) -> Any:
        held = self.held.get(key)
        if fresh:
            # An ask begun before this call may predate what it is asked for.
            running = self.asks.get(key)
            if running is not None and running.get_loop() is asyncio.get_running_loop():
                await asyncio.shield(running)
        if held is None or fresh:
            return await asyncio.shield(self.ask(key, fn, tell=False))
        self.ask(key, fn)
        return held[0]


class Updating(Refused):
    """Refused because the app is in the seconds before it restarts. A 503."""

    def __init__(self, said: str) -> None:
        super().__init__(said, ("updating",))


class NotUpdatable(Invalid):
    """This install is not the shape an update can be applied to. A 409."""


def _younger_than(at: str, oldest: datetime) -> bool:
    """Whether a run-log `at` is after `oldest`. One that will not parse is not shown."""
    try:
        when = datetime.fromisoformat(at)
    except TypeError, ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when > oldest


def step_cwd(stage: str, work: str, directory: Path, spike_dir: str | None = None) -> str:
    """Where a step's session runs: the unit's worktree, except for `ship` and `spike`.

    `spike` runs in `spike_dir`, a throwaway directory under the data root, so its probe code
    never lands in the worktree whose branch it would ride.

    `ship` runs `gh pr merge --squash --delete-branch`, which inside a worktree merges and then
    fails (gh tries to switch the worktree to `main`, git refuses, exit 1, branches left
    behind). Run from a non-git directory with the PR URL it merges and deletes the remote
    branch; the unit's store directory is such a directory. `worktrees.remove_if_finished`
    removes the worktree and local branch once GitHub says `MERGED`. The gates still read `work`.
    """
    if stage == "spike" and spike_dir:
        return spike_dir
    return str(directory) if stage == "ship" else work


def describe_base(base: dict[str, Any] | None) -> str:
    """The one sentence saying a step's base may be stale, or `""` when it is fresh. `state.py`
    and the runner prompt both call this so the sentence is written once."""
    if not base or base.get("fresh", True):
        return ""
    sha = base.get("sha") or "?"
    ref = base.get("ref") or f"{BRANCH_REMOTE}/{BRANCH_TRUNK}"
    reason = base.get("reason") or ""
    return f"This step ran on {ref} at {sha}, which may be stale: {reason}"


# The three results a `### Outcome` block may carry, as a person types them, and the word
# the loop's `parseOutcome` reads each one as.
OUTCOME_RESULTS = {"đạt": "met", "trượt": "missed", "không đo được": "unmeasurable"}
# A line for the board to show on a missed outcome, no action.
MISSED_HINT = "cân nhắc bỏ hoặc làm lại"


def outcome_label(
    outcome: dict[str, Any] | None, today: date, finished: bool = False
) -> dict[str, Any] | None:
    """What the board shows for one unit's outcome, or None for no label.

    `outcome` is what `board._outcome_of` copied from the loop. `today` is a parameter so the
    deadline branch is testable without a clock. `counted` is whether the unit has a result
    (`không đo được` is shown but is not one). `form` is whether the board offers to record one:
    only on a finished unit, the one `record_outcome` accepts.
    """
    if not outcome:
        return None
    deadline = outcome.get("deadline")
    result = outcome.get("result")
    if result == "met":
        kind, text, color, counted = "met", "đạt", "grass", True
    elif result == "missed":
        kind, text, color, counted = "missed", "trượt", "red", True
    elif result == "unmeasurable":
        kind, text, color, counted = "unmeasurable", "không đo được", "amber", False
    elif not deadline:
        return None
    elif date.fromisoformat(deadline) <= today:
        kind, text, color, counted = "due", "tới hạn — chưa đo", "amber", False
    else:
        kind, text, color, counted = "pending", "chưa tới hạn", "gray", False
    return {
        "kind": kind,
        "text": text,
        "color": color,
        "counted": counted,
        "hint": MISSED_HINT if kind == "missed" else "",
        "deadline": deadline,
        "by": outcome.get("by"),
        "date": outcome.get("date"),
        "measured_by": outcome.get("measured_by"),
        "source": outcome.get("source"),
        "reason": outcome.get("reason"),
        "note": outcome.get("note"),
        "invalid": int(outcome.get("invalid") or 0),
        "form": bool(finished),
        # What the page shows. `text` and `kind` stay as they were for the API.
        "label": OUTCOME_LABEL[kind],
        "hint_label": MISSED_HINT_LABEL if kind == "missed" else "",
    }


# `MISSED_HINT` in the page's language; the stored word is unchanged.
MISSED_HINT_LABEL = "Consider dropping or redoing it."
# Each outcome kind as the page says it.
OUTCOME_LABEL = {
    "met": "met",
    "missed": "missed",
    "unmeasurable": "could not be measured",
    "due": "due, not measured",
    "pending": "not due yet",
}

# The one sentence the page keeps beside each action whose effect costs money or leaves this
# machine. The full warnings stay in `policy.py`, the API and `.claude/rules/coscc-app.md`.
CONSEQUENCE = {
    "run": "Runs a real Claude session and spends account quota.",
    "pr": "Pushes and opens a pull request with this machine's gh login, and spends quota.",
    "ship": "Merges the pull request with this machine's gh login, and spends quota.",
    "integrate": "Rebases this pull request with this machine's gh login; a conflict opens a paid session.",
    "estimate": "Opens one paid session that proposes estimates.",
    "drop": "Closes this unit's open pull request with this machine's gh login.",
    # The whole warning is `release.WARNING`, in `/api/board`.
    "release": "Commits, pushes, merges and tags on main with this machine's gh login.",
}


def consequence(stage: str) -> str:
    """The sentence for running `stage`, `CONSEQUENCE["run"]` when it has none of its own."""
    return CONSEQUENCE.get(stage, CONSEQUENCE["run"])


def attention_reason(unit: dict[str, Any]) -> str:
    """What a unit waits on, `""` when it waits on nothing named here. The board's state is
    `unit_state`'s; this shows in the unit's dialog beside it, through `reason_beside`."""
    # `next`'s code, never its words. `dependency` is the one `why` whose words began `waiting`.
    why = str(unit.get("why") or "")
    rows = unit.get("stages") or []
    if unit.get("phase") == "pre-intent" or why in ("finished", "rejected"):
        return ""
    if not (
        unit.get("problems") or any(r.get("status") in ("draft", "changes-requested") for r in rows)
    ):
        return ""
    waiting = any(not p.get("answered") for p in unit.get("person_findings") or [])
    if unit.get("problems") or waiting or why == "dependency":
        return "Needs a person"
    draft = next((r for r in rows if r.get("status") == "draft"), None)
    if draft is not None:
        # A `ship.md` a refused merge left is worked by `next`, not accepted; accepting it
        # reads the unit as finished with its pull request open.
        if unit.get("why") == "ship-refused":
            return ""
        # A merge asked for and not recorded: while its `ship` runs the card says `Running`, and
        # `reason_beside` drops this.
        if unit.get("why") == "ship-merging":
            return SHIP_UNRECORDED
        # A fact, not an order: no button accepts a draft; its stage's next run does.
        return f"{draft.get('stage')}.md is a draft"
    return "Changes requested"


# The nine states and their labels, in the order their rules are tried. No label reads as
# approval: `Done` comes from `next.why = finished` alone. `running` and `starting` are only
# laid over (`shown_state`), never decided by `unit_state`.
STATE_LABEL = {
    "done": "Done",
    "dropped": "Dropped",
    "paused": "Paused",
    "running": "Running",
    "starting": "Starting",
    "needs-you": "Needs you",
    "error": "Error",
    "awaiting": "Awaiting CI/merge",
    "ready": "Ready",
}
# One colour per state, none shared, in place of the lane colours.
STATE_COLOR = {
    "done": "grass",
    "dropped": "bronze",
    "paused": "plum",
    "running": "iris",
    "starting": "blue",
    "needs-you": "amber",
    "error": "red",
    "awaiting": "cyan",
    "ready": "gray",
}
# The states `Running` is never laid over, and the reason is never shown beside.
COLLAPSED_STATES = ("done", "paused", "dropped")
# The states the board folds into a closed group at its foot rather than a stage lane: a
# paused unit stays in its lane, since it waits on a person.
FOLDED_STATES = ("done", "dropped")


def _state(state: str, label: str = "", ci: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "state": state,
        "label": label or STATE_LABEL[state],
        "color": STATE_COLOR[state],
        "ci": ci,
    }


def unit_state(
    unit: dict[str, Any], last_end: dict[str, Any] | None, ci: dict[str, Any] | None
) -> dict[str, Any]:
    """The one state `Service.board` decides for a unit: rules 1-3 and 5-8.

    `unit` is the board's dict with `integration` attached; `last_end` the unit's latest ended
    run-log row; `ci` the held answer of `integrate.required_checks` for the pull request's
    head, or None. `Running` is the page's to lay over this (`shown_state`). `ci` in the answer
    is what the dialog's CI line says, None where there is no line.
    """
    why = str(unit.get("why") or "")
    hold = (unit.get("hold") or {}).get("state")
    if why == "finished":
        return _state("done")
    if hold == "dropped" or why == "rejected":
        if why == "rejected":
            stage = next(
                (r.get("stage") for r in unit.get("stages") or [] if r.get("status") == "rejected"),
                "",
            )
            return _state("dropped", f"Dropped — {stage} rejected")
        return _state("dropped")
    if hold == "paused":
        return _state("paused")
    if int(unit.get("open") or 0) > 0 or why in ("needs-person", "awaits-person"):
        return _state("needs-you")
    # The buckets `integrate.classify` reads as red. A held answer that is `gh`'s error has no
    # `checks`, and reads as not read.
    red = [
        str(c.get("name") or "")
        for c in (ci or {}).get("checks") or []
        if c.get("bucket") in ("fail", "cancel")
    ]
    line = None
    if ci is not None:
        line = {"read": "checks" in ci, "red": red, "at": str(ci.get("at") or "")}
    failed = (
        last_end is not None
        and last_end.get("outcome") in ("failed", "exhausted")
        and last_end.get("stage") == unit.get("at")
    )
    # `ship-merging` with no `ship` running is a merge nothing will record; `shown_state` lays
    # `Running` over it while one runs.
    if (
        unit.get("problems")
        or why in ("unreadable", "ship-merging")
        or failed
        or red
        or (unit.get("integration") or {}).get("state") == "red-after-integration"
    ):
        return _state("error", ci=line if red else None)
    # `impl` waits on another unit's merge: a wait, not a stage to run.
    if why == "dependency":
        return _state("awaiting", "Awaiting a dependency")
    # A `review` or `ship` made stale by a rerun waits on CI as a missing one does; a stale
    # `pr.md` in the window is a stage to run, not a wait.
    due = why == "missing" or (why == "stale" and unit.get("at") in ("review", "ship"))
    if unit.get("between_pr_and_ship") and due:
        return _state("awaiting", ci=line or {"read": False, "red": [], "at": ""})
    return _state("ready")


def shown_state(
    decided: dict[str, Any], running_rows: list[dict[str, Any]] | None
) -> dict[str, Any]:
    """`Running` while `Board.running` lists a `running` or `ending` attempt of the unit, and
    `Starting` while it lists only `queued` or `preparing` ones, below rules 1-3 and above the
    rest. `running_rows` is that answer's `running` entry for the unit; a row's `state` is its
    attempt's."""
    if not running_rows or decided.get("state") in COLLAPSED_STATES:
        return decided
    if all(r.get("state") in ("queued", "preparing") for r in running_rows):
        return _state("starting")
    return _state("running")


def reason_beside(reason: str, state: str) -> str:
    """`attention_reason` as the dialog shows it beside the state shown, `""` where the two
    would disagree: any reason beside a collapsed state, "Needs a person" beside any state
    but *Needs you*, and `SHIP_UNRECORDED` beside any but *Error*."""
    if (
        state in COLLAPSED_STATES
        or (reason == "Needs a person" and state != "needs-you")
        or (reason == SHIP_UNRECORDED and state != "error")
    ):
        return ""
    return reason
