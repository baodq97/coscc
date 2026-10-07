"""One workspace's insights: what it shipped, spent and spent again."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, TypedDict, get_args

from coscc.leif import spend
from coscc.store.db import Busy
from coscc.store.journal import SHIP_RECORD
from coscc.kernel import Invalid
from coscc.units.contracts import Graded

from coscc.config import Config

from coscc.units.workspaces import Workspaces

log = logging.getLogger(__name__)

GRADED = get_args(Graded)


# What a shipped unit should cost and how many review rounds it should take: the owner's targets.
TARGET_USD = spend.BUDGET_USD
TARGET_ROUNDS = 1.5
# A unit counts as shipped once the loop says it is finished, or finished with main moved on.
SHIPPED = ("finished", "outdated-main")
# The owner's outcome targets: every shipped unit graded within 7 days of the week the grader
# waits after a merge, and 95% of those graded met.
WAIT = timedelta(days=7)
TARGET_GRADED = 1.0
TARGET_MET = 0.95


class Shipped(TypedDict):
    unit: str
    usd: float | None
    rounds: int
    at: str
    # Its latest graded outcome (`contracts.Graded`), `""` when none.
    outcome: str


class Outcomes(TypedDict):
    """The shipped units' outcomes: `due` those shipped a week ago or more, `on_time` those of
    them graded within 7 days after that week, `graded` those with a verdict, `met` those whose
    latest is met, `missed` those whose latest is not met or unclear, not met first."""

    due: int
    on_time: int
    graded: int
    met: int
    target_graded: float
    target_met: float
    missed: list[str]


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


class AgentRun(TypedDict):
    """One run of an agent, which `/api/runs/{run}` opens: its unit (`""` for none), when it
    ended, what it cost and how."""

    run: str
    unit: str
    at: str
    usd: float | None
    outcome: str


class AgentSpend(TypedDict):
    """What one agent spent: a stage's, the estimate's, a feature's session's, Gebo's or chat's,
    with its latest runs."""

    agent: str
    usd: float | None
    steps: int
    unknown: int
    runs: list[AgentRun]


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
    by_agent: list[AgentSpend]
    waste: list[Waste]
    outcomes: Outcomes


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
        review rounds, the median of each against its target, the money by day and by agent, and
        what was spent again. `units` are the board's, for which shipped and their rounds."""
        rows = self._records_or_none(cwd)
        out: Insights = {
            "days": days,
            "recording": rows is not None,
            "shipped": [],
            "targets": [],
            "by_day": [],
            "by_agent": [],
            "waste": [],
            "outcomes": _outcomes([], {}, now),
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
            if r.get("kind") == SHIP_RECORD and r.get("result") == "shipped" and r.get("unit"):
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
                        "outcome": "",
                    }
                )
        out["shipped"].sort(key=lambda s: s["at"], reverse=True)
        verdicts = _verdicts(rows)
        for s in out["shipped"]:
            s["outcome"] = verdicts[s["unit"]][-1][1] if s["unit"] in verdicts else ""
        out["outcomes"] = _outcomes(out["shipped"], verdicts, now)
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
        out["by_agent"] = [
            {
                "agent": r["key"],
                "usd": r["usd"],
                "steps": r["steps"],
                "unknown": r["unknown"],
                "runs": r["runs"],
            }
            for r in found["by_agent"]
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


def _verdicts(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[tuple[str, str]]]:
    """`{unit: [(at, graded)]}`, oldest first: each run that graded a unit's outcome, as its `end`
    says (`coscc/runner/triggers.py`)."""
    out: dict[str, list[tuple[str, str]]] = {}
    for r in rows:
        if r.get("kind") == "end" and r.get("unit") and r.get("verdict") in GRADED:
            out.setdefault(str(r["unit"]), []).append((str(r.get("at") or ""), str(r["verdict"])))
    return out


def _outcomes(
    shipped: Sequence[Shipped],
    verdicts: Mapping[str, list[tuple[str, str]]],
    now: datetime | None,
) -> Outcomes:
    """KR2.1 and KR2.2 over `shipped`."""
    at = now or datetime.now(timezone.utc)
    due = on_time = met = 0
    missed: list[tuple[int, str]] = []
    for s in shipped:
        try:
            shipped_at = datetime.fromisoformat(s["at"])
        except ValueError:
            continue
        found = verdicts.get(s["unit"]) or []
        if shipped_at + WAIT <= at:
            due += 1
            limit = (shipped_at + 2 * WAIT).isoformat()
            on_time += bool(found) and found[0][0] <= limit
        if not found:
            continue
        last = found[-1][1]
        met += last == "met"
        if last != "met":
            missed.append((0 if last == "not-met" else 1, s["unit"]))
    return {
        "due": due,
        "on_time": on_time,
        "graded": sum(1 for s in shipped if s["unit"] in verdicts),
        "met": met,
        "target_graded": TARGET_GRADED,
        "target_met": TARGET_MET,
        "missed": [u for _, u in sorted(missed)],
    }
