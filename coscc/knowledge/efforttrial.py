"""The names the effort trial of `0123` wrote into `start`, kept so its rows can be read.

`0123_no-one-knows-if-each-stage-runs-at-the-right-effort` ran a routine `impl` of half the
units at a higher effort while `COS_EFFORT_TRIAL` was on. `0139` R18 ended it: nothing picks
an effort here any more, and `coscc/knowledge/modeltrial.py` divides the units instead. The
rows already written keep `FIELD` and `CI_RED`, and `coscc/knowledge/effort_measure.py` and
`coscc/knowledge/measure.py` read them back by these names (`.claude/rules/coscc-data.md`).
`CI_RED` is still written, by the model trial and by `COS_KNOWLEDGE`.
"""

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

