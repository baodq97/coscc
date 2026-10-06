"""The agents: who each is, what it runs on, what it may do and how its runs went.

The rows are `coscc/agent/pack.py`'s, resolved by `coscc/agent/agents.py` (who) and
`coscc/agent/models.py` (model, effort and the two ceilings); this is where a field the page
sets is written into the owner's layer and logged, and where the run log's `end` records are
added up and grouped by the definition they ran for the Agents page. `Models` gathers the inputs for which model and effort each run
gets.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from collections.abc import Callable
from typing import Any, Literal, TypedDict

from coscc.agent import agents, models, modeltrial, pack, policy
from coscc.config import Config
from coscc.kernel import OWNER, Hooks, Invalid
from coscc.leif import decide
from coscc.runner import run as run_mod
from coscc.store.db import Busy, Unusable
from coscc.store.journal import BadRecord, Journal
from coscc.units import board as board_reader
from coscc.units import contracts
from coscc.units.contracts import Plan
from coscc.units.workspaces import Workspaces

log = logging.getLogger(__name__)

# The `runs` kind of one saved or reset field: the trace of who moved what.
SETTING_KIND = "agent-setting"
# How far back the page adds up cost and lists runs.
WINDOW_DAYS = 30
# A last run that spent this share of its budget or more is `costly`. Chosen, not measured.
COSTLY_SHARE = 0.8
# The chips, worst first: a row with one of `ATTENTION` is listed before the rest.
CHIPS = ("failed", "costly", "idle", "ok")
ATTENTION = ("failed", "costly")


class RunView(TypedDict):
    """One `end` record of an agent, as the page shows it; `workspace` is the run-log key, the
    workspace's resolved path; `row_hash` the definition its `start` ran (`""` before rows had
    one)."""

    workspace: str
    unit: str
    outcome: str
    at: str
    turns: int | None
    cost_usd: float | None
    row_hash: str


class Setting(TypedDict):
    """One `agent-setting` record: a field the owner saved or reset."""

    at: str
    field: str
    old: Any
    new: Any
    by: str


class RunGroup(TypedDict):
    """The runs of one definition of an agent (`row_hash`) in a row, newest first, headed by the
    settings saved since the group before it; a group with no run yet is the edits waiting for the
    next run."""

    row_hash: str
    settings: list[Setting]
    runs: list[RunView]
    cost_usd: float
    turns: int


class CatalogTool(TypedDict):
    """One tool a row may name (`kernel.Hooks.catalog`): what it does, how much a wrong call
    costs, its MCP server and feature (`""` for Claude Code's own), and whether that feature is on
    in the workspace asked about."""

    name: str
    effect: str
    tier: str
    server: str
    feature: str
    on: bool


class SkillText(TypedDict):
    """A skill a row names: the text a run is given, the built-in's, whether the owner's differs."""

    name: str
    text: str
    builtin: str
    edited: bool


class RowFields(TypedDict, total=False):
    """A row's frontmatter and body (`pack.KEYS`, `pack.BODY`); a value of the wrong type (a
    hand-edited file) is left out, and the row's `problems` say why."""

    name: str
    glyph: str
    description: str
    model: dict[str, Any]
    variants: dict[str, Any]
    skills: list[str]
    tools: dict[str, str]
    helpers: list[str]
    input: dict[str, Any]
    output: dict[str, Any]
    trigger: dict[str, str]
    ceilings: dict[str, Any]
    warning: str
    consequence: str
    body: str


Group = Literal["stage", "engine", "helper", "feature"]


class AgentRow(TypedDict):
    key: str
    # Opened on a unit's state, by the engine (Gebo, the estimate, Leif), as another's helper, or by
    # a feature (its own session, shown and not edited here).
    group: Group
    row: RowFields
    builtin: RowFields
    # The keys the owner's layer sets (`skill:<name>` for a skill's text).
    edited: list[str]
    # Why the row cannot run now; its runs are refused `agent-invalid` while there is one.
    problems: list[str]
    editable: bool
    skills: list[SkillText]
    # What a run of the row as it stands records (`pack.hash_of`).
    row_hash: str
    # What a run gets, resolved; `novel` under its `novel` variant, where it has one.
    config: models.ConfigRow
    novel: models.ConfigRow | None
    last: RunView | None
    runs_30d: int
    cost_30d: float
    chip: str
    # The last `WINDOW_DAYS`, newest first.
    groups: list[RunGroup]


class AgentPage(TypedDict):
    rows: list[AgentRow]
    catalog: list[CatalogTool]
    problems: list[str]
    cos_model: str | None
    # Whose runs the counts and costs add up: `workspace` (the page has a `cwd`) or `all`.
    scope: Literal["workspace", "all"]


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
        row_hash=str(record.get("row_hash") or ""),
    )


def groups_of(runs: list[RunView], settings: list[Setting]) -> list[RunGroup]:
    """`runs` and `settings` (each oldest first) as `RunGroup`s, newest first: a run on another
    `row_hash` than the one before it, or after a setting, opens a group headed by the settings
    saved since."""
    out: list[RunGroup] = []
    waiting = list(settings)
    for run in runs:
        since = [s for s in waiting if s["at"] <= run["at"]]
        waiting = waiting[len(since) :]
        if since or not out or out[-1]["row_hash"] != run["row_hash"]:
            out.append(
                RunGroup(row_hash=run["row_hash"], settings=since, runs=[], cost_usd=0.0, turns=0)
            )
        group = out[-1]
        group["runs"].insert(0, run)
        group["cost_usd"] = round(group["cost_usd"] + (run["cost_usd"] or 0.0), 6)
        group["turns"] += run["turns"] or 0
    if waiting:
        out.append(RunGroup(row_hash="", settings=waiting, runs=[], cost_usd=0.0, turns=0))
    return out[::-1]


_TYPES: dict[str, type | tuple[type, ...]] = {
    "model": dict,
    "variants": dict,
    "tools": dict,
    "input": dict,
    "output": dict,
    "trigger": dict,
    "ceilings": dict,
    "skills": list,
    "helpers": list,
}


def _fields_of(found: dict[str, Any]) -> RowFields:
    """`found`'s frontmatter and body, each value of the type `RowFields` gives it."""
    out: dict[str, Any] = {}
    for k in (*pack.KEYS, pack.BODY):
        if k in found and isinstance(found[k], _TYPES.get(k, str)):
            out[k] = found[k]
    return RowFields(**out)


def _group_of(found: dict[str, Any]) -> Group:
    """Read from the built-in row: an edit moves no agent between groups."""
    base = found.get("builtin") or found
    if (base.get("output") or {}).get("kind") == "helper":
        return "helper"
    return "stage" if "state" in (base.get("trigger") or {}) else "engine"


def _skills(found: dict[str, Any]) -> list[SkillText]:
    out = []
    for name in found.get("skills") or []:
        builtin = pack.BUILTIN / "skills" / name / pack.SKILL_FILE
        base = builtin.read_text(encoding="utf-8") if builtin.is_file() else ""
        try:
            text = pack.skill(name)
        except LookupError:
            text = ""
        out.append(SkillText(name=name, text=text, builtin=base, edited=text != base))
    return out


class Agents:
    def __init__(self, config: Config, ws: Workspaces, hooks: Callable[[], Hooks] = Hooks) -> None:
        self.config = config
        self.ws = ws
        # The app's catalog, asked when the page is read or a field saved: set once the features
        # are built, after this.
        self.hooks = hooks

    def agent(self, key: str) -> dict[str, Any] | None:
        """The resolved identity of `key`, or `None`. Every place in the service that shows or
        writes an agent's name asks this. Never raises on bad data."""
        return agents.agent_for(key)

    def catalog(self, cwd: str = "") -> list[CatalogTool]:
        """Every tool a row may name, with whether its feature is on for `cwd` (`""`: the
        features' own default)."""
        hooks = self.hooks()
        feature = {t.name: f for f, p in hooks.parts for t in p.tools}
        return [
            CatalogTool(
                name=t.name,
                effect=t.effect,
                tier=t.tier,
                server=t.server,
                feature=feature.get(t.name, ""),
                on=t.name not in feature or hooks.enabled(feature[t.name], cwd),
            )
            for t in hooks.catalog().values()
            if t.name not in pack.ENGINE_TOOLS
        ]

    def _effects(self) -> dict[str, str] | None:
        """Each catalog tool's effect, for `pack.check`; `None` for a core built with no feature
        (a test's), whose catalog lacks the tools the built-in rows name."""
        hooks = self.hooks()
        return {n: t.effect for n, t in hooks.catalog().items()} if hooks.parts else None

    def _records(
        self, workspace: str | None
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[Setting]], list[str]]:
        """Every `end` by agent, oldest first, each with the `label` and `row_hash` of the `start`
        it closes (a run is measured against its own ceiling, grouped by its own definition); every
        `agent-setting` by agent, oldest first. One read of the run log."""
        journal = self.ws.journal()
        if journal is None:
            return {}, {}, []
        try:
            records = journal.records(None, kinds=("start", "end", SETTING_KIND))
        except (Unusable, Busy, sqlite3.Error, OSError) as e:
            return {}, {}, [f"the run log could not be read, so no run is shown: {e}"]
        starts: dict[tuple[str, str, str], dict[str, Any]] = {}
        ends: dict[str, list[dict[str, Any]]] = {}
        settings: dict[str, list[Setting]] = {}
        for record in records:
            if record.get("kind") == SETTING_KIND:
                settings.setdefault(str(record.get("agent") or ""), []).append(
                    Setting(
                        at=str(record.get("at") or ""),
                        field=str(record.get("field") or ""),
                        old=record.get("old"),
                        new=record.get("new"),
                        by=str(record.get("by") or ""),
                    )
                )
                continue
            if workspace is not None and record.get("workspace") != workspace:
                continue
            step = (str(record.get("workspace")), str(record.get("unit")), str(record.get("stage")))
            if record.get("kind") == "start":
                starts[step] = record
            else:
                start = starts.pop(step, {})
                ends.setdefault(step[2], []).append(
                    {**record, "label": start.get("label"), "row_hash": start.get("row_hash")}
                )
        return ends, settings, []

    def agent_page(
        self, workspace: str | None = None, now: datetime | None = None, cwd: str = ""
    ) -> AgentPage:
        """Everything the Agents page shows, in one call: the run log is read once.

        `rows`, one per agent in the pack's order, then the features' own sessions: its row as it
        stands and as built in, the keys the owner set, its problems, its skills' texts, its hash,
        what a run gets (resolved), the last run, the cost and count of the last `WINDOW_DAYS`, the
        chip and the runs of that window grouped by definition. `catalog`: every tool a row may
        name, its feature on or off for `cwd`. `problems`: every row or record that cannot be used.

        `workspace` is a run-log key; `None` adds up every workspace of the working folder.
        """
        now = now or datetime.now(timezone.utc)
        since = (now - timedelta(days=WINDOW_DAYS)).isoformat(timespec="seconds")
        ends, settings, bad_runs = self._records(workspace)
        effects = self._effects()
        rows: list[AgentRow] = []
        for key, found in pack.rows().items():
            config = models.config_row(key, self.config.model)
            variants = found.get("variants")
            has_novel = isinstance(variants, dict) and policy.NOVEL in variants
            novel = models.config_row(key, self.config.model, policy.NOVEL) if has_novel else None
            rows.append(
                self._row(key, found, _group_of(found), config, novel, ends, settings, since)
            )
            rows[-1]["problems"] = pack.problems(key, effects)
        for key, added in policy.ADDED.items():
            found = {
                "name": key,
                "tools": {t: "allow" for t in added.tools},
                "ceilings": {"turns": added.max_turns, "usd": added.max_budget_usd},
            }
            config = models.config_row(key, self.config.model)
            rows.append(self._row(key, found, "feature", config, None, ends, settings, since))
        table = agents.table()
        return AgentPage(
            rows=rows,
            catalog=self.catalog(cwd),
            problems=table["problems"] + bad_runs,
            cos_model=self.config.model,
            scope="all" if workspace is None else "workspace",
        )

    def _row(
        self,
        key: str,
        found: dict[str, Any],
        group: Group,
        config: models.ConfigRow,
        novel: models.ConfigRow | None,
        ends: dict[str, list[dict[str, Any]]],
        settings: dict[str, list[Setting]],
        since: str,
    ) -> AgentRow:
        mine = ends.get(key, [])
        recent = [r for r in mine if str(r.get("at") or "") >= since]
        last = _run_view(mine[-1]) if mine else None
        budget = config["ceilings"]["max_budget_usd"]
        if mine and mine[-1].get("label") == policy.NOVEL and novel is not None:
            budget = novel["ceilings"]["max_budget_usd"] or budget
        views = [_run_view(r) for r in recent]
        pack_row = group != "feature"
        return AgentRow(
            key=key,
            group=group,
            row=_fields_of(found),
            builtin=_fields_of(found.get("builtin") or found),
            edited=list(found.get("edited") or []),
            problems=[],
            editable=pack_row,
            skills=_skills(found) if pack_row else [],
            row_hash=pack.hash_of(found) if pack_row else "",
            config=config,
            novel=novel,
            last=last,
            runs_30d=len(recent),
            cost_30d=round(sum(r["cost_usd"] or 0.0 for r in views), 6),
            chip=chip_of(last, budget, len(recent)),
            groups=groups_of(views, [s for s in settings.get(key, []) if s["at"] >= since]),
        )

    def set_agent_field(
        self, key: object, field: object, value: object = None, cwd: str = ""
    ) -> AgentPage:
        """Save one field of one row into the owner's layer (`pack.write`), or put the built-in's
        back when `value` is `None` (or `""`), then log it and return `agent_page`.

        `field` is a frontmatter key (`pack.KEYS`), `body`, or `skill:<name>`, its value the whole
        key as the row file holds it. The row as it would then stand must pass `pack.check` with
        the app's catalog, its `input` and `output` `contracts`; else a 400 naming every reason and
        nothing is written. `trigger` is shown and not saved: a step still runs the row named after
        its stage.

        **Behind the password like every route here**: whoever holds it or a live session can give
        any agent another model, larger ceilings, another prompt or more of the catalog's tools,
        inside the critical calls (`policy.critical`), and the autopilot runs with it. The trace is
        the `agent-setting` record and each run's `row_hash` and `edited`.
        """
        if not isinstance(key, str) or pack.row(key) is None:
            raise Invalid(f"no such agent: {key} (use one of {', '.join(pack.rows())})")
        field = str(field)
        if field == "trigger":
            raise Invalid("trigger is read-only: a step still runs the agent named after its stage")
        if value == "":
            value = None
        try:
            if value is not None and field == "input":
                contracts.check_input(key, value, pack.rows())
            if (
                isinstance(value, dict)
                and field == "output"
                and value.get("kind") in contracts.KINDS
            ):
                contracts.check(key, value)
            old, new = pack.write(key, field, value, self._effects())
        except (ValueError, contracts.ContractError) as e:
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
        return self.agent_page(self.ws.key(cwd) if cwd else None, cwd=cwd)


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
