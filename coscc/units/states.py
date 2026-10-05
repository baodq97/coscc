"""The set of states a unit can be in, read from the packaged `states.json` rather than written here.

Nothing else may name a state. `absent` is a state so a transition log can record a move out
of nothing; it is a member of no stage's `statuses`, so nothing may write it as a destination.
Which transitions are allowed is not decided here; only that a stage can carry the state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from collections.abc import Mapping
from typing import Any, Literal, get_args

from coscc.agent import harness


StageName = Literal["idea", "impl", "intent", "plan", "pr", "review", "ship", "spec", "spike"]
STAGE_NAMES: tuple[StageName, ...] = get_args(StageName)


@dataclass(frozen=True)
class Stage:
    """One step of the loop, and the states its artifact may carry."""

    name: str
    artifact: str
    statuses: tuple[str, ...]
    optional: bool = False


@dataclass(frozen=True)
class Machine:
    """A whole state set: the stages, what counts as settled, and the name of nothing."""

    name: str
    absent: str
    settled: frozenset[str]
    stages: tuple[Stage, ...]

    @property
    def stage_names(self) -> tuple[str, ...]:
        return tuple(s.name for s in self.stages)

    @property
    def artifacts(self) -> tuple[str, ...]:
        """In stage order; every projection reports in this order."""
        return tuple(s.artifact for s in self.stages)

    def stage(self, name: str) -> Stage | None:
        for s in self.stages:
            if s.name == name:
                return s
        return None

    def for_artifact(self, artifact: str) -> Stage | None:
        for s in self.stages:
            if s.artifact == artifact:
                return s
        return None

    def knows(self, artifact: str) -> bool:
        return self.for_artifact(artifact) is not None

    def is_settled(self, state: str) -> bool:
        """Whether a state means "this stage is behind us"."""
        return state in self.settled

    def allows(self, artifact: str, state: str) -> bool:
        """Whether this artifact may carry this state. `absent` is allowed everywhere."""
        if state == self.absent:
            return True
        stage = self.for_artifact(artifact)
        return stage is not None and state in stage.statuses

    def refuse(self, artifact: str, state: str) -> str | None:
        """Why this artifact may not carry this state, or `None`. Wording for a caller."""
        if self.allows(artifact, state):
            return None
        stage = self.for_artifact(artifact)
        if stage is None:
            return (
                f"{artifact!r} is not an artifact of the {self.name!r} state set "
                f"(it has {', '.join(self.artifacts)})"
            )
        return (
            f"{artifact!r} cannot be {state!r} in the {self.name!r} state set "
            f"(it may be {', '.join((*stage.statuses, self.absent))})"
        )

    @classmethod
    def of(cls, raw: Mapping[str, Any]) -> Machine:
        return cls(
            name=raw["name"],
            absent=raw["absent"],
            settled=frozenset(raw["settled"]),
            stages=tuple(
                Stage(s["name"], s["artifact"], tuple(s["statuses"]), bool(s.get("optional")))
                for s in raw["stages"]
            ),
        )


@lru_cache(maxsize=1)
def default() -> Machine:
    """The packaged set, read once. Callers that take a `Machine | None` default to this."""
    return Machine.of(json.loads(harness.STATES_PATH.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class Lane:
    name: str
    path: tuple[tuple[str, str], ...]
    end: str
    guards: dict[str, dict[str, str]]

    def guard_for(self, machine: str, transition: str) -> str:
        return self.guards[machine][transition]


@dataclass(frozen=True)
class Lanes:
    lanes: dict[str, Lane]
    ci_poll_seconds: float

    def lane(self, name: str = "full") -> Lane:
        return self.lanes[name]


@lru_cache(maxsize=1)
def default_lanes() -> Lanes:
    raw = json.loads(harness.LANES_PATH.read_text(encoding="utf-8"))
    return Lanes(
        lanes={
            name: Lane(
                name,
                tuple((step["stage"], step["when"]) for step in lane["path"]),
                lane["end"],
                lane["guards"],
            )
            for name, lane in raw["lanes"].items()
        },
        ci_poll_seconds=float(raw["params"]["ci_poll_seconds"]),
    )
