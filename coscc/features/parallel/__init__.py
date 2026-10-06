"""Parallel: the steps of a plan's record, handed to impl.

The block names the steps to impl's leading session only when there are two or more, which is
when it starts one `worker` for each; how the agents talk is the kernel's
(`coscc/agent/helpers.py`). `submit` refuses a plan whose steps name a path outside its
`files`, or one path twice.
"""

from __future__ import annotations

from coscc.kernel import Block, Facts, Feature, Parts


def render(facts: Facts) -> str:
    """impl's block: the steps, when the plan's record names two or more."""
    found = facts.plan["steps"] if facts.stage == "impl" and facts.plan else []
    if len(found) < 2:
        return ""
    parts = [
        "# The plan's parallel steps",
        "",
        f"The plan names {len(found)} steps to run at the same time: start one `worker` for "
        "each, with the step's line below as `description`. Its prompt is that step's slice: "
        "its paths and report, the part of `plan.md` and the answers and findings that touch "
        "those paths, copied from this prompt. A worker reads no unit file.",
    ]
    for i, step in enumerate(found):
        parts += [
            "",
            f"({chr(ord('a') + i)}) {step['title']}",
            *(f"- {p}" for p in step["paths"]),
            step["report"],
        ]
    return "\n".join(parts).rstrip()


FEATURE = Feature(
    "parallel",
    lambda _ctx: [],
    agent=lambda _ctx: Parts(blocks=(Block("parallel", render),)),
    summary="Lets impl start one helper per parallel step of a plan's record.",
)
