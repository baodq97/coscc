"""Parallel: the steps of a plan's `## Parallelization`, read and handed to impl.

`steps` reads the section into `[{name, paths, report}]`: a step opens with `(a) <title>`, its
paths are the bullets under it, one path or glob each as in `## Files that change`, and the first
other line starts what it reports. `none: one session`, or no section, is `[]`.

The block names the steps to impl's leading session only when there are two or more, which is
when it starts one `worker` for each; how the agents talk is the kernel's
(`coscc/agent/helpers.py`). Nothing here checks a plan: two steps sharing a path, or a step past
its ceiling, is not refused yet.
"""

from __future__ import annotations

import re
from typing import TypedDict

from coscc.kernel import Block, Facts, Feature, Parts

HEADING = "## Parallelization"
NONE = "none: one session"
_STEP = re.compile(r"\(([a-z])\)\s+(.+)")


class Step(TypedDict):
    name: str
    paths: list[str]
    report: str


def _section(plan_text: str) -> list[str] | None:
    """The lines under `HEADING` up to the next `## ` heading or the end, `None` without one."""
    lines = plan_text.splitlines()
    for i, line in enumerate(lines):
        if line.rstrip() == HEADING:
            end = next(
                (j for j in range(i + 1, len(lines)) if lines[j].startswith("## ")),
                len(lines),
            )
            return lines[i + 1 : end]
    return None


def _path(bullet: str) -> str:
    """`coscc/x.py (new)` or `` `coscc/x.py` `` as the path alone."""
    words = bullet.replace("`", "").split()
    return words[0] if words else ""


def steps(plan_text: str) -> list[Step]:
    """The plan's parallel steps, in order; `[]` for one session."""
    section = _section(plan_text)
    if section is None or any(line.strip().rstrip(".").lower() == NONE for line in section):
        return []
    found: list[Step] = []
    report: list[list[str]] = []
    for line in section:
        text = line.strip()
        head = _STEP.fullmatch(text)
        if head:
            found.append({"name": text, "paths": [], "report": ""})
            report.append([])
        elif not found:
            continue
        elif text.startswith("- ") and not report[-1]:
            found[-1]["paths"].append(_path(text[2:]))
        elif text or report[-1]:
            report[-1].append(line.rstrip())
    for step, lines in zip(found, report):
        step["report"] = "\n".join(lines).strip()
    return found


def render(facts: Facts) -> str:
    """impl's block: the steps, when the plan names two or more."""
    if facts.stage != "impl":
        return ""
    try:
        found = steps((facts.directory / "plan.md").read_text(encoding="utf-8"))
    except OSError:
        return ""
    if len(found) < 2:
        return ""
    parts = [
        "# The plan's parallel steps",
        "",
        f"The plan names {len(found)} steps to run at the same time: start one `worker` for "
        "each, with the step's line below as `description` and its paths and report in the "
        "prompt.",
    ]
    for step in found:
        parts += ["", step["name"], *(f"- {p}" for p in step["paths"]), step["report"]]
    return "\n".join(parts).rstrip()


FEATURE = Feature(
    "parallel",
    lambda _ctx: [],
    agent=lambda _ctx: Parts(blocks=(Block("parallel", render),)),
    summary="Lets impl start one helper per step of a plan's Parallelization section.",
)
