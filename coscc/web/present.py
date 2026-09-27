"""How a stored value reads on the page (`0082` R5, R6, R7). Pure functions, no state.

The data and the API keep every raw value — an ISO time, an epoch, a full sha, a stored
Vietnamese word. `state.py` passes a value through here only when it builds a field the
page draws, so the page shows a reader's words while everything else keeps the original.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

# `0082` R7. Under this many seconds a time reads as "3 min ago"; past it, as a date.
RELATIVE_FOR = 24 * 3600

# `0082` R5. The English label of each stored relation. The key is what `backlog.RELATIONS`
# stores and what the page sends back; only the label is new.
RELATION_LABEL = {
    "liên quan": "related to",
    "trùng": "duplicates",
    "thay thế": "replaces",
    "phụ thuộc": "depends on",
}

# The same relation read from the other unit's side, for the two that have a direction.
RELATION_LABEL_IN = {
    "thay thế": "replaced by",
    "phụ thuộc": "needed by",
}

# `Result:` of a `### Outcome` block, as stored (`service.OUTCOME_RESULTS`), in English.
RESULT_LABEL = {
    "đạt": "met",
    "trượt": "missed",
    "không đo được": "could not be measured",
}

# `Measured by:` of a `### Outcome` block, as the board offers it (`0089` R4). `owner` is the
# signed-in person (S7); the API still takes any word.
MEASURER_LABEL = {"agent": "Agent", "owner": "You"}

# `service.outcome_label`'s kinds, in English (D22).
OUTCOME_LABEL = {
    "met": "met",
    "missed": "missed",
    "unmeasurable": "could not be measured",
    "due": "due, not measured",
    "pending": "not due yet",
}


def _parse(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        number = float(value)
        # Epoch milliseconds (what the SDK's `last_modified` is, `.cos/0082_*/spike.md ## U2`)
        # or seconds: anything past the year 5000 in seconds is read as milliseconds.
        if number > 1e11:
            number /= 1000
        return datetime.fromtimestamp(number, tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def when(value, now: datetime | None = None) -> str:
    """A time for a reader: "3 min ago" under a day, "Sep 24, 09:00" (local) past it.

    Takes an ISO string, epoch milliseconds or seconds (int or digits), or nothing — which
    reads as `""`. A string that is none of these is returned as it is, never guessed at.
    """
    moment = _parse(value)
    if moment is None:
        return "" if value in (None, "") else str(value)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    seconds = (now - moment).total_seconds()
    if 0 <= seconds < RELATIVE_FOR:
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            return f"{int(seconds // 60)} min ago"
        return f"{int(seconds // 3600)} h ago"
    local = moment.astimezone()
    return f"{local.strftime('%b')} {local.day}, {local.strftime('%H:%M')}"


def short_sha(sha) -> str:
    """The first seven characters of a commit id, `""` for none."""
    return str(sha or "")[:7]


def money(usd: float | None) -> str:
    """An amount in dollars for the *Cost* screen (`0093` R4).

    `—` for none, so a sum nobody knows never reads as `$0`. From a dollar up, two decimals;
    under one, three significant digits (`$0.123`, `$0.00456`), so the rounding alone never
    moves a figure by more than the 1% R4 allows.
    """
    if usd is None:
        return "—"
    if usd == 0:
        return "$0.00"
    if abs(usd) >= 1:
        return f"${usd:,.2f}"
    places = 2 - math.floor(math.log10(abs(usd)))
    if abs(round(usd, places)) >= 1:
        return f"${usd:,.2f}"
    return f"${usd:.{places}f}"
