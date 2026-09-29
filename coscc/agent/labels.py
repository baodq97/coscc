"""What a plan says about how hard its work is, and what the app runs it as.

A plan's header declares `Impl: routine` or `Impl: novel`; this turns that into the
effective label and its source: `forced` (`## Files that change` names a `SECURITY_SURFACE`
file), `missing` (nothing usable declared; run as `novel`), `escalated` (a `routine` plan
whose earlier `impl` stopped at `max_turns`; a budget stop does not escalate), `declared`.
The label picks a model and effort in `models.py`; `cos.mjs` never reads it. Nothing raises.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# The files where a mistake costs the most. `coscc/agent/sessions.py` stands for `_options`
# (a list of files cannot name a function). Pinned by `coscc/agent/labels_test.py`.
SECURITY_SURFACE = (
    "coscc/agent/policy.py",
    "coscc/agent/sessions.py",
    ".claude/scripts/cos.mjs",
    ".claude/settings.json",
)

ROUTINE = "routine"
NOVEL = "novel"
MISSING = "missing"

DECLARED = "declared"
FORCED = "forced"
ESCALATED = "escalated"

_LABEL = re.compile(r"\bImpl:\s*(\w+)", re.IGNORECASE)
_HEADING = re.compile(r"^## ", re.MULTILINE)
_FILES = re.compile(r"^## Files that change\s*$", re.MULTILINE)
_TOKEN = re.compile(r"[^\s,;()\[\]]+")
_LINE_SUFFIX = re.compile(r":\d.*$")


def declared(plan_text: str | None) -> str:
    """`routine`, `novel` or `missing`, from the header — the lines before the first `## `."""
    if not plan_text:
        return MISSING
    first = _HEADING.search(plan_text)
    header = plan_text[: first.start()] if first else plan_text
    found = _LABEL.search(header)
    value = found.group(1).lower() if found else ""
    return value if value in (ROUTINE, NOVEL) else MISSING


def _normalise(token: str) -> str:
    # A leading `.` is part of `.claude/…`, so only the trailing punctuation is cut.
    token = token.replace("`", "").strip("*'\"").rstrip(".:")
    if token.startswith("./"):
        token = token[2:]
    return _LINE_SUFFIX.sub("", token)


def listed_paths(plan_text: str | None) -> set[str]:
    """Every token under `## Files that change`, up to the next `## ` heading, normalised:
    backticks and a leading `./` dropped, a `:<line>…` suffix cut."""
    if not plan_text:
        return set()
    start = _FILES.search(plan_text)
    if start is None:
        return set()
    rest = plan_text[start.end() :]
    end = _HEADING.search(rest)
    section = rest[: end.start()] if end else rest
    return {n for n in (_normalise(t) for t in _TOKEN.findall(section)) if n}


def _stopped_at_max_turns(end: dict[str, Any], before: Iterable[dict[str, Any]]) -> bool:
    """An `end` that was `exhausted` on `max_turns`."""
    if end.get("outcome") != "exhausted":
        return False
    terminal = end.get("terminal")
    if terminal is None:
        for rec in reversed(list(before)):
            if rec.get("kind") == "attempt":
                terminal = rec.get("terminal")
                break
            if rec.get("kind") in ("start", "end"):
                break
    return "max_turns" in str(terminal or "").lower()


def label_for(
    stage: str,
    stages: list[str],
    plan_text: str | None,
    impl_history: list[dict[str, Any]],
) -> tuple[str | None, str | None, str | None]:
    """`(label_declared, label, label_source)` for one step. Never raises.

    A stage at or before `plan` has no label: its plan is not written yet.
    `impl_history` is the unit's run log records for stage `impl`, oldest first.
    """
    try:
        if (
            "plan" not in stages
            or stage not in stages
            or stages.index(stage) <= stages.index("plan")
        ):
            return None, None, None
        said = declared(plan_text)
        if listed_paths(plan_text) & set(SECURITY_SURFACE):
            return said, NOVEL, FORCED
        if said == MISSING:
            return said, NOVEL, MISSING
        if said == ROUTINE and stage == "impl":
            history = [r for r in impl_history if r.get("stage") == "impl"]
            for i, rec in enumerate(history):
                if rec.get("kind") == "end" and _stopped_at_max_turns(rec, history[:i]):
                    return said, NOVEL, ESCALATED
        return said, said, DECLARED
    except Exception:  # noqa: BLE001 — a label is never a reason for a step not to run
        return MISSING, NOVEL, MISSING
