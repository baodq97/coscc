"""Which units run `impl` at a higher effort, while `COS_EFFORT_TRIAL` is on.

`0123_no-one-knows-if-each-stage-runs-at-the-right-effort`. A holdout: every unit is in one
of two arms, decided by its name alone, so it is in the same arm on every run and after every
restart, and nothing is stored but the `start` record (spec R2). The `trial` arm's routine
`impl` runs at `EFFORT`; everything else runs as it did (R3, R5).

Pure: it reads nothing but its arguments. The arm and whether the effort was taken go into
`start` under `FIELD`, beside `CI_RED`, and `coscc/knowledge/effort_measure.py` reads them back by
these names (`.claude/rules/coscc-data.md`).
"""

from __future__ import annotations

import hashlib

from coscc.agent import labels

# The one pair under trial (spec Design, part 3). Not a row of `models.json`: `models.table`
# reports any key it does not know there. Spec C1 leaves `high` against `low` to the originator.
STAGE = "impl"
EFFORT = "high"

TRIAL_ARM = "trial"
CONTROL_ARM = "control"

FIELD = "effort_trial"
CI_RED = "ci_red"


def arm(unit: str) -> str:
    """`trial` when the first byte of the name's SHA-256 is even, `control` when it is odd."""
    return TRIAL_ARM if hashlib.sha256(unit.encode("utf-8")).digest()[0] % 2 == 0 else CONTROL_ARM


def effort_for(stage: str, label: str | None, arm: str) -> str | None:
    """`EFFORT` for a routine `impl` of a `trial` unit, `None` for every other step."""
    if stage == STAGE and label == labels.ROUTINE and arm == TRIAL_ARM:
        return EFFORT
    return None
