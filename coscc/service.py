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
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from coscc import agents
from coscc import board as board_reader
from coscc import drift, fetches, gitops
from coscc import harness, integrate
from coscc import hold as hold_rules
from coscc import prcomment
from coscc import sessions as reader
from coscc.board import Unavailable
from coscc.config import Config
from coscc.data import Data, now as _now
from coscc.gitops import GitError
from coscc.history import UNKNOWN, BadTransition, History, settled_edits
from coscc.journal import (
    COST_FIELDS,
    COST_USD,
    BadRecord,
    Busy,
    Journal,
    add_cost,
    last_runs,
    totals_of,
    zero_cost,
)
from coscc.policy import GRANTS, NOVEL_CEILINGS, PROSE_STAGES, grant_for, grant_for_step
from coscc import labels, models
from coscc.runner import SESSIONS_PER_STEP, STATUS_RE, RunError, Runner, describe_attempt
from coscc.sessions import Sessions
from coscc.store import BadName, Store, require_name
from coscc import steps as steps_mod
from coscc import units, updater as updater_mod, worktrees
from coscc.units import BadUnit, CannotCreate

# The eight stage names, in stage order. Taken from the stage list the board reports rather
# than written again here would be better; the board read is async and this method is not,
# so the names are repeated and this comment is the warning.
STAGE_FILES = ("idea", "intent", "spec", "plan", "impl", "pr", "review", "ship")

# Where a unit's branch is cut from: the trunk as this remote has it. Constants, not
# request fields — a caller cannot point the fetch at another remote or another branch.
BRANCH_REMOTE = "origin"
BRANCH_TRUNK = gitops.TRUNK

# `0051` spec, answer 4: an `ended, unknown` row stops being shown this long after it began,
# unless a later `start` of the same unit retired it first.
UNKNOWN_END_FOR = timedelta(hours=24)


class Invalid(Exception):
    """A request this layer refuses, carrying a reason a caller can show verbatim."""


class Updating(Invalid):
    """`0068` R11: refused because the app is in the seconds before it restarts. A 503."""


class NotUpdatable(Invalid):
    """`0068` R2: this install is not the shape an update can be applied to. A 409."""


class StaleCutList(Invalid):
    """`0068` R10: the list a person confirmed is not the list running now."""

    def __init__(self, message: str, listing: dict[str, Any]):
        super().__init__(message)
        self.listing = listing


def _as_invalid(e: updater_mod.Refused) -> Invalid:
    if isinstance(e, updater_mod.Stale):
        return StaleCutList(str(e), e.listing)
    if isinstance(e, updater_mod.Updating):
        return Updating(str(e))
    if isinstance(e, updater_mod.NotHere):
        return NotUpdatable(str(e))
    return Invalid(str(e))


def _younger_than(at: str, oldest: datetime) -> bool:
    """Whether a run-log `at` is after `oldest`. One that will not parse is not shown."""
    try:
        when = datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when > oldest


def _attach_comment_state(units_: list[dict[str, Any]], records: list[dict[str, Any]]) -> None:
    """`0021` D4. Give every review round a `comment`: on the pull request, or not and why.

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


def step_cwd(stage: str, work: str, directory: Path, spike_dir: str | None = None) -> str:
    """Where a step's session runs. The unit's worktree, except for `ship` and `spike`.

    `spike` (`0039` R11) runs in `spike_dir`, a throwaway directory under the data root:
    its probe code must never land in the worktree whose branch it would then ride.

    `ship` runs `gh pr merge --squash --delete-branch`, and inside a worktree that command
    fails after it has already merged. Measured 2026-09-23 on `baodq97/coscc-proof` with gh
    2.93.0, `main` checked out at the root and the branch in a worktree: the pull request
    went to `MERGED`, then gh tried to switch the worktree to `main`, git answered
    `fatal: 'main' is already used by worktree`, and gh exited 1 -- with the remote branch
    and the local branch both left behind. A step reading that exit code reports a failed
    merge for a pull request that merged.

    The same command with the pull request's URL, run from a directory that is not a git
    checkout, exited 0, merged, and deleted the remote branch. The unit's directory in the
    store is such a directory -- the store has no git (`coscc/units.py`) -- and `ship` writes
    `ship.md` there anyway. The worktree and the local branch are then removed by
    `worktrees.remove_if_finished`, which already waits for GitHub to say `MERGED`.

    The gates still read `work`: only the session moves.
    """
    if stage == "spike" and spike_dir:
        return spike_dir
    return str(directory) if stage == "ship" else work


def describe_base(base: dict[str, Any] | None) -> str:
    """The one sentence saying a step's base may be stale, or `""` when it is fresh.

    `0030_a-unit-branch-starts-from-a-stale-main`. `state.py` and this module's own prompt
    (`runner.build_prompt`, *The base this step runs on*) both call this rather than each
    writing the sentence its own way — the same reason `.claude/CLAUDE.md` gives for
    `cos.mjs` being the one place the loop is defined, at a much smaller scale.
    """
    if not base or base.get("fresh", True):
        return ""
    sha = base.get("sha") or "?"
    ref = base.get("ref") or f"{BRANCH_REMOTE}/{BRANCH_TRUNK}"
    reason = base.get("reason") or ""
    return f"This step ran on {ref} at {sha}, which may be stale: {reason}"


def integration_since_review(journal: Journal, key: str, unit: str) -> dict[str, Any] | None:
    """`0035` R10: the latest `pushed` integration recorded after the last `review` step
    that ended `done`, or None. Read by id order, which is the order the rows were written."""
    try:
        rows = journal.records(key, unit)
    except Busy:
        return None
    found = None
    for rec in rows:
        if rec.get("kind") == "integration" and rec.get("outcome") == "pushed":
            found = rec
        elif rec.get("kind") == "end" and rec.get("stage") == "review" and rec.get("outcome") == "done":
            found = None
    return found


# `0047`. The three results a `### Outcome` block may carry, as a person types them, and the
# word `cos.mjs` `parseOutcome` reads each one as.
OUTCOME_RESULTS = {"đạt": "met", "trượt": "missed", "không đo được": "unmeasurable"}
# `0047` spec, Answers, câu 5: a line for the board to show on a missed outcome, no action.
MISSED_HINT = "cân nhắc bỏ hoặc làm lại"


def outcome_label(outcome: dict[str, Any] | None, today: date, finished: bool = False) -> dict[str, Any] | None:
    """`0047` R8. What the board shows for one unit's outcome, or None for no label.

    `outcome` is what `board._outcome_of` copied from `cos.mjs`; nothing here reads a block.
    `today` is a parameter so the deadline branch is testable without a clock. `counted` is
    whether the unit has a result in the sense of the intent's outcome: `không đo được` is
    shown on its own but is not one (`0047` intent, Answers, câu 3). `form` is whether the
    board offers to record one — only on a finished unit, the one `record_outcome` accepts.
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
    }


@dataclass
class Service:
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
    # `0034`. The board steps running now, each as its own task, so a reader that goes
    # away does not take the step with it and a Stop has something to cancel.
    steps: steps_mod.Registry = field(default_factory=lambda: steps_mod.Registry(), init=False, repr=False)

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

    # -- workspaces ---------------------------------------------------------

    def workspaces(self) -> dict[str, Any]:
        """Both sources, with the count the app could not answer before the store existed.

        `source` is carried per entry rather than merged away: an env workspace cannot be
        renamed or removed from here, and a caller has to be able to tell.
        """
        rows: list[dict[str, Any]] = []
        for path in self.config.workspaces:
            rows.append(
                {
                    "name": Path(path).name,
                    "path": path,
                    "label": "",
                    "source": "env",
                    "missing": not Path(path).expanduser().is_dir(),
                }
            )
        if self.store is not None:
            for entry in self.store.entries():
                target = self.store.path_of(entry.name)
                rows.append(
                    {
                        "name": entry.name,
                        "path": str(target),
                        "label": entry.label,
                        "source": "store",
                        "missing": not target.is_dir(),
                    }
                )
        return {
            "working_dir": self.config.working_dir,
            "count": len(rows),
            "workspaces": rows,
            # Kept so the original shape still reads: it only ever asked for paths.
            "paths": [r["path"] for r in rows],
        }

    # -- changing the list --------------------------------------------------

    def _store_or_refuse(self) -> Store:
        if self.store is None:
            raise Invalid(
                "no working folder configured; set COS_WORKING_DIR and restart "
                "(it is deliberately not settable over HTTP)"
            )
        return self.store

    def _name_or_refuse(self, name: str) -> str:
        try:
            return require_name(name)
        except BadName as e:
            raise Invalid(str(e)) from e

    def _row(self, name: str, label: str) -> dict[str, Any]:
        store = self._store_or_refuse()
        target = store.path_of(name)
        return {
            "name": name,
            "path": str(target),
            "label": label,
            "source": "store",
            "missing": not target.is_dir(),
        }

    async def add_workspace(
        self, name: str, label: str = "", repo_url: str | None = None
    ) -> dict[str, Any]:
        """Add by adopting a directory already under the root, or by cloning into it.

        Both are `intent.md`'s scope line, where "thêm" and "clone" are separate entries.

        Order matters and is the whole of `spec.md` R16: clone into a temp directory,
        rename into place, and only then write the store. The worst state a failure can
        leave is a temp directory nobody cleaned — never a listed workspace that does not
        work.
        """
        store = self._store_or_refuse()
        self._name_or_refuse(name)
        if any(e.name == name for e in store.entries()):
            raise Invalid(f"workspace already exists: {name}")

        target = store.path_of(name)
        if repo_url:
            if target.exists():
                raise Invalid(f"directory already exists: {target}")
            store.working_dir.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(dir=store.working_dir, prefix=".cos-clone-"))
            try:
                await gitops.clone(repo_url, staging / name)
                os.replace(staging / name, target)
            except GitError as e:
                raise Invalid(str(e)) from e
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        elif not target.is_dir():
            raise Invalid(f"no such directory under the working folder: {target}")

        entry = store.add(name, label)
        return self._row(entry.name, entry.label)

    def set_label(self, name: str, label: str) -> dict[str, Any]:
        store = self._store_or_refuse()
        self._name_or_refuse(name)
        try:
            entry = store.set_label(name, label)
        except KeyError as e:
            raise Invalid(f"no such workspace: {name}") from e
        return self._row(entry.name, entry.label)

    def remove_workspace(self, name: str) -> dict[str, Any]:
        """Drops the entry only. The directory stays — `spec.md` R18 and C6."""
        store = self._store_or_refuse()
        self._name_or_refuse(name)
        try:
            store.remove(name)
        except KeyError as e:
            raise Invalid(f"no such workspace: {name}") from e
        return {"removed": name, "count": len(self.workspaces()["workspaces"])}

    async def pull_workspace(self, name: str) -> dict[str, Any]:
        """Fast-forward only. A failure comes back with its output — `spec.md` R20.

        Refused outright while a session is live here (`spec.md` R6). The refusal is an
        `Invalid` like every other reason a pull fails, so it reaches the page through the
        path R20 already built rather than through one of its own — R8.
        """
        store = self._store_or_refuse()
        self._name_or_refuse(name)
        if not any(e.name == name for e in store.entries()):
            raise Invalid(f"no such workspace: {name}")
        target = store.path_of(name)
        if not target.is_dir():
            raise Invalid(f"workspace directory is missing: {target}")
        # R6, and this has to come before `gitops`: a fast-forward rewrites files
        # under a turn that is already reading them, and the turn cannot be told. The
        # answer covers this process only (`spec.md` C2) — a second app holding a session
        # here is not seen, and the pull will go ahead.
        live = self.sessions.live_in(str(target))
        if live:
            raise Invalid(
                f"workspace {name} has {len(live)} live session(s) — "
                "pull would change files under them. Finish or reload, then try again."
            )
        try:
            output = await gitops.pull(target)
        except GitError as e:
            raise Invalid(str(e)) from e
        return {"name": name, "output": output}

    # -- board --------------------------------------------------------------

    def _journal(self) -> Journal | None:
        """The run log, or `None` when there is no working folder to keep it in.

        Unset `COS_WORKING_DIR` and the app behaves as it did before the store — which now also means
        the board is read-only: there is nowhere to record a mode, so every step reads
        `manual` and nothing can be started. That is the safe direction to fail in.
        """
        return (
            Journal(self.config.working_dir, self.config.data_dir)
            if self.config.working_dir
            else None
        )

    # `0050`. Check-and-mark with no `await` in any of these, so nothing on the event loop
    # can come between the look and the write.
    def _busy(self, key: str, unit: str) -> str:
        """What holds this unit, in the one sentence every refusal carries, or `""`."""
        mark = self._active.get((key, unit))
        return steps_mod.describe(unit, mark) if mark is not None else ""

    def _take(self, key: str, unit: str, kind: str, stage: str = "") -> steps_mod.Mark:
        said = self._busy(key, unit)
        if said:
            raise Invalid(said)
        mark = steps_mod.Mark(kind, stage, "preparing" if kind == "step" else "")
        self._active[(key, unit)] = mark
        return mark

    def _release(self, key: str, unit: str, mark: steps_mod.Mark) -> None:
        """Only this mark: a refused or late caller never frees a unit someone else holds."""
        if self._active.get((key, unit)) is mark:
            del self._active[(key, unit)]

    @staticmethod
    def _journal_key(cwd: str) -> str:
        """How a workspace is named in the journal.

        The resolved path, not a store name: an env-declared workspace has no name at all
        (`config.is_workspace`), and a path is the one identifier both kinds have. The
        cost is that moving a workspace detaches its history from it.
        """
        return str(Path(cwd).expanduser().resolve())

    def _units_root(self, cwd: str) -> Path:
        """Where this workspace's units live. One question, asked of one module.

        `coscc/units.py` owns the answer; this is the only place in the service that asks.
        """
        return units.root(cwd, self.config.data_dir)

    def _unit_dir(self, cwd: str, unit: str) -> Path:
        try:
            return units.unit_dir(cwd, unit, self.config.data_dir)
        except BadUnit as e:
            raise Invalid(str(e)) from e

    async def board(self, cwd: str) -> dict[str, Any]:
        """Every unit in this workspace, each with its eight stages, modes and cost.

        The status of a stage comes from the artifact and the mode comes from the journal,
        and they are joined here rather than stored together. Storing them together is how
        a board starts disagreeing with the files it claims to describe.
        """
        self._workspace_or_refuse(cwd)
        try:
            data = await board_reader.read(self._units_root(cwd))
        except Unavailable as e:
            raise Invalid(str(e)) from e

        journal = self._journal()
        key = self._journal_key(cwd)
        modes: dict[tuple[str, str], str] = {}
        timelines: dict[str, list[dict[str, Any]]] = {}
        comments: list[dict[str, Any]] = []
        if journal is not None:
            try:
                modes = journal.modes(key)
                # One read for every unit's cost. Asking `totals` per unit re-scanned the
                # working folder N times for the rows this already has.
                timelines = journal.timelines(key)
                # `0021` D4. One read for every unit's comment attempts, too.
                comments = journal.records(key, kind="pr-comment")
            except Busy as e:
                raise Invalid(str(e)) from e
        _attach_comment_state(data["units"], comments)

        for unit in data["units"]:
            unit_last_runs = last_runs(timelines.get(unit["name"], []))
            for row in unit["stages"]:
                # `manual` is the default because starting work is a decision someone has
                # to make, not one an unset value should make for them.
                row["mode"] = modes.get((unit["name"], row["stage"]), "manual")
                # The mode is a label since `0020`; the grant follows the stage alone.
                grant = grant_for(row["stage"])
                # Carried to the page so `spec.md` C4 can be met where the button is: what
                # a step will be allowed to do has to be readable before it is started.
                row["grants"] = list(grant.tools)
                row["warning"] = grant.warning
                # `0019` plan step 6 / `spec.md` R5. From the same `timelines` read above —
                # no second scan of the run log. `status` (and the lanes) stays read from
                # the artifact alone (C6); this is a second, separate field.
                row["last_run"] = unit_last_runs.get(row["stage"])
            unit["cost"] = (
                totals_of(timelines.get(unit["name"], [])) if journal is not None else {}
            )
            # `0047` R8, R9. A label and nothing else: a deadline passing writes no row and
            # starts no step.
            unit["outcome_label"] = outcome_label(
                unit.get("outcome"), date.today(), finished=unit.get("next") == "finished"
            )

        await self._attach_worktrees(cwd, data["units"])
        await self._attach_integration(cwd, data["units"], journal, key)

        data["recording"] = journal is not None
        data["read_only_because"] = (
            None if journal is not None
            else "no working folder is set, so nothing can be recorded — set COS_WORKING_DIR"
        )
        if not data["units"]:
            # `0001_product-describes-a-state-it-is-not-in` R6, R7, R8. The board reads the
            # store, and a host repository can have a `.cos/` full of units the store never
            # heard of. The page has to be able to say which directory it read and how many
            # units sit in the one it did not. Counted on every call: R8 forbids a cache.
            data["empty"] = {
                "store": str(self._units_root(cwd)),
                "host": units.key(cwd),
                "host_units": units.host_unit_count(cwd),
            }
        return data

    def _mark_running(self, key: str, unit: str, stage: str, kind: str) -> str:
        """`0051` R1. Put one entry in `_running` and return its id, for the `finally` to pop.

        `started` is stamped by the same clock `Journal.append` uses, so it reads like an
        `at`. `turns` and `cost_usd` stay `None` while the session runs (R5).
        """
        rid = uuid.uuid4().hex
        self._running[rid] = {
            "workspace": key, "unit": unit, "stage": stage, "started": _now(),
            "kind": kind, "turns": None, "cost_usd": None,
        }
        return rid

    def running(self, cwd: str) -> dict[str, Any]:
        """`0051` R2. What has an agent working in this workspace now, and what ended unseen.

        `running` is `_running` for this workspace, one element per entry, by unit.
        `unknown_end` is every `start` the run log holds without an `end` that no entry
        accounts for (R6): the unit has nothing running here, no later `start` of the unit
        retired it, and it is younger than `UNKNOWN_END_FOR` (spec, answer 4). Matched by
        unit, not by session: `_active` allows one per unit per process, so a unit with an
        entry has no other `start` open in this process — only one another process wrote,
        and that one is shown as ended (spec C2, answer 3).

        Reads memory and the run log, nothing else: no `git`, no `gh`, no `cos.mjs`, and
        writes nothing. A busy run log is a `note`, not a refusal — the board asks this
        every few seconds, and a lock someone else holds must not break the board.
        """
        self._workspace_or_refuse(cwd)
        key = self._journal_key(cwd)
        running: dict[str, list[dict[str, Any]]] = {}
        for entry in self._running.values():
            if entry["workspace"] != key:
                continue
            kind = entry["kind"]
            agent = None if kind == "rebase" else agents.agent_for(entry["stage"])
            running.setdefault(entry["unit"], []).append({
                "kind": kind, "stage": entry["stage"], "agent": agent,
                "started": entry["started"], "turns": entry["turns"], "cost_usd": entry["cost_usd"],
            })
        out: dict[str, Any] = {"running": running, "unknown_end": {}}
        journal = self._journal()
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
                {"stage": r["stage"], "started": r["started"]}
                for r in found["open"]
                if r.get("started") and r["started"] == found["last_start"]
                and _younger_than(r["started"], oldest)
            ]
            if rows:
                out["unknown_end"][unit] = rows
        return out

    async def _attach_worktrees(self, cwd: str, units_: list[dict[str, Any]]) -> None:
        """`0017`. Give every unit `worktree: {path, branch, prepare}`, or `None`.

        One `git worktree list` for the whole board. A `finished` unit that still has a tree
        is cleaned up here (R10), so a unit shipped at a terminal is cleaned up too — at the
        cost of a `gh pr view` (up to 30s) on **every** board read for as long as the tree
        stays: once, when the removal succeeds; on each read after, when it does not
        (`gh` failing, the pull request not merged, the local branch off the merged head).
        Nothing remembers a refusal, so a transient `gh` error is retried rather than
        believed. A dirty tree is refused before `gh` is asked. (Plan Risk 7; `0017`
        review F4 — this docstring said "the first time" until then.)
        """
        root = Path(cwd).expanduser().resolve()
        try:
            listed = {
                str(Path(t["path"]).resolve()): t
                for t in await gitops.worktree_list(root)
            } if (root / ".git").exists() else {}
        except GitError:
            listed = {}
        for u in units_:
            u["worktree"] = None
            try:
                where = worktrees.path(cwd, u["name"], self.config.data_dir)
            except BadUnit:
                continue
            found = listed.get(str(where))
            if found is None:
                continue
            if u.get("next") == "finished":
                done = await worktrees.remove_if_finished(cwd, u["name"], u, self.config.data_dir)
                if done.get("removed"):
                    continue
            u["worktree"] = {
                "path": str(where),
                "branch": found.get("branch") or "",
                "prepare": worktrees.read_prepare(where),
            }

    # -- integration (`0035`) -------------------------------------------------

    async def _attach_integration(
        self, cwd: str, units_: list[dict[str, Any]], journal: Journal | None, key: str
    ) -> None:
        """R1/R2. Give every unit `integration: {...}` when it sits in the window, else None.

        **Reads only.** One `gh pr list` for the workspace (up to `integrate.GH_TIMEOUT`),
        `git` counts against the `origin/main` the last fetch brought — no fetch here — and
        `gh pr checks` only for a unit whose head is the one its last integration pushed.
        Nothing here writes a record, calls `update-branch` or opens a session.
        """
        for u in units_:
            u["integration"] = None
        window = [u for u in units_ if u.get("between_pr_and_ship") and u.get("pr")]
        if not window:
            return
        root = Path(cwd).expanduser().resolve()
        last = self._last_integrations(journal, key)
        try:
            prs: list[dict[str, Any]] | str = await integrate.open_prs(str(root))
        except integrate.IntegrateError as e:
            prs = str(e)
        for u in window:
            info = await self._integration_of(root, u, prs, last.get(u["name"]))
            if info is not None:
                u["integration"] = info

    @staticmethod
    def _last_integrations(journal: Journal | None, key: str) -> dict[str, dict[str, Any]]:
        if journal is None:
            return {}
        try:
            rows = journal.records(key, kind="integration")
        except Busy:
            return {}
        return {str(r.get("unit")): r for r in rows}

    async def _integration_of(
        self, root: Path, u: dict[str, Any], prs: list[dict[str, Any]] | str,
        last_record: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """One unit's state. None when its pull request is not among the open ones."""
        number = (u.get("pr") or {}).get("number")
        if isinstance(prs, str):
            pr_row: dict[str, Any] | str = prs
        else:
            match = next((r for r in prs if r.get("number") == number), None)
            if match is None:
                return None
            pr_row = match
        origin_sha = ""
        missing: int | str = 0
        if isinstance(pr_row, dict):
            try:
                origin_sha = await gitops.rev_parse(root, "refs/remotes/origin/main")
                head = str(pr_row.get("headRefOid") or "")
                if not await gitops.has_commit(root, head):
                    missing = f"the pull request's head {head[:7]} is not here: fetch, then ask again"
                else:
                    missing = await gitops.count_missing(root, head, origin_sha)
            except GitError as e:
                missing = str(e)
        checks: list[dict[str, Any]] | str | None = None
        if integrate.needs_checks(pr_row, last_record):
            try:
                checks = await integrate.required_checks(str(root), int(number))
            except integrate.IntegrateError as e:
                checks = str(e)
        verdict = integrate.classify(pr_row, missing, origin_sha, last_record, checks)
        state = verdict["state"]
        review_status = next((r.get("status") or "" for r in u.get("stages") or [] if r.get("stage") == "review"), "")
        gebo = state in integrate.GEBO_STATES
        return {
            "state": state,
            "reason": verdict.get("reason", ""),
            "behind": missing if isinstance(missing, int) else None,
            "origin_sha": origin_sha,
            "pr_head": pr_row.get("headRefOid", "") if isinstance(pr_row, dict) else "",
            "mode": "agent" if gebo else ("mechanical" if state == "behind" else ""),
            "button": state in integrate.BUTTON_STATES,
            "needs_person": list((last_record or {}).get("needs_person") or [])
            if (last_record or {}).get("outcome") == "needs-person" else [],
            "warnings": integrate.warnings(
                u.get("rounds") or [], review_status, gebo, grant_for("integrate").warning
            ),
        }

    async def integrate(self, cwd: str, unit: str) -> AsyncIterator[tuple[str, Any]]:
        """`0035`. Integrate one unit, on a person's request. Streams like `run_step`.

        Refuses before anything changes (R12), and every refusal, push or failure leaves one
        `integration` record (R9). `behind` goes the mechanical road (R4); `conflicting`
        and `red-after-integration` open Gebo (R5).
        """
        self._workspace_or_refuse(cwd)
        self._refuse_while_updating()
        journal = self._journal()
        if journal is None:
            raise Invalid("no working folder is set, so an integration cannot be recorded — set COS_WORKING_DIR")
        if not unit:
            raise Invalid("name a work unit")
        directory = self._unit_dir(cwd, unit)
        try:
            data = await board_reader.read(self._units_root(cwd))
        except Unavailable as e:
            raise Invalid(str(e)) from e
        found = next((u for u in data["units"] if u["name"] == unit), None)
        if found is None:
            raise Invalid(f"no such work unit in this workspace: {unit}")
        key = self._journal_key(cwd)
        root = Path(cwd).expanduser().resolve()
        last = self._last_integrations(journal, key).get(unit)
        info = None
        if found.get("between_pr_and_ship") and found.get("pr"):
            try:
                prs: list[dict[str, Any]] | str = await integrate.open_prs(str(root))
            except integrate.IntegrateError as e:
                prs = str(e)
            info = await self._integration_of(root, found, prs, last)
        pr = (found.get("pr") or {}).get("number")
        state = (info or {}).get("state", "")
        pr_head = (info or {}).get("pr_head", "")
        origin_sha = (info or {}).get("origin_sha", "")
        try:
            branch = units.branch_name(cwd, unit, self.config.data_dir)
        except (CannotCreate, BadUnit):
            branch = ""
        tree_found = None
        try:
            tree_found = await worktrees.find(cwd, unit, self.config.data_dir)
        except (GitError, BadUnit):
            tree_found = None
        tree = Path(tree_found["path"]) if tree_found else None

        def write(rec: dict[str, Any]) -> dict[str, Any]:
            try:
                return journal.append(rec)
            except (BadRecord, Busy):
                return rec

        lock = self._integrate_locks.setdefault(key, asyncio.Lock())
        async with lock:
            clean = on_branch = None
            local_head = ""
            if tree is not None:
                try:
                    clean = await gitops.is_clean(tree)
                    on_branch = bool(branch) and (await gitops.current_branch(tree)) == branch
                    local_head, _ = await gitops.head_and_branch(tree)
                except GitError:
                    clean = on_branch = None
            reason = integrate.refusal(
                in_window=info is not None, busy=self._busy(key, unit),
                clean=clean, branch_ok=on_branch, local_head=local_head, pr_head=pr_head, state=state,
            )
            if reason:
                write(integrate.record(
                    workspace=key, unit=unit, pr=pr, mode=(info or {}).get("mode") or "mechanical",
                    head_before=pr_head, head_after="", origin_sha=origin_sha, outcome="refused",
                    detail=reason,
                ))
                raise Invalid(reason)
            mark = self._take(key, unit, "integrate")
            # `0051` spec, answer 1: Gebo shows as running under its agent name; a mechanical
            # rebase has no agent and shows as rebasing. The same condition as below.
            rid = self._mark_running(key, unit, "integrate", "rebase" if state == "behind" else "gebo")
        try:
            assert tree is not None
            if state == "behind":
                rec = await self._integrate_mechanical(key, unit, int(pr), tree, branch, pr_head, origin_sha)
                write(rec)
                yield ("done", {"integration": rec})
                return
            async for item in self._integrate_gebo(
                cwd, key, unit, directory, found, data, info, int(pr), tree, branch, pr_head, origin_sha,
                journal, write,
            ):
                yield item
        finally:
            self._release(key, unit, mark)
            self._running.pop(rid, None)
            self.updater.job_ended()

    async def _integrate_mechanical(
        self, key: str, unit: str, pr: int, tree: Path, branch: str, head_before: str, origin_sha: str
    ) -> dict[str, Any]:
        """R4. GitHub rebases, the local branch follows. No session."""
        base = dict(workspace=key, unit=unit, pr=pr, mode="mechanical", head_before=head_before, origin_sha=origin_sha)
        try:
            ok, said = await integrate.update_branch(str(tree), pr)
        except integrate.IntegrateError as e:
            return integrate.record(**base, head_after="", outcome="failed", detail=str(e))
        if not ok:
            return integrate.record(**base, head_after="", outcome="refused", detail=said or "gh refused")
        head_after = head_before
        for attempt in range(integrate.POLL_TRIES):
            try:
                head_after = await integrate.pr_head(str(tree), pr)
            except integrate.IntegrateError:
                head_after = head_before
            if head_after and head_after != head_before:
                break
            if attempt + 1 < integrate.POLL_TRIES:
                await asyncio.sleep(integrate.POLL_DELAY)
        if not head_after or head_after == head_before:
            return integrate.record(
                **base, head_after="", outcome="failed",
                detail="GitHub accepted the command but the head has not changed yet",
            )
        try:
            await gitops.reset_branch_to(tree, branch, head_before, head_after)
            detail = said
        except GitError as e:
            # The push happened on GitHub's side either way; the local tree is behind it.
            detail = f"pushed on GitHub, but the local branch was not moved: {e}"
        return integrate.record(**base, head_after=head_after, outcome="pushed", detail=detail)

    async def _integrate_gebo(
        self, cwd: str, key: str, unit: str, directory: Path, found: dict[str, Any],
        data: dict[str, Any], info: dict[str, Any], pr: int, tree: Path, branch: str,
        head_before: str, origin_sha: str, journal: Journal, write: Any,
    ) -> AsyncIterator[tuple[str, Any]]:
        """R5–R8. One Gebo session; the outcome is read from GitHub afterwards."""
        root = Path(cwd).expanduser().resolve()
        rel = await self._related(root, unit, data, head_before, origin_sha)
        units_root = self._units_root(cwd)
        own = {}
        for name in ("intent.md", "spec.md", "plan.md", "impl.md"):
            path = directory / name
            if path.exists():
                own[name] = path.read_text(encoding="utf-8", errors="replace")
        try:
            skill = harness.read_skill("integrate")
        except harness.MissingRules as e:
            raise Invalid(f"the integrate skill could not be read: {e}") from e
        prompt = integrate.build_prompt(
            skill=skill, unit=unit, branch=branch, pr=pr, state=info["state"], reason=info.get("reason", ""),
            head_before=head_before, origin_sha=origin_sha, rel=rel, units_root=units_root, own_artifacts=own,
        )
        grant = grant_for("integrate")
        model, model_source = self._model_for("impl")
        try:
            journal.started(key, unit, "integrate", "manual", prompt_chars=len(prompt), granted=list(grant.tools),
                            max_turns=grant.max_turns, head=head_before, model=model, model_source=model_source)
        except (BadRecord, Busy):
            pass
        end: dict[str, Any] = {}
        failure = ""
        try:
            async for kind, payload in integrate.run_gebo(
                self.sessions, tree=str(tree), workspace=cwd, prompt=prompt, grant=grant,
                read_also=integrate.read_paths(units_root, unit, rel), lease=(branch, head_before), model=model,
            ):
                if kind == "chunk":
                    yield ("chunk", payload)
                else:
                    end = payload
        except Exception as e:  # noqa: BLE001 — recorded, never swallowed silently
            failure = f"the session failed: {e}"
        details = [failure] if failure else []
        try:
            if await gitops.rebase_in_progress(tree):
                await gitops.abort_rebase(tree)
                details.append("the session left a rebase in progress; the app aborted it")
        except GitError as e:
            details.append(f"could not check for a stopped rebase: {e}")
        try:
            head_now = await integrate.pr_head(str(tree), pr)
        except integrate.IntegrateError as e:
            head_now = head_before
            details.append(f"could not read the pull request's head afterwards: {e}")
        reply = str(end.get("reply") or "")
        outcome = integrate.outcome_of_session(head_before, head_now, reply)
        try:
            journal.finished(
                key, unit, "integrate", "done" if outcome in ("pushed", "needs-person") else "failed",
                session_id=end.get("session_id", ""), detail="; ".join(details) or None,
                denials=end.get("denials", 0), denied=end.get("denied"),
                models_used=end.get("models_used") or None, **(end.get("cost") or {}),
            )
        except (BadRecord, Busy):
            pass
        rec = write(integrate.record(
            workspace=key, unit=unit, pr=pr, mode="agent", head_before=head_before, head_after=head_now,
            origin_sha=origin_sha, outcome=outcome, related_=rel, report=reply,
            needs_person=integrate.parse_needs_person(reply), detail="; ".join(details),
        ))
        yield ("done", {"integration": rec})

    async def _related(
        self, root: Path, unit: str, data: dict[str, Any], head: str, origin_sha: str
    ) -> dict[str, list[dict[str, Any]]]:
        """R7, from git. A failure leaves a list empty rather than stopping the step."""
        try:
            base = await gitops.merge_base_of(root, head, origin_sha)
            mine = await gitops.files_between(root, base, head)
            commits = []
            for c in await gitops.commits_between(root, base, origin_sha):
                commits.append({**c, "files": await gitops.files_of_commit(root, c["sha"])})
        except GitError:
            return {"merged": [], "open": []}
        try:
            prs = await integrate.open_prs(str(root))
        except integrate.IntegrateError:
            prs = []
        heads = {r.get("number"): str(r.get("headRefOid") or "") for r in prs}
        others = []
        for u in data["units"]:
            if u["name"] == unit or not u.get("between_pr_and_ship") or not u.get("pr"):
                continue
            other_head = heads.get(u["pr"].get("number"))
            if other_head is None:
                continue
            files = None
            try:
                if await gitops.has_commit(root, other_head):
                    their_base = await gitops.merge_base_of(root, other_head, origin_sha)
                    files = await gitops.files_between(root, their_base, other_head)
            except GitError:
                files = None
            others.append({"unit": u["name"], "files": files})
        return integrate.related(commits, mine, data["units"], others, unit)

    async def _cleanup(self, cwd: str, unit: str) -> dict[str, Any]:
        """R10 after a `ship` step. Never raises; says what it did or why not."""
        try:
            data = await board_reader.read(self._units_root(cwd))
        except Unavailable as e:
            return {"removed": False, "reason": str(e)}
        found = next((u for u in data["units"] if u["name"] == unit), None)
        if found is None:
            return {"removed": False, "reason": "unit not on the board"}
        return await worktrees.remove_if_finished(cwd, unit, found, self.config.data_dir)

    async def next_step(self, cwd: str, unit: str) -> dict[str, Any]:
        """`0024`. The one stage the run button may offer, and why -- `cos.mjs next`'s answer.

        Read with the same store and the same `repo=cwd` that `run_step` hands the gate, so
        the stage offered and the gate that will be asked read one checkout (`0024` spec,
        *Repo mà `cos.mjs` đọc*). Nothing here chooses a stage.
        """
        self._workspace_or_refuse(cwd)
        if not unit:
            raise Invalid("name a work unit")
        self._unit_dir(cwd, unit)
        # `0045` R15. Asked first with no `--repo`, which reads files only: a held unit is
        # answered here, before `_worktree` could reopen the tree a drop just removed.
        try:
            held = await board_reader.next_step(self._units_root(cwd), unit, repo=None)
        except Unavailable as e:
            raise Invalid(str(e)) from e
        if held.get("hold"):
            return {
                "cwd": cwd, "unit": unit, **{k: held[k] for k in ("stage", "action", "blocked")},
                "waiting": [], "hold": held["hold"],
            }
        # `0017`. The unit's worktree is the checkout its branch and pull request are read
        # from. None when there is none to open, and `cos.mjs` then keeps `review` and
        # `ship` closed rather than read the workspace's branch, which is not this unit's.
        # A workspace that is not a git repository has no worktrees, and is read as it
        # always was — the same fallback `run_step` takes, so the two read one checkout.
        if (Path(cwd).expanduser().resolve() / ".git").exists():
            tree = await self._worktree(cwd, unit)
            repo = tree["path"] if tree else None
        else:
            repo = cwd
        try:
            found = await board_reader.next_step(self._units_root(cwd), unit, repo=repo)
        except Unavailable as e:
            raise Invalid(str(e)) from e
        return {
            "cwd": cwd,
            "unit": unit,
            **{k: found[k] for k in ("stage", "action", "blocked")},
            # `0028`. The findings a person is awaited on, copied from `cos.mjs next`.
            "waiting": list(found.get("waiting") or []),
        }

    async def set_mode(self, cwd: str, unit: str, stage: str, mode: str) -> dict[str, Any]:
        """Choose how one step runs. Validated against the board, not against a second list."""
        self._workspace_or_refuse(cwd)
        journal = self._journal()
        if journal is None:
            raise Invalid(
                "no working folder is set, so a mode cannot be recorded — set COS_WORKING_DIR"
            )

        try:
            data = await board_reader.read(self._units_root(cwd))
        except Unavailable as e:
            raise Invalid(str(e)) from e

        found = next((u for u in data["units"] if u["name"] == unit), None)
        if found is None:
            raise Invalid(f"no such work unit in this workspace: {unit}")
        if stage not in data["stages"]:
            raise Invalid(f"no such stage: {stage} (use one of {', '.join(data['stages'])})")

        try:
            journal.set_mode(self._journal_key(cwd), unit, stage, mode)
        except BadRecord as e:
            raise Invalid(str(e)) from e
        except Busy as e:
            raise Invalid(str(e)) from e
        return {"cwd": cwd, "unit": unit, "stage": stage, "mode": mode}

    async def run_step(self, cwd: str, unit: str, stage: str) -> AsyncIterator[tuple[str, Any]]:
        """Run one step of one unit, streaming the reply as it arrives.

        Everything this needs — the stage order, the artifact filename, the mode — comes
        from one board read, so a step cannot run against a different idea of the unit
        than the one the page is showing.
        """
        self._workspace_or_refuse(cwd)
        self._refuse_while_updating()
        journal = self._journal()
        if journal is None:
            raise Invalid(
                "no working folder is set, so a run cannot be recorded — set COS_WORKING_DIR"
            )

        # `0050` R4. The unit is held from here, before the first `await`: a second request
        # for any stage of it is refused before it reads the board, opens a worktree, runs
        # the gate or fetches -- not after all of that, as it was (`spike.md ## U1`). Until
        # the step is handed to `_drive` the mark is this frame's to return, on every road
        # out: a refusal, an exception, or a cancel when the client goes away (R5).
        key = self._journal_key(cwd)
        mark = self._take(key, unit, "step", stage)
        handed = False
        try:
            try:
                data = await board_reader.read(self._units_root(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e

            found = next((u for u in data["units"] if u["name"] == unit), None)
            if found is None:
                raise Invalid(f"no such work unit in this workspace: {unit}")
            row = next((r for r in found["stages"] if r["stage"] == stage), None)
            if row is None:
                raise Invalid(f"no such stage: {stage} (use one of {', '.join(data['stages'])})")
            # `0045` R4/R15. `cos.mjs`'s own field, read before any worktree is opened — the gate
            # below would refuse too, but only after `_worktree` had reopened a dropped tree.
            held = found.get("hold")
            if held:
                raise Invalid(f"{unit} is {held.get('state')}: {held.get('reason')} — nothing runs on it")

            # `.claude/CLAUDE.md` invariant 2: *"Ask `cos.mjs gate` before a stage and stop
            # when it exits non-zero."* Until 2026-09-23 this app did neither. It read the
            # board, found the row, and started the session -- so the board would run `ship`
            # on a unit whose `intent.md` was still a draft, and the only thing standing
            # between it and that was a sentence in a skill file addressed to a session that
            # often has no way to run a command.
            #
            # Asked here rather than in `Runner` because a refusal must arrive before any
            # money is spent, and `run_step` is the last place that is still true.
            # `0017`. Every step runs in the unit's own worktree. A workspace that is not a git
            # repository has none, and its steps run where they always did — there is no
            # branch there for another unit to take away.
            is_repo = (Path(cwd).expanduser().resolve() / ".git").exists()
            tree = await self._worktree(cwd, unit, strict=True) if is_repo else None
            if is_repo and tree is None:
                try:
                    tree = {"path": (await worktrees.ensure(cwd, unit, None, self.config.data_dir))["path"]}
                except (GitError, BadUnit) as e:
                    raise Invalid(f"{unit} has no worktree and one could not be opened: {e}") from e
            work = tree["path"] if tree else cwd
            # `0039` R13. A spike is watched through the worktree's `HEAD` and `git status`;
            # with no git there is nothing to watch, so it does not run at all.
            if stage == "spike" and tree is None:
                raise Invalid("spike needs a git worktree to watch, and this workspace is not a git repository")
            # `0030_a-unit-branch-starts-from-a-stale-main` R1/R4/R5. A tree already on its
            # branch carries whatever `_worktree` read when it was opened onto it (or nothing,
            # when it was already there before this call); a tree still detached is refreshed
            # now, on the spot, because a session about to run on it is about to read it.
            base: dict[str, Any] | None = None
            if tree is not None:
                if tree.get("branch"):
                    base = tree.get("base")
                else:
                    base = await worktrees.refresh_base(cwd, unit, self.config.data_dir)
            try:
                # `work` is the checkout the `review` and `ship` gates read git and the pull
                # request from (`0015`). The store has no git to read.
                allowed, said = await board_reader.gate(
                    self._units_root(cwd), unit, stage, repo=work
                )
            except Unavailable as e:
                raise Invalid(str(e)) from e
            if not allowed:
                raise Invalid(said)

            if stage == "impl" and tree is not None:
                # R6. A tree that cannot run its tests turns every `impl` red from the start, so
                # the step is not started on one. Tried once more first: a network blip is the
                # ordinary reason, and the page has nothing better to offer than *try again*.
                prepared = worktrees.read_prepare(Path(work))
                if not (prepared or {}).get("ok"):
                    prepared = await worktrees.prepare(Path(work), cwd, data_dir=self.config.data_dir)
                if not prepared.get("ok"):
                    raise Invalid(worktrees.describe_failure(prepared))

            directory = self._unit_dir(cwd, unit)
            mode = journal.modes(key).get((unit, stage), "manual")
            # `0021` D3. The rounds `review.md` held before this step, so that the ones it adds
            # can be told apart afterwards. Taken from the board already read above.
            rounds_before = (
                {r.get("n") for r in found.get("rounds") or []}
                if row["file"] == "review.md" else None
            )
            # `0004_no-setting-says-which-model-runs-a-stage`. Resolved after the gate, so a
            # refused step reads nothing more. `stage` was checked against the board above.
            # `0033`: with the plan's label, the effort and, for `impl`, which run this is.
            # `0019` plan step 6 / `spec.md` R6. Read after the gate, before any money is
            # spent — the same place `model` is resolved. `Runner` does not read the run log
            # itself; `build_prompt` only places what it is handed, the same as `base_note`.
            try:
                config = self._stage_config(stage, list(data["stages"]), directory, journal, key, unit)
                failed = journal.failed_attempts(key, unit, stage)
            except Busy as e:
                raise Invalid(str(e)) from e
            end_fields = None
            if rounds_before is not None:
                async def end_fields() -> dict[str, int]:
                    return await self._findings_added(cwd, unit, rounds_before)
            # `0035` R10. The integration pushed since the last review round, for `review` only.
            integration_note = ""
            if stage == "review":
                since = integration_since_review(journal, key, unit)
                integration_note = integrate.describe_for_review(since) if since else ""
            # `0042`. Which files the plan names `main` changed since the plan ran, for `impl`
            # only. Unlike `failed_attempts` above, nothing here may refuse the step (R8): a
            # busy run log, an unreadable `plan.md` or a bug in `drift.py` is "could not check".
            plan_drift: dict[str, Any] | None = None
            if stage in ("impl", "implement"):
                try:
                    plan_drift = await drift.compute(
                        journal.records(key, unit),
                        (directory / "plan.md").read_text(encoding="utf-8"),
                        tree["path"] if tree else None,
                    )
                except Exception as e:  # noqa: BLE001 — R8, recorded as the reason
                    plan_drift = {
                        "plan_sha": None, "main_sha": None, "files": None,
                        "checked": False, "reason": str(e) or type(e).__name__,
                    }
            # `0041` R2. The unit's open pull request, for `pr` only, after the gate and before
            # any money is spent. One `gh pr list`, up to `integrate.GH_TIMEOUT`; a lookup that
            # fails still starts the step, and its prompt says so.
            pr_note, pr_before = "", None
            if stage == "pr":
                if tree is not None:
                    lookup = await integrate.pr_for_branch(work, tree.get("branch") or "")
                else:
                    lookup = {"state": "unknown", "reason": "this workspace is not a git checkout"}
                pr_note, pr_before = integrate.describe_pr_lookup(lookup), lookup.get("url", "")
            runner = Runner(self.sessions, journal)
            # `0034` R11. The registry is what the page lists and what a Stop finds; the mark
            # taken above is what everything else asks. The same start time for both, and no
            # `await` between the listing and the phase (`0050` R3).
            try:
                running = self.steps.claim(key, unit, stage, started_at=mark.started_at)
            except steps_mod.Busy as e:
                raise Invalid(str(e)) from e
            mark.phase = "running"
            # `0039` R12: emptied before the step, whatever an earlier one left, and removed
            # after it however it ends -- in `_drive`, so a client that drops the stream no
            # longer decides when (`0034`).
            scratch = units.spike_dir(cwd, unit, self.config.data_dir) if stage == "spike" else None
            rid = self._mark_running(key, unit, stage, "step")
            queue: asyncio.Queue = asyncio.Queue()
            running.listeners.add(queue)
            running.task = asyncio.create_task(self._drive(
                running, mark, runner, cwd, unit, stage, row["file"], directory, tree, base, rounds_before,
                rid, scratch,
                dict(
                    workspace=cwd,
                    directory=directory,
                    journal_key=key,
                    unit=unit,
                    stage=stage,
                    artifact=row["file"],
                    stages=list(data["stages"]),
                    mode=mode,
                    gate_said=said,
                    cwd=step_cwd(stage, work, directory, str(scratch) if scratch else None),
                    base=base,
                    base_note=describe_base(base),
                    last_attempt=describe_attempt(failed) if failed else "",
                    integration_note=integration_note,
                    plan_drift=plan_drift,
                    drift_note=drift.describe(plan_drift) if plan_drift is not None else "",
                    end_fields=end_fields,
                    pr_note=pr_note,
                    pr_before=pr_before,
                    **config,
                    # Only named for a spike, so a stand-in `run` without it keeps working.
                    **({"watch": work} if scratch is not None else {}),
                ),
            ))
            handed = True
        finally:
            if not handed:
                self._release(key, unit, mark)
        # `0034` R3/R4. Only the reader lives here. A reader that goes away -- a closed
        # tab, a dropped NDJSON client -- takes its queue with it and nothing else: the
        # step runs on to its own end in `_drive`. Stopping it is `stop_step`, and only that.
        try:
            while True:
                kind, payload = await queue.get()
                if kind == "raise":
                    raise payload
                yield (kind, payload)
                if kind == "done":
                    return
        finally:
            running.listeners.discard(queue)

    async def _drive(
        self, running: steps_mod.Running, mark: steps_mod.Mark, runner: Runner, cwd: str, unit: str, stage: str,
        artifact: str, directory: Path, tree: dict[str, Any] | None, base: dict[str, Any] | None,
        rounds_before: set[Any] | None, rid: str, scratch: Path | None, kwargs: dict[str, Any],
    ) -> None:
        """One board step, start to end, as its own task (`0034`).

        What `run_step` used to do inline, unchanged, except that every item goes to the
        step's listeners with `put_nowait` -- this never waits on a reader -- and that a
        `stopped` step records no transition, cleans nothing and posts nothing (R9).
        """

        def tell(item: tuple[str, Any]) -> None:
            for q in list(running.listeners):
                q.put_nowait(item)

        told_done = False
        try:
            if scratch is not None:
                shutil.rmtree(scratch, ignore_errors=True)
                scratch.mkdir(parents=True)
            async for item in runner.run(**kwargs, running=running):
                if item[0] == "done":
                    item = ("done", {**item[1], "base": base})
                    if item[1].get("outcome") != "stopped":
                        self._record_transition(cwd, unit, artifact, directory, item[1])
                    if stage == "ship" and tree is not None and item[1].get("outcome") == "done":
                        # R10. Only if `cos.mjs` now says `finished` and GitHub says merged;
                        # otherwise nothing is touched and the board tries again later.
                        item = ("done", {**item[1], "cleanup": await self._cleanup(cwd, unit)})
                    if rounds_before is not None and item[1].get("outcome") == "done":
                        # After `Runner` has written `review.md` (`runner.py:442`), never
                        # before: the artifact does not wait on GitHub (`0021` R6).
                        item = (
                            "done",
                            {**item[1], "comments": await self._post_new_rounds(cwd, unit, rounds_before)},
                        )
                    told_done = True
                tell(item)
        except RunError as e:
            tell(("raise", Invalid(str(e))))
            told_done = True
        except asyncio.CancelledError:
            if not running.stop_requested:
                raise
            # A Stop's cancel that arrived after the runner had already ended.
            task = asyncio.current_task()
            if task is not None:
                task.uncancel()
        except Exception as e:  # noqa: BLE001 - the reader raises it, as it always did
            tell(("raise", e))
            told_done = True
        finally:
            if not told_done:
                tell(("raise", Invalid(f"{unit}'s {stage} step ended without an outcome; the app may be shutting down")))
            self._release(running.workspace, running.unit, mark)
            self._running.pop(rid, None)
            if scratch is not None:
                shutil.rmtree(scratch, ignore_errors=True)
            self.steps.release(running)
            self.updater.job_ended()

    async def stop_step(self, cwd: str, unit: str, by: str) -> dict[str, Any]:
        """Stop one running board step (`0034` R2, R5, R6). The route and the page's
        button both call this, and nothing else.

        `by` is a name the person typed, not an identity: the password names nobody. What it
        leaves is an `end` record with `outcome: stopped` and `stopped_by`. It opens and
        closes no gate, and starts nothing.
        """
        self._workspace_or_refuse(cwd)
        name = (by or "").strip()
        if not name:
            raise Invalid("a name is required to stop a step")
        return await self._stop_running(self._journal_key(cwd), unit, name)

    async def _stop_running(self, key: str, unit: str, by: str) -> dict[str, Any]:
        """The Stop itself, shared with `0068`'s "áp dụng ngay" so a step it cuts ends the
        same way: an `end` record with `stopped` and `stopped_by`."""
        try:
            running = self.steps.request_stop(key, unit, by)
        except (steps_mod.NotRunning, steps_mod.Finishing) as e:
            raise Invalid(str(e)) from e
        await running.handle.close()
        if running.task is not None:
            running.task.cancel()
        return {"unit": running.unit, "stage": running.stage, "stopped_by": running.stopped_by}

    def running_steps(self, cwd: str) -> list[dict[str, Any]]:
        """The board steps running now in this workspace (`0034` R13). This process only."""
        self._workspace_or_refuse(cwd)
        return self.steps.listing(self._journal_key(cwd))

    # -- updating the app (`0068`) -------------------------------------------
    #
    # Every decision is `Updater`'s; these translate its refusals into `Invalid`, as the
    # rest of this file does, so a route maps one exception type.

    def _refuse_while_updating(self) -> None:
        try:
            self.updater.refuse_while_updating()
        except updater_mod.Refused as e:
            raise _as_invalid(e) from e

    def _update_jobs(self) -> list[dict[str, Any]]:
        """R8. What this process is running now, besides the updater's own build."""
        jobs: list[dict[str, Any]] = []
        for r in self.steps.all():
            jobs.append({
                "kind": "step", "id": f"step:{r.workspace}:{r.unit}", "workspace": r.workspace,
                "unit": r.unit, "stage": r.stage, "started": r.started_at,
            })
        for entry in self._running.values():
            if entry["stage"] == "integrate":
                jobs.append({
                    "kind": "integration", "id": f"integration:{entry['workspace']}:{entry['unit']}",
                    "workspace": entry["workspace"], "unit": entry["unit"], "stage": "integrate",
                    "started": entry["started"],
                })
        for turn in self.sessions.in_flight():
            jobs.append({"kind": "chat", "id": f"chat:{turn['id']}", "turn": turn["id"],
                         "session_id": turn["session_id"], "workspace": turn["workspace"],
                         "started": turn["started"]})
        return jobs

    async def _update_cut(self, job: dict[str, Any], by: str) -> bool:
        """R10. A step through Stop's own road; a chat turn closed. Never an integration."""
        if job["kind"] == "step":
            try:
                await self._stop_running(job["workspace"], job["unit"], by)
            except Invalid:
                return False  # already ended, or writing its artifact: it is waited for
            return True
        if job["kind"] == "chat":
            return await self.sessions.cut_turn(job["turn"])
        return False

    def update_status(self) -> dict[str, Any]:
        return self.updater.status()

    def update_cut_list(self) -> dict[str, Any]:
        try:
            return self.updater.cut_list()
        except updater_mod.Refused as e:
            raise _as_invalid(e) from e

    async def update_apply(self, channel: str, mode: str, by: str, token: str) -> dict[str, Any]:
        try:
            return await self.updater.apply(channel, mode, by, token)
        except updater_mod.Refused as e:
            raise _as_invalid(e) from e

    def update_cancel(self, by: str) -> dict[str, Any]:
        try:
            return self.updater.cancel(by)
        except updater_mod.Refused as e:
            raise _as_invalid(e) from e

    def update_build_local(self, by: str) -> dict[str, Any]:
        try:
            return self.updater.build_local(by)
        except updater_mod.Refused as e:
            raise _as_invalid(e) from e

    async def shutdown(self) -> None:
        """Cancel every step still running, and wait for them, 10 seconds at most.

        No `end` is written for them (C6): a step with no `end` is what an app that went
        down in the middle of it looks like, and that is what happened.
        """
        tasks = [r.task for r in self.steps.all() if r.task is not None and not r.task.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=10)

    async def _post_new_rounds(
        self, cwd: str, unit: str, before: set[Any]
    ) -> list[dict[str, Any]]:
        """`0021` R2. Post every round the step just added. Never raises.

        What happened to each is in the run log whatever it was, and the board shows a
        round that did not make it as *not on the PR* with the reason.
        """
        try:
            data = await board_reader.read(self._units_root(cwd))
        except Unavailable as e:
            return [{"round": None, "state": "failed", "url": "", "reason": str(e)}]
        found = next((u for u in data["units"] if u["name"] == unit), None)
        if found is None:
            return []
        out = []
        for rnd in found.get("rounds") or []:
            if rnd.get("n") in before:
                continue
            async with self._comment_lock:
                out.append(await self._post_round(cwd, found, rnd))
        return out

    async def post_review_comment(self, cwd: str, unit: str, round_n: Any) -> dict[str, Any]:
        """`0021` R8, R9. Post one review round to the unit's pull request, or say it is there.

        The body is built from the round as `cos.mjs` read it out of `review.md`; nothing a
        caller sends reaches GitHub but the unit's name and the round's number. Not an
        approval, and it opens no gate: neither gate reads comments (R10).
        """
        self._workspace_or_refuse(cwd)
        try:
            n = int(round_n)
        except (TypeError, ValueError):
            raise Invalid(f"a round is named by its number, got {round_n!r}") from None
        async with self._comment_lock:
            try:
                data = await board_reader.read(self._units_root(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e
            found = next((u for u in data["units"] if u["name"] == unit), None)
            if found is None:
                raise Invalid(f"no such work unit in this workspace: {unit}")
            rnd = next((r for r in found.get("rounds") or [] if r.get("n") == n), None)
            if rnd is None:
                have = ", ".join(str(r.get("n")) for r in found.get("rounds") or []) or "none"
                raise Invalid(f"review.md of {unit} has no round {n} (it has {have})")
            return await self._post_round(cwd, found, rnd)

    async def _post_round(
        self, cwd: str, found: dict[str, Any], rnd: dict[str, Any]
    ) -> dict[str, Any]:
        """Post one round and write one `pr-comment` row saying how it went (R13).

        The caller holds `_comment_lock`. A row that cannot be written is dropped rather
        than turned into a failure: the comment is on GitHub or it is not, and that is what
        the person asked about. The next board read then shows the round as not posted, and
        a second press finds the marker and says `already`.
        """
        unit = found["name"]
        n = rnd.get("n")
        pr_url = (found.get("pr") or {}).get("url") or ""
        result = await prcomment.post(
            unit, n, rnd.get("verdict"), rnd.get("text") or "", pr_url,
            str(Path(cwd).expanduser().resolve()),
        )
        record: dict[str, Any] = {
            "kind": "pr-comment",
            "workspace": self._journal_key(cwd),
            "unit": unit,
            "stage": "review",
            "round": n,
            "pr": pr_url,
            "outcome": result.state,
        }
        if result.state == "failed":
            record["detail"] = result.reason
        else:
            record["comment_url"] = result.url
        journal = self._journal()
        if journal is not None:
            try:
                journal.append(record)
            except (Busy, BadRecord, OSError):
                pass
        return {"unit": unit, "round": n, "pr": pr_url, **result.as_dict()}

    def _record_transition(
        self, cwd: str, unit: str, artifact: str, directory: Path, done: dict[str, Any]
    ) -> None:
        """`0014` R6. One transition per step that finished, written as it happens.

        This is the first writer into `0013`'s log that is not the git import.
        `.cos/0013_.../ship.md` said the loop would come back here: history imported from
        git carries no actor and no session, because git knows neither, so the provenance
        that unit built is only ever true of work done **after** it. This is that work.

        Never raises into the run. A step that did its job and then failed to be recorded
        has still done its job, and turning a bookkeeping failure into a failed step would
        cost real money for nothing. The failure is dropped rather than shown, and that is
        a cost `0014` `impl.md` states rather than hides.
        """
        if done.get("outcome") != "done":
            return
        history = self._history()
        if history is None:
            return
        try:
            text = (directory / artifact).read_text(encoding="utf-8", errors="replace")
            found = STATUS_RE.search(text)
            if not found:
                return
            history.record(
                self._journal_key(cwd),
                unit,
                artifact,
                found.group(1).lower(),
                actor=f"stage:{done.get('stage') or ''}",
                session=str(done.get("session_id") or "") or UNKNOWN,
                source=f"run:{done.get('stage') or ''}",
            )
        except (OSError, BadTransition, Busy):
            return

    def _create_lock(self, cwd: str) -> asyncio.Lock:
        """`0017` R8. One lock per workspace, held across numbering and making the tree."""
        return self._create_locks.setdefault(units.key(cwd), asyncio.Lock())

    async def create_unit(self, cwd: str, slug: str, brief: str = "") -> dict[str, Any]:
        """`0014` R1. Start a work unit, in the product's store rather than the repository.

        The number and the slug grammar are `cos.mjs`'s, through `coscc/units.py`. Nothing
        here is a second opinion about either — `.claude/CLAUDE.md` says that script is the
        one place the loop is defined.

        Since `0017` it also opens the unit's own worktree, detached at the workspace's
        `main`. A worktree that cannot be opened does not undo the unit: the result says
        why under `worktree.error`, and the next step that needs the tree tries again.
        """
        self._workspace_or_refuse(cwd)
        root = Path(cwd).expanduser().resolve()
        async with self._create_lock(cwd):
            reserve = [root]
            try:
                reserve += [
                    Path(t["path"]) for t in (await gitops.worktree_list(root))[1:]
                    if (Path(t["path"]) / units.COS_DIR).is_dir()
                ]
            except GitError:
                pass
            try:
                made = {
                    "cwd": cwd,
                    # The host repository's own `.cos/` counts toward the number, so a unit
                    # started here cannot take a number already used there
                    # (`0001_product-describes-a-state-it-is-not-in` R10), and since `0017`
                    # so does every worktree's. Counting is `cos.mjs`'s.
                    **units.create(cwd, slug, brief, self.config.data_dir, reserve_from=reserve),
                }
            except (CannotCreate, BadUnit) as e:
                raise Invalid(str(e)) from e
            try:
                made["worktree"] = await worktrees.ensure(
                    cwd, made["unit"], None, self.config.data_dir
                )
            except (GitError, BadUnit) as e:
                made["worktree"] = {"path": "", "error": str(e)}
        return made

    async def _worktree(self, cwd: str, unit: str, strict: bool = False) -> dict[str, Any] | None:
        """The unit's worktree, opened on its branch if the branch exists and it is not.

        None when there is none and none can be opened — the workspace is dirty on the
        unit's branch, or is not a git repository at all. `strict` turns the first of those
        into `Invalid`: a step must not run on a tree that is not on its unit's branch.
        """
        try:
            found = await worktrees.find(cwd, unit, self.config.data_dir)
            if found is not None and found["branch"]:
                return {"path": found["path"], "branch": found["branch"]}
            try:
                branch = units.branch_name(cwd, unit, self.config.data_dir)
                await gitops.rev_parse(Path(cwd).expanduser().resolve(), f"refs/heads/{branch}")
            except (CannotCreate, BadUnit, GitError):
                branch = None
            if branch is None:
                return {"path": found["path"], "branch": ""} if found else None
            # The branch exists and the tree is not on it: open it there (`worktrees.ensure`).
            made = await worktrees.ensure(cwd, unit, branch, self.config.data_dir)
            return {"path": made["path"], "branch": made["branch"], "base": made.get("base")}
        except (GitError, BadUnit) as e:
            if strict:
                raise Invalid(f"{unit}'s worktree could not be opened on its branch: {e}") from e
            return None

    async def answer(
        self,
        cwd: str,
        unit: str,
        artifact: str,
        question: Any,
        answer: str,
        answered_by: str,
    ) -> dict[str, Any]:
        """`0016` R2–R4. A person answers one item under an artifact's `## Open questions`.

        The only route in this app that writes into an artifact a stage wrote, and it only
        ever **appends**: the file is opened `"a"`, never `"w"`, so every byte above the
        `## Answers` block is the byte the stage left there (R4). What counts as a question
        and whether it is answered is `cos.mjs`'s decision, read through one board read;
        nothing here parses `## Open questions` a second time (R7).

        Not an approval, and it starts nothing. `answered_by` is whatever name the caller
        typed: no route in this app has a login, so it is a claim, not an identity.

        `0028`: `question` may be `"F<n>"`, a finding `cos.mjs` lists in the unit's
        `personFindings`; then `artifact` must be `review.md` and the block is `### F<n>`.
        Unlike a numbered answer, that block is read by `cos.mjs next` and the `ship` gate.
        """
        self._workspace_or_refuse(cwd)
        name = str(answered_by or "").strip()
        text = str(answer or "").strip("\n")
        async with self._answer_lock:
            try:
                data = await board_reader.read(self._units_root(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e

            found = next((u for u in data["units"] if u["name"] == unit), None)
            if found is None:
                raise Invalid(f"no such work unit in this workspace: {unit}")
            # `0028`. A finding the last review round confirmed needs a person is answered
            # by its id, `F<n>`, into `review.md` -- and only while `cos.mjs` lists it in
            # `personFindings`, so what may be answered is its decision, not this route's.
            finding = str(question).strip() if isinstance(question, str) else ""
            finding = finding if re.fullmatch(r"F\d+", finding) else ""
            if finding:
                if artifact != "review.md":
                    raise Invalid(f"a finding is answered in review.md, not {artifact}")
                awaited = [p["id"] for p in found.get("person_findings") or []]
                if finding not in awaited:
                    raise Invalid(
                        f"{finding} is not a finding the last review round of {unit} "
                        "confirmed needs a person"
                        + (f" (those are {', '.join(awaited)})" if awaited else "")
                    )
                number: int | str = finding
            else:
                asked = [q for q in found.get("questions") or [] if q.get("artifact") == artifact]
                if not asked:
                    raise Invalid(f"{artifact} in {unit} has no numbered item under ## Open questions")
                try:
                    number = int(question)
                except (TypeError, ValueError):
                    raise Invalid(f"a question is named by its number, got {question!r}") from None
                if number not in {q["n"] for q in asked}:
                    raise Invalid(
                        f"{artifact} has no question {number} "
                        f"(it has {', '.join(str(q['n']) for q in asked)})"
                    )
            if not text.strip():
                raise Invalid("the answer is empty")
            if not name or "\n" in name or "\r" in name:
                raise Invalid("say who is answering, on one line")
            nxt = str(found.get("next") or "")
            if nxt == "finished" or nxt.startswith("closed"):
                raise Invalid(f"{unit} is {nxt}; its questions can no longer be answered")
            # A line that reads as a heading would end this block early or open another,
            # and `cos.mjs` would then read the answer wrongly. Refusing is cheaper and more
            # honest than escaping somebody's words.
            if any(line.lstrip().startswith("#") for line in text.splitlines()):
                raise Invalid("no line of an answer may start with #")

            path = self._unit_dir(cwd, unit) / artifact
            try:
                existing = path.read_text(encoding="utf-8")
            except OSError as e:
                raise Invalid(f"could not read {artifact}: {e}") from e
            lines = existing.splitlines()
            heading = next((i for i, l in enumerate(lines) if l.rstrip() == "## Answers"), None)
            if heading is not None and any(l.startswith("## ") for l in lines[heading + 1:]):
                raise Invalid(
                    f"{artifact} has a section after its ## Answers, so a block appended at "
                    "the end would not be read as an answer"
                )

            today = date.today().isoformat()
            block = ""
            if existing and not existing.endswith("\n"):
                block += "\n"
            if heading is None:
                block += "\n## Answers\n"
            block += f"\n### {finding}\n" if finding else f"\n### Câu {number}\n"
            block += (
                f"Answered by: {name}. Date: {today}. Via: product.\n\n"
                f"{text}\n"
            )
            try:
                with path.open("a", encoding="utf-8") as f:
                    f.write(block)
            except OSError as e:
                raise Invalid(f"could not write {artifact}: {e}") from e

        # `0016` plan, in place of spec R9: the store is not a git repository, so there is
        # no commit to make. The provenance this app already keeps is a row in `outputs`.
        # Never raises: the answer is on disk, and failing the request now would tell the
        # person it was not.
        history = self._history()
        if history is not None:
            try:
                history.add_output(
                    self._journal_key(cwd),
                    unit,
                    artifact.removesuffix(".md"),
                    "deliverable",
                    artifact,
                    actor=f"human:{name}",
                    source="answer",
                )
            except (OSError, BadTransition, Busy):
                pass

        return {
            "unit": unit,
            "artifact": artifact,
            "question": number,
            "answered_by": name,
            "date": today,
        }

    async def record_outcome(
        self,
        cwd: str,
        unit: str,
        result: str,
        measured_by: str,
        source: str = "",
        reason: str = "",
        note: str = "",
        recorded_by: str = "",
    ) -> dict[str, Any]:
        """`0047` R1–R4, R7. Record whether a finished unit met its intent's outcome.

        Built on `answer()`: the same lock, the same one board read, the same refusal when a
        section follows `## Answers`, and the same `"a"` open, so every byte above the block
        stays the byte the stage left there. The block is `### Outcome` under `intent.md`'s
        `## Answers`; whether it is valid and which one is in force is `cos.mjs`'s reading.

        Not an approval, and it starts nothing: no gate reads the block, and a finished unit
        stays finished. `recorded_by` and `measured_by` are names somebody typed; no route
        has a login, so both are claims. `source` is not checked against anything.
        """
        self._workspace_or_refuse(cwd)
        name = str(recorded_by or "").strip()
        measurer = str(measured_by or "").strip()
        word = str(result or "").strip()
        src = str(source or "").strip()
        why = str(reason or "").strip()
        text = str(note or "").strip("\n")
        async with self._answer_lock:
            try:
                data = await board_reader.read(self._units_root(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e

            found = next((u for u in data["units"] if u["name"] == unit), None)
            if found is None:
                raise Invalid(f"no such work unit in this workspace: {unit}")
            nxt = str(found.get("next") or "")
            if nxt != "finished":
                raise Invalid(f"{unit} is {nxt or 'not finished'}; an outcome is recorded only on a finished unit")
            if word not in OUTCOME_RESULTS:
                raise Invalid(f"the result is one of {', '.join(OUTCOME_RESULTS)}, got {word!r}")
            kind = OUTCOME_RESULTS[word]
            if not name or "\n" in name or "\r" in name:
                raise Invalid("say who is recording, on one line")
            if not measurer or "\n" in measurer or "\r" in measurer:
                raise Invalid("say who measured it — agent, or a person's name — on one line")
            if "\n" in src or "\r" in src:
                raise Invalid("the source is one line")
            if "\n" in why or "\r" in why:
                raise Invalid("the reason is one line")
            if kind != "unmeasurable" and not src:
                raise Invalid(f"{word} needs a source: where the figure it rests on came from")
            if kind == "unmeasurable" and not why:
                raise Invalid(f"{word} needs a reason: why it could not be measured")
            # The same refusal as `answer()`, and for the same reason: a heading would end
            # this block early or open another, and `cos.mjs` would read it wrongly.
            if any(line.lstrip().startswith("#") for line in [src, why, *text.splitlines()]):
                raise Invalid("no line of an outcome may start with #")

            path = self._unit_dir(cwd, unit) / "intent.md"
            try:
                existing = path.read_text(encoding="utf-8")
            except OSError as e:
                raise Invalid(f"could not read intent.md: {e}") from e
            lines = existing.splitlines()
            heading = next((i for i, l in enumerate(lines) if l.rstrip() == "## Answers"), None)
            if heading is not None and any(l.startswith("## ") for l in lines[heading + 1:]):
                raise Invalid(
                    "intent.md has a section after its ## Answers, so a block appended at "
                    "the end would not be read as an outcome"
                )

            today = date.today().isoformat()
            block = ""
            if existing and not existing.endswith("\n"):
                block += "\n"
            if heading is None:
                block += "\n## Answers\n"
            block += (
                "\n### Outcome\n"
                f"Answered by: {name}. Date: {today}. Via: product.\n\n"
                f"Result: {word}\n"
                f"Measured by: {measurer}\n"
            )
            if src:
                block += f"Source: {src}\n"
            if why:
                block += f"Reason: {why}\n"
            if text.strip():
                block += f"\n{text}\n"
            try:
                with path.open("a", encoding="utf-8") as f:
                    f.write(block)
            except OSError as e:
                raise Invalid(f"could not write intent.md: {e}") from e

        # As in `answer()`: the store has no git, so the provenance is a row in `outputs`,
        # and a failure to write it never fails a block already on disk.
        history = self._history()
        if history is not None:
            try:
                history.add_output(
                    self._journal_key(cwd),
                    unit,
                    "intent",
                    "deliverable",
                    "intent.md",
                    actor=f"human:{name}",
                    source="outcome",
                )
            except (OSError, BadTransition, Busy):
                pass

        return {
            "unit": unit,
            "result": word,
            "measured_by": measurer,
            "recorded_by": name,
            "date": today,
        }

    async def hold(self, cwd: str, unit: str, to: str, reason: str, by: str) -> dict[str, Any]:
        """`0045`. A person pauses, drops or resumes a unit (`to`: paused, dropped, active).

        Appends one `### Paused|Dropped|Resumed` block under `intent.md ## Answers` — the
        way `answer` appends, never rewriting a byte above it (R8) — and one `hold` row to
        the run log (R10). Which moves exist is `cos.mjs`'s `holdMoves`, read off the board;
        nothing here decides it (R6). Dropping also closes the unit's open pull request with
        this machine's `gh` login and removes its worktree (R12); a failure there is
        reported, never raised, and undoes nothing.

        Not an approval, and it starts nothing (R16): no step runs, no session opens, even on
        a resume. `by` is whatever name the caller typed. Refused while a step or an
        integration of this unit runs in this process (R13); it holds that same mark itself
        while it writes, so no step can begin halfway through.
        """
        self._workspace_or_refuse(cwd)
        journal = self._journal()
        if journal is None:
            raise Invalid("no working folder is set, so a hold cannot be recorded — set COS_WORKING_DIR")
        if not unit:
            raise Invalid("name a work unit")
        to = str(to or "").strip()
        reason = str(reason or "").strip()
        by = str(by or "").strip()
        directory = self._unit_dir(cwd, unit)
        key = self._journal_key(cwd)
        # No `await` between the check and the take: the same mark `run_step` and
        # `integrate` take, so neither starts while this writes. When the unit is already
        # held, the board is still read, so a move refused for another reason says that one.
        held = self._active.get((key, unit))
        mark = self._take(key, unit, "hold") if held is None else None
        try:
            try:
                data = await board_reader.read(self._units_root(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e
            found = next((u for u in data["units"] if u["name"] == unit), None)
            said = hold_rules.refusal(found, to, reason, by, steps_mod.describe(unit, held) if held else "")
            if said:
                raise Invalid(said)
            assert found is not None
            from_ = (found.get("hold") or {}).get("state") or "active"
            today = date.today().isoformat()
            path = directory / "intent.md"
            async with self._answer_lock:
                try:
                    existing = path.read_text(encoding="utf-8")
                except OSError as e:
                    raise Invalid(f"could not read intent.md: {e}") from e
                lines = existing.splitlines()
                heading = next((i for i, l in enumerate(lines) if l.rstrip() == "## Answers"), None)
                if heading is not None and any(l.startswith("## ") for l in lines[heading + 1:]):
                    raise Invalid(
                        "intent.md has a section after its ## Answers, so a block appended at "
                        "the end would not be read as a hold"
                    )
                text = ""
                if existing and not existing.endswith("\n"):
                    text += "\n"
                if heading is None:
                    text += "\n## Answers\n"
                text += hold_rules.block(to, by, today, reason)
                try:
                    with path.open("a", encoding="utf-8") as f:
                        f.write(text)
                except OSError as e:
                    raise Invalid(f"could not write intent.md: {e}") from e

            effects: list[dict[str, str]] = []
            if to == "dropped":
                try:
                    branch = units.branch_name(cwd, unit, self.config.data_dir)
                except (CannotCreate, BadUnit):
                    branch = ""
                root = str(Path(cwd).expanduser().resolve())
                effects.append(await hold_rules.close_pr(root, branch))
                effects.append(await hold_rules.remove_tree(cwd, unit, self.config.data_dir))
            try:
                journal.append(hold_rules.record(
                    workspace=key, unit=unit, from_=from_, to=to, reason=reason, by=by, effects=effects,
                ))
            except (BadRecord, Busy):
                # The block is on disk and `cos.mjs` reads it; failing now would tell the
                # person their decision was not recorded when it was.
                pass
        finally:
            if mark is not None:
                self._release(key, unit, mark)
        return {"unit": unit, "from": from_, "to": to, "reason": reason, "by": by, "date": today, "effects": effects}

    async def start_branch(self, cwd: str, unit: str) -> dict[str, Any]:
        """`0014` R4. Cut this unit's branch in the workspace and switch to it.

        The name is not chosen here and is not the caller's: `cos.mjs unit-branch` reads
        the `Type:` the intent declared and prints `<type>/<slug>`. `coscc/gitops.py`
        carries the list of what the app may do with it, which is this and nothing else.

        Since `0001_product-describes-a-state-it-is-not-in` it is cut from the trunk **as the
        remote has it**, not from whatever the local `main` last saw: fetch, read the SHA
        that fetch brought, cut from that SHA (R1). If the fetch fails nothing is cut and
        the refusal says so (R2) — cutting from a stale `main` with a warning would still
        open the pull request on the wrong base. The result names the ref and the commit
        (R3). The remote and the trunk are constants here, never taken from a request.
        """
        self._workspace_or_refuse(cwd)
        try:
            name = units.branch_name(cwd, unit, self.config.data_dir)
        except (CannotCreate, BadUnit) as e:
            raise Invalid(str(e)) from e
        # `0017`. Cut in the unit's own worktree, never in the workspace: cutting there is
        # what took one unit's branch away from another. The workspace stays on `main`.
        try:
            tree = await worktrees.ensure(cwd, unit, None, self.config.data_dir)
        except (GitError, BadUnit) as e:
            raise Invalid(f"Could not open {unit}'s worktree, so no branch was cut. {e}") from e
        repo = Path(tree["path"])
        # `0048`: through the coordinator, so a step starting beside this does not race it
        # for `refs/remotes/origin/main` — and a fetch under 30s old is reused here too
        # (`spec.md ## Answers, câu 2`).
        try:
            await fetches.fetch(repo, BRANCH_REMOTE, BRANCH_TRUNK)
        except GitError as e:
            raise Invalid(
                f"Could not update {BRANCH_TRUNK} from {BRANCH_REMOTE}, so no branch was cut. "
                f"Nothing in the repository changed. git said: {e}"
            ) from e
        try:
            sha = await gitops.rev_parse(repo, f"refs/remotes/{BRANCH_REMOTE}/{BRANCH_TRUNK}")
            output = await gitops.create_branch(repo, name, sha)
        except GitError as e:
            raise Invalid(str(e)) from e
        # Prepared here rather than when the tree was made (`0017` plan): the lockfiles an
        # `impl` works with are the ones at the commit just cut from, not the local `main`.
        # A failure is returned, not raised — the branch is cut either way — and `run_step`
        # refuses `impl` until preparing succeeds (R6).
        prepared = await worktrees.prepare(repo, cwd, data_dir=self.config.data_dir)
        return {
            "cwd": cwd,
            "unit": unit,
            "branch": name,
            "base": f"{BRANCH_REMOTE}/{BRANCH_TRUNK}",
            "sha": sha[:7],
            "output": output,
            "worktree": str(repo),
            "switched": bool(tree.get("switched")),
            "prepare": prepared,
        }

    async def branch_here(self, cwd: str) -> dict[str, Any]:
        """Which branch the workspace is on. A read, so the page can show it."""
        self._workspace_or_refuse(cwd)
        try:
            return {
                "cwd": cwd,
                "branch": await gitops.current_branch(Path(cwd).expanduser().resolve()),
            }
        except GitError as e:
            raise Invalid(str(e)) from e

    def timeline(self, cwd: str, unit: str) -> dict[str, Any]:
        """What has happened to one unit, oldest first (`spec.md` R15)."""
        self._workspace_or_refuse(cwd)
        journal = self._journal()
        if journal is None:
            return {"cwd": cwd, "unit": unit, "runs": [], "cost": {}}
        key = self._journal_key(cwd)
        try:
            runs = journal.timeline(key, unit)
        except Busy as e:
            raise Invalid(str(e)) from e
        return {"cwd": cwd, "unit": unit, "runs": runs, "cost": totals_of(runs)}

    def _history(self) -> History | None:
        """The transition log, or `None` when there is no working folder to keep it in.

        Same shape and same reasoning as `_journal`: with nothing set, the app behaves as
        it did before, and the safe direction to fail in is read-only.
        """
        return (
            History(self.config.working_dir, self.config.data_dir)
            if self.config.working_dir
            else None
        )

    def unit_history(self, cwd: str, unit: str) -> dict[str, Any]:
        """`0013` R8. Everything the log knows about one unit.

        **Beside the board, not instead of it.** `board()` still asks `cos.mjs` and still
        reads state out of the `Status:` line on disk (`intent.md` constraint 3); this
        answers from the transition log. Two sources during a transition is deliberate and
        has a cost, and `spec.md` C2 and C5 are where that cost is written down.

        `state` here is a projection over `transitions` and is computed, never stored —
        R1. It is returned alongside the transitions rather than instead of them precisely
        so a caller can check one against the other.

        `settled_edits` is carried because it is the unit of measure `intent.md` named:
        the number of times an artifact was rewritten after it had been settled, which
        was 0 before this and 43 in this repository on 2026-09-22.
        """
        self._workspace_or_refuse(cwd)
        history = self._history()
        key = self._journal_key(cwd)
        if history is None:
            return {
                "cwd": cwd,
                "unit": unit,
                "recording": False,
                "machine": "",
                "written_under": [],
                "mixed_state_sets": None,
                "transitions": [],
                "state": {},
                "settled_edits": 0,
                "sessions": [],
                "unknown_transitions": 0,
                "outputs": [],
                "output_counts": {},
            }
        try:
            rows = history.transitions(key, unit)
            state = history.state(key, unit)
            sessions = history.sessions_of(key, unit)
            outputs = history.outputs(key, unit)
            counts = history.output_counts(key, unit)
            written_under = history.machines_in(key, unit)
        except Busy as e:
            raise Invalid(str(e)) from e
        # `spec.md` C5. Rows written under one state set and read under another compare
        # words that never meant the same thing, and nothing about that failure looks like
        # a failure -- every query still returns rows. Said out loud in the payload rather
        # than refused, because refusing a *read* would hide the only evidence there is.
        # A caller that goes on to compare these against another source must stop here.
        foreign = [name for name in written_under if name != history.machine.name]
        return {
            "cwd": cwd,
            "unit": unit,
            "recording": True,
            "machine": history.machine.name,
            "written_under": written_under,
            "mixed_state_sets": (
                None if not foreign
                else f"this unit holds transitions written under {', '.join(foreign)}, "
                     f"but is being read under {history.machine.name} — the states in "
                     "those rows do not mean what they appear to mean here"
            ),
            "transitions": rows,
            "state": state,
            "settled_edits": len(settled_edits(rows, history.machine)),
            "sessions": sessions["sessions"],
            "unknown_transitions": sessions["unknown_transitions"],
            "outputs": outputs,
            "output_counts": counts,
        }

    def units_with_history(self, cwd: str) -> dict[str, Any]:
        """Every unit the log has a transition for, in the order they first appear.

        Not the same list as `board()`'s, and the difference is the point: a unit retired
        from the working tree still has a history, and this is the only place it can be
        seen. `.cos/` in this repository lost five units that way (`f506aae`).
        """
        self._workspace_or_refuse(cwd)
        history = self._history()
        if history is None:
            return {"cwd": cwd, "recording": False, "units": []}
        try:
            return {
                "cwd": cwd,
                "recording": True,
                "units": history.units(self._journal_key(cwd)),
            }
        except Busy as e:
            raise Invalid(str(e)) from e

    # -- sessions -----------------------------------------------------------

    def _is_member(self, cwd: str) -> bool:
        """The single membership question: env list, or a store entry under the root."""
        if self.config.is_workspace(cwd):
            return True
        return self.store is not None and self.store.resolves_to_entry(cwd)

    def _workspace_or_refuse(self, cwd: str) -> str:
        """The single gate. Every capability below goes through it.

        `spec.md` R21 wants this asked on every read rather than cached, because after
        the workspace list is no longer fixed for the life of the process.
        """
        # Recomputed from the working folder every time, so editing the store by hand
        # cannot widen what this accepts — the entry has to name a segment, and the
        # segment has to resolve back under the root.
        if self._is_member(cwd):
            return cwd
        raise Invalid(f"not a configured workspace: {cwd}")

    def sessions_for(self, cwd: str, limit: int | None = None) -> dict[str, Any]:
        self._workspace_or_refuse(cwd)
        rows = reader.list_for_directory(cwd, limit=limit)
        for row in rows:
            # Terminal sessions show up here too — the read layer sees them. This flag is
            # what tells a caller which of them it may write to (`spec.md` C1).
            row["resumable"] = self.config.may_resume(
                self.sessions.created_here(row["session_id"])
            )
        return {"cwd": cwd, "sessions": rows}

    def history(self, cwd: str, session_id: str) -> dict[str, Any]:
        self._workspace_or_refuse(cwd)
        if not session_id:
            raise Invalid("session_id is required")
        return {
            "session_id": session_id,
            "messages": reader.history(session_id, cwd),
        }

    def check_send(self, cwd: str, text: str) -> None:
        """Everything a caller can reject with a status code, decided before any output.

        Split out from `stream` on purpose. The design draws a hard line between two kinds of
        failure: an invalid request is a status code, while a refusal that surfaces once
        the reply is already streaming has to arrive as data, because the status line is
        long gone (`web.py` docstring on `post_send`). Validating inside an async
        generator would collapse that distinction, since the first item is only pulled
        after a caller has committed to streaming. A test in `web_test.py` holds the line.
        """
        self._workspace_or_refuse(cwd)
        self._refuse_while_updating()
        if not text.strip():
            raise Invalid("text is required")

    async def stream(
        self, cwd: str, text: str, session_id: str | None = None
    ) -> AsyncIterator[tuple[str, Any]]:
        """Yield `(kind, payload)` exactly as the session layer does.

        Re-runs `check_send` so the generator is safe on its own; the checks are pure, so
        doing them twice costs nothing and leaves no caller able to skip them.
        """
        self.check_send(cwd, text)
        # `0004_no-setting-says-which-model-runs-a-stage`. Chat is a row of the same table
        # as the stages.
        model, model_source = self._model_for(models.CHAT)
        async for item in self.sessions.stream(
            cwd, text, session_id, **({"model": model} if model is not None else {})
        ):
            if item[0] == "session":
                # `0019` plan step 2, risk 2. `api.py` treats every kind but `chunk` as
                # the terminal `done` row; forwarding this to chat would turn it into a
                # spurious one, mid-reply.
                continue
            if item[0] == "done":
                # Chat wrote nothing to the run log before this. Now one record per turn
                # says which model it asked for — the model a *new* client is created with.
                # A client already live keeps the model it was made with (plan Risk 7).
                journal = self._journal()
                if journal is not None:
                    try:
                        journal.append({
                            "kind": "chat",
                            "workspace": self._journal_key(cwd),
                            "unit": "",
                            "stage": "",
                            "model": model,
                            "model_source": model_source,
                            "session_id": (item[1] or {}).get("session_id", ""),
                        })
                    except (BadRecord, Busy):
                        pass  # a busy log must not cost the reply that was already paid for
            yield item

    # -- which model each stage runs on --------------------------------------
    #
    # `0004_no-setting-says-which-model-runs-a-stage`. The resolving is `coscc/models.py`;
    # this is where its three inputs are gathered: the stage list from `cos.mjs`, the
    # overrides from `prefs`, `COS_MODEL` from `Config`.

    def _model_overrides(self) -> tuple[dict[str, str], list[str]]:
        return models.overrides_from(Data(self.config.data_dir).pref_rows(models.PREFIX))

    def _effort_overrides(self) -> tuple[dict[str, str], list[str]]:
        return models.overrides_from(
            Data(self.config.data_dir).pref_rows(models.EFFORT_PREFIX), models.EFFORT_PREFIX
        )

    def _model_for(self, name: str) -> tuple[str | None, str]:
        """`(model, source)` for chat, and for Gebo on `impl`'s base row. Never raises on bad data.

        Takes no stage list: the caller has already checked `name` against `cos.mjs`
        (`run_step` found the row), and resolving one row does not need the others.
        A board step goes through `_stage_config` instead, which also reads the label.
        """
        overrides, _ = self._model_overrides()
        defaults, _ = models.load_defaults()
        return models.resolve(name, None, overrides, {}, defaults, self.config.model)[:2]

    def _stage_config(
        self, stage: str, stages: list[str], directory: Path, journal: Journal, key: str, unit: str
    ) -> dict[str, Any]:
        """`0033`. The label a step runs under, the model and effort it resolves to, and
        for `impl` which run of the unit's this is. Called after the gate, before any money
        is spent. The label chooses a configuration and nothing else (spec R11).

        `Busy` from the run log is left to the caller, as `failed_attempts` is.
        """
        try:
            plan_text: str | None = (Path(directory) / "plan.md").read_text(encoding="utf-8", errors="replace")
        except OSError:
            plan_text = None
        history = [r for r in journal.records(key, unit) if r.get("stage") == "impl"]
        label_declared, label, label_source = labels.label_for(stage, stages, plan_text, history)
        model, model_source, effort, effort_source = models.resolve(
            stage, label, self._model_overrides()[0], self._effort_overrides()[0],
            models.load_defaults()[0], self.config.model,
        )
        return {
            "model": model, "model_source": model_source,
            "effort": effort, "effort_source": effort_source,
            "label_declared": label_declared, "label": label, "label_source": label_source,
            # R10: every `start` of `impl` counts, the review-driven fixes included; the
            # reading "before the first `pr`" is done from the log (spec Answers, câu 2).
            "impl_run": (
                sum(1 for r in history if r.get("kind") == "start") + 1 if stage == "impl" else None
            ),
        }

    async def _findings_added(self, cwd: str, unit: str, before: set[Any]) -> dict[str, int]:
        """`0033` R10. The findings in the rounds a `review` step added, off the board —
        `parseReview`'s count, read the way `_post_new_rounds` reads it."""
        data = await board_reader.read(self._units_root(cwd))
        found = next((u for u in data["units"] if u["name"] == unit), None) or {}
        added = [r for r in found.get("rounds") or [] if r.get("n") not in before]
        return {
            "findings": sum(int(r.get("findings") or 0) for r in added),
            "findings_open": sum(int(r.get("findings_open") or 0) for r in added),
        }

    async def stage_models(self) -> dict[str, Any]:
        """Every row Settings shows: stage, agents, model, effort, where each came from.

        When `node` cannot run there is no stage list, and inventing one here would be the
        second copy of the loop. So the table is empty and `problems` says why.
        """
        try:
            stages = await board_reader.stages()
        except Unavailable as e:
            return {"rows": [], "problems": [str(e)], "cos_model": self.config.model}
        overrides, bad_rows = self._model_overrides()
        efforts, bad_efforts = self._effort_overrides()
        defaults, bad_defaults = models.load_defaults()
        table = models.table(stages, overrides, efforts, defaults, self.config.model, SESSIONS_PER_STEP)
        for r in table["rows"]:
            r["overridden"] = r["name"] in overrides
            r["effort_overridden"] = r["name"] in efforts
        table["problems"] = bad_defaults + bad_rows + bad_efforts + table["problems"]
        table["cos_model"] = self.config.model
        return table

    async def _setting_row(self, name: Any, allow_chat: bool) -> str:
        """Check a Settings row name against `cos.mjs`: a stage, `<stage>:novel` for a
        stage after `plan`, or `chat` when the setting has one."""
        if not isinstance(name, str) or not name:
            raise Invalid("name is required")
        try:
            stages = await board_reader.stages()
        except Unavailable as e:
            raise Invalid(str(e)) from e
        allowed = [r for r in models.rows_for(stages) if allow_chat or r != models.CHAT]
        if name not in allowed:
            raise Invalid(f"no such stage: {name} (use one of {', '.join(allowed)})")
        return name

    def _log_setting(self, key: str, old: Any, new: Any) -> None:
        journal = self._journal()
        if journal is not None:
            try:
                journal.append({
                    "kind": "setting", "workspace": "", "unit": "", "stage": "",
                    "name": key, "old": old, "new": new,
                })
            except (BadRecord, Busy) as e:
                raise Invalid(f"the setting was saved but not logged: {e}") from e

    async def set_stage_model(self, name: Any, model: Any = None) -> dict[str, Any]:
        """Set one row's model, or remove the override when `model` is None.

        **Behind the password like every route here** (`0070`): whoever holds it or a live
        session can move `review` to a weaker model, or every stage to a dearer one. The one trace is the `setting`
        record appended below, with the old and new value. It chooses a model and nothing
        else: no gate reads it, and no stage starts because of it.

        The model name is not checked against the API — an unknown one fails at the next
        step of that stage, with the CLI's own error (spec Out of scope).
        """
        name = await self._setting_row(name, allow_chat=True)
        if model is not None:
            if not isinstance(model, str) or not model.strip():
                raise Invalid("model is required")
            model = model.strip()

        data = Data(self.config.data_dir)
        key = models.PREFIX + name
        old = self._model_overrides()[0].get(name)
        if model is None:
            data.delete_pref(key)
        else:
            data.set_pref(key, model)
        self._log_setting(key, old, model)
        return await self.stage_models()

    async def set_stage_effort(self, name: Any, effort: Any = None) -> dict[str, Any]:
        """`0033` R9. Set one row's effort, or remove the override when `effort` is None.

        The same exposure as `set_stage_model`, and the `setting` record is the
        trace. `max` is accepted here and only here — `models.json` may not ship it, so
        every `max` run traces back to one of these records (spec R7, C8). `chat` has no
        effort (spec Out of scope).
        """
        name = await self._setting_row(name, allow_chat=False)
        if effort is not None and effort not in models.EFFORTS:
            raise Invalid(f"effort must be one of {', '.join(models.EFFORTS)}")

        data = Data(self.config.data_dir)
        key = models.EFFORT_PREFIX + name
        old = self._effort_overrides()[0].get(name)
        if effort is None:
            data.delete_pref(key)
        else:
            data.set_pref(key, effort)
        self._log_setting(key, old, effort)
        return await self.stage_models()

    # -- activity, usage and settings ---------------------------------------
    #
    # Three read-only methods. `spec.md` said `Service` would not change,
    # and this is the one place it does — recorded as a departure in `plan.md`. The
    # alternative was to let the new page read `Journal` and `policy` directly, and that
    # would break the rule this module exists for (see the module docstring), which is a
    # far worse trade than three methods that only read.

    def _records_or_none(self, cwd: str) -> list[dict[str, Any]] | None:
        """Every record for this workspace, or `None` when nothing is being recorded.

        `activity` and `usage` both want the same rows and are always called together by
        the Activity screen. Shared so the scan is written once — see `activity_and_usage`
        for why it is also *read* once.
        """
        self._workspace_or_refuse(cwd)
        journal = self._journal()
        if journal is None:
            return None
        try:
            return journal.records(self._journal_key(cwd))
        except Busy as e:
            raise Invalid(str(e)) from e

    def _events_of(self, cwd: str, rows: list[dict[str, Any]], limit: int) -> dict[str, Any]:
        events = [
            {
                "at": r.get("at") or "",
                "kind": r.get("kind") or "",
                "unit": r.get("unit") or "",
                "stage": r.get("stage") or "",
                "mode": r.get("mode") or "",
                "outcome": r.get("outcome") or "",
                "session_id": r.get("session_id") or "",
                "artifact": r.get("artifact") or "",
                "denials": int(r.get("denials") or 0),
                "cost": {f: r.get(f) for f in COST_FIELDS + (COST_USD,) if r.get(f)},
                # `0045` R10. A `hold` row's move, reason, name and side effects; empty on
                # every other kind.
                "from": r.get("from") or "",
                "to": r.get("to") or "",
                "reason": r.get("reason") or "",
                "by": r.get("by") or "",
                "effects": [e for e in r.get("effects") or [] if isinstance(e, dict)],
            }
            for r in rows
        ]
        events.reverse()
        return {"cwd": cwd, "events": events[:limit], "recording": True}

    def _usage_of(self, cwd: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        per_unit: dict[str, dict[str, Any]] = {}
        for record in rows:
            if record.get("kind") != "end":
                continue
            bucket = per_unit.setdefault(str(record.get("unit") or ""), zero_cost())
            add_cost(bucket, record)
        total = zero_cost()
        for bucket in per_unit.values():
            add_cost(total, bucket)
        return {"cwd": cwd, "total": total, "per_unit": per_unit, "recording": True}

    def activity(self, cwd: str, limit: int = 40) -> dict[str, Any]:
        """What has happened across the whole workspace, newest first.

        `timeline` answers the same question for one unit. This one exists because the
        Activity screen is workspace-wide, and building it by calling `timeline` once per
        unit would spawn one board read per unit to find out what the units are.
        """
        rows = self._records_or_none(cwd)
        if rows is None:
            return {"cwd": cwd, "events": [], "recording": False}
        return self._events_of(cwd, rows, limit)

    def usage(self, cwd: str) -> dict[str, Any]:
        """What this workspace has cost, added up from its records.

        Added rather than stored, for the reason `journal.totals` gives: a stored total is
        a second number that can disagree with the first.
        """
        rows = self._records_or_none(cwd)
        if rows is None:
            return {"cwd": cwd, "total": {}, "per_unit": {}, "recording": False}
        return self._usage_of(cwd, rows)

    def activity_and_usage(self, cwd: str, limit: int = 40) -> dict[str, Any]:
        """Both of the above, from one read.

        The Activity screen wants both at once. Calling the two public methods meant two
        connections and two full parses of the identical rows; they stay for the JSON API,
        and this is what the page calls.
        """
        rows = self._records_or_none(cwd)
        if rows is None:
            return {
                "cwd": cwd, "events": [], "total": {}, "per_unit": {}, "recording": False,
            }
        return {**self._events_of(cwd, rows, limit), **self._usage_of(cwd, rows)}

    def settings(self) -> dict[str, Any]:
        """The safety posture, as something a screen can render. Read only.

        `spec.md` R18: this screen shows the four knobs and the grant table and can
        change neither. There is no setter here for the same reason there is none in
        `config.from_env` — a request that could turn a knob is a request that could turn
        it on.

        **The model per stage is the one thing Settings can change**, and it is not here:
        `stage_models` and `set_stage_model` are. `0004_no-setting-says-which-model-runs-
        a-stage` made it changeable on purpose — a model is a choice of cost, not of
        capability, and the originator asked for it without a release. The price is a
        route that decides what every step spends for whoever holds the password or a live
        session. `cos_model` below is only
        the fallback for a row nothing else answers.
        """
        c = self.config
        return {
            "working_dir": c.working_dir,
            "data_dir": str(Data(c.data_dir).root),
            "host": c.host,
            "port": c.port,
            "cos_model": c.model,
            "knobs": [
                {
                    "name": "tools",
                    "value": ", ".join(c.effective_tools()) or "none",
                    "on": bool(c.effective_tools()),
                    "detail": "Chat sessions are created with this tool list. Empty means "
                              "chat only — a session with no tools cannot write a file.",
                },
                {
                    "name": "allow_write_and_exec",
                    "value": "on" if c.allow_write_and_exec else "off",
                    "on": c.allow_write_and_exec,
                    "detail": "While off, no write or exec tool survives into a session, "
                              "whatever the tool list says.",
                },
                {
                    "name": "bypass_permissions",
                    "value": "on" if c.bypass_permissions else "off",
                    "on": c.bypass_permissions,
                    "detail": "Off, and not settable over HTTP. The only way in is the "
                              "environment this process was started with.",
                },
                {
                    "name": "resume_foreign_sessions",
                    "value": "on" if c.resume_foreign_sessions else "off",
                    "on": c.resume_foreign_sessions,
                    "detail": "Off because it is untested, not because it is dangerous. "
                              "The app resumes only what it created.",
                },
            ],
            # The board's own grants, from `policy.py` rather than from the config. They
            # are separate on purpose, and the screen has to show that they are. `0062`
            # R9: a stage with its own `novel` ceilings shows them as `<stage>:novel`,
            # right after its own row.
            "grants": [
                {
                    "stage": name,
                    "tools": ", ".join(grant.tools) or "none",
                    "commands": ", ".join(grant.commands) or "none",
                    "max_turns": grant.max_turns,
                    "max_budget_usd": grant.max_budget_usd,
                    "app_writes_artifact": grant.app_writes_artifact,
                    "warning": grant.warning,
                }
                for stage, own in sorted(GRANTS.items())
                for name, grant in (
                    [(stage, own)]
                    + ([(f"{stage}:{labels.NOVEL}", grant_for_step(stage, labels.NOVEL))]
                       if stage in NOVEL_CEILINGS else [])
                )
            ],
            "prose_stages": list(PROSE_STAGES),
        }

    # -- artifacts and preferences ------------------------------------------

    def artifact(self, cwd: str, unit: str, stage: str) -> dict[str, Any]:
        """The text of one stage's artifact, or why there is none.

        The path is built by `runner.unit_dir`, which validates the unit name against the
        `NNNN_slug` shape. That is the same function the runner uses, so a name this
        refuses is a name no step could run against either — one rule, not two.
        """
        self._workspace_or_refuse(cwd)
        if stage not in STAGE_FILES:
            raise Invalid(f"no such stage: {stage}")
        filename = f"{stage}.md"
        path = self._unit_dir(cwd, unit) / filename
        if not path.is_file():
            return {"unit": unit, "stage": stage, "file": filename, "text": "", "exists": False}
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise Invalid(f"could not read {filename}: {e}") from e
        return {"unit": unit, "stage": stage, "file": filename, "text": text, "exists": True}

    # Which preferences the page may keep. An open key/value store reachable from a
    # request is a place to put anything; this is the list of things the Settings screen
    # actually remembers, and nothing else is writable.
    PREFERENCES = {"density": "comfortable", "screen": "overview", "board_view": "Board"}

    def preferences(self) -> dict[str, Any]:
        data = Data(self.config.data_dir)
        stored = data.prefs()
        return {k: stored.get(k, default) for k, default in self.PREFERENCES.items()}

    def set_preference(self, key: str, value: Any) -> dict[str, Any]:
        if key not in self.PREFERENCES:
            raise Invalid(f"not a stored preference: {key}")
        if not isinstance(value, (str, int, float, bool)):
            raise Invalid("a preference must be a simple value")
        Data(self.config.data_dir).set_pref(key, value)
        return {"key": key, "value": value}
