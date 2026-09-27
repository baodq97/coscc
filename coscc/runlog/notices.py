"""What a run-log record says to whoever is listening (`0113`), with no I/O.

`notice_of` is the one place a record becomes a notice: which of the five kinds of R2 it is,
and the sentence shown for it. The stream (`service/notices.py`), the page's script and a
terminal all read what it returns and decide nothing more.

A notice only tells (R13): nothing here writes, and no sentence reads as anyone's approval.
The sentence names the unit and the workspace's folder name and never carries a stop's
`reason`, a `detail`, a path, a SHA or a `run` — a reason can hold any of them, or `gh`'s
words (S3). The whole record still goes out beside it, as `record`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from coscc.units import autopilot
from coscc.web.auth import WS_RECHECK

# The run-log kinds a notice can come from; `Journal.notice_rows` narrows on them.
SOURCE_KINDS = ("autopilot-stop", "questions", "end", "ship")
# R2's five, in its order.
KINDS = ("autopilot-stop", "questions", "step-ended", "ship-refused", "shipped")
# R8. Seconds between two `beat` lines of a quiet stream. Chosen by the spec, not measured.
BEAT_SECONDS = 15.0
# Seconds one stream lasts before it ends and its listener connects again with `after`.
# `auth.Guard` asks for a live session once per request, so without an end a listener whose
# session was logged out or cleared would go on hearing (review round 1, F1). The same bound
# an open socket has.
LIFETIME_SECONDS = WS_RECHECK
# How many rows one read of the stream takes at most. Chosen, not measured.
PAGE = 500

# What each stop of `autopilot.STOP_KINDS` means, said of a unit. `full` is no stop of the
# autopilot's and never reaches here (R2).
STOPS = {
    "a": "it has open questions",
    "b": "it is waiting for a person",
    "c": "shipping waits for a person",
    "d": "its last integration needs a person",
    "e": "its last step did not finish",
    "f": "there is nothing it may run",
    "cap": "the daily spending cap is reached",
    "shortlist": "nothing is on the shortlist",
    "reruns": "a draft was run again as often as it may",
}
# The same, said of a whole workspace: a failed look at it, `COS_HOST` off loopback, a busy
# run log (R3). The reason says which; the sentence does not (S3).
WORKSPACE_F = "it could not look at the workspace"
UNKNOWN_STOP = "the autopilot stopped"

# How an `end` that is not `done` is said (`journal.OUTCOMES`).
ENDED = {
    "failed": "failed",
    "exhausted": "ran out of turns",
    "stopped": "was stopped",
    "cancelled": "was cancelled",
}


def _where(record: dict[str, Any]) -> str:
    """The workspace's folder name, never its path (S3)."""
    return Path(str(record.get("workspace") or "")).name or "this workspace"


def _stop_text(record: dict[str, Any]) -> str:
    unit = str(record.get("unit") or "")
    stop = str(record.get("stop") or "")
    where = _where(record)
    if not unit:
        said = WORKSPACE_F if stop == "f" else STOPS.get(stop)
        if said is None:
            return f"The autopilot stopped in {where}."
        return f"The autopilot stopped in {where}: {said}."
    said = STOPS.get(stop)
    if said is None:
        return f"The autopilot stopped on {unit} in {where}."
    return f"The autopilot stopped on {unit} in {where}: {said}."


def _kind_and_text(record: dict[str, Any]) -> tuple[str, str] | None:
    kind = record.get("kind")
    unit = str(record.get("unit") or "")
    stage = str(record.get("stage") or "")
    where = _where(record)
    if kind == "autopilot-stop":
        stop = str(record.get("stop") or "")
        if not stop or stop == "full":
            return None
        return "autopilot-stop", _stop_text(record)
    if kind == "questions":
        n = len(record.get("questions") or [])
        asked = f"{n} open question{'' if n == 1 else 's'}" if n else "open questions"
        after = f" after its {stage} step" if stage else ""
        return "questions", f"{unit or 'A unit'} in {where} has {asked}{after}."
    if kind == "end":
        outcome = str(record.get("outcome") or "")
        if not unit or not autopilot.is_step(record) or outcome == "done":
            return None
        said = ENDED.get(outcome, "ended without finishing")
        return "step-ended", f"The {stage or 'last'} step of {unit} in {where} {said}."
    if kind == "ship":
        result = record.get("result")
        if result == "shipped":
            return "shipped", f"{unit} in {where} was merged."
        if result == "refused":
            return "ship-refused", f"{unit} in {where} did not merge."
    return None


def notice_of(id: int, record: dict[str, Any]) -> dict[str, Any] | None:
    """R1's notice line for the run-log row `id`, or `None` when R2 makes it no notice.

    The keys stay in this order: `type` then `id` open every line, and the terminal command
    in `.claude/docs/coscc-notices.md` reads `id` off that prefix."""
    found = _kind_and_text(record)
    if found is None:
        return None
    kind, text = found
    return {
        "type": "notice",
        "id": int(id),
        "at": str(record.get("at") or ""),
        "workspace": str(record.get("workspace") or ""),
        "unit": str(record.get("unit") or ""),
        "stage": str(record.get("stage") or ""),
        "kind": kind,
        "text": text,
        "record": record,
    }
