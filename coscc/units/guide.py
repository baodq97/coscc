"""What the board's guide says (`0101` R10), with no I/O: every function here is pure.

Three lists for a person who has no operator beside them: what runs now, what waits for
them and where to do it, and what Jera decided for them lately. `Service._guide_block` hands
in what it already read — the running entries, the autopilot's stops, the board's run-log
rows — and shows what comes back. Nothing here decides anything the autopilot does.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from coscc.agent import precedent

# What one stop asks of a person, and where on the page to do it: `(do, screen, tab)`. `tab`
# is one of `coscc/web/place.py` `TABS` for `screen` `unit`. Every stop kind of
# `autopilot.STOP_KINDS` but `full`, which only waits for a free place.
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
# `spec.md` R10: at most this many of Jera's answers, from this many days back.
DECIDED_MAX = 10
DECIDED_DAYS = 7


def _moment(at: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(at or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def running(entries: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Every step, integration and Jera session running now, `[{unit, stage, agent,
    started}]`, oldest first. `entries` is `Service.running`'s `running`: by unit, each with
    its `agent` (`coscc/agent/agents.py`), `None` for a rebase."""
    out = [
        {"unit": unit, "stage": str(e.get("stage") or ""),
         "agent": str((e.get("agent") or {}).get("name") or ""), "started": str(e.get("started") or "")}
        for unit, rows in entries.items() for e in rows
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
        out.append({"unit": unit, "kind": kind, "do": do, "reason": str(stop.get("reason") or ""),
                    "screen": screen, "tab": tab})
    return out


def decided(records: Iterable[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Jera's answers that were written, `DECIDED_DAYS` back from `now`, newest first and at
    most `DECIDED_MAX`: `[{unit, artifact, n, at, tab}]`."""
    oldest = now - timedelta(days=DECIDED_DAYS)
    found = []
    for r in records:
        if r.get("kind") != "precedent" or r.get("written") is not True or r.get("verdict") != precedent.ANSWER:
            continue
        moment = _moment(r.get("at"))
        if moment is None or moment < oldest:
            continue
        found.append((moment, {"unit": str(r.get("unit") or ""), "artifact": str(r.get("artifact") or ""),
                               "n": r.get("n"), "at": str(r.get("at") or ""), "tab": "questions"}))
    found.sort(key=lambda x: x[0], reverse=True)
    return [row for _, row in found[:DECIDED_MAX]]
