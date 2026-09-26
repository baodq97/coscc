"""What earlier reviews said about the files a plan changes, for its `impl` step (`0110`).

`0110_review-sends-most-units-back-to-impl-at-least-once`. 37 of the 51 findings that sent a
unit back to `impl` named a path an earlier shipped unit's review had already named
(`spike.md ## U2`). Before an `impl` step, `Service.run_step` hands this module the units the
board it already read reports `finished`; it reads their `review.md` in the store, keeps the
finding lines whose path the plan's `## Files that change` names, and gives back the section
`runner.compose_prompt` places and the record `Runner.run` puts into `start` (`spec.md` R6,
R7).

It matches by path, never by what a finding says (`spec.md` C2). It opens no `cos.db`, runs
no `git` and asks nothing of `cos.mjs`. Every function here but `section_for` and `for_step`
is pure.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from coscc.drift import files_section, mentioned

# `spec.md` R6. The bytes of the lines one step receives, heading and advice aside. Chosen,
# not measured: a file twelve earlier units named reaches it (`spike.md ## U2`, Giới hạn).
CAP_BYTES = 6144

# Its own, not `runner._ROUND_RE`: `runner` would have to be imported, and it imports this.
_ROUND = re.compile(r"^## Round (\d+)\b")
_FINDING = re.compile(r"^- F\d+ \[[^\]]*\]\s+(\S+)")
_LINE_REF = re.compile(r":[\d,-]+$")


def _empty() -> dict[str, Any]:
    return {"bytes": 0, "lines": 0, "units": 0, "dropped": 0}


def path_of(line: str) -> str:
    """The token after a finding's label, without backticks or a trailing `:line`; `""`
    when that token is not a path — nothing with neither `/` nor `.` is (an `S<n>`)."""
    m = _FINDING.match(line)
    if not m:
        return ""
    token = _LINE_REF.sub("", m.group(1).strip("`"))
    return token if ("/" in token or "." in token) else ""


def findings(text: str) -> list[tuple[int, int, str]]:
    """`(round, position, line)` for every finding line under a `## Round <n>`, in file
    order. A line under `## Answers`, or above the first round, is not one."""
    out: list[tuple[int, int, str]] = []
    number: int | None = None
    for i, line in enumerate(text.splitlines()):
        m = _ROUND.match(line)
        if m:
            number = int(m.group(1))
        elif line.startswith("## "):
            number = None
        elif number is not None and _FINDING.match(line):
            out.append((number, i, line.rstrip()))
    return out


def select(reviews: dict[str, str], plan_text: str) -> tuple[str, dict[str, Any]]:
    """`(section, record)`: the finding lines of `reviews` — unit to `review.md` text — whose
    path the plan's `## Files that change` names, prefixed `<NNNN> Round <n>`, by path,
    unit, round and place in the file, never over `CAP_BYTES`; and `{bytes, lines, units,
    dropped}` for the `start` record. No section, or nothing matching, is `""`."""
    files = files_section(plan_text)
    if files is None:
        return "", _empty()
    matched: dict[str, tuple[bytes, int, int, int]] = {}
    for unit, text in reviews.items():
        number = unit[:4]
        for rnd, pos, line in findings(text):
            path = path_of(line)
            if not path or not mentioned(files, [path]):
                continue
            out = f"- {number} Round {rnd} {line[2:]}"
            matched.setdefault(out, (path.encode(), int(number) if number.isdigit() else -1, rnd, pos))

    def order(lines: list[str]) -> list[str]:
        return sorted(lines, key=lambda x: matched[x])

    chosen = order(list(matched))
    if len("\n".join(chosen).encode("utf-8")) > CAP_BYTES:
        # The newest unit's lines first, whole, then back in order: `knowledge.slice_for`.
        taken, used = [], 0
        for line in sorted(chosen, key=lambda x: -matched[x][1]):
            size = len(line.encode("utf-8")) + (1 if taken else 0)
            if used + size <= CAP_BYTES:
                taken.append(line)
                used += size
        chosen = order(taken)
    section = "\n".join(chosen)
    return section, {
        "bytes": len(section.encode("utf-8")),
        "lines": len(chosen),
        "units": len({x.split(" ", 2)[1] for x in chosen}),
        "dropped": len(matched) - len(chosen),
    }


def section_for(
    cos_dir: str | os.PathLike[str], finished: list[str], plan_text: str, unit: str
) -> tuple[str, dict[str, Any]]:
    """`select` over the `review.md` of every unit in `finished` but `unit`. A unit with no
    `review.md` is skipped; one that cannot be read raises."""
    reviews: dict[str, str] = {}
    for name in finished:
        if name == unit:
            continue
        try:
            reviews[name] = (Path(cos_dir) / name / "review.md").read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
    return select(reviews, plan_text)


def for_step(
    cos_dir: str | os.PathLike[str], finished: list[str], plan_path: str | os.PathLike[str], unit: str
) -> dict[str, Any]:
    """`{prior_findings, prior_findings_record}` for `Runner.run`. Never raises: a `plan.md`
    or a `review.md` that cannot be read, or a bug here, is no section and a record carrying
    `error`, and the step runs as it would have."""
    try:
        plan_text = Path(plan_path).read_text(encoding="utf-8")
        section, record = section_for(cos_dir, finished, plan_text, unit)
    except Exception as e:  # noqa: BLE001 — recorded, never a reason to refuse the step
        return {
            "prior_findings": "",
            "prior_findings_record": {**_empty(), "error": f"{type(e).__name__}: {e}"},
        }
    return {"prior_findings": section, "prior_findings_record": record}
