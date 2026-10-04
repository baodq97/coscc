"""Activity, usage and cost, an artifact's text, settings and preferences."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, TypedDict

from coscc.runlog import spend
from coscc.data import Data
from coscc.runlog.journal import COST_FIELDS, COST_USD, add_cost, zero_cost
from coscc.data import Busy
from coscc.agent.policy import PROSE_STAGES
from coscc.service.common import STAGE_FILES
from coscc.kernel import Invalid

from coscc.config import Config

from coscc.service.workspaces import Workspaces

log = logging.getLogger(__name__)


def _count(questions: object) -> int:
    if isinstance(questions, int):
        return questions
    return len(questions) if isinstance(questions, list) else 0


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
    `over` names the shipped units past the target."""

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

    # -- activity, usage and settings ---------------------------------------

    def _records_or_none(self, cwd: str) -> list[dict[str, Any]] | None:
        """Every record for this workspace, or `None` when nothing is being recorded.

        Shared by `activity` and `usage`; see `activity_and_usage` for why it is read once.
        """
        self.ws.check(cwd)
        journal = self.ws.journal()
        if journal is None:
            return None
        try:
            return journal.records(self.ws.key(cwd))
        except Busy as e:
            raise Invalid(str(e)) from e

    def _events_of(self, cwd: str, rows: list[dict[str, Any]], limit: int) -> dict[str, Any]:
        events = [
            {
                "at": r.get("at") or "",
                "kind": r.get("kind") or "",
                "unit": r.get("unit") or "",
                "stage": r.get("stage") or "",
                "mode": r.get("mode") or "",
                "outcome": r.get("outcome") or "",
                "session_id": r.get("session_id") or "",
                "artifact": r.get("artifact") or "",
                "denials": int(r.get("denials") or 0),
                "cost": {f: r.get(f) for f in COST_FIELDS + (COST_USD,) if r.get(f)},
                # A `hold` row's move, reason, name and side effects; empty on every other kind.
                "from": r.get("from") or "",
                "to": r.get("to") or "",
                "reason": r.get("reason") or "",
                "by": r.get("by") or "",
                "effects": [e for e in r.get("effects") or [] if isinstance(e, dict)],
                # A `release` row's version and what happened; empty on every other kind.
                "version": str(r.get("version") or "") if r.get("kind") == "release" else "",
                "detail": str(r.get("detail") or "") if r.get("kind") == "release" else "",
                # A `transition`'s new state, how many questions a `questions` row asked, and a
                # `ship` row's result; empty on every other kind.
                "to_state": str(r.get("to_state") or ""),
                # Older rows record the count, newer ones the questions themselves.
                "asked": _count(r.get("questions")),
                "result": str(r.get("result") or ""),
            }
            for r in rows
            # A retake of the screenshots is recorded, and shown on no screen.
            if r.get("kind") != "screens"
        ]
        events.reverse()
        return {"cwd": cwd, "events": events[:limit], "recording": True}

    def _usage_of(self, cwd: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        # Only known costs are added; `unknown` counts the `end` rows that carried no `cost_usd`, so the sum is never shown as the whole of it.
        per_unit: dict[str, dict[str, Any]] = {}
        for record in rows:
            if record.get("kind") != "end":
                continue
            bucket = per_unit.setdefault(
                str(record.get("unit") or ""), {**zero_cost(), "unknown": 0}
            )
            add_cost(bucket, record)
            bucket["unknown"] += int(COST_USD not in record)
        total = {**zero_cost(), "unknown": 0}
        for bucket in per_unit.values():
            add_cost(total, bucket)
            total["unknown"] += bucket["unknown"]
        return {"cwd": cwd, "total": total, "per_unit": per_unit, "recording": True}

    def activity(self, cwd: str, limit: int = 40) -> dict[str, Any]:
        """What has happened across the whole workspace, newest first.

        Built from one read, not by calling `timeline` once per unit, which would spawn one board read per unit.
        """
        rows = self._records_or_none(cwd)
        if rows is None:
            return {"cwd": cwd, "events": [], "recording": False}
        return self._events_of(cwd, rows, limit)

    def usage(self, cwd: str) -> dict[str, Any]:
        """What this workspace has cost, added up from its records.

        Added rather than stored: a stored total is a second number that can disagree with the first.
        """
        rows = self._records_or_none(cwd)
        if rows is None:
            return {"cwd": cwd, "total": {}, "per_unit": {}, "recording": False}
        return self._usage_of(cwd, rows)

    def activity_and_usage(self, cwd: str, limit: int = 40) -> dict[str, Any]:
        """Both of the above, from one read: the page calls this rather than the two public methods, which parse the same rows twice."""
        rows = self._records_or_none(cwd)
        if rows is None:
            return {
                "cwd": cwd,
                "events": [],
                "total": {},
                "per_unit": {},
                "recording": False,
            }
        return {**self._events_of(cwd, rows, limit), **self._usage_of(cwd, rows)}

    def cost(self, cwd: str, rounds: dict[str, list[str]] | None = None) -> dict[str, Any]:
        """Where this workspace's money went, from one read of its run log.

        `rounds` is each unit's review verdicts, from the board read the page already has. Read only; no gate reads a figure here.
        """
        rows = self._records_or_none(cwd)
        if rows is None:
            return {"cwd": cwd, "recording": False}
        return {**spend.model(rows, rounds), "recording": True}

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
        last: dict[str, str] = {}
        for r in rows:
            if r.get("kind") == "end" and r.get("unit"):
                last[str(r["unit"])] = max(last.get(str(r["unit"]), ""), str(r.get("at") or ""))
        for u in units:
            name = str(u["name"])
            if u.get("why") in SHIPPED and last.get(name, "") >= since:
                out["shipped"].append(
                    {
                        "unit": name,
                        "usd": whole.get(name),
                        "rounds": len(rounds[name]),
                        "at": last[name],
                    }
                )
        out["shipped"].sort(key=lambda s: s["at"], reverse=True)
        costs = [s["usd"] for s in out["shipped"] if s["usd"] is not None]
        out["targets"] = [
            {
                "name": "cost",
                "value": round(median(costs), 2) if costs else None,
                "target": TARGET_USD,
                "over": [s["unit"] for s in out["shipped"] if (s["usd"] or 0) > TARGET_USD],
            },
            {
                "name": "rounds",
                "value": median([s["rounds"] for s in out["shipped"]]) if out["shipped"] else None,
                "target": TARGET_ROUNDS,
                "over": [s["unit"] for s in out["shipped"] if s["rounds"] > TARGET_ROUNDS],
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

    def unit_cost(self, cwd: str, unit: str) -> dict[str, Any]:
        """One unit's cost by stage and its anomalies.

        The whole log is read, not the unit's rows: a token-per-turn median is the workspace's.
        """
        rows = self._records_or_none(cwd)
        if rows is None:
            return {"by_stage": [], "anomalies": [], "recording": False}
        found = spend.model(rows)
        return {
            "by_stage": found["unit_stages"].get(unit, []),
            "anomalies": [a for a in found["anomalies"] if a["unit"] == unit],
            "recording": True,
        }

    def settings(self) -> dict[str, Any]:
        """The safety posture, as something a screen can render. Read only.

        The screen shows the four knobs and can change none of them; there is no setter here, as in `config.from_env`.

        The grants and the model of each stage are on the Agents page (`Agents.agent_page`), so they are not here. `cos_model` below is only the fallback for a row nothing else answers.
        """
        c = self.config
        return {
            "working_dir": c.working_dir,
            "data_dir": str(Data(c.data_dir).root),
            "host": c.host,
            "port": c.port,
            "cos_model": c.model,
            "knobs": [
                {
                    "name": "tools",
                    "value": ", ".join(c.effective_tools()) or "none",
                    "on": bool(c.effective_tools()),
                    "detail": "The tools a chat session gets; none means chat only.",
                },
                {
                    "name": "allow_write_and_exec",
                    "value": "on" if c.allow_write_and_exec else "off",
                    "on": c.allow_write_and_exec,
                    "detail": "While off, no chat session can write files or run commands.",
                },
                {
                    "name": "bypass_permissions",
                    "value": "on" if c.bypass_permissions else "off",
                    "on": c.bypass_permissions,
                    "detail": "Set only by the environment the app started with.",
                },
                {
                    "name": "resume_foreign_sessions",
                    "value": "on" if c.resume_foreign_sessions else "off",
                    "on": c.resume_foreign_sessions,
                    "detail": "The app resumes only the sessions it created.",
                },
            ],
            "prose_stages": list(PROSE_STAGES),
            "import_report": self._import_report(),
        }

    def _import_report(self) -> dict[str, Any]:
        """Every field an import could not read, its workspace by name, not a failed ingest: the card shows that one. A database that cannot be read is `problem`, in place of a Settings screen that does not load. What went wrong goes to the log, not the screen: `Busy` and `Incompatible` name the database's path."""
        names = {self.ws.key(r["path"]): str(r["name"]) for r in self.ws.all()["workspaces"]}
        try:
            found = self.ws.unit_meta().unknowns()
        except Exception:
            log.exception("the import report could not be read")
            return {"rows": [], "problem": "The import report could not be read."}
        return {
            "rows": [
                {**r, "workspace": names.get(r["workspace"], "a removed workspace")} for r in found
            ],
            "problem": "",
        }

    # -- artifacts and preferences ------------------------------------------

    def artifact(self, cwd: str, unit: str, stage: str) -> dict[str, Any]:
        """The text of one stage's artifact, or why there is none.

        The path is built by `runner.unit_dir`, which validates the unit name against the `NNNN_slug` shape, so a name this refuses is one no step could run against either.
        """
        self.ws.check(cwd)
        if stage not in STAGE_FILES:
            raise Invalid(f"no such stage: {stage}")
        filename = f"{stage}.md"
        path = self.ws.unit_dir(cwd, unit) / filename
        if not path.is_file():
            return {"unit": unit, "stage": stage, "file": filename, "text": "", "exists": False}
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise Invalid(f"could not read {filename}: {e}") from e
        return {"unit": unit, "stage": stage, "file": filename, "text": text, "exists": True}

    # Which preferences the page may keep: an open key/value store reachable from a request
    # is a place to put anything, so only the keys the Settings screen remembers are writable.
    PREFERENCES = {"density": "comfortable", "screen": "overview", "board_view": "Board"}

    def preferences(self) -> dict[str, Any]:
        data = Data(self.config.data_dir)
        stored = data.prefs()
        return {k: stored.get(k, default) for k, default in self.PREFERENCES.items()}

    def set_preference(self, key: str, value: Any) -> dict[str, Any]:
        if key not in self.PREFERENCES:
            raise Invalid(f"not a stored preference: {key}")
        if not isinstance(value, (str, int, float, bool)):
            raise Invalid("a preference must be a simple value")
        Data(self.config.data_dir).set_pref(key, value)
        return {"key": key, "value": value}
