"""An in-process bus for what ended.

A part that finishes something publishes a fact, named `<subject>.<past-tense verb>`; the
parts that care subscribe by name. The publisher does not know who listens. Delivery is
synchronous and in the order of subscription, so a handler is quick and schedules a task for
long work. A handler that raises is logged and the next still runs: one part's failure never
breaks the publisher's cleanup.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, get_args

log = logging.getLogger(__name__)

Name = Literal[
    # An attempt's moves (`coscc/service/attempts.py`), each published once its row is committed.
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
    "scan.queued",
    "scan.running",
    "scan.ended",
    "scan.refused",
    "integration.escalated",
    "retake.ended",
    "chat-turn.ended",
    "answer.written",
    "shortlist.saved",
    "hold.moved",
    "mode.set",
]
NAMES: tuple[Name, ...] = get_args(Name)


@dataclass(frozen=True)
class Event:
    name: Name
    workspace: str = ""
    unit: str = ""
    going_down: bool = False


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

    def publish(self, event: Event) -> None:
        log.debug("%s %s %s", event.name, event.workspace, event.unit)
        for handler in [*self._handlers[event.name], *self._watchers]:
            try:
                handler(event)
            except Exception:
                log.exception("a %s handler failed", event.name)
