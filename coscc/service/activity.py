"""One workspace's insights: what it shipped, spent and spent again."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, TypedDict

from coscc.runlog import spend
from coscc.store.db import Busy
from coscc.kernel import Invalid

from coscc.config import Config

from coscc.units.workspaces import Workspaces

log = logging.getLogger(__name__)


# What a shipped unit should cost and how many review rounds it should take: the owner's targets.
TARGET_USD = spend.BUDGET_USD
TARGET_ROUNDS = 1.5
# A unit counts as shipped once the loop says it is finished, or finished with main moved on.
SHIPPED = ("finished", "outdated-main")


class Shipped(TypedDict):
    unit: str
    usd: float | None
    rounds: int
    at: str


class Target(TypedDict):
    """One target and where the window stands: `value` is the median over `shipped`, and
    `over` names the shipped units past the target, worst first."""

    name: str
    value: float | None
    target: float
    over: list[str]


class DaySpend(TypedDict):
    day: str
    usd: float | None
    steps: int


class StageSpend(TypedDict):
    stage: str
    usd: float | None
    steps: int
    unknown: int


class Waste(TypedDict):
    """Money spent again, one kind at a time; `not_recorded` counts review rounds no step claimed."""

    kind: str
    count: int
    usd: float | None
    unknown: int
    not_recorded: int


class Insights(TypedDict):
    days: int
    recording: bool
    shipped: list[Shipped]
    targets: list[Target]
    by_day: list[DaySpend]
    by_stage: list[StageSpend]
    waste: list[Waste]


class Activity:
    def __init__(self, config: Config, ws: Workspaces) -> None:
        self.config = config
        self.ws = ws

    def _records_or_none(self, cwd: str) -> list[dict[str, Any]] | None:
        """Every record for this workspace, or `None` when nothing is being recorded."""
        self.ws.check(cwd)
        journal = self.ws.journal()
        if journal is None:
            return None
        try:
            return journal.records(self.ws.key(cwd))
        except Busy as e:
            raise Invalid(str(e)) from e

    def insights(
        self,
        cwd: str,
        units: Sequence[Mapping[str, Any]],
        days: int = 30,
        now: datetime | None = None,
    ) -> Insights:
        """How one workspace did over the last `days`: each unit it shipped with its cost and
        review rounds, the median of each against its target, the money by day and by stage, and
        what was spent again. `units` are the board's, for which shipped and their rounds."""
        rows = self._records_or_none(cwd)
        out: Insights = {
            "days": days,
            "recording": rows is not None,
            "shipped": [],
            "targets": [],
            "by_day": [],
            "by_stage": [],
            "waste": [],
        }
        if rows is None:
            return out
        since = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()
        recent = [r for r in rows if str(r.get("at") or "") >= since]
        rounds = {
            str(u["name"]): [str(r.get("verdict") or "") for r in u.get("rounds") or []]
            for u in units
        }
        found = spend.model(recent, rounds)
        whole = {r["key"]: r["usd"] for r in spend.model(rows)["by_unit"]}
        # When the app merged it: its `ship` record. A unit merged by hand has none and is left out.
        shipped_at: dict[str, str] = {}
        for r in rows:
            if r.get("kind") == "ship" and r.get("result") == "shipped" and r.get("unit"):
                shipped_at[str(r["unit"])] = str(r.get("at") or "")
        for u in units:
            name = str(u["name"])
            if u.get("why") in SHIPPED and shipped_at.get(name, "") >= since:
                out["shipped"].append(
                    {
                        "unit": name,
                        "usd": whole.get(name),
                        "rounds": len(rounds[name]),
                        "at": shipped_at[name],
                    }
                )
        out["shipped"].sort(key=lambda s: s["at"], reverse=True)
        by_cost = sorted(out["shipped"], key=lambda s: -(s["usd"] or 0))
        by_rounds = sorted(out["shipped"], key=lambda s: -s["rounds"])
        costs = [s["usd"] for s in out["shipped"] if s["usd"] is not None]
        out["targets"] = [
            {
                "name": "cost",
                "value": round(median(costs), 2) if costs else None,
                "target": TARGET_USD,
                "over": [s["unit"] for s in by_cost if (s["usd"] or 0) > TARGET_USD],
            },
            {
                "name": "rounds",
                "value": median([s["rounds"] for s in out["shipped"]]) if out["shipped"] else None,
                "target": TARGET_ROUNDS,
                "over": [s["unit"] for s in by_rounds if s["rounds"] > TARGET_ROUNDS],
            },
        ]
        out["by_day"] = [
            {"day": d["key"], "usd": d["usd"], "steps": d["steps"]} for d in found["by_day"]
        ]
        out["by_stage"] = [
            {"stage": r["key"], "usd": r["usd"], "steps": r["steps"], "unknown": r["unknown"]}
            for r in found["by_stage"]
        ]
        out["waste"] = [
            {
                "kind": w["kind"],
                "count": w["count"],
                "usd": w["usd"],
                "unknown": w["unknown"],
                "not_recorded": int(w["note"] or 0),
            }
            for w in found["waste"]
        ]
        return out
