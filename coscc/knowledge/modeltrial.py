"""Which model a routine `impl` runs on: each unit is in one of two arms, decided by its name.

Pure. `start` carries `FIELD`: `{arm, requested}`, then `model` once the session's `init`
names it, or `NEVER_STARTED` when the session ended before one.
"""

from __future__ import annotations

import hashlib

from coscc.agent import labels

STAGE = "impl"

OPUS_ARM = "opus-5-5"
SONNET_ARM = "sonnet-5-5"
MODELS = {OPUS_ARM: "claude-opus-5-5[1m]", SONNET_ARM: "claude-sonnet-5-5[1m]"}

FIELD = "model_trial"
NEVER_STARTED = "never-started"


def arm(unit: str) -> str:
    """`opus-5-5` when the first byte of the name's SHA-256 is even, `sonnet-5-5` when odd."""
    return OPUS_ARM if hashlib.sha256(unit.encode("utf-8")).digest()[0] % 2 == 0 else SONNET_ARM


def applies(stage: str, label: str | None) -> bool:
    """Whether a step is in the trial: a routine `impl`, and nothing else."""
    return stage == STAGE and label == labels.ROUTINE


def model_for(stage: str, label: str | None, arm: str) -> str | None:
    """The model `arm` asks for on a routine `impl`, `None` for every other step."""
    return MODELS.get(arm) if applies(stage, label) else None
