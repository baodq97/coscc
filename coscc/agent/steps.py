"""The board steps running right now, one per unit at most.

In memory, this process only: a restart forgets every row. Nothing here decides whether a
step may run (`cos.mjs gate`); a `Mark` in `Service._active` keeps a second step, hold or
integration off a unit, and this registry is what *Stop* and the Board's list read, and it
records who asked for a stop.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

from coscc.agent.sessions import StepHandle


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Busy(ValueError):
    """The unit already has a step running."""


class NotRunning(ValueError):
    """There is no step on that unit to stop."""


class Finishing(ValueError):
    """The step has begun writing its artifact; stopping now would leave half of one."""


@dataclass(eq=False)  # identity, not value: `Service._release` removes this one and no other
class Mark:
    """What holds a unit in `Service._active`: a step, an integration, a hold, Jera or a
    review round being allowed.

    `phase` is a step's only: `preparing` until the registry lists it, `running` after.
    """

    kind: str  # "step" | "integrate" | "hold" | "precedent" | "more-rounds"
    stage: str = ""
    phase: str = ""
    started_at: str = field(default_factory=now)


def describe(unit: str, mark: Mark) -> str:
    """The one sentence every refusal of a busy unit carries: what, and since when."""
    t = mark.started_at
    if mark.kind == "integrate":
        return f"{unit} is busy: it is being integrated since {t}; wait for the integration to end"
    if mark.kind == "precedent":
        return f"{unit} is busy: Jera is answering its questions since {t}; wait for it to end"
    if mark.kind == "hold":
        return f"{unit} is busy: a hold is being recorded since {t}; try again in a moment"
    if mark.kind == "more-rounds":
        return f"{unit} is busy: a review round is being allowed since {t}; try again in a moment"
    if mark.phase == "preparing":
        return (
            f"{unit} is busy: a {mark.stage} step is being prepared since {t} and is not on the "
            "Board's list of running steps yet, so it cannot be stopped; wait for it to start "
            "or fail, then try again"
        )
    return (
        f"{unit} is busy: a {mark.stage} step is running since {t}; stop it with its Stop "
        "button on the Board (0034), or wait for it to end"
    )


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
    # The id of this step's events, set by `Service.run_step`. Empty for a row made any other way.
    run: str = ""


class Registry:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], Running] = {}

    def claim(self, workspace: str, unit: str, stage: str, started_at: str | None = None) -> Running:
        held = self._rows.get((workspace, unit))
        if held is not None:
            raise Busy(describe(unit, Mark("step", held.stage, "running", held.started_at)))
        running = Running(
            workspace=workspace,
            unit=unit,
            stage=stage,
            started_at=started_at or now(),
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
                "run": r.run,
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

    Synchronous on purpose: no `await` between the check and the set, so no stop slips between.
    """
    if running is None:
        return True
    if running.stop_requested:
        return False
    running.sealed = True
    return True
