"""The eight agents: who each is, what it runs on, what it may do and how its runs went.

The resolving is `coscc/agent/agents.py` (who) and `coscc/agent/models.py` (model, effort and
the two ceilings); this is where the overrides are read from `prefs` and written back, and
where the run log's `end` records are added up for the Agents page.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict

from coscc.agent import agents, models, policy
from coscc.store.db import Busy, Data, Unusable
from coscc.store.journal import BadRecord
from coscc.kernel import OWNER
from coscc.kernel import Invalid
from coscc.config import Config
from coscc.units.workspaces import Workspaces

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


def skill_of(key: str) -> str:
    """The skill a step of `key` loads: `write-<stage>` (`coscc/runner/prompt.py`), and Gebo's
    own `integrate` (`coscc/github/integration.py`)."""
    return key if key == "integrate" else f"write-{key}"


class RunView(TypedDict):
    """One `end` record of a stage, as the page shows it; `workspace` is the run-log key, the
    workspace's resolved path."""

    workspace: str
    unit: str
    outcome: str
    at: str
    turns: int | None
    cost_usd: float | None


class GrantView(TypedDict):
    """What `grant_for` gives a step, to be read and never written: changing one widens what a step may do."""

    tools: list[str]
    commands: list[str]
    mcp: list[str]
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
    skill: str
    grant: GrantView
    last: RunView | None
    # The last `RECENT`, newest first.
    runs: list[RunView]
    runs_30d: int
    cost_30d: float
    chip: str


class AgentPage(TypedDict):
    rows: list[AgentRow]
    # `estimate` and `chat`.
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


def _grant_view(key: str) -> GrantView:
    grant = policy.grant_for(key)
    return GrantView(
        tools=list(grant.tools),
        commands=list(grant.commands),
        mcp=list(grant.mcp),
        submits=grant.submits,
        warning=grant.warning,
    )


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

    def agent_overrides(self) -> tuple[dict[str, dict[str, str]], list[str]]:
        """The stored overrides, or none and why when `cos.db` cannot be read: a name is
        never a reason to refuse a step or a board read."""
        try:
            rows = Data(self.config.data_dir).pref_rows(agents.PREFIX)
        except (Unusable, sqlite3.Error, OSError) as e:
            return {}, [f"the agent overrides could not be read, so the defaults apply: {e}"]
        return agents.overrides_from(rows)

    def agent(self, key: str) -> dict[str, Any] | None:
        """The resolved row for `key`, overrides included, or `None`. Every place in the
        service that shows or writes an agent's name asks this. Never raises on bad data."""
        return agents.agent_for(key, self.agent_overrides()[0])

    def agent_table(self) -> dict[str, Any]:
        """Every agent's identity, each with `overridden`, and what was wrong."""
        overrides, bad = self.agent_overrides()
        found = agents.table(overrides)
        for row in found["rows"]:
            row["overridden"] = row["key"] in overrides
        found["problems"] = bad + found["problems"]
        return found

    def config_overrides(self) -> tuple[dict[str, dict[str, models.Value]], list[str]]:
        """`{field: {row: value}}` for model, effort, turns and budget, and what was wrong. A
        store that cannot be read is no override at all, said in the problems: never a reason
        to refuse a step or the page."""
        found: dict[str, dict[str, models.Value]] = {}
        problems: list[str] = []
        try:
            data = Data(self.config.data_dir)
            for field, prefix in models.FIELD_PREFIX.items():
                found[field], bad = models.overrides_from(data.pref_rows(prefix), prefix)
                problems += bad
        except (Unusable, sqlite3.Error, OSError) as e:
            found = {field: {} for field in models.FIELD_PREFIX}
            problems = [
                f"the model and ceiling overrides could not be read, so the defaults apply: {e}"
            ]
        return found, problems

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
        labels: dict[tuple[str, str, str], Any] = {}
        ends: list[dict[str, Any]] = []
        for record in records:
            step = (str(record.get("workspace")), str(record.get("unit")), str(record.get("stage")))
            if record.get("kind") == "start":
                labels[step] = record.get("label")
            else:
                ends.append({**record, "label": labels.pop(step, None)})
        by_stage: dict[str, list[dict[str, Any]]] = {}
        for record in reversed(ends):
            by_stage.setdefault(str(record.get("stage") or ""), []).append(record)
        return by_stage, []

    def agent_page(self, workspace: str | None = None, now: datetime | None = None) -> AgentPage:
        """Everything the Agents page shows, in one call: the run log is read once, not per stage.

        `rows`, one per agent, failed and costly first and otherwise in `agents.json`'s order:
        identity, model, effort and the two ceilings each with its source, its `:novel` rows
        under `variants`, the grant (read only), the skill, the last run, the last `RECENT`,
        the cost and count of the last `WINDOW_DAYS`, and the chip. `others`: `estimate` and
        `chat`. `problems`: every override, default or record that was skipped.

        `workspace` is a run-log key; `None` adds up every workspace of the working folder.
        """
        now = now or datetime.now(timezone.utc)
        since = (now - timedelta(days=WINDOW_DAYS)).isoformat(timespec="seconds")
        identity = self.agent_table()
        overrides, bad_overrides = self.config_overrides()
        defaults, bad_defaults = models.load_defaults()
        keys = [r["key"] for r in identity["rows"]]
        config = models.agent_config(keys, overrides, defaults, self.config.model)
        by_key = {r["key"]: r for r in config["rows"]}
        ends, bad_runs = self._ends(workspace)

        rows: list[AgentRow] = []
        for who in identity["rows"]:
            key = str(who["key"])
            mine = ends.get(key, [])
            recent = [r for r in mine if str(r.get("at") or "") >= since]
            last = _run_view(mine[0]) if mine else None
            config_row = by_key[key]
            variants = [r for k, r in by_key.items() if k == key + models.NOVEL_SUFFIX]
            # A `novel` run is held to the `:novel` row's dollar ceiling where it has one.
            ran_under = next(
                (
                    v["ceilings"]["max_budget_usd"]
                    for v in variants
                    if mine and mine[0].get("label") == models.NOVEL
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
                    skill=skill_of(key),
                    grant=_grant_view(key),
                    last=last,
                    runs=[_run_view(r) for r in mine[:RECENT]],
                    runs_30d=len(recent),
                    cost_30d=round(cost, 6),
                    chip=chip_of(last, ran_under, len(recent)),
                )
            )
        rows.sort(key=lambda r: r["chip"] not in ATTENTION)
        return AgentPage(
            rows=rows,
            others=[by_key[k] for k in (models.ESTIMATE, models.CHAT) if k in by_key],
            problems=identity["problems"]
            + bad_defaults
            + bad_overrides
            + config["problems"]
            + bad_runs,
            cos_model=self.config.model,
        )

    def set_agent_field(
        self, key: object, field: object, value: object = None, workspace: str | None = None
    ) -> AgentPage:
        """Save one field of one row, or reset it to its default when `value` is `None`, then
        log it and return `agent_page`. Out of bounds is refused and nothing is written.

        `field` is one of `agents.FIELDS` (an agent's identity; `""` resets too) or of
        `models.FIELD_PREFIX` (model, effort, turns, budget; which rows take which is
        `models.settable`; an effort of `""` resets too).

        **Behind the password like every route here**: whoever holds it or a live session can
        raise any agent's budget to `BUDGET_MAX` a step, and the autopilot runs with it. The
        trace is the `agent-setting` record and each step's `config` event.
        """
        if not isinstance(key, str) or not key:
            raise Invalid("key is required")
        field = str(field)
        if field in agents.FIELDS:
            old, new = self._set_identity(key, field, value)
        elif field in models.FIELD_PREFIX:
            old, new = self._set_config(key, field, value)
        else:
            known = (*agents.FIELDS, *models.FIELD_PREFIX)
            raise Invalid(f"no such field: {field} (use one of {', '.join(known)})")
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

    def _set_config(self, key: str, field: str, value: Any) -> tuple[Any, Any]:
        """Write or remove `<prefix><key>`; `(old, new)` override."""
        rows = models.settable(agents.load_defaults()[0])
        if key not in rows:
            raise Invalid(f"no such row: {key} (use one of {', '.join(rows)})")
        if field not in rows[key]:
            raise Invalid(f"{key} has no {field} to set")
        # The effort box's "Default" choice sends nothing: it means the default, so a reset.
        if field == "effort" and value == "":
            value = None
        if value is not None:
            value, reason = models.check(field, value)
            if reason:
                raise Invalid(reason)
        prefix = models.FIELD_PREFIX[field]
        data = Data(self.config.data_dir)
        try:
            old = models.overrides_from(data.pref_rows(prefix), prefix)[0].get(key)
        except (Unusable, sqlite3.Error, OSError) as e:
            raise Invalid(f"the overrides could not be read, so nothing was saved: {e}") from e
        if value is None:
            data.delete_pref(prefix + key)
        else:
            data.set_pref(prefix + key, value)
        return old, value

    def _set_identity(self, key: str, field: str, value: Any) -> tuple[Any, Any]:
        """Write or remove one identity field of `key`'s override; `(old, new)` of that field.

        A name another row has, override or default, whatever its case, is refused: removing
        an override brings the default name back, so that is checked too.
        """
        defaults, _ = agents.load_defaults()
        if key not in defaults:
            raise Invalid(f"no such agent: {key} (use one of {', '.join(defaults)})")
        value = "" if value is None else value
        if value != "":
            reason = agents.check_field(field, value)
            if reason:
                raise Invalid(reason)

        # Read strictly, unlike `agent_overrides`: a write built on overrides it could not read
        # would drop the row's other fields and check the name against defaults alone.
        data = Data(self.config.data_dir)
        try:
            overrides, _ = agents.overrides_from(data.pref_rows(agents.PREFIX))
        except (Unusable, sqlite3.Error, OSError) as e:
            raise Invalid(
                f"the agent overrides could not be read, so nothing was saved: {e}"
            ) from e
        old = overrides.get(key) or {}
        new = {f: v for f, v in {**old, field: value}.items() if v != ""}
        after = {k: v for k, v in overrides.items() if k != key}
        if new:
            after[key] = new

        def named(k: str) -> str:
            row = agents.resolve(k, defaults, after)
            if row is None:
                raise Invalid(f"no such agent: {k} (use one of {', '.join(defaults)})")
            return str(row["name"])

        name = named(key)
        taken = {named(k).lower(): k for k in defaults if k != key}
        if name.lower() in taken:
            raise Invalid(f"the name {name} is already {taken[name.lower()]}'s")

        if new:
            data.set_pref(agents.PREFIX + key, new)
        else:
            data.delete_pref(agents.PREFIX + key)
        return old.get(field), new.get(field)
