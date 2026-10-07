"""Which model a routine step of a row with `model.trial` runs on: each unit is in one of two arms,
decided by its name; the row names the two models.

Pure. `start` carries `FIELD`: `{arm, requested}`, then `model` once the session's `init`
names it, or `NEVER_STARTED` when the session ended before one. A return to the step also carries
`CI_RED`: whether CI sent it back, `None` when that could not be asked.
"""

from __future__ import annotations

import hashlib

from coscc.agent import pack, policy

FIELD = "model_trial"
NEVER_STARTED = "never-started"
CI_RED = "ci_red"


def arm_name(model: str) -> str:
    """`claude-opus-5-5[1m]` is the arm `opus-5-5`."""
    return model.removeprefix("claude-").split("[", 1)[0]


def arms(stage: str) -> dict[str, str]:
    """The two arms of `stage`'s row, `{arm: model}` in the row's order; `{}` with no trial."""
    trial = ((pack.row(stage) or {}).get("model") or {}).get("trial") or []
    return {arm_name(m): m for m in trial} if len(trial) == 2 else {}


def arm(unit: str, stage: str) -> str:
    """The first arm when the first byte of the unit name's SHA-256 is even, the second when odd."""
    names = list(arms(stage))
    return names[hashlib.sha256(unit.encode("utf-8")).digest()[0] % 2] if names else ""


def applies(stage: str, label: str | None) -> bool:
    """Whether a step is in the trial: a routine step of a row that names two arms."""
    return label == policy.ROUTINE and bool(arms(stage))


def model_for(stage: str, label: str | None, arm: str) -> str | None:
    """The model `arm` asks for on a routine step, `None` for every other step."""
    return arms(stage).get(arm) if applies(stage, label) else None
