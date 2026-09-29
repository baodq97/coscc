"""Which model a routine `impl` runs on: the trial that replaced `COS_EFFORT_TRIAL`.

`0139_some-transitions-still-read-prose`, Part 3. Every unit is in one of two arms, decided by
its name alone, as `coscc/knowledge/efforttrial.py` divided them (spec R16), so it is in the
same arm on every run and after every restart. The arm names the model its routine `impl`
asks for; both arms run the same effort, so the model is the only thing that differs.

Pure: it reads nothing but its arguments. `start` carries `FIELD`: `{arm, requested}` when it
is written, and `model` once the session's `init` names it (`Journal.set_trial_model`), or
`NEVER_STARTED` when the session ended before one.
"""

from __future__ import annotations

import hashlib

from coscc.agent import labels

STAGE = "impl"

OPUS_ARM = "opus-5-5"
SONNET_ARM = "sonnet-5-5"
MODELS = {OPUS_ARM: "claude-opus-5-5[1m]", SONNET_ARM: "claude-sonnet-5-5[1m]"}

FIELD = "model_trial"
# `0139` C10: what `model` is when the session ended before its `init`.
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
