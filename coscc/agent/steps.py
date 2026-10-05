"""What this process holds of one launched step or integration, beside its attempt.

In memory, this process only. Nothing here decides whether a step may run (`python -m coscc.loop gate`)
or what holds a unit (its attempt, `coscc/runner/queue.py`): a `Running` is the session
handle a Stop closes, the task it cancels, the readers its items go to, and a copy of the
Stop the runner reads between two `await`s.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from coscc.agent.sessions import StepHandle


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(eq=False)  # identity, not value
class Running:
    workspace: str
    unit: str
    stage: str
    started_at: str
    handle: StepHandle = field(default_factory=StepHandle)
    task: asyncio.Task | None = None
    # Set with the attempt's `stop_asked_at`, by `Steps.stop_running`, with no `await` between.
    stop_requested: bool = False
    stopped_by: str = ""
    # Set by `seal`, once the runner has begun writing the artifact: the attempt is `ending`
    # from then, and a stop after this point is recorded, not honoured halfway.
    sealed: bool = False
    listeners: set = field(default_factory=set)
    # The id of this step's events, set by `Steps.run_step`. Empty for a row made any other way.
    run: str = ""
    # The attempt this is the live part of, and what `seal` moves it with.
    attempt: int = 0
    on_seal: Callable[[], None] | None = None
    # The workspace as the click named it; the journal key (`workspace`) when none did.
    cwd: str = ""


def seal(running: Running | None) -> bool:
    """Close the door on a stop, unless one already came through it.

    Synchronous on purpose: no `await` between the check and the set, so no stop slips between.
    """
    if running is None:
        return True
    if running.stop_requested:
        return False
    if not running.sealed:
        running.sealed = True
        if running.on_seal is not None:
            running.on_seal()
    return True
