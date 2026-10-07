"""The set of states a unit can be in, read from the pack's processes rather than written here.

Every state of every process is a stage here, its artifact `<state>.md` and its statuses what
`pack.statuses` derives. `absent` is a state so a transition log can record a move out
of nothing; it is a member of no stage's `statuses`, so nothing may write it as a destination.
Which transitions are allowed is not decided here; only that a stage can carry the state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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


_DEFAULT: dict[str, Any] = {}


def default() -> Machine:
    """Every pack's set: every state of every process, in the order first seen; built again only
    when the processes are. Callers that take a `Machine | None` default to this."""
    every = pack.processes()
    if _DEFAULT.get("of") is not every:
        _DEFAULT.update(of=every, machine=_machine(every))
    return _DEFAULT["machine"]


def _machine(every: Mapping[str, pack.Process]) -> Machine:
    rows = pack.rows()
    stages: dict[str, list[str]] = {}
    optional: set[str] = set()
    for p in every.values():
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
                for ref, p in every.items()
            },
        }
    )


def _state(process: str | None, name: str) -> Mapping[str, Any]:
    found = pack.process(process or pack.DEFAULT_PROCESS) or {}
    return found.get("states", {}).get(name) or {}


def row_of(process: str | None, name: str) -> Mapping[str, Any]:
    """The agent row a state runs: `{}` for an action state or an unknown one."""
    agent = _state(process, name).get("agent")
    return (pack.row(str(agent)) if agent else None) or {}


def action_of(process: str | None, name: str) -> str:
    """The engine's action a state is (`open-pr`, `merge`), `""` for an agent's state."""
    return str(_state(process, name).get("action") or "")


def by_of(process: str | None, name: str) -> str:
    """Who writes the state's output: `app`, `session` (its unit's branch) or `scratch`."""
    return str((row_of(process, name).get("output") or {}).get("by") or "")


def kind_of(process: str | None, name: str) -> str:
    """The state's output kind (`artifact`, `review`, ...), `""` for an action."""
    return str((row_of(process, name).get("output") or {}).get("kind") or "")


_WHERE: dict[Any, Any] = {}


def states_where(
    *, action: str = "", by: str = "", kind: str = "", process: str | None = None
) -> tuple[str, ...]:
    """Every state of every process (or of `process` alone) that is this action, writes this way
    or has this output kind; worked out again only when the processes are read again."""
    every = pack.processes()
    asked = (action, by, kind, process)
    held = _WHERE.get(asked)
    if held is None or held[0] is not every:
        held = _WHERE[asked] = (every, _states_where(action, by, kind, process))
    return held[1]


def _states_where(action: str, by: str, kind: str, process: str | None) -> tuple[str, ...]:
    return tuple(
        n
        for n in pack.state_names()
        if any(
            (not action or action_of(ref, n) == action)
            and (not by or by_of(ref, n) == by)
            and (not kind or kind_of(ref, n) == kind)
            for ref in pack.processes()
            if (process is None or ref == process) and n in pack.processes()[ref]["states"]
        )
    )


def files_where(**what: str) -> tuple[str, ...]:
    """`states_where` as artifact names (`<state>.md`), for a query's `?` params."""
    return tuple(f"{n}.md" for n in states_where(**what))


def marks(values: tuple[str, ...] | list[str]) -> str:
    """`?, ?, ?` for a SQL `IN (...)`."""
    return ", ".join("?" * len(values))


def data_of(process: str | None, name: str) -> tuple[str, ...]:
    """The data the state's agent declares it reads (`input.data`), e.g. `drift`, `screens`."""
    return tuple((row_of(process, name).get("input") or {}).get("data") or ())


def states_with_field(field: str) -> tuple[str, ...]:
    """Every state whose agent hands back `field` (`files`, `variant`), of every process."""
    return tuple(
        n
        for n in pack.state_names()
        if any(
            field in pack.output_fields(row_of(ref, n))
            for ref, p in pack.processes().items()
            if n in p["states"]
        )
    )


def agents_with_field(field: str) -> tuple[str, ...]:
    """Every agent row whose output declares `field`."""
    return tuple(k for k, r in pack.rows().items() if field in pack.output_fields(r))


def first_file(**what: str) -> str:
    """The first state's artifact with this action, writer or output kind: `""` when none."""
    return next(iter(files_where(**what)), "")


def by_of_agent(agent: str) -> str:
    """`output.by` of an agent row, `""` for none."""
    return str(((pack.row(agent) or {}).get("output") or {}).get("by") or "")


def opening_states(process: str | None = None, n: int = 2) -> tuple[str, ...]:
    """The process's first `n` states along its main line: where the unit's brief and its intent
    are written."""
    found = pack.process(process or pack.DEFAULT_PROCESS) or {}
    names: list[str] = []
    name = found.get("start", "")
    while name and name not in names and len(names) < n:
        names.append(name)
        ways = (found["states"].get(name) or {}).get("next") or []
        name = ways[-1]["to"] if ways else ""
    return tuple(names)


def brief_file(process: str | None = None) -> str:
    """The artifact the unit's brief is written to: the process's start state's."""
    return f"{opening_states(process, 1)[0]}.md"


def files_with_field(field: str) -> tuple[str, ...]:
    """`<state>.md` of every state whose agent hands back `field`."""
    return tuple(f"{n}.md" for n in states_with_field(field))


def coder_agents() -> tuple[str, ...]:
    """The agent rows that write in the unit's branch (`by: session`, an artifact)."""
    return tuple(
        k
        for k, r in pack.rows().items()
        if (r.get("output") or {}).get("by") == "session"
        and (r.get("output") or {}).get("kind") == "artifact"
    )


def skippable() -> tuple[str, ...]:
    """Every state a person may skip: those a process gives a `skip` guard."""
    return tuple(
        n
        for n in pack.state_names()
        if any(n in p["states"] and p["states"][n].get("skip") for p in pack.processes().values())
    )


def is_review(stage: object, process: str | None = None) -> bool:
    """Whether `stage` is a state whose output is a review's rounds."""
    return kind_of(process, str(stage)) == "review"
