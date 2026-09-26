"""What the page shows, before it is state: the rows and cards `StudioState` holds, and the
pure functions that build them from what `Service` returns. Split from `coscc/state.py`
(`0095`), which re-exports every name.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys

from coscc import events as events_mod
from coscc import present, spend
from coscc.journal import COST_USD, TOKEN_FIELDS
from coscc.service import reason_beside, shown_state


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
    # which `said` this and cites `cites`. `needs_person`: Jera's last run said a person must
    # answer it, with its `proposal` and `reason`.
    by_jera: bool = False
    cites: list[str] = dataclasses.field(default_factory=list)
    said: str = ""
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
    # The last stage with an artifact: the one the Artifact tab opens. Not the column,
    # which is `at` (`0100` R2).
    stage: str = ""
    owner: str = "You"
    mode: str = "manual"
    tokens: str = ""
    usd: str = ""
    token_count: int = 0
    progress: int = 0
    problems: str = ""
    cells: list[Cell] = dataclasses.field(default_factory=list)
    # `0016` R8. How many questions in the counted artifact nobody has answered, taken
    # from `cos.mjs` (`open`) and never recounted (R7). Since `0100` an open question
    # makes the unit's state *Needs you* (`intent.md ## Answers, câu 3`).
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
    # `0100` R2, R3. The stage whose column the unit sits in, as `cos.mjs` sent it, and the
    # state shown: `Service.board`'s decision (`decided_*`), with `Running` laid over it by
    # `service.shown_state` alone (Design 5). `ci_line` is the dialog's CI line (R7), and
    # `state_reason` the `attention_reason` its header shows beside the state (review F1).
    at: str = ""
    state: str = ""
    state_label: str = ""
    state_color: str = "gray"
    ci_line: str = ""
    state_reason: str = ""
    decided_state: str = ""
    decided_label: str = ""
    decided_color: str = "gray"


@dataclasses.dataclass
class Card:
    """`0053` R7. One card: only what a card, a List row, *Pick up where you left off*, a
    Usage row and the command palette draw. The page receives this list once; the whole
    `Unit` of the one unit open is `current_unit`, and every other `Unit` stays on the server
    (`_full`). Each field is copied from that `Unit` by `_card`."""

    id: str = ""
    title: str = ""
    summary: str = ""
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
    at: str = ""
    state: str = ""
    state_label: str = ""
    state_color: str = "gray"


def _card(u: Unit) -> Card:
    """`0053`. A `Unit` as its card. Copies; decides nothing."""
    return Card(
        id=u.id, title=u.title, summary=u.summary, mode=u.mode, owner=u.owner, progress=u.progress, tokens=u.tokens, usd=u.usd,
        token_count=u.token_count, has_problem=u.problems != "", open_questions=u.open_questions,
        integration_state=u.integration_state, integrate_button=u.integrate_button,
        outcome_text=u.outcome_text, outcome_color=u.outcome_color, hold_state=u.hold_state,
        shortlist_rank=u.shortlist_rank, relations_text=u.relations_text, live=list(u.live),
        answerable=u.answerable, attention_reason=u.attention_reason,
        at=u.at, state=u.state, state_label=u.state_label, state_color=u.state_color,
    )


def _ci_line(ci: dict | None) -> str:
    """`0100` R7. The service's CI answer as one line, `""` where it gives none. The time
    is for a reader (S4); no SHA (S3)."""
    if not ci:
        return ""
    read = " · read " + present.when(ci.get("at")) if ci.get("read") else ""
    if ci.get("red"):
        return "CI is red: " + ", ".join(ci["red"]) + read
    if ci.get("read"):
        return "CI is not red" + read
    return "CI has not been read"


def _shown(u: Unit, read: dict) -> dict:
    """`0100` Design 5. The `state*` fields of `u` under one `Service.running` answer: the
    service's own choice between its two answers, never one made here."""
    shown = shown_state(
        {"state": u.decided_state, "label": u.decided_label, "color": u.decided_color},
        (read.get("running") or {}).get(u.id),
    )
    return {"state": shown["state"], "state_label": shown["label"], "state_color": shown["color"],
            "state_reason": reason_beside(u.attention_reason, shown["state"])}


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
    # `0114` R1: `integration` has no watch pane and no Stop.
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
            said=str(q.get("said") or ""),
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


def _current_stage(unit: dict, stages: list[str]) -> str:
    """The stage a person would say the unit is at: the last one with an artifact."""
    started = [r["stage"] for r in unit.get("stages") or [] if r.get("status") != "not started"]
    return started[-1] if started else (stages[0] if stages else "")
