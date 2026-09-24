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
import dataclasses
import sys

import reflex as rx
from reflex_base.event.context import EventContext

from coscc import place
from coscc.api import build
from coscc.journal import COST_USD, TOKEN_FIELDS
from coscc.service import Invalid, StaleCutList, describe_base

API = build()
SERVICE = API.state.service

NAVIGATION = (
    ("overview", "Overview", "house"),
    ("workspaces", "Workspaces", "layers"),
    ("board", "Board", "columns-3"),
    ("sessions", "Sessions", "messages-square"),
    ("activity", "Activity & usage", "chart-no-axes-combined"),
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
        if turns is None or cost is None:
            return f"not started · {outcome} · turns and cost unknown", "amber"
        return f"not started · {outcome} · {turns} turns · ${cost:.2f}", "amber"
    return status, STATUS_COLOR.get(status, "gray")


LANE_COLOR = {
    "Planned": "gray",
    "In progress": "iris",
    "Needs review": "amber",
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
    # True when this unit sits in *Needs review*: an artifact is in draft, or the harness
    # reported a problem with the directory. Not the harness's `blocked` — see `_lane`.
    needs_attention: bool = False
    problems: str = ""
    cells: list[Cell] = dataclasses.field(default_factory=list)
    # `0016` R8. How many questions in the counted artifact nobody has answered, taken
    # from `cos.mjs` (`open`) and never recounted (R7). Shown as a badge, not a lane:
    # an open question does not move a unit into *Needs review*.
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
        "outcome_text": str(label.get("text") or ""),
        "outcome_color": str(label.get("color") or "gray"),
        "outcome_detail": detail,
        "outcome_by": str(label.get("by") or ""),
        "outcome_date": str(label.get("date") or ""),
        "outcome_measured_by": str(label.get("measured_by") or ""),
        "outcome_deadline": str(label.get("deadline") or ""),
        "outcome_hint": str(label.get("hint") or ""),
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
            started=str(row.get("started") or ""),
            turns="" if turns is None else str(turns),
            cost="" if cost is None else f"${float(cost):.2f}",
            kind=str(row.get("kind") or ""),
        ))
    for row in (read.get("unknown_end") or {}).get(unit) or []:
        out.append(Activity(
            label="ended, unknown",
            stage=str(row.get("stage") or ""),
            started=str(row.get("started") or ""),
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


@dataclasses.dataclass
class Conversation:
    id: str = ""
    title: str = ""
    subtitle: str = ""
    resumable: bool = False


@dataclasses.dataclass
class RunningStep:
    """`0034`. One board step running now, as `Service.running_steps` lists it."""

    unit: str = ""
    stage: str = ""
    started_at: str = ""
    stopping: bool = False


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
        what = f"chat {job.get('session_id') or '(phiên mới)'} in {job.get('workspace', '')}"
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


def _usd(cost: dict) -> str:
    usd = float(cost.get(COST_USD) or 0.0)
    if not usd:
        return "—"
    # Under a cent still has to read as a number, not as $0.00.
    return f"${usd:.4f}" if usd < 0.01 else f"${usd:.2f}"


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
    Mapping it onto a lane called *Needs review* put all six unfinished units there and
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
        return "Needs review"
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
    units: list[Unit] = []
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
    trees: dict[str, str] = {}

    @rx.var
    def unit_tree(self) -> str:
        return self.trees.get(self.unit_id, "")

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
    run_log: str = ""
    # The unit whose step this page is streaming into `run_log`, so another unit's
    # reply is never shown under the one now open.
    log_unit: str = ""
    # A name typed to stop a step. Like `answer_by`, never stored as a preference.
    stop_by: str = ""

    # -- `0068`: the *Cập nhật* panel. Every field is copied from `Service.update_status`,
    # re-read on load, on every screen change and on every `poll_running` ask; the page
    # decides nothing about an update. `update_pending` is what Run and Send warn on (R9).
    upd_version: str = ""
    upd_commit: str = ""
    upd_available: bool = False
    upd_reason: str = ""
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
    # A name typed to apply, cancel or build. Never stored, like `stop_by`.
    update_by: str = ""
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
    # `answer_by` stays in the page's state and is not stored as a preference, so the
    # settings store gains no key for a name nobody verified.
    answer_target: str = ""
    answer_text: str = ""
    answer_by: str = ""
    # `0071` R6. The key of the question being sent, `""` while none is: only that row's
    # button shows it is sending.
    answering_key: str = ""
    # `0021`. The round being posted, 0 while none is.
    posting_round: int = 0
    # `0035`. True while an integration runs; locks the *Integrate* button.
    integrating: bool = False
    # `0047`. The outcome form. The person recording is `answer_by`, so nobody types a name
    # twice; `outcome_measured_by` starts as `agent`, the case with no script to run.
    outcome_result: str = "đạt"
    outcome_measured_by: str = "agent"
    outcome_source: str = ""
    outcome_reason: str = ""
    outcome_note: str = ""
    recording_outcome: bool = False
    # `0045`. The reason and name typed into the hold panel, and whether a move is in flight.
    # Like `answer_by`, the name is a claim nobody verifies and is not stored as a preference.
    hold_reason: str = ""
    hold_by: str = ""
    holding: bool = False

    # -- sessions
    conversations: list[Conversation] = []
    session_id: str = ""
    messages: list[Message] = []
    prompt: str = ""
    sending: bool = False

    # -- activity and settings
    events: list[Event] = []
    usage_total_tokens: str = "—"
    usage_total_usd: str = "—"
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

    @rx.var
    def visible_units(self) -> list[Unit]:
        q = self.query.strip().lower()
        # `0045` (`spec.md ## Answers, câu 1`). A dropped unit leaves the four lanes for the
        # collapsed group at the foot of the board; a paused one stays in its lane.
        rows = [u for u in self.units if u.hold_state != "dropped"]
        if q:
            rows = [u for u in rows if q in u.id.lower() or q in u.title.lower()]
        if self.focus == "Autonomous":
            rows = [u for u in rows if u.mode == "autonomous"]
        elif self.focus == "Needs review":
            rows = [u for u in rows if u.needs_attention]
        return rows

    @rx.var
    def planned(self) -> list[Unit]:
        return [u for u in self.visible_units if u.lane == "Planned"]

    @rx.var
    def in_progress(self) -> list[Unit]:
        return [u for u in self.visible_units if u.lane == "In progress"]

    @rx.var
    def needs_review(self) -> list[Unit]:
        return [u for u in self.visible_units if u.lane == "Needs review"]

    @rx.var
    def complete(self) -> list[Unit]:
        return [u for u in self.visible_units if u.lane == "Complete"]

    @rx.var
    def active_count(self) -> int:
        return len([u for u in self.units if u.lane == "In progress"])

    @rx.var
    def attention_count(self) -> int:
        return len([u for u in self.units if u.needs_attention and u.hold_state != "dropped"])

    @rx.var
    def dropped_units(self) -> list[Unit]:
        """`0045`. The units `cos.mjs` reads as dropped, for the collapsed group."""
        return [u for u in self.units if u.hold_state == "dropped"]

    @rx.var
    def current_unit(self) -> Unit:
        for u in self.units:
            if u.id == self.unit_id:
                return u
        return Unit()

    @rx.var
    def ws_name(self) -> str:
        """`0056` R8. What `ws=` carries: the workspace's name, never its path."""
        return next((w.name for w in self.workspaces if w.id == self.cwd), "")

    @rx.var
    def unit_missing(self) -> bool:
        """`0056` R9. An address named a unit this workspace's board does not list."""
        return (self.unit_id != "" and not self.loading
                and not any(u.id == self.unit_id for u in self.units))

    @rx.var
    def unit_dropped(self) -> bool:
        """`0056` R11. The open unit is dropped, so the dialog offers nothing that writes."""
        return any(u.id == self.unit_id and u.hold_state == "dropped" for u in self.units)

    @rx.var
    def board_href(self) -> str:
        ws = next((w.name for w in self.workspaces if w.id == self.cwd), "")
        return place.href(place.Place("board", ws))

    @rx.var
    def open_questions_here(self) -> list[Question]:
        """`0016`. The open unit's unanswered questions, counted artifact's first."""
        waiting = [q for q in self.current_unit.questions if not q.answered]
        return sorted(waiting, key=lambda q: not q.counted)

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
    def command_units(self) -> list[Unit]:
        q = self.command_query.strip().lower()
        if not q:
            return self.units[:5]
        return [u for u in self.units if q in u.title.lower() or q in u.id.lower()][:6]

    @rx.var
    def usage_rows(self) -> list[Unit]:
        return [u for u in self.units if u.token_count > 0]

    @rx.var
    def usage_scale(self) -> int:
        """The bar scale, taken from the largest real value rather than from a guess."""
        return max([u.token_count for u in self.units] + [1])

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
            self.running_steps = [RunningStep(**r) for r in SERVICE.running_steps(self.cwd)]
        except Invalid:
            self.running_steps = []

    async def _load_board(self) -> None:
        self.units, self.stages, self.board_note = [], [], ""
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
        self.trees = {
            u["name"]: tree_line(u.get("worktree")) for u in data.get("units") or []
        }
        self.recording = bool(data["recording"])
        self.board_note = data.get("read_only_because") or data.get("empty_because") or ""
        empty = data.get("empty") or {}
        self.empty_store = str(empty.get("store") or "")
        self.empty_host = str(empty.get("host") or "")
        self.empty_host_units = int(empty.get("host_units") or 0)
        if self.empty_host_units > 0:
            # `empty_because` speaks of the store's `.cos/`, and next to a host `.cos/`
            # that is full it reads as a claim about the wrong directory — the fault this
            # unit exists for. Only the read-only reason survives, appended by the page.
            self.board_note = data.get("read_only_because") or ""

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
                    needs_attention=lane == "Needs review",
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
                )
            )
        self.units = units

    def _apply_running(self, read: dict) -> None:
        """`0051` R3. Put one `Service.running` answer on every card. Decides nothing."""
        self._running_read = read
        for unit in self.units:
            unit.live = _activities(unit.id, read)
        # Reflex sends a list whose items were changed in place only if the list is set.
        self.units = list(self.units)

    def _load_sessions(self) -> None:
        self.conversations, self.messages = [], []
        if not self.cwd:
            self.session_id = ""
            return
        try:
            data = SERVICE.sessions_for(self.cwd, limit=40)
        except Invalid as e:
            self._fail(e)
            return
        rows = [
            Conversation(
                id=row["session_id"],
                title=(row.get("summary") or row.get("session_id") or "")[:60] or "Untitled",
                subtitle=str(row.get("last_modified") or row.get("created_at") or "")[:19],
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
            self.messages = []
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
        self.messages = rows

    def _load_activity(self) -> None:
        self.events = []
        self.usage_total_tokens, self.usage_total_usd = "—", "—"
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
            events.append(Event(title=title, detail=detail, icon=icon, color=color, time=row["at"]))
        self.events = events
        total = feed.get("total") or {}
        _, shown = _tokens(total)
        self.usage_total_tokens = shown
        self.usage_total_usd = _usd(total)

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
                started=r.get("started") or "",
                ended=r.get("ended") or "(still running)",
                outcome=r.get("outcome") or "—",
                session_id=(r.get("session_id") or "—")[:12],
                tokens=_tokens(r.get("cost") or {})[1],
                usd=_usd(r.get("cost") or {}),
                color="grass" if r.get("outcome") == "done" else "amber",
                detail=r.get("detail") or "",
            )
            for r in data["runs"]
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
            self._load_workspaces()
        except Invalid as e:
            self._fail(e)

    async def _load_rest(self) -> None:
        await self._load_models()
        await self._load_board()
        self._load_sessions()
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
        if first:
            self.loading, self.error = True, ""
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
        self.unit_id, self.detail_tab = unit, tab

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
            self._load_sessions()
            self._load_activity()
        elif (moved_unit and not unit) or (moved_screen and not moved_unit):
            self._load_update()
        self._read_cwd = cwd
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
        if not isinstance(value, str) or value not in ("All work", "Autonomous", "Needs review"):
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
            self.run_stage = ""
            self.run_waiting = []
            self.run_said = "Asking cos.mjs what comes next…"
        if not (unit and cwd):
            async with self:
                self.run_said = ""
            return
        waiting: list[str] = []
        try:
            stage, said = _run_target(found := await SERVICE.next_step(cwd, unit))
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
    def set_answer_by(self, value: str):
        self.answer_by = value

    @rx.event
    def set_stop_by(self, value: str):
        self.stop_by = value

    @rx.event
    async def stop_step(self, unit: str):
        """`0034` R6. Stop one unit's running step. Every rule is `Service.stop_step`'s --
        a missing name, nothing running, a step already writing its artifact -- and its
        refusal is shown as its words. The streaming handler sees the `stopped` outcome."""
        self.error = ""
        try:
            done = await SERVICE.stop_step(self.cwd, unit, self.stop_by)
            self.notice = f"Stopping {done['unit']} {done['stage']} (by {done['stopped_by']})."
        except Invalid as e:
            self._fail(e)
        finally:
            self._load_running()

    # -- `0068`: updating the app. Every rule is `Updater`'s, behind `Service`; a refusal
    # arrives here as its words.

    def _load_update(self) -> None:
        u = SERVICE.update_status()
        self.upd_version = str(u.get("version") or "")
        self.upd_commit = str(u.get("commit_label") or "")
        self.upd_available = u.get("shape") == "service"
        self.upd_reason = str(u.get("reason") or "")
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
        self.upd_checked_at = str(u.get("checked_at") or "")
        error = u.get("error") or {}
        self.upd_error = str(error.get("message") or "")
        self.upd_error_tail = str(error.get("log_tail") or "")
        last = u.get("last") or {}
        self.upd_last = (
            f"{last.get('result')}: {last.get('from')} → {last.get('to')} (log: {last.get('log')})"
            if last else ""
        )
        self.upd_last_tail = str(last.get("log_tail") or "")

    @rx.event
    def set_update_by(self, value: str):
        self.update_by = value

    @rx.event
    async def apply_update(self, channel: str):
        """R7: apply, or wait for what is running (R9)."""
        try:
            await SERVICE.update_apply(channel, "wait", self.update_by, "")
        except Invalid as e:
            self._fail(e)
        self._load_update()

    @rx.event
    def show_cut_list(self, channel: str):
        """R10: what "áp dụng ngay" would cut, shown before anything is cut."""
        try:
            listing = SERVICE.update_cut_list()
        except Invalid as e:
            self._fail(e)
            return
        self._show_cut(channel, listing)

    def _show_cut(self, channel: str, listing: dict) -> None:
        self.cut_channel = channel
        self.cut_items = [f"{i['action']}: {_job_line(i)}" for i in listing.get("items") or []]
        self.cut_token = str(listing.get("token") or "")
        self.cut_open = True

    @rx.event
    def close_cut_list(self):
        self.cut_open = False

    @rx.event
    async def confirm_apply_now(self):
        try:
            await SERVICE.update_apply(self.cut_channel, "now", self.update_by, self.cut_token)
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
            SERVICE.update_cancel(self.update_by)
        except Invalid as e:
            self._fail(e)
        self._load_update()

    @rx.event
    def build_local(self):
        try:
            SERVICE.update_build_local(self.update_by)
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
                self.cwd, self.unit_id, artifact, number, self.answer_text, self.answer_by
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
        is `Service.record_outcome`'s; a refusal arrives here as its words, shown as they are."""
        self.recording_outcome = True
        try:
            done = await SERVICE.record_outcome(
                self.cwd, self.unit_id, self.outcome_result, self.outcome_measured_by,
                self.outcome_source, self.outcome_reason, self.outcome_note, self.answer_by,
            )
        except Invalid as e:
            self.notice = str(e)
            return
        finally:
            self.recording_outcome = False
        self.outcome_source, self.outcome_reason, self.outcome_note = "", "", ""
        self.notice = (
            f"Recorded {done['result']} for {done['unit']} as {done['recorded_by']}, measured by "
            f"{done['measured_by']}. Nothing was started, and no gate reads it."
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
    def set_hold_by(self, value: str):
        self.hold_by = value

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
            done = await SERVICE.hold(self.cwd, self.unit_id, to, self.hold_reason, self.hold_by)
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
        self.trees = {**self.trees, self.unit_id: tree_line(
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
        self.messages = []
        self.prompt = ""
        self.notice = "New conversation. It is saved once you send the first message."

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
        self._load_sessions()
        self._load_activity()
