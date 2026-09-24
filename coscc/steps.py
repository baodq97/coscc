"""The board steps running right now, one per unit at most.

`0034`. The page used to hold one `running: str` for the whole board, so a step on any
unit greyed out the run button on every other one, though each unit has its own worktree.
What is running lives here instead, in the process that runs it, where both the page and
`POST /api/board/stop` read it.

**In memory, this process only.** A restart forgets every row, and so does a second copy
of the app on the same data root -- the same limit `Sessions.live_in` names.

Nothing here decides whether a step may run: that is `cos.mjs gate`. This only refuses a
second step on a unit that already has one, and records who asked for a stop.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

from coscc.sessions import StepHandle


class Busy(ValueError):
    """The unit already has a step running."""


class NotRunning(ValueError):
    """There is no step on that unit to stop."""


class Finishing(ValueError):
    """The step has begun writing its artifact; stopping now would leave half of one."""


@dataclass(eq=False)  # identity, not value: `release` removes this object and no other
class Running:
    workspace: str
    unit: str
    stage: str
    started_at: str
    handle: StepHandle = field(default_factory=StepHandle)
    task: asyncio.Task | None = None
    stop_requested: bool = False
    stopped_by: str = ""
    # Set by `seal`, once the runner has begun writing the artifact. A stop after this
    # point is refused rather than honoured halfway.
    sealed: bool = False
    listeners: set = field(default_factory=set)


class Registry:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], Running] = {}

    def claim(self, workspace: str, unit: str, stage: str) -> Running:
        held = self._rows.get((workspace, unit))
        if held is not None:
            raise Busy(
                f"{unit} is already running {held.stage} (started {held.started_at}); "
                "one step per unit at a time"
            )
        running = Running(
            workspace=workspace,
            unit=unit,
            stage=stage,
            started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        self._rows[(workspace, unit)] = running
        return running

    def get(self, workspace: str, unit: str) -> Running | None:
        return self._rows.get((workspace, unit))

    def release(self, running: Running) -> None:
        if self._rows.get((running.workspace, running.unit)) is running:
            del self._rows[(running.workspace, running.unit)]

    def all(self) -> list[Running]:
        return list(self._rows.values())

    def listing(self, workspace: str) -> list[dict]:
        return [
            {
                "unit": r.unit,
                "stage": r.stage,
                "started_at": r.started_at,
                "stopping": r.stop_requested,
            }
            for r in sorted(self._rows.values(), key=lambda r: r.started_at)
            if r.workspace == workspace
        ]

    def request_stop(self, workspace: str, unit: str, by: str) -> Running:
        running = self._rows.get((workspace, unit))
        if running is None:
            raise NotRunning(f"{unit} has no step running")
        if running.sealed:
            raise Finishing(
                f"{unit}'s {running.stage} step is already writing its artifact; it cannot be stopped now"
            )
        if not running.stop_requested:
            # The first name stays: two presses are one stop, with one person behind it.
            running.stop_requested = True
            running.stopped_by = by
        return running


def seal(running: Running | None) -> bool:
    """Close the door on a stop, unless one already came through it.

    Synchronous on purpose: with no `await` between the check and the set, nothing on the
    event loop can slip a stop in between them.
    """
    if running is None:
        return True
    if running.stop_requested:
        return False
    running.sealed = True
    return True
