"""The board: every unit of a workspace with its stage, what is running on it and its worktree."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TypedDict

from coscc.units import backlog, prose_import
from coscc.agent import agents
from coscc.units import board as board_reader
from coscc.git import gitops
from coscc.units.board import Unavailable
from coscc.git.gitops import GitError
from coscc.runlog.journal import last_runs, timelines_of, totals_of
from coscc.data import Busy, now
from coscc.agent.policy import grant_for
from coscc import units
from coscc.units import scratch, worktrees
from coscc.units import BadUnit
from coscc.service.common import (
    Asked,
    open_prs_held,
    CONSEQUENCE,
    _younger_than,
    attention_reason,
    consequence,
    outcome_label,
    unit_state,
)
from coscc.kernel import Invalid
from coscc.config import Config
from coscc.service.workspaces import Workspaces
from coscc.service.common import Holds
from coscc.service.agents import Agents
from coscc.service.steps import HoldView
from coscc.service.release import Release
from coscc.service.steps import Steps

log = logging.getLogger(__name__)


# An `ended, unknown` row stops being shown this long after it began, unless a later `start`
# of the same unit retired it first.
UNKNOWN_END_FOR = timedelta(hours=24)


class CardState(TypedDict):
    state: str
    label: str
    color: str


class PullRequest(TypedDict):
    number: int
    url: str


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
    at: str
    attention_reason: str
    idea: str
    repo: str
    rank: int | None
    effort: str | None


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
        "attention_reason": str(u.get("attention_reason") or ""),
        "idea": str(u.get("idea") or ""),
        "repo": str(u.get("repo") or ""),
        "rank": backlog_.get("rank"),
        "effort": backlog_.get("effort"),
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
    mode: str
    last_run: LastRun | None


class Question(TypedDict):
    artifact: str
    n: int
    text: str
    answered: bool
    by: str


class Answer(TypedDict):
    artifact: str
    n: int
    question: str
    text: str
    by: str
    # Who decided: `person`, `delegated` or `agent-inferred`.
    authority: str
    via: str
    date: str


class Round(TypedDict):
    n: int
    verdict: str
    findings: int
    findings_open: int
    reviewed: str
    unfinished: bool


class Dependency(TypedDict):
    ref: str
    why: str
    merged: bool


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


class Worktree(TypedDict):
    branch: str
    path: str


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


def _text(v: Any) -> str:
    return "" if v is None else str(v)


def _number(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _count(v: Any) -> int | None:
    return int(v) if isinstance(v, int) and not isinstance(v, bool) else None


def detail(unit: Mapping[str, Any], timeline: Sequence[Mapping[str, Any]]) -> Detail:
    """`unit`, one unit of `Board.read`, with `timeline` (`Journal.timeline`) as a page shows it."""

    def last(r: Mapping[str, Any] | None) -> LastRun | None:
        if not r:
            return None
        return {
            "outcome": _text(r.get("outcome")),
            "ended": _text(r.get("ended")),
            "turns": _count(r.get("turns")),
            "cost_usd": _number(r.get("cost_usd")),
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
                "mode": _text(st.get("mode")),
                "last_run": last(st.get("last_run")),
            }
            for st in unit.get("stages") or []
        ],
        "questions": [
            {
                "artifact": _text(q.get("artifact")),
                "n": int(q.get("n") or 0),
                "text": _text(q.get("text")),
                "answered": bool(q.get("answered")),
                "by": _text(q.get("by")),
            }
            for q in unit.get("questions") or []
        ],
        "answers": [
            {
                "artifact": _text(a.get("artifact")),
                "n": int(a.get("n") or 0),
                "question": _text(a.get("question")),
                "text": _text(a.get("text")),
                "by": _text(a.get("by")),
                "authority": _text(a.get("authority")),
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
                "reviewed": _text(r.get("reviewed")),
                "unfinished": bool(r.get("unfinished")),
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
                "cost_usd": _number((r.get("cost") or {}).get("cost_usd"))
                if r.get("reported", True)
                else None,
                "turns": _count((r.get("cost") or {}).get("turns"))
                if r.get("turns_reported", True)
                else None,
                "run": _text(r.get("run")),
            }
            for r in timeline
        ],
        "worktree": {"branch": _text(tree.get("branch")), "path": _text(tree.get("path"))}
        if tree
        else None,
        "hold_moves": [str(m) for m in unit.get("hold_moves") or []],
    }


def waits_for(unit: dict[str, Any]) -> list[str]:
    """The units `impl` waits on, when the loop said it waits; else none."""
    if unit.get("why") != "dependency":
        return []
    return [d["ref"] for d in unit.get("depends_on") or [] if d.get("merged") is not True]


def _attach_comment_state(units_: list[dict[str, Any]], records: list[dict[str, Any]]) -> None:
    """Give every review round a `comment`: on the pull request, or not and why.

    Read off the run log, never stored beside the round: a `posted` or `already` row for
    the round means it is there. Anything else -- including a round written at a terminal,
    which has no row at all -- is *not on the PR*, with the latest failure's reason if any.
    """
    posted: dict[tuple[str, Any], str] = {}
    failed: dict[tuple[str, Any], str] = {}
    for r in records:
        k = (str(r.get("unit") or ""), r.get("round"))
        if r.get("outcome") in ("posted", "already"):
            posted[k] = str(r.get("comment_url") or "")
        elif r.get("outcome") == "failed":
            failed[k] = str(r.get("detail") or "")
    for u in units_:
        for rnd in u.get("rounds") or []:
            k = (u["name"], rnd.get("n"))
            rnd["comment"] = (
                {"posted": True, "url": posted[k], "reason": None}
                if k in posted
                else {"posted": False, "url": "", "reason": failed.get(k)}
            )


def _brief_rounds(units_: list[dict[str, Any]]) -> None:
    """Every review round without its text or its findings' text: what reads the whole text
    (an import, a comment, an impl's claim) reads it from the store, never from the board."""
    for u in units_:
        for rnd in u.get("rounds") or []:
            rnd.pop("text", None)
            for f in rnd.get("found") or []:
                f.pop("text", None)


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


def answerable(unit: dict[str, Any]) -> bool:
    """Whether the board invites an answer on this unit: not once it is finished, closed or
    dropped."""
    # `next`'s code, never its words.
    why = str(unit.get("why") or "")
    dropped = (unit.get("hold") or {}).get("state") == "dropped"
    return not (why in ("finished", "rejected") or dropped)


class Board:
    def __init__(
        self,
        config: Config,
        ws: Workspaces,
        holds: Holds,
        agents: Agents,
        release: Release,
        steps: Steps,
    ) -> None:
        self.config = config
        self.ws = ws
        self.holds = holds
        self.agents = agents
        self.release = release
        self.steps = steps
        # By journal key: the last board read, `{cwd, data, read_at}`; the one read running;
        # the keys a change came to while it ran, so it reads once more; and what waits for
        # the next read to end. This process only.
        self.held: dict[str, dict[str, Any]] = {}
        self.reads: dict[str, asyncio.Task] = {}
        self._again: dict[str, bool] = {}
        self._ended: dict[str, asyncio.Future] = {}
        # The open pull requests of each workspace, as `gh` last answered; an answer that
        # changed reads the board again, and so does a held release answer.
        self.prs = Asked(self._changed)
        self.release.details.changed = self._changed
        # A finished unit's tree being removed, by `(cwd, unit)`: never waited on by a read.
        self._removing: dict[tuple[str, str], asyncio.Task] = {}
        # Set by `stop`: the app is going down, and no read or removal starts any more.
        self._stopping = False

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

    def _changed(self, asked: tuple[str, ...]) -> None:
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

    async def _import_rounds(self, cwd: str, units_: list[dict[str, Any]]) -> None:
        """The review rounds only the prose of a store holds, into `cos.db`, on the
        first board read that finds the store unimported (`coscc/units/prose_import.py`). The
        board this read shows is the same either way. One that cannot write goes to the log and
        is tried on the next read. Its `cos.db` work runs off the event loop."""
        meta = self.ws.unit_meta()
        key = self.ws.key(cwd)
        try:
            if await _in_thread(meta.data.has_run, prose_import.key(meta.root, key)):
                return
            root = Path(cwd).expanduser().resolve()
            heads: dict[str, str] = {}
            for sha in {
                str(r.get("reviewed"))
                for u in units_
                for r in u.get("rounds") or []
                if r.get("reviewed")
            }:
                try:
                    heads[sha] = await gitops.rev_parse(root, sha)
                except GitError:
                    pass
            await _in_thread(prose_import.import_rounds, meta, key, units_, heads)
        except (Busy, sqlite3.Error, OSError) as e:
            log.warning("the review rounds of %s could not be imported: %s", key, e)

    async def read(self, cwd: str, fresh: bool = False) -> dict[str, Any]:  # noqa: PLR0915 - still to split
        """Every unit in this workspace, each with its eight stages, modes and cost.

        The status of a stage comes from the artifact and the mode comes from the journal,
        and they are joined here rather than stored together. Storing them together is how
        a board starts disagreeing with the files it claims to describe.

        Waits on no network once the workspace's open pull requests were asked once: `gh` is
        answered from what is held and asked again in the background. A `fresh` read waits on
        those asks instead, for whoever needs the state as it is now. What reads `cos.db` runs
        off the event loop. One `log.info` line says how long each part took.
        """
        self.ws.check(cwd)
        read_at = now()
        took: dict[str, float] = {}
        last = start = time.monotonic()

        def lap(part: str) -> None:
            nonlocal last
            took[part], last = time.monotonic() - last, time.monotonic()

        def _snapshot() -> tuple[dict[str, Any], list[str]]:
            peers, problems = self.ws.peer_table()
            return self.ws.snapshot(cwd, peers=peers), problems

        state, peer_problems = await _in_thread(_snapshot)
        lap("snapshot")
        try:
            data = await board_reader.read(self.ws.units_root(cwd), state=state)
        except Unavailable as e:
            raise Invalid(str(e)) from e
        lap("loop")
        await self._import_rounds(cwd, data["units"])
        # What the import read the whole text for; the board carries none of it.
        _brief_rounds(data["units"])
        lap("import")
        data["read_at"] = read_at
        # Only when there is something to say.
        if peer_problems:
            data["peer_problems"] = peer_problems
        # Each stage column's agent, by the one lookup, for the page to show only.
        # A stage the table has no row for is left out, and its column has no glyph.
        overrides = self.agents.agent_overrides()[0]
        data["stage_agents"] = {}
        for stage in data["stages"]:
            row = agents.agent_for(stage, overrides)
            if row is not None:
                data["stage_agents"][stage] = {
                    "glyph": row["glyph"],
                    "label": agents.label({**row, "key": stage}),
                    "meaning": row["meaning"],
                    "role": row["role"],
                }
        name = self.ws.name(cwd)
        for unit in data["units"]:
            if unit.get("repo") and name and unit["repo"] != name:
                unit["problems"] = [
                    *unit["problems"],
                    f"Repo: {unit['repo']} is not this workspace, {name}.",
                ]
            unit["waits_for"] = waits_for(unit)

        journal = self.ws.journal()
        key = self.ws.key(cwd)
        modes: dict[tuple[str, str], str] = {}
        timelines: dict[str, list[dict[str, Any]]] = {}
        comments: list[dict[str, Any]] = []
        ranking: list[dict[str, Any]] = []
        if journal is not None:
            try:
                # One read for every unit's cost, comment attempts and the backlog's
                # records. Asking `totals` per unit re-scanned the
                # working folder N times for the rows this already has.
                modes, rows = await _in_thread(lambda: (journal.modes(key), journal.records(key)))
            except Busy as e:
                raise Invalid(str(e)) from e
            timelines = timelines_of(rows)
            comments = [r for r in rows if r.get("kind") == "pr-comment"]
            ranking = [r for r in rows if r.get("kind") in backlog.KINDS]
        lap("run log")
        _attach_comment_state(data["units"], comments)
        # Display only: nothing below reads it, and `next`/`blocked` are untouched.
        folded = backlog.fold(
            data["units"],
            ranking,
            backlog.measured(timelines, data["units"]),
            backlog.undetermined(timelines, data["units"]),
        )
        per_unit = folded.pop("per_unit")
        data["backlog"] = {
            **folded,
            "propose_warning": grant_for("estimate").warning,
            "propose_consequence": CONSEQUENCE["estimate"],
        }
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
                # `manual` is the default because starting work is a decision someone has
                # to make, not one an unset value should make for them.
                row["mode"] = modes.get((unit["name"], row["stage"]), "manual")
                # The mode is a label; the grant follows the stage alone.
                grant = grant_for(row["stage"])
                # Carried to the page: what a step will be allowed to do has to be readable
                # before it is started.
                row["grants"] = list(grant.tools)
                row["warning"] = grant.warning
                row["consequence"] = consequence(row["stage"])
                # From the same `timelines` read above, no second scan of the run log.
                # `status` stays read from the artifact alone; this is a separate field.
                row["last_run"] = unit_last_runs.get(row["stage"])
            unit["cost"] = totals_of(timelines.get(unit["name"], [])) if journal is not None else {}
            # A label and nothing else: a deadline passing writes no row and
            # starts no step.
            unit["outcome_label"] = outcome_label(
                unit.get("outcome"), date.today(), finished=unit.get("why") == "finished"
            )
            # Decided here so the page only shows them.
            unit["answerable"] = answerable(unit)
            unit["attention_reason"] = attention_reason(unit)

        lap("fold")
        await self._attach_worktrees(cwd, data["units"])
        lap("worktree")
        # One held `gh pr list` for the whole read, asked only by whichever block needs it.
        prs = open_prs_held(self.prs, cwd, fresh)
        asks = await self.steps.attach_integration(cwd, data["units"], journal, key, prs, fresh)
        lap("integration")
        data["release"] = await self.release.attach_release(
            cwd, data["units"], journal, key, prs, fresh
        )
        lap("release")
        for unit in data["units"]:
            # From the timelines read above: no second scan of the run log.
            ended = [r for r in timelines.get(unit["name"], []) if r.get("ended") is not None]
            unit["state"] = unit_state(
                unit, ended[-1] if ended else None, unit.pop("ci_held", None)
            )

        data["recording"] = journal is not None
        data["read_only_because"] = (
            None
            if journal is not None
            else "no working folder is set, so nothing can be recorded — set COS_WORKING_DIR"
        )
        if not data["units"]:
            # A host repository can have a `.cos/` full of units the store never heard of, so
            # the page can say which directory it read and how many units sit in the other.
            # Counted on every call, never cached.
            data["empty"] = {
                "store": str(self.ws.units_root(cwd)),
                "host": units.key(cwd),
                "host_units": units.host_unit_count(cwd),
            }
        # Started last and never awaited: their answers count from the next read, which
        # each answer starts.
        self.steps.ask_ci(asks, ended=lambda tree: self._changed((tree,)))
        log.info(
            "board %s read in %.2fs: %s",
            key,
            time.monotonic() - start,
            ", ".join(f"{part} {s:.2f}s" for part, s in took.items()),
        )
        return data

    def running_here(self, key: str, overrides: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        """`running`'s `running`, from memory alone: what `guide_block` reads too."""
        running: dict[str, list[dict[str, Any]]] = {}
        for entry in self.holds.attempts.unfinished(key):
            if entry["machine"] not in ("step", "integration", "estimate"):
                continue
            kind = (
                (entry["road"] or "rebase")
                if entry["machine"] == "integration"
                else entry["machine"]
            )
            row = None if kind == "rebase" else agents.agent_for(entry["stage"], overrides)
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
        overrides = self.agents.agent_overrides()[0]
        running = self.running_here(key, overrides)
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
                    "agent": agents.of_record(r, overrides),
                }
                for r in found["open"]
                if r.get("started")
                and r["started"] == found["last_start"]
                and _younger_than(r["started"], oldest)
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
                self._changed((cwd,))

        task = asyncio.get_running_loop().create_task(remove())
        self._removing[slot] = task
        task.add_done_callback(
            lambda t: self._removing.pop(slot, None) if self._removing.get(slot) is t else None
        )
