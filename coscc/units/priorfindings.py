"""What earlier reviews said about the files a plan changes, for its `impl` step.

Reads the `review.md` of finished units, keeps the finding lines whose path the plan's
`## Files that change` names, and returns the prompt section and the `start` record. It
matches by path only. Every function but `section_for` and `for_step` is pure.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from coscc.git.drift import files_section, mentioned

# The bytes of the lines one step receives, heading and advice aside.
CAP_BYTES = 6144

# Its own, not `runner._ROUND_RE`: `runner` would have to be imported, and it imports this.
_ROUND = re.compile(r"^## Round (\d+)\b")
_FINDING = re.compile(r"^- (F\d+) \[[^\]]*\]\s+(\S+)")
_LINE_REF = re.compile(r":[\d,-]+$")


def _empty() -> dict[str, Any]:
    return {"bytes": 0, "lines": 0, "units": 0, "dropped": 0}


def path_of(line: str) -> str:
    """The token after a finding's label, without backticks or a trailing `:line`; `""` if not a path."""
    m = _FINDING.match(line)
    if not m:
        return ""
    token = _LINE_REF.sub("", m.group(2).strip("`"))
    return token if ("/" in token or "." in token) else ""


def findings(text: str) -> list[tuple[int, int, str]]:
    """`(round, position, line)` for every finding line under a `## Round <n>`, in file order."""
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
    """`(section, record)`: the finding lines of `reviews` (unit to `review.md` text) whose path the plan names.

    Lines are prefixed `<NNNN> Round <n>`, never over `CAP_BYTES`; the record is `{bytes, lines,
    units, dropped}`. No match is `""`. Only the last line a finding is stated on is kept,
    because every round carries earlier findings forward.
    """
    files = files_section(plan_text)
    if files is None:
        return "", _empty()
    latest: dict[tuple[str, str], tuple[int, int, str]] = {}
    for unit, text in reviews.items():
        for rnd, pos, line in findings(text):
            latest[(unit, _FINDING.match(line).group(1))] = (rnd, pos, line)
    matched: dict[str, tuple[bytes, int, int, int]] = {}
    for (unit, _), (rnd, pos, line) in latest.items():
        number = unit[:4]
        path = path_of(line)
        if not path or not mentioned(files, [path]):
            continue
        out = f"- {number} Round {rnd} {line[2:]}"
        matched.setdefault(out, (path.encode(), int(number) if number.isdigit() else -1, rnd, pos))

    def order(lines: list[str]) -> list[str]:
        return sorted(lines, key=lambda x: matched[x])

    chosen = order(list(matched))
    if len("\n".join(chosen).encode("utf-8")) > CAP_BYTES:
        # The newest unit's lines first, whole, then back in order.
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
    """`select` over the `review.md` of every unit in `finished` but `unit`; an unreadable one is counted `unreadable`."""
    reviews: dict[str, str] = {}
    unreadable = 0
    for name in finished:
        if name == unit:
            continue
        try:
            reviews[name] = (Path(cos_dir) / name / "review.md").read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError):
            unreadable += 1
    section, record = select(reviews, plan_text)
    if unreadable:
        record["unreadable"] = unreadable
    return section, record


def for_step(
    cos_dir: str | os.PathLike[str], finished: list[str], plan_path: str | os.PathLike[str], unit: str
) -> dict[str, Any]:
    """`{prior_findings, prior_findings_record}` for `Runner.run`. Never raises: a failure is no section and a record with `error`."""
    try:
        plan_text = Path(plan_path).read_text(encoding="utf-8")
        section, record = section_for(cos_dir, finished, plan_text, unit)
    except Exception as e:  # noqa: BLE001 — recorded, never a reason to refuse the step
        return {
            "prior_findings": "",
            "prior_findings_record": {**_empty(), "error": f"{type(e).__name__}: {e}"},
        }
    return {"prior_findings": section, "prior_findings_record": record}
