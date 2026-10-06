"""Scratch: tells every step where it may write besides the worktree.

The kernel makes the unit's two directories and puts their paths in `COS_SCRATCH_RAM` and
`COS_SCRATCH_DISK` (`coscc/units/scratch.py`); this only says which is for what. The block goes
to a run whose grant holds Bash, the one its scratch is issued with.
"""

from __future__ import annotations

from coscc.kernel import SCRATCH_RAM_CAP, Block, Facts, Feature, Parts

# The tool the block teaches: the unit's scratch is written through it.
BASH = "Bash"


def render(_facts: Facts) -> str:
    """The three places an agent writes."""
    cap = SCRATCH_RAM_CAP // 2**20
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


FEATURE = Feature(
    "scratch",
    lambda _ctx: [],
    agent=lambda _ctx: Parts(blocks=(Block("scratch", render, tool=BASH),)),
    summary="Tells a step that runs commands where it may write besides the worktree: RAM or disk.",
)
