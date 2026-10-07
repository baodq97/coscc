"""An in-process bus for what ended.

A part that finishes something publishes a fact, named `<subject>.<past-tense verb>`, with the
payload its name declares (`SCHEMAS`): `publish` refuses any other, so nothing undeclared, a vault
value included, can ride an event to `/api/stream`. The parts that care subscribe by name. The publisher does not know who listens. Delivery is
synchronous and in the order of subscription, so a handler is quick and schedules a task for
long work. A handler that raises is logged and the next still runs: one part's failure never
breaks the publisher's cleanup.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from typing import Literal, TypedDict, get_args, get_type_hints

log = logging.getLogger(__name__)

Name = Literal[
    # An attempt's moves (`coscc/runner/queue.py`), each published once its row is committed.
    "step.queued",
    "step.preparing",
    "step.running",
    "step.ending",
    "step.ended",
    "step.refused",
    "step.stop-asked",
    "integration.queued",
    "integration.running",
    "integration.ending",
    "integration.ended",
    "integration.refused",
    "integration.stop-asked",
    "hold.queued",
    "hold.running",
    "hold.ended",
    "hold.refused",
    "rounds.queued",
    "rounds.running",
    "rounds.ended",
    "rounds.refused",
    "estimate.queued",
    "estimate.running",
    "estimate.ended",
    "estimate.refused",
    "integration.escalated",
    "retake.ended",
    "chat-turn.ended",
    "answer.written",
    "shortlist.saved",
    "hold.moved",
    "mode.set",
    "unit.shipped",
    # An agent run (`coscc/runner/triggers.py`): held from `started` to `ended`, its `end` already written.
    "agent-run.started",
    "agent-run.ended",
]
NAMES: tuple[Name, ...] = get_args(Name)


class Moved(TypedDict):
    """An attempt's move, `<machine>.<state>`."""

    workspace: str
    unit: str
    # The app is going down: the move is a shutdown's, not a reason to start more work.
    going_down: bool


class OfUnit(TypedDict):
    workspace: str
    unit: str


class OfWorkspace(TypedDict):
    workspace: str


class OfAgent(TypedDict):
    workspace: str
    agent: str
    run: str


class ChatTurn(TypedDict):
    session: str


class Shipped(TypedDict):
    """A unit's merge, once the app read it: `sha` the merge commit, `at` when it was read."""

    workspace: str
    unit: str
    sha: str
    at: str


Payload = Moved | OfUnit | OfWorkspace | OfAgent | ChatTurn | Shipped

# The states an attempt moves to.
MOVES = ("queued", "preparing", "running", "ending", "ended", "refused", "stop-asked")
SCHEMAS: dict[str, type] = {
    "integration.escalated": OfUnit,
    "retake.ended": OfUnit,
    "chat-turn.ended": ChatTurn,
    "answer.written": OfUnit,
    "shortlist.saved": OfWorkspace,
    "hold.moved": OfUnit,
    "mode.set": OfUnit,
    "unit.shipped": Shipped,
    "agent-run.started": OfAgent,
    "agent-run.ended": OfAgent,
}


@cache
def fields_of(name: str) -> dict[str, type]:
    schema = SCHEMAS.get(name) or (
        Moved if name in NAMES and name.rpartition(".")[2] in MOVES else None
    )
    if schema is None:
        raise ValueError(f"{name} is no bus subject")
    return get_type_hints(schema)


def check(name: str, payload: Payload) -> None:
    """Raises `ValueError` unless `payload` holds exactly the fields `name` declares, each of
    its type."""
    fields, got = fields_of(name), dict(payload)
    if set(got) != set(fields):
        raise ValueError(f"{name} carries {sorted(fields)}, not {sorted(got)}")
    for key, kind in fields.items():
        if not isinstance(got[key], kind):
            raise ValueError(f"{name}.{key} is a {kind.__name__}: {got[key]!r}")


@dataclass(frozen=True)
class Event:
    name: Name
    payload: Payload


class Bus:
    def __init__(self) -> None:
        self._handlers: defaultdict[Name, list[Callable[[Event], None]]] = defaultdict(list)
        self._watchers: list[Callable[[Event], None]] = []

    def subscribe(self, name: Name, handler: Callable[[Event], None]) -> None:
        self._handlers[name].append(handler)

    def watch(self, handler: Callable[[Event], None]) -> Callable[[], None]:
        """`handler` hears every event, after its subscribers, until the returned call."""
        self._watchers.append(handler)
        return lambda: self._watchers.remove(handler)

    def publish(self, name: Name, payload: Payload) -> None:
        """Raises `ValueError`, before any handler hears it, for a payload `name` does not
        declare."""
        check(name, payload)
        event = Event(name, payload)
        log.debug("%s %s", name, payload)
        for handler in [*self._handlers[name], *self._watchers]:
            try:
                handler(event)
            except Exception:
                log.exception("a %s handler failed", name)
