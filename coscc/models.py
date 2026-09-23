"""Which model a stage, or chat, runs on — resolved in one place.

`0004_no-setting-says-which-model-runs-a-stage`. Until this module every session got
`COS_MODEL`, whatever it was for, and nobody could see or change a stage's model without
editing code. Now each row — every stage `cos.mjs` names, then `chat` — resolves in this
order, and the first that has an answer wins:

1. **override** — a `model:<name>` row in the `prefs` table, set from Settings. It lives
   under the data root, outside the code and outside any repository.
2. **default** — `coscc/models.json`, shipped with the package beside `states.json` and for
   the same reason (`coscc/states.py:36-39`): a wheel has to carry the rules it runs on.
   It has no `chat` key on purpose (spec `## Answers`, câu 1).
3. **COS_MODEL** — `Config.model`, handed in. This module never reads the environment
   (`coscc/config.py:5-6`).

If none answers, the model is `None`, and the SDK's own default applies as it did before.

**What it does not hold is the list of stages.** That list is `cos.mjs`'s, and the caller
passes it in (`coscc/board.py` `stages`). A key here that `cos.mjs` does not name is
reported as a problem, never shown as a stage — `.claude/CLAUDE.md` forbids a second copy
of the loop, and a list of stage names in this file would be one.

Nothing here raises on bad data. A broken `models.json` or a hand-edited override that does
not parse is skipped and named in `problems`, so the app keeps running and says why a row
fell back (spec R11).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

DEFAULT_PATH = Path(__file__).resolve().parent / "models.json"

CHAT = "chat"
PREFIX = "model:"

OVERRIDE = "override"
DEFAULT = "default"
ENV = "COS_MODEL"
NONE = "none"


def load_defaults(path: str | Path | None = None) -> tuple[dict[str, str], list[str]]:
    """The shipped defaults and what was wrong with them. Never raises."""
    where = Path(path) if path is not None else DEFAULT_PATH
    try:
        raw = json.loads(where.read_text(encoding="utf-8"))
    except OSError as e:
        return {}, [f"{where.name} could not be read: {e}"]
    except ValueError as e:
        return {}, [f"{where.name} is not JSON: {e}"]
    models = raw.get("models") if isinstance(raw, dict) else None
    if not isinstance(models, dict):
        return {}, [f"{where.name} has no \"models\" object"]
    out: dict[str, str] = {}
    problems: list[str] = []
    for name, model in models.items():
        if isinstance(model, str) and model.strip():
            out[str(name)] = model.strip()
        else:
            problems.append(f"{where.name}: {name!r} is not a model name: {model!r}")
    return out, problems


def overrides_from(raw_rows: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Parse `model:<name>` rows as `Data.pref_rows` returns them. Never raises."""
    out: dict[str, str] = {}
    problems: list[str] = []
    for key, raw in sorted(raw_rows.items()):
        if not key.startswith(PREFIX):
            continue
        name = key[len(PREFIX):]
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
        out[name] = value.strip()
    return out, problems


def resolve(
    name: str,
    overrides: dict[str, str],
    defaults: dict[str, str],
    env_model: str | None,
) -> tuple[str | None, str]:
    """`(model, source)` for one row: override, then default, then `COS_MODEL`."""
    if overrides.get(name):
        return overrides[name], OVERRIDE
    if defaults.get(name):
        return defaults[name], DEFAULT
    if env_model:
        return env_model, ENV
    return None, NONE


def table(
    stages: Iterable[str],
    overrides: dict[str, str],
    defaults: dict[str, str],
    env_model: str | None,
    agents: int,
) -> dict[str, Any]:
    """Every row Settings shows: the stages in `cos.mjs` order, then `chat`.

    `problems` names every key, in the overrides or the defaults, that is neither a stage
    `cos.mjs` names nor `chat`. Such a key changes nothing; saying so is the only way a
    person finds out a stage was renamed under their setting.
    """
    names = [str(s) for s in stages]
    rows = []
    for name in names + [CHAT]:
        model, source = resolve(name, overrides, defaults, env_model)
        rows.append({"name": name, "agents": agents, "model": model or "", "source": source})
    known = set(names) | {CHAT}
    problems = [
        f"{PREFIX}{k}: no stage called {k!r}, ignored"
        for k in sorted(overrides) if k not in known
    ] + [
        f"{DEFAULT_PATH.name}: no stage called {k!r}, ignored"
        for k in sorted(defaults) if k not in known
    ]
    return {"rows": rows, "problems": problems}
