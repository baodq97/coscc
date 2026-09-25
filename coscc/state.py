"""Everything the page shows, and nothing it decides.

`spec.md` R10 is the rule this module answers to: the page and the JSON API are two
entry points to one capability, so no handler here validates anything, builds a path, or
decides what counts as a workspace. Each one calls `Service`, turns `Invalid` into a line
of text, and stops. A conditional about business state in this file is a bug in
`service.py`.

The dataclasses below exist because Reflex needs a declared shape to render a list against,
and `Service` returns dictionaries. They are a *view*: every field is something the screen
draws. Nothing is computed here that the service could have answered — the one exception is
`_lane`, which is named and explained where it sits.

The service instance is the same object the FastAPI app holds. Two instances would mean two
`Sessions` registries, and knob 4 ("resume only what this app created") would answer
differently depending on which door you came through.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import sys

import reflex as rx
from reflex_base.event.context import EventContext

from coscc import events as events_mod
from coscc import place, present, spend
from coscc.api import build
from coscc.journal import COST_USD, TOKEN_FIELDS
from coscc.service import Invalid, StaleCutList, describe_base

API = build()
SERVICE = API.state.service

NAVIGATION = (
    ("overview", "Overview", "house"),
    ("workspaces", "Workspaces", "layers"),
    ("board", "Board", "columns-3"),
    # `0082` R10. The backlog left the board for a route of its own.
    ("backlog", "Backlog", "list-ordered"),
    ("sessions", "Sessions", "messages-square"),
    ("activity", "Activity & usage", "chart-no-axes-combined"),
    # `0093` R1.
    ("cost", "Cost", "circle-dollar-sign"),
    ("settings", "Settings", "settings-2"),
)

SCREEN_TITLES = {key: label for key, label, _ in NAVIGATION}

# How a status reads at a glance. `not started` is deliberately the quietest: most cells on
# most boards are it, and a board where everything shouts says nothing.
STATUS_COLOR = {
    "accepted": "grass",
    "done": "iris",
    "draft": "amber",
    "skipped": "gray",
    "rejected": "red",
    # A reviewer looked and asked for changes: waiting on someone, like a draft, but not
    # the same colour, because it is not unfinished writing — it is a verdict.
    "changes-requested": "orange",
    "not started": "gray",
}

def _cell_label(row: dict) -> tuple[str, str]:
    """The chip text and colour for one stage row (`0019` plan step 7, `spec.md` R5).

    `row` is one entry of `Service.board`'s `stages` list. Only the "no artifact, last run
    failed" case departs from the ordinary `status`/`STATUS_COLOR` pair — everything else
    is unchanged, so a stage with an artifact never shows a stale failure again.
    """
    status = row.get("status") or ""
    last_run = row.get("last_run")
    if status == "not started" and last_run and last_run.get("outcome") != "done":
        turns, cost = last_run.get("turns"), last_run.get("cost_usd")
        outcome = last_run.get("outcome")
        # `0092` R8 a: turns and cost are each known or not, and each is said as it is.
        if turns is None and cost is None:
            return f"not started · {outcome} · turns and cost unknown", "amber"
        said_turns = "turns unknown" if turns is None else f"{turns} turns"
        said_cost = "cost unknown" if cost is None else f"${cost:.2f}"
        return f"not started · {outcome} · {said_turns} · {said_cost}", "amber"
    return status, STATUS_COLOR.get(status, "gray")


LANE_COLOR = {
    "Planned": "gray",
    "In progress": "iris",
    "Needs you": "amber",
    "Complete": "grass",
}

# Colours for the workspace marks, assigned by position so the same workspace keeps the
# same colour between loads. Nothing is stored; the list is the only state.
MARK_COLORS = ("iris", "grass", "blue", "amber", "plum", "cyan")


def tree_line(tree: dict | None) -> str:
    """`0017` R6. A unit's worktree and its preparation, as the page says it.

    A failure names the command and its exit code, never just *failed*: the person has to
    be able to run that command themselves.
    """
    if not tree or not tree.get("path"):
        return ""
    line = f"Worktree: {tree['path']}"
    if tree.get("branch"):
        line += f" on {tree['branch']}"
    prepared = tree.get("prepare")
    if prepared is None:
        line += " · not prepared yet"
    elif prepared.get("ok"):
        line += " · prepared"
    else:
        line += (f" · preparing failed: `{prepared.get('command')}` exited "
                 f"{prepared.get('exit_code')}")
    return line


# --- the view shapes ---------------------------------------------------------


@dataclasses.dataclass
class Workspace:
    id: str = ""  # the resolved path; env workspaces have no name to key on
    name: str = ""
    path: str = ""
    label: str = ""
    source: str = ""
    missing: bool = False
    removable: bool = False
    initials: str = ""
    color: str = "iris"


@dataclasses.dataclass
class Cell:
    """One stage of one unit, as the board shows it."""

    stage: str = ""
    status: str = ""
    # `0019` plan step 7 / `spec.md` R5. What the chip actually shows. Equal to `status`
    # except when the artifact is absent and the last run of this stage failed — then it
    # names the failure instead of the bare word "not started". `status` itself keeps
    # meaning only what the artifact says (C6); this is a second, cosmetic field.
    label: str = ""
    mode: str = "manual"
    color: str = "gray"
    started: bool = False
    grants: str = ""
    warning: str = ""
    opens_tools: bool = False
    # `0082` R8: the one sentence the page keeps beside Run.
    consequence: str = ""
    # `idea` is the one optional stage and it gates nothing. Carried here so the run
    # button can skip it: the board card says "Next: write-pr" and a button offering to
    # run `idea` beside it is two answers to one question.
    optional: bool = False


@dataclasses.dataclass
class Question:
    """`0016`. One numbered item under an artifact's `## Open questions`, as `cos.mjs`
    read it. Nothing here parses an artifact; every field is copied from `status --json`."""

    # `<artifact>#<n>`: one string the page can bind a text box to.
    key: str = ""
    artifact: str = ""
    number: int = 0
    text: str = ""
    answered: bool = False
    # Whether this is the artifact the unit's open count is taken from.
    counted: bool = False
    # `0028`. What the row shows as its name: the number for a numbered question, `F<n>` for
    # a review finding the last round confirmed needs a person (`number` is 0 for those).
    label: str = ""
    # `0044` R10, all decided by `Service.board`. `by_jera`: the answer in force is Jera's,
    # citing `cites`. `needs_person`: Jera's last run said a person must answer it, with its
    # `proposal` and `reason`.
    by_jera: bool = False
    cites: list[str] = dataclasses.field(default_factory=list)
    needs_person: bool = False
    proposal: str = ""
    reason: str = ""


@dataclasses.dataclass
class Round:
    """`0021`. One round of `review.md` and whether it is on the pull request as a comment.
    The round is `cos.mjs`'s; whether it is posted is `Service.board`'s reading of the run
    log. Nothing here decides either."""

    number: int = 0
    verdict: str = ""
    posted: bool = False
    url: str = ""
    # Why the latest attempt failed, or empty when none has been made.
    reason: str = ""


@dataclasses.dataclass
class Unit:
    id: str = ""
    title: str = ""
    summary: str = ""
    lane: str = ""
    stage: str = ""
    color: str = "gray"
    owner: str = "You"
    mode: str = "manual"
    tokens: str = ""
    usd: str = ""
    token_count: int = 0
    progress: int = 0
    # True when this unit sits in *Needs you*: an artifact is in draft, or the harness
    # reported a problem with the directory. Not the harness's `blocked` — see `_lane`.
    needs_attention: bool = False
    problems: str = ""
    cells: list[Cell] = dataclasses.field(default_factory=list)
    # `0016` R8. How many questions in the counted artifact nobody has answered, taken
    # from `cos.mjs` (`open`) and never recounted (R7). Shown as a badge, not a lane:
    # an open question does not move a unit into *Needs you*.
    open_questions: int = 0
    questions: list[Question] = dataclasses.field(default_factory=list)
    # `0021`. The pull request `pr.md` names, and every review round with its comment state.
    pr_url: str = ""
    rounds: list[Round] = dataclasses.field(default_factory=list)
    # `0035` R1/R3/R8/R13. Copied from `Service.board`'s `integration`; empty state means
    # the unit is outside the window. `integrate_button` is the service's decision (R3).
    integration_state: str = ""
    integration_reason: str = ""
    integration_behind: str = ""
    integration_origin: str = ""
    integrate_button: bool = False
    integration_warnings: list[str] = dataclasses.field(default_factory=list)
    integration_needs_person: list[str] = dataclasses.field(default_factory=list)
    # `0047` R8. Copied from `Service.board`'s `outcome_label`; empty text means no label.
    # `outcome_form` is the service's decision: the unit is finished.
    outcome_text: str = ""
    outcome_color: str = "gray"
    outcome_detail: str = ""
    outcome_by: str = ""
    outcome_date: str = ""
    outcome_measured_by: str = ""
    outcome_deadline: str = ""
    outcome_hint: str = ""
    outcome_invalid: int = 0
    outcome_form: bool = False
    # `0051`. What is running on this unit now, or ended unseen: one line each, copied from
    # `Service.running` by `_activities`. Never from `StudioState.running` (R8).
    live: list[Activity] = dataclasses.field(default_factory=list)
    # `0045`. The hold `cos.mjs` read (`paused`, `dropped`, or empty) and the moves it allows
    # from there, copied from the board. The page offers one button per move and decides none.
    hold_state: str = ""
    hold_reason: str = ""
    hold_by: str = ""
    hold_date: str = ""
    hold_moves: list[str] = dataclasses.field(default_factory=list)
    # `0074`. The unit's place in the shortlist in effect, 0 when it has none, and its
    # relations in one line (R9). Labels only.
    shortlist_rank: int = 0
    relations_text: str = ""
    # `0082` R11, R12. The service's decisions: whether the board invites an answer, and
    # what a unit in *Needs you* waits on.
    answerable: bool = True
    attention_reason: str = ""


@dataclasses.dataclass
class Card:
    """`0053` R7. One card: only what a card, a List row, *Pick up where you left off*, a
    Usage row and the command palette draw. The page receives this list once; the whole
    `Unit` of the one unit open is `current_unit`, and every other `Unit` stays on the server
    (`_full`). Each field is copied from that `Unit` by `_card`."""

    id: str = ""
    title: str = ""
    summary: str = ""
    lane: str = ""
    stage: str = ""
    color: str = "gray"
    mode: str = "manual"
    owner: str = "You"
    progress: int = 0
    tokens: str = ""
    usd: str = ""
    token_count: int = 0
    # The card shows a badge, not the text; the text is the dialog's (`current_unit`).
    has_problem: bool = False
    open_questions: int = 0
    integration_state: str = ""
    integrate_button: bool = False
    outcome_text: str = ""
    outcome_color: str = "gray"
    hold_state: str = ""
    shortlist_rank: int = 0
    relations_text: str = ""
    live: list[Activity] = dataclasses.field(default_factory=list)
    answerable: bool = True
    attention_reason: str = ""


def _card(u: Unit) -> Card:
    """`0053`. A `Unit` as its card. Copies; decides nothing."""
    return Card(
        id=u.id, title=u.title, summary=u.summary, lane=u.lane, stage=u.stage, color=u.color,
        mode=u.mode, owner=u.owner, progress=u.progress, tokens=u.tokens, usd=u.usd,
        token_count=u.token_count, has_problem=u.problems != "", open_questions=u.open_questions,
        integration_state=u.integration_state, integrate_button=u.integrate_button,
        outcome_text=u.outcome_text, outcome_color=u.outcome_color, hold_state=u.hold_state,
        shortlist_rank=u.shortlist_rank, relations_text=u.relations_text, live=list(u.live),
        answerable=u.answerable, attention_reason=u.attention_reason,
    )


@dataclasses.dataclass
class UsageRow:
    """`0053` R11. One line of *Usage by work unit*."""

    id: str = ""
    title: str = ""
    tokens: str = ""
    usd: str = ""
    token_count: int = 0


@dataclasses.dataclass
class SpendRow:
    """`0093` R1–R3, R11. One unit, stage or day of the *Cost* screen: money read by
    `present.money`, and beside it the steps whose cost is not known (R5)."""

    key: str = ""
    usd: str = ""
    steps: str = ""
    unknown: str = ""
    over: bool = False


@dataclasses.dataclass
class TokenRow:
    """`0093` R6. The four kinds of token for the workspace or one stage, each `1,234 (12%)`."""

    scope: str = ""
    input: str = ""
    output: str = ""
    cache_read: str = ""
    cache_creation: str = ""
    total: str = ""


@dataclasses.dataclass
class WasteRow:
    """`0093` R7–R9. One kind of waste; `sub` is a line under the kind above it."""

    label: str = ""
    count: str = ""
    usd: str = ""
    unknown: str = ""
    sub: bool = False


@dataclasses.dataclass
class AnomalyRow:
    """`0093` R10. One anomaly, its measure and threshold in one cell."""

    key: str = ""
    kind: str = ""
    unit: str = ""
    stage: str = ""
    ended: str = ""
    measured: str = ""
    usd: str = ""


# `0093`. The words each kind of `spend.model` reads as. Labels only.
WASTE_LABEL = {
    "exhausted-or-failed": ("Exhausted or failed", False),
    "run-again": ("Stage run again", False),
    "integrate-conflict": ("Integrate for a conflict", False),
    "integrate-other": ("other reason", True),
    "integrate-not-recorded": ("reason not recorded", True),
    "changes-requested": ("Changes-requested rounds", False),
}
ANOMALY_LABEL = {
    "over-budget": "Over budget",
    "failed": "Exhausted or failed",
    "reruns": "Run too many times",
    "tokens-per-turn": "Tokens per turn",
}
NO_UNIT = "No unit"


def _unknown(n) -> str:
    """`0093` R5: said beside the money, or nothing when every cost is known."""
    n = int(n or 0)
    return f"{n} unknown" if n else ""


def _spend_rows(rows: list[dict], unit: bool = False) -> list[SpendRow]:
    return [
        SpendRow(
            key=(r["key"] or NO_UNIT) if unit else (r["key"] or "—"),
            usd=present.money(r.get("usd")),
            steps=f"{int(r.get('steps') or 0):,}",
            unknown=_unknown(r.get("unknown")),
            over=bool(r.get("over")),
        )
        for r in rows
    ]


def _token_row(scope: str, t: dict) -> TokenRow:
    total = int(t.get("total") or 0)

    def cell(name: str) -> str:
        n = int(t.get(name) or 0)
        return f"{n:,} ({round(100 * n / total)}%)" if total else "0"

    return TokenRow(
        scope=scope, input=cell("input_tokens"), output=cell("output_tokens"),
        cache_read=cell("cache_read_tokens"), cache_creation=cell("cache_creation_tokens"),
        total=f"{total:,}",
    )


def _waste_rows(rows: list[dict]) -> list[WasteRow]:
    out: list[WasteRow] = []
    for r in rows:
        label, sub = WASTE_LABEL.get(r["kind"], (r["kind"], False))
        out.append(WasteRow(label=label, count=f"{int(r.get('count') or 0):,}", usd=present.money(r.get("usd")),
                            unknown=_unknown(r.get("unknown")), sub=sub))
        if r["kind"] == "changes-requested":
            # R9, C5: the rounds no `review` step claimed are counted; their money is not known.
            out.append(WasteRow(label="not recorded", count=f"{int(r.get('note') or 0):,}",
                                usd="not recorded", sub=True))
    return out


def _measured(a: dict) -> str:
    """`0093` R10: the measure and the threshold, `$18.40 > $15`, `4 runs > 3`."""
    value, limit = a.get("value"), a.get("limit")
    if a["kind"] == "over-budget":
        return f"{present.money(value)} > ${limit:g}"
    if a["kind"] == "reruns":
        return f"{value} runs > {limit}"
    if a["kind"] == "tokens-per-turn":
        median = limit / spend.TOKENS_PER_TURN_TIMES
        return f"{value:,.0f} per turn > {spend.TOKENS_PER_TURN_TIMES} × {median:,.0f}"
    return str(value or "")


def _anomaly_rows(rows: list[dict]) -> list[AnomalyRow]:
    return [
        AnomalyRow(
            key=f"{a['kind']}-{i}", kind=ANOMALY_LABEL.get(a["kind"], a["kind"]),
            unit=a.get("unit") or NO_UNIT, stage=a.get("stage") or "—",
            ended=present.when(a.get("ended")) or "—", measured=_measured(a),
            usd=present.money(a.get("usd")),
        )
        for i, a in enumerate(rows)
    ]


# `0053` R10. A chat message longer than this many characters is sent cut to it, with a
# button to fetch the rest. Chosen, not measured; `spec.md ## Answers, câu 1` accepted it.
MESSAGE_CUT = 4000


@dataclasses.dataclass
class BacklogRow:
    """`0074`. One line of the Backlog panel, copied from `Service.board`'s `backlog`."""

    rank: int = 0
    unit: str = ""
    value: str = ""
    effort: str = ""
    basis: str = ""
    by: str = ""
    drift: str = ""
    warnings: str = ""
    agent_differs: str = ""


def _estimated_by(by) -> str:
    """`0082` D68: an agent's estimate reads `agent`; its session id stays in the API."""
    by = str(by or "")
    return "agent" if by.startswith("agent:") else by


def _backlog_row(entry: dict, rank: int) -> BacklogRow:
    est = entry.get("estimate") or {}
    effort = str(est.get("effort") or "")
    if est.get("effort_source") == "guess":
        effort = f"{effort} (guess)"
    basis = " · ".join(x for x in (str(est.get("basis") or ""), str(est.get("effort_basis") or "")) if x)
    other = entry.get("agent_differs") or {}
    return BacklogRow(
        rank=rank, unit=str(entry.get("unit") or ""),
        value=str(est.get("value") or "—"), effort=effort or "—", basis=basis, by=_estimated_by(est.get("by")),
        drift=(f"off by rank — computed {entry.get('computed') or 'none'}" if entry.get("drift") else ""),
        warnings="; ".join(entry.get("warnings") or []),
        agent_differs=(
            f"agent proposed: value {other.get('value')}, effort {other.get('effort')} — {other.get('basis')}"
            if other else ""
        ),
    )


def _relations_text(relations: list | None) -> str:
    """R9. `thay thế` and `phụ thuộc` read from the side they are on; the other two either way.
    `0082` D31: in English, from `present`'s tables; the stored words are unchanged."""
    def word(r: dict) -> str:
        if r.get("direction") == "in" and r.get("type") in present.RELATION_LABEL_IN:
            return present.RELATION_LABEL_IN[r["type"]]
        return present.RELATION_LABEL.get(r.get("type"), str(r.get("type")))
    return "; ".join(
        f"{word(r)} {r.get('other')}"
        for r in relations or []
    )


def backlog_view(data: dict) -> dict:
    """`0074`. The panel's fields from the board's `backlog`; copies, decides nothing."""
    b = data.get("backlog") or {}
    cuts = b.get("terciles")
    # `0082` D32: one English sentence; the cost thresholds stay in the API and in `backlog`.
    note = (f"Only {len(b.get('backlog') or [])} units wait, so a shortlist of 7 picks nothing out."
            if b.get("undiscriminating") else "")
    record = b.get("shortlist_record") or {}
    return {
        "backlog_rows": [_backlog_row(e, int(e.get("rank") or 0)) for e in b.get("shortlist") or []],
        # `0082` R10: a unit with no estimate is a row too, with value and effort empty.
        "backlog_rest": [_backlog_row(e, int(e.get("computed") or 0)) for e in b.get("order") or []]
        + [BacklogRow(unit=str(n), value="—", effort="—") for n in b.get("unestimated") or []],
        "shortlist_draft": [str(e.get("unit") or "") for e in b.get("shortlist") or []],
        "backlog_unestimated": list(b.get("unestimated") or []),
        "backlog_note": note,
        # `0092` R11: the finished units measured, and those left out for an unknown cost.
        "backlog_measured": (
            f"{int(b.get('measured_count') or 0)} finished units measured; "
            f"{int(b.get('undetermined_count') or 0)} left out, cost unknown."
            if int(b.get("undetermined_count") or 0) > 0 else ""
        ),
        "backlog_recorded": (
            f"Saved {present.when(record.get('at'))} by {record.get('by')}: {record.get('reason')}"
            if record else ""
        ),
        "backlog_warnings": [f"{w.get('unit')}: {w.get('text')}" for w in b.get("warnings") or []]
        + list(b.get("problems") or []),
        "backlog_suggested": list(b.get("suggested") or []),
        "propose_warning": str(b.get("propose_consequence") or b.get("propose_warning") or ""),
        "backlog_names": list(b.get("backlog") or []),
    }


def _outcome_fields(label: dict | None) -> dict:
    """`Unit`'s outcome fields from the board's `outcome_label` dict, or all empty.

    `outcome_detail` is the one line a result rests on: its source, or for `không đo được`
    its reason, with the note after it when there is one."""
    if not label:
        return {}
    detail = " — ".join(
        str(x) for x in (label.get("source") or label.get("reason"), label.get("note")) if x
    )
    return {
        # `0082` D22: the English label; the API keeps `text` as it was.
        "outcome_text": str(label.get("label") or label.get("text") or ""),
        "outcome_color": str(label.get("color") or "gray"),
        "outcome_detail": detail,
        "outcome_by": str(label.get("by") or ""),
        "outcome_date": str(label.get("date") or ""),
        "outcome_measured_by": str(label.get("measured_by") or ""),
        "outcome_deadline": str(label.get("deadline") or ""),
        "outcome_hint": str(label.get("hint_label") or label.get("hint") or ""),
        "outcome_invalid": int(label.get("invalid") or 0),
        "outcome_form": bool(label.get("form")),
    }


@dataclasses.dataclass
class Activity:
    """`0051`. One line on a card: a session running on the unit, or one that ended unseen.

    Every field is copied from `Service.running`; the page chooses only the words.
    """

    # `running`, `rebasing` or `ended, unknown`.
    label: str = ""
    # `ᚢ Uruz`, or empty for a mechanical rebase and for `ended, unknown`.
    agent: str = ""
    stage: str = ""
    started: str = ""
    # Empty when unknown, never `0` (R5): today they are always unknown while a step runs.
    turns: str = ""
    cost: str = ""
    # `step`, `gebo`, `rebase` or `unknown`.
    kind: str = ""
    # `0073`. The step's `run`, what the watch pane opens; empty for anything else.
    run: str = ""


def _activities(unit: str, read: dict) -> list[Activity]:
    """`0051` R3, R6. `Service.running`'s answer for one unit, as the lines its card shows."""
    out: list[Activity] = []
    for row in (read.get("running") or {}).get(unit) or []:
        agent = row.get("agent") or {}
        turns, cost = row.get("turns"), row.get("cost_usd")
        out.append(Activity(
            label="rebasing" if row.get("kind") == "rebase" else "running",
            agent=f"{agent['glyph']} {agent['name']}" if agent else "",
            stage=str(row.get("stage") or ""),
            started=present.when(row.get("started")),
            turns="" if turns is None else str(turns),
            cost="" if cost is None else f"${float(cost):.2f}",
            kind=str(row.get("kind") or ""),
            run=str(row.get("run") or ""),
        ))
    for row in (read.get("unknown_end") or {}).get(unit) or []:
        out.append(Activity(
            label="ended, unknown",
            stage=str(row.get("stage") or ""),
            started=present.when(row.get("started")),
            kind="unknown",
        ))
    return out


# `0051` R3. Seconds between two asks of `Service.running` while the Board is shown. Chosen,
# half of the 10s the intent accepted, not measured.
RUNNING_POLL = 5

# `0051`. The tabs (client tokens) with a `poll_running` loop alive in this process. Kept in
# the process rather than in the page's state: a state var would outlive the loop it stands
# for across a restart, and the Board would never ask again.
_POLLING: set[str] = set()

# `0051` review round 1, F2. How many asks in a row must find the tab's token unmapped
# before its loop ends. Reflex unmaps a token on every socket drop and maps it again on
# reconnect, so one miss is a flaky network as often as a closed tab. Chosen: 12 asks,
# one minute at `RUNNING_POLL`; not measured.
GONE_AFTER = 12


# `0056` review round 1, F2. The `cos.mjs next` asks in flight, by (workspace, unit). The next
# navigation cancels an arrival's `on_load` chain, and the `load_next` it chained with it; the
# ask itself — `node`, the `gh` it calls, a `git worktree add` in `worktrees.ensure` — runs on
# in a task of its own, awaited through `asyncio.shield`, so nothing is left running unread
# and the next arrival at that unit waits for it instead of starting a second beside it.
_ASKING: dict[tuple[str, str], asyncio.Task] = {}


def _asking(ask, cwd: str, unit: str, join: bool) -> asyncio.Future:
    """`ask(cwd, unit)` in a task no cancellation reaches: the one in flight when `join`."""
    key = (cwd, unit)
    task = _ASKING.get(key)
    if not (join and task is not None and not task.done()
            and task.get_loop() is asyncio.get_running_loop()):
        task = asyncio.ensure_future(ask(cwd, unit))
        _ASKING[key] = task

        def done(t: asyncio.Task, key=key) -> None:
            if _ASKING.get(key) is t:
                del _ASKING[key]
            if not t.cancelled():
                t.exception()  # read, so an answer nobody waited for is not logged unhandled

        task.add_done_callback(done)
    return asyncio.shield(task)


def _tab_gone(token: str) -> bool:
    """Whether the tab behind `token` has no socket open to this process any more.

    Without this a loop started by a tab that was then closed would ask every
    `RUNNING_POLL` seconds until the app stopped. Where no socket server exists at all —
    in-process, as the proofs drive the state — nobody can be gone.
    """
    app_module = sys.modules.get("coscc.coscc")
    namespace = getattr(getattr(app_module, "app", None), "event_namespace", None)
    if namespace is None or not token:
        return False
    return token not in namespace.token_to_sid


def _hold_fields(u: dict) -> dict:
    """`Unit`'s hold fields from one board unit, or all empty when it is not held."""
    held = u.get("hold") or {}
    return {
        "hold_state": str(held.get("state") or ""),
        "hold_reason": str(held.get("reason") or ""),
        "hold_by": str(held.get("by") or ""),
        "hold_date": str(held.get("date") or ""),
        "hold_moves": [str(m) for m in u.get("hold_moves") or []],
    }


def _hold_detail(row: dict) -> str:
    """The Activity detail of one `hold` run-log row after its unit: reason, name, and each
    side effect that did not finish with what it said — a failed close names the pull
    requests already closed, and nothing retries it (review round 1, F2)."""
    detail = f" / {row.get('reason', '')} / by {row.get('by', '')}"
    for e in row.get("effects") or []:
        if e.get("result") != "done":
            detail += f" / {e.get('effect')}: {e.get('result')} ({e.get('detail', '')})"
    return detail


def _integration_fields(info: dict | None) -> dict:
    """`Unit`'s integration fields from the board's `integration` dict, or all empty."""
    if not info:
        return {}
    behind = info.get("behind")
    return {
        "integration_state": str(info.get("state") or ""),
        "integration_reason": str(info.get("reason") or ""),
        "integration_behind": "" if behind is None else str(behind),
        "integration_origin": str(info.get("origin_sha") or "")[:7],
        "integrate_button": bool(info.get("button")),
        "integration_warnings": [str(w) for w in info.get("warnings") or []],
        "integration_needs_person": [str(n) for n in info.get("needs_person") or []],
    }


@dataclasses.dataclass
class Message:
    role: str = ""
    text: str = ""
    # `0053` R10. How many characters of `text` were not sent, 0 when it is whole.
    cut: int = 0


@dataclasses.dataclass
class Conversation:
    id: str = ""
    title: str = ""
    subtitle: str = ""
    resumable: bool = False


@dataclasses.dataclass
class AutopilotStop:
    """`0043` R9. One unit the autopilot will not start anything on, and why."""

    unit: str = ""
    kind: str = ""
    reason: str = ""


@dataclasses.dataclass
class RunningStep:
    """`0034`. One board step running now, as `Service.running_steps` lists it."""

    unit: str = ""
    stage: str = ""
    started_at: str = ""
    stopping: bool = False
    # `0073`. What the watch pane opens.
    run: str = ""


@dataclasses.dataclass
class Run:
    """One row of a unit's timeline."""

    stage: str = ""
    mode: str = ""
    started: str = ""
    ended: str = ""
    outcome: str = ""
    session_id: str = ""
    tokens: str = ""
    usd: str = ""
    color: str = "gray"
    # Why it ended this way, empty when it ended well. A run that says `failed` and nothing
    # else sends the only person who can fix it to the database -- and for a prose stage
    # this is where the reply it was paid for comes back (`coscc/runner.py`, `_with_reply`).
    detail: str = ""
    # `0073`. The step's `run`, empty for one written before `0073` (R13).
    run: str = ""
    # `0089`. The row's own `_details` key: `run`, or `<stage>-<i>` when that is empty.
    key: str = ""


@dataclasses.dataclass
class WatchEvent:
    """`0073` R10, R12. One event of the watch pane, as `events.collapse` shaped it. Never
    more than the collapsed body: the whole of one opened event is `watch_open_text`."""

    seq: int = 0
    at: int = 0
    when: str = ""
    kind: str = ""
    label: str = ""
    body: str = ""
    collapsed: bool = False
    truncated: bool = False
    original_length: int = 0
    persisted: str = ""


# `0073`. The most events the watch pane holds at once. Every frame carries the whole list
# (`spike.md ## U4`: about 2 062 bytes an event at the collapsed size), so the list has a
# ceiling: 400 × 2 062 ≈ 825 KB at worst -- a multiplication, not a measurement, under the
# 1 031 008 bytes U4 measured within 2 s on loopback. Chosen.
WATCH_WINDOW = 400

# Seconds the pane gathers new events before it sends them (`spike.md ## U3`).
WATCH_GATHER = 0.5

# `0073` R13, in English since `0089` (S6): no longer R13's words (`.cos/0089_*/spec.md` C1).
NO_RUN_NOTE = "no event stream: this step ran before events were recorded"


# `0089` R11 (D55). What the board says in place of the service's `read_only_because`, which
# names `COS_WORKING_DIR` and stays as it is for the API (`coscc/board_api_test.py`).
READ_ONLY_NOTE = "No working folder is set, so nothing can be recorded."

# `0043` R9. A label for each of R6's stops (a–f) and the cap; the page words, not a decision.
AUTOPILOT_STOP_LABEL = {
    "a": "Open question", "b": "Needs a person", "c": "Ship waits", "d": "Integration needs a person",
    "e": "Last step did not finish", "f": "Blocked", "cap": "Daily cap", "shortlist": "No shortlist",
}


def _number(text: str, kind: type) -> object:
    """`text` as `kind`, or the text itself for the service to refuse with its reason."""
    try:
        return kind(str(text).strip())
    except ValueError:
        return text


def _watch_note(page: dict) -> str:
    """R13. The line the pane shows so it is never empty without a reason."""
    notes: list[str] = []
    status = page.get("status")
    if status == "purged":
        notes.append(f"events purged ({present.when(page.get('purged_at'))})")
    elif status == "ended-unknown":
        notes.append(
            "the app stopped while this step ran; no events after "
            + (events_mod.when(page.get("last_at")) if page.get("last_at") else "the start")
        )
    elif status == "none":
        notes.append("no events were stored for this run")
    lost = int(page.get("events_lost") or 0)
    if lost > 0:
        notes.append(f"{lost} events missing")
    return " · ".join(notes)


def _watch_events(raw: list) -> list[WatchEvent]:
    return [WatchEvent(**events_mod.collapse(e)) for e in raw]


@dataclasses.dataclass
class Event:
    title: str = ""
    detail: str = ""
    icon: str = "circle-check"
    color: str = "iris"
    time: str = ""


@dataclasses.dataclass
class Knob:
    name: str = ""
    value: str = ""
    detail: str = ""
    on: bool = False


@dataclasses.dataclass
class ModelRow:
    """`0004_no-setting-says-which-model-runs-a-stage`. One stage, or chat, as Settings
    shows it. Every field is copied from `Service.stage_models`; nothing is resolved here."""

    name: str = ""
    agents: int = 1
    model: str = ""
    source: str = ""
    overridden: bool = False
    # `0033`: the effort beside the model, looked up on its own. `chat` has none.
    effort: str = ""
    effort_source: str = ""
    effort_overridden: bool = False
    has_effort: bool = True


@dataclasses.dataclass
class GrantRow:
    stage: str = ""
    tools: str = ""
    commands: str = ""
    turns: str = ""
    budget: str = ""
    warning: str = ""
    consequence: str = ""
    tool_list: list[str] = dataclasses.field(default_factory=list)
    command_list: list[str] = dataclasses.field(default_factory=list)


# --- formatting --------------------------------------------------------------


def _run_target(data: dict) -> tuple[str, str]:
    """`0024`. The stage the run button offers and the sentence beside it, copied from
    `Service.next_step` -- which is `cos.mjs next`'s answer.

    Until `0024` the page worked this out for itself: "the first required stage with no
    artifact". That was a second copy of the loop `.claude/CLAUDE.md` forbids, and once a
    review asked for changes both `impl.md` and `review.md` existed, so it offered `ship`,
    whose gate was closed, and nothing else. Nothing here reads `action` to pick a stage.
    """
    return str(data.get("stage") or ""), str(data.get("action") or "")


def _run_waiting(data: dict) -> list[str]:
    """`0028`. The findings `cos.mjs next` says a person is awaited on, copied. Kept apart
    from `_run_target` so that function's answer is what it was; nothing here decides whether
    anyone is awaited."""
    return [str(x) for x in data.get("waiting") or []]


def _key_label(key: str) -> str:
    """`0071` R3. A Questions row's key as a person reads it: `intent.md#1` is
    `question 1 of intent.md`, `review.md#F2` is `finding F2 of review.md`."""
    artifact, _, number = key.rpartition("#")
    kind = "finding" if number.startswith("F") else "question"
    return f"{kind} {number} of {artifact}"


def _tokens(cost: dict) -> tuple[int, str]:
    """Every token the turn was billed for, as one number.

    Cache reads and writes are included because they are billed. Showing only input plus
    output would report a cache-heavy session as nearly free, which is the opposite of
    what a cost display is for.
    """
    total = sum(int(cost.get(name) or 0) for name in TOKEN_FIELDS)
    return total, (f"{total:,}" if total else "—")


def _job_line(job: dict) -> str:
    """`0068` R10: one running job, as the confirmation lists it."""
    kind = job.get("kind", "")
    if kind == "chat":
        what = f"chat {job.get('session_id') or '(new session)'} in {job.get('workspace', '')}"
    elif kind == "build":
        what = "build local"
    else:
        what = f"{kind} {job.get('unit', '')} {job.get('stage', '')}"
    return f"{what}, since {job.get('started', '')}"


def _channel_line(channel: dict) -> str:
    """`0068`: one channel's state as the panel says it."""
    state = str(channel.get("state") or "")
    parts = [state]
    if channel.get("version"):
        parts.append(str(channel["version"]))
    if channel.get("reason"):
        parts.append(f"— {channel['reason']}")
    return " ".join(parts)


COST_NOTE = "Added up from each finished run"


def cost_note(total: dict) -> str:
    """`0092` R8 c. The Cost tile's caption, saying how many runs it could not add (S1)."""
    n = int(total.get("unknown") or 0)
    return f"{COST_NOTE}; {n} run(s) with unknown cost" if n > 0 else COST_NOTE


def _usd(cost: dict) -> str:
    usd = float(cost.get(COST_USD) or 0.0)
    # `0092` R8 b, c. Runs whose cost nobody knows are never shown as `—` or as nothing:
    # the known part is added, and the rest said to be unknown.
    unknown = int(cost.get("unknown") or 0) > 0
    if not usd:
        return "unknown" if unknown else "—"
    # Under a cent still has to read as a number, not as $0.00.
    known = f"${usd:.4f}" if usd < 0.01 else f"${usd:.2f}"
    return f"{known} + unknown" if unknown else known


def _title_of(unit_name: str) -> str:
    """`0006_demo-data-and-no-durable-store` reads as `Demo data and no durable store`.

    The slug is the only human-written name a unit has before its artifacts are read, and
    reading eight files per card to find a better one would make opening the board cost a
    directory walk. The harness fixes the slug at creation for exactly this reason.
    """
    _, _, slug = unit_name.partition("_")
    words = (slug or unit_name).replace("-", " ").strip()
    return words[:1].upper() + words[1:] if words else unit_name


def _initials(name: str) -> str:
    letters = [part[0] for part in name.replace("_", "-").replace(".", "-").split("-") if part]
    return ("".join(letters[:2]) or name[:2] or "WS").upper()


def _questions(unit: dict) -> tuple[int, list[Question]]:
    """`0016` R7. The open count and the questions of one board unit, copied from what
    `cos.mjs` sent through `coscc/board.py`. Nothing is counted here: `open` is taken as
    sent, so the page and `status --json` cannot disagree.

    `0028`: the findings `cos.mjs` lists in `personFindings` follow, one row each, keyed
    `review.md#F<n>` and answered into `review.md`. They are not counted into `open`, which
    stays `cos.mjs`'s number."""
    return int(unit.get("open") or 0), [
        Question(
            key=f"{q['artifact']}#{q['n']}",
            artifact=str(q["artifact"]),
            number=int(q["n"]),
            text=str(q.get("text") or ""),
            answered=bool(q.get("answered")),
            counted=bool(q.get("counted")),
            label=str(q["n"]),
            by_jera=bool(q.get("by_jera")),
            cites=[str(c) for c in q.get("cites") or []],
            needs_person=bool(q.get("needs_person")),
            proposal=str(q.get("proposal") or ""),
            reason=str(q.get("reason") or ""),
        )
        for q in unit.get("questions") or []
    ] + [
        Question(
            key=f"review.md#{p['id']}",
            artifact="review.md",
            number=0,
            text=str(p.get("reason") or ""),
            answered=bool(p.get("answered")),
            counted=False,
            label=str(p["id"]),
        )
        for p in unit.get("person_findings") or []
    ]


def _rounds(unit: dict) -> list[Round]:
    """`0021`. Each review round as `Service.board` sent it, with its comment state."""
    out = []
    for r in unit.get("rounds") or []:
        c = r.get("comment") or {}
        out.append(
            Round(
                number=int(r.get("n") or 0),
                verdict=str(r.get("verdict") or "unreadable"),
                posted=bool(c.get("posted")),
                url=str(c.get("url") or ""),
                reason=str(c.get("reason") or ""),
            )
        )
    return out


def _lane(unit: dict) -> str:
    """Which column a unit sits in.

    The only derivation in this module, and it is presentation. Nothing downstream reads
    it back, so it cannot drift into a second answer about progress.

    **It deliberately does not use the harness's `blocked`.** Measured on 2026-09-22 by
    reading `cos.mjs status --json` against this repository: `blocked` is `true` for every
    unit that is not finished — `.claude/scripts/cos.mjs:122-135` returns it for "the next
    stage has not been written yet" as readily as for "an artifact is sitting in draft".
    Mapping it onto a lane called *Needs you* put all six unfinished units there and
    left *Planned* and *In progress* permanently empty, which is a board that sorts nothing.

    So the lanes are read off the artifacts instead: a `draft` artifact is something a
    person wrote and has not accepted, and that is the thing worth surfacing.
    """
    action = str(unit.get("next") or "")
    rows = unit.get("stages") or []
    # First, before every other rule. A unit started from this page holds only an
    # `idea.md`, and that idea carries `Status: accepted` (`coscc/units.py` writes it so),
    # so the *In progress* rule below would otherwise claim it the moment its `problems`
    # went empty. `cos.mjs` decides what pre-intent means; this only places it.
    if unit.get("phase") == "pre-intent":
        return "Planned"
    if action == "finished" or action.startswith("closed"):
        return "Complete"
    # `changes-requested` sits with `draft`: the review found something and the unit waits
    # on a fix, which is the kind of thing this lane exists to surface (`0015`).
    if unit.get("problems") or any(
        r.get("status") in ("draft", "changes-requested") for r in rows
    ):
        return "Needs you"
    if any(r.get("status") != "not started" for r in rows):
        return "In progress"
    return "Planned"


def _current_stage(unit: dict, stages: list[str]) -> str:
    """The stage a person would say the unit is at: the last one with an artifact."""
    started = [r["stage"] for r in unit.get("stages") or [] if r.get("status") != "not started"]
    return started[-1] if started else (stages[0] if stages else "")


class StudioState(rx.State):
    """The whole page. No business state lives here — it is all read back from `Service`."""

    screen: str = "overview"
    # `0056`. The socket `session_id` of the last full read; another one is a new page
    # (`arrive`). Then the workspace and the unit last read, and the unit `load_next` last
    # answered for. Each is set once its read is done. Backend only.
    _loaded_sid: str = ""
    _read_cwd: str = ""
    _read_unit: str = ""
    _asked: str = ""
    # `0056` F2. Set by `arrive` for the `load_next` it chains, which then waits for an ask
    # already in flight; *Ask again*, a step's end and a hold move ask afresh.
    _ask_joins: bool = False
    loading: bool = False
    busy: bool = False
    error: str = ""
    notice: str = ""

    # -- workspaces
    workspaces: list[Workspace] = []
    cwd: str = ""
    working_dir: str = ""
    workspace_query: str = ""

    new_name: str = ""
    new_label: str = ""
    new_url: str = ""
    form_error: str = ""
    workspace_form: bool = False
    editing: str = ""
    remove_name: str = ""

    # -- board
    stages: list[str] = []
    # `0053`. Every unit of the last board read, whole, keyed by id in board order. Backend
    # only: the page gets `cards` and, for the one unit open, `current_unit`. Always
    # assigned a new dict, never changed in place (`spike.md ## U3` measured only that).
    _full: dict[str, Unit] = {}
    # `0053` R7, R8. The one list of cards the page receives; lanes, List, Dropped, the
    # palette and Overview all filter it on the page.
    cards: list[Card] = []
    # `0053` R7. The open unit, whole, copied from `_full` by `_set_current` — never by a
    # computed var walking a list the page would then receive too.
    current_unit: Unit = Unit()
    board_note: str = ""
    # Set only when the board is empty (`0001_product-describes-a-state-it-is-not-in` R6):
    # the store the board read, the host repository, and how many units the host's own
    # `.cos/` holds that the board does not list.
    empty_store: str = ""
    empty_host: str = ""
    empty_host_units: int = 0
    recording: bool = False
    # `0051`. `Service.running`'s latest answer, kept so a board read that rebuilds every
    # card can put `live` back on at once instead of waiting for the next ask. Backend only.
    _running_read: dict = {}
    query: str = ""
    focus: str = "All work"
    board_view: str = "Board"
    density: str = "comfortable"

    # -- starting a unit (`0014` R8)
    new_slug: str = ""
    new_brief: str = ""
    starting: bool = False
    branch: str = ""
    # `0017`. Per unit: where its worktree is and what preparing it said, as one line.
    # Backend only since `0053`: the page reads the open unit's line, `unit_tree`.
    _trees: dict[str, str] = {}

    @rx.var
    def unit_tree(self) -> str:
        return self._trees.get(self.unit_id, "")

    # -- one unit
    unit_id: str = ""
    detail_tab: str = "overview"
    artifact: str = ""
    artifact_file: str = ""
    artifact_missing: bool = False
    runs: list[Run] = []
    # `0034`. Every step running in this workspace, as the service lists it. One per unit
    # -- the service refuses a second -- and any number of units at once. This page holds
    # no running flag of its own; the list is re-read, never patched.
    running_steps: list[RunningStep] = []
    # `0043` R9. The board's `autopilot` block, copied: whether it is on, its stops, the cap.
    autopilot_on: bool = False
    autopilot_stops: list[AutopilotStop] = []
    autopilot_cap: str = ""
    autopilot_refused: str = ""
    run_log: str = ""
    # The unit whose step this page is streaming into `run_log`, so another unit's
    # reply is never shown under the one now open.
    log_unit: str = ""
    # `0082` R7, R3: which *Details* are open, by key. Closed, their content is not in the DOM.
    open_details: list[str] = []
    # -- `0073`: the watch pane. One `run` at a time; `_watch_token` changes whenever the pane
    # closes or opens another, and the loop following the old one stops at its next batch.
    watch_run: str = ""
    watch_unit: str = ""
    watch_title: str = ""
    watch_status: str = ""
    watch_note: str = ""
    watch_events: list[WatchEvent] = []
    watch_has_older: bool = False
    # Older pages pushed the newest rows out of the list; *Jump to latest* brings them back.
    watch_has_newer: bool = False
    # At the bottom and taking new events; off once older ones were loaded.
    watch_following: bool = False
    # New events not in the list because it was full while the person read older ones.
    watch_pending: int = 0
    watch_open_seq: int = 0
    watch_open_text: str = ""
    _watch_token: int = 0

    # -- `0068`: the *Update* panel. Every field is copied from `Service.update_status`,
    # re-read on load, on every screen change and on every `poll_running` ask; the page
    # decides nothing about an update. `update_pending` is what Run and Send warn on (R9).
    upd_version: str = ""
    upd_commit: str = ""
    upd_available: bool = False
    upd_state: str = ""
    upd_waiting: list[str] = []
    upd_pending_reason: str = ""
    upd_release: str = ""
    upd_release_ready: bool = False
    upd_local: str = ""
    upd_local_ready: bool = False
    upd_local_configured: bool = False
    upd_local_tail: str = ""
    upd_checked_at: str = ""
    upd_error: str = ""
    upd_error_tail: str = ""
    upd_last: str = ""
    upd_last_tail: str = ""
    update_pending: bool = False
    update_warning: str = ""
    # `0082` R9: the service's line and the buttons it lists; the full sha for *Details*.
    upd_line: str = ""
    upd_local_line: str = ""
    upd_actions: list[str] = []
    upd_commit_full: str = ""
    # R10's confirmation: what "áp dụng ngay" would cut, and the token of that list.
    cut_open: bool = False
    cut_channel: str = ""
    cut_items: list[str] = []
    cut_token: str = ""
    # `0024`. The stage `cos.mjs next` names for the open unit, and what it said. Set only
    # by `load_next`, from `_run_target`; `next_stage` reads it and nothing computes it.
    run_stage: str = ""
    run_said: str = ""
    # `0028`. The findings `cos.mjs next` says a person is awaited on; set only by
    # `load_next`, from `_run_waiting`. Non-empty means the button offers nothing and the
    # page points at the Questions tab instead.
    run_waiting: list[str] = []

    # -- answering a question (`0016`). One text box is live at a time: typing into a
    # question's box makes it the target, and the box of every other question reads empty.
    # `0082` R3: no name is typed; the service records `owner`.
    answer_target: str = ""
    answer_text: str = ""
    # `0071` R6. The key of the question being sent, `""` while none is: only that row's
    # button shows it is sending.
    answering_key: str = ""
    # `0021`. The round being posted, 0 while none is.
    posting_round: int = 0
    # `0035`. True while an integration runs; locks the *Integrate* button.
    integrating: bool = False
    # `0044`. True while Jera runs on the open unit; locks *Ask Jera*.
    asking_jera: bool = False
    # `0044` R8a. The Settings text Jera reads as precedent, as typed and as last saved.
    decision_preferences: str = ""
    # `0047`. The outcome form. Nobody types who records it (`0082` R3); `outcome_measured_by`
    # starts as `agent`, the case with no script to run.
    # Both hold the English label the select shows (`0089` R1, R4); `record_outcome` sends
    # the stored word.
    outcome_result: str = "met"
    outcome_measured_by: str = "Agent"
    outcome_source: str = ""
    outcome_reason: str = ""
    outcome_note: str = ""
    recording_outcome: bool = False
    # `0045`. The reason typed into the hold panel, and whether a move is in flight.
    hold_reason: str = ""
    holding: bool = False
    # `0074`. The Backlog panel, copied from `Service.board`'s `backlog` by `backlog_view`,
    # and what a person types into it. `0082` R10: the shortlist is edited row by row.
    backlog_rows: list[BacklogRow] = []
    backlog_rest: list[BacklogRow] = []
    backlog_unestimated: list[str] = []
    backlog_note: str = ""
    backlog_measured: str = ""
    backlog_recorded: str = ""
    backlog_warnings: list[str] = []
    backlog_suggested: list[str] = []
    propose_warning: str = ""
    _backlog_history: dict = {}
    history_unit: str = ""
    history_lines: list[str] = []
    backlog_names: list[str] = []
    shortlist_draft: list[str] = []
    # The unit whose row is open for editing, `""` for none.
    backlog_editing: str = ""
    shortlist_reason: str = ""
    est_unit: str = ""
    est_value: str = ""
    est_effort: str = ""
    est_basis: str = ""
    rel_unit: str = ""
    rel_other: str = ""
    rel_type: str = "liên quan"
    rel_op: str = "add"
    rel_reason: str = ""
    proposing: bool = False

    # -- sessions
    conversations: list[Conversation] = []
    session_id: str = ""
    # `0053` R10. What the page shows: each message of `_history`, in the same order, cut
    # to `MESSAGE_CUT` characters; `open_message` puts one back whole.
    messages: list[Message] = []
    _history: list[Message] = []
    # `0053` R9. The workspace the conversation list was last read for, `""` when none:
    # Sessions reads it on arrival only when this differs from `cwd`.
    _sessions_cwd: str = ""
    prompt: str = ""
    sending: bool = False

    # -- activity and settings
    events: list[Event] = []
    usage_total_tokens: str = "—"
    usage_total_usd: str = "—"
    usage_cost_note: str = COST_NOTE
    # `0093`. The *Cost* screen, read only on arrival there (R12), and the open unit's part.
    cost_recording: bool = True
    cost_total_usd: str = "—"
    cost_total_steps: str = "0"
    cost_total_unknown: str = ""
    cost_offset: str = ""
    cost_units: list[SpendRow] = []
    cost_stages: list[SpendRow] = []
    cost_days: list[SpendRow] = []
    cost_tokens: list[TokenRow] = []
    cost_waste: list[WasteRow] = []
    cost_anomalies: list[AnomalyRow] = []
    unit_cost_stages: list[SpendRow] = []
    unit_anomalies: list[AnomalyRow] = []
    knobs: list[Knob] = []
    grants: list[GrantRow] = []
    data_dir: str = ""
    host_port: str = ""
    # Whether the bound address reaches this machine only. Since `0011` the default
    # is `0.0.0.0`, so the page may no longer state "loopback" as a fact -- it has to
    # read it. A page that claims a safety property it does not have is worse than a
    # page that says nothing, and `0007` exists because of exactly that.
    loopback_only: bool = True
    # `COS_MODEL`, the fallback for a row nothing else answers. Until `0004_no-setting-
    # says-which-model-runs-a-stage` it was the model of every session.
    model: str = ""
    # The model of each stage, then chat, and why a row fell back. The box being typed in
    # is `model_target`, the same one-box-at-a-time shape the answers use.
    model_rows: list[ModelRow] = []
    model_problems: list[str] = []
    # `0043` R2. One workspace's autopilot settings, as `Service.autopilot_settings` has them.
    ap_on: bool = False
    ap_may_ship: bool = False
    ap_max_parallel: str = ""
    ap_cap: str = ""
    ap_refused: str = ""
    model_target: str = ""
    model_text: str = ""
    saving_model: bool = False

    # -- chrome
    mobile_open: bool = False
    command_open: bool = False
    command_query: str = ""

    # -- computed ------------------------------------------------------------

    @rx.var
    def screen_title(self) -> str:
        return SCREEN_TITLES.get(self.screen, "Overview")

    @rx.var
    def session_title(self) -> str:
        """`0082` R14. The open conversation's title, as its row in the list shows it."""
        return next((c.title for c in self.conversations if c.id == self.session_id), "")

    @rx.var
    def current_workspace(self) -> Workspace:
        for w in self.workspaces:
            if w.id == self.cwd:
                return w
        return Workspace(name="No workspace", initials="--", color="gray")

    @rx.var
    def has_workspace(self) -> bool:
        return self.cwd != ""

    @rx.var
    def filtered_workspaces(self) -> list[Workspace]:
        q = self.workspace_query.strip().lower()
        if not q:
            return self.workspaces
        return [w for w in self.workspaces if q in w.name.lower() or q in w.label.lower()]

    # `0053` R8. The lanes, the List, Dropped, the palette and Overview each filter `cards`
    # on the page by one of the lists of ids below: a list of cards per lane would send
    # every card again (`spike.md ## U2`). Order is always `cards`' order.

    @rx.var
    def shown_ids(self) -> list[str]:
        q = self.query.strip().lower()
        # `0045` (`spec.md ## Answers, câu 1`). A dropped unit leaves the four lanes for the
        # collapsed group at the foot of the board; a paused one stays in its lane.
        rows = [c for c in self.cards if c.hold_state != "dropped"]
        if q:
            rows = [c for c in rows if q in c.id.lower() or q in c.title.lower()]
        if self.focus == "Autonomous":
            rows = [c for c in rows if c.mode == "autonomous"]
        elif self.focus == "Needs you":
            # `Unit.needs_attention` is set from exactly this (`_load_board`).
            rows = [c for c in rows if c.lane == "Needs you"]
        return [c.id for c in rows]

    @rx.var
    def lane_counts(self) -> dict[str, int]:
        """How many shown cards each lane holds, for its count and its *Nothing here*."""
        shown = set(self.shown_ids)
        counts = {name: 0 for name in LANE_COLOR}
        for c in self.cards:
            if c.id in shown:
                counts[c.lane] = counts.get(c.lane, 0) + 1
        return counts

    @rx.var
    def resume_id(self) -> str:
        """*Pick up where you left off*: the first shown card in progress, `""` if none."""
        shown = set(self.shown_ids)
        return next((c.id for c in self.cards if c.id in shown and c.lane == "In progress"), "")

    @rx.var
    def active_count(self) -> int:
        return len([c for c in self.cards if c.lane == "In progress"])

    @rx.var
    def attention_count(self) -> int:
        return len([c for c in self.cards if c.lane == "Needs you" and c.hold_state != "dropped"])

    @rx.var
    def dropped_count(self) -> int:
        """`0045`. How many units `cos.mjs` reads as dropped, for the collapsed group."""
        return len([c for c in self.cards if c.hold_state == "dropped"])

    @rx.var
    def ws_name(self) -> str:
        """`0056` R8. What `ws=` carries: the workspace's name, never its path."""
        return next((w.name for w in self.workspaces if w.id == self.cwd), "")

    @rx.var
    def unit_missing(self) -> bool:
        """`0056` R9. An address named a unit this workspace's board does not list."""
        return (self.unit_id != "" and not self.loading
                and not any(c.id == self.unit_id for c in self.cards))

    @rx.var
    def unit_dropped(self) -> bool:
        """`0056` R11. The open unit is dropped, so the dialog offers nothing that writes."""
        return self.current_unit.hold_state == "dropped"

    @rx.var
    def board_href(self) -> str:
        ws = next((w.name for w in self.workspaces if w.id == self.cwd), "")
        return place.href(place.Place("board", ws))

    @rx.var
    def open_questions_here(self) -> list[Question]:
        """`0016`. The open unit's unanswered questions, counted artifact's first. `0044`:
        then the ones Jera answered, so a person can read them and answer over them."""
        shown = [q for q in self.current_unit.questions if not q.answered or q.by_jera]
        return sorted(shown, key=lambda q: (q.answered, not q.counted))

    @rx.var
    def jera_can_ask(self) -> bool:
        """`0044` R2, as the page can see it: an unanswered question outside `review.md`.
        `Service.precedent` still decides; this only hides a button it would refuse (S8)."""
        return self.current_unit.answerable and any(
            not q.answered and q.artifact != "review.md" for q in self.current_unit.questions
        )

    @rx.var
    def next_stage(self) -> str:
        """The stage the run button would run: the one `cos.mjs next` named (`0024`)."""
        return self.run_stage

    @rx.var
    def next_cell(self) -> Cell:
        for cell in self.current_unit.cells:
            if cell.stage == self.next_stage:
                return cell
        return Cell()

    @rx.var
    def running_here(self) -> bool:
        return self.unit_id != "" and any(r.unit == self.unit_id for r in self.running_steps)

    @rx.var
    def log_here(self) -> bool:
        return self.run_log != "" and self.log_unit == self.unit_id

    @rx.var
    def command_ids(self) -> list[str]:
        q = self.command_query.strip().lower()
        if not q:
            return [c.id for c in self.cards[:5]]
        return [c.id for c in self.cards if q in c.title.lower() or q in c.id.lower()][:6]

    @rx.var
    def usage_rows(self) -> list[UsageRow]:
        """`0053` R11. Only while *Activity & usage* is shown; nothing elsewhere.

        `0092` R8 c: a unit whose steps all died before a token was counted still has a
        row, reading `unknown`, rather than leaving the table as if it cost nothing."""
        if self.screen != "activity":
            return []
        return [UsageRow(id=c.id, title=c.title, tokens=c.tokens, usd=c.usd,
                         token_count=c.token_count) for c in self.cards
                if c.token_count > 0 or c.usd not in ("", "—")]

    @rx.var
    def usage_scale(self) -> int:
        """The bar scale, taken from the largest real value rather than from a guess."""
        return max([c.token_count for c in self.cards] + [1])

    # -- plumbing ------------------------------------------------------------

    def _fail(self, e: Exception) -> None:
        """`Invalid` carries a reason a caller shows verbatim. Nothing rewrites it."""
        self.error = str(e)

    # -- loading -------------------------------------------------------------

    def _load_settings(self) -> None:
        data = SERVICE.settings()
        self.working_dir = data.get("working_dir") or ""
        self.data_dir = data.get("data_dir") or ""
        self.host_port = f"{data.get('host')}:{data.get('port')}"
        self.loopback_only = data.get("host") in ("127.0.0.1", "localhost", "::1")
        self.model = data.get("cos_model") or "unset"
        self.knobs = [
            Knob(name=k["name"], value=k["value"], detail=k["detail"], on=bool(k["on"]))
            for k in data.get("knobs") or []
        ]
        self.grants = [
            GrantRow(
                stage=g["stage"],
                tools=g["tools"],
                commands=g["commands"],
                turns=str(g["max_turns"]),
                budget=f"${g['max_budget_usd']:.2f}",
                warning=g["warning"],
                consequence=str(g.get("consequence") or ""),
                tool_list=list(g.get("tool_list") or []),
                command_list=list(g.get("command_list") or []),
            )
            for g in data.get("grants") or []
        ]

    def _show_models(self, data: dict) -> None:
        self.model_rows = [
            ModelRow(
                name=str(r["name"]),
                agents=int(r["agents"]),
                model=str(r["model"] or "SDK default"),
                source=str(r["source"]),
                # A `:novel` row can show `override` inherited from its base row; only
                # the service knows whether this row itself has one to reset.
                overridden=bool(r.get("overridden", r["source"] == "override")),
                effort=str(r.get("effort") or "SDK default"),
                effort_source=str(r.get("effort_source") or "none"),
                effort_overridden=bool(r.get("effort_overridden", r.get("effort_source") == "override")),
                has_effort=str(r["name"]) != "chat",
            )
            for r in data.get("rows") or []
        ]
        self.model_problems = [str(p) for p in data.get("problems") or []]

    async def _load_models(self) -> None:
        self._show_models(await SERVICE.stage_models())
        self._load_autopilot()

    def _show_autopilot_block(self, block: dict) -> None:
        """`0043` R9. Copied from the board; nothing here decides whether to stop."""
        self.autopilot_on = bool(block.get("on"))
        self.autopilot_refused = str(block.get("refused_because") or "")
        self.autopilot_stops = [
            AutopilotStop(unit=str(x.get("unit") or "the workspace"), kind=AUTOPILOT_STOP_LABEL.get(
                str(x.get("kind") or ""), str(x.get("kind") or "")), reason=str(x.get("reason") or ""))
            for x in block.get("stops") or []
        ]
        cap = block.get("cap") or {}
        self.autopilot_cap = (
            "" if not cap else
            f"Today: {cap.get('spent', 0):.2f} spent, "
            + (f"{cap.get('estimated', 0):.2f} of it estimated, " if cap.get("estimated_count") else "")
            + f"{cap.get('running', 0):.2f} running, cap {cap.get('limit', 0):.2f} USD"
        )

    def _show_autopilot(self, data: dict) -> None:
        self.ap_on = bool(data.get("autopilot"))
        self.ap_may_ship = bool(data.get("autopilot_may_ship"))
        self.ap_max_parallel = str(data.get("max_parallel") or "")
        cap = data.get("daily_cap_usd")
        self.ap_cap = f"{cap:g}" if isinstance(cap, (int, float)) else ""
        self.ap_refused = str(data.get("refused_because") or "")

    def _load_autopilot(self) -> None:
        if not self.cwd:
            return
        try:
            self._show_autopilot(SERVICE.autopilot_settings(self.cwd))
        except Invalid:
            return

    def _load_workspaces(self) -> None:
        data = SERVICE.workspaces()
        self.working_dir = data.get("working_dir") or ""
        rows = data.get("workspaces") or []
        self.workspaces = [
            Workspace(
                id=row["path"],
                name=row["name"],
                path=row["path"],
                label=row["label"],
                source=row["source"],
                missing=bool(row["missing"]),
                # An env workspace cannot be renamed or removed from here, and the page
                # has to be able to tell rather than offering a button that refuses.
                removable=row["source"] == "store",
                initials=_initials(row["name"]),
                color=MARK_COLORS[i % len(MARK_COLORS)],
            )
            for i, row in enumerate(rows)
        ]
        if self.cwd and not any(w.id == self.cwd for w in self.workspaces):
            self.cwd = ""
        if not self.cwd and self.workspaces:
            self.cwd = self.workspaces[0].id

    def _load_running(self) -> None:
        """`0034`. In memory and synchronous: no `gh`, no git, so a step's first chunk
        can ask it without waiting on anything a full board read waits on."""
        self.running_steps = []
        if not self.cwd:
            return
        try:
            self.running_steps = [RunningStep(**{**r, "started_at": present.when(r.get("started_at"))})
                                  for r in SERVICE.running_steps(self.cwd)]
        except Invalid:
            self.running_steps = []

    def _set_current(self) -> None:
        """`0053`. `current_unit` from `_full` for `unit_id`: its own copy, lists and the
        rows in them included, so nothing shares an object with `_full` (review round 1,
        F1). Called wherever either of the two changes."""
        found = self.get_value("_full").get(self.unit_id)
        self.current_unit = copy.deepcopy(found) if found is not None else Unit()

    async def _load_board(self) -> None:
        self._full, self.cards, self.stages, self.board_note = {}, [], [], ""
        self._set_current()
        self.empty_store, self.empty_host, self.empty_host_units = "", "", 0
        self.branch = ""
        self._load_running()
        if not self.cwd:
            return
        try:
            self.branch = (await SERVICE.branch_here(self.cwd))["branch"]
        except Invalid:
            # A workspace that is not a git checkout still has a board. Saying nothing is
            # right here: there is no branch to show, and that is not an error to report.
            self.branch = ""
        try:
            data = await SERVICE.board(self.cwd)
        except Invalid as e:
            self.board_note = str(e)
            return

        self.stages = list(data["stages"])
        for name, value in backlog_view(data).items():
            setattr(self, name, value)
        self._backlog_history = dict((data.get("backlog") or {}).get("history") or {})
        self._trees = {
            u["name"]: tree_line(u.get("worktree")) for u in data.get("units") or []
        }
        self.recording = bool(data["recording"])
        self._show_autopilot_block(data.get("autopilot") or {})
        read_only = READ_ONLY_NOTE if data.get("read_only_because") else ""
        self.board_note = read_only or data.get("empty_because") or ""
        empty = data.get("empty") or {}
        self.empty_store = str(empty.get("store") or "")
        self.empty_host = str(empty.get("host") or "")
        self.empty_host_units = int(empty.get("host_units") or 0)
        if self.empty_host_units > 0:
            # `empty_because` speaks of the store's `.cos/`, and next to a host `.cos/`
            # that is full it reads as a claim about the wrong directory — the fault this
            # unit exists for. Only the read-only reason survives, appended by the page.
            self.board_note = read_only

        units: list[Unit] = []
        for u in data["units"]:
            cells = []
            for row in u["stages"]:
                label, color = _cell_label(row)
                cells.append(Cell(
                    stage=row["stage"],
                    status=row["status"],
                    label=label,
                    mode=row["mode"],
                    color=color,
                    started=row["status"] != "not started",
                    optional=bool(row.get("optional")),
                    grants=", ".join(row.get("grants") or []) or "no tools",
                    warning=row.get("warning") or "",
                    opens_tools=bool(row.get("grants")),
                    consequence=str(row.get("consequence") or ""),
                ))
            started = len([c for c in cells if c.started])
            lane = _lane(u)
            stage = _current_stage(u, self.stages)
            count, shown = _tokens(u.get("cost") or {})
            # The file-only stage `cos.mjs` names, not the one the run button asks for: that
            # one can cost two `gh` calls, and this runs for every card (`0024` plan, Risk 7).
            nxt = next((c for c in cells if c.stage == (u.get("next_stage") or "")), None)
            mode = nxt.mode if nxt is not None else "manual"
            waiting, asked = _questions(u)
            units.append(
                Unit(
                    id=u["name"],
                    title=_title_of(u["name"]),
                    summary=u.get("next") or "",
                    lane=lane,
                    stage=stage,
                    color=LANE_COLOR.get(lane, "gray"),
                    owner="AI" if mode == "autonomous" else "You",
                    mode=mode,
                    tokens=shown,
                    usd=_usd(u.get("cost") or {}),
                    token_count=count,
                    progress=int(started * 100 / len(cells)) if cells else 0,
                    needs_attention=lane == "Needs you",
                    problems="; ".join(u.get("problems") or []),
                    cells=cells,
                    open_questions=waiting,
                    questions=asked,
                    pr_url=str((u.get("pr") or {}).get("url") or ""),
                    rounds=_rounds(u),
                    **_integration_fields(u.get("integration")),
                    **_outcome_fields(u.get("outcome_label")),
                    live=_activities(u["name"], self._running_read),
                    **_hold_fields(u),
                    shortlist_rank=int((u.get("backlog") or {}).get("rank") or 0),
                    relations_text=_relations_text((u.get("backlog") or {}).get("relations")),
                    answerable=bool(u.get("answerable", True)),
                    attention_reason=str(u.get("attention_reason") or ""),
                )
            )
        self._full = {u.id: u for u in units}
        self.cards = [_card(u) for u in units]
        self._set_current()

    def _apply_running(self, read: dict) -> None:
        """`0051` R3. Put one `Service.running` answer on every card. Decides nothing.

        `0053` C4: the cards are sent again only when a card's `live` changed, and the open
        unit only when its own did — an ask that changes nothing sends neither."""
        self._running_read = read
        full, moved = {}, set()
        for key, unit in self.get_value("_full").items():
            live = _activities(key, read)
            if live != unit.live:
                unit = dataclasses.replace(unit, live=live)
                moved.add(key)
            full[key] = unit
        if not moved:
            return
        self._full = full
        self.cards = [_card(u) for u in full.values()]
        if self.unit_id in moved:
            self._set_current()

    def _load_sessions(self) -> None:
        self.conversations, self.messages, self._history = [], [], []
        if not self.cwd:
            self.session_id = ""
            return
        try:
            data = SERVICE.sessions_for(self.cwd, limit=40)
        except Invalid as e:
            self._fail(e)
            return
        self._sessions_cwd = self.cwd
        rows = [
            Conversation(
                id=row["session_id"],
                title=(row.get("summary") or row.get("session_id") or "")[:60] or "Untitled",
                subtitle=present.when(row.get("last_modified") or row.get("created_at")),
                resumable=bool(row.get("resumable")),
            )
            for row in data["sessions"]
        ]
        self.conversations = rows
        # A session this app created but the SDK has not listed would be invisible here.
        # Measured on 2026-09-22 against a real send: it does not happen — `sessions_for`
        # returned the new session on the first call after the reply finished. An earlier
        # version of this method inserted a placeholder row for that case; it was removed
        # once the measurement showed the case does not arise, because a branch nothing
        # reaches is a branch nobody maintains.
        if self.session_id and not any(c.id == self.session_id for c in rows):
            self.session_id = ""
        if not self.session_id and self.conversations:
            self.session_id = self.conversations[0].id
        self._load_history()

    def _load_history(self) -> None:
        if not (self.cwd and self.session_id):
            self.messages, self._history = [], []
            return
        try:
            data = SERVICE.history(self.cwd, self.session_id)
        except Invalid as e:
            self._fail(e)
            return
        rows = [
            Message(role=m.get("role") or "assistant", text=m.get("text") or "")
            for m in data["messages"]
            if (m.get("text") or "").strip()
        ]
        # A read that comes back empty while something is on screen is not a reason to
        # clear the screen: the exchange the user just had would be the thing thrown away,
        # and this app keeps no other copy of it.
        if not rows and self.messages:
            return
        self._history = rows
        self.messages = [
            Message(role=m.role, text=m.text[:MESSAGE_CUT], cut=max(len(m.text) - MESSAGE_CUT, 0))
            for m in rows
        ]

    def _keep_whole(self, shown: list[Message]) -> None:
        """`0053` review round 1, F3. After `send` reads the conversation again, a message
        the page showed whole just before — the reply that streamed, one opened with
        `open_message` — stays whole instead of shrinking under the reader. Matched on the
        text, not the index: the read may not line up with what streamed."""
        whole = {m.text for m in shown if not m.cut and len(m.text) > MESSAGE_CUT}
        full = self.get_value("_history")
        messages = list(self.get_value("messages"))
        kept = False
        for i, m in enumerate(messages):
            if m.cut and i < len(full) and full[i].text in whole:
                messages[i] = Message(role=full[i].role, text=full[i].text, cut=0)
                kept = True
        if kept:
            self.messages = messages

    def _load_activity(self) -> None:
        self.events = []
        self.usage_total_tokens, self.usage_total_usd = "—", "—"
        self.usage_cost_note = COST_NOTE
        if not self.cwd:
            return
        try:
            # One read for both halves of this screen; `activity` and `usage` on their own
            # would each scan and parse the identical rows.
            feed = SERVICE.activity_and_usage(self.cwd, limit=40)
        except Invalid as e:
            self._fail(e)
            return
        icons = {
            "mode": ("sliders-horizontal", "blue"),
            "start": ("zap", "iris"),
            "end": ("circle-check", "grass"),
            # `0019` plan step 7. What a stopped step left behind, captured just before
            # `end` — its own row, distinct from the `end` row that follows it.
            "attempt": ("camera", "amber"),
            # `0045` R10. A person paused, dropped or resumed a unit.
            "hold": ("pause", "amber"),
        }
        events: list[Event] = []
        for row in feed["events"]:
            icon, color = icons.get(row["kind"], ("dot", "gray"))
            if row["kind"] == "end" and row["outcome"] != "done":
                icon, color = "triangle-alert", "amber"
            title = {
                "mode": f"{row['stage']} set to {row['mode']}",
                "start": f"{row['stage']} started ({row['mode']})",
                "end": f"{row['stage']} {row['outcome']}",
                "attempt": f"{row['stage']} stopped — what it left was recorded",
                "hold": f"{row.get('from', '')} → {row.get('to', '')}",
            }.get(row["kind"], row["kind"])
            detail = f"{row['unit']}"
            if row["kind"] == "hold":
                detail += _hold_detail(row)
            if row["denials"]:
                detail += f" / {row['denials']} tool call(s) refused"
            if row["artifact"]:
                detail += f" / wrote {row['artifact']}"
            events.append(Event(title=title, detail=detail, icon=icon, color=color, time=present.when(row["at"])))
        self.events = events
        total = feed.get("total") or {}
        _, shown = _tokens(total)
        self.usage_total_tokens = shown
        self.usage_total_usd = _usd(total)
        self.usage_cost_note = cost_note(total)

    def _load_cost(self) -> None:
        """`0093`. The *Cost* screen, from one read of the run log (R12). The review rounds
        come from the board already read (R9); nothing here adds or decides a figure."""
        self.cost_units, self.cost_stages, self.cost_days = [], [], []
        self.cost_tokens, self.cost_waste, self.cost_anomalies = [], [], []
        self.cost_total_usd, self.cost_total_steps, self.cost_total_unknown = "—", "0", ""
        self.cost_offset, self.cost_recording = "", True
        if not self.cwd:
            return
        rounds = {key: [r.verdict for r in u.rounds] for key, u in self.get_value("_full").items()}
        try:
            data = SERVICE.cost(self.cwd, rounds)
        except Invalid as e:
            self._fail(e)
            return
        if not data.get("recording"):
            self.cost_recording = False
            return
        total = data["total"]
        self.cost_total_usd = present.money(total.get("usd"))
        self.cost_total_steps = f"{int(total.get('steps') or 0):,}"
        self.cost_total_unknown = _unknown(total.get("unknown"))
        self.cost_offset = data["offset"]
        self.cost_units = _spend_rows(data["by_unit"], unit=True)
        self.cost_stages = _spend_rows(data["by_stage"])
        self.cost_days = _spend_rows(data["by_day"])
        tokens = data["tokens"]
        self.cost_tokens = [_token_row("Workspace", tokens["workspace"])] + [
            _token_row(t["stage"] or "—", t) for t in tokens["by_stage"]
        ]
        self.cost_waste = _waste_rows(data["waste"])
        self.cost_anomalies = _anomaly_rows(data["anomalies"])

    def _load_unit_cost(self) -> None:
        """`0093` R11. The open unit's cost by stage and its anomalies."""
        self.unit_cost_stages, self.unit_anomalies = [], []
        if not (self.cwd and self.unit_id):
            return
        try:
            data = SERVICE.unit_cost(self.cwd, self.unit_id)
        except Invalid as e:
            self._fail(e)
            return
        self.unit_cost_stages = _spend_rows(data["by_stage"])
        self.unit_anomalies = _anomaly_rows(data["anomalies"])

    def _load_timeline(self) -> None:
        self.runs = []
        if not (self.cwd and self.unit_id):
            return
        try:
            data = SERVICE.timeline(self.cwd, self.unit_id)
        except Invalid as e:
            self._fail(e)
            return
        self.runs = [
            Run(
                stage=r.get("stage") or "",
                mode=r.get("mode") or "",
                # `0089` R12: a reader's time; `ended` is empty while the run is not over.
                started=present.when(r.get("started")),
                ended=present.when(r.get("ended")),
                outcome=r.get("outcome") or "—",
                session_id=(r.get("session_id") or "—")[:12],
                tokens=_tokens(r.get("cost") or {})[1],
                # `0092` R8 b: an ended run that reported no cost reads `unknown`, not `—`.
                usd=_usd({**(r.get("cost") or {}), "unknown": int(
                    r.get("ended") is not None and not r.get("reported", True)
                )}),
                color="grass" if r.get("outcome") == "done" else "amber",
                detail=r.get("detail") or "",
                run=r.get("run") or "",
                key=r.get("run") or f"{r.get('stage') or ''}-{i}",
            )
            for i, r in enumerate(data["runs"])
        ]

    def _load_artifact(self) -> None:
        self.artifact, self.artifact_file, self.artifact_missing = "", "", False
        if not (self.cwd and self.unit_id):
            return
        stage = self.current_unit.stage or (self.stages[0] if self.stages else "")
        if not stage:
            return
        try:
            data = SERVICE.artifact(self.cwd, self.unit_id, stage)
        except Invalid as e:
            self._fail(e)
            return
        self.artifact_file = data["file"]
        self.artifact_missing = not data["exists"]
        self.artifact = data["text"] or f"`{data['file']}` has not been written yet."

    # -- events --------------------------------------------------------------

    def _load_base(self) -> None:
        """The first half of what a page's first arrival reads: enough to pick a workspace."""
        try:
            self._load_settings()
            prefs = SERVICE.preferences()
            self.density = str(prefs.get("density") or "comfortable")
            self.board_view = str(prefs.get("board_view") or "Board")
            self.decision_preferences = str(prefs.get("decision_preferences") or "")
            self._load_workspaces()
        except Invalid as e:
            self._fail(e)

    async def _load_rest(self) -> None:
        await self._load_models()
        await self._load_board()
        # `0053` R9: no Sessions here. `arrive` reads them when Sessions is where it lands.
        self._load_activity()
        self._load_update()

    def _load_unit(self, forget: bool = True) -> None:
        """What opening a unit reads. Asked even of a unit the board does not list: the
        board may predate it, and whether it exists is the service's to say (R9)."""
        self.run_log = ""
        if forget:
            # `0071` R9: a message shown in the dialog is one made after it opened. Not on
            # a page's first arrival, whose messages are about the load itself.
            self.error, self.notice = "", ""
        self._load_timeline()
        self._load_unit_cost()
        self._load_artifact()

    # -- where the page is (`0056`) ------------------------------------------
    #
    # The address is the one source of `screen`, `cwd`, `unit_id` and `detail_tab`: a
    # button builds the address of where it goes and redirects there, and `arrive` — the
    # `on_load` of every route — is the only handler that sets the four (`spec.md` R12).

    def _address(self) -> tuple[str, str, str]:
        """The one place `router` is read: the path, the query, and the socket's id."""
        url = self.router.url
        return url.path, url.query, self.router.session.session_id

    def _name_of(self, cwd: str) -> str:
        return next((w.name for w in self.workspaces if w.id == cwd), "")

    @rx.event
    async def arrive(self):
        """`0056` R15, R16. Put the page where its address says, reading once.

        A page's first arrival — a load, a reload, an address typed into the bar — is told
        from a move inside the app by the socket's `session_id`: it is new on every such
        load and the same across `rx.redirect`, Back and Forward (`spike.md ## U4`). The
        token, and so this state, survives a reload, so an empty state cannot tell it.

        Reflex's `on_load_internal` supersedes: a newer navigation cancels whatever of the
        older one's chain is still running — this handler and what it chained. So what was
        read is recorded only once the read is done (`_loaded_sid`, `_read_cwd`,
        `_read_unit`, `_asked`), and an arrival cut short is read again by the next one.
        """
        path, query, sid = self._address()
        want = place.read(path, query)
        first = sid != self._loaded_sid
        if self.watch_run:
            # `0073`. The pane belongs to the page it was opened on; its loop stops.
            self._watch_reset("", "", "")
        if first:
            self.loading, self.error = True, ""
            # `0053` R9. A new page reads Sessions again when it gets there, as before.
            self._sessions_cwd = ""
            yield
            self._load_base()

        # Which workspace: the first one of that name (C6), else what `_load_workspaces`
        # left, which keeps the current one while it is still listed (R8).
        named = next((w.id for w in self.workspaces if want.ws and w.name == want.ws), "")
        cwd = named or self.cwd
        stray = bool(want.ws) and not named

        screen, unit, tab = want.screen, want.unit, want.tab
        if screen not in place.SCREENS and screen != "unit":
            screen = "overview"
        if screen == "unit" and not unit:
            screen = "board"  # R10
        if screen != "unit":
            unit, tab = "", "overview"
        elif tab not in place.TABS:
            tab = "overview"
        fixed = place.Place(screen, self._name_of(cwd), unit, tab)

        moved_ws = cwd != self._read_cwd
        moved_unit = unit != self._read_unit
        moved_screen = ("board" if screen == "unit" else screen) != self.screen
        self.cwd = cwd
        self.screen = "board" if screen == "unit" else screen
        if moved_ws or moved_unit or moved_screen:
            # Not on an arrival that moved nothing — the one a corrected address causes
            # may come after a person opened the menu (`verify_0071` at 390px).
            self.mobile_open = self.command_open = False
        if moved_ws and not first:
            self.session_id, self.query, self.error = "", "", ""
        # While a workspace's board is read, `cards` is still the last one's: a dialog open
        # over it would show that list's unit, or "not a unit", under this workspace's name.
        # So the unit opens once its own board is in (`0056` review round 1, F1).
        reading = first or moved_ws
        self.unit_id, self.detail_tab = ("", "overview") if reading else (unit, tab)
        self._set_current()
        if reading:
            # `_load_board` empties `cards` before its first await: a read cut short there
            # must leave nothing recorded as read, or going back finds no move and an empty
            # board (`0056` review round 2, F5).
            self._read_cwd = self._read_unit = ""

        # R16: one read, the one of the largest change.
        if first:
            yield
            await self._load_rest()
            self.loading = False
            self._loaded_sid = sid
        elif moved_ws:
            # The last read answered for the workspace just left; a unit of the same name
            # here must not show its session (`0051` review round 1, F1).
            try:
                self._running_read = SERVICE.running(cwd)
            except Invalid:
                self._running_read = {}
            yield
            await self._load_board()
            # `0053` R9. What Sessions showed was the last workspace's; it is read again
            # only if Sessions is where this arrival lands, just below.
            self.conversations, self.messages, self._history = [], [], []
            self._sessions_cwd = ""
            self._load_activity()
        elif (moved_unit and not unit) or (moved_screen and not moved_unit):
            self._load_update()
        if self.screen == "sessions" and self._sessions_cwd != cwd:
            self._load_sessions()
        if self.screen == "cost":
            # `0093` R12: every arrival here reads the run log once; no other screen does.
            self._load_cost()
        self._read_cwd = cwd
        self.unit_id, self.detail_tab = unit, tab
        self._set_current()
        # A unit is read after whatever the arrival read, which R16 does not list: a pasted
        # link or a reload at `/unit` would otherwise open a dialog with no timeline or
        # artifact (R4, R5). None of it calls `SERVICE.board` (R17).
        if unit and (moved_unit or moved_ws or first):
            self._load_unit(forget=not first)
            self._asked = ""
        self._read_unit = unit
        if stray:
            self.notice = "That workspace is not on the list."

        # R7, R8, R10: an address the page had to correct is replaced, not added to. Last,
        # and alone: the arrival it causes would cancel anything chained here, so that one
        # chains it instead, and finds nothing else to read.
        if place.href(fixed) != place.href(want):
            yield rx.redirect(place.href(fixed), replace=True)
            return
        if unit and self._asked != unit:
            self._ask_joins = True
            yield StudioState.load_next
        # A reload that finds the tab already on the Board: nothing else would start the loop.
        yield StudioState.poll_running

    @rx.event
    def navigate(self, screen: str):
        if screen not in SCREEN_TITLES:
            self.notice = "That screen does not exist."
            return
        self.mobile_open = False
        self.command_open = False
        return rx.redirect(place.href(place.Place(screen, self._name_of(self.cwd))))

    @rx.event
    def choose_workspace(self, path: str):
        if not any(w.id == path for w in self.workspaces):
            self.notice = "That workspace is not on the list."
            return
        # R13: a unit belongs to the workspace it was opened in, so leaving it goes to Board.
        screen = "board" if self.unit_id else self.screen
        return rx.redirect(place.href(place.Place(screen, self._name_of(path))))

    @rx.event
    def open_workspace(self, path: str):
        if not any(w.id == path for w in self.workspaces):
            self.notice = "That workspace is not on the list."
            return
        return rx.redirect(place.href(place.Place("board", self._name_of(path))))

    @rx.event(background=True)
    async def poll_running(self):
        """`0051` R3. Ask `Service.running` every `RUNNING_POLL` seconds while the Board shows.

        The one source for every card's `live`, whichever tab, route or process started
        the step (R8). One loop per tab: a second start while one lives returns at once.
        The loop ends when the tab leaves the Board, has no workspace, or has had no socket
        for `GONE_AFTER` asks in a row.
        """
        # The token the event came with: the one the socket server maps to this tab.
        # `router.session.client_token` is empty when no browser hydrated the state.
        token = EventContext.get().token
        if token in _POLLING:
            return
        _POLLING.add(token)
        missed = 0
        try:
            while True:
                missed = missed + 1 if _tab_gone(token) else 0
                if missed >= GONE_AFTER:
                    return
                async with self:
                    if self.screen != "board" or not self.cwd:
                        return
                    try:
                        read = SERVICE.running(self.cwd)
                    except Invalid:
                        read = {}
                    self._apply_running(read)
                    self._load_update()
                await asyncio.sleep(RUNNING_POLL)
        finally:
            _POLLING.discard(token)

    @rx.event
    def search_workspaces(self, value: str):
        self.workspace_query = value

    @rx.event
    def search_work(self, value: str):
        self.query = value

    @rx.event
    def filter_work(self, value: str | list[str]):
        # `rx.segmented_control` hands back a list when it is multi-select. This one is
        # not, and the prototype shipped a version that accepted a list and then indexed a string.
        if not isinstance(value, str) or value not in ("All work", "Autonomous", "Needs you"):
            self.notice = "Choose one of the work filters."
            return
        self.focus = value

    @rx.event
    def set_board_view(self, value: str | list[str]):
        if not isinstance(value, str) or value not in ("Board", "List"):
            self.notice = "Choose the board or the list."
            return
        self.board_view = value
        self._remember("board_view", value)

    @rx.event
    def edit_decision_preferences(self, value: str):
        self.decision_preferences = value

    @rx.event
    def save_decision_preferences(self):
        """`0044` R8a. Kept as typed; each paragraph becomes one `pref:<k>` Jera may cite."""
        self._remember("decision_preferences", self.decision_preferences)
        self.notice = "Decision preferences saved; Jera reads them on its next run."

    @rx.event
    def set_density(self, value: str):
        if value not in ("comfortable", "compact"):
            self.notice = "Choose comfortable or compact."
            return
        self.density = value
        self._remember("density", value)

    @rx.event
    def edit_model(self, name: str, value: str):
        """Typing into one row's box makes it the row being edited."""
        self.model_target = name
        self.model_text = value

    @rx.event
    async def save_model(self, name: str):
        """Set one row's model. Whether it may be set is `Service.set_stage_model`'s call."""
        if name != self.model_target or not self.model_text.strip():
            self.notice = "Type a model name in that row's box first."
            return
        await self._change_model(name, self.model_text)

    @rx.event
    async def reset_model(self, name: str):
        """Remove the override, so the row falls back to the default or `COS_MODEL`."""
        await self._change_model(name, None)

    async def _change_model(self, name: str, model: str | None) -> None:
        self.saving_model = True
        try:
            self._show_models(await SERVICE.set_stage_model(name, model))
        except Invalid as e:
            self.notice = str(e)
            return
        finally:
            self.saving_model = False
        self.model_target, self.model_text = "", ""
        self.notice = (
            f"{name} now runs on {model.strip()} from its next session."
            if model is not None
            else f"{name} is back on its default."
        )

    @rx.event
    def set_autopilot_on(self, value: bool):
        """`0043`. Whether it may be turned on is `Service.set_autopilot`'s call."""
        self._change_autopilot("autopilot", bool(value))

    @rx.event
    def set_autopilot_may_ship(self, value: bool):
        self._change_autopilot("autopilot_may_ship", bool(value))

    @rx.event
    def edit_ap_max_parallel(self, value: str):
        self.ap_max_parallel = value

    @rx.event
    def edit_ap_cap(self, value: str):
        self.ap_cap = value

    @rx.event
    def save_ap_max_parallel(self):
        self._change_autopilot("max_parallel", _number(self.ap_max_parallel, int))

    @rx.event
    def save_ap_cap(self):
        self._change_autopilot("daily_cap_usd", _number(self.ap_cap, float))

    def _change_autopilot(self, name: str, value) -> None:
        try:
            self._show_autopilot(SERVICE.set_autopilot(self.cwd, name, value))
        except Invalid as e:
            self.notice = str(e)
            self._load_autopilot()
            return
        self.notice = "Saved."

    @rx.event
    async def save_effort(self, name: str, effort: str):
        """`0033`. Set one row's effort. Whether it may be set is `Service.set_stage_effort`'s call."""
        await self._change_effort(name, effort)

    @rx.event
    async def reset_effort(self, name: str):
        """Remove the effort override, so the row falls back to its default or the SDK's."""
        await self._change_effort(name, None)

    async def _change_effort(self, name: str, effort: str | None) -> None:
        self.saving_model = True
        try:
            self._show_models(await SERVICE.set_stage_effort(name, effort))
        except Invalid as e:
            self.notice = str(e)
            return
        finally:
            self.saving_model = False
        self.notice = (
            f"{name} now runs at effort {effort} from its next session."
            if effort is not None
            else f"{name} effort is back on its default."
        )

    def _remember(self, key: str, value: str) -> None:
        try:
            SERVICE.set_preference(key, value)
        except Invalid as e:
            self._fail(e)

    @rx.event
    def dismiss_notice(self):
        self.notice, self.error = "", ""

    @rx.event
    def toggle_mobile(self, value: bool):
        self.mobile_open = value

    @rx.event
    def toggle_command(self, value: bool):
        self.command_open = value
        if value:
            self.command_query = ""

    @rx.event
    def search_commands(self, value: str):
        self.command_query = value

    # -- managing workspaces -------------------------------------------------

    @rx.event
    def edit_workspace(self, name: str):
        self.editing = name
        self.form_error = ""
        self.new_name = name
        self.new_url = ""
        self.new_label = next((w.label for w in self.workspaces if w.name == name), "")
        self.workspace_form = True

    @rx.event
    def toggle_workspace_form(self, value: bool):
        self.workspace_form = value
        if not value:
            self.form_error = ""

    @rx.event
    def set_new_name(self, value: str):
        self.new_name = value

    @rx.event
    def set_new_label(self, value: str):
        self.new_label = value

    @rx.event
    def set_new_url(self, value: str):
        self.new_url = value

    @rx.event
    async def save_workspace(self):
        """Adopt, clone, or relabel. Which one is `Service`'s decision, not this file's."""
        self.busy, self.form_error = True, ""
        yield
        try:
            if self.editing:
                SERVICE.set_label(self.editing, self.new_label)
            else:
                await SERVICE.add_workspace(
                    self.new_name, label=self.new_label, repo_url=self.new_url or None
                )
            self._load_workspaces()
            self.workspace_form = False
            self.new_name = self.new_url = self.new_label = ""
            self.editing = ""
        except Invalid as e:
            self.form_error = str(e)
        finally:
            self.busy = False
        yield
        await self._load_board()

    @rx.event
    def request_remove(self, name: str):
        self.remove_name = name

    @rx.event
    def toggle_remove(self, value: bool):
        if not value:
            self.remove_name = ""

    @rx.event
    async def remove_workspace(self):
        """De-lists only. The directory stays on disk — `spec.md` R18."""
        name, self.remove_name = self.remove_name, ""
        if not name:
            return
        try:
            SERVICE.remove_workspace(name)
            self._load_workspaces()
            self.notice = f"{name} is off the list. Its folder is untouched."
        except Invalid as e:
            self._fail(e)
        yield
        await self._load_board()

    @rx.event
    async def pull(self, name: str):
        self.busy, self.error = True, ""
        yield
        try:
            await SERVICE.pull_workspace(name)
            self.notice = f"{name} is up to date."
        except Invalid as e:
            # A failed fast-forward is normal and must be visible, not swallowed.
            self._fail(e)
        finally:
            self.busy = False

    # -- one unit ------------------------------------------------------------

    @rx.event
    def open_unit(self, unit: str):
        # `arrive` reads it (`_load_unit`); the dialog sits over the Board (R13, C9).
        return rx.redirect(place.href(place.Place("unit", self._name_of(self.cwd), unit)))

    @rx.event(background=True)
    async def load_next(self):
        """`0024`. Ask `cos.mjs next` which stage the run button may offer for the open unit.

        In the background because the answer can wait on `gh` for up to 60s (two calls,
        `board.GATE_TIMEOUT` each), and a handler holding the page's lock that long freezes
        every other control. Runs when a unit is opened, after a step ends, and when a
        person presses *Ask again* -- never on a timer: nothing here starts anything (R5).
        """
        async with self:
            unit, cwd = self.unit_id, self.cwd
            join, self._ask_joins = self._ask_joins, False
            self.run_stage = ""
            self.run_waiting = []
            self.run_said = "Asking cos.mjs what comes next…"
        if not (unit and cwd):
            async with self:
                self.run_said = ""
            return
        waiting: list[str] = []
        try:
            stage, said = _run_target(found := await _asking(SERVICE.next_step, cwd, unit, join))
            waiting = _run_waiting(found)
        except Invalid as e:
            stage, said = "", str(e)
        async with self:
            # A unit opened while this was asking is not the unit this answer is about.
            if self.unit_id == unit and self.cwd == cwd:
                self.run_stage, self.run_said = stage, said
                self.run_waiting = waiting
                self._asked = unit

    @rx.event
    def toggle_detail(self, value: bool):
        if not value:
            return rx.redirect(place.href(place.Place("board", self._name_of(self.cwd))))

    @rx.event
    def set_detail_tab(self, value: str):
        if value not in place.TABS:
            self.notice = "That tab does not exist."
            return
        # R7: replaced, so Back from a unit goes to where the unit was opened from.
        here = place.Place("unit", self._name_of(self.cwd), self.unit_id, value)
        return rx.redirect(place.href(here), replace=True)

    @rx.event
    def edit_answer(self, key: str, value: str):
        """Typing into one question's box makes it the one being answered."""
        if key != self.answer_target:
            self.answer_target = key
        self.answer_text = value

    @rx.event
    def toggle_details(self, key: str):
        """`0082`. Open or close one *Details*; nothing else reads which are open."""
        self.open_details = ([k for k in self.open_details if k != key]
                             if key in self.open_details else [*self.open_details, key])

    @rx.event

    @rx.event
    async def stop_step(self, unit: str):
        """`0034` R6. Stop one unit's running step. Every rule is `Service.stop_step`'s --
        a missing name, nothing running, a step already writing its artifact -- and its
        refusal is shown as its words. The streaming handler sees the `stopped` outcome."""
        self.error = ""
        try:
            done = await SERVICE.stop_step(self.cwd, unit, "")
            self.notice = f"Stopping {done['unit']} {done['stage']} (by {done['stopped_by']})."
        except Invalid as e:
            self._fail(e)
        finally:
            self._load_running()

    # -- `0073`: watching a step. Every read is `Service.events_page` or `.follow_events`; the
    # page only keeps the list under `WATCH_WINDOW`. Nothing here writes anything anywhere.

    def _watch_reset(self, run: str, title: str, unit: str) -> None:
        self._watch_token += 1
        self.watch_run, self.watch_title, self.watch_unit = run, title, unit
        self.watch_status, self.watch_note = "", ""
        self.watch_events, self.watch_has_older, self.watch_has_newer = [], False, False
        self.watch_following, self.watch_pending = False, 0
        self.watch_open_seq, self.watch_open_text = 0, ""

    def _watch_page(self, before: int | None = None) -> dict | None:
        try:
            return SERVICE.events_page(self.cwd, self.watch_unit, self.watch_run, before=before)
        except Invalid as e:
            self.watch_note = str(e)
            return None

    def _watch_take(self, fresh: list[WatchEvent]) -> None:
        """New events, by `plan.md` step 7's rule: at the bottom, appended and the oldest
        dropped past `WATCH_WINDOW`; reading older ones, appended while there is room and
        counted in `watch_pending` once there is none. An event already shown is dropped: a
        batch the follower yielded before *Jump to latest* read the last page again is also in that
        page (`review.md` F1)."""
        last = self.watch_events[-1].seq if self.watch_events else 0
        fresh = [e for e in fresh if e.seq > last]
        if not fresh:
            return
        if self.watch_following:
            kept = self.watch_events + fresh
            if len(kept) > WATCH_WINDOW:
                kept = kept[-WATCH_WINDOW:]
                self.watch_has_older = True
            self.watch_events = kept
            return
        # Rows past the last one shown were dropped: a new event appended would sit after a gap.
        room = 0 if self.watch_has_newer else WATCH_WINDOW - len(self.watch_events)
        if room > 0:
            self.watch_events = self.watch_events + fresh[:room]
        self.watch_pending += max(0, len(fresh) - max(room, 0))

    @rx.event
    def open_watch(self, run: str, title: str, unit: str = ""):
        """R10. Open the pane on one step's `run`. A timeline row with no `run` opens it on
        R13's line instead."""
        self._watch_reset(run, title or run, unit or self.unit_id)
        if not run:
            self.watch_run = "-"
            self.watch_note = NO_RUN_NOTE
            return
        return StudioState.watch_follow

    @rx.event
    def close_watch(self):
        self._watch_reset("", "", "")

    @rx.event
    def toggle_watch(self, value: bool):
        if not value:
            self._watch_reset("", "", "")

    @rx.event(background=True)
    async def watch_follow(self):
        """R10, R11. The last page, then every new event of a running step, gathered up to
        `WATCH_GATHER` seconds. Reading the page first and following from its last `seq` is
        safe: a running step's recorder holds every event, so nothing falls in between."""
        async with self:
            token, run = self._watch_token, self.watch_run
            page = self._watch_page()
            if page is None:
                return
            self.watch_events = _watch_events(page["events"])
            self.watch_has_older = bool(page["has_older"])
            self.watch_has_newer, self.watch_pending = False, 0
            self.watch_status = str(page["status"])
            self.watch_note = _watch_note(page)
            self.watch_following = True
            cwd, unit = self.cwd, self.watch_unit
            last = self.watch_events[-1].seq if self.watch_events else 0
        if page["status"] != "running":
            return
        try:
            async for kind, value in SERVICE.follow_events(cwd, unit, run, after=last, gather=WATCH_GATHER):
                async with self:
                    if self._watch_token != token:
                        return
                    if kind == "events":
                        self._watch_take(_watch_events(value))
                        if value and value[-1].get("kind") == "end":
                            self.watch_status = "ended"
                    elif kind == "cut":
                        # Fell `SUB_LIMIT` behind: start again from the last page.
                        return StudioState.watch_follow
                    else:
                        self.watch_status = str(value.get("status") or "")
                        self.watch_note = _watch_note(value)
        except Invalid as e:
            async with self:
                if self._watch_token == token:
                    self.watch_note = str(e)

    @rx.event
    def watch_older(self):
        """R7, R10. The page before the first event shown; the pane stops following. A full
        list drops its newest rows, and says so, for a step that has ended too
        (`review.md` F2)."""
        if not self.watch_events or not self.watch_has_older:
            return
        page = self._watch_page(before=self.watch_events[0].seq)
        if page is None:
            return
        older = _watch_events(page["events"])
        self.watch_following = False
        kept = older + self.watch_events
        if len(kept) > WATCH_WINDOW:
            kept = kept[:WATCH_WINDOW]
            self.watch_has_newer = True
        self.watch_events = kept
        self.watch_has_older = bool(page["has_older"])

    @rx.event
    def watch_live(self):
        """*Jump to latest*: the last page again, and following again."""
        page = self._watch_page()
        if page is None:
            return
        self.watch_events = _watch_events(page["events"])
        self.watch_has_older = bool(page["has_older"])
        self.watch_has_newer, self.watch_pending = False, 0
        self.watch_following = True

    @rx.event
    def watch_expand(self, seq: int):
        """R12 *Mở*: one event whole, as stored, in a var of its own."""
        try:
            page = SERVICE.events_page(self.cwd, self.watch_unit, self.watch_run, seq=int(seq))
        except Invalid as e:
            self.watch_note = str(e)
            return
        if page["events"]:
            self.watch_open_seq = int(seq)
            self.watch_open_text = events_mod.full_text(page["events"][0])

    @rx.event
    def watch_collapse(self):
        self.watch_open_seq, self.watch_open_text = 0, ""

    # -- `0068`: updating the app. Every rule is `Updater`'s, behind `Service`; a refusal
    # arrives here as its words.

    def _load_update(self) -> None:
        u = SERVICE.update_status()
        self.upd_version = str(u.get("version") or "")
        self.upd_commit = present.short_sha(u.get("commit"))
        self.upd_commit_full = str(u.get("commit_label") or u.get("commit") or "")
        self.upd_line = str(u.get("line") or "")
        self.upd_local_line = str(u.get("local_line") or "")
        self.upd_actions = [str(a) for a in u.get("actions") or []]
        self.upd_available = u.get("shape") == "service"
        self.upd_state = str(u.get("state") or "")
        pending = u.get("pending") or {}
        self.upd_waiting = [_job_line(j) for j in pending.get("waiting") or []]
        self.upd_pending_reason = str(pending.get("reason") or "")
        self.update_pending = self.upd_state == "pending"
        self.update_warning = str(u.get("warning") or "")
        release, local = u.get("release") or {}, u.get("local") or {}
        self.upd_release = _channel_line(release)
        self.upd_release_ready = release.get("state") == "ready"
        self.upd_local = _channel_line(local)
        self.upd_local_ready = local.get("state") == "ready"
        self.upd_local_configured = bool(local) and local.get("state") != "unconfigured"
        self.upd_local_tail = str(local.get("log_tail") or "")
        self.upd_checked_at = present.when(u.get("checked_at"))
        error = u.get("error") or {}
        self.upd_error = str(error.get("message") or "")
        self.upd_error_tail = str(error.get("log_tail") or "")
        last = u.get("last") or {}
        self.upd_last = (
            f"{last.get('result')}: {last.get('from')} → {last.get('to')}"
            if last else ""
        )
        self.upd_last_tail = str(last.get("log_tail") or "")

    @rx.event
    async def apply_update(self, channel: str):
        """R7: apply, or wait for what is running (R9)."""
        try:
            await SERVICE.update_apply(channel, "wait", "", "")
        except Invalid as e:
            self._fail(e)
        self._load_update()

    @rx.event
    def show_cut_list(self, channel: str):
        """R10: what "apply now" would cut, shown before anything is cut."""
        try:
            listing = SERVICE.update_cut_list()
        except Invalid as e:
            self._fail(e)
            return
        self._show_cut(channel, listing)

    def _show_cut(self, channel: str, listing: dict) -> None:
        self.cut_channel = channel
        self.cut_items = [f"{i['action']}: {_job_line(i)}"
                          for i in listing.get("items") or []]
        self.cut_token = str(listing.get("token") or "")
        self.cut_open = True

    @rx.event
    def close_cut_list(self):
        self.cut_open = False

    @rx.event
    async def confirm_apply_now(self):
        try:
            await SERVICE.update_apply(self.cut_channel, "now", "", self.cut_token)
            self.cut_open = False
        except StaleCutList as e:
            # The list changed since it was shown: show the new one, cut nothing.
            self.notice = str(e)
            self._show_cut(self.cut_channel, e.listing)
        except Invalid as e:
            self._fail(e)
        self._load_update()

    @rx.event
    def cancel_update(self):
        try:
            SERVICE.update_cancel("")
        except Invalid as e:
            self._fail(e)
        self._load_update()

    @rx.event
    def build_local(self):
        try:
            SERVICE.update_build_local("")
        except Invalid as e:
            self._fail(e)
        self._load_update()

    @rx.event
    async def answer_question(self, key: str):
        """`0016` R2. Send one answer. Every rule about whether it may be written is
        `Service.answer`'s; a refusal arrives here as its words and is shown as they are.

        `0071` R1: every press ends in a block written or a reason shown, never in nothing.
        The `yield` after raising `answering_key` is what sends it to the browser, as in
        `post_review_comment`. There is no guard against a second press: Reflex queues it
        behind the first, by then the box is empty, and the first branch gives it a reason.
        That branch leaves `notice` alone: the press it follows may be the one that wrote the
        block, and its "Answered …" must survive the second press (review round 1, F1).
        """
        self.error = ""
        if key != self.answer_target or not self.answer_text.strip():
            self.error = f"Nothing was sent: you pressed Send on {_key_label(key)}, but its box is empty."
            if self.answer_target and self.answer_text.strip():
                self.error += (
                    f" Your text is in the box of {_key_label(self.answer_target)}, and it is "
                    "still there."
                )
            return
        artifact, _, number = key.rpartition("#")
        self.notice = ""
        self.answering_key = key
        yield
        try:
            done = await SERVICE.answer(
                self.cwd, self.unit_id, artifact, number, self.answer_text, ""
            )
        except Invalid as e:
            self._fail(e)
            return
        except Exception as e:  # noqa: BLE001 - R5: an unexpected failure is shown, not lost
            # Not "nothing was written": `Service.answer` writes the block before it touches
            # the history, so a failure after that leaves it on disk (`spec.md` R5).
            self.error = (
                f"{type(e).__name__}: {e}. It is not known whether the answer was written; "
                f"open {artifact} to check."
            )
            return
        finally:
            self.answering_key = ""
        self.answer_target, self.answer_text = "", ""
        kind = "finding" if str(done["question"]).startswith("F") else "question"
        self.notice = (
            f"Answered {kind} {done['question']} of {done['artifact']} as "
            f"{done['answered_by']}. Nothing was started; the next step reads it when it runs."
        )
        try:
            await self._load_board()
            self._load_artifact()
        except Exception as e:  # noqa: BLE001 - R5: the answer is written; say so regardless
            self.notice += f" The board could not be read again: {type(e).__name__}: {e}"

    @rx.event
    async def ask_jera(self):
        """`0044`. Ask Jera to answer the open unit's questions from precedent. Whether it may,
        and what is written, is `Service.precedent`'s; this locks the button and says what
        happened. The `yield` sends `asking_jera` to the browser, as `integrate` does."""
        if self.asking_jera:
            return
        self.error, self.notice = "", ""
        self.asking_jera = True
        yield
        try:
            done = await SERVICE.precedent(self.cwd, self.unit_id)
        except Invalid as e:
            self._fail(e)
            return
        finally:
            self.asking_jera = False
        if done.get("outcome") == "failed":
            self.error = f"Jera's reply could not be read, so nothing was written: {done.get('detail')}"
        else:
            self.notice = (
                f"Jera answered {len(done['written'])}; {len(done['needs_person'])} need a person"
                + (f"; {len(done['skipped'])} were answered meanwhile." if done["skipped"] else ".")
            )
        try:
            await self._load_board()
            self._load_artifact()
        except Exception as e:  # noqa: BLE001 - what Jera wrote is written; say so regardless
            self.notice += f" The board could not be read again: {type(e).__name__}: {e}"

    @rx.event
    def set_outcome_result(self, value: str):
        self.outcome_result = value

    @rx.event
    def set_outcome_measured_by(self, value: str):
        self.outcome_measured_by = value

    @rx.event
    def set_outcome_source(self, value: str):
        self.outcome_source = value

    @rx.event
    def set_outcome_reason(self, value: str):
        self.outcome_reason = value

    @rx.event
    def set_outcome_note(self, value: str):
        self.outcome_note = value

    @rx.event
    async def record_outcome(self):
        """`0047`. Record the open unit's outcome. Every rule about whether it may be written
        is `Service.record_outcome`'s; a refusal arrives here as its words, shown as they are.
        A label missing from the tables goes as it is, for the service to refuse."""
        result = {v: k for k, v in present.RESULT_LABEL.items()}.get(self.outcome_result, self.outcome_result)
        measurer = {v: k for k, v in present.MEASURER_LABEL.items()}.get(
            self.outcome_measured_by, self.outcome_measured_by
        )
        self.recording_outcome = True
        try:
            done = await SERVICE.record_outcome(
                self.cwd, self.unit_id, result, measurer,
                self.outcome_source, self.outcome_reason, self.outcome_note, "",
            )
        except Invalid as e:
            self.notice = str(e)
            return
        finally:
            self.recording_outcome = False
        self.outcome_source, self.outcome_reason, self.outcome_note = "", "", ""
        self.notice = (
            f"Recorded {present.RESULT_LABEL.get(done['result'], done['result'])} for {done['unit']}, "
            f"measured by {present.MEASURER_LABEL.get(done['measured_by'], done['measured_by'])}."
        )
        await self._load_board()
        self._load_artifact()

    @rx.event
    async def post_review_comment(self, number: int):
        """`0021` R8. Post one review round to the pull request. Whether it may, and whether
        it is already there, is `Service.post_review_comment`'s decision.

        The `yield` after raising `posting_round` is what sends it to the browser: without
        it the flag is set and cleared inside one delta, and the button never locks for the
        up to 60s `gh` may take (`0021` review, F1)."""
        if self.posting_round != 0:
            return
        self.posting_round = int(number)
        yield
        try:
            done = await SERVICE.post_review_comment(self.cwd, self.unit_id, number)
        except Invalid as e:
            self.notice = str(e)
            return
        finally:
            self.posting_round = 0
        if done["state"] == "failed":
            self.notice = f"Round {done['round']} is not on the PR: {done['reason']}"
        elif done["state"] == "already":
            self.notice = f"Round {done['round']} was already on the PR. Nothing was posted."
        else:
            self.notice = (
                f"Posted round {done['round']} to the PR as a comment. It is not an approval "
                "and no gate reads it."
            )
        await self._load_board()

    @rx.event
    async def integrate(self):
        """`0035`. Integrate the open unit. Whether it may, and which road, is
        `Service.integrate`'s decision; this only locks the button and says what happened.
        The `yield` sends `integrating` to the browser, as `post_review_comment` does."""
        if self.integrating:
            return
        self.integrating = True
        yield
        done: dict = {}
        try:
            async for kind, payload in SERVICE.integrate(self.cwd, self.unit_id):
                if kind == "done":
                    done = payload.get("integration") or {}
        except Invalid as e:
            self.notice = f"Not integrated: {e}"
            return
        finally:
            self.integrating = False
        outcome = done.get("outcome", "")
        who = "the app" if done.get("mode") == "mechanical" else "an agent (Gebo)"
        if outcome == "pushed":
            self.notice = (
                f"Integrated by {who}: the pull request is now at {str(done.get('head_after'))[:7]}. "
                "The ship gate is closed until a new review round reviews that head."
            )
        elif outcome == "needs-person":
            self.notice = "Gebo stopped: a person is needed. The contradictions are listed on the unit."
        else:
            self.notice = f"Integration {outcome or 'ended'}: {done.get('detail') or 'see Activity'}"
        await self._load_board()

    @rx.event
    def set_hold_reason(self, value: str):
        self.hold_reason = value

    @rx.event

    @rx.event
    async def set_hold(self, to: str):
        """`0045`. Pause, drop or resume the open unit. Whether the move exists and whether
        the words will do is `Service.hold`'s decision; a refusal is shown as it is. Reads
        the board again afterwards and starts nothing (R16) — not `run_step`, not `load_next`'s
        stage: the button still waits for a person to press it."""
        if self.holding:
            return
        self.holding = True
        yield
        try:
            done = await SERVICE.hold(self.cwd, self.unit_id, to, self.hold_reason, "")
        except Invalid as e:
            self.notice = f"Not changed: {e}"
            return
        finally:
            self.holding = False
        self.hold_reason = ""
        effects = "; ".join(
            f"{e['effect']}: {e['result']}" + (f" ({e['detail']})" if e["result"] != "done" else "")
            for e in done["effects"]
        )
        self.notice = (
            f"{done['unit']}: {done['from']} → {done['to']}, by {done['by']}."
            + (f" {effects}." if effects else "")
            + " Nothing was started."
        )
        await self._load_board()
        self._load_activity()
        yield StudioState.load_next

    # -- backlog (`0074`). Every handler calls `SERVICE` and copies; none decides (R12, R15).

    @rx.event
    def set_backlog_field(self, name: str, value: str):
        if name in ("shortlist_reason", "est_value", "est_effort", "est_basis", "rel_other", "rel_type",
                    "rel_op", "rel_reason"):
            setattr(self, name, value)

    @rx.event
    def edit_backlog_row(self, unit: str):
        """`0082` R10. Open one row's estimate and relation forms, or close the open one."""
        if self.backlog_editing == unit:
            self.backlog_editing = ""
            return
        self.backlog_editing, self.est_unit, self.rel_unit = unit, unit, unit
        self.show_backlog_history(unit)

    @rx.event
    def shortlist_add(self, unit: str):
        if unit not in self.shortlist_draft:
            self.shortlist_draft = [*self.shortlist_draft, unit]

    @rx.event
    def shortlist_remove(self, unit: str):
        self.shortlist_draft = [u for u in self.shortlist_draft if u != unit]

    @rx.event
    def shortlist_move(self, unit: str, step: int):
        """Move one unit of the draft up (-1) or down (1). The page decides nothing else."""
        draft = list(self.shortlist_draft)
        if unit not in draft:
            return
        i = draft.index(unit)
        j = min(max(i + int(step), 0), len(draft) - 1)
        draft[i], draft[j] = draft[j], draft[i]
        self.shortlist_draft = draft

    @rx.event
    def fill_shortlist(self):
        """R12. The first seven of the computed order, into the draft. A person still saves it."""
        self.shortlist_draft = list(self.backlog_suggested)

    @rx.event
    def show_backlog_history(self, unit: str):
        self.history_unit = unit
        self.history_lines = [
            (f"{present.when(h.get('at'))} · {h.get('by')} · value {h.get('value')}, effort {h.get('effort')} — {h.get('basis')}"
             if h.get("kind") == "estimate" else
             f"{present.when(h.get('at'))} · {h.get('by')} · {h.get('op')} {h.get('unit')} {present.RELATION_LABEL.get(h.get('type'), h.get('type'))} {h.get('other')} — {h.get('reason')}")
            for h in self._backlog_history.get(unit) or []
        ]

    async def _backlog_write(self, call) -> None:
        try:
            await call
        except Invalid as e:
            self.notice = f"Not recorded: {e}"
            return
        self.notice = "Recorded in the run log. No gate reads it and nothing was started."
        await self._load_board()

    @rx.event
    async def save_shortlist(self):
        await self._backlog_write(SERVICE.record_shortlist(self.cwd, list(self.shortlist_draft), self.shortlist_reason, ""))

    @rx.event
    async def save_estimate(self):
        await self._backlog_write(SERVICE.record_estimate(
            self.cwd, self.est_unit.strip(), self.est_value.strip(), self.est_effort.strip(), self.est_basis,
            "",
        ))

    @rx.event
    async def save_relation(self):
        await self._backlog_write(SERVICE.record_relation(
            self.cwd, self.rel_unit.strip(), self.rel_other.strip(), self.rel_type, self.rel_op, self.rel_reason,
            "",
        ))

    @rx.event
    async def propose_estimates(self):
        """R17. Opens one paid session; the warning above the button says so (R19)."""
        if self.proposing:
            return
        self.proposing = True
        yield
        done: dict = {}
        try:
            async for kind, payload in SERVICE.propose_estimates(self.cwd):
                if kind == "done":
                    done = payload.get("estimate") or {}
        except Invalid as e:
            self.notice = f"Not started: {e}"
            return
        finally:
            self.proposing = False
        self.notice = (
            f"Proposal {done.get('outcome')}: {done.get('written', 0)} recorded, "
            f"{len(done.get('rejected') or [])} refused" + (f" — {done['detail']}" if done.get("detail") else "")
        )
        await self._load_board()

    @rx.event
    def set_new_slug(self, value: str):
        self.new_slug = value

    @rx.event
    def set_new_brief(self, value: str):
        self.new_brief = value

    @rx.event
    async def create_unit(self):
        """`0014` R8. Start a work unit from the page.

        The slug grammar and the number are not decided here and not decided in
        `service.py` either — they come back from `cos.mjs`, and a bad slug arrives as its
        refusal, word for word. A handler that decided anything would be a bug in
        `service.py` (`.cos/0001_.../spec.md` R10).
        """
        slug = self.new_slug.strip()
        if not slug:
            self.notice = "Give the work a short name, like `board-cannot-say-what-happened`."
            return
        if not self.new_brief.strip():
            # Not a validation rule of the loop — a rule of this page. The brief becomes
            # `idea.md`, which is the only thing the intent step will have to work from,
            # and a unit started without one wastes a paid step on an empty prompt.
            self.notice = "Say what the problem is, in your own words. The intent step reads it."
            return
        self.starting = True
        try:
            made = await SERVICE.create_unit(self.cwd, slug, self.new_brief)
        except Invalid as e:
            self.notice = str(e)
            return
        finally:
            self.starting = False
        self.new_slug, self.new_brief = "", ""
        self.notice = f"Started {made['unit']}."
        await self._load_board()

    @rx.event
    async def start_branch(self):
        """`0014` R8. Cut the open unit's branch in the workspace.

        The one control on this page that writes to somebody else's git.
        `coscc/gitops.py` carries the list of what that may be.
        """
        if not self.unit_id:
            return
        try:
            cut = await SERVICE.start_branch(self.cwd, self.unit_id)
        except Invalid as e:
            self.notice = str(e)
            return
        # R3 of `0001_product-describes-a-state-it-is-not-in`: say where it was cut from,
        # from the fetched ref rather than the local `main`. Since `0017` it is cut in the
        # unit's own worktree, so the workspace's branch does not change and is not reset.
        said = f"{cut['branch']} cut from {cut['base']} at {cut['sha']}, in {cut['worktree']}."
        if cut.get("switched"):
            said += " The workspace was moved back to main to open it."
        prepared = cut.get("prepare") or {}
        if not prepared.get("ok", True):
            said += (f" Preparing it failed: `{prepared.get('command')}` exited "
                     f"{prepared.get('exit_code')} — impl will not run until it succeeds.")
        self.notice = said
        self._trees = {**self._trees, self.unit_id: tree_line(
            {"path": cut["worktree"], "branch": cut["branch"], "prepare": prepared})}

    async def set_mode(self, value: str | list[str]):
        if not isinstance(value, str) or value not in ("manual", "autonomous"):
            self.notice = "Choose manual or autonomous."
            return
        stage = self.next_stage
        if not (self.unit_id and stage):
            self.notice = "There is no next step to set a mode on."
            return
        try:
            await SERVICE.set_mode(self.cwd, self.unit_id, stage, value)
        except Invalid as e:
            self._fail(e)
            return
        await self._load_board()
        self._load_activity()

    @rx.event(background=True)
    async def run_step(self):
        """Run the unit's next step, streaming what comes back.

        `background=True` is the difference between a board and a frozen page. A generator
        event handler holds the state lock for its whole life, so a step that takes minutes
        would lock every other control. A background handler takes the lock in short
        bursts, which is why every write below sits inside `async with self`.
        """
        async with self:
            # `0034`. No "already running" check of the page's own: whether this unit may
            # start a step is the service's to refuse, and its `Invalid` lands in `_fail`.
            unit, stage, cwd = self.unit_id, self.next_stage, self.cwd
            if not (unit and stage and cwd):
                self.notice = "There is no next step to run."
                return
            self.run_log = ""
            self.log_unit = unit
            self.error = ""

        listed = False
        try:
            async for kind, payload in SERVICE.run_step(cwd, unit, stage):
                async with self:
                    if not listed:
                        # The step is in the service's list from its first item on.
                        listed = True
                        self._load_running()
                    if kind == "chunk" and self.log_unit == unit:
                        self.run_log += payload
                    elif kind == "done" and isinstance(payload, dict):
                        if payload.get("error"):
                            self.error = payload["error"]
                        outcome = payload.get("outcome") or ""
                        written = payload.get("artifact") or ""
                        self.notice = (
                            f"{stage} {outcome}" + (f" — wrote {written}" if written else "")
                        )
                        # `0030_a-unit-branch-starts-from-a-stale-main`. `describe_base`
                        # is the one sentence saying a step's base may be stale; this page
                        # only appends the string the service already worked out, never
                        # its own reading of `payload["base"]`.
                        stale = describe_base(payload.get("base"))
                        if stale:
                            self.notice += " " + stale
        except Invalid as e:
            async with self:
                self._fail(e)
        finally:
            # Re-read rather than patch. The artifact on disk is the truth about a stage's
            # status, and this is the moment it changed.
            async with self:
                await self._load_board()
                self._load_timeline()
                self._load_artifact()
                self._load_activity()
        # `0024`. The stage that ran is behind the unit now; ask again what is next. This
        # names a stage and runs nothing — a person still presses the button (R5).
        return StudioState.load_next

    # -- sessions ------------------------------------------------------------

    @rx.event
    def choose_session(self, session_id: str):
        if not any(c.id == session_id for c in self.conversations):
            self.notice = "That conversation is not in this workspace."
            return
        self.session_id = session_id
        self.prompt = ""
        self._load_history()

    @rx.event
    def new_session(self):
        """A session exists once something has been said in it, so this only clears the view.

        The SDK owns session identity. Inventing one here would put an id on the page that
        nothing on disk has ever heard of.
        """
        if not self.cwd:
            self.notice = "Choose a workspace first."
            return
        self.session_id = ""
        self.messages, self._history = [], []
        self.prompt = ""
        self.notice = "New conversation. It is saved once you send the first message."

    @rx.event
    def open_message(self, index: int):
        """`0053` R10. Show one cut message whole, from the copy `_load_history` kept.

        Messages `send` adds are never cut and come after every message of `_history`, so
        an index into `messages` below its length is the same message there."""
        full = self.get_value("_history")
        shown = list(self.get_value("messages"))
        if not (0 <= index < len(full) and index < len(shown)):
            return
        shown[index] = Message(role=full[index].role, text=full[index].text, cut=0)
        self.messages = shown

    @rx.event
    def set_prompt(self, value: str):
        self.prompt = value

    @rx.event
    async def send(self):
        text, self.prompt = self.prompt.strip(), ""
        if not text:
            self.notice = "Write a message before sending."
            return
        self.sending, self.error = True, ""
        self.messages = [*self.messages, Message(role="user", text=text),
                         Message(role="assistant", text="")]
        yield
        try:
            SERVICE.check_send(self.cwd, text)
            async for kind, payload in SERVICE.stream(
                self.cwd, text, self.session_id or None
            ):
                if kind == "chunk":
                    self.messages[-1].text += payload
                    self.messages = list(self.messages)
                    yield
                elif kind == "done" and isinstance(payload, dict):
                    # The SDK resolves the id. Taking it from here rather than inventing
                    # one is what keeps the page's idea of a session and the store's idea
                    # of it the same thing.
                    self.session_id = payload.get("session_id") or self.session_id
        except Exception as e:
            self._fail(e)
        finally:
            self.sending = False
        yield
        shown = list(self.get_value("messages"))
        self._load_sessions()
        self._keep_whole(shown)
        self._load_activity()
