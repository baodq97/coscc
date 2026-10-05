"""The autopilot: the loop that asks `coscc.loop next` and queues the stages it names."""

from __future__ import annotations

import asyncio
import math
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Iterable

from coscc.leif import decide, guide
from coscc.units import backlog, planmap, states
from coscc.git import fetches
from coscc.github import integrate, prmachine
from coscc.git.gitops import GitError
from coscc.store.journal import BadRecord, Journal, is_step
from coscc.store.db import Busy
from coscc.config import LOOPBACK, Config
from coscc.store.db import Data
from coscc.runner.queue import Refused
from coscc.units.board import shown_state
from coscc.units.read import count, number
from coscc.units.worktrees import BRANCH_REMOTE, BRANCH_TRUNK
from coscc.kernel import Invalid
from coscc.units.workspaces import Workspaces
from coscc.github.integration import Integration
from coscc.runner.queue import Holds
from coscc.runner.steps import Steps

if TYPE_CHECKING:
    from coscc.units.read import Board

log = logging.getLogger(__name__)


# The autopilot's settings live in the data root's `prefs`, not the workspace's repository. Three per workspace, keyed by
# the journal key; the cap is one for the whole app, since the quota is the machine's account.
# Not in `PREFERENCES`: those are the page's.

SETTINGS = ("autopilot", "autopilot_may_ship", "max_parallel", "daily_cap_usd")
CAP_PREF = "autopilot_daily_cap_usd"


def _whole_at_least_one(value: Any) -> bool:
    """`max_parallel`: an int, not a bool, 1 or more."""
    n = count(value)
    return n is not None and n >= 1


def _positive_number(value: Any) -> bool:
    """`daily_cap_usd`: a finite number above 0, not a bool."""
    n = number(value)
    return n is not None and math.isfinite(n) and n > 0


def _shortlist(records: Iterable[dict[str, Any]], key: str) -> tuple[dict[str, Any] | None, int]:
    """The last well-formed shortlist of workspace `key` in `records`, and its number."""
    return backlog.shortlist_of(r for r in records if r.get("workspace") == key)


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


def pref_name(name: str, key: str) -> str:
    return CAP_PREF if name == "daily_cap_usd" else f"{name}:{key}"


def autopilot_values(config: Config, key: str) -> dict[str, Any]:
    """The four values in effect. A hand-edited value of the wrong type reads as its
    default, and the default of both switches is off."""
    data = Data(config.data_dir)

    def read(name: str, ok: Any, default: Any) -> Any:
        value = data.pref(pref_name(name, key), default)
        return value if ok(value) else default

    return {
        "autopilot": read("autopilot", lambda v: isinstance(v, bool), False),
        "autopilot_may_ship": read("autopilot_may_ship", lambda v: isinstance(v, bool), False),
        "max_parallel": read("max_parallel", _whole_at_least_one, decide.DEFAULT_MAX_PARALLEL),
        "daily_cap_usd": float(
            read("daily_cap_usd", _positive_number, decide.DEFAULT_DAILY_CAP_USD)
        ),
    }


def off_loopback(config: Config) -> str:
    """Why the autopilot may not run on this bind, or `""`."""
    if config.host in LOOPBACK:
        return ""
    # No variable name here: the page shows it verbatim.
    return f"The app listens on {config.host}, beyond this machine; restart it on 127.0.0.1 to use the autopilot."


class Autopilot:
    def __init__(
        self,
        config: Config,
        ws: Workspaces,
        holds: Holds,
        budget: Callable[[], Mapping[str, Any]],
        agent_overrides: Callable[[], dict[str, Any]],
        steps: Steps,
        integration: Integration,
        boards: Board,
    ) -> None:
        self.config = config
        self.ws = ws
        self.holds = holds
        self.budget = budget
        self.agent_overrides = agent_overrides
        self.steps = steps
        self.integration = integration
        self.boards = boards
        # Per journal key: the lock every pass holds, the poll loop, the workspace directory it
        # was turned on for, the stops the last pass found by unit, the wake that no pass has
        # taken yet (the transitions that came with it), and the passes scheduled but not yet run.
        self.locks: dict[str, asyncio.Lock] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.cwds: dict[str, str] = {}
        self.waiting: dict[str, list[dict[str, Any]]] = {}
        self.stops: dict[str, dict[str, dict[str, str]]] = {}
        self.pending: set[asyncio.Task] = set()
        # What the last pass held back by unit, `(code, detail)`, for the card to show.
        self.held: dict[str, dict[str, tuple[str, str]]] = {}
        # The reader of each workspace's pull requests, while it is on.
        self.pr_readers: dict[str, asyncio.Task] = {}

    # The autopilot holds no rule of the loop. Each pass reads the board, the run log, the settings
    # and the workspace's last shortlist, and asks `next` for every unit on it and no other; with no
    # shortlist it asks nothing. `coscc/leif/decide.py` decides where to stop and what to queue;
    # what it queues is an attempt the scheduler starts and the gate is asked about in `_prepare`.
    # A refusal is the attempt's `refused` row, read on the next pass as a stop line, never a
    # second way past the gate. It holds no task: what runs is what the attempts say runs.

    def start(self, cwd: str) -> None:
        """Run a pass now and every `POLL_SECONDS` after, for as long as the switch is on. A second
        call for a workspace already running does nothing.
        """
        key = self.ws.key(cwd)
        self.cwds[key] = cwd
        task = self.tasks.get(key)
        if task is not None and not task.done():
            return
        self.tasks[key] = asyncio.get_running_loop().create_task(self._loop(key))
        # The reader of the workspace's pull requests lives and dies with it.
        self.pr_readers[key] = asyncio.get_running_loop().create_task(self._pr_reader_loop(key))

    def stop(self, key: str) -> None:
        """Turned off: no more passes and no more `gh` calls for it. A step it already started runs on
        to its end, as a person's would.
        """
        task = self.tasks.pop(key, None)
        if task is not None:
            task.cancel()
        reader = self.pr_readers.pop(key, None)
        if reader is not None:
            reader.cancel()
        self.waiting.pop(key, None)
        self.stops.pop(key, None)
        self.held.pop(key, None)

    def resume(self) -> list[str]:
        """At start-up, every workspace whose switch is on starts again. Returns them."""
        started = []
        for row in self.ws.all()["workspaces"]:
            if row["missing"]:
                continue
            if autopilot_values(self.config, self.ws.key(row["path"]))["autopilot"]:
                self.start(row["path"])
                started.append(row["path"])
        return started

    def _on(self, key: str) -> bool:
        task = self.tasks.get(key)
        return task is not None and not task.done()

    def nudge(self, key: str, woken_by: list[dict[str, Any]] | None = None) -> None:
        """A step or an integration ended or was refused, or an answer was written. One pass is
        scheduled and not waited for; nothing happens when the switch is off. A wake that comes
        while one is scheduled and has not begun joins it; one that comes while a pass runs
        schedules exactly one more, which the wakes after it join. `woken_by`: the transitions of
        the PR machine that scheduled it, which its picks record.
        """
        if not self._on(key):
            return
        if key in self.waiting:
            self.waiting[key].extend(woken_by or [])
            return
        self.waiting[key] = list(woken_by or [])
        task = asyncio.get_running_loop().create_task(self._woken(key))
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)

    async def _woken(self, key: str) -> None:
        """A scheduled pass: it waits its turn, takes the wakes that joined it, and runs once."""
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            taken = self.waiting.pop(key, None)
        if taken is not None:
            await self._guarded(key, taken or None)

    async def _loop(self, key: str) -> None:
        while True:
            await self._guarded(key)
            await asyncio.sleep(decide.POLL_SECONDS)

    async def _pr_reader_loop(self, key: str) -> None:
        """Every `ci_poll_seconds` of the lane config, one read of the workspace's pull requests. The
        300-second pass goes on beside it as the net.
        """
        poll = states.default_lanes().ci_poll_seconds
        while True:
            await asyncio.sleep(poll)
            try:
                await self.pr_read(key)
            except asyncio.CancelledError:
                raise
            except Exception:
                # The next read tries again; the pass is the net.
                log.exception("the pull request reader of %s failed", key)

    async def pr_read(self, key: str) -> prmachine.Read:
        """One read of the workspace's pull requests. Whatever it recorded schedules one pass for this
        workspace, however many transitions that was; a merge also schedules one for every other
        workspace the autopilot is on in, where a unit may depend on it.
        """
        cwd = self.cwds.get(key)
        if cwd is None or not self._on(key):
            return prmachine.Read()
        root = Path(cwd).expanduser().resolve()

        def directory_of(unit: str) -> Path:
            try:
                return self.ws.unit_dir(cwd, unit)
            except Invalid:
                return self.ws.units_root(cwd) / unit

        got = await self.integration.pr_machine().read(str(root), key, directory_of)
        if not got.moved:
            return got
        merged = [c for c in got.causes if c["transition"] == "merged"]
        # A merge made on GitHub is followed by no `ship` step, so its `ship` row and cleanup
        # are the reader's, before the pass it schedules.
        for c in merged:
            await self.integration.shipped(cwd, key, c["unit"], "shipped")
        self.nudge(key, got.causes)
        if merged:
            for other in list(self.tasks):
                if other != key:
                    self.nudge(other, merged)
        return got

    async def _guarded(self, key: str, woken_by: list[dict[str, Any]] | None = None) -> None:
        """A pass that raises leaves a stop line saying so, not a dead loop."""
        try:
            await (self.run_pass(key, woken_by) if woken_by else self.run_pass(key))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Shown on the board, never swallowed.
            log.exception("the autopilot pass of %s failed", key)
            self.set_stops(
                key, {"": {"unit": "", "kind": "f", "reason": f"the autopilot's pass failed: {e}"}}
            )

    def _running(self, key: str) -> list[dict[str, Any]]:
        """What holds a unit in this workspace now, by unit: every attempt not ended, a queued one
        and a person's steps included."""
        out: dict[str, dict[str, Any]] = {}
        for row in self.holds.attempts.unfinished(key):
            if row["unit"]:
                out[row["unit"]] = {"unit": row["unit"], "stage": row["stage"] or row["machine"]}
        return list(out.values())

    def _refusals(self, key: str) -> dict[str, dict[str, Any]]:
        """Per unit, the attempt the autopilot queued that the gate refused, while it is the unit's
        last attempt: `{stage, code, at}`. Read from the rows, not from anything this process kept."""
        try:
            rows = self.holds.attempts.latest(key)
        except sqlite3.Error, Busy:
            return {}
        return {
            r["unit"]: {"stage": r["stage"], "code": r["outcome"], "at": r["at"]}
            for r in rows
            if r["state"] == "refused" and r["started_by"] == "autopilot"
        }

    def _ci_read(self, key: str, unit: str) -> tuple[str, list[dict[str, Any]]]:
        """The head the PR machine read for the unit, and the required checks it held at that head;
        `("", [])` when it has read none."""
        try:
            history = self.integration.pr_machine().history
            now = prmachine.state(history, key, unit)
            head, number = str(now.get("head") or ""), now.get("number")
            held = prmachine.ci_held(history, key, int(number), head) if number and head else None
        except sqlite3.Error, OSError, Busy, ValueError, TypeError:
            return "", []
        return str((held or {}).get("head") or head), list((held or {}).get("checks") or [])

    def _ci_recorded_red(self, key: str, unit: str, head: str) -> bool:
        """Whether the PR machine recorded `red` at `head`, the pull request's head the board read
        (at the head it last read when the board read none): it reruns a red head once first."""
        try:
            now = prmachine.state(self.integration.pr_machine().history, key, unit)
        except sqlite3.Error, OSError, Busy:
            return False
        return now.get("ci") == "red" and (not head or now.get("head") == head)

    def _files(self, cwd: str, unit: str) -> set[str] | None:
        try:
            return planmap.files_of(
                (self.ws.unit_dir(cwd, unit) / "plan.md").read_text(encoding="utf-8")
            )
        except Invalid, OSError:
            return None

    def cap(self, records: list[dict[str, Any]], limit: float) -> dict[str, Any]:
        """The figures for a pass and for the board: every workspace, every starter."""
        now = datetime.now().astimezone()
        budget = self.budget()
        spent = decide.spent_today(records, now, budget)
        active = {
            (row["workspace"], row["unit"]): row["stage"] or row["machine"]
            for row in self.holds.attempts.unfinished()
            if row["unit"]
        }
        running = decide.reserved(
            records, now, [(k, unit, stage) for (k, unit), stage in active.items()], budget
        )
        return {
            "limit": limit,
            "spent": round(spent["known"] + spent["estimated"], 2),
            "known": round(spent["known"], 2),
            "estimated": round(spent["estimated"], 2),
            "estimated_count": spent["estimated_count"],
            "running": round(running, 2),
            "day": decide.today(now),
        }

    def set_stops(
        self,
        key: str,
        found: dict[str, dict[str, str]],
        asked: set[str] | None = None,
    ) -> None:
        """Keep the stops a pass found, and log each unit's that changed.

        `asked`: the units this pass looked at, when it did not look at all of them; the others keep
        what they had, the workspace's own stop among them. The log is an `autopilot-stop` record per
        change, `stop` empty once it cleared, which tells a person's press at a stop from one outside
        them. The workspace's own stop, unit `""`, is logged the same way, so a notice can say it.
        """
        before = self.stops.get(key, {})
        if asked is None:
            after = dict(found)
        else:
            after = {**{u: s for u, s in before.items() if u not in asked}, **found}
        self.stops[key] = after
        journal = self.ws.journal()
        if journal is None:
            return
        for unit in sorted(set(before) | set(after)):
            old, new = before.get(unit), after.get(unit)
            if (old or {}).get("kind") == (new or {}).get("kind"):
                continue
            try:
                journal.append(
                    {
                        "kind": "autopilot-stop",
                        "workspace": key,
                        "unit": unit,
                        "stage": "",
                        "stop": (new or {}).get("kind", ""),
                        "reason": (new or {}).get("reason", ""),
                    }
                )
            except BadRecord, Busy:
                pass

    async def run_pass(self, key: str, woken_by: list[dict[str, Any]] | None = None) -> None:  # noqa: C901, PLR0915 - still to split
        """One look at a workspace: follow its shortlist, find each listed unit's stop or why it waits,
        then queue what may start, highest first, each after its record. Nothing here starts a step:
        the attempt it queues is the scheduler's.
        """
        cwd = self.cwds.get(key)
        if cwd is None or not self._on(key):
            return
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            settings = autopilot_values(self.config, key)
            if not settings["autopilot"]:
                return
            refused = off_loopback(self.config)
            if refused:
                # `COS_HOST` can change after the switch was turned on.
                self.set_stops(key, {"": {"unit": "", "kind": "f", "reason": refused}})
                return
            journal = self.ws.journal()
            if journal is None:
                return
            data = await self.boards.read(cwd)
            try:
                records = journal.records(
                    kinds=(
                        "start",
                        "end",
                        "integration",
                        "shortlist",
                        "answer",
                        "screens",
                        "autopilot-pick",
                        prmachine.RECORD_KIND,
                    ),
                )
            except Busy as e:
                self.set_stops(key, {"": {"unit": "", "kind": "f", "reason": str(e)}})
                return
            # The last well-formed shortlist, read again on every pass.
            listed, n = _shortlist(records, key)
            if listed is None or not listed["units"]:
                # Nothing is asked and nothing starts.
                if not self._on(key) or not autopilot_values(self.config, key)["autopilot"]:
                    return
                self.set_stops(
                    key, {"": {"unit": "", "kind": "shortlist", "reason": decide.NO_SHORTLIST}}
                )
                return
            names = list(listed["units"])
            # A listed unit at `ship` is decided on the `origin/main` the remote has now: `behind` below
            # and the `ship` gate `next` asks both read that ref, and neither fetches. Through the
            # coordinator, which reuses a fetch under `REUSE_SECONDS`. A fetch that fails leaves the ref as
            # it was, and each such unit's stop says so.
            at_ship = {
                u["name"]
                for u in data["units"]
                if u["name"] in names and u.get("between_pr_and_ship") and u.get("at") == "ship"
            }
            unfetched: dict[str, Any] | None = None
            if at_ship:
                try:
                    await fetches.fetch(
                        Path(cwd).expanduser().resolve(), BRANCH_REMOTE, BRANCH_TRUNK
                    )
                except GitError as e:
                    unfetched = {"outcome": "failed", "detail": str(e)}
                else:
                    data = await self.boards.read(cwd)
            last: dict[str, dict[str, Any]] = {}
            integrations: dict[str, dict[str, Any]] = {}
            # The `start` of the step each unit's last `end` closed, the latest of that unit and stage
            # before it, so a recording `ship` that ran out is told apart.
            starts: dict[tuple[str, str], dict[str, Any]] = {}
            began: dict[str, dict[str, Any] | None] = {}
            for r in records:
                if r.get("workspace") == key and r.get("kind") == "start" and is_step(r):
                    starts[(str(r.get("unit") or ""), str(r.get("stage") or ""))] = r
                # A retake of the screenshots that failed is the unit's last word too, and so is a `pr` or
                # `ship` the PR machine ran.
                if (
                    r.get("workspace") == key
                    and r.get("kind") in ("end", "integration", "screens", prmachine.RECORD_KIND)
                    and is_step(r)
                ):
                    last[str(r.get("unit") or "")] = r
                    if r.get("kind") == "integration":
                        integrations[str(r.get("unit") or "")] = r
                    if r.get("kind") == "end":
                        began[str(r.get("unit") or "")] = starts.get(
                            (str(r.get("unit") or ""), str(r.get("stage") or ""))
                        )

            running = self._running(key)
            here = {r["unit"]: r["stage"] for r in running}
            refusals = self._refusals(key)
            at_pass = datetime.now().astimezone()
            board = {u["name"]: u for u in data["units"]}
            found: dict[str, dict[str, str]] = {}
            candidates: list[dict[str, Any]] = []
            budget = self.budget()
            # Why each unit that is no candidate waits, for the units ranked below it.
            reasons: dict[str, tuple[str, str]] = {}
            # Every unit on the shortlist is asked, and no other.
            for rank, name in enumerate(names, 1):
                u = board.get(name)
                if u is None:
                    reasons[name] = ("missing", "")
                    continue
                try:
                    nxt = await self.steps.next_step(cwd, name)
                except Invalid as e:
                    if decide.is_waiting(e):
                        reasons[name] = (
                            ("ci", "")
                            if decide.is_ci_pending(e)
                            else ("running", here.get(name, ""))
                        )
                        continue
                    found[name] = {"unit": name, "kind": "f", "reason": str(e)}
                    if isinstance(e, Refused) and e.reasons:
                        found[name]["code"] = e.reasons[0]
                    reasons[name] = (
                        ("running", here[name]) if name in here else decide.stop_reason(found[name])
                    )
                    continue
                # `next` reads the checks of `gh` itself: its red waits like `ci-pending` until the
                # PR machine, having rerun the head once, recorded `red` there.
                if decide.is_ci_red(nxt) and not self._ci_recorded_red(
                    key, name, str((u.get("integration") or {}).get("pr_head") or "")
                ):
                    reasons[name] = ("ci", "")
                    continue
                # A first `exhausted` step of a stage other than `ship` runs again once; so does a first prose
                # step whose reply lacked its opening.
                last_stage = str((last.get(name) or {}).get("stage") or "")
                ran_out = decide.exhausted_of(records, key, name, last_stage)
                unopened = decide.unopened_of(records, key, name, last_stage)
                # No `start` found reads as no recording `ship`.
                recorded = (began.get(name) or {}).get("ship_mode") == "record"
                shipping = here.get(name) == "ship"
                stop = decide.stop_for(
                    u,
                    nxt,
                    last.get(name),
                    settings["autopilot_may_ship"],
                    ran_out,
                    unopened,
                    recorded,
                    shipping,
                )
                stage = nxt.get("stage") or ""
                info = u.get("integration") or {}
                # Not while its step runs, whose `start` is already in the window.
                own = (
                    None
                    if name in here
                    else decide.after_own_integration(
                        info,
                        integrations.get(name),
                        decide.since_integration(records, key, name),
                        nxt,
                        decide.exhausted_of(records, key, name, "impl"),
                    )
                )
                # A unit behind `main`, conflicting or red after integration is integrated first, also after a
                # `pass`, but only where the autopilot may ship (otherwise a person merges and the round is
                # theirs: GitHub would refuse the merge). A rebase that leaves the unit's patch unchanged opens
                # `ship` again once CI is green; one that changes it closes `ship` until a new round passes.
                # CI red after the autopilot's own integration runs `impl` once if `next` names it, and is not
                # integrated again, nor once `main` has moved on or the pull request conflicts, which would open
                # a new window.
                rounds = u.get("rounds") or []
                passed = bool(rounds) and rounds[-1].get("verdict") == "pass"
                if (
                    info.get("state") in integrate.BUTTON_STATES
                    and (not passed or settings["autopilot_may_ship"])
                    and (stop is None or stop["kind"] == "f")
                ):
                    if own is not None and (
                        info.get("state") == "red-after-integration" or own[1] is not None
                    ):
                        stage, stop = own
                    else:
                        stop, stage = None, "integrate"
                # Once the `impl` pushed: the board no longer reads the head as the integrated one, and only
                # `next`'s words say CI is still red.
                if stop is None and stage == "impl" and own is not None and own[1] is not None:
                    stage, stop = own
                # A draft whose questions are all answered runs again, at most `MAX_RERUNS` times, and only on
                # an answer given since its last run; with none, it is a stop. Before `reason`, which raises on
                # no stage and no stop.
                rerun = False
                if stop is None and not stage and nxt.get("rerun"):
                    if decide.reruns_of(records, key, name, nxt["rerun"]) >= decide.MAX_RERUNS:
                        artifact = next(
                            (
                                s["file"]
                                for s in u.get("stages") or []
                                if s["stage"] == nxt["rerun"]
                            ),
                            nxt["rerun"],
                        )
                        stop = decide.rerun_stop(artifact)
                    elif not decide.answered_since_start(records, key, name, nxt["rerun"]):
                        stop = decide.stop_for(
                            u,
                            {**nxt, "rerun": ""},
                            last.get(name),
                            settings["autopilot_may_ship"],
                            ran_out,
                            unopened,
                            recorded,
                            shipping,
                        )
                    else:
                        stage, rerun = nxt["rerun"], True
                # The unit's `impl` queued again with a note of the app's: a draft `impl.md` that
                # `next` says to go on with, or a red CI. Each head gets `MAX_TRIES`.
                app_note = ""
                extra: dict[str, Any] = {}
                if stop is None and not stage and decide.continues(nxt):
                    head, _ = self._ci_read(key, name)
                    tries = decide.tries_on_head(records, key, name, head)
                    if tries >= decide.MAX_TRIES:
                        stop = decide.tries_stop("impl", tries)
                    else:
                        stage, app_note, extra = (
                            "impl",
                            decide.CONTINUE_NOTE,
                            {"continued": True},
                        )
                elif stop is None and stage == "impl" and decide.is_ci_red(nxt):
                    head, checks = self._ci_read(key, name)
                    tries = decide.tries_on_head(records, key, name, head)
                    if tries >= decide.MAX_TRIES:
                        stop = decide.tries_stop("impl", tries)
                    else:
                        app_note = decide.ci_note(head, checks)
                        extra = {"ci_note": app_note}
                # What the gate said to the autopilot's last attempt of the unit, when it would queue
                # that same stage again.
                if stop is None and stage and name not in here:
                    stop, why_not = decide.after_refusal(
                        refusals.get(name), stage, at_pass, fresh=bool(woken_by)
                    )
                    if why_not is not None:
                        reasons[name] = why_not
                        continue
                if stop is not None and unfetched is not None and name in at_ship:
                    note = integrate.origin_note(str(info.get("origin_sha") or ""), unfetched)
                    stop = {**stop, "reason": f"{stop['reason']}; {note}"}
                reason = (
                    ("running", here[name]) if name in here else decide.reason_for(nxt, stage, stop)
                )
                if reason is not None:
                    reasons[name] = reason
                if stop is not None:
                    found[name] = {"unit": name, **stop}
                    continue
                if not stage:
                    continue
                files = self._files(cwd, name) if stage in decide.CODE_STAGES else None
                # The exhausted `ship` this pick went past. Not once the stage became `integrate`, which
                # skipped nothing.
                skipped = stage == "ship" and decide.skips_exhausted(nxt, last.get(name), recorded)
                candidates.append(
                    {
                        "unit": name,
                        "stage": stage,
                        "files": files,
                        "rank": rank,
                        "need": decide.reservation(stage, budget),
                        "rerun": rerun,
                        "note": app_note,
                        "extra": extra,
                        "past_exhausted": {"at": last[name].get("at")} if skipped else None,
                    }
                )

            for r in running:
                if r["stage"] in decide.CODE_STAGES:
                    r["files"] = self._files(cwd, r["unit"])
            now = datetime.now().astimezone()
            # A `start` with no `end`, from a process before this one, counts against N for 24 hours.
            elsewhere = sum(
                1 for (k, unit) in decide.open_starts(records, now) if k == key and unit not in here
            )
            cap = self.cap(records, settings["daily_cap_usd"])
            room = cap["limit"] - cap["spent"] - cap["running"]
            # The pull requests the PR machine holds open, with the files it read.
            try:
                prs = prmachine.open_prs(self.integration.pr_machine().history, key)
            except sqlite3.Error, OSError, Busy:
                prs = []
            picked = decide.pick(
                candidates, running, settings["max_parallel"] - elsewhere, room, prs
            )
            reasons.update(picked["held"])
            est = f" ({cap['estimated']:.2f} estimated)" if cap["estimated_count"] else ""
            for c in picked["capped"]:
                found[c["unit"]] = {
                    "unit": c["unit"],
                    "kind": "cap",
                    "reason": (
                        f"spent {cap['spent']:.2f}{est} + running {cap['running']:.2f} + {c['stage']} "
                        f"{c['need']:.2f} is over the cap of {cap['limit']:.2f} USD ({cap['day']})"
                    ),
                }
                reasons[c["unit"]] = decide.stop_reason(found[c["unit"]])
            # A run again that `max_parallel` alone held back says so. Any other candidate held back that
            # way still says nothing.
            left = {c["unit"] for c in picked["chosen"] + picked["capped"]} | set(picked["held"])
            for c in candidates:
                if c["rerun"] and c["unit"] not in left:
                    found[c["unit"]] = {
                        "unit": c["unit"],
                        **decide.full_stop(c["stage"], settings["max_parallel"]),
                    }
                    reasons[c["unit"]] = decide.stop_reason(found[c["unit"]])
            # Raises before anything is recorded or started when a unit above one chosen has no
            # reason; `_guarded` shows it as a stop line.
            passed = decide.passed_for(names, [c["unit"] for c in picked["chosen"]], reasons)
            # The switch may have been turned off while this pass read the board and `next`. Nothing from
            # here on awaits, so nothing starts once it is off.
            if not self._on(key) or not autopilot_values(self.config, key)["autopilot"]:
                return
            self.set_stops(key, found)
            self.held[key] = dict(picked["held"])
            run_id = uuid.uuid4().hex
            shortlist = {"n": n, "at": listed.get("at"), "units": names}
            for c, over in zip(picked["chosen"], passed):
                # No record, no start, and nothing ranked below it either, since starting one would pass over
                # a unit chosen with no record of it.
                try:
                    journal.append(
                        {
                            "kind": "autopilot-pick",
                            "workspace": key,
                            "unit": c["unit"],
                            "stage": c["stage"],
                            "pass": run_id,
                            "rank": c["rank"],
                            "shortlist": shortlist,
                            "passed": over,
                            **c["extra"],
                            **(
                                {"past_exhausted": c["past_exhausted"]}
                                if c.get("past_exhausted")
                                else {}
                            ),
                            # The transitions whose read scheduled this pass.
                            **({"woken_by": woken_by} if woken_by else {}),
                        }
                    )
                except (BadRecord, Busy) as e:
                    self.set_stops(
                        key,
                        {
                            **found,
                            "": {
                                "unit": "",
                                "kind": "f",
                                "reason": f"could not record the autopilot's choice, so nothing more was started: {e}",
                            },
                        },
                    )
                    return
                self._queue(key, cwd, c)

    def _queue(self, key: str, cwd: str, c: dict[str, Any]) -> None:
        """Queue one chosen step or integration, written before the pass returns. A refusal at once
        is a stop line with the words of the refusal, unless it is a race or the database was held,
        which the next pass asks again; the units after it in the pass are queued all the same."""
        unit, stage = c["unit"], c["stage"]
        try:
            if stage == "integrate":
                self.integration.enqueue_integration(cwd, unit)
            else:
                self.steps.enqueue_step(cwd, unit, stage, c["note"])
        except Busy as e:
            log.warning("the autopilot could not queue %s of %s: %s", stage, unit, e)
        except (Refused, Invalid) as e:
            if decide.is_waiting(e) or self.holds.busy(key, unit):
                log.info("the autopilot did not queue %s of %s: %s", stage, unit, e)
                return
            log.warning("the autopilot could not queue %s of %s: %s", stage, unit, e)
            stop = {"unit": unit, "kind": "f", "reason": str(e)}
            if isinstance(e, Refused) and e.reasons:
                stop["code"] = e.reasons[0]
            self.set_stops(key, {unit: stop}, {unit})

    def _block(self, key: str) -> dict[str, Any]:
        """What the board shows of the autopilot. Display only; decides nothing."""
        values = autopilot_values(self.config, key)
        on = values["autopilot"]
        block: dict[str, Any] = {
            "on": on,
            "may_ship": values["autopilot_may_ship"],
            "max_parallel": values["max_parallel"],
            "cap": None,
            "stops": [],
            "refused_because": off_loopback(self.config) if on else "",
        }
        if not on:
            return block
        journal = self.ws.journal()
        if journal is not None:
            try:
                block["cap"] = self.cap(
                    journal.records(kinds=("start", "end")), values["daily_cap_usd"]
                )
            except Busy:
                block["cap"] = None
        block["stops"] = sorted(
            (self.stops.get(key) or {}).values(),
            key=lambda s: (decide.unit_number(s["unit"]), s["unit"]),
        )
        return block

    def show(self, key: str, data: dict[str, Any]) -> None:
        """What the board shows of the autopilot, on a board `read` returned. Display only."""
        for unit in data["units"]:
            # The code the last autopilot pass held the unit back with, and its
            # detail (`overlap-pr #7`); display only, and nothing while the autopilot is off.
            held = (self.held.get(key) or {}).get(unit["name"])
            unit["held"] = " ".join(p for p in held if p) if held else ""
        data["autopilot"] = self._block(key)
        data["guide"] = self.guide_block(key, data["units"])

    def guide_block(self, key: str, units: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
        """The board's guide: `{on, running, needs_you, held, notes, shortlist_empty}`, or
        `{on: False}` alone while the autopilot is off. `units` are those of the board read:
        `needs_you` has one item per unit its card labels `Needs you`: its `state` with what
        runs laid over, as the card has it (`shown_state`). What runs is read from memory, and
        `shortlist_empty` from the run log on every call, as a pass reads it. Display only;
        decides nothing.
        """
        if not autopilot_values(self.config, key)["autopilot"]:
            return {"on": False}
        entries = self.boards.running_here(key, self.agent_overrides())
        units = [
            {**u, "state": shown_state(u.get("state") or {}, entries.get(u.get("name")))}
            for u in units
        ]
        stops = sorted(
            (self.stops.get(key) or {}).values(),
            key=lambda s: (decide.unit_number(s["unit"]), s["unit"]),
        )
        return {
            "on": True,
            "running": guide.running(entries),
            "needs_you": guide.needs_you(units, stops),
            "held": guide.held(units, stops),
            "notes": guide.notes(stops),
            "shortlist_empty": self._shortlist_empty(key),
        }

    def _shortlist_empty(self, key: str) -> bool:
        """No shortlist was ever saved, or the one in effect has no unit; `False` where the run
        log cannot be read now. The read of `run_pass`, so there is one rule."""
        journal = self.ws.journal()
        if journal is None:
            return False
        try:
            records = journal.records(kinds=("shortlist",))
        except Busy:
            return False
        listed, _ = _shortlist(records, key)
        return listed is None or not listed["units"]

    def settings(self, cwd: str) -> dict[str, Any]:
        """The four settings of one workspace, and whether the bind lets the autopilot run."""
        self.ws.check(cwd)
        return {
            "cwd": cwd,
            **autopilot_values(self.config, self.ws.key(cwd)),
            "refused_because": off_loopback(self.config),
        }

    def today(self, cwd: str) -> tuple[float, float] | None:
        """What every workspace spent today and the daily cap, the autopilot on or off; `None`
        when the journal cannot be read now. Display only."""
        self.ws.check(cwd)
        journal = self.ws.journal()
        if journal is None:
            return None
        limit = autopilot_values(self.config, self.ws.key(cwd))["daily_cap_usd"]
        try:
            return self.cap(journal.records(kinds=("start", "end")), limit)["spent"], limit
        except Busy:
            return None

    def set_setting(self, cwd: str, name: Any, value: Any) -> dict[str, Any]:
        """Set one of the four. A wrong value is refused and nothing is written.

        Behind the password like every route: whoever holds it can turn the autopilot on,
        raise the cap, or let it ship. The trace is the `setting` record. Turning it on is
        refused while the app listens beyond loopback.
        """
        self.ws.check(cwd)
        if name not in SETTINGS:
            raise Invalid(f"no such setting: {name} (use one of {', '.join(SETTINGS)})")
        if name in ("autopilot", "autopilot_may_ship"):
            if value is not True and value is not False:
                raise Invalid(f"{name} must be true or false")
        elif name == "max_parallel":
            if not _whole_at_least_one(value):
                raise Invalid("max_parallel must be a whole number, 1 or more")
        elif not _positive_number(value):
            raise Invalid("daily_cap_usd must be a number above 0")
        if name == "autopilot" and value and off_loopback(self.config):
            raise Invalid(f"the autopilot was not turned on: {off_loopback(self.config)}")
        key = self.ws.key(cwd)
        old = autopilot_values(self.config, key)[name]
        stored = float(value) if name == "daily_cap_usd" else value
        Data(self.config.data_dir).set_pref(pref_name(name, key), stored)
        log_setting(self.ws.journal(), pref_name(name, key), old, stored)
        if name == "autopilot":
            if value:
                self.start(cwd)
            else:
                self.stop(key)
        return self.settings(cwd)
