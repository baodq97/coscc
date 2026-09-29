"""Which model, and which effort, a stage or chat runs on, resolved in one place.

Each row (every stage `cos.mjs` names, then `chat`) resolves override first (a `model:<name>`
row in `prefs`), then default (`models.json`, shipped with the package; no `chat` key), then
`COS_MODEL` (`Config.model`, handed in; this module never reads the environment). If none
answers the model is `None` and the SDK default applies.

Effort is overridden under `effort:<name>` with no environment fallback. A stage after `plan`
may have a `<stage>:novel` row, used when the plan's effective label is `novel`; model and
effort are looked up separately, variant first. `max` is taken only from an override.

`resolve` may be handed a `trial_model`: lookup is then override, `COS_MODEL`, trial, default.
Only a routine `impl` board step hands one in; `table` never does.

The list of stages is `cos.mjs`'s and the caller passes it in; a key here that `cos.mjs` does
not name is reported as a problem. Bad data never raises: it is skipped and named in `problems`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

DEFAULT_PATH = Path(__file__).resolve().parent / "models.json"

CHAT = "chat"
# The backlog's proposal session: a row of its own, shown just before `chat`.
ESTIMATE = "estimate"
PREFIX = "model:"
EFFORT_PREFIX = "effort:"
NOVEL_SUFFIX = ":novel"
NOVEL = "novel"

# What the CLI's `--effort` accepts. `max` only from an override.
EFFORTS = ("low", "medium", "high", "xhigh", "max")
OVERRIDE_ONLY = "max"

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
        return {}, [f"{where.name} has no \"models\" object"]
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
            problems.append(f"{where.name}: {name!r} effort {effort!r} is not one of {', '.join(EFFORTS)}, ignored")
            effort = None
        if effort == OVERRIDE_ONLY:
            problems.append(f"{where.name}: {name!r} effort 'max' is taken only from an override, ignored")
            effort = None
        if model is None and effort is None:
            continue
        out[str(name)] = {"model": model.strip() if model else None, "effort": effort}
    return out, problems


def overrides_from(raw_rows: dict[str, str], prefix: str = PREFIX) -> tuple[dict[str, str], list[str]]:
    """Parse `<prefix><name>` rows as `Data.pref_rows` returns them. Never raises.

    With `EFFORT_PREFIX` a value must also be one of `EFFORTS`; `max` is allowed here.
    """
    out: dict[str, str] = {}
    problems: list[str] = []
    for key, raw in sorted(raw_rows.items()):
        if not key.startswith(prefix):
            continue
        name = key[len(prefix):]
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            problems.append(f"{key}: the stored value is not JSON ({raw!r}), ignored")
            continue
        if not isinstance(value, str):
            problems.append(f"{key}: the stored value is not a string ({value!r}), ignored")
            continue
        if not value.strip():
            problems.append(f"{key}: the stored value is empty, ignored")
            continue
        if prefix == EFFORT_PREFIX and value.strip() not in EFFORTS:
            problems.append(f"{key}: {value!r} is not one of {', '.join(EFFORTS)}, ignored")
            continue
        out[name] = value.strip()
    return out, problems


def resolve(
    name: str,
    label: str | None,
    model_overrides: dict[str, str],
    effort_overrides: dict[str, str],
    defaults: dict[str, dict[str, str | None]],
    env_model: str | None,
    trial_model: str | None = None,
) -> tuple[str | None, str, str | None, str]:
    """`(model, model_source, effort, effort_source)` for one row.

    The `<name>:novel` keys are consulted only when `label` is `novel`. `trial_model` is taken,
    as `TRIAL`, when no key has a model override and `COS_MODEL` is unset.
    """
    keys = ([name + NOVEL_SUFFIX] if label == NOVEL else []) + [name]

    def pick(field: str, overrides: dict[str, str]) -> tuple[str | None, str]:
        for key in keys:
            if overrides.get(key):
                return overrides[key], OVERRIDE
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


def rows_for(stages: Iterable[str]) -> list[str]:
    """Every row Settings shows: each stage, its `:novel` variant right after it when the
    stage comes after `plan`, then `estimate`, then `chat`."""
    names = [str(s) for s in stages]
    after_plan = names.index("plan") + 1 if "plan" in names else len(names)
    out: list[str] = []
    for i, name in enumerate(names):
        out.append(name)
        if i >= after_plan:
            out.append(name + NOVEL_SUFFIX)
    return out + [ESTIMATE, CHAT]


def table(
    stages: Iterable[str],
    model_overrides: dict[str, str],
    effort_overrides: dict[str, str],
    defaults: dict[str, dict[str, str | None]],
    env_model: str | None,
    agents: int,
) -> dict[str, Any]:
    """Every row Settings shows, as `rows_for` orders them.

    A `:novel` row is resolved as a `novel` run of its stage would be. `chat` has no effort.
    `problems` names every key, in the overrides or the defaults, that is not a row; such a
    key changes nothing, and this is how a person learns a stage was renamed.
    """
    names = rows_for(stages)
    rows = []
    for name in names:
        label = NOVEL if name.endswith(NOVEL_SUFFIX) else None
        model, model_source, effort, effort_source = resolve(
            name.removesuffix(NOVEL_SUFFIX), label, model_overrides, effort_overrides, defaults, env_model
        )
        if name == CHAT:
            effort, effort_source = None, NONE
        rows.append({
            "name": name, "agents": agents, "model": model or "", "source": model_source,
            "effort": effort or "", "effort_source": effort_source,
        })
    known = set(names)
    problems = [
        f"{PREFIX}{k}: no stage called {k!r}, ignored"
        for k in sorted(model_overrides) if k not in known
    ] + [
        f"{EFFORT_PREFIX}{k}: no stage called {k!r}, ignored"
        for k in sorted(effort_overrides) if k not in known or k == CHAT
    ] + [
        f"{DEFAULT_PATH.name}: no stage called {k!r}, ignored"
        for k in sorted(defaults) if k not in known
    ]
    return {"rows": rows, "problems": problems}
