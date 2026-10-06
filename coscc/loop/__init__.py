"""The work-unit loop, decided in Python, command by command.

`python -m coscc.loop <command>` takes the loop's arguments and prints its answers,
byte for byte as the app reads them. This module holds what every part shares: the stages and the other
constants the loop defines, and the few helpers that keep JavaScript's semantics where Python's
differ — `trim`, the `${}` of a template string, `?.` on a dict, `JSON.stringify`.
"""

from __future__ import annotations

import json
import re
import subprocess
from functools import cache
from pathlib import Path
from typing import Any, get_args

from coscc.units import UNIT_RE, states
from coscc.units.contracts import BranchType
from coscc.units.guards import DECIDERS, REASONS

__all__ = ["DECIDERS", "REASONS", "UNIT_RE"]


@cache
def checkout() -> Path:
    """The checkout the process stands in, whose `.cos/` and version files the commands read
    without `--root`: git's toplevel of the cwd, else the cwd. Never where this package is
    installed, which a wheel puts in `site-packages`."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )
    except OSError:
        return Path.cwd()
    top = r.stdout.decode("utf-8", "replace").strip()
    return Path(top) if r.returncode == 0 and top else Path.cwd()


SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", re.ASCII)

# `status --json` prints these. Names, files, statuses and `optional` are `states.json`'s; what is
# here is only what the loop adds to a stage: the hint it prints, the lanes it runs on, `when`.
_ADDED: dict[str, dict[str, Any]] = {
    "idea": {"hint": "write-idea"},
    "intent": {"hint": "write-intent — the unit has no intent.md"},
    "spec": {"lanes": ["full"], "hint": "write-spec — it assesses whether to skip first"},
    "spike": {
        "lanes": ["full"],
        "when": "unmeasured",
        "hint": "write-spike — spec.md has [unmeasured] items",
    },
    "plan": {"lanes": ["full"], "hint": "write-plan"},
    "impl": {"hint": "write-impl — implementation starts"},
    "pr": {"hint": "pr"},
    "review": {"hint": "write-review"},
    "ship": {"hint": "ship"},
}
STAGES: list[dict[str, Any]] = [
    {
        "name": s.name,
        "file": s.artifact,
        **({"optional": True} if s.optional else {}),
        **{k: v for k, v in _ADDED[s.name].items() if k != "hint"},
        "hint": _ADDED[s.name]["hint"],
        "statuses": list(s.statuses),
    }
    for s in states.default().stages
]

STAGE_NAMES = [s["name"] for s in STAGES]
ARTIFACTS = [s["file"] for s in STAGES]
VALID: dict[str, Any] = {s["file"]: s["statuses"] for s in STAGES}
SPIKE = next(s for s in STAGES if s.get("when") == "unmeasured")


def stage_of(name):
    """`stageOf`: the stage named, or `None`."""
    return next((s for s in STAGES if s["name"] == name), None)


RERUNNABLE = ["intent", "spec", "spike", "plan", "pr"]
# `RERUN_STAGES`: what `status --json` carries as `afterAnswers`.
RERUN_STAGES = ["intent", "spec", "spike", "plan", "impl"]
SPIKE_ROUNDS = 2
REVIEW_ROUNDS = 3
BRANCH_TYPES = list(get_args(BranchType))
SLUG_MAX = 60
IDEAS = "ideas"
LOCAL_ONLY = {"check-branch", "check-tag", "check-version"}
STATE_READERS = ["status", "gate", "next", "rerun", "unit-branch", "screens"]
NEEDS_STATE = "needs the coscc app: pass --state <file|-> (uv run coscc state <workspace>)"


def code(c: str) -> str:
    """`code`: a reason code, refused here when `REASONS` lacks it."""
    if c not in REASONS:
        raise ValueError(f"{c!r} is not in coscc.units.guards.REASONS")
    return c


# --- JavaScript's semantics, where Python's differ -------------------------------------

# `String.prototype.trim`'s set: `str.strip()` also strips `\x1c`-`\x1f` and `\x85`, and keeps
# `﻿`.
JS_SPACE = " \t\n\v\f\r                 　﻿"


def trim(s: str) -> str:
    return s.strip(JS_SPACE)


def trim_end(s: str) -> str:
    return s.rstrip(JS_SPACE)


def trim_start(s: str) -> str:
    return s.lstrip(JS_SPACE)


class _Undefined:
    """JavaScript's `undefined`: what `dig` returns for a key that is not there."""

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "undefined"


UNDEFINED = _Undefined()


def dig(obj, *keys):
    """`obj?.[k1]?.[k2]…`: `UNDEFINED` once a step is missing or not a dict (or a list, for ints)."""
    for k in keys:
        if isinstance(obj, dict) and k in obj:
            obj = obj[k]
        elif isinstance(obj, list) and isinstance(k, int) and 0 <= k < len(obj):
            obj = obj[k]
        else:
            return UNDEFINED
    return obj


def nullish(v, default=None):
    """`v ?? default`."""
    return default if v is None or v is UNDEFINED else v


def js(v) -> str:
    """`${v}` of a template string."""
    if v is None:
        return "null"
    if v is UNDEFINED:
        return "undefined"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, list):
        return ",".join("" if x is None or x is UNDEFINED else js(x) for x in v)
    if isinstance(v, dict):
        return "[object Object]"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def truthy(v) -> bool:
    """JavaScript truthiness: `[]` and `{}` are true, as every object is."""
    if isinstance(v, (list, dict)):
        return True
    return bool(v)


def _drop_undefined(v):
    if isinstance(v, dict):
        return {k: _drop_undefined(x) for k, x in v.items() if x is not UNDEFINED}
    if isinstance(v, list):
        return [None if x is UNDEFINED else _drop_undefined(x) for x in v]
    return v


def stringify(v, indent: int | None = None) -> str:
    """`JSON.stringify(v, null, indent)`: an `UNDEFINED` value drops its key, as `undefined` does."""
    v = _drop_undefined(v)
    if indent is None:
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(v, ensure_ascii=False, indent=indent)


def read_text(path) -> str:
    """`readFileSync(path, 'utf8')`: no newline translation, a bad byte read as U+FFFD."""
    return Path(path).read_bytes().decode("utf-8", "replace")


def split_lines(text: str) -> list[str]:
    """`text.split(/\\r?\\n/)`, without a regular expression: `re.split` was most of `status`'s time."""
    return text.replace("\r\n", "\n").split("\n")
