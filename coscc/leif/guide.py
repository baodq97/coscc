"""What the board's guide says, with no I/O: every function here is pure.

Lists for a person: what runs now, what waits for them and where to do it, and what the
autopilot holds back. `Autopilot.guide_block` hands in what it already read.
"""

from __future__ import annotations

from typing import Any, Iterable

# What one stop asks of a person: `(do, screen, tab)`.
# Every stop kind of `decide.STOP_KINDS` but `full`, which only waits for a free place.
TODO: dict[str, tuple[str, str, str]] = {
    "a": ("Answer its open questions.", "unit", "questions"),
    "b": ("Decide what it waits on.", "unit", "overview"),
    "c": ("Ship it, or let the autopilot ship in Settings.", "unit", "overview"),
    "d": ("Settle what the integration could not.", "unit", "timeline"),
    "e": ("Look at the step that did not finish, then run it again.", "unit", "timeline"),
    "f": ("See why nothing can run.", "unit", "overview"),
    "reruns": ("Decide whether to run it again.", "unit", "artifacts"),
    "cap": ("Raise the daily cap, or wait for tomorrow.", "settings", ""),
    "shortlist": ("Put units on the shortlist.", "backlog", ""),
}


def running(entries: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Every step and integration running now, `[{unit, stage, agent,
    started}]`, oldest first. `entries` is `Board.running`'s `running`: by unit, each with
    its `agent` (`coscc/agent/agents.py`), `None` for a rebase."""
    out = [
        {
            "unit": unit,
            "stage": str(e.get("stage") or ""),
            "agent": str((e.get("agent") or {}).get("name") or ""),
            "started": str(e.get("started") or ""),
        }
        for unit, rows in entries.items()
        for e in rows
    ]
    return sorted(out, key=lambda r: (r["started"], r["unit"]))


def _item(stop: dict[str, Any]) -> dict[str, str] | None:
    """One thing to do for `stop`, `{unit, kind, do, reason, screen, tab}`; `None` for `full`.
    A stop with no unit is the workspace's: it links to nothing on a unit."""
    kind = str(stop.get("kind") or "")
    if kind not in TODO:
        return None
    do, screen, tab = TODO[kind]
    unit = str(stop.get("unit") or "")
    if screen == "unit" and not unit:
        screen, tab = "board", ""
    return {
        "unit": unit,
        "kind": kind,
        "do": do,
        "reason": str(stop.get("reason") or ""),
        "screen": screen,
        "tab": tab,
    }


def _labelled(units: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The units the board labels `Needs you`, by name. `state` is `unit_state`'s answer."""
    return sorted(
        (u for u in units if (u.get("state") or {}).get("state") == "needs-you"),
        key=lambda u: str(u.get("name") or ""),
    )


def needs_you(
    units: Iterable[dict[str, Any]], stops: Iterable[dict[str, Any]]
) -> list[dict[str, str]]:
    """One thing to do per unit labelled `Needs you`, `[{unit, kind, do, reason, screen, tab}]`,
    by unit name: as many as the cards with that label. A unit with a stop takes that stop's
    `TODO`; one without is `kind` `needs-you`, with its `attention_reason` as the reason."""
    by_unit = {str(s.get("unit") or ""): s for s in stops if s.get("unit")}
    out = []
    for u in _labelled(units):
        name = str(u.get("name") or "")
        item = _item(by_unit[name]) if name in by_unit else None
        if item is None:
            asked = int(u.get("open") or 0) > 0
            item = {
                "unit": name,
                "kind": "needs-you",
                "do": "Answer its open questions." if asked else "See what it waits on.",
                "reason": str(u.get("attention_reason") or ""),
                "screen": "unit",
                "tab": "questions" if asked else "overview",
            }
        out.append(item)
    return out


def held(units: Iterable[dict[str, Any]], stops: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    """The stops on units not labelled `Needs you`, in the order given: the autopilot holds
    them back, and their cards say something else (`Error`, `Ready`)."""
    asking = {str(u.get("name") or "") for u in _labelled(units)}
    items = (_item(s) for s in stops if s.get("unit") and s["unit"] not in asking)
    return [i for i in items if i is not None]


def notes(stops: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    """The stops of the workspace, in the order given, but `shortlist`: the board says that one
    outside the lists."""
    items = (_item(s) for s in stops if not s.get("unit") and s.get("kind") != "shortlist")
    return [i for i in items if i is not None]


# What a unit the autopilot did not start waits on, by the code it was held with
# (`decide.REASONS`): `(why, moves_it)`, `{detail}` the code's detail.
WAITS: dict[str, tuple[str, str]] = {
    "held": (
        "A person put it on hold ({detail}).",
        "A person taking the hold off.",
    ),
    "stop": ("{detail}", "A person looking at the stop."),
    "ci": ("CI is still running on its pull request.", "CI finishing; the next pass asks again."),
    "overlap": (
        "A step of {detail} changes the same files.",
        "That step ending.",
    ),
    "ship-busy": ("{detail} is shipping, and one ship runs at a time.", "That ship ending."),
    "missing": (
        "It is on the shortlist but not on the board.",
        "Its directory coming back, or a person taking it off the shortlist.",
    ),
    "dependency": (
        "It waits on a unit it depends on: {detail}",
        "That unit merging.",
    ),
    "overlap-pr": (
        "Another unit's pull request {detail} changes files its plan names.",
        "That pull request merging or closing.",
    ),
    "full": (
        "The most steps this workspace allows are already running.",
        "A running step ending, or a person raising Max parallel in Settings.",
    ),
    "cap": (
        "The daily cap is spent.",
        "Tomorrow, or a person raising the daily cap in Settings.",
    ),
    "session-limit": (
        "The account reached its session limit.",
        "The limit resetting; the autopilot runs it again then.",
    ),
    "conflict-running": (
        "PR conflicts with main; it is integrated once {detail} ends.",
        "The {detail} step ending; the autopilot then integrates it first.",
    ),
    "conflict-person": (
        "PR conflicts with main while {detail} runs, and the autopilot may not ship here.",
        "A person integrating it or merging once {detail} ends.",
    ),
}
# The codes of a unit that is not waiting: a step of it runs, or it is over.
NOT_WAITING = ("running", "finished", "closed")


def waiting_line(
    held: tuple[str, str] | None, stop: dict[str, Any] | None
) -> dict[str, str] | None:
    """What the card says a unit waits on, `{code, why, moves_it, until}`, or `None` when it does
    not wait. `held` is the `(code, detail)` the last pass held it with, `stop` its stop. A
    conflict while its step runs is said over a stop; a stop over its code. `until` is the moment
    the session limit resets, else `""`."""
    code, detail = held or ("", "")
    if code in NOT_WAITING:
        return None
    if stop is not None and not code.startswith("conflict-"):
        kind = str(stop.get("kind") or "")
        moves = TODO[kind][0] if kind in TODO else "A running step ending, so a place frees."
        return {
            "code": kind,
            "why": str(stop.get("reason") or ""),
            "moves_it": moves,
            "until": "",
        }
    if code not in WAITS:
        return None
    why, moves = WAITS[code]
    return {
        "code": code,
        "why": why.format(detail=detail or "another unit"),
        "moves_it": moves.format(detail=detail or "its step"),
        "until": detail if code == "session-limit" else "",
    }


def waiting(units: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every unit with a waiting line that the board does not label `Needs you`, by name,
    `[{unit, code, why, moves_it, until}]`. `units` carry `Autopilot.show`'s `waiting`."""
    units = list(units)
    asking = {str(u.get("name") or "") for u in _labelled(units)}
    return [
        {"unit": str(u.get("name") or ""), **u["waiting"]}
        for u in sorted(units, key=lambda u: str(u.get("name") or ""))
        if u.get("waiting") and u.get("name") not in asking
    ]
