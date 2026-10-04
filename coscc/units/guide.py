"""What the board's guide says, with no I/O: every function here is pure.

Lists for a person: what runs now, what waits for them and where to do it, and what the
autopilot holds back. `Autopilot.guide_block` hands in what it already read.
"""

from __future__ import annotations

from typing import Any, Iterable

# What one stop asks of a person: `(do, screen, tab)`.
# Every stop kind of `autopilot.STOP_KINDS` but `full`, which only waits for a free place.
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
