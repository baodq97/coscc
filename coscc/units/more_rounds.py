"""A person allows one more review round to a unit that used all of its.

The loop decides whether a unit is out of rounds; this module only reads the board's
`more_rounds` and never compares rounds with a limit.
"""

from __future__ import annotations

from typing import Any

BLOCK = "\n### More rounds\nDecided by: {by}. Date: {today}. Via: product.\nRounds: 1\n"


def block(by: str, today: str) -> str:
    """The block appended under `review.md ## Answers`, blank line first."""
    return BLOCK.format(by=by, today=today)


def refusal(found: dict[str, Any] | None, by: str, busy: str) -> str:
    """The first reason a round is refused, or `""`."""
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
        return f"{busy}; allowing a round does not stop anything itself"
    return ""
