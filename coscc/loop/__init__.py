"""The work-unit loop, decided in Python: a port of `.claude/scripts/cos.mjs`, command for command.

`python -m coscc.loop <command>` takes the arguments `cos.mjs` takes and prints what it prints,
byte for byte. This module holds what every part shares: the stages and the other
constants `cos.mjs` defines, and the few helpers that keep JavaScript's semantics where Python's
differ — `trim`, the `${}` of a template string, `?.` on a dict, `JSON.stringify`.
"""

from __future__ import annotations

import json
import re
import subprocess
from functools import cache
from pathlib import Path
from typing import Any

from coscc.units.guards import REASONS

__all__ = ["REASONS"]


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


UNIT_RE = re.compile(r"^(\d{4})_([a-z0-9]+(?:-[a-z0-9]+)*)$", re.ASCII)
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", re.ASCII)

# `cos.mjs` `STAGES`, in order and with the same keys, so `status --json` prints them alike.
STAGES: list[dict[str, Any]] = [
    {"name": "idea", "file": "idea.md", "optional": True, "hint": "write-idea",
     "statuses": ["draft", "accepted", "rejected"]},
    {"name": "intent", "file": "intent.md", "hint": "write-intent — the unit has no intent.md",
     "statuses": ["draft", "accepted", "rejected"]},
    {"name": "spec", "file": "spec.md", "lanes": ["full"],
     "hint": "write-spec — it assesses whether to skip first",
     "statuses": ["draft", "accepted", "rejected", "skipped"]},
    {"name": "spike", "file": "spike.md", "lanes": ["full"], "when": "unmeasured",
     "hint": "write-spike — spec.md has [unmeasured] items",
     "statuses": ["draft", "accepted", "rejected"]},
    {"name": "plan", "file": "plan.md", "lanes": ["full"], "hint": "write-plan",
     "statuses": ["draft", "accepted", "rejected", "done"]},
    {"name": "impl", "file": "impl.md", "hint": "write-impl — implementation starts",
     "statuses": ["draft", "accepted", "rejected", "done"]},
    {"name": "pr", "file": "pr.md", "hint": "pr", "statuses": ["draft", "accepted", "rejected"]},
    {"name": "review", "file": "review.md", "hint": "write-review",
     "statuses": ["draft", "changes-requested", "accepted", "rejected"]},
    {"name": "ship", "file": "ship.md", "hint": "ship", "statuses": ["draft", "accepted", "rejected"]},
]  # fmt: skip

STAGE_ALIAS = {"implement": "impl"}
STAGE_NAMES = [s["name"] for s in STAGES]
ARTIFACTS = [s["file"] for s in STAGES]
VALID: dict[str, Any] = {s["file"]: s["statuses"] for s in STAGES}
SPIKE = next(s for s in STAGES if s.get("when") == "unmeasured")


def stage_of(name):
    """`cos.mjs` `stageOf`: the stage named, through `STAGE_ALIAS`, or `None`."""
    want = STAGE_ALIAS.get(name, name) if isinstance(name, str) else name
    return next((s for s in STAGES if s["name"] == want), None)


RERUNNABLE = ["intent", "spec", "spike", "plan", "pr"]
# `cos.mjs` `RERUN_STAGES`: what `status --json` carries as `afterAnswers`.
RERUN_STAGES = ["intent", "spec", "spike", "plan", "impl"]
SPIKE_ROUNDS = 2
REVIEW_ROUNDS = 3
BRANCH_TYPES = ["feat", "fix", "docs", "refactor", "test", "chore", "perf", "build", "ci", "revert"]
DECIDERS = ["person", "delegated"]
SLUG_MAX = 60
IDEAS = "ideas"
LOCAL_ONLY = {"check-branch", "check-tag", "check-version"}
STATE_READERS = ["status", "gate", "next", "rerun", "unit-branch", "pr-text", "screens"]
NEEDS_STATE = "needs the coscc app: pass --state <file|-> (uv run coscc state <workspace>)"


def code(c: str) -> str:
    """`cos.mjs` `code`: a reason code, refused here when `REASONS` lacks it."""
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
