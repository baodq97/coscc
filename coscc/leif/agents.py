"""The agents: who each is, what it runs on, what it may do and how its runs went.

The rows are `coscc/agent/pack.py`'s, resolved by `coscc/agent/agents.py` (who) and
`coscc/agent/models.py` (model, effort and the two ceilings); this is where a field the page
sets is written into the owner's layer and logged, and where the run log's `end` records are
added up and grouped by the definition they ran for the Agents page. `Models` gathers the inputs for which model and effort each run
gets.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from collections.abc import Callable
from typing import Any, Literal, TypedDict

from coscc import bus, vault
from coscc.agent import agents, models, modeltrial, pack, policy, skills
from coscc.config import Config
from coscc.kernel import OWNER, Hooks, Invalid
from coscc.leif import decide
from coscc.runner import run as run_mod
from coscc.runner import triggers
from coscc.store.db import Busy, Data, Unusable
from coscc.store.journal import BadRecord, Journal
from coscc.units import board as board_reader
from coscc.units import contracts, proposals, states
from coscc.units.contracts import Plan
from coscc.units.workspaces import Workspaces

log = logging.getLogger(__name__)

# The `runs` kind of one saved or reset field: the trace of who moved what.
SETTING_KIND = "agent-setting"
# The `runs` kind of a pack the owner imported, removed, or whose process they set.
PACK_KIND = "pack-setting"
# How far back the page adds up cost and lists runs.
WINDOW_DAYS = 30
# How long an agent's failed latest run is held up to the owner.
FAILED_DAYS = 3
# A last run that spent this share of its budget or more is `costly`. Chosen, not measured.
COSTLY_SHARE = 0.8
# The chips, worst first: a row with one of `ATTENTION` is listed before the rest.
CHIPS = ("failed", "paused", "costly", "stopped", "idle", "ok")
ATTENTION = ("failed", "paused", "costly")


class RunView(TypedDict):
    """One `end` record of an agent, as the page shows it; `workspace` is the run-log key, the
    workspace's resolved path; `row_hash` the definition its `start` ran (`""` before rows had
    one). `run` is the run-log id its events were kept under (`""` when none); `skipped` and
    `detail` say a run that spent nothing and why; `started_by` is who started it; `made` how many
    proposals it kept (`None` when it makes none); `session` whether it kept a session to ask;
    `verdict` what a grading run came to (`""` otherwise); `refused` the tool calls it was
    refused and `helpers` the helpers it started (`None` unless the page asked for this agent's
    runs); `shallow` that it was only partly checked: calls refused, or a verdict with an
    unclear criterion."""

    workspace: str
    unit: str
    outcome: str
    at: str
    turns: int | None
    cost_usd: float | None
    row_hash: str
    run: str
    skipped: bool
    detail: str
    started_by: str
    made: int | None
    session: bool
    verdict: str
    refused: int | None
    helpers: int | None
    shallow: bool


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
    """A skill a row names: the text a run is given, the built-in's (`""` unless the owner's
    differs), whether it does."""

    name: str
    text: str
    builtin: str
    edited: bool


class Schedule(TypedDict):
    hours: int


# `from` is a keyword: the agent whose done run starts the row (`pack.after_of`).
TriggerEvent = TypedDict(
    "TriggerEvent", {"name": str, "after_hours": int, "from": str}, total=False
)


class TriggerFields(TypedDict, total=False):
    """A row's `trigger` (`pack.TRIGGERS`), or `state` for a row a process state runs."""

    state: str
    engine: str
    event: TriggerEvent
    schedule: Schedule
    manual: bool
    leif: bool


class RowFields(TypedDict, total=False):
    """A row's frontmatter and body (`pack.KEYS`, `pack.BODY`); a value of the wrong type (a
    hand-edited file) is left out, and the row's `problems` say why."""

    name: str
    glyph: str
    description: str
    model: dict[str, Any]
    variants: dict[str, Any]
    skills: list[str]
    tools: dict[str, Any]
    helpers: list[str]
    input: dict[str, Any]
    output: dict[str, Any]
    trigger: TriggerFields
    default: str
    ceilings: dict[str, Any]
    warning: str
    consequence: str
    body: str


Group = Literal["stage", "engine", "helper", "triggered"]


class Running(TypedDict):
    """A run of an agent that this app holds now: its id (the run page follows it) and start."""

    run: str
    started: str


class LiveRun(Running):
    workspace: str
    agent: str
    name: str


class LiveProposal(TypedDict):
    id: int
    workspace: str
    agent: str
    agent_name: str
    type: str
    title: str
    at: str


class LiveFailed(TypedDict):
    workspace: str
    agent: str
    name: str
    run: str
    at: str
    detail: str


class Live(TypedDict):
    """What agents are doing across every listed workspace: the runs in flight, the proposals
    waiting for a person and the agents whose latest run of the last `FAILED_DAYS` days failed
    (workspaces by their names)."""

    running: list[LiveRun]
    proposals: list[LiveProposal]
    failed: list[LiveFailed]


@dataclass(frozen=True)
class _Now:
    """What one read of the page asks once for every row: the runs held, the workspaces by their
    run-log key, each row's newest on/off record, what was spent against the daily cap."""

    running: list[dict[str, str]]
    names: dict[str, str]
    states: dict[tuple[str, str], dict[str, Any]]
    cap: tuple[float, float] | None
    now: datetime


class AgentRow(TypedDict):
    key: str
    # The pack the row comes from: `coscc-sdlc`, `local` or an imported pack's name.
    pack: str
    # A whole row of the owner's own pack (`local`): they may delete it.
    own: bool
    # Opened on a unit's state, by the engine (Gebo, the estimate, Leif), as another's helper, or by
    # its own trigger (an event, a schedule, a press, Leif: `coscc/runner/triggers.py`).
    group: Group
    row: RowFields
    # The built-in's value of each part in `edited` and no other: any other part of `row` is
    # still the built-in's.
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
    # The last `WINDOW_DAYS`, newest first; empty unless the page asked for this agent's runs.
    groups: list[RunGroup]
    # Proposals the agent made in the window by what became of them, and the runs it skipped
    # (listed in `groups`, in neither `runs_30d` nor `cost_30d`).
    accepted_30d: int
    dismissed_30d: int
    pending: int
    skips_30d: int
    # Holds only reading tools and no `ask`: a trigger starts it (`pack.reads_only`).
    reads_only: bool
    # The app's data a run of it is handed: all of `contracts.DATA`, or what a triggered row's
    # prompt reads (`contracts.TRIGGERED_DATA`).
    usable_data: list[str]
    # Whether its event or schedule runs it in the workspace asked about; `None` for a row with
    # neither, or no workspace.
    on: bool | None
    # The run of it in the workspace asked about that is going now (any workspace for "all").
    running: Running | None
    # When its schedule or a due event next runs it there; `None` off, with neither, or unscheduled.
    next_at: str | None
    # The names of the workspaces where its event or schedule is on.
    on_in: list[str]
    # Why it is off in the workspace asked about: what the app or the owner logged; `""` when on.
    off_reason: str


class ProposingAgent(TypedDict):
    key: str
    name: str
    on: bool | None


class ProposalRow(proposals.Proposal):
    # The name of the agent that proposed it.
    agent_name: str


class ProposalsView(TypedDict):
    """Up next's Proposals: every agent's, newest first, and the agents that propose."""

    proposals: list[ProposalRow]
    agents: list[ProposingAgent]


class AgentPage(TypedDict):
    rows: list[AgentRow]
    catalog: list[CatalogTool]
    problems: list[str]
    cos_model: str | None
    # Whose runs the counts and costs add up: `workspace` (the page has a `cwd`) or `all`.
    scope: Literal["workspace", "all"]


def chip_of(last: RunView | None, budget: float | None, runs_in_window: int) -> str:
    """What the last run came to: `failed`, `paused` (it stopped at a ceiling and kept its
    session) or `stopped` (a person's Stop, or the app going down) when it did not end `done`;
    `costly` when it spent `COSTLY_SHARE` of `budget` or more, `idle` with no run in the window,
    else `ok`."""
    if last is not None and last["outcome"] != "done":
        return {"paused-budget": "paused", "cancelled": "stopped", "stopped": "stopped"}.get(
            last["outcome"], "failed"
        )
    cost = last["cost_usd"] if last is not None else None
    if budget and cost is not None and cost >= COSTLY_SHARE * budget:
        return "costly"
    if not runs_in_window:
        return "idle"
    return "ok"


def _run_view(record: dict[str, Any], counts: dict[str, tuple[int, int]] | None = None) -> RunView:
    cost = record.get("cost_usd")
    turns = record.get("turns")
    run = str(record.get("run") or "")
    refused, helpers = counts.get(run, (0, 0)) if counts is not None else (None, None)
    verdict = str(record.get("verdict") or "")
    return RunView(
        workspace=str(record.get("workspace") or ""),
        unit=str(record.get("unit") or ""),
        outcome=str(record.get("outcome") or ""),
        at=str(record.get("at") or ""),
        turns=turns if isinstance(turns, int) else None,
        cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
        row_hash=str(record.get("row_hash") or ""),
        run=str(record.get("run") or ""),
        skipped=bool(record.get("skipped")),
        detail=str(record.get("detail") or ""),
        started_by=str(record.get("started_by") or ""),
        made=made if isinstance(made := record.get("proposals"), int) else None,
        session=bool(record.get("session_id")),
        verdict=verdict,
        refused=refused,
        helpers=helpers,
        shallow=bool(refused) or verdict == "unclear",
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
    """`found`'s frontmatter and body, each value of the type `RowFields` gives it. A row a process
    state runs shows where as its trigger, `{state: "impl in full, short"}`: the process binds
    it, the row names no state."""
    out: dict[str, Any] = {}
    for k in (*pack.KEYS, pack.BODY):
        if k in found and isinstance(found[k], _TYPES.get(k, str)):
            out[k] = found[k]
    where = pack.states_of(str(found.get("key") or ""))
    if where and "trigger" not in out:
        out["trigger"] = {"state": where}
    return RowFields(**out)


def _group_of(found: dict[str, Any]) -> Group:
    """Read from the built-in row: an edit moves no agent between groups."""
    base = found.get("builtin") or found
    if (base.get("output") or {}).get("kind") == "helper":
        return "helper"
    if pack.states_of(str(found.get("key") or "")):
        return "stage"
    return "triggered" if pack.triggered(base) else "engine"


def _skills(found: dict[str, Any]) -> list[SkillText]:
    out = []
    for name in found.get("skills") or []:
        base = pack.skill_base(name)
        try:
            text = pack.skill(name)
        except LookupError:
            text = ""
        edited = text != base
        out.append(SkillText(name=name, text=text, builtin=base if edited else "", edited=edited))
    return out


def _off_reason(said: dict[str, Any], mine: list[dict[str, Any]]) -> str:
    """Why the app or the owner turned a row off, from what the run log recorded: a pause at a
    ceiling is read from the run's own outcome, not from the words logged beside it."""
    if said.get("by") != OWNER and mine and mine[-1].get("outcome") == "paused-budget":
        return "its last run stopped at its ceiling"
    return str(said.get("reason") or "") or (
        "turned off by you" if said.get("by") == OWNER else "turned off"
    )


class Agents:
    def __init__(
        self,
        config: Config,
        ws: Workspaces,
        hooks: Callable[[], Hooks] = Hooks,
        spent: Callable[[str], tuple[float, float] | None] = lambda _cwd: None,
    ) -> None:
        self.config = config
        self.ws = ws
        # What was spent today and the daily cap for a workspace (`Autopilot.today`).
        self.spent = spent
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

    def catalog_block(self, cwd: str = "") -> str:
        """Everything a row or a process may be composed from, as one JSON text: the `catalog`
        data source (`contracts.DATA`) Dagaz reads. The tools (`catalog`), the data sources, the
        output kinds with the fields the engine reads of each, the triggers and the bus events a
        row may wait on, the process guards and actions, the bounds a row keeps, the skills, every
        row (its parts a composition names) and every process. Read only."""
        rows = pack.rows()
        events = {}
        for name in bus.NAMES:
            fields = sorted(bus.fields_of(name))
            if "workspace" in fields:
                events[name] = fields
        said = {
            "tools": [
                {"name": t["name"], "effect": t["effect"], "tier": t["tier"], "on": t["on"]}
                for t in self.catalog(cwd)
            ],
            "data": list(contracts.DATA),
            "outputs": {
                k: {f: t for f, (_, t) in contracts.READS.get(k, {}).items()}
                for k in pack.OUTPUT_KINDS
                if k not in ("helper", "draft")
            },
            "triggers": [t for t in pack.TRIGGERS if t != "engine"],
            "sandbox": (
                'A triggered row may hold Bash as {"Bash": {"sandbox": {"network": '
                '["127.0.0.1:<port>"]}}}: its commands run in an OS sandbox that writes only its '
                "scratch folder, reads none of the app's secrets and reaches only those loopback "
                "hosts (127.0.0.1, localhost or [::1], each with its port)."
            ),
            "events": events,
            "chain": (
                'A row runs after another agent with {"event": {"name": "agent-run.ended", '
                '"from": "<that agent\'s key>"}} and "default": "off": that agent\'s done run '
                f"starts it, its result in the prompt; at most {pack.CHAIN_MAX} agents after the "
                "first, never in a circle. agent-run.started starts nothing."
            ),
            "guards": list(pack.PROCESS_GUARDS),
            "actions": list(pack.ACTIONS),
            "bounds": {
                "efforts": list(pack.EFFORTS),
                "turns": [pack.TURNS_MIN, pack.TURNS_MAX],
                "usd": [pack.BUDGET_MIN, pack.BUDGET_MAX],
                "name_max": pack.NAME_MAX,
                "names_reserved": sorted(pack.RESERVED_NAMES),
                "key_max": pack.KEY_MAX,
            },
            "skills": sorted({s for r in rows.values() for s in r.get("skills") or []}),
            "rows": [
                {
                    "key": k,
                    "pack": r.get("pack"),
                    **{
                        f: r[f]
                        for f in (
                            "name",
                            "description",
                            "model",
                            "input",
                            "output",
                            "trigger",
                            "tools",
                        )
                        if f in r
                    },
                }
                for k, r in rows.items()
                if not r.get("problems")
            ],
            "processes": pack.processes(),
        }
        return json.dumps(said, ensure_ascii=False, indent=1)

    def _effects(self) -> dict[str, str] | None:
        """Each catalog tool's effect, for `pack.check`; `None` for a core built with no feature
        (a test's), whose catalog lacks the tools the built-in rows name."""
        hooks = self.hooks()
        return triggers.effects(hooks) if hooks.parts else None

    def _records(
        self, workspace: str | None
    ) -> tuple[
        dict[str, list[dict[str, Any]]],
        dict[str, list[Setting]],
        dict[tuple[str, str], dict[str, Any]],
        list[str],
    ]:
        """Every `end` by its `agent` (a chat turn is Leif's, a question to a run is the asked
        agent's), else by its stage, oldest first, each with the `label` and `row_hash` of the `start`
        it closes (a run is measured against its own ceiling, grouped by its own definition); every
        `agent-setting` by agent, oldest first; the newest `agent-state` of each (workspace, agent).
        One read of the run log."""
        journal = self.ws.journal()
        if journal is None:
            return {}, {}, {}, []
        try:
            records = journal.records(
                None, kinds=("start", "end", SETTING_KIND, triggers.STATE_KIND)
            )
        except (Unusable, Busy, sqlite3.Error, OSError) as e:
            return {}, {}, {}, [f"the run log could not be read, so no run is shown: {e}"]
        starts: dict[tuple[str, str, str], dict[str, Any]] = {}
        ends: dict[str, list[dict[str, Any]]] = {}
        settings: dict[str, list[Setting]] = {}
        onoff: dict[tuple[str, str], dict[str, Any]] = {}
        for record in records:
            if record.get("kind") == triggers.STATE_KIND:
                onoff[str(record.get("workspace")), str(record.get("agent"))] = record
                continue
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
            elif step[2] == triggers.TRIAL:
                # A trial ran a row before it was saved: no run of any agent, saved later or not.
                starts.pop(step, None)
            else:
                start = starts.pop(step, {})
                ends.setdefault(str(record.get("agent") or step[2]), []).append(
                    {
                        "started_by": start.get("started_by"),
                        **record,
                        "label": start.get("label"),
                        "row_hash": start.get("row_hash"),
                    }
                )
        return ends, settings, onoff, []

    @staticmethod
    def _counts(data: Data, runs: list[str]) -> dict[str, tuple[int, int]]:
        """For each run: the tool calls it was refused and the helpers it started."""
        kept = data.step_event_counts(runs, ("denied", "worker_start"))
        return {r: (k.get("denied", 0), k.get("worker_start", 0)) for r, k in kept.items()}

    def agent_page(
        self,
        workspace: str | None = None,
        now: datetime | None = None,
        cwd: str = "",
        agent: str = "",
    ) -> AgentPage:
        """Everything the Agents page shows, in one call: the run log is read once.

        `rows`, one per agent in the pack's order: its row as it stands and as built in, the keys the owner set, its problems, its skills' texts, its hash,
        what a run gets (resolved), the last run, the cost and count of the last `WINDOW_DAYS`, the
        chip and the runs of that window grouped by definition. `catalog`: every tool a row may
        name, its feature on or off for `cwd`. `problems`: every row or record that cannot be used.

        `workspace` is a run-log key; `None` adds up every workspace of the working folder.
        Only `agent`'s row holds its runs (`groups`, with what each was refused and which helpers
        it started); the rest hold the counts, so the page stays small.
        """
        now = now or datetime.now(timezone.utc)
        since = (now - timedelta(days=WINDOW_DAYS)).isoformat(timespec="seconds")
        ends, settings, onoff, bad_runs = self._records(workspace)
        effects = self._effects()
        data = Data(self.config.data_dir)
        rows: list[AgentRow] = []
        made = proposals.counts(data, workspace, since)
        by = _Now(
            running=triggers.running(),
            names={
                self.ws.key(w["path"]): w["name"]
                for w in self.ws.all()["workspaces"]
                if not w["missing"]
            },
            states=onoff,
            cap=self._cap(cwd) if cwd and workspace else None,
            now=now,
        )
        for key, found in pack.rows().items():
            config = models.config_row(key, self.config.model)
            variants = found.get("variants")
            has_novel = isinstance(variants, dict) and policy.NOVEL in variants
            novel = models.config_row(key, self.config.model, policy.NOVEL) if has_novel else None
            counts = None
            if key == agent:
                ids = [str(r["run"]) for r in ends.get(key, []) if r.get("run")]
                counts = self._counts(data, ids)
            rows.append(
                self._row(
                    key,
                    found,
                    _group_of(found),
                    config,
                    novel,
                    ends,
                    settings,
                    since,
                    made.get(key, {}),
                    counts,
                )
            )
            rows[-1]["problems"] = pack.problems(key, effects)
            rows[-1]["on"] = self._on(data, key, workspace)
            runs = [r for r in ends.get(key, []) if r.get("stage") != "ask"]
            self._live_fields(rows[-1], found, data, workspace, runs, by)
        table = agents.table()
        return AgentPage(
            rows=rows,
            catalog=self.catalog(cwd),
            problems=table["problems"] + bad_runs,
            cos_model=self.config.model,
            scope="all" if workspace is None else "workspace",
        )

    def _cap(self, cwd: str) -> tuple[float, float] | None:
        try:
            return self.spent(cwd)
        except Invalid:
            return None

    def _live_fields(
        self,
        row: AgentRow,
        found: dict[str, Any],
        data: Data,
        workspace: str | None,
        mine: list[dict[str, Any]],
        by: "_Now",
    ) -> None:
        """`running`, `next_at`, `on_in` and `off_reason` of `row`."""
        for held in by.running:
            if held["agent"] == row["key"] and workspace in (None, held["workspace"]):
                row["running"] = Running(run=held["run"], started=held["started"])
        if not (pack.triggered(found, "event") or pack.triggered(found, "schedule")):
            return
        key = row["key"]
        row["on_in"] = [n for k, n in by.names.items() if pack.agent_on(data, key, k)]
        if workspace is None or row["on"] is None:
            return
        if not pack.pack_on(data, str(found.get("pack") or ""), workspace):
            row["off_reason"] = "its pack is off here"
            return
        if not row["on"]:
            said = by.states.get((workspace, key))
            row["off_reason"] = "not turned on yet" if said is None else _off_reason(said, mine)
            return
        ceiling = float((found.get("ceilings") or {}).get("usd") or 0.0)
        if by.cap and by.cap[0] + ceiling > by.cap[1]:
            row["off_reason"] = (
                f"held by the daily cap (${by.cap[0]:.2f} of ${by.cap[1]:.2f} spent)"
            )
        due = triggers.due(data, workspace, key)
        hours = (found.get("trigger", {}).get("schedule") or {}).get("hours")
        if hours:
            last = (
                datetime.fromisoformat(mine[-1]["at"]) if mine else by.now - timedelta(hours=hours)
            )
            at = (last + timedelta(hours=hours)).isoformat(timespec="seconds")
            due = min(due or at, at)
        row["next_at"] = due

    def live(self) -> Live:
        """The runs in flight and the proposals pending in every listed workspace."""
        data = Data(self.config.data_dir)
        rows = pack.rows()
        by_key = {self.ws.key(w["path"]): w["name"] for w in self.ws.all()["workspaces"]}

        def name(key: str) -> str:
            return str((rows.get(key) or {}).get("name") or key)

        running = [
            LiveRun(
                workspace=by_key.get(h["workspace"], ""),
                agent=h["agent"],
                name=name(h["agent"]),
                run=h["run"],
                started=h["started"],
            )
            for h in triggers.running()
            if h["workspace"] in by_key
        ]
        waiting = [
            LiveProposal(
                id=p["id"],
                workspace=label,
                agent=p["agent"],
                agent_name=name(p["agent"]),
                type=p["type"],
                title=p["title"],
                at=p["at"],
            )
            for key, label in by_key.items()
            for p in proposals.listed(data, key)
            if p["state"] == "pending"
        ]
        return Live(
            running=running,
            proposals=sorted(waiting, key=lambda p: p["at"], reverse=True),
            failed=self._failed(by_key, name),
        )

    def _failed(self, by_key: dict[str, str], name: Callable[[str], str]) -> list[LiveFailed]:
        """The agents whose latest run in a workspace failed within `FAILED_DAYS`, newest first:
        a later run that did not fail clears it, and so does turning the agent off after the failure
        (a person's run of an agent that is off here stays listed). A follow-up, a skip, a chat turn
        and a trial are no run of the agent's own."""
        journal = self.ws.journal()
        if journal is None:
            return []
        since = (datetime.now(timezone.utc) - timedelta(days=FAILED_DAYS)).isoformat(
            timespec="seconds"
        )
        latest: dict[tuple[str, str], dict[str, Any]] = {}
        off: dict[tuple[str, str], str] = {}
        try:
            for r in journal.records(None, kinds=("end", triggers.STATE_KIND), since=since):
                if r.get("kind") == triggers.STATE_KIND:
                    if r.get("on") is False:
                        off[str(r.get("workspace")), str(r.get("agent"))] = str(r.get("at") or "")
                    else:
                        off.pop((str(r.get("workspace")), str(r.get("agent"))), None)
                elif r.get("workspace") in by_key and r.get("agent") and not r.get("unit"):
                    if not (
                        r.get("skipped")
                        or r.get("parent_run")
                        or r.get("stage") in ("ask", "chat", triggers.TRIAL)
                    ):
                        latest[str(r["workspace"]), str(r["agent"])] = r
        except Unusable, Busy, sqlite3.Error, OSError:
            return []
        out = [
            LiveFailed(
                workspace=by_key[ws],
                agent=key,
                name=name(key),
                run=str(r.get("run") or ""),
                at=str(r.get("at") or ""),
                detail=str(r.get("detail") or ""),
            )
            for (ws, key), r in latest.items()
            if r.get("outcome") == "failed" and off.get((ws, key), "") <= str(r.get("at") or "")
        ]
        return sorted(out, key=lambda f: f["at"], reverse=True)

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
        made: dict[str, int],
        counts: dict[str, tuple[int, int]] | None,
    ) -> AgentRow:
        # A question to one of its runs (`ask`) is no run of it: only its cost counts.
        mine = [r for r in ends.get(key, []) if r.get("stage") != "ask"]
        edited = list(found.get("edited") or [])
        recent = [r for r in mine if str(r.get("at") or "") >= since]
        ran = [r for r in recent if not r.get("skipped")]
        asked = [
            r
            for r in ends.get(key, [])
            if r.get("stage") == "ask" and str(r.get("at") or "") >= since
        ]
        last = _run_view(mine[-1]) if mine else None
        budget = config["ceilings"]["max_budget_usd"]
        if mine and mine[-1].get("label") == policy.NOVEL and novel is not None:
            budget = novel["ceilings"]["max_budget_usd"] or budget
        views = [_run_view(r, counts) for r in recent]
        return AgentRow(
            key=key,
            pack=str(found.get("pack") or ""),
            own=bool(found.get("own")),
            group=group,
            row=_fields_of(found),
            builtin=RowFields(
                **{
                    k: v
                    for k, v in _fields_of(found.get("builtin") or found).items()
                    if k in edited
                }
            ),
            edited=edited,
            problems=[],
            editable=True,
            skills=_skills(found),
            row_hash=pack.hash_of(found),
            config=config,
            novel=novel,
            last=last,
            runs_30d=len(ran),
            cost_30d=round(sum(r.get("cost_usd") or 0.0 for r in (*ran, *asked)), 6),
            chip=chip_of(last, budget, len(recent)),
            groups=(
                groups_of(views, [s for s in settings.get(key, []) if s["at"] >= since])
                if counts is not None
                else []
            ),
            accepted_30d=made.get("accepted", 0),
            dismissed_30d=made.get("dismissed", 0),
            pending=made.get("pending", 0),
            skips_30d=len(recent) - len(ran),
            reads_only=pack.reads_only(found),
            usable_data=list(contracts.TRIGGERED_DATA if pack.triggered(found) else contracts.DATA),
            on=None,
            running=None,
            next_at=None,
            on_in=[],
            off_reason="",
        )

    @staticmethod
    def _on(data: Data, key: str, workspace: str | None) -> bool | None:
        found = pack.row(key)
        timed = pack.triggered(found, "event") or pack.triggered(found, "schedule")
        return pack.agent_on(data, key, workspace) if timed and workspace else None

    def set_state(self, cwd: str, key: object, on: object) -> AgentPage:
        """Turn `key`'s event or schedule on or off in the workspace `cwd`: the pref
        `agents.state`, logged as an `agent-state` row `by: owner`. A press and Leif run it
        either way. **Behind the password**: whoever holds it can make a read-only row run on its
        own there, under its ceilings and the daily cap."""
        ws = self.ws.key(self.ws.check(cwd))
        if not isinstance(key, str) or not isinstance(on, bool):
            raise Invalid("send {cwd, key, on}: on is true or false")
        try:
            pack.set_agent_on(Data(self.config.data_dir), key, ws, on)
        except pack.PackError as e:
            raise Invalid(str(e)) from e
        journal = self.ws.journal()
        if journal is not None:
            try:
                journal.append(dict(triggers.state_record(ws, key, on, OWNER)))
            except (BadRecord, Busy) as e:
                raise Invalid(f"the setting was saved but not logged: {e}") from e
        return self.agent_page(ws, cwd=cwd, agent=str(key))

    def proposals_view(self, cwd: str) -> ProposalsView:
        """Every agent's proposals in the workspace `cwd`, newest first, each naming its agent, and
        the rows that propose, on or off there."""
        ws = self.ws.key(self.ws.check(cwd))
        data = Data(self.config.data_dir)
        rows = pack.rows()

        def name(key: str) -> str:
            return str((rows.get(key) or {}).get("name") or key)

        return ProposalsView(
            proposals=[
                ProposalRow(**p, agent_name=name(p["agent"])) for p in proposals.listed(data, ws)
            ],
            agents=[
                ProposingAgent(key=k, name=name(k), on=self._on(data, k, ws))
                for k, r in rows.items()
                if (r.get("output") or {}).get("kind") == "proposal"
            ],
        )

    def set_agent_field(
        self, key: object, field: object, value: object = None, cwd: str = "", also: object = None
    ) -> AgentPage:
        """Save one field of one row into the owner's layer (`pack.write`), or put the built-in's
        back when `value` is `None` (or `""`), then log it and return `agent_page`. `also` holds
        frontmatter keys saved with it in the same checked write, each logged.

        `field` is a frontmatter key (`pack.KEYS`), `body`, or `skill:<name>`, its value the whole
        key as the row file holds it. The row as it would then stand must pass `pack.check` with
        the app's catalog, its `input` and `output` `contracts`; else a 400 naming every reason and
        nothing is written. `trigger` is saved only on a row its own trigger starts (an event, a
        schedule, a press, Leif), and only within `pack.check`: a row an event, a schedule or Leif
        starts holds only reading tools, and Bash only in the sandbox. Any other row's is shown, not saved.

        **Behind the password like every route here**: whoever holds it or a live session can give
        any agent another model, larger ceilings, another prompt or more of the catalog's tools,
        inside the critical calls (`policy.critical`), and the autopilot runs with it. The trace is
        the `agent-setting` record and each run's `row_hash` and `edited`.
        """
        if not isinstance(key, str) or pack.row(key) is None:
            raise Invalid(f"no such agent: {key} (use one of {', '.join(pack.rows())})")
        field = str(field)
        if also is not None and (not isinstance(also, dict) or set(also) - set(pack.ALSO)):
            raise Invalid(f"also is {{field: value}}, its fields only {', '.join(pack.ALSO)}")
        parts: dict[str, Any] = {k: (None if v == "" else v) for k, v in (also or {}).items()}
        if "trigger" in (field, *parts) and not pack.triggered(
            (pack.row(key) or {}).get("builtin")
        ):
            raise Invalid("trigger is read-only: the process or the engine says when it runs")
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
            before = {f: (pack.row(key) or {}).get(f) for f in parts}
            old, new = pack.write(key, field, value, self._effects(), parts)
        except (ValueError, contracts.ContractError) as e:
            raise Invalid(str(e)) from e
        except OSError as e:
            raise Invalid(
                f"the owner's layer could not be written, so nothing was saved: {e}"
            ) from e
        journal = self.ws.journal()
        if journal is not None:
            after = pack.row(key) or {}
            logged = [(field, old, new), *((f, before[f], after.get(f)) for f in parts)]
            try:
                for f, was, now in logged:
                    journal.append(
                        {
                            "kind": SETTING_KIND,
                            "workspace": "",
                            "unit": "",
                            "stage": "",
                            "agent": key,
                            "field": f,
                            "old": was,
                            "new": now,
                            "by": OWNER,
                        }
                    )
            except (BadRecord, Busy) as e:
                raise Invalid(f"the setting was saved but not logged: {e}") from e
        return self.agent_page(self.ws.key(cwd) if cwd else None, cwd=cwd, agent=str(key))

    def _vault(self) -> vault.Store:
        return vault.Store(Data(self.config.data_dir))

    def _log(self, record: dict[str, Any]) -> None:
        journal = self.ws.journal()
        if journal is None:
            return
        try:
            journal.append({"workspace": "", "unit": "", "stage": "", **record, "by": OWNER})
        except (BadRecord, Busy) as e:
            raise Invalid(f"the setting was saved but not logged: {e}") from e

    def new_agent(
        self, key: object, start: object, name: object, cwd: str = "", given: object = None
    ) -> AgentPage:
        """Write a new agent into the owner's pack (`pack.new_row`): a copy of row `start`, the
        whole row `given` (`{fields, body}`: a draft a person read and saves), or the smallest row
        that runs, checked with the app's catalog; logged as an `agent-setting` record. **Behind
        the password**: its tools are the catalog's, its runs get only what the engine derives,
        inside the critical calls."""
        if not isinstance(key, str) or not isinstance(name, str):
            raise Invalid("send {key, name, from?, row?}: key and name are names")
        if start is not None and not isinstance(start, str):
            raise Invalid("from is the key of an agent")
        if given is not None and not isinstance(given, dict):
            raise Invalid("row is {fields, body}")
        try:
            pack.new_row(key, name, start or None, self._effects(), given)
        except pack.PackError as e:
            raise Refused(e) from e
        except OSError as e:
            raise Invalid(
                f"the owner's pack could not be written, so nothing was saved: {e}"
            ) from e
        self._log(
            {
                "kind": SETTING_KIND,
                "agent": key,
                "field": "new",
                "old": None,
                "new": start or ("draft" if given is not None else ""),
            }
        )
        return self.agent_page(self.ws.key(cwd) if cwd else None, cwd=cwd, agent=str(key))

    def delete_agent(self, key: object, cwd: str = "") -> AgentPage:
        """Remove a whole row of the owner's pack, refused `in-use` while a process names it."""
        try:
            pack.delete_row(str(key))
        except pack.PackError as e:
            raise Refused(e) from e
        self._vault().forget_agents({str(key)})
        self._log(
            {"kind": SETTING_KIND, "agent": str(key), "field": "delete", "old": None, "new": None}
        )
        return self.agent_page(self.ws.key(cwd) if cwd else None, cwd=cwd)

    def set_process(self, cwd: str, name: object, given: object) -> list[pack.PackShown]:
        """Set the owner's process `local/<name>` (`pack.write_process`), or remove it with `None`:
        refused `in-use` while a unit records it. Logged as a `pack-setting` record."""
        key = self.ws.key(self.ws.check(cwd))
        if not isinstance(name, str) or (given is not None and not isinstance(given, dict)):
            raise Invalid("send {cwd, name, process}: process is {start, end, states} or null")
        ref = f"{pack.LOCAL_NAME}/{name}"
        try:
            if given is None and (used := self.ws.unit_meta().units_on(ref)):
                raise pack.PackError([f"{ref} is the process of {', '.join(used)}"], code="in-use")
            old, new = pack.write_process(name, given)
        except pack.PackError as e:
            raise Refused(e) from e
        except OSError as e:
            raise Invalid(
                f"the owner's pack could not be written, so nothing was saved: {e}"
            ) from e
        self._log(
            {
                "kind": PACK_KIND,
                "pack": pack.LOCAL_NAME,
                "field": f"process:{name}",
                "old": old,
                "new": new,
            }
        )
        return pack.packs_shown(Data(self.config.data_dir), key)

    def export_pack(self, name: str) -> bytes:
        try:
            return pack.export_zip(name)
        except pack.PackError as e:
            raise Refused(e) from e

    def import_pack(self, cwd: str, blob: bytes) -> list[pack.PackShown]:
        """Put the pack in zip `blob` in place (`pack.import_zip`, checked with the app's catalog),
        off in every workspace; logged as a `pack-setting` record. **Behind the password**: it
        brings prompts, skills and compositions of catalog tools, nothing that runs until a
        workspace turns it on and a run is started."""
        key = self.ws.key(self.ws.check(cwd))
        data = Data(self.config.data_dir)
        try:
            named = self._vault().named_agents()
            name = pack.import_zip(blob, self._effects(), lambda k, _: _named(k, named))
        except pack.PackError as e:
            raise Refused(e) from e
        except OSError as e:
            raise Invalid(f"the pack could not be written, so nothing was imported: {e}") from e
        _forget(data, name)
        self._log(
            {
                "kind": PACK_KIND,
                "pack": name,
                "field": "import",
                "old": None,
                "new": pack.pack_version(name),
            }
        )
        return pack.packs_shown(data, key)

    def delete_pack(self, cwd: str, name: str) -> list[pack.PackShown]:
        """Remove an imported pack, refused `in-use` while a unit records one of its processes."""
        key = self.ws.key(self.ws.check(cwd))
        data = Data(self.config.data_dir)
        try:
            if used := self.ws.unit_meta().units_on(name):
                raise pack.PackError(
                    [f"{name} holds the process of {', '.join(used)}"], code="in-use"
                )
            old = pack.pack_version(name)
            keys = {k for k, r in pack.rows().items() if r["pack"] == name}
            pack.remove_pack(name)
            self._vault().forget_agents(keys)
        except pack.PackError as e:
            raise Refused(e) from e
        except OSError as e:
            raise Invalid(f"the pack could not be removed: {e}") from e
        _forget(data, name)
        self._log({"kind": PACK_KIND, "pack": name, "field": "delete", "old": old, "new": None})
        return pack.packs_shown(data, key)


def _named(key: str, named: set[str]) -> list[str]:
    """An imported row never takes a key a vault secret's list names: it would get the secret."""
    return [f"{key}: a vault secret names an agent {key}"] if key in named else []


def _forget(data: Data, name: str) -> None:
    """Drop pack `name`'s on/off from every workspace: a pack imported under a removed one's name
    starts off."""
    state = data.pref(pack.STATE_PREF, {})
    if isinstance(state, dict) and name in state:
        data.set_pref(pack.STATE_PREF, {k: v for k, v in state.items() if k != name})


class Refused(Invalid):
    """A pack write refused: its `code` (`pack-refused`, `in-use`) and every reason in words."""

    def __init__(self, e: pack.PackError):
        super().__init__("; ".join(e.reasons))
        self.code = e.code
        self.reasons = e.reasons


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
        and with its body and its skills as the system prompt (`skills.system`)."""
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
            name=str((pack.row(key) or {}).get("name") or ""),
            system=skills.system(key),
        )

    def config_for(self, name: str) -> tuple[str | None, str, str | None, str]:
        """`(model, model_source, effort, effort_source)` of a row run with no label: Gebo's
        `integrate`."""
        return models.resolve(name, None, self.config.model)

    def stage_config(
        self,
        stage: str,
        process: str | None,
        plan: Plan | None,
        journal: Journal,
        key: str,
        unit: str,
        agent: str | None = None,
    ) -> dict[str, Any]:
        """The label a step runs under, the model and effort its `agent` row resolves to (the
        stage's own when none is given), and for a state that writes in the unit's branch which
        run of the unit's this is. Called after the gate, before any money is spent.
        The label chooses a configuration and nothing else.

        `Busy` from the run log is left to the caller, as `failed_attempts` is.

        On a routine `impl`, with no flag, the unit's arm names the model handed to `resolve`,
        and `trial_record` says which arm and what was asked for; the model the session really
        ran is filled in once its `init` names it. Any other step has no `trial_record` key.
        """
        row = agent or stage
        history = [r for r in journal.records(key, unit) if r.get("stage") == stage]
        label_declared, label, label_source = models.label_of(stage, process, plan)
        arm = modeltrial.arm(unit, row) if modeltrial.applies(row, label) else None
        trial_model = modeltrial.model_for(row, label, arm) if arm else None
        model, model_source, effort, effort_source = models.resolve(
            row, label, self.config.model, trial_model
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
                if states.by_of(process, stage) == "session"
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
