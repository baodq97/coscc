"""What the page shows, before it is state: the rows and cards `StudioState` holds, and the
pure functions that build them from what `Service` returns.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from datetime import datetime

from coscc.agent.models import ConfigRow
from coscc.runlog import events as events_mod
from coscc.state import place, present
from coscc.runlog import spend
from coscc.runlog.journal import COST_USD, TOKEN_FIELDS
from coscc.service.agents import AgentPage, RunView
from coscc.service.common import reason_beside
from coscc.service.common import shown_state


NAVIGATION = (
    ("overview", "Overview", "house"),
    ("workspaces", "Workspaces", "layers"),
    ("board", "Board", "columns-3"),
    ("backlog", "Backlog", "list-ordered"),
    ("sessions", "Sessions", "messages-square"),
    ("activity", "Activity", "chart-no-axes-combined"),
    ("cost", "Cost", "circle-dollar-sign"),
    ("agents", "Agents", "users"),
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
    # A reviewer asked for changes: waiting on someone like a draft, but a verdict, so another colour.
    "changes-requested": "orange",
    "not started": "gray",
}


def _cell_label(row: dict) -> tuple[str, str]:
    """The chip text and colour for one stage row, an entry of `Service.board`'s `stages`.

    Only the "no artifact, last run failed" case departs from the ordinary
    `status`/`STATUS_COLOR` pair, so a stage with an artifact never shows a stale failure.
    """
    status = row.get("status") or ""
    last_run = row.get("last_run")
    if status == "not started" and last_run and last_run.get("outcome") != "done":
        turns, cost = last_run.get("turns"), last_run.get("cost_usd")
        outcome = last_run.get("outcome")
        # Turns and cost are each known or not, and each is said as it is.
        if turns is None and cost is None:
            return f"not started · {outcome} · turns and cost unknown", "amber"
        said_turns = "turns unknown" if turns is None else f"{turns} turns"
        said_cost = "cost unknown" if cost is None else f"${cost:.2f}"
        return f"not started · {outcome} · {said_turns} · {said_cost}", "amber"
    return status, STATUS_COLOR.get(status, "gray")


# Colours for the workspace marks, assigned by position so the same workspace keeps the
# same colour between loads. Nothing is stored; the list is the only state.
MARK_COLORS = ("iris", "grass", "blue", "amber", "plum", "cyan")


def tree_line(tree: dict | None) -> str:
    """A unit's worktree and its preparation, as the page says it.

    A failure names the command and its exit code so the person can run it themselves.
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
        line += (
            f" · preparing failed: `{prepared.get('command')}` exited {prepared.get('exit_code')}"
        )
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
    # What the chip shows. Equal to `status` except when the artifact is absent and the
    # last run of this stage failed; then it names the failure. `status` keeps meaning only
    # what the artifact says.
    label: str = ""
    mode: str = "manual"
    color: str = "gray"
    started: bool = False
    grants: str = ""
    warning: str = ""
    opens_tools: bool = False
    # The one sentence the page keeps beside Run.
    consequence: str = ""
    # `idea` is the one optional stage and it gates nothing; carried so the run button can skip it.
    optional: bool = False


@dataclasses.dataclass
class Question:
    """One numbered item under an artifact's `## Open questions`, as the loop read it;
    every field is copied from `status --json`."""

    # `<artifact>#<n>`: one string the page can bind a text box to.
    key: str = ""
    artifact: str = ""
    number: int = 0
    text: str = ""
    answered: bool = False
    # Whether this is the artifact the unit's open count is taken from.
    counted: bool = False
    # What the row shows as its name: the number for a numbered question, `F<n>` for a
    # review finding the last round confirmed needs a person (`number` is 0 for those).
    label: str = ""


@dataclasses.dataclass
class Round:
    """One round of `review.md` and whether it is on the pull request as a comment. The
    round is the loop's; whether it is posted is `Service.board`'s reading of the run log."""

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
    # The last stage with an artifact: the one the Artifact tab opens. Not the column, which is `at`.
    stage: str = ""
    mode: str = "manual"
    tokens: str = ""
    usd: str = ""
    token_count: int = 0
    progress: int = 0
    # Whether a stage past the idea has an artifact.
    begun: bool = False
    problems: str = ""
    cells: list[Cell] = dataclasses.field(default_factory=list)
    # How many questions in the counted artifact nobody has answered, taken from the loop
    # (`open`) and never recounted. An open question makes the unit's state *Needs you*.
    open_questions: int = 0
    questions: list[Question] = dataclasses.field(default_factory=list)
    # The pull request `pr.md` names, and every review round with its comment state.
    pr_url: str = ""
    rounds: list[Round] = dataclasses.field(default_factory=list)
    # Copied from `Service.board`'s `integration`; empty state means the unit is outside
    # the window. `integrate_button` is the service's decision.
    integration_state: str = ""
    integration_reason: str = ""
    integration_behind: str = ""
    integration_origin: str = ""
    integrate_button: bool = False
    integration_warnings: list[str] = dataclasses.field(default_factory=list)
    integration_needs_person: list[str] = dataclasses.field(default_factory=list)
    # Copied from `Service.board`'s `outcome_label`; empty text means no label.
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
    # What is running on this unit now, or ended unseen: one line each, copied from
    # `Board.running` by `_activities`.
    live: list[Activity] = dataclasses.field(default_factory=list)
    # The hold the loop read (`paused`, `dropped`, or empty) and the moves it allows from
    # there, copied from the board. The page offers one button per move and decides none.
    hold_state: str = ""
    hold_reason: str = ""
    hold_by: str = ""
    hold_date: str = ""
    hold_moves: list[str] = dataclasses.field(default_factory=list)
    # Whether the loop says the unit used its review rounds with findings still open. The
    # page offers *Allow one more review round* off this alone.
    more_rounds: bool = False
    # The unit's place in the shortlist in effect, 0 when it has none, and its relations
    # in one line. Labels only.
    shortlist_rank: int = 0
    relations_text: str = ""
    # The service's decisions: whether the board invites an answer, and what a unit in
    # *Needs you* waits on.
    answerable: bool = True
    attention_reason: str = ""
    # The stage whose column the unit sits in, as the loop sent it, and the state shown:
    # `Service.board`'s decision (`decided_*`), with `Running` laid over it by
    # `service.shown_state` alone. `ci_line` is the dialog's CI line, and `state_reason`
    # the `attention_reason` its header shows beside the state.
    at: str = ""
    state: str = ""
    state_label: str = ""
    state_color: str = "gray"
    ci_line: str = ""
    state_reason: str = ""
    decided_state: str = ""
    decided_label: str = ""
    decided_color: str = "gray"
    # The idea the unit was opened from, its `Repo:`, and the unit it waits on, each with
    # the address it links to; empty when it has none. `link_fields` copies them.
    idea_ref: str = ""
    idea_href: str = ""
    repo: str = ""
    waits_for: str = ""
    waits_for_href: str = ""
    # The code the last autopilot pass held the unit back with (`overlap-pr #7`).
    held: str = ""


@dataclasses.dataclass
class IdeaRow:
    """One idea on the Board of its home workspace."""

    id: str = ""
    title: str = ""
    units: int = 0
    href: str = ""


@dataclasses.dataclass
class ChildRow:
    """One unit an idea lists, as the `/idea` page shows it."""

    ref: str = ""
    unit: str = ""
    repo: str = ""
    stage: str = ""
    state: str = ""
    waits_for: str = ""
    href: str = ""
    missing: bool = False


def _unit_href(ref: str, home: str) -> str:
    """The `/unit` address of `<ws>/NNNN_<slug>`, or of `NNNN_<slug>` in `home`."""
    ws, _, unit = ref.rpartition("/")
    return place.href(place.Place("unit", ws or home, unit))


def _idea_href(ref: str, home: str) -> str:
    """The `/idea` address of `<ws>/ideas/NNNN_<slug>.md`, or of `ideas/…` in `home`."""
    ws, sep, file = ref.rpartition("/ideas/")
    if not sep:
        ws, file = "", ref.removeprefix("ideas/")
    return place.href(place.Place("idea", ws or home, idea=file.removesuffix(".md")))


def link_fields(u: dict, home: str) -> dict:
    """A board unit's links, as `Unit` fields. Copies what `Service.board` sent."""
    idea = str(u.get("idea") or "")
    waits = [str(x) for x in u.get("waits_for") or []]
    return {
        "idea_ref": idea,
        "idea_href": _idea_href(idea, home) if idea else "",
        "repo": str(u.get("repo") or ""),
        "waits_for": ", ".join(waits),
        "waits_for_href": _unit_href(waits[0], home) if waits else "",
    }


def idea_rows(data: dict, home: str) -> list[IdeaRow]:
    """The ideas `status --json` read in this workspace's store, one row each."""
    return [
        IdeaRow(
            id=str(i.get("id") or ""),
            title=str(i.get("title") or i.get("id") or ""),
            units=len(i.get("units") or []),
            href=place.href(place.Place("idea", home, idea=str(i.get("id") or ""))),
        )
        for i in data.get("ideas") or []
    ]


def child_rows(page: dict) -> list[ChildRow]:
    """`Ideas.idea`'s rows, as the page draws them. Copies; decides nothing."""
    return [
        ChildRow(
            ref=str(r["ref"]),
            unit=str(r["unit"]),
            repo=str(r["repo"]),
            stage=str(r.get("stage") or ""),
            state=str(r.get("state") or ""),
            waits_for=", ".join(r.get("waits_for") or []),
            href="" if r.get("missing") else _unit_href(str(r["ref"]), ""),
            missing=bool(r.get("missing")),
        )
        for r in page.get("units") or []
    ]


@dataclasses.dataclass
class Card:
    """One card: only what a card, a List row, *Pick up where you left off*, a Usage row
    and the command palette draw. The page receives this list once; the whole `Unit` of the
    one unit open is `current_unit`, and every other `Unit` stays on the server (`_full`)."""

    id: str = ""
    title: str = ""
    summary: str = ""
    mode: str = "manual"
    progress: int = 0
    # Some stage after the idea has an artifact: *Pick up where you left off* skips a ready
    # unit without one, and its card is drawn quieter.
    begun: bool = False
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
    at: str = ""
    state: str = ""
    state_label: str = ""
    state_color: str = "gray"
    # The unit `impl` waits on, when it waits.
    waits_for: str = ""
    held: str = ""
    # Where the card stands in its lane and in the List (`board_place`).
    place: int = 0


# A card's place among its lane's and the List's: what waits on a person first, what has not
# begun last before the folded groups.
PLACE = {
    "needs-you": 0,
    "error": 1,
    "running": 2,
    "starting": 2,
    "awaiting": 3,
    "paused": 4,
    "ready": 5,
}


def board_place(state: str, begun: bool) -> int:
    if state == "ready" and not begun:
        return 6
    return PLACE.get(state, 7)


def _card(u: Unit) -> Card:
    """A `Unit` as its card. Copies, and places it (`board_place`)."""
    return Card(
        id=u.id,
        title=u.title,
        summary=u.summary,
        mode=u.mode,
        progress=u.progress,
        begun=u.begun,
        tokens=u.tokens,
        usd=u.usd,
        token_count=u.token_count,
        has_problem=u.problems != "",
        open_questions=u.open_questions,
        integration_state=u.integration_state,
        integrate_button=u.integrate_button,
        outcome_text=u.outcome_text,
        outcome_color=u.outcome_color,
        hold_state=u.hold_state,
        shortlist_rank=u.shortlist_rank,
        relations_text=u.relations_text,
        live=list(u.live),
        answerable=u.answerable,
        attention_reason=u.attention_reason,
        at=u.at,
        state=u.state,
        state_label=u.state_label,
        state_color=u.state_color,
        waits_for=u.waits_for,
        held=u.held,
        place=board_place(u.state, u.begun),
    )


def _ci_line(ci: dict | None) -> str:
    """The service's CI answer as one line, `""` where it gives none. The time is for a
    reader; no SHA."""
    if not ci:
        return ""
    read = " · read " + present.when(ci.get("at")) if ci.get("read") else ""
    if ci.get("red"):
        return "CI is red: " + ", ".join(ci["red"]) + read
    if ci.get("read"):
        return "CI is not red" + read
    return "CI has not been read"


def _shown(u: Unit, read: dict) -> dict:
    """The `state*` fields of `u` under one `Board.running` answer: the service's own
    choice between its two answers, never one made here."""
    shown = shown_state(
        {"state": u.decided_state, "label": u.decided_label, "color": u.decided_color},
        (read.get("running") or {}).get(u.id),
    )
    return {
        "state": shown["state"],
        "state_label": shown["label"],
        "state_color": shown["color"],
        "state_reason": reason_beside(u.attention_reason, shown["state"]),
    }


@dataclasses.dataclass
class SpendRow:
    """One unit, stage or day of the *Cost* screen: money read by `present.money`, and
    beside it the steps whose cost is not known."""

    key: str = ""
    usd: str = ""
    steps: str = ""
    unknown: str = ""
    over: bool = False


@dataclasses.dataclass
class TokenRow:
    """The four kinds of token for the workspace or one stage, each `1,234 (12%)`."""

    scope: str = ""
    input: str = ""
    output: str = ""
    cache_read: str = ""
    cache_creation: str = ""
    total: str = ""


@dataclasses.dataclass
class WasteRow:
    """One kind of waste; `sub` is a line under the kind above it."""

    label: str = ""
    count: str = ""
    usd: str = ""
    unknown: str = ""
    sub: bool = False


@dataclasses.dataclass
class AnomalyRow:
    """One anomaly, its measure and threshold in one cell."""

    key: str = ""
    kind: str = ""
    unit: str = ""
    stage: str = ""
    ended: str = ""
    measured: str = ""
    usd: str = ""


# The words each kind of `spend.model` reads as. Labels only.
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
    """Said beside the money, or nothing when every cost is known."""
    n = int(n or 0)
    return f"{n} unknown" if n else ""


def per_merged_unit(by_unit: list[dict], done: set[str]) -> tuple[str, int]:
    """The mean known cost of the units that finished, and how many there were."""
    costs = [r["usd"] for r in by_unit if r["key"] in done and r.get("usd") is not None]
    if not costs:
        return "—", 0
    return present.money(sum(costs) / len(costs)), len(costs)


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
        scope=scope,
        input=cell("input_tokens"),
        output=cell("output_tokens"),
        cache_read=cell("cache_read_tokens"),
        cache_creation=cell("cache_creation_tokens"),
        total=f"{total:,}",
    )


def _waste_rows(rows: list[dict]) -> list[WasteRow]:
    out: list[WasteRow] = []
    for r in rows:
        label, sub = WASTE_LABEL.get(r["kind"], (r["kind"], False))
        out.append(
            WasteRow(
                label=label,
                count=f"{int(r.get('count') or 0):,}",
                usd=present.money(r.get("usd")),
                unknown=_unknown(r.get("unknown")),
                sub=sub,
            )
        )
        if r["kind"] == "changes-requested":
            # The rounds no `review` step claimed are counted; their money is not known.
            out.append(
                WasteRow(
                    label="not recorded",
                    count=f"{int(r.get('note') or 0):,}",
                    usd="not recorded",
                    sub=True,
                )
            )
    return out


def _measured(a: dict) -> str:
    """The measure and the threshold, `$18.40 > $15`, `4 runs > 3`."""
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
            key=f"{a['kind']}-{i}",
            kind=ANOMALY_LABEL.get(a["kind"], a["kind"]),
            unit=a.get("unit") or NO_UNIT,
            stage=a.get("stage") or "—",
            ended=present.when(a.get("ended")) or "—",
            measured=_measured(a),
            usd=present.money(a.get("usd")),
        )
        for i, a in enumerate(rows)
    ]


# A chat message longer than this many characters is sent cut to it, with a button to fetch the rest.
MESSAGE_CUT = 4000


@dataclasses.dataclass
class BacklogRow:
    """One line of the Backlog panel, copied from `Service.board`'s `backlog`."""

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
    """An agent's estimate reads `agent`; its session id stays in the API."""
    by = str(by or "")
    return "agent" if by.startswith("agent:") else by


def _backlog_row(entry: dict, rank: int) -> BacklogRow:
    est = entry.get("estimate") or {}
    effort = str(est.get("effort") or "")
    if est.get("effort_source") == "guess":
        effort = f"{effort} (guess)"
    basis = " · ".join(
        x for x in (str(est.get("basis") or ""), str(est.get("effort_basis") or "")) if x
    )
    other = entry.get("agent_differs") or {}
    return BacklogRow(
        rank=rank,
        unit=str(entry.get("unit") or ""),
        value=str(est.get("value") or "—"),
        effort=effort or "—",
        basis=basis,
        by=_estimated_by(est.get("by")),
        drift=(
            (f"computed order: #{entry['computed']}" if entry.get("computed") else "not estimated")
            if entry.get("drift")
            else ""
        ),
        warnings="; ".join(entry.get("warnings") or []),
        agent_differs=(
            f"agent proposed: value {other.get('value')}, effort {other.get('effort')} — {other.get('basis')}"
            if other
            else ""
        ),
    )


def _relations_text(relations: list | None) -> str:
    """`thay thế` and `phụ thuộc` read from the side they are on; the other two either way.
    In English, from `present`'s tables; the stored words are unchanged."""

    def word(r: dict) -> str:
        if r.get("direction") == "in" and r.get("type") in present.RELATION_LABEL_IN:
            return present.RELATION_LABEL_IN[r["type"]]
        return present.RELATION_LABEL.get(r.get("type"), str(r.get("type")))

    return "; ".join(f"{word(r)} {r.get('other')}" for r in relations or [])


def backlog_view(data: dict) -> dict:
    """The panel's fields from the board's `backlog`; copies, decides nothing."""
    b = data.get("backlog") or {}
    # One English sentence; the cost thresholds stay in the API and in `backlog`.
    note = (
        f"Only {len(b.get('backlog') or [])} units wait, so a shortlist of 7 picks nothing out."
        if b.get("undiscriminating")
        else ""
    )
    record = b.get("shortlist_record") or {}
    return {
        "backlog_rows": [
            _backlog_row(e, int(e.get("rank") or 0)) for e in b.get("shortlist") or []
        ],
        # A unit with no estimate is a row too, with value and effort empty.
        "backlog_rest": [_backlog_row(e, int(e.get("computed") or 0)) for e in b.get("order") or []]
        + [BacklogRow(unit=str(n), value="—", effort="—") for n in b.get("unestimated") or []],
        "shortlist_draft": [str(e.get("unit") or "") for e in b.get("shortlist") or []],
        "backlog_unestimated": list(b.get("unestimated") or []),
        "backlog_note": note,
        # The finished units measured, and those left out for an unknown cost.
        "backlog_measured": (
            f"{int(b.get('measured_count') or 0)} finished units measured; "
            f"{int(b.get('undetermined_count') or 0)} left out, cost unknown."
            if int(b.get("undetermined_count") or 0) > 0
            else ""
        ),
        "backlog_recorded": (
            f"Saved {present.when(record.get('at'))} by {record.get('by')}: {record.get('reason')}"
            if record
            else ""
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
        # The English label; the API keeps `text` as it was.
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
    """One line on a card: a session running on the unit, or one that ended unseen.

    Every field is copied from `Board.running`; the page chooses only the words.
    """

    # `running`, `rebasing` or `ended, unknown`.
    label: str = ""
    # `ᚢ Uruz`, or empty for a mechanical rebase and for `ended, unknown`.
    agent: str = ""
    stage: str = ""
    started: str = ""
    # Empty when unknown, never `0`: they are always unknown while a step runs.
    turns: str = ""
    cost: str = ""
    # `step`, `gebo`, `rebase` or `unknown`.
    kind: str = ""
    # The step's `run`, what the watch pane opens; empty for anything else.
    run: str = ""


def _activities(unit: str, read: dict) -> list[Activity]:
    """`Board.running`'s answer for one unit, as the lines its card shows."""
    out: list[Activity] = []
    for row in (read.get("running") or {}).get(unit) or []:
        agent = row.get("agent") or {}
        turns, cost = row.get("turns"), row.get("cost_usd")
        out.append(
            Activity(
                label="rebasing" if row.get("kind") == "rebase" else "running",
                agent=f"{agent['glyph']} {agent['name']}" if agent else "",
                stage=str(row.get("stage") or ""),
                started=present.when(row.get("started")),
                turns="" if turns is None else str(turns),
                cost="" if cost is None else f"${float(cost):.2f}",
                kind=str(row.get("kind") or ""),
                run=str(row.get("run") or ""),
            )
        )
    for row in (read.get("unknown_end") or {}).get(unit) or []:
        out.append(
            Activity(
                label="ended, unknown",
                stage=str(row.get("stage") or ""),
                started=present.when(row.get("started")),
                kind="unknown",
            )
        )
    return out


# Seconds between two asks of `Board.running` while the Board is shown.
RUNNING_POLL = 5

# The tabs (client tokens) with a `poll_running` loop alive in this process. Kept here, not in
# page state: a state var would outlive its loop across a restart and the Board would never ask again.
_POLLING: set[str] = set()

# The tabs (client tokens) with a `watch_attempts` loop alive in this process, each with the loop
# it runs on and the event the bus wakes it by. Kept here for the same reason as `_POLLING`.
ATTEMPT_WAKES: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Event]] = {}

# The attempt moves a tab's running list shows: a step's and an integration's, and a Stop asked.
ATTEMPT_MOVES = (
    *(f"step.{m}" for m in ("queued", "preparing", "running", "ending", "ended", "refused")),
    *(f"integration.{m}" for m in ("queued", "running", "ending", "ended", "refused")),
    "step.stop-asked",
    "integration.stop-asked",
)

# The buses `_wake_attempt_watches` is already subscribed to, so one bus is listened to once.
_LISTENING: list = []


def _wake_attempt_watches(_event) -> None:
    """The bus handler: set every tab's event, on the loop that tab waits on. Quick, and it
    reads nothing: the loop that wakes reads the attempts."""
    for loop, woken in list(ATTEMPT_WAKES.values()):
        try:
            loop.call_soon_threadsafe(woken.set)
        except RuntimeError:
            continue  # that tab's loop is closed; its watch ends with it


def listen_to_attempts(bus) -> None:
    """Subscribe `_wake_attempt_watches` to every attempt move of `bus`, once."""
    if any(b is bus for b in _LISTENING):
        return
    _LISTENING.append(bus)
    for name in ATTEMPT_MOVES:
        bus.subscribe(name, _wake_attempt_watches)


# How many asks in a row must find the tab's token unmapped before its loop ends. Reflex
# unmaps a token on every socket drop and maps it again on reconnect, so one miss is often
# a flaky network, not a closed tab.
GONE_AFTER = 12


# The loop `next` asks in flight, by (workspace, unit). Navigation cancels an arrival's
# `on_load` chain, but the ask itself runs on in a task of its own behind `asyncio.shield`,
# so the next arrival at that unit waits for it instead of starting a second.
_ASKING: dict[tuple[str, str], asyncio.Task] = {}


def _asking(ask, cwd: str, unit: str, join: bool) -> asyncio.Future:
    """`ask(cwd, unit)` in a task no cancellation reaches: the one in flight when `join`."""
    key = (cwd, unit)
    task = _ASKING.get(key)
    if not (
        join
        and task is not None
        and not task.done()
        and task.get_loop() is asyncio.get_running_loop()
    ):
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

    Without this a loop of a closed tab would ask until the app stopped. Where no socket
    server exists (in-process, as the proofs drive the state) nobody can be gone.
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
    requests already closed, and nothing retries it."""
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
    # How many characters of `text` were not sent, 0 when it is whole.
    cut: int = 0


@dataclasses.dataclass
class Conversation:
    id: str = ""
    title: str = ""
    subtitle: str = ""
    resumable: bool = False


@dataclasses.dataclass
class AutopilotStop:
    """One unit the autopilot will not start anything on, and why."""

    unit: str = ""
    kind: str = ""
    reason: str = ""


@dataclasses.dataclass
class RunningStep:
    """One board step or integration not yet ended, as `Steps.running_steps` lists it."""

    unit: str = ""
    stage: str = ""
    started_at: str = ""
    # `queued`, `preparing`, `running` or `ending`; `stopping` is a Stop recorded on any of them.
    state: str = "running"
    stopping: bool = False
    # What the watch pane opens; empty until the session starts, and for an integration.
    run: str = ""
    kind: str = "step"


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
    # this is where the reply it was paid for comes back (`coscc/runner/step.py`, `_with_reply`).
    detail: str = ""
    # The step's `run`, empty for a row without one.
    run: str = ""
    # The row's own `_details` key: `run`, or `<stage>-<i>` when that is empty.
    key: str = ""


@dataclasses.dataclass
class Move:
    """One transition of a unit's timeline: what moved and when, the label of the
    guard that decided it and whose decision it was. The guard's id, the full SHA it read
    and the run's id are for *Details* alone."""

    artifact: str = ""
    change: str = ""
    at: str = ""
    guard_label: str = ""
    authority: str = ""
    guard: str = ""
    head: str = ""
    run: str = ""
    key: str = ""


# Each authority in words, for a reader rather than a column name; an answer imported
# unclassified says nobody recorded one.
AUTHORITY_LABEL = {
    "person": "By a person",
    "delegated": "By their delegate",
    "agent": "By an agent",
    "code": "By the app",
}
NO_AUTHORITY = "Author not recorded"


def _moves(rows: list[dict]) -> list[Move]:
    """The timeline's transitions, as `Backlog.timeline` sent them, newest first. Copies."""
    out = []
    for i, r in reversed(list(enumerate(rows))):
        authority = str(r.get("authority") or "")
        out.append(
            Move(
                artifact=str(r.get("artifact") or ""),
                change=f"{r.get('from_state') or '—'} → {r.get('to_state') or '—'}",
                at=present.when(r.get("at")),
                guard_label=str(r.get("guard_label") or "")
                or "No guard was recorded for this change.",
                authority=AUTHORITY_LABEL.get(authority, NO_AUTHORITY),
                guard=str(r.get("guard") or ""),
                head=str(r.get("head") or ""),
                run=str(r.get("run") or ""),
                key=f"move-{r.get('id', i)}",
            )
        )
    return out


@dataclasses.dataclass
class WatchEvent:
    """One event of the watch pane, as `events.collapse` shaped it. Never
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


# The most events the watch pane holds at once. Every frame carries the whole list
# (about 2 KB an event at the collapsed size), so the list has a ceiling.
WATCH_WINDOW = 400

# Seconds the pane gathers new events before it sends them.
WATCH_GATHER = 0.5

NO_RUN_NOTE = "no event stream: this step ran before events were recorded"


# What the board says in place of the service's `read_only_because`, which names
# `COS_WORKING_DIR` and stays as it is for the API.
READ_ONLY_NOTE = "No working folder is set, so nothing can be recorded."

# A label for each autopilot stop (a–f) and the cap; the page words, not a decision.
AUTOPILOT_STOP_LABEL = {
    "a": "Open question",
    "b": "Needs a person",
    "c": "Ship waits",
    "d": "Integration needs a person",
    "e": "Last step did not finish",
    "f": "Blocked",
    "cap": "Daily cap",
    "shortlist": "No shortlist",
}


@dataclasses.dataclass
class GuideItem:
    """One line of the board's guide, copied from `Autopilot.guide_block`: a unit,
    what it is (a stage and its agent, or a thing to do), a line
    below it, and where the link goes (`""` for none)."""

    unit: str = ""
    what: str = ""
    detail: str = ""
    href: str = ""


def _number(text: str, kind: type) -> object:
    """`text` as `kind`, or the text itself for the service to refuse with its reason."""
    try:
        return kind(str(text).strip())
    except ValueError:
        return text


def _watch_note(page: dict) -> str:
    """The line the pane shows so it is never empty without a reason."""
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
    # What the screen calls it, and the environment variable that sets it.
    label: str = ""
    variable: str = ""


@dataclasses.dataclass
class FeatureRow:
    """One feature's state in the open workspace, `plugin.Shown` as the panel reads it."""

    name: str = ""
    state: str = "on"
    # Whether the panel offers `pilot` between `off` and `on`.
    pilot: bool = False
    sentence: str = ""
    # `pilot` and `on` may not be chosen now; `sentence` says why.
    locked: bool = False
    # The schedule chosen here and the choices, as `schedule_label` words; both empty for a
    # feature with no schedule.
    schedule: str = ""
    schedules: list[str] = dataclasses.field(default_factory=list)
    # What the feature does, one fixed sentence.
    summary: str = ""


def schedule_label(hours: int) -> str:
    """How Settings says a schedule's hours."""
    return f"Every {hours} h" if hours else "Schedule off"


# A knob's name is the config field's; the screen says what it does.
KNOB_LABELS = {
    "tools": "Tools",
    "allow_write_and_exec": "Write files and run commands",
    "bypass_permissions": "Skip permission prompts",
    "resume_foreign_sessions": "Resume sessions started elsewhere",
}


def knob(k: dict) -> Knob:
    name = k["name"]
    return Knob(
        name=name,
        value=k["value"],
        detail=k["detail"],
        on=bool(k["on"]),
        label=KNOB_LABELS.get(name, name),
        variable="COS_" + name.upper(),
    )


@dataclasses.dataclass
class DecisionRow:
    """One of the person's decisions as Settings shows it. Copied from
    `Answers.decisions_table`, the days through `present.day`; nothing is decided here."""

    id: str = ""
    kind: str = ""
    text: str = ""
    source: str = ""
    workspace: str = ""
    agent: str = ""
    covers: str = ""
    from_day: str = ""
    until: str = ""
    withdrawn: str = ""
    state: str = ""
    in_force: bool = False


@dataclasses.dataclass
class ImportRow:
    """One field an import could not read, as `Activity.settings` returned it."""

    workspace: str = ""
    unit: str = ""
    artifact: str = ""
    field: str = ""
    reason: str = ""


# --- the Agents page --------------------------------------------------------

# A chip's colour: the two that need a look are loud, the two that do not are quiet.
CHIP_COLOR = {"failed": "red", "costly": "amber", "idle": "gray", "ok": "grass"}
# What each field is called beside its box.
FIELD_LABEL = {
    "model": "Model",
    "effort": "Effort",
    "turns": "Turn ceiling",
    "budget": "Cost ceiling",
    "glyph": "Glyph",
    "name": "Name",
    "meaning": "Meaning",
    "role": "Role",
}
# What a value reads as when no layer names one.
NO_MODEL = "SDK default"
NO_CEILING = "none"


@dataclasses.dataclass
class AgentField:
    """One value the Agents page can set: what is in force, where it came from and what the box holds."""

    row: str = ""
    field: str = ""
    label: str = ""
    value: str = ""
    draft: str = ""
    source: str = ""
    overridden: bool = False


@dataclasses.dataclass
class AgentListRow:
    """One agent as the table shows it."""

    key: str = ""
    glyph: str = ""
    name: str = ""
    model: str = ""
    effort: str = ""
    turns: str = ""
    budget: str = ""
    outcome: str = ""
    when: str = ""
    cost: str = ""
    chip: str = ""
    color: str = "gray"


@dataclasses.dataclass
class AgentRunRow:
    outcome: str = ""
    when: str = ""
    turns: str = ""
    cost: str = ""


@dataclasses.dataclass
class AgentDetail:
    """What one agent's drawer shows; the grant is read, never written."""

    key: str = ""
    glyph: str = ""
    name: str = ""
    meaning: str = ""
    role: str = ""
    glyph_source: str = ""
    name_source: str = ""
    meaning_source: str = ""
    role_source: str = ""
    skill: str = ""
    tools: list[str] = dataclasses.field(default_factory=list)
    commands: list[str] = dataclasses.field(default_factory=list)
    mcp: list[str] = dataclasses.field(default_factory=list)
    submits: str = ""
    warning: str = ""
    chip: str = ""
    color: str = "gray"
    # model, effort, then the two ceilings; and the same of `<key>:novel`, when it has them.
    fields: list[AgentField] = dataclasses.field(default_factory=list)
    variants: list[AgentField] = dataclasses.field(default_factory=list)
    runs: list[AgentRunRow] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class OtherRow:
    """`estimate` or `chat`: a model, and an effort where the row has one."""

    key: str = ""
    model: AgentField = dataclasses.field(default_factory=AgentField)
    effort: AgentField = dataclasses.field(default_factory=AgentField)
    has_effort: bool = False


def _plain(value: object, none: str) -> str:
    return none if value is None else str(value)


def _agent_fields(row: ConfigRow, suffix: str = "") -> list[AgentField]:
    """The fields of one `ConfigRow` its row may set, in the page's order. `suffix` says which
    variant they are, so two `model` boxes in one drawer are told apart."""
    ceil = row["ceilings"]
    shown: dict[str, tuple[str, str, str]] = {
        "model": (_plain(row["model"], NO_MODEL), row["model"] or "", row["model_source"]),
        "effort": (_plain(row["effort"], NO_MODEL), row["effort"] or "", row["effort_source"]),
        "turns": (
            _plain(ceil["max_turns"], NO_CEILING),
            _plain(ceil["max_turns"], ""),
            ceil["max_turns_source"],
        ),
        "budget": (
            "none" if ceil["max_budget_usd"] is None else f"${ceil['max_budget_usd']:.2f}",
            "" if ceil["max_budget_usd"] is None else f"{ceil['max_budget_usd']:.2f}",
            ceil["max_budget_source"],
        ),
    }
    return [
        AgentField(
            row=row["key"],
            field=name,
            label=FIELD_LABEL[name] + suffix,
            value=shown[name][0],
            draft=shown[name][1],
            source=shown[name][2],
            overridden=bool(row["overridden"].get(name)),
        )
        for name in shown
        if name in row["fields"]
    ]


def _run_row(run: RunView, now: datetime | None) -> AgentRunRow:
    return AgentRunRow(
        outcome=run["outcome"] or "—",
        when=present.when(run["at"], now) or "—",
        turns="—" if run["turns"] is None else str(run["turns"]),
        cost=present.money(run["cost_usd"]),
    )


def agent_views(
    page: AgentPage, now: datetime | None = None
) -> tuple[list[AgentListRow], list[AgentDetail], list[OtherRow]]:
    """The table, the drawers and the "Other sessions" rows of one `Agents.agent_page`.
    Nothing is resolved here: the values and their sources are the service's."""
    table: list[AgentListRow] = []
    details: list[AgentDetail] = []
    for r in page["rows"]:
        fields = _agent_fields(r["config"])
        by_name = {f.field: f for f in fields}
        last = r["last"]
        color = CHIP_COLOR.get(r["chip"], "gray")
        table.append(
            AgentListRow(
                key=r["key"],
                glyph=r["glyph"],
                name=r["name"],
                model=by_name["model"].value,
                effort=by_name["effort"].value,
                turns=by_name["turns"].value,
                budget=by_name["budget"].value,
                outcome=(last["outcome"] or "—") if last else "—",
                when=(present.when(last["at"], now) or "—") if last else "",
                cost=present.money(r["cost_30d"]) if r["runs_30d"] else "—",
                chip=r["chip"],
                color=color,
            )
        )
        grant = r["grant"]
        source = r["identity_source"]
        details.append(
            AgentDetail(
                key=r["key"],
                glyph=r["glyph"],
                name=r["name"],
                meaning=r["meaning"],
                role=r["role"],
                glyph_source=source.get("glyph", "default"),
                name_source=source.get("name", "default"),
                meaning_source=source.get("meaning", "default"),
                role_source=source.get("role", "default"),
                skill=r["skill"],
                tools=grant["tools"],
                commands=grant["commands"],
                mcp=grant["mcp"],
                submits="yes" if grant["submits"] else "no",
                warning=grant["warning"],
                chip=r["chip"],
                color=color,
                fields=fields,
                variants=[
                    f
                    for v in r["variants"]
                    for f in _agent_fields(v, " (" + v["key"].rpartition(":")[2] + ")")
                ],
                runs=[_run_row(run, now) for run in r["runs"]],
            )
        )
    others: list[OtherRow] = []
    for row in page["others"]:
        by_name = {f.field: f for f in _agent_fields(row)}
        others.append(
            OtherRow(
                key=row["key"],
                model=by_name["model"],
                effort=by_name.get("effort", AgentField()),
                has_effort="effort" in by_name,
            )
        )
    return table, details, others


# --- formatting --------------------------------------------------------------


def _run_target(data: dict) -> tuple[str, str]:
    """The stage the run button offers and the sentence beside it, copied from
    `Steps.next_step`, which is `coscc.loop next`'s answer.

    The page must not work the stage out itself: that would be a second copy of the loop.
    Nothing here reads `action` to pick a stage.
    """
    return str(data.get("stage") or ""), str(data.get("action") or "")


def _run_waiting(data: dict) -> list[str]:
    """The findings `coscc.loop next` says a person is awaited on, copied; nothing here
    decides whether anyone is awaited."""
    return [str(x) for x in data.get("waiting") or []]


def _run_dropped(data: dict) -> list[str]:
    """The ids `coscc.loop next` says the last review round left out, copied, so the page
    lists them rather than reading them out of `action`."""
    return [str(x) for x in data.get("dropped") or []]


def _key_label(key: str) -> str:
    """A Questions row's key as a person reads it: `intent.md#1` is
    `question 1 of intent.md`, `review.md#F<k>` is `finding F<k> of review.md`."""
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
    """One running job, as the confirmation lists it."""
    kind = job.get("kind", "")
    if kind == "chat":
        what = f"chat {job.get('session_id') or '(new session)'} in {job.get('workspace', '')}"
    elif kind == "build":
        what = "build local"
    else:
        what = f"{kind} {job.get('unit', '')} {job.get('stage', '')}"
    return f"{what}, since {job.get('started', '')}"


def _channel_line(channel: dict) -> str:
    """One channel's state as the panel says it."""
    state = str(channel.get("state") or "")
    parts = [state]
    if channel.get("version"):
        parts.append(str(channel["version"]))
    if channel.get("reason"):
        parts.append(f"— {channel['reason']}")
    return " ".join(parts)


COST_NOTE = "Every finished run"


def cost_note(total: dict) -> str:
    """The Cost tile's caption, saying how many runs it could not add."""
    n = int(total.get("unknown") or 0)
    return f"{COST_NOTE}; {n} without a cost" if n > 0 else COST_NOTE


def _usd(cost: dict) -> str:
    usd = float(cost.get(COST_USD) or 0.0)
    # Runs whose cost nobody knows are never shown as `—` or as nothing: the known part
    # is added, and the rest said to be unknown.
    unknown = int(cost.get("unknown") or 0) > 0
    if not usd:
        return "unknown" if unknown else "—"
    # Under a cent still has to read as a number, not as $0.00.
    known = f"${usd:.4f}" if usd < 0.01 else f"${usd:.2f}"
    return f"{known} + unknown" if unknown else known


def _title_of(unit_name: str) -> str:
    """`NNNN_demo-data-and-no-durable-store` reads as `Demo data and no durable store`.

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
    """The open count and the questions of one board unit, copied from what the loop sent.
    `open` is taken as sent, so the page and `status --json` cannot disagree.

    The findings the loop lists in `personFindings` follow, one row each, keyed
    `review.md#F<n>`. They are not counted into `open`."""
    return int(unit.get("open") or 0), [
        Question(
            key=f"{q['artifact']}#{q['n']}",
            artifact=str(q["artifact"]),
            number=int(q["n"]),
            text=str(q.get("text") or ""),
            answered=bool(q.get("answered")),
            counted=bool(q.get("counted")),
            label=str(q["n"]),
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
    """Each review round as `Service.board` sent it, with its comment state."""
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


def _current_stage(unit: dict, stages: list[str]) -> str:
    """The stage a person would say the unit is at: the last one with an artifact."""
    started = [r["stage"] for r in unit.get("stages") or [] if r.get("status") != "not started"]
    return started[-1] if started else (stages[0] if stages else "")
