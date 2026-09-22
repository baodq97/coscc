"""The set of states a unit can be in, loaded from a file rather than written here.

`0013`'s `spec.md` R6 has two halves and this module exists to make both of them checkable.
The first half is that the default is today's set — eight stages, the statuses
`.claude/scripts/cos.mjs:25-34` already enforces — so nothing changes for work already in
flight. The second half is that a different set can be loaded **without editing Python**,
and the only honest proof of that is a test that loads one and drives a unit through it.
`coscc/states_test.py` is that test; without it "configuration" is a word.

**Nothing else may name a state.** `coscc/history.py` validates every transition against a
`Machine` and `coscc/backfill.py` decides what counts as settled by asking one. The moment
a second module writes `"accepted"` as a literal, the file below stops being the definition
and becomes a copy of one.

**`absent` is a state, and giving it a name is the point.** `coscc/board.py:63-81` derives
"not started" from a missing file, which is fine when the only question is where a unit is
now. A transition log has to record the move *out of* nothing, so nothing needs a name it
can be stored under. It is deliberately not a member of any stage's `statuses`: an artifact
can leave it but nothing may ever write it as a destination.

**What this module does not decide.** Which transitions are *allowed* in the sense of a
workflow engine — "you may not go from `draft` to `ship`" — is not here and is not in
`cos.mjs` either. What is enforced is narrower and is the part that catches real mistakes:
a state that the stage it is attached to cannot carry. Widening that later is adding a key
to the file, which is the shape this whole module is arguing for.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

# The packaged default. `coscc/harness.py` makes the same argument for `cos.mjs` and the
# skills: a wheel has to carry the rules it runs on. `wheel_complaints` refuses a wheel
# that does not have this file, for exactly the reason `0012` cost a unit.
DEFAULT_PATH = Path(__file__).resolve().parent / "states.json"


class BadMachine(ValueError):
    """A state definition this module will not load, carrying what is wrong with it.

    Raised rather than defaulted. A definition with a typo in it would otherwise load,
    agree with every write, and disagree with the one query that mattered.
    """


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

    # -- lookups ------------------------------------------------------------

    @property
    def stage_names(self) -> tuple[str, ...]:
        return tuple(s.name for s in self.stages)

    @property
    def artifacts(self) -> tuple[str, ...]:
        """In stage order. Every projection reports in this order, so it is defined once."""
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

    # -- questions the log asks ---------------------------------------------

    def is_settled(self, state: str) -> bool:
        """Whether a state means "this stage is behind us".

        The same question `cos.mjs:123` answers, asked of configuration instead of a
        literal. `0013`'s outcome is counted in terms of it: an artifact edited while in a
        settled state is the event that used to leave no trace.
        """
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

    Every failure here is a `BadMachine` naming the file, because the alternative — a
    half-loaded set — disagrees with the log that was written under the whole one.
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
    """A `Machine` from already-parsed data, validated. Exposed for tests and callers
    that hold a definition in memory rather than on disk."""
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
            # Otherwise a stage could be written back into nothing, and the projection
            # would have two ways to say "not started" that a query cannot tell apart.
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
        # A settled name no stage can carry makes nothing settled, silently -- and
        # `0013`'s whole count is "edited while settled", so it would come back zero and
        # look like good news.
        raise BadMachine(
            f"{source}: 'settled' names {', '.join(unknown)}, which no stage can carry"
        )

    return Machine(name=name, absent=absent, settled=settled, stages=tuple(stages))


@lru_cache(maxsize=1)
def default() -> Machine:
    """The packaged set, read once. Callers that take a `Machine | None` default to this."""
    return load(None)
