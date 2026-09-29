"""The one place a transition of the unit, the run or the pull request is applied.

`apply` asks the lane's guard (writing nothing when closed), writes the transition and its
event in one transaction, then tells `notify`. `to_state` comes from code, never an agent.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from coscc.runlog.journal import Journal
from coscc.units import guards
from coscc.units import states
from coscc.units.history import AUTHORITIES, UNKNOWN, BadTransition, History


@dataclass(frozen=True)
class Applied:
    """What `apply` did. `row` is the transition as stored, `None` when the guard closed."""

    open: bool
    guard: str
    label: str
    reasons: tuple[str, ...]
    row: dict[str, Any] | None = None


def apply(
    history: History,
    journal: Journal,
    *,
    machine: str,
    transition: str,
    workspace: str,
    unit: str,
    artifact: str,
    to_state: str,
    inputs: Mapping[str, Any],
    authority: str,
    run: str = UNKNOWN,
    session: str = UNKNOWN,
    actor: str = UNKNOWN,
    source: str = UNKNOWN,
    lane: str = "full",
    lanes: states.Lanes | None = None,
    notify: Callable[[Applied], None] | None = None,
    also: Callable[[Any], None] | None = None,
) -> Applied:
    """Guard, then transition and event in one transaction, then `notify`.

    `inputs` is what the guard reads, stored whole on the row. `authority` must be one of
    `AUTHORITIES`. `also(conn)` writes what the transition carries, in the same transaction.
    """
    if authority not in AUTHORITIES:
        raise BadTransition(f"authority must be one of {', '.join(AUTHORITIES)}, got {authority!r}")
    config = (lanes or states.default_lanes()).lane(lane)
    try:
        guard_id = config.guard_for(machine, transition)
    except KeyError:
        raise BadTransition(f"the {machine!r} machine has no transition {transition!r}") from None
    g = guards.guard(guard_id)
    verdict = g.check(inputs)
    if not verdict.open:
        return Applied(False, g.id, g.label, verdict.reasons)

    item = {
        "workspace": workspace,
        "unit": unit,
        "artifact": artifact,
        "to_state": to_state,
        "actor": actor,
        "session": session,
        "source": source,
        "guard": g.id,
        "authority": authority,
        "run": run,
        "inputs": dict(inputs),
    }
    stored: list[dict[str, Any]] = []
    stage = history.machine.for_artifact(artifact)
    event = {
        "kind": "transition",
        "workspace": workspace,
        "unit": unit,
        "stage": stage.name if stage is not None else UNKNOWN,
        "machine": machine,
        "transition": transition,
        "artifact": artifact,
        "to_state": to_state,
        "guard": g.id,
        "authority": authority,
        "run": run,
    }
    def write(conn: Any) -> None:
        stored.extend(history.record_in(conn, [item]))
        if also is not None:
            also(conn)

    journal.append_with([event], also=write)
    applied = Applied(True, g.id, g.label, (), stored[0] if stored else None)
    if notify is not None:
        notify(applied)
    return applied
