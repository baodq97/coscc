"""The set of states a unit can be in, read from the pack's processes rather than written here.

Every state of every process is a stage here, its artifact `<state>.md` and its statuses what
`pack.statuses` derives. `absent` is a state so a transition log can record a move out
of nothing; it is a member of no stage's `statuses`, so nothing may write it as a destination.
Which transitions are allowed is not decided here; only that a stage can carry the state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from collections.abc import Mapping
from typing import Any

from coscc.agent import pack


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
    # `{process: {artifact: statuses}}`: what an artifact may carry on a unit of that process, which
    # `stages` (every process's, merged) may be wider than.
    by_process: Mapping[str, Mapping[str, tuple[str, ...]]] = field(default_factory=dict)

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

    def _statuses(self, artifact: str, process: str | None) -> tuple[str, ...] | None:
        """What `artifact` may carry, on `process` when it has the artifact, else in any."""
        own = self.by_process.get(process or "", {}).get(artifact)
        stage = self.for_artifact(artifact)
        return own if own is not None else stage.statuses if stage else None

    def allows(self, artifact: str, state: str, process: str | None = None) -> bool:
        """Whether this artifact may carry this state. `absent` is allowed everywhere."""
        if state == self.absent:
            return True
        got = self._statuses(artifact, process)
        return got is not None and state in got

    def refuse(self, artifact: str, state: str, process: str | None = None) -> str | None:
        """Why this artifact may not carry this state, or `None`. Wording for a caller."""
        if self.allows(artifact, state, process):
            return None
        stage = self.for_artifact(artifact)
        if stage is None:
            return (
                f"{artifact!r} is not an artifact of the {self.name!r} state set "
                f"(it has {', '.join(self.artifacts)})"
            )
        return (
            f"{artifact!r} cannot be {state!r} in the {self.name!r} state set "
            f"(it may be {', '.join((*(self._statuses(artifact, process) or ()), self.absent))})"
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
            by_process={
                ref: {a: tuple(ss) for a, ss in arts.items()}
                for ref, arts in raw.get("by_process", {}).items()
            },
        )


@lru_cache(maxsize=1)
def default() -> Machine:
    """The built-in pack's set, read once: every state of its processes, in the order first seen.
    Callers that take a `Machine | None` default to this."""
    rows = pack.builtin_rows()
    stages: dict[str, list[str]] = {}
    optional: set[str] = set()
    for p in pack.processes().values():
        for name, st in p["states"].items():
            have = stages.setdefault(name, [])
            have += [x for x in pack.statuses(st, rows) if x not in have]
            if st.get("optional"):
                optional.add(name)
    return Machine.of(
        {
            "name": pack.manifest()["name"],
            "absent": "not started",
            "settled": ["accepted", "skipped"],
            "stages": [
                {"name": n, "artifact": f"{n}.md", "statuses": ss, "optional": n in optional}
                for n, ss in stages.items()
            ],
            "by_process": {
                ref: {f"{n}.md": pack.statuses(st, rows) for n, st in p["states"].items()}
                for ref, p in pack.processes().items()
            },
        }
    )
