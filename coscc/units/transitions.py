"""The one place a transition of the unit, the run or the pull request is applied.

`.cos/0136_transitions-are-decided-by-parsing-prose`, spec Design "Một điểm áp bước chuyển":
every transition goes through `apply`, which

- asks the guard the lane config names for it (R1), and writes nothing when it is closed;
- writes the transition, carrying guard, authority, run and inputs (R15), and its event, a
  `runs` row of kind `transition`, in one `Data.write()` (R16);
- tells `notify` only after that transaction has committed, so a reader woken by it finds the
  rows (R23). `notify` writes nothing.

No agent chooses the state a transition leads to: `to_state` comes from the caller, which is
code, and the guard decides whether it may happen.
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
    actor: str = UNKNOWN,
    source: str = UNKNOWN,
    lane: str = "full",
    lanes: states.Lanes | None = None,
    notify: Callable[[Applied], None] | None = None,
) -> Applied:
    """Guard, then transition and event in one transaction, then `notify`.

    `inputs` is what the guard reads, and is stored whole on the row: a reader of the log sees
    exactly what decided it. `authority` must be one of the four; a new row never says
    `unknown` about whose decision it was.
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
        "session": run,
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
    journal.append_with([event], also=lambda conn: stored.extend(history.record_in(conn, [item])))
    applied = Applied(True, g.id, g.label, (), stored[0] if stored else None)
    if notify is not None:
        notify(applied)
    return applied
