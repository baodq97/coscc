"""The effort-trial field names in `start` rows, kept so already-written rows can be read back."""

from __future__ import annotations

import hashlib

STAGE = "impl"

TRIAL_ARM = "trial"
CONTROL_ARM = "control"

FIELD = "effort_trial"
CI_RED = "ci_red"


def arm(unit: str) -> str:
    """`trial` when the first byte of the name's SHA-256 is even, `control` when it is odd."""
    return TRIAL_ARM if hashlib.sha256(unit.encode("utf-8")).digest()[0] % 2 == 0 else CONTROL_ARM

