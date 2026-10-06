"""The agents: who each is, what it runs on, what it may do and how its runs went.

The rows are `coscc/agent/pack.py`'s, resolved by `coscc/agent/agents.py` (who) and
`coscc/agent/models.py` (model, effort and the two ceilings); this is where a field the page
sets is written into the owner's layer and logged, and where the run log's `end` records are
added up for the Agents page. `Models` gathers the inputs for which model and effort each run
gets.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict

from coscc.agent import agents, models, modeltrial, pack, policy
from coscc.config import Config
from coscc.kernel import OWNER, Invalid
from coscc.leif import decide
from coscc.runner import run as run_mod
from coscc.store.db import Busy, Unusable
from coscc.store.journal import BadRecord, Journal
from coscc.units import board as board_reader
from coscc.units.contracts import Plan
from coscc.units.workspaces import Workspaces

log = logging.getLogger(__name__)

# The `runs` kind of one saved or reset field: the trace of who moved what.
SETTING_KIND = "agent-setting"
# How far back the page adds up cost and looks for a run; how many runs a drawer lists.
WINDOW_DAYS = 30
RECENT = 5
# A last run that spent this share of its budget or more is `costly`. Chosen, not measured.
COSTLY_SHARE = 0.8
# The chips, worst first: a row with one of `ATTENTION` is listed before the rest.
CHIPS = ("failed", "costly", "idle", "ok")
ATTENTION = ("failed", "costly")


class RunView(TypedDict):
    """One `end` record of a stage, as the page shows it; `workspace` is the run-log key, the
    workspace's resolved path."""

    workspace: str
    unit: str
    outcome: str
    at: str
    turns: int | None
    cost_usd: float | None


class RowView(TypedDict):
    """What `row_for` gives an agent, to be read and never written: changing one widens what its
    runs may be granted. `tools` are catalog names (`coscc/kernel.py`)."""

    tools: list[str]
    submits: bool
    warning: str


class AgentRow(TypedDict):
    key: str
    glyph: str
    name: str
    meaning: str
    role: str
    # Per identity field, `default` or `override`.
    identity_source: dict[str, str]
    config: models.ConfigRow
    # The `:novel` rows of this agent's stage, edited in its drawer.
    variants: list[models.ConfigRow]
    # The skills its row names, joined.
    skill: str
    row: RowView
    last: RunView | None
    # The last `RECENT`, newest first.
    runs: list[RunView]
    runs_30d: int
    cost_30d: float
    chip: str


class AgentPage(TypedDict):
    rows: list[AgentRow]
    # The rows no state opens and no helper: `estimate` and `leif`.
    others: list[models.ConfigRow]
    problems: list[str]
    cos_model: str | None


def chip_of(last: RunView | None, budget: float | None, runs_in_window: int) -> str:
    """`failed` when the last run did not end `done`, `costly` when it spent `COSTLY_SHARE` of
    `budget` or more, `idle` with no run in the window, else `ok`."""
    if last is not None and last["outcome"] != "done":
        return "failed"
    cost = last["cost_usd"] if last is not None else None
    if budget and cost is not None and cost >= COSTLY_SHARE * budget:
        return "costly"
    if not runs_in_window:
        return "idle"
    return "ok"


def _row_view(key: str) -> RowView:
    row = policy.row_for(key)
    return RowView(tools=list(row.tools), submits=row.submits, warning=row.warning)


def _run_view(record: dict[str, Any]) -> RunView:
    cost = record.get("cost_usd")
    turns = record.get("turns")
    return RunView(
        workspace=str(record.get("workspace") or ""),
        unit=str(record.get("unit") or ""),
        outcome=str(record.get("outcome") or ""),
        at=str(record.get("at") or ""),
        turns=turns if isinstance(turns, int) else None,
        cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
    )


class Agents:
    def __init__(self, config: Config, ws: Workspaces) -> None:
        self.config = config
        self.ws = ws

    def agent(self, key: str) -> dict[str, Any] | None:
        """The resolved identity of `key`, or `None`. Every place in the service that shows or
        writes an agent's name asks this. Never raises on bad data."""
        return agents.agent_for(key)

    def _ends(self, workspace: str | None) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
        """Every `end` record by stage, newest first, from one read of the run log, each with
        the `label` of the `start` it closes, so a run is measured against its own ceiling."""
        journal = self.ws.journal()
        if journal is None:
            return {}, []
        try:
            records = journal.records(workspace, kinds=("start", "end"))
        except (Unusable, Busy, sqlite3.Error, OSError) as e:
            return {}, [f"the run log could not be read, so no run is shown: {e}"]
        step_labels: dict[tuple[str, str, str], Any] = {}
        ends: list[dict[str, Any]] = []
        for record in records:
            step = (str(record.get("workspace")), str(record.get("unit")), str(record.get("stage")))
            if record.get("kind") == "start":
                step_labels[step] = record.get("label")
            else:
                ends.append({**record, "label": step_labels.pop(step, None)})
        by_stage: dict[str, list[dict[str, Any]]] = {}
        for record in reversed(ends):
            by_stage.setdefault(str(record.get("stage") or ""), []).append(record)
        return by_stage, []

    def agent_page(self, workspace: str | None = None, now: datetime | None = None) -> AgentPage:
        """Everything the Agents page shows, in one call: the run log is read once, not per stage.

        `rows`, one per agent, failed and costly first and otherwise in the pack's order:
        identity, model, effort and the two ceilings each with its source, its `:novel` rows
        under `variants`, the row (read only), the skill, the last run, the last `RECENT`,
        the cost and count of the last `WINDOW_DAYS`, and the chip. `others`: the rows an
        engine opens other than Gebo (`estimate`, `leif`). `problems`: every row or record that
        cannot be used.

        `workspace` is a run-log key; `None` adds up every workspace of the working folder.
        """
        now = now or datetime.now(timezone.utc)
        since = (now - timedelta(days=WINDOW_DAYS)).isoformat(timespec="seconds")
        identity = agents.table()
        ends, bad_runs = self._ends(workspace)

        rows: list[AgentRow] = []
        for who in identity["rows"]:
            key = str(who["key"])
            mine = ends.get(key, [])
            recent = [r for r in mine if str(r.get("at") or "") >= since]
            last = _run_view(mine[0]) if mine else None
            config_row = models.config_row(key, self.config.model)
            variants = [models.config_row(k, self.config.model) for k in models.variants_of(key)]
            # A `novel` run is held to the `:novel` row's dollar ceiling where it has one.
            ran_under = next(
                (
                    v["ceilings"]["max_budget_usd"]
                    for v in variants
                    if mine and mine[0].get("label") == policy.NOVEL
                    if v["ceilings"]["max_budget_usd"]
                ),
                config_row["ceilings"]["max_budget_usd"],
            )
            cost = sum(
                float(r["cost_usd"]) for r in recent if isinstance(r.get("cost_usd"), (int, float))
            )
            rows.append(
                AgentRow(
                    key=key,
                    glyph=str(who["glyph"]),
                    name=str(who["name"]),
                    meaning=str(who["meaning"]),
                    role=str(who["role"]),
                    identity_source=dict(who["source"]),
                    config=config_row,
                    variants=variants,
                    skill=", ".join((pack.row(key) or {}).get("skills") or []),
                    row=_row_view(key),
                    last=last,
                    runs=[_run_view(r) for r in mine[:RECENT]],
                    runs_30d=len(recent),
                    cost_30d=round(cost, 6),
                    chip=chip_of(last, ran_under, len(recent)),
                )
            )
        rows.sort(key=lambda r: r["chip"] not in ATTENTION)
        others = [
            k
            for k, r in pack.rows().items()
            if not agents.shown(k) and (r.get("output") or {}).get("kind") != "helper"
        ]
        return AgentPage(
            rows=rows,
            others=[models.config_row(k, self.config.model) for k in others],
            problems=identity["problems"] + bad_runs,
            cos_model=self.config.model,
        )

    def set_agent_field(
        self, key: object, field: object, value: object = None, workspace: str | None = None
    ) -> AgentPage:
        """Save one field of one row into the owner's layer (`pack.write`), or put the built-in's
        back when `value` is `None`, then log it and return `agent_page`. A value out of bounds,
        or a row that would not pass `pack.check`, is refused and nothing is written.

        `field` is one of `agents.FIELDS` (an agent's identity; `""` resets too) or of
        `models.FIELDS` (model, effort, turns, budget; `key` may be `<row>:novel`; an effort of
        `""` resets too).

        **Behind the password like every route here**: whoever holds it or a live session can
        raise any agent's budget to `pack.BUDGET_MAX` a step, and the autopilot runs with it. The
        trace is the `agent-setting` record and each run's `row_hash` and `edited`.
        """
        if not isinstance(key, str) or not key:
            raise Invalid("key is required")
        field = str(field)
        if field not in (*agents.FIELDS, *models.FIELDS):
            known = (*agents.FIELDS, *models.FIELDS)
            raise Invalid(f"no such field: {field} (use one of {', '.join(known)})")
        reason = ""
        if value == "" or value is None:
            value = None
        elif field in agents.FIELDS:
            reason = agents.check_field(field, value)
        else:
            value, reason = models.check(field, value)
        if reason:
            raise Invalid(reason)
        try:
            if field in agents.FIELDS:
                if pack.row(key) is None:
                    raise Invalid(f"no such agent: {key} (use one of {', '.join(pack.rows())})")
                old, new = pack.write(key, agents.WHERE[field], value)
            else:
                old, new = models.set_field(key, field, value)
        except ValueError as e:
            raise Invalid(str(e)) from e
        except OSError as e:
            raise Invalid(
                f"the owner's layer could not be written, so nothing was saved: {e}"
            ) from e
        journal = self.ws.journal()
        if journal is not None:
            try:
                journal.append(
                    {
                        "kind": SETTING_KIND,
                        "workspace": "",
                        "unit": "",
                        "stage": "",
                        "agent": key,
                        "field": field,
                        "old": old,
                        "new": new,
                        "by": OWNER,
                    }
                )
            except (BadRecord, Busy) as e:
                raise Invalid(f"the setting was saved but not logged: {e}") from e
        return self.agent_page(workspace)


class Models:
    def __init__(self, config: Config, ws: Workspaces) -> None:
        self.config = config
        self.ws = ws

    def agent(
        self,
        key: str,
        row: policy.Row,
        model: str | None = None,
        effort: str | None = None,
    ) -> run_mod.Agent:
        """The `run` agent of a session no stage runs: the estimate, a feature's session (whose own
        `model` and `effort` stand where the pack has no row) and Leif, on the row's own ceilings
        and with its body as the system prompt."""
        model, model_source, effort, effort_source = models.resolve(
            key, None, self.config.model, own={"id": model, "effort": effort}
        )
        return run_mod.Agent(
            key,
            row,
            model=model,
            effort=effort,
            sources={
                "model_source": model_source,
                "effort_source": effort_source,
                "max_turns_source": models.DEFAULT,
                "max_budget_source": models.DEFAULT if row.max_budget_usd else models.NONE,
            },
            system=str((pack.row(key) or {}).get(pack.BODY) or ""),
        )

    def config_for(self, name: str) -> tuple[str | None, str, str | None, str]:
        """`(model, model_source, effort, effort_source)` of a row run with no label: Gebo's
        `integrate`."""
        return models.resolve(name, None, self.config.model)

    def stage_config(
        self,
        stage: str,
        stages: list[str],
        plan: Plan | None,
        journal: Journal,
        key: str,
        unit: str,
    ) -> dict[str, Any]:
        """The label a step runs under, the model and effort it resolves to, and for `impl`
        which run of the unit's this is. Called after the gate, before any money is spent.
        The label chooses a configuration and nothing else.

        `Busy` from the run log is left to the caller, as `failed_attempts` is.

        On a routine `impl`, with no flag, the unit's arm names the model handed to `resolve`,
        and `trial_record` says which arm and what was asked for; the model the session really
        ran is filled in once its `init` names it. Any other step has no `trial_record` key.
        """
        history = [r for r in journal.records(key, unit) if r.get("stage") == "impl"]
        label_declared, label, label_source = models.label_of(stage, stages, plan)
        arm = modeltrial.arm(unit, stage) if modeltrial.applies(stage, label) else None
        trial_model = modeltrial.model_for(stage, label, arm) if arm else None
        model, model_source, effort, effort_source = models.resolve(
            stage, label, self.config.model, trial_model
        )
        trial_record = (
            {"trial_record": {modeltrial.FIELD: {"arm": arm, "requested": model}}} if arm else {}
        )
        return {
            **trial_record,
            "model": model,
            "model_source": model_source,
            "effort": effort,
            "effort_source": effort_source,
            "label_declared": label_declared,
            "label": label,
            "label_source": label_source,
            # Every `start` of `impl` counts, the review-driven fixes included; a raise's does not.
            "impl_run": (
                sum(1 for r in history if r.get("kind") == "start" and "continues" not in r) + 1
                if stage == "impl"
                else None
            ),
        }

    async def ci_red(self, cwd: str, unit: str, repo: str) -> bool | None:
        """Whether `coscc.loop next` sends `unit` back to `impl` because CI is red, read with
        `decide.is_ci_red`; `None` when it could not be asked. Never raises."""
        try:
            found = await board_reader.next_step(
                self.ws.units_root(cwd), unit, repo=repo, state=self.ws.snapshot(cwd, [unit])
            )
            return decide.is_ci_red(found)
        except Exception:
            # Recorded as null.
            log.exception("whether the CI of %s is red could not be read", unit)
            return None
