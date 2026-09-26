"""Watching a running step: the events it recorded, a page at a time or followed live.

Split from `coscc/service.py` (`0095`), whose `Service` inherits it; a mixin with no fields.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from coscc import backlog
from coscc import events
from coscc.data import Data
from coscc.journal import Busy
from coscc.service_common import Invalid


class WatchMixin:

    # -- watching a step (`0073`) ---------------------------------------------
    #
    # Two reads and nothing else: no row, no transition, no artifact, no gate asked, and
    # nothing reaches the step (R15). Whoever holds the password or a live session reads
    # every command, path, thought and tool output a step saw (R16).

    def _run_of(self, cwd: str, unit: str, run: str) -> tuple[events.Recorder | None, dict[str, Any] | None]:
        """The recorder running `run`, or its index row -- refused unless it is `unit`'s, in
        this workspace. `(None, None)` for a `run` the run log names with no index row yet."""
        self._workspace_or_refuse(cwd)
        key = self._journal_key(cwd)
        if not run:
            raise Invalid("a run is required")
        recorder = self._recorders.get(run)
        if recorder is not None:
            if (recorder.workspace, recorder.unit) != (key, unit):
                raise Invalid(f"run {run} is not a step of {unit}")
            return recorder, None
        try:
            row = Data(self.config.data_dir).step_run(run)
        except Busy as e:
            raise Invalid(str(e)) from e
        if row is not None:
            if (row["workspace"], row["unit"]) != (key, unit):
                raise Invalid(f"run {run} is not a step of {unit}")
            return None, row
        journal = self._journal()
        try:
            started = journal.records(key, unit, kind="start") if journal is not None else []
        except Busy as e:
            raise Invalid(str(e)) from e
        if any(r.get("run") == run for r in started):
            return None, None
        raise Invalid(f"no such run of {unit}: {run}")

    def events_page(
        self, cwd: str, unit: str, run: str, before: int | None = None,
        limit: int = events.PAGE_DEFAULT, seq: int | None = None,
    ) -> dict[str, Any]:
        """R7. The last `limit` events of `run` below `before`, oldest first -- or, with `seq`,
        that one event whole as stored. The same answer while the step runs (from memory)
        and after it ended (from `step_events`).

        `status`: `running` while this process runs it; `purged` once R14 took its events;
        `ended` with an end; `ended-unknown` when its index row has none -- `0051`'s "a
        `start` with no `end`", read off the index row; `none` for a `run` the run log names
        that never got an index row, as when the app went down before the first write."""
        recorder, row = self._run_of(cwd, unit, run)
        limit = max(1, min(events.PAGE_MAX, int(limit)))
        out: dict[str, Any] = {
            "run": run, "unit": unit, "stage": "", "status": "none", "events": [],
            "first_seq": None, "has_older": False, "last_at": None, "events_lost": 0,
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
                stage=row["stage"], events_lost=int(row["lost"] or 0), purged_at=row["purged_at"],
                last_at=row["last_at"],
                status=(
                    "purged" if row["purged_at"] else "ended" if row["ended_at"] is not None
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
        else:
            found = []
        out["events"] = found
        out["first_seq"] = found[0]["seq"] if found else None
        return out

    async def follow_events(
        self, cwd: str, unit: str, run: str, after: int = 0, gather: float = 0.0,
    ) -> AsyncIterator[tuple[str, Any]]:
        """R8. `("events", [...])` for every event of a running `run` past `after`, in order,
        none twice, until its `end`; `("cut", n)` when this follower fell `SUB_LIMIT` behind
        (read again from `n`); one `("status", page)` when the `run` is not running here.

        Subscribes before it reads what is there (`spike.md ## U3`). `gather` > 0 holds each
        batch up to that many seconds, as the page does; an empty batch comes every
        `IDLE_WAKE` seconds with nothing new, so a caller can notice it should stop."""
        recorder, _ = self._run_of(cwd, unit, run)
        if recorder is None:
            yield ("status", self.events_page(cwd, unit, run, limit=1))
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
                yield ("status", self.events_page(cwd, unit, run, limit=1))
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
        """R14, for a caller that holds a `Service`; `coscc/run.py` calls `events.purge_on_start`."""
        return await events.purge(Data(self.config.data_dir), self._journal())
