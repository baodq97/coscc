"""Which model, and which effort, a stage or chat runs on, resolved in one place.

Each row (every stage the loop names, then `chat`) resolves override first (a `model:<name>`
row in `prefs`), then default (`models.json`, shipped with the package; no `chat` key), then
`COS_MODEL` (`Config.model`, handed in; this module never reads the environment). If none
answers the model is `None` and the SDK default applies.

Effort is overridden under `effort:<name>` with no environment fallback. A stage after `plan`
may have a `<stage>:novel` row, used when the plan's effective label is `novel`; model and
effort are looked up separately, variant first. `max` is taken only from an override.

`resolve` may be handed a `trial_model`: lookup is then override, `COS_MODEL`, trial, default.
Only a routine `impl` board step hands one in; `table` never does. Gebo (`integrate`) is no
stage: with nothing of its own it runs `impl`'s base row (`FALLS_BACK`).

A step's two ceilings resolve in `ceilings`, the one place the Agents page and the runner both
ask: override (`turns:<row>`, `budget:<row>`), then the grant's own (`coscc/agent/policy.py`),
the `SUBMIT_TURNS` floor applied after either. `check` holds the bounds every override keeps.

The list of stages is the loop's and the caller passes it in; a key here that the loop does
not name is reported as a problem. Bad data never raises: it is skipped and named in `problems`.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, TypedDict

from coscc.agent import policy

DEFAULT_PATH = Path(__file__).resolve().parent / "models.json"

CHAT = "chat"
# The backlog's proposal session: a row of its own, shown just before `chat`.
ESTIMATE = "estimate"
PREFIX = "model:"
EFFORT_PREFIX = "effort:"
TURNS_PREFIX = "turns:"
BUDGET_PREFIX = "budget:"
NOVEL_SUFFIX = ":novel"
# Gebo is no stage and has no row in `models.json`: it runs `impl`'s base row unless its own
# key is overridden.
FALLS_BACK = {"integrate": "impl"}

# What the CLI's `--effort` accepts. `max` only from an override.
EFFORTS = ("low", "medium", "high", "xhigh", "max")
OVERRIDE_ONLY = "max"

# The bounds an override keeps. Chosen, not measured: 500 turns is twice the `novel`
# ceiling, $50 about three times its $16.
MODEL_MAX = 100
TURNS_MIN, TURNS_MAX = 1, 500
BUDGET_MIN, BUDGET_MAX = 0.10, 50.00
# An override as stored: a model or an effort, a number of turns, dollars.
Value = str | int | float
# The prefix each field of `check` is stored under.
FIELD_PREFIX = {
    "model": PREFIX,
    "effort": EFFORT_PREFIX,
    "turns": TURNS_PREFIX,
    "budget": BUDGET_PREFIX,
}

OVERRIDE = "override"
# The model came from the trial, between `COS_MODEL` and the default.
TRIAL = "trial"
DEFAULT = "default"
ENV = "COS_MODEL"
NONE = "none"


def load_defaults(
    path: str | Path | None = None,
) -> tuple[dict[str, dict[str, str | None]], list[str]]:
    """The shipped defaults, `{name: {"model", "effort"}}`, and what was wrong with them.

    A bare string means that model, no effort. Never raises.
    """
    where = Path(path) if path is not None else DEFAULT_PATH
    try:
        raw = json.loads(where.read_text(encoding="utf-8"))
    except OSError as e:
        return {}, [f"{where.name} could not be read: {e}"]
    except ValueError as e:
        return {}, [f"{where.name} is not JSON: {e}"]
    rows = raw.get("models") if isinstance(raw, dict) else None
    if not isinstance(rows, dict):
        return {}, [f'{where.name} has no "models" object']
    out: dict[str, dict[str, str | None]] = {}
    problems: list[str] = []
    for name, entry in rows.items():
        if isinstance(entry, str):
            entry = {"model": entry}
        if not isinstance(entry, dict):
            problems.append(f"{where.name}: {name!r} is not a model entry: {entry!r}")
            continue
        model = entry.get("model")
        if not (isinstance(model, str) and model.strip()):
            problems.append(f"{where.name}: {name!r} has no model name: {model!r}")
            model = None
        effort = entry.get("effort")
        if effort is not None and effort not in EFFORTS:
            problems.append(
                f"{where.name}: {name!r} effort {effort!r} is not one of {', '.join(EFFORTS)}, ignored"
            )
            effort = None
        if effort == OVERRIDE_ONLY:
            problems.append(
                f"{where.name}: {name!r} effort 'max' is taken only from an override, ignored"
            )
            effort = None
        if model is None and effort is None:
            continue
        out[str(name)] = {"model": model.strip() if model else None, "effort": effort}
    return out, problems


def check(field: str, value: Any) -> tuple[Any, str]:
    """`(value as stored, "")`, or `(None, why)` when `value` is out of `field`'s bounds.

    A model is trimmed text of 1 to `MODEL_MAX` characters; an effort one of `EFFORTS` (`max`
    included: only an override may name it); turns a whole number `TURNS_MIN`-`TURNS_MAX`; a
    budget a number of dollars `BUDGET_MIN`-`BUDGET_MAX`, rounded to the cent.
    """
    if field == "model":
        if not isinstance(value, str) or not value.strip():
            return None, "model must be a name"
        if len(value.strip()) > MODEL_MAX:
            return None, f"model must be at most {MODEL_MAX} characters"
        return value.strip(), ""
    if field == "effort":
        if value not in EFFORTS:
            return None, f"effort must be one of {', '.join(EFFORTS)}"
        return value, ""
    if field == "turns":
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        if isinstance(value, bool) or not isinstance(value, int):
            return None, f"turns must be a whole number from {TURNS_MIN} to {TURNS_MAX}"
        if not TURNS_MIN <= value <= TURNS_MAX:
            return None, f"turns must be from {TURNS_MIN} to {TURNS_MAX}"
        return value, ""
    if field == "budget":
        if isinstance(value, str):
            try:
                value = float(value.strip().lstrip("$"))
            except ValueError:
                return (
                    None,
                    f"budget must be a number of dollars, ${BUDGET_MIN:.2f} to ${BUDGET_MAX:.2f}",
                )
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            return (
                None,
                f"budget must be a number of dollars, ${BUDGET_MIN:.2f} to ${BUDGET_MAX:.2f}",
            )
        value = round(float(value), 2)
        if not BUDGET_MIN <= value <= BUDGET_MAX:
            return None, f"budget must be from ${BUDGET_MIN:.2f} to ${BUDGET_MAX:.2f}"
        return value, ""
    return None, f"no such field: {field} (use one of {', '.join(FIELD_PREFIX)})"


def overrides_from(
    raw_rows: dict[str, str], prefix: str = PREFIX
) -> tuple[dict[str, Value], list[str]]:
    """Parse `<prefix><name>` rows as `Data.pref_rows` returns them. Never raises.

    Each value is held to `check` of its prefix's field; one out of bounds is skipped and named.
    """
    field = next(f for f, p in FIELD_PREFIX.items() if p == prefix)
    out: dict[str, Value] = {}
    problems: list[str] = []
    for key, raw in sorted(raw_rows.items()):
        if not key.startswith(prefix):
            continue
        name = key[len(prefix) :]
        try:
            value = json.loads(raw)
        except TypeError, ValueError:
            problems.append(f"{key}: the stored value is not JSON ({raw!r}), ignored")
            continue
        if field in ("model", "effort") and not isinstance(value, str):
            problems.append(f"{key}: the stored value is not a string ({value!r}), ignored")
            continue
        if field in ("turns", "budget") and isinstance(value, str):
            problems.append(f"{key}: the stored value is not a number ({value!r}), ignored")
            continue
        if field == "model" and not value.strip():
            problems.append(f"{key}: the stored value is empty, ignored")
            continue
        found, reason = check(field, value.strip() if isinstance(value, str) else value)
        if reason:
            problems.append(f"{key}: {value!r}: {reason}, ignored")
            continue
        out[name] = found
    return out, problems


def resolve(
    name: str,
    label: str | None,
    model_overrides: Mapping[str, Value],
    effort_overrides: Mapping[str, Value],
    defaults: dict[str, dict[str, str | None]],
    env_model: str | None,
    trial_model: str | None = None,
) -> tuple[str | None, str, str | None, str]:
    """`(model, model_source, effort, effort_source)` for one row.

    The `<name>:novel` keys are consulted only when `label` is `novel`. `trial_model` is taken,
    as `TRIAL`, when no key has a model override and `COS_MODEL` is unset.
    """
    keys = ([name + NOVEL_SUFFIX] if label == policy.NOVEL else []) + [name]
    keys += [FALLS_BACK[name]] if name in FALLS_BACK else []

    def pick(field: str, overrides: Mapping[str, Value]) -> tuple[str | None, str]:
        for key in keys:
            if overrides.get(key):
                return str(overrides[key]), OVERRIDE
            if (defaults.get(key) or {}).get(field):
                return defaults[key][field], DEFAULT
        return None, NONE

    model, model_source = pick("model", model_overrides)
    if trial_model and model_source != OVERRIDE:
        model, model_source = (env_model, ENV) if env_model else (trial_model, TRIAL)
    if model is None and env_model:
        model, model_source = env_model, ENV
    effort, effort_source = pick("effort", effort_overrides)
    return model, model_source, effort, effort_source


def ceiling_key(stage: str, label: str | None) -> str:
    """The row a step's ceilings are overridden under: `<stage>:novel` for a `novel` step of a
    stage `NOVEL_CEILINGS` names, else the stage."""
    if label == policy.NOVEL and stage in policy.NOVEL_CEILINGS:
        return stage + NOVEL_SUFFIX
    return stage


class Ceilings(TypedDict):
    """A step's two ceilings and where each came from; `None` from `none` where there is none."""

    max_turns: int | None
    max_turns_source: str
    max_budget_usd: float | None
    max_budget_source: str


NO_CEILINGS = Ceilings(
    max_turns=None, max_turns_source=NONE, max_budget_usd=None, max_budget_source=NONE
)


class ConfigRow(TypedDict):
    """One row of `agent_config`: what a session of `key` runs on, each with its source."""

    key: str
    # The fields this row may override, of `FIELD_PREFIX`.
    fields: list[str]
    model: str | None
    model_source: str
    effort: str | None
    effort_source: str
    ceilings: Ceilings
    # Per field of `fields`: whether an override is stored.
    overridden: dict[str, bool]


class AgentConfig(TypedDict):
    rows: list[ConfigRow]
    problems: list[str]


def ceilings(
    stage: str,
    label: str | None,
    turns_overrides: Mapping[str, Value],
    budget_overrides: Mapping[str, Value],
) -> Ceilings:
    """The two ceilings a step of `stage` under `label` runs with, and where each came from.

    `{max_turns, max_turns_source, max_budget_usd, max_budget_source}`: the override of
    `ceiling_key`'s row, else the agent row's own (`row_for_step`), with `turns_floor` applied
    after either. A row with no budget is `None`, from `none`. The page shows these and the
    runner hands them to the session, so the two cannot differ.
    """
    row = policy.row_for_step(stage, label)
    key = ceiling_key(stage, label)
    if key in turns_overrides:
        turns, turns_source = int(turns_overrides[key]), OVERRIDE
    else:
        turns, turns_source = row.max_turns, DEFAULT
    if key in budget_overrides:
        budget, budget_source = float(budget_overrides[key]), OVERRIDE
    elif row.max_budget_usd:
        budget, budget_source = float(row.max_budget_usd), DEFAULT
    else:
        budget, budget_source = None, NONE
    return Ceilings(
        max_turns=policy.turns_floor(stage, turns),
        max_turns_source=turns_source,
        max_budget_usd=budget,
        max_budget_source=budget_source,
    )


def settable(agent_keys: Iterable[str]) -> dict[str, tuple[str, ...]]:
    """Every row the Agents page shows, in its order, with the fields it may override.

    `agent_keys` are `agents.json`'s, which are the loop's stages (pr and ship run no session)
    and Gebo. An agent takes all four; a `:novel` row model and effort, and its ceilings when
    `NOVEL_CEILINGS` names its stage; `estimate` model and effort; `chat` a model only.
    """
    keys = [str(k) for k in agent_keys]
    out: dict[str, tuple[str, ...]] = {}
    for name in rows_for([k for k in keys if k not in FALLS_BACK]) + [
        k for k in keys if k in FALLS_BACK
    ]:
        base = name.removesuffix(NOVEL_SUFFIX)
        if name == CHAT:
            out[name] = ("model",)
        elif name == ESTIMATE:
            out[name] = ("model", "effort")
        elif name != base:
            ceilings_too = ("turns", "budget") if base in policy.NOVEL_CEILINGS else ()
            out[name] = ("model", "effort") + ceilings_too
        else:
            out[name] = tuple(FIELD_PREFIX)
    return out


def agent_config(
    agent_keys: Iterable[str],
    overrides: Mapping[str, Mapping[str, Value]],
    defaults: dict[str, dict[str, str | None]],
    env_model: str | None,
) -> AgentConfig:
    """Each row of `settable`, every field resolved with its source, and what was wrong.

    `overrides` is `{field: overrides_from(...)}` for the four fields of `FIELD_PREFIX`. A field
    a row does not take is `None` from `none`. `problems` names every stored key, and every key
    of the defaults, that is no row's: such a key changes nothing.
    """
    rows_fields = settable(agent_keys)
    rows: list[ConfigRow] = []
    for name, fields in rows_fields.items():
        base = name.removesuffix(NOVEL_SUFFIX)
        label = policy.NOVEL if name != base else None
        model, model_source, effort, effort_source = resolve(
            base, label, overrides["model"], overrides["effort"], defaults, env_model
        )
        if "effort" not in fields:
            effort, effort_source = None, NONE
        rows.append(
            ConfigRow(
                key=name,
                fields=list(fields),
                model=model,
                model_source=model_source,
                effort=effort,
                effort_source=effort_source,
                ceilings=(
                    ceilings(base, label, overrides["turns"], overrides["budget"])
                    if "turns" in fields
                    else NO_CEILINGS
                ),
                overridden={f: name in overrides[f] for f in fields},
            )
        )
    problems = [
        f"{FIELD_PREFIX[field]}{k}: no row called {k!r} takes a {field}, ignored"
        for field in FIELD_PREFIX
        for k in sorted(overrides[field])
        if field not in rows_fields.get(k, ())
    ] + [
        f"{DEFAULT_PATH.name}: no row called {k!r}, ignored"
        for k in sorted(defaults)
        if k not in rows_fields
    ]
    return AgentConfig(rows=rows, problems=problems)


def rows_for(stages: Iterable[str]) -> list[str]:
    """Each stage, its `:novel` variant right after it when the stage comes after `plan`,
    then `estimate`, then `chat`."""
    names = [str(s) for s in stages]
    after_plan = names.index("plan") + 1 if "plan" in names else len(names)
    out: list[str] = []
    for i, name in enumerate(names):
        out.append(name)
        if i >= after_plan:
            out.append(name + NOVEL_SUFFIX)
    return out + [ESTIMATE, CHAT]


# The label of a step, from the plan's record. The files where a mistake costs the most force
# `novel`: `coscc/agent/sessions.py` stands for `_options` (a list of files cannot name a
# function), `coscc/loop/rules.py` for the gates that decide a unit.
SECURITY_SURFACE = (
    "coscc/agent/policy.py",
    "coscc/agent/sessions.py",
    "coscc/loop/rules.py",
    ".claude/settings.json",
)
# Where a step's label came from: the record's `impl`; `forced` by a file of `SECURITY_SURFACE`;
# `escalated`, a routine impl that stopped on `max_turns` before (a budget stop does not
# escalate); `missing`, no plan record, run as `novel`.
DECLARED, FORCED, ESCALATED, MISSING = "declared", "forced", "escalated", "missing"


def _stopped_at_max_turns(end: dict[str, Any], before: Iterable[dict[str, Any]]) -> bool:
    """An `end` that was `exhausted` on `max_turns`."""
    if end.get("outcome") != "exhausted":
        return False
    terminal = end.get("terminal")
    if terminal is None:
        for rec in reversed(list(before)):
            if rec.get("kind") == "attempt":
                terminal = rec.get("terminal")
                break
            if rec.get("kind") in ("start", "end"):
                break
    return "max_turns" in str(terminal or "").lower()


def label_of(
    stage: str,
    stages: list[str],
    plan: Mapping[str, Any] | None,
    impl_history: list[dict[str, Any]],
) -> tuple[str | None, str | None, str | None]:
    """`(label_declared, label, label_source)` for one step; `(None, None, None)` at or before
    `plan`, whose record is not written yet. `plan` is the plan's record (`contracts.Plan`). `impl_history` is the unit's run log records for
    `impl`, oldest first."""
    if "plan" not in stages or stage not in stages or stages.index(stage) <= stages.index("plan"):
        return None, None, None
    if plan is None:
        return MISSING, policy.NOVEL, MISSING
    said = plan["impl"]
    if set(plan["files"]) & set(SECURITY_SURFACE):
        return said, policy.NOVEL, FORCED
    if said == policy.ROUTINE and stage == "impl":
        history = [r for r in impl_history if r.get("stage") == "impl"]
        for i, rec in enumerate(history):
            if rec.get("kind") == "end" and _stopped_at_max_turns(rec, history[:i]):
                return said, policy.NOVEL, ESCALATED
    return said, said, DECLARED
