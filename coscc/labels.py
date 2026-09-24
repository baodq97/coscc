"""What a plan says about how hard its work is, and what the app runs it as.

`0033_impl-runs-one-model-whatever-the-plan-demands`. A plan's header declares
`Impl: routine` or `Impl: novel` (spec R1). This module turns that into the **effective**
label a step runs under, and says where it came from:

- `forced` — `## Files that change` names a file in `SECURITY_SURFACE` (R3);
- `missing` — the plan declares nothing usable, and nothing is cheaper to be wrong about
  than running it as `novel` (R5, spec C3);
- `escalated` — a `routine` plan whose earlier `impl` run stopped at `max_turns`, so this
  one runs as `novel` (R4). A budget stop does not escalate;
- `declared` — otherwise, what the plan said.

**The label opens and closes nothing.** It picks a model and an effort in
`coscc/models.py`; `cos.mjs` never reads it (R11). The rule that forces `novel` reads only
what the plan lists, so a file the impl touches without the plan naming it escapes it
(spec C2) — the escalation is the only net left behind that.

Nothing here raises. A plan that cannot be read is `missing`.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# The one list of files where a mistake costs the most (spec R2, `0033 intent.md ## Answers,
# câu 2`). `coscc/sessions.py` stands for `_options`: a list of files cannot name a
# function, so every plan touching that file is forced (spec C4). Pinned by
# `coscc/labels_test.py`; nothing else in the code may hold a copy.
SECURITY_SURFACE = (
    "coscc/policy.py",
    "coscc/sessions.py",
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
    rest = plan_text[start.end():]
    end = _HEADING.search(rest)
    section = rest[: end.start()] if end else rest
    return {n for n in (_normalise(t) for t in _TOKEN.findall(section)) if n}


def _stopped_at_max_turns(end: dict[str, Any], before: Iterable[dict[str, Any]]) -> bool:
    """An `end` that was `exhausted` on `max_turns`. An `end` written before `0033` carries
    no `terminal`; the `attempt` row `0019` writes just before it does."""
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

    A stage at or before `plan` has no label: its plan is not written yet (spec R10).
    `impl_history` is the unit's run log records for stage `impl`, oldest first.
    """
    try:
        if "plan" not in stages or stage not in stages or stages.index(stage) <= stages.index("plan"):
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
