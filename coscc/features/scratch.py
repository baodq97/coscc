"""Scratch: tells every step where it may write besides the worktree.

The kernel makes the unit's two directories and puts their paths in `COS_SCRATCH_RAM` and
`COS_SCRATCH_DISK` (`coscc/units/scratch.py`); this only says which is for what. The block is
the same for every step, since reading scratch is open to all of them.
"""

from __future__ import annotations

from coscc.hooks import Block, Facts, Parts
from coscc.plugin import Plugin
from coscc.units.scratch import RAM_CAP


def render(_facts: Facts) -> str:
    """The three places an agent writes, whatever the step."""
    cap = RAM_CAP // 2**20
    return "\n".join(
        [
            "# Where you write",
            "",
            "- What will be committed goes in the worktree.",
            f"- Small, one-off things (a probe, a count, a short log) go in `$COS_SCRATCH_RAM`, "
            f"the unit's directory in RAM. It is capped at {cap} MiB: a write there is refused "
            "once it holds that much.",
            "- Large things (screenshots, build or e2e logs), and anything a later stage of the "
            "unit reads back, go in `$COS_SCRATCH_DISK`. `TMPDIR` points there.",
            "",
            "A redirect may name either directory by its variable (`> $COS_SCRATCH_DISK/log`); "
            "`Write` and `Edit` take the path itself, which `echo $COS_SCRATCH_DISK` prints.",
            "",
            "Nowhere else outside the worktree can be written. There is no need to delete "
            "anything: the app removes both directories when the unit ends.",
        ]
    )


PLUGIN = Plugin(
    "scratch",
    lambda _ctx: [],
    agent=lambda _ctx: Parts(blocks=(Block("scratch", render),)),
    summary="Tells every step where it may write besides the worktree: one folder in RAM, one on disk.",
)
