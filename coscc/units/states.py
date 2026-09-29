"""The set of states a unit can be in, loaded from a file rather than written here.

The default is the packaged `states.json`; a different set loads without editing Python.
Nothing else may name a state. `absent` is a state so a transition log can record a move out
of nothing; it is a member of no stage's `statuses`, so nothing may write it as a destination.
Which transitions are allowed is not decided here; only that a stage can carry the state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

# The packaged default; a wheel has to carry the rules it runs on (`wheel_complaints`).
DEFAULT_PATH = Path(__file__).resolve().parent / "states.json"


class BadMachine(ValueError):
    """A state definition this module will not load. Raised, never defaulted: a typo would otherwise load and disagree with one query."""


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


def load(path: str | Path | None = None) -> Machine:
    """Read a state set from a file. `None` means the packaged default.

    Every failure is a `BadMachine` naming the file, never a half-loaded set.
    """
    source = Path(path) if path is not None else DEFAULT_PATH
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except OSError as e:
        raise BadMachine(f"could not read the state set at {source}: {e}") from e
    except json.JSONDecodeError as e:
        raise BadMachine(f"{source} is not readable JSON: {e}") from e
    return build(raw, source)


def build(raw: Any, source: str | Path = "<memory>") -> Machine:
    """A `Machine` from already-parsed data, validated."""
    if not isinstance(raw, dict):
        raise BadMachine(f"{source}: a state set is an object, not {type(raw).__name__}")

    name = str(raw.get("name") or "").strip()
    if not name:
        raise BadMachine(f"{source}: the state set needs a 'name'")

    absent = str(raw.get("absent") or "").strip()
    if not absent:
        raise BadMachine(f"{source}: the state set needs an 'absent' state to name nothing")

    stages_raw = raw.get("stages")
    if not isinstance(stages_raw, list) or not stages_raw:
        raise BadMachine(f"{source}: 'stages' must be a non-empty list")

    stages: list[Stage] = []
    seen_names: set[str] = set()
    seen_artifacts: set[str] = set()
    for index, item in enumerate(stages_raw):
        if not isinstance(item, dict):
            raise BadMachine(f"{source}: stage {index} is not an object")
        stage_name = str(item.get("name") or "").strip()
        artifact = str(item.get("artifact") or "").strip()
        if not stage_name or not artifact:
            raise BadMachine(f"{source}: stage {index} needs both a 'name' and an 'artifact'")
        if stage_name in seen_names:
            raise BadMachine(f"{source}: two stages are called {stage_name!r}")
        if artifact in seen_artifacts:
            raise BadMachine(f"{source}: two stages write {artifact!r}")
        statuses = item.get("statuses")
        if not isinstance(statuses, list) or not statuses:
            raise BadMachine(f"{source}: stage {stage_name!r} needs a non-empty 'statuses'")
        statuses = tuple(str(s) for s in statuses)
        if absent in statuses:
            # Otherwise `absent` would be a second way to say "not started".
            raise BadMachine(
                f"{source}: stage {stage_name!r} lists the absent state {absent!r} as a "
                "status it can be written to"
            )
        seen_names.add(stage_name)
        seen_artifacts.add(artifact)
        stages.append(
            Stage(
                name=stage_name,
                artifact=artifact,
                statuses=statuses,
                optional=bool(item.get("optional")),
            )
        )

    settled_raw = raw.get("settled")
    if not isinstance(settled_raw, list):
        raise BadMachine(f"{source}: 'settled' must be a list, even if it is empty")
    settled = frozenset(str(s) for s in settled_raw)
    every = {s for stage in stages for s in stage.statuses}
    unknown = sorted(settled - every)
    if unknown:
        # A settled name no stage can carry makes nothing settled, silently.
        raise BadMachine(
            f"{source}: 'settled' names {', '.join(unknown)}, which no stage can carry"
        )

    return Machine(name=name, absent=absent, settled=settled, stages=tuple(stages))


@lru_cache(maxsize=1)
def default() -> Machine:
    """The packaged set, read once. Callers that take a `Machine | None` default to this."""
    return load(None)


# -- lanes: which stages a lane runs, under what condition, and which guard decides each
# transition of the three machines. Carried in the wheel beside `states.json`.
LANES_PATH = Path(__file__).resolve().parent / "lanes.json"

# How a stage on a lane's path is entered. `unless-skipped` needs the `skip` guard's decision
# to be passed over; `if-unmeasured` runs only when the spec named a `U<n>`.
WHEN = ("always", "unless-skipped", "if-unmeasured")


class BadLanes(ValueError):
    """A lane config this module will not load, naming what is wrong with it."""


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


def load_lanes(path: str | Path | None = None, machine: Machine | None = None) -> Lanes:
    """Read a lane config. `None` means the packaged one."""
    source = Path(path) if path is not None else LANES_PATH
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except OSError as e:
        raise BadLanes(f"could not read the lanes at {source}: {e}") from e
    except json.JSONDecodeError as e:
        raise BadLanes(f"{source} is not readable JSON: {e}") from e
    return build_lanes(raw, source, machine)


def build_lanes(raw: Any, source: str | Path = "<memory>", machine: Machine | None = None) -> Lanes:
    """A `Lanes` from parsed data, refused whole when a guard the machines need is missing.

    `guards` is imported here, not at the top, because the state set knows it for nothing else.
    """
    from coscc.units import guards

    machine = machine or default()
    if not isinstance(raw, dict):
        raise BadLanes(f"{source}: a lane config is an object, not {type(raw).__name__}")
    params = raw.get("params")
    if not isinstance(params, dict):
        raise BadLanes(f"{source}: 'params' must be an object")
    poll = params.get("ci_poll_seconds")
    if isinstance(poll, bool) or not isinstance(poll, (int, float)) or poll <= 0:
        raise BadLanes(f"{source}: 'params.ci_poll_seconds' must be a positive number of seconds")

    lanes_raw = raw.get("lanes")
    if not isinstance(lanes_raw, dict) or not lanes_raw:
        raise BadLanes(f"{source}: 'lanes' must name at least one lane")
    if "full" not in lanes_raw:
        raise BadLanes(f"{source}: there is no lane 'full', the one every unit is on")

    lanes: dict[str, Lane] = {}
    for name, item in lanes_raw.items():
        where = f"{source}: lane {name!r}"
        if not isinstance(item, dict):
            raise BadLanes(f"{where} is not an object")
        path_raw = item.get("path")
        if not isinstance(path_raw, list) or not path_raw:
            raise BadLanes(f"{where} needs a non-empty 'path'")
        path: list[tuple[str, str]] = []
        for step in path_raw:
            stage = str((step or {}).get("stage") or "") if isinstance(step, dict) else ""
            when = str((step or {}).get("when") or "") if isinstance(step, dict) else ""
            if machine.stage(stage) is None:
                raise BadLanes(f"{where}: {stage!r} is no stage of the {machine.name!r} state set")
            if when not in WHEN:
                raise BadLanes(
                    f"{where}: stage {stage!r} has 'when' {when!r}, not one of {', '.join(WHEN)}"
                )
            path.append((stage, when))
        end = str(item.get("end") or "").strip()
        if not end:
            raise BadLanes(f"{where} needs an 'end' state")

        chosen = item.get("guards")
        if not isinstance(chosen, dict):
            raise BadLanes(f"{where} needs 'guards', one per transition of each machine")
        extra = sorted(set(chosen) - set(guards.TRANSITIONS))
        if extra:
            raise BadLanes(f"{where}: no machine is called {', '.join(extra)}")
        picked: dict[str, dict[str, str]] = {}
        for m, transitions in guards.TRANSITIONS.items():
            given = chosen.get(m)
            if not isinstance(given, dict):
                raise BadLanes(f"{where}: no guards for the {m!r} machine")
            extra = sorted(set(given) - set(transitions))
            if extra:
                raise BadLanes(f"{where}: the {m!r} machine has no transition {', '.join(extra)}")
            picked[m] = {}
            for transition, allowed in transitions.items():
                g = str(given.get(transition) or "")
                if not g:
                    raise BadLanes(
                        f"{where}: the {m!r} transition {transition!r} has no guard, and it needs one"
                    )
                if g not in allowed:
                    raise BadLanes(
                        f"{where}: {g!r} cannot decide the {m!r} transition {transition!r} "
                        f"(it may be {', '.join(allowed)})"
                    )
                picked[m][transition] = g
        lanes[name] = Lane(name=name, path=tuple(path), end=end, guards=picked)
    return Lanes(lanes=lanes, ci_poll_seconds=float(poll))


@lru_cache(maxsize=1)
def default_lanes() -> Lanes:
    return load_lanes(None)
