"""What the board's guide says, with no I/O: every function here is pure.

Two lists for a person: what runs now, and what waits for them and where to do it.
`Autopilot.guide_block` hands in what it already read.
"""

from __future__ import annotations

from typing import Any, Iterable

# What one stop asks of a person: `(do, screen, tab)`, `tab` one of `coscc/state/place.py` `TABS`.
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


def needs_you(stops: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    """One thing to do per stop but `full`, `[{unit, kind, do, reason, screen, tab}]`, in the
    order given. A stop with no unit is the workspace's: it links to nothing on a unit."""
    out = []
    for stop in stops:
        kind = str(stop.get("kind") or "")
        if kind not in TODO:
            continue
        do, screen, tab = TODO[kind]
        unit = str(stop.get("unit") or "")
        if screen == "unit" and not unit:
            screen, tab = "board", ""
        out.append(
            {
                "unit": unit,
                "kind": kind,
                "do": do,
                "reason": str(stop.get("reason") or ""),
                "screen": screen,
                "tab": tab,
            }
        )
    return out
