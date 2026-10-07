"""The one board read: every unit of a workspace with its stage, what is running on it and its
worktree, held per workspace and read again when the app changes something.

`units.board` runs the loop and shapes its answer; this module adds what only the app knows (the
run log, backlog, worktrees, open pull requests, CI) and keeps the last read. What sits above
`units` (the step machine's integration and CI reads) is handed in as plain callables by whoever
builds the `Board`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, TypedDict

from coscc.agent import agents, pack
from coscc.agent.policy import row_for
from coscc.bus import Bus, Event
from coscc.config import Config
from coscc.git import gitops
from coscc.git.gitops import GitError
from coscc.store.db import Busy, now
from coscc.store.journal import last_runs, paused_stage, timelines_of, totals_of
from coscc.units import BadUnit, Invalid, backlog, contracts, scratch, worktrees
from coscc.units import board as board_reader
from coscc.units.board import Unavailable, attention_reason, unit_state
from coscc.units.meta import (
    By,
    DecisionKind,
    OutputRecord,
    RoundCriterion,
    RoundFinding,
    RoundGrades,
    Verdict,
)
from coscc.units.meta import Decision as DecisionRow
from coscc.units.proposals import Proposal
from coscc.units.workspaces import Workspaces

log = logging.getLogger(__name__)


def younger_than(at: str, oldest: datetime) -> bool:
    """Whether a run-log `at` is after `oldest`. One that will not parse is not shown."""
    try:
        when = datetime.fromisoformat(at)
    except TypeError, ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when > oldest


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
            self.held[key] = (value, now())
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


# An `ended, unknown` row stops being shown this long after it began, unless a later `start`
# of the same unit retired it first.
UNKNOWN_END_FOR = timedelta(hours=24)


class HoldView(TypedDict):
    state: str
    by: str
    date: str
    reason: str


class CardState(TypedDict):
    state: str
    label: str
    color: str


class PullRequest(TypedDict):
    number: int
    url: str


# The reason code (`guards.REASONS`) of a stage paused at its ceiling.
BUDGET_REACHED = "budget-reached"


class Paused(TypedDict):
    """A run that stopped at a ceiling and kept its session: the `stage`, which `ceiling` it hit
    (`turns` or `usd`), what it spent and both ceilings. The unit is held: `code` is
    `budget-reached`, what a plain run of the stage is refused with."""

    stage: str
    code: str
    ceiling: str
    usd: float | None
    max_usd: float | None
    turns: int | None
    max_turns: int | None


class Card(TypedDict):
    """A unit as a list shows it: what it is, where it stands and what it cost. The whole unit
    is the board's (`Board.read`)."""

    name: str
    number: int
    slug: str
    type: str
    phase: str
    next_stage: str
    why: str
    open: int
    state: CardState
    hold: HoldView | None
    pr: PullRequest | None
    cost_usd: float
    # The stage the unit is at.
    at: str
    # When its last run ended, or empty: what a list sorts by.
    updated: str
    attention_reason: str
    # The process the unit walks, `<pack>/<name>`.
    process: str
    # What the next stage declares it needs and the unit lacks: the step is refused until it is there.
    missing: list[str]
    idea: str
    rank: int | None
    effort: str | None
    # Set while the stage the unit is at waits on a raised ceiling.
    paused: Paused | None


class Cap(TypedDict):
    day: str
    limit: float
    spent: float
    known: float
    estimated: float
    estimated_count: int
    running: float


class AutopilotBrief(TypedDict):
    on: bool
    may_ship: bool
    max_parallel: int
    refused_because: str
    cap: Cap | None


class Running(TypedDict):
    unit: str
    stage: str
    agent: str
    started: str


class Cards(TypedDict):
    workspace: str
    read_at: str
    units: list[Card]
    autopilot: AutopilotBrief | None
    running: list[Running]


class EstimateBrief(TypedDict):
    unit: str
    value: int | None
    effort: str | None
    effort_source: str
    similar: list[str]
    basis: str
    effort_basis: str
    by: str
    at: str


class Shortlisted(TypedDict):
    """One unit of the saved shortlist, in its order; `computed` is where the estimates and
    relations would put it, `drift` when that differs."""

    rank: int
    unit: str
    estimate: EstimateBrief | None
    agent_differs: EstimateBrief | None
    computed: int | None
    drift: bool
    warnings: list[str]


class Suggested(TypedDict):
    unit: str
    computed: int
    estimate: EstimateBrief
    agent_differs: EstimateBrief | None


class ShortlistSaved(TypedDict):
    at: str | None
    n: int
    by: str
    reason: str


class UpNext(TypedDict):
    """What the autopilot works on next in one workspace: the saved shortlist, every other
    estimated unit in the computed order, and what has no estimate yet."""

    shortlist: list[Shortlisted]
    shortlist_record: ShortlistSaved | None
    order: list[Suggested]
    unestimated: list[str]
    warnings: list[Any]
    max: int
    propose_warning: str


def card(u: Mapping[str, Any]) -> Card:
    """One unit of `Board.read` as a list shows it."""
    state, hold, pr, backlog_ = u["state"], u.get("hold"), u.get("pr"), u.get("backlog") or {}
    return {
        "name": u["name"],
        "number": int(u["number"]),
        "slug": u["slug"],
        "type": str(u.get("type") or ""),
        "phase": str(u.get("phase") or ""),
        "next_stage": str(u.get("next_stage") or ""),
        "why": str(u.get("why") or ""),
        "open": int(u.get("open") or 0),
        "state": {"state": state["state"], "label": state["label"], "color": state["color"]},
        "hold": {
            "state": str(hold.get("state") or ""),
            "by": str(hold.get("by") or ""),
            "date": str(hold.get("date") or ""),
            "reason": str(hold.get("reason") or ""),
        }
        if hold
        else None,
        "pr": {"number": int(pr["number"]), "url": str(pr["url"])} if pr else None,
        "cost_usd": float((u.get("cost") or {}).get("cost_usd") or 0),
        "at": str(u.get("at") or ""),
        "updated": max(
            (str((st.get("last_run") or {}).get("ended") or "") for st in u.get("stages") or []),
            default="",
        ),
        "attention_reason": str(u.get("attention_reason") or ""),
        "process": str(u.get("process") or ""),
        "missing": [str(m) for m in u.get("missing") or []],
        "idea": str(u.get("idea") or ""),
        "rank": backlog_.get("rank"),
        "effort": backlog_.get("effort"),
        "paused": paused(u.get("paused")),
    }


def paused(p: Mapping[str, Any] | None) -> Paused | None:
    """A timeline row's `paused` (`journal.paused_of`) with its stage, as a card carries it."""
    if not p:
        return None
    return {
        "stage": _text(p.get("stage")),
        "code": BUDGET_REACHED,
        "ceiling": _text(p.get("ceiling")),
        "usd": number(p.get("usd")),
        "max_usd": number(p.get("max_usd")),
        "turns": count(p.get("turns")),
        "max_turns": count(p.get("max_turns")),
    }


def cards(board: Mapping[str, Any]) -> Cards:
    """`board` (`Board.read` with the autopilot's view) cut to what a list of units shows."""

    pilot = board.get("autopilot")
    autopilot: AutopilotBrief | None = (
        {
            "on": bool(pilot["on"]),
            "may_ship": bool(pilot.get("may_ship")),
            "max_parallel": int(pilot.get("max_parallel") or 0),
            "refused_because": str(pilot.get("refused_because") or ""),
            "cap": pilot.get("cap"),
        }
        if pilot
        else None
    )
    running = (board.get("guide") or {}).get("running") or []
    return {
        "workspace": str(board.get("workspace") or ""),
        "read_at": str(board.get("read_at") or ""),
        "units": [card(u) for u in board.get("units") or []],
        "autopilot": autopilot,
        "running": [
            {
                "unit": str(r.get("unit") or ""),
                "stage": str(r.get("stage") or ""),
                "agent": str(r.get("agent") or ""),
                "started": str(r.get("started") or ""),
            }
            for r in running
        ],
    }


class LastRun(TypedDict):
    outcome: str
    ended: str
    turns: int | None
    cost_usd: float | None


class StageView(TypedDict):
    stage: str
    file: str
    status: str
    optional: bool
    last_run: LastRun | None


class Question(TypedDict):
    artifact: str
    n: int
    text: str
    # The answer the agent that asked recommends, `""` when it gave none.
    recommendation: str
    answered: bool
    # The `by` and `name` of the answer in force, `""` while unanswered.
    by: str
    name: str


class Answer(TypedDict):
    artifact: str
    n: int
    question: str
    text: str
    # Whose decision: `person` (a person's press) or `delegated` (decided for them), as sent.
    by: By
    # The name the answer was sent with: a claim, not an identity.
    name: str
    via: str
    date: str


class Decision(TypedDict):
    """A person's rerun, more rounds or outcome, as the unit's history shows it."""

    kind: DecisionKind
    by: str
    date: str
    text: str


def _decision_text(d: DecisionRow) -> str:
    fields = d["fields"]
    if d["kind"] == "rerun":
        return f"asked {fields.get('stage')} to run again"
    if d["kind"] == "more-rounds":
        return "allowed one more review round"
    return f"recorded the outcome: {fields.get('result')}"


class Round(TypedDict):
    n: int
    verdict: str
    findings: int
    findings_open: int
    unfinished: bool
    criteria: list[RoundCriterion]
    items: list[RoundFinding]


class Dependency(TypedDict):
    ref: str
    why: str
    merged: bool


class RunPart(TypedDict):
    """One part of a session that a ceiling paused and a raise went on from."""

    run: str
    ended: str
    cost_usd: float | None
    paused: Paused | None


class UnitRun(TypedDict):
    """One run of a stage, from the run log; `ended` is empty while it runs."""

    stage: str
    agent: str
    model: str
    started: str
    ended: str
    outcome: str
    detail: str
    artifact: str
    cost_usd: float | None
    turns: int | None
    run: str
    # The parts its prompt was handed (`start.envelope`), `[]` when it records none.
    envelope: list[str]
    # Set while it waits on a raised ceiling.
    paused: Paused | None
    # The parts of this one session that ended at a ceiling before a raise went on, oldest first.
    parts: list[RunPart]
    raised_by: str


class Worktree(TypedDict):
    branch: str
    path: str


class OutcomeProposal(TypedDict):
    id: int
    title: str
    state: str


class Outcome(TypedDict):
    """What a shipped unit's page shows of its outcome: the row a *Grade outcome* press runs (its
    key, name and $ ceiling), its latest verdict, and the proposals the grader made of it."""

    grader: str
    name: str
    usd: float | None
    verdict: Verdict | None
    proposals: list[OutcomeProposal]


def grader() -> tuple[str, Mapping[str, Any]] | None:
    """The row a person presses to grade a unit's outcome: the first whose output is a `verdict`
    and whose trigger says `manual`."""
    for key, row in pack.rows().items():
        if (row.get("output") or {}).get("kind") == "verdict" and pack.triggered(row, "manual"):
            return key, row
    return None


def outcome(
    key: str, row: Mapping[str, Any], verdict: Verdict | None, made: Sequence[Proposal]
) -> Outcome:
    usd = (row.get("ceilings") or {}).get("usd")
    return {
        "grader": key,
        "name": str(row.get("name") or key),
        "usd": float(usd) if isinstance(usd, (int, float)) else None,
        "verdict": verdict,
        "proposals": [{"id": p["id"], "title": p["title"], "state": p["state"]} for p in made],
    }


class Detail(TypedDict):
    """One unit as its page shows it: the card, its stages, what was asked and answered, its
    review rounds and every run, oldest first."""

    card: Card
    stages: list[StageView]
    questions: list[Question]
    answers: list[Answer]
    rounds: list[Round]
    depends_on: list[Dependency]
    runs: list[UnitRun]
    worktree: Worktree | None
    # The holds the loop allows now: `paused`, `dropped`, `active` (a resume).
    hold_moves: list[str]
    # What each agent last handed back, with the contract version it was written to.
    outputs: list[OutputRecord]
    # A person's reruns, more rounds and outcomes, oldest first.
    decisions: list[Decision]
    # Its graded outcome, `None` when no row grades outcomes.
    outcome: Outcome | None


def _text(v: Any) -> str:
    return "" if v is None else str(v)


def number(v: Any) -> float | None:
    """`v` as a float when it is a number and no bool, else `None`."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def count(v: Any) -> int | None:
    """`v` when it is a whole number and no bool, else `None`."""
    return int(v) if isinstance(v, int) and not isinstance(v, bool) else None


def detail(
    unit: Mapping[str, Any],
    timeline: Sequence[Mapping[str, Any]],
    outputs: list[OutputRecord],
    decisions: Sequence[DecisionRow] = (),
    graded: Mapping[int, RoundGrades] | None = None,
    outcome: Outcome | None = None,
) -> Detail:
    """`unit`, one unit of `Board.read`, with `timeline` (`Journal.timeline`), its `outputs`
    (`UnitMeta.outputs`), its `decisions` (`UnitMeta.decisions`) and what each review round graded
    and found (`UnitMeta.graded`) as a page shows it."""
    graded = graded or {}

    def last(r: Mapping[str, Any] | None) -> LastRun | None:
        if not r:
            return None
        return {
            "outcome": _text(r.get("outcome")),
            "ended": _text(r.get("ended")),
            "turns": count(r.get("turns")),
            "cost_usd": number(r.get("cost_usd")),
        }

    tree = unit.get("worktree")
    return {
        "card": card(unit),
        "stages": [
            {
                "stage": _text(st.get("stage")),
                "file": _text(st.get("file")),
                "status": _text(st.get("status")),
                "optional": bool(st.get("optional")),
                "last_run": last(st.get("last_run")),
            }
            for st in unit.get("stages") or []
        ],
        "questions": [
            {
                "artifact": _text(q.get("artifact")),
                "n": int(q.get("n") or 0),
                "text": _text(q.get("text")),
                "recommendation": _text(q.get("recommendation")),
                "answered": bool(q.get("answered")),
                "by": _text(q.get("by")),
                "name": _text(q.get("name")),
            }
            for q in unit.get("questions") or []
        ],
        "answers": [
            {
                "artifact": _text(a.get("artifact")),
                "n": int(a.get("n") or 0),
                "question": _text(a.get("question")),
                "text": _text(a.get("text")),
                "by": "person" if a.get("by") == "person" else "delegated",
                "name": _text(a.get("name")),
                "via": _text(a.get("via")),
                "date": _text(a.get("date")),
            }
            for a in unit.get("answers") or []
        ],
        "rounds": [
            {
                "n": int(r.get("n") or 0),
                "verdict": _text(r.get("verdict")),
                "findings": int(r.get("findings") or 0),
                "findings_open": int(r.get("findings_open") or 0),
                "unfinished": bool(r.get("unfinished")),
                "criteria": graded.get(int(r.get("n") or 0), {"criteria": []})["criteria"],
                "items": graded.get(int(r.get("n") or 0), {"items": []})["items"],
            }
            for r in unit.get("rounds") or []
        ],
        "depends_on": [
            {
                "ref": _text(d.get("ref")),
                "why": _text(d.get("why")),
                "merged": bool(d.get("merged")),
            }
            for d in unit.get("depends_on") or []
        ],
        "runs": [
            {
                "stage": _text(r.get("stage")),
                "agent": _text(r.get("agent")),
                "model": _text(r.get("model")),
                "started": _text(r.get("started")),
                "ended": _text(r.get("ended")),
                "outcome": _text(r.get("outcome")),
                "detail": _text(r.get("detail")),
                "artifact": _text(r.get("artifact")),
                "cost_usd": number((r.get("cost") or {}).get("cost_usd"))
                if r.get("reported", True)
                else None,
                "turns": count((r.get("cost") or {}).get("turns"))
                if r.get("turns_reported", True)
                else None,
                "run": _text(r.get("run")),
                "envelope": [str(p) for p in r.get("envelope") or []],
                "paused": paused({**p, "stage": r.get("stage")})
                if (p := r.get("paused"))
                else None,
                "parts": [
                    {
                        "run": _text(part.get("run")),
                        "ended": _text(part.get("ended")),
                        "cost_usd": number((part.get("cost") or {}).get("cost_usd")),
                        "paused": paused({**pp, "stage": r.get("stage")})
                        if (pp := part.get("paused"))
                        else None,
                    }
                    for part in r.get("parts") or []
                ],
                "raised_by": _text(r.get("raised_by")),
            }
            for r in timeline
        ],
        "worktree": {"branch": _text(tree.get("branch")), "path": _text(tree.get("path"))}
        if tree
        else None,
        "hold_moves": [str(m) for m in unit.get("hold_moves") or []],
        "outputs": outputs,
        "outcome": outcome,
        "decisions": [
            {"kind": d["kind"], "by": d["by"], "date": d["date"], "text": _decision_text(d)}
            for d in decisions
        ],
    }


def _brief_rounds(units_: list[dict[str, Any]]) -> None:
    """Every review round without its text: what reads the whole text (a comment, an impl's
    claim) reads it from the store, never from the board."""
    for u in units_:
        for rnd in u.get("rounds") or []:
            rnd.pop("text", None)


async def _in_thread[T](fn: Callable[..., T], *args: Any) -> T:
    """`asyncio.to_thread`, except that a cancelled caller ends only once the thread returned: a
    thread cannot be stopped, and one still writing `cos.db` after shutdown returned is what
    this waits out."""
    fut = asyncio.ensure_future(asyncio.to_thread(fn, *args))
    try:
        return await asyncio.shield(fut)
    except asyncio.CancelledError:
        # `wait` neither cancels `fut` nor raises what it raised; a second cancel waits on.
        while not fut.done():
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait({fut})
        if not fut.cancelled():
            fut.exception()  # retrieved: the caller is told it was cancelled, nothing else
        raise


class Board:
    """The board of every workspace, read once each and held.

    `unfinished(key)` lists a workspace's unfinished attempts (what is running); `open_prs(cwd)` asks `gh` for the open pull requests (a list, or
    its error as a string); `attach(cwd, data, journal, key, prs, fresh)` adds what sits above
    `units`: each unit's `integration`, and returns the function that starts the CI asks that
    read found missing, which the read calls last.
    """

    def __init__(
        self,
        config: Config,
        ws: Workspaces,
        bus: Bus,
        unfinished: Callable[[str], list[Any]],
        open_prs: Callable[[str], Awaitable[list[dict[str, Any]] | str]],
        attach: Callable[..., Awaitable[Callable[[], None]]],
    ) -> None:
        self.config = config
        self.ws = ws
        self.unfinished = unfinished
        self.open_prs = open_prs
        self.attach = attach
        # By journal key: the last board read, `{cwd, data, read_at}`; the one read running;
        # the keys a change came to while it ran, so it reads once more; and what waits for
        # the next read to end. This process only.
        self.held: dict[str, dict[str, Any]] = {}
        self.reads: dict[str, asyncio.Task] = {}
        self._again: dict[str, bool] = {}
        self._ended: dict[str, asyncio.Future] = {}
        # The open pull requests of each workspace, as `gh` last answered; an answer that
        # changed reads the board again.
        self.prs = Asked(self.changed)
        # A finished unit's tree being removed, by `(cwd, unit)`: never waited on by a read.
        self._removing: dict[tuple[str, str], asyncio.Task] = {}
        # Set by `stop`: the app is going down, and no read or removal starts any more.
        self._stopping = False
        # A change the app made reads that workspace's board again, once one was read.
        for name in (
            "step.ended",
            "step.refused",
            "integration.ended",
            "integration.refused",
            "answer.written",
            "hold.moved",
            "mode.set",
            "unit.shipped",
        ):
            bus.subscribe(name, self._on_event)

    def _on_event(self, event: Event) -> None:
        key = event.payload.get("workspace", "")
        if event.payload.get("going_down") or key not in self.held:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self.refresh(self.held[key]["cwd"], again=True)

    async def get(self, cwd: str, which: Literal["new", "held", "next"] = "new") -> dict[str, Any]:
        """The board of `cwd`.

        `new` waits for a read begun after this call, which asks `gh` anew. `held` answers with the
        last read and starts the next, so it waits only while nothing was read yet: what the page
        asks. `next` waits for the next read to end and starts none: what a tab
        that shows the board waits on. Every workspace has one read running at most.
        """
        self.ws.check(cwd)
        key = self.ws.key(cwd)
        if which == "next":
            await self.next_read(cwd)
            return self.held[key]["data"]
        kept = self.held.get(key) if which == "held" else None
        task = self.refresh(cwd, again=which == "new", fresh=which == "new")
        return kept["data"] if kept is not None else await asyncio.shield(task)

    async def warm(self) -> None:
        """Every listed workspace's board read once, so the first page opened finds it held. A
        first read waits on its workspace's open pull requests, so the board it holds has them."""
        cwds = [w["path"] for w in self.ws.all()["workspaces"] if not w.get("missing")]
        await asyncio.gather(*(self.refresh(c) for c in cwds), return_exceptions=True)

    # -- the held board ---------------------------------------------------------

    def refresh(self, cwd: str, again: bool = False, fresh: bool = False) -> asyncio.Task:
        """The read of `cwd` running now, or a new one: one per workspace at a time. With
        `again`, a read already running reads once more when it ends, so what the task
        returns was read after this call. A `fresh` read waits on `gh` anew (`read`). Its
        end is held in `held`. Once `stop` was called it is a read cancelled before it began,
        as every running read was."""
        key = self.ws.key(cwd)
        loop = asyncio.get_running_loop()
        if self._stopping:
            task = loop.create_task(asyncio.sleep(0))
            task.cancel()
            return task
        task = self.reads.get(key)
        if task is not None and not task.done() and task.get_loop() is loop:
            if again:
                self._again[key] = self._again.get(key, False) or fresh
            return task
        task = loop.create_task(self._read_held(cwd, key, fresh))
        self.reads[key] = task
        task.add_done_callback(lambda t: self._read_ended(key, t))
        return task

    def _prs_held(self, cwd: str, fresh: bool = False):
        """`open_prs` for `cwd`, answered from what `prs` holds: `gh` is waited on only when
        nothing is held yet, or when `fresh`. One ask per board read, however often it is awaited."""
        got: list[Any] = []

        async def prs():
            if not got:
                got.append(await self.prs.get((cwd, "prs"), lambda: self.open_prs(cwd), fresh))
            return got[0]

        return prs

    async def next_read(self, cwd: str) -> None:
        """Until the next read of `cwd` ends well, which leaves its board in `held`. Starts no
        read."""
        key = self.ws.key(cwd)
        loop = asyncio.get_running_loop()
        waiting = self._ended.get(key)
        if waiting is None or waiting.done() or waiting.get_loop() is not loop:
            waiting = self._ended[key] = loop.create_future()
        return await asyncio.shield(waiting)

    async def _read_held(self, cwd: str, key: str, fresh: bool) -> dict[str, Any]:
        while True:
            data = await self.read(cwd, fresh)
            self.held[key] = {"cwd": cwd, "data": data, "read_at": data["read_at"]}
            waiting = self._ended.pop(key, None)
            if waiting is not None and not waiting.done():
                waiting.set_result(None)
            if key not in self._again:
                return data
            fresh = self._again.pop(key)

    def _read_ended(self, key: str, task: asyncio.Task) -> None:
        if self.reads.get(key) is task:
            del self.reads[key]
            self._again.pop(key, None)
        if not task.cancelled() and task.exception() is not None:
            # Whoever waited was told; a read nobody waited for is only logged.
            log.warning("the board of %s could not be read: %s", key, task.exception())

    def changed(self, asked: tuple[str, ...]) -> None:
        """Something a read shows changed under `asked[0]`: read its board again, once one
        was read."""
        found = self.held.get(self.ws.key(asked[0]))
        if found is not None:
            self.refresh(found["cwd"], again=True)

    async def stop(self) -> list[tuple[str, asyncio.Task]]:
        """The board closed for the app going down: no read or removal starts from now on,
        every read running is cancelled, and every removal running is left to end as it would.
        Returns them all, each with what it is, for shutdown to wait on: taken before their
        ends drop them from `reads` and `_removing`."""
        self._stopping = True
        waited = [(f"board read of {key}", t) for key, t in self.reads.items()]
        for _label, t in waited:
            t.cancel()
        waited += [
            (f"tree removal of {unit} in {cwd}", t) for (cwd, unit), t in self._removing.items()
        ]
        return waited

    # -- board --------------------------------------------------------------

    async def read(self, cwd: str, fresh: bool = False) -> dict[str, Any]:
        """Every unit in this workspace, each with its eight stages and cost.

        The status of a stage comes from the artifact and the last run from the journal, and
        they are joined here rather than stored together. Storing them together is how a board
        starts disagreeing with the files it claims to describe.

        Waits on no network once the workspace's open pull requests were asked once: `gh` is
        answered from what is held and asked again in the background. A `fresh` read waits on
        those asks instead, for whoever needs the state as it is now. What reads `cos.db` runs
        off the event loop. One `log.info` line says how long each part took. The packs are
        looked at once for the whole read.
        """
        with pack.held():
            return await self._read(cwd, fresh)

    async def _read(self, cwd: str, fresh: bool) -> dict[str, Any]:
        self.ws.check(cwd)
        read_at = now()
        took: dict[str, float] = {}
        last = start = time.monotonic()

        def lap(part: str) -> None:
            nonlocal last
            took[part], last = time.monotonic() - last, time.monotonic()

        def _snapshot() -> dict[str, Any]:
            return self.ws.snapshot(cwd, peers=self.ws.peer_table()[0])

        state = await _in_thread(_snapshot)
        lap("snapshot")
        try:
            data = await board_reader.read(self.ws.units_root(cwd), state=state)
        except Unavailable as e:
            raise Invalid(str(e)) from e
        lap("loop")
        _brief_rounds(data["units"])
        data["read_at"] = read_at

        journal = self.ws.journal()
        key = self.ws.key(cwd)
        timelines: dict[str, list[dict[str, Any]]] = {}
        ranking: list[dict[str, Any]] = []
        if journal is not None:
            try:
                # One read for every unit's cost and the backlog's records. Asking `totals` per
                # unit re-scanned the working folder N times for the rows this already has.
                rows = await _in_thread(journal.records, key)
            except Busy as e:
                raise Invalid(str(e)) from e
            timelines = timelines_of(rows)
            ranking = [r for r in rows if r.get("kind") in backlog.KINDS]
        lap("run log")
        # Display only: nothing below reads it, and `next`/`blocked` are untouched.
        folded = backlog.fold(
            data["units"],
            ranking,
            backlog.measured(timelines, data["units"]),
            backlog.undetermined(timelines, data["units"]),
        )
        per_unit = folded.pop("per_unit")
        data["backlog"] = {**folded, "propose_warning": row_for("estimate").warning}
        for unit in data["units"]:
            unit["backlog"] = per_unit.get(unit["name"]) or {
                "rank": None,
                "value": None,
                "effort": None,
                "effort_source": None,
                "relations": [],
            }

        for unit in data["units"]:
            unit_last_runs = last_runs(timelines.get(unit["name"], []))
            for row in unit["stages"]:
                # From the same `timelines` read above, no second scan of the run log.
                # `status` stays read from the artifact alone; this is a separate field.
                row["last_run"] = unit_last_runs.get(row["stage"])
            unit["cost"] = totals_of(timelines.get(unit["name"], [])) if journal is not None else {}
            unit["attention_reason"] = attention_reason(unit)
            stage = str(unit.get("next_stage") or "")
            unit["missing"] = (
                contracts.missing(
                    stage,
                    self.ws.unit_dir(cwd, unit["name"]),
                    state["units"].get(f"{state['workspace']}/{unit['name']}"),
                )
                if stage
                else []
            )
            if unit["missing"]:
                unit["attention_reason"] = f"{stage} needs {' and '.join(unit['missing'])}"

        lap("fold")
        await self._attach_worktrees(cwd, data["units"])
        lap("worktree")
        # One held `gh pr list` for the whole read, asked only by whichever block needs it.
        prs = self._prs_held(cwd, fresh)
        ask_ci = await self.attach(cwd, data, journal, key, prs, fresh)
        lap("integration")
        for unit in data["units"]:
            # From the timelines read above: no second scan of the run log.
            rows = timelines.get(unit["name"], [])
            ended = [r for r in rows if r.get("ended") is not None]
            # What waits on a raised ceiling: the unit's own stage, whose latest run ended there.
            latest = paused_stage(rows, str(unit.get("at") or ""))
            unit["paused"] = {**latest, "stage": unit["at"]} if latest else None
            unit["state"] = unit_state(
                unit, ended[-1] if ended else None, unit.pop("ci_held", None)
            )

        # Started last and never awaited: their answers count from the next read, which
        # each answer starts.
        ask_ci()
        log.info(
            "board %s read in %.2fs: %s",
            key,
            time.monotonic() - start,
            ", ".join(f"{part} {s:.2f}s" for part, s in took.items()),
        )
        return data

    def running_here(self, key: str) -> dict[str, list[dict[str, Any]]]:
        """`running`'s `running`, from memory alone: what `guide_block` reads too."""
        running: dict[str, list[dict[str, Any]]] = {}
        for entry in self.unfinished(key):
            if entry["machine"] not in ("step", "integration", "estimate"):
                continue
            kind = (
                (entry["road"] or "rebase")
                if entry["machine"] == "integration"
                else entry["machine"]
            )
            row = None if kind == "rebase" else agents.agent_for(entry["stage"])
            agent = {"glyph": row["glyph"], "name": row["name"]} if row else None
            running.setdefault(entry["unit"], []).append(
                {
                    "kind": kind,
                    "stage": entry["stage"],
                    "agent": agent,
                    "started": entry["since"],
                    # `queued`, `preparing`, `running` or `ending`, and a Stop recorded on it.
                    "state": entry["state"],
                    "stopping": bool(entry["stop_asked_at"]),
                    "turns": None,
                    "cost_usd": None,
                    # A board step's events; `""` for an integration or an estimate.
                    "run": entry["run"] if entry["machine"] == "step" else "",
                }
            )
        return running

    def running(self, cwd: str) -> dict[str, Any]:
        """What has an agent working in this workspace now, and what ended unseen.

        `running` is this workspace's unfinished attempts of steps, integrations and estimates,
        one element per attempt, by unit.
        `unknown_end` is every `start` the run log holds without an `end` that no entry
        accounts for: the unit has nothing running here, no later `start` of the unit
        retired it, and it is younger than `UNKNOWN_END_FOR`. Matched by
        unit, not by session: an attempt allows one per unit, so a unit with an
        entry has no other `start` open in this process — only one another process wrote,
        and that one is shown as ended.

        Reads memory and the run log, nothing else: no `git`, no `gh`, no loop, and
        writes nothing. A busy run log is a `note`, not a refusal — the board asks this
        every few seconds, and a lock someone else holds must not break the board.
        """
        self.ws.check(cwd)
        key = self.ws.key(cwd)
        # Through the one lookup, so an override shows here too. Read once per call.
        running = self.running_here(key)
        out: dict[str, Any] = {"running": running, "unknown_end": {}}
        journal = self.ws.journal()
        if journal is None:
            return out
        try:
            opened = journal.open_starts(key)
        except Busy as e:
            out["note"] = str(e)
            return out
        oldest = datetime.now(timezone.utc) - UNKNOWN_END_FOR
        for unit, found in opened.items():
            if unit in running:
                continue
            rows = [
                # The name the `start` carries, or its stage's for an older one.
                {
                    "stage": r["stage"],
                    "started": r["started"],
                    "agent": agents.of_record(r),
                }
                for r in found["open"]
                if r.get("started")
                and r["started"] == found["last_start"]
                and younger_than(r["started"], oldest)
            ]
            if rows:
                out["unknown_end"][unit] = rows
        return out

    async def _attach_worktrees(self, cwd: str, units_: list[dict[str, Any]]) -> None:
        """Give every unit `worktree: {path, branch, prepare}`, or `None`.

        One `git worktree list` for the whole board. A `finished` unit that still has a tree
        is cleaned up from here, so a unit shipped at a terminal is cleaned up too — at the
        cost of a `gh pr view` (up to 30s) started in the background by **every** board read
        for as long as the tree stays, one per unit at a time, and never waited on: the
        read shows the tree, and the removal reads the board again. Nothing remembers a
        refusal (`gh` failing, the pull request not merged, the local branch off the merged
        head), so a transient `gh` error is retried rather than believed. A dirty tree is
        refused before `gh` is asked.
        """
        root = Path(cwd).expanduser().resolve()
        try:
            listed = (
                {str(Path(t["path"]).resolve()): t for t in await gitops.worktree_list(root)}
                if (root / ".git").exists()
                else {}
            )
        except GitError:
            listed = {}
        for u in units_:
            u["worktree"] = None
            try:
                where = worktrees.path(cwd, u["name"], self.config.data_dir)
            except BadUnit:
                continue
            if u.get("why") in ("finished", "rejected"):
                scratch.remove(cwd, u["name"], self.config.data_dir)
            found = listed.get(str(where))
            if found is None:
                continue
            if u.get("why") == "finished":
                self._remove_later(cwd, dict(u))
            u["worktree"] = {
                "path": str(where),
                "branch": found.get("branch") or "",
                "prepare": worktrees.read_prepare(where),
            }

    def _remove_later(self, cwd: str, unit: dict[str, Any]) -> None:
        slot = (cwd, unit["name"])
        if slot in self._removing or self._stopping:
            return

        async def remove() -> None:
            try:
                done = await worktrees.remove_if_finished(
                    cwd, unit["name"], unit, self.config.data_dir
                )
            except Exception:
                # A background removal never raises.
                log.exception("the tree of %s could not be removed", unit["name"])
                return
            if done.get("removed"):
                self.changed((cwd,))

        task = asyncio.get_running_loop().create_task(remove())
        self._removing[slot] = task
        task.add_done_callback(
            lambda t: self._removing.pop(slot, None) if self._removing.get(slot) is t else None
        )
