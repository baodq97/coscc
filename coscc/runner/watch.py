"""Watching a run, any agent's, with a unit or none: the events it recorded, a page at a time or
followed live. Held by `Core`."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any, NotRequired, TypedDict

from coscc.runlog import events
from coscc.runner import triggers
from coscc.runner.run import LIVE
from coscc.store.db import Data
from coscc.store.db import Busy
from coscc.kernel import Invalid
from coscc.config import Config
from coscc.units.workspaces import Workspaces


class StepEvent(TypedDict):
    """One recorded event. The first four fields are every event's; the rest are its kind's
    (`runlog/events.py`), and a field cut at `events.FIELD_MAX` is named in `truncated_fields`."""

    run: str
    seq: int
    at: int
    kind: str
    # A helper's event: the `agent_id` of the helper that made it.
    agent_id: NotRequired[str]
    role: NotRequired[str]
    text: NotRequired[str]
    thinking: NotRequired[str]
    id: NotRequired[str]
    name: NotRequired[str]
    input: NotRequired[Any]
    tool_use_id: NotRequired[str]
    is_error: NotRequired[bool]
    content: NotRequired[Any]
    tool: NotRequired[str]
    reason: NotRequired[str]
    # A `denied`'s: the grant the refusal lacked (`policy.lacked`), "" when its rule names none.
    lacked: NotRequired[str]
    # A `config`'s: what the run was granted, one phrase each (`policy.granted`).
    granted: NotRequired[list[str]]
    n: NotRequired[int]
    model: NotRequired[str | None]
    effort: NotRequired[str | None]
    num_turns: NotRequired[int]
    cost_usd: NotRequired[float | None]
    duration_ms: NotRequired[int]
    terminal_reason: NotRequired[str | None]
    outcome: NotRequired[str]
    detail: NotRequired[str]
    subtype: NotRequired[str | None]
    truncated: NotRequired[bool]
    length: NotRequired[int]
    truncated_fields: NotRequired[list[str]]


class EventsPage(TypedDict):
    run: str
    unit: str
    stage: str
    status: str
    events: list[StepEvent]
    first_seq: int | None
    has_older: bool
    last_at: int | None
    events_lost: int
    purged_at: str | None
    # Who pressed it (`person`, `autopilot`, ...), from the run's `start`; on the first page.
    started_by: NotRequired[str]
    # From the run's `end` in the run log, once it has one (on the first page): how it ended, why,
    # and a `draft` kind's object (Dagaz's), which nothing else keeps.
    outcome: NotRequired[str]
    detail: NotRequired[str]
    draft: NotRequired[dict[str, Any]]


class Watch:
    def __init__(self, config: Config, ws: Workspaces) -> None:
        self.config = config
        self.ws = ws

    # -- watching a step ------------------------------------------------------
    #
    # Two reads and nothing else: no row, no transition, no artifact, no gate, and nothing
    # reaches the step. Whoever holds the password or a live session reads every command,
    # path, thought and tool output a step saw.

    def _run_of(
        self, cwd: str, run: str
    ) -> tuple[events.Recorder | None, dict[str, Any] | None, str]:
        """The recorder running `run`, or its index row, and the unit it is of (`""` for none);
        refused unless it is a run of this workspace. `(None, None, unit)` for a `run` the run
        log names with no index row yet."""
        self.ws.check(cwd)
        key = self.ws.key(cwd)
        if not run:
            raise Invalid("a run is required")
        recorder = LIVE.get(run)
        if recorder is not None:
            if recorder.workspace != key:
                raise Invalid(f"run {run} is not a run of this workspace")
            return recorder, None, recorder.unit
        try:
            row = Data(self.config.data_dir).step_run(run)
        except Busy as e:
            raise Invalid(str(e)) from e
        if row is not None:
            if row["workspace"] != key:
                raise Invalid(f"run {run} is not a run of this workspace")
            return None, row, str(row["unit"] or "")
        if self._held(key, run):
            return None, None, ""
        journal = self.ws.journal()
        try:
            started = journal.records(key, kinds=("start", "end")) if journal is not None else []
        except Busy as e:
            raise Invalid(str(e)) from e
        for r in started:
            if r.get("run") == run:
                return None, None, str(r.get("unit") or "")
        raise Invalid(f"no such run: {run}")

    @staticmethod
    def _held(key: str, run: str) -> dict[str, str] | None:
        """The agent run `run` of workspace `key` that this process holds, before its `start` is
        written (a press hands the id out first)."""
        return next(
            (h for h in triggers.running() if h["run"] == run and h["workspace"] == key), None
        )

    def events_page(
        self,
        cwd: str,
        run: str,
        before: int | None = None,
        limit: int = events.PAGE_DEFAULT,
        seq: int | None = None,
    ) -> dict[str, Any]:
        """The last `limit` events of `run` below `before`, oldest first, or with `seq` that one
        event whole as stored. Same answer while the step runs (memory) and after (`step_events`).

        `status`: `running` while this process runs it; `purged` once its events were purged;
        `ended` with an end; `ended-unknown` when its index row has none; `none` for a `run`
        the run log names that never got an index row (the app went down before the first write)."""
        recorder, row, unit = self._run_of(cwd, run)
        limit = max(1, min(events.PAGE_MAX, int(limit)))
        out: dict[str, Any] = {
            "run": run,
            "unit": unit,
            "stage": "",
            "status": "none",
            "events": [],
            "first_seq": None,
            "has_older": False,
            "last_at": None,
            "events_lost": 0,
            "purged_at": None,
        }
        if recorder is not None:
            out.update(stage=recorder.stage, status="running", events_lost=recorder.lost)
            if recorder.events:
                out["last_at"] = recorder.events[-1]["at"]
            if seq is not None:
                found = [e for e in recorder.events if e["seq"] == int(seq)]
            else:
                below = [e for e in recorder.events if before is None or e["seq"] < int(before)]
                found = below[-limit:]
                out["has_older"] = len(below) > len(found)
        elif row is not None:
            out.update(
                stage=row["stage"],
                events_lost=int(row["lost"] or 0),
                purged_at=row["purged_at"],
                last_at=row["last_at"],
                status=(
                    "purged"
                    if row["purged_at"]
                    else "ended"
                    if row["ended_at"] is not None
                    else "ended-unknown"
                ),
            )
            try:
                data = Data(self.config.data_dir)
                if seq is not None:
                    one = data.step_event(run, int(seq))
                    found = [one] if one is not None else []
                else:
                    found, out["has_older"] = data.step_events_page(run, before, limit)
            except Busy as e:
                raise Invalid(str(e)) from e
        elif held := self._held(self.ws.key(cwd), run):
            out.update(stage=held["agent"], status="running")
            found = []
        else:
            found = []
        out["events"] = found
        out["first_seq"] = found[0]["seq"] if found else None
        if before is None and seq is None:
            out["started_by"] = self._started_by(self.ws.key(cwd), run, unit)
            if recorder is None:
                out.update(self._ended(self.ws.key(cwd), run, unit))
        return out

    def _started_by(self, workspace: str, run: str, unit: str) -> str:
        journal = self.ws.journal()
        if journal is None:
            return ""
        try:
            starts = journal.records(workspace, unit, kinds=("start",))
        except Busy as e:
            raise Invalid(str(e)) from e
        start = next((r for r in reversed(starts) if r.get("run") == run), None)
        return str(start.get("started_by") or "") if start else ""

    def _ended(self, workspace: str, run: str, unit: str) -> dict[str, Any]:
        """What the run's `end` says: `outcome`, `detail` and a `draft`; `{}` with no `end` yet."""
        journal = self.ws.journal()
        if journal is None:
            return {}
        try:
            ends = journal.records(workspace, unit, kinds=("end",))
        except Busy as e:
            raise Invalid(str(e)) from e
        end = next((r for r in reversed(ends) if r.get("run") == run), None)
        if end is None:
            return {}
        out = {"outcome": str(end.get("outcome") or ""), "detail": str(end.get("detail") or "")}
        if isinstance(end.get("draft"), dict):
            out["draft"] = end["draft"]
        return out

    async def follow_events(
        self,
        cwd: str,
        run: str,
        after: int = 0,
        gather: float = 0.0,
    ) -> AsyncGenerator[tuple[str, Any], None]:
        """`("events", [...])` for every event of a running `run` past `after`, in order, none
        twice, until its `end`; `("cut", n)` when this follower fell `SUB_LIMIT` behind (read
        again from `n`); one `("status", page)` when the `run` is not running here.

        Subscribes before it reads what is there. `gather` > 0 holds each batch up to that many
        seconds; an empty batch comes every `IDLE_WAKE` seconds so a caller can notice it should stop."""
        recorder, row, _ = self._run_of(cwd, run)
        # A press hands the id out before the run has begun: wait for its first event.
        while recorder is None and row is None and self._held(self.ws.key(cwd), run):
            await asyncio.sleep(0.1)
            recorder, row, _ = self._run_of(cwd, run)
        if recorder is None:
            yield ("status", self.events_page(cwd, run, limit=1))
            return
        q, backlog = recorder.subscribe(int(after))
        try:
            seen = int(after)
            if backlog:
                seen = backlog[-1]["seq"]
                yield ("events", backlog)
                if backlog[-1]["kind"] == "end":
                    return
            if recorder.closed:
                yield ("status", self.events_page(cwd, run, limit=1))
                return
            loop = asyncio.get_running_loop()
            last = loop.time()
            while True:
                try:
                    first = await asyncio.wait_for(q.get(), events.IDLE_WAKE)
                except asyncio.TimeoutError:
                    yield ("events", [])
                    continue
                wait = last + gather - loop.time()
                if gather > 0 and wait > 0:
                    await asyncio.sleep(wait)
                items = [first]
                while not q.empty():
                    items.append(q.get_nowait())
                batch: list[dict[str, Any]] = []
                cut: int | None = None
                for kind, value in items:
                    if kind == "cut":
                        cut = value
                        break
                    if value["seq"] > seen:
                        batch.append(value)
                        seen = value["seq"]
                last = loop.time()
                if batch:
                    yield ("events", batch)
                if cut is not None:
                    yield ("cut", cut)
                    return
                if batch and batch[-1]["kind"] == "end":
                    return
        finally:
            recorder.unsubscribe(q)

    async def purge_events(self) -> tuple[int, int]:
        """For a caller that holds a `Core`; `coscc/run.py` calls `events.purge_on_start`."""
        return await events.purge(Data(self.config.data_dir), self.ws.journal())
