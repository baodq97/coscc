"""`0081`. A person allows one more review round to a unit that used all of its.

Whether a unit is out of rounds, and what its limit is, is `cos.mjs`'s decision
(`moreRounds`, `reviewLimit`): the board carries `more_rounds` and this module only reads
it. Nothing here compares rounds used with a limit (`.claude/CLAUDE.md`: "nothing may hold a
second copy of it").

The pure functions `Service.more_rounds` shapes its write with: `refusal` and `block`.
"""

from __future__ import annotations

from typing import Any

# R1, R7: the app always grants exactly one round a press.
BLOCK = "\n### More rounds\nDecided by: {by}. Date: {today}. Via: product.\nRounds: 1\n"


def block(by: str, today: str) -> str:
    """R1: the block appended under `review.md ## Answers`, blank line first."""
    return BLOCK.format(by=by, today=today)


def refusal(found: dict[str, Any] | None, by: str, busy: str) -> str:
    """The first reason a round is refused, or `""`. Spec R6, in that order."""
    if found is None:
        return "no such work unit in this workspace"
    if not found.get("more_rounds"):
        return (
            f"{found.get('name', 'this unit')} has not used all its review rounds with findings "
            "still open; there is no round to add"
        )
    if "\n" in by or "\r" in by:
        return "the name must be one line"
    if by.lstrip().startswith("#"):
        return "the name may not start with #"
    if busy:
        # `busy` is `steps.describe`'s sentence (`0050` R3), as for a hold.
        return f"{busy}; allowing a round does not stop anything itself"
    return ""
