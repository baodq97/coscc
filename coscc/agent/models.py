"""Which model, which effort and which two ceilings a run of an agent gets, resolved in one place
from its row (`coscc/agent/pack.py`): the row's `model`, its `novel` variant's for a `novel` step,
its `ceilings` likewise. Each says where it came from: `override` when the owner's layer changed it,
`default` when it is the built-in's. With no model the row's `COS_MODEL` (`Config.model`, handed
in; this module never reads the environment) applies, and with neither the SDK default.

`resolve` may be handed a `trial_model`: lookup is then the owner's model, `COS_MODEL`, the trial,
the row's own. Only a routine step of a row with `model.trial` hands one in.

A step's two ceilings resolve in `ceilings`, the one place the Agents page and the runner both
ask, the `SUBMIT_TURNS` floor applied after either. `check` reads a value a person sends.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, TypedDict

from coscc.agent import pack, policy


OVERRIDE = "override"
# The model came from the trial, between `COS_MODEL` and the row's.
TRIAL = "trial"
DEFAULT = "default"
ENV = "COS_MODEL"
NONE = "none"


def check(field: str, value: Any) -> tuple[Any, str]:
    """`(value as stored, "")`, or `(None, why)` when `value` is out of `field`'s bounds.

    A model is trimmed text of 1 to `pack.MODEL_MAX` characters; an effort one of `pack.EFFORTS`;
    turns a whole number in `pack`'s bounds; a budget a number of dollars in them, rounded to the
    cent.
    """
    if field == "model":
        if not isinstance(value, str) or not value.strip():
            return None, "model must be a name"
        if len(value.strip()) > pack.MODEL_MAX:
            return None, f"model must be at most {pack.MODEL_MAX} characters"
        return value.strip(), ""
    if field == "effort":
        if value not in pack.EFFORTS:
            return None, f"effort must be one of {', '.join(pack.EFFORTS)}"
        return value, ""
    if field == "turns":
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        if isinstance(value, bool) or not isinstance(value, int):
            return None, f"turns must be a whole number from {pack.TURNS_MIN} to {pack.TURNS_MAX}"
        if not pack.TURNS_MIN <= value <= pack.TURNS_MAX:
            return None, f"turns must be from {pack.TURNS_MIN} to {pack.TURNS_MAX}"
        return value, ""
    if field == "budget":
        dollars = f"${pack.BUDGET_MIN:.2f} to ${pack.BUDGET_MAX:.2f}"
        if isinstance(value, str):
            try:
                value = float(value.strip().lstrip("$"))
            except ValueError:
                return None, f"budget must be a number of dollars, {dollars}"
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            return None, f"budget must be a number of dollars, {dollars}"
        value = round(float(value), 2)
        if not pack.BUDGET_MIN <= value <= pack.BUDGET_MAX:
            return None, f"budget must be from {dollars}"
        return value, ""
    return None, f"no such field: {field} (use one of model, effort, turns, budget)"


def _source(found: Mapping[str, Any], top: str, sub: str, label: str | None) -> str:
    """`override` when the owner's layer gives `top.sub` another value than the built-in row."""
    now = policy.part_of(found, top, label).get(sub)
    if now is None:
        return NONE
    return (
        OVERRIDE
        if now != policy.part_of(found.get("builtin") or found, top, label).get(sub)
        else DEFAULT
    )


def resolve(
    name: str,
    label: str | None,
    env_model: str | None,
    trial_model: str | None = None,
    own: Mapping[str, Any] | None = None,
) -> tuple[str | None, str, str | None, str]:
    """`(model, model_source, effort, effort_source)` for one run of `name`'s row.

    `own` stands for a row the pack does not have (a feature's session): its `id` and `effort`.
    `trial_model` is taken, as `TRIAL`, when the owner did not choose the model and `COS_MODEL`
    is unset.
    """
    found = pack.row(name)
    if found is None:
        model, effort = (own or {}).get("id"), (own or {}).get("effort")
        model_source = DEFAULT if model else NONE
        effort_source = DEFAULT if effort else NONE
    else:
        chosen = policy.part_of(found, "model", label)
        model, effort = chosen.get("id"), chosen.get("effort")
        model_source = _source(found, "model", "id", label)
        effort_source = _source(found, "model", "effort", label)
    if trial_model and model_source != OVERRIDE:
        model, model_source = (env_model, ENV) if env_model else (trial_model, TRIAL)
    if model is None and env_model:
        model, model_source = env_model, ENV
    return model, model_source, effort, effort_source


class Ceilings(TypedDict):
    """A step's two ceilings and where each came from; `None` from `none` where there is none."""

    max_turns: int | None
    max_turns_source: str
    max_budget_usd: float | None
    max_budget_source: str


def ceilings(stage: str, label: str | None) -> Ceilings:
    """The two ceilings a step of `stage` under `label` runs with, and where each came from:
    `policy.row_for_step`'s, the owner's where they set them. A row with no budget is `None`, from
    `none`. The page shows these and the runner hands them to the session, so the two cannot
    differ."""
    row = policy.row_for_step(stage, label)
    found = pack.row(stage)
    if found is None:
        turns_source = DEFAULT
        budget_source = DEFAULT if row.max_budget_usd else NONE
    else:
        turns_source = _source(found, "ceilings", "turns", label)
        budget_source = _source(found, "ceilings", "usd", label)
    return Ceilings(
        max_turns=row.max_turns,
        max_turns_source=DEFAULT if turns_source == NONE else turns_source,
        max_budget_usd=row.max_budget_usd or None,
        max_budget_source=budget_source if row.max_budget_usd else NONE,
    )


class ConfigRow(TypedDict):
    """What a session of `key` runs on, resolved, each with its source; `label` is `novel` for the
    row's `novel` variant, else `None`."""

    key: str
    label: str | None
    model: str | None
    model_source: str
    effort: str | None
    effort_source: str
    ceilings: Ceilings


def config_row(key: str, env_model: str | None, label: str | None = None) -> ConfigRow:
    """`key` under `label` as the Agents page shows it: every value resolved, as a run gets it."""
    model, model_source, effort, effort_source = resolve(key, label, env_model)
    return ConfigRow(
        key=key,
        label=label,
        model=model,
        model_source=model_source,
        effort=effort,
        effort_source=effort_source,
        ceilings=ceilings(key, label),
    )


# The label of a step, from the plan's record. The files where a mistake costs the most force
# `novel`: `coscc/agent/sessions.py` stands for `_options` (a list of files cannot name a
# function), `coscc/loop/rules.py` for the gates that decide a unit.
SECURITY_SURFACE = (
    "coscc/agent/policy.py",
    "coscc/agent/sessions.py",
    "coscc/loop/rules.py",
    ".claude/settings.json",
)
# Where a step's label came from: the record's `variant`; `forced` by a file of `SECURITY_SURFACE`;
# `missing`, no plan record, run as `novel`.
DECLARED, FORCED, MISSING = "declared", "forced", "missing"


def label_of(
    stage: str, process: str | None, plan: Mapping[str, Any] | None
) -> tuple[str | None, str | None, str | None]:
    """`(label_declared, label, label_source)` for one step of a unit on `process`. The label is
    the `variant` of the last record before `stage` whose agent declares one (`plan`, the record
    as `contracts.Plan` reads it); `(None, None, None)` at or before that state. With no such
    state before it, a state whose row has variants runs as `novel`, and any other has none."""
    states = list((pack.process(process) or {}).get("states") or {})
    if stage not in states:
        return None, None, None
    declares = [
        s
        for s in states[: states.index(stage)]
        if "variant" in pack.output_fields(pack.row(pack.agent_for(process, s) or ""))
    ]
    if not declares:
        if (pack.row(pack.agent_for(process, stage) or "") or {}).get("variants"):
            return MISSING, policy.NOVEL, MISSING
        return None, None, None
    if plan is None:
        return MISSING, policy.NOVEL, MISSING
    said = plan["variant"]
    if set(plan["files"]) & set(SECURITY_SURFACE):
        return said, policy.NOVEL, FORCED
    return said, said, DECLARED
