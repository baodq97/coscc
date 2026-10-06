"""The work-unit loop, decided in Python, command by command.

`python -m coscc.loop <command>` takes the loop's arguments and prints its answers,
byte for byte as the app reads them. This module holds what every part shares: the unit's process as
the loop walks it (`Proc`), the other constants the loop defines, and the few helpers that keep
JavaScript's semantics where Python's differ — `trim`, the `${}` of a template string, `?.` on a
dict, `JSON.stringify`.
"""

from __future__ import annotations

import json
import re
import subprocess
from functools import cache
from pathlib import Path
from typing import Any, get_args

from coscc.agent import pack
from coscc.units import UNIT_RE
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


def conditions(when) -> list[pack.Condition]:
    """A `when`: one condition or a list of them, all to hold."""
    if when is None:
        return []
    return list(when) if isinstance(when, list) else [when]


class Proc:
    """A process of the pack as the loop walks it.

    `stages` is what `status --json` prints of each state, in the process's order: its name, its
    artifact, `optional`, `when` (the field every way into it is conditioned on), the hint and
    the statuses. `info` keeps the rest: `next`, `agent`, `action`, the agent's output `kind`,
    `by` and `fields`, `rerun`, `skip`. The roles are what a state *is*: `review` (output kind
    `review`), `fixer` (where a review sends changes back), `pr` and `merge` (the actions),
    `spike` (its way on is guarded by `spike-holds`) and `rests` (where that way leads),
    `measured` (the state whose output field `measure` decides whether `spike` runs), `opener`
    (the first state that is not optional), `waits` (entered on `dependency-merged`) and `fast`
    (the `fast-lane` branch: from, to, the states it passes over, the fields that send it back).
    """

    def __init__(self, ref: str):
        raw = pack.process(ref)
        if raw is None:
            raise KeyError(ref)
        rows = pack.builtin_rows()
        self.ref = ref
        self.start: str = raw["start"]
        self.info: dict[str, dict[str, Any]] = {}
        for name, st in raw["states"].items():
            row = rows.get(str(st.get("agent"))) or {}
            output = row.get("output") or {}
            self.info[name] = {
                "next": list(st.get("next") or []),
                "agent": st.get("agent"),
                "action": st.get("action"),
                "kind": output.get("kind"),
                "by": output.get("by"),
                "fields": pack.output_fields(row),
                "rerun": list(st.get("rerun") or []),
                "skip": st.get("skip"),
                "optional": bool(st.get("optional")),
                "hint": st.get("hint") or name,
                "statuses": list(pack.statuses(st, rows)),
            }
        self.order = list(self.info)
        into: dict[str, list[dict[str, Any]]] = {n: [] for n in self.order}
        for name, i in self.info.items():
            for e in i["next"]:
                into[e["to"]].append({**e, "from": name})
        self.into = into
        self.stages: list[dict[str, Any]] = []
        for name, i in self.info.items():
            s: dict[str, Any] = {"name": name, "file": f"{name}.md"}
            if i["optional"]:
                s["optional"] = True
            when = self._when(name)
            if when:
                s["when"] = when
            s["hint"] = i["hint"]
            s["statuses"] = i["statuses"]
            self.stages.append(s)
        self.by_name = {s["name"]: s for s in self.stages}
        self.names = list(self.by_name)
        self.files = [s["file"] for s in self.stages]
        self.valid = {s["file"]: s["statuses"] for s in self.stages}
        self.review: str | None = self._first(lambda i: i["kind"] == "review")
        self.fixer: str | None = (
            next(
                (
                    e["to"]
                    for e in self.info[self.review]["next"]
                    for c in conditions(e.get("when"))
                    if c.get("field") == "verdict" and c.get("is") == "changes-requested"
                ),
                None,
            )
            if self.review
            else None
        )
        self.pr: str | None = self._first(lambda i: i["action"] == "open-pr")
        self.merge: str | None = self._first(lambda i: i["action"] == "merge")
        guarded = self._guarded("spike-holds")
        self.spike: str | None = guarded[0][0] if guarded else None
        self.rests: str | None = guarded[0][1] if guarded else None
        self.measured: str | None = None
        self.measure: str | None = None
        for e in into.get(self.spike, []) if self.spike else []:
            for c in conditions(e.get("when")):
                if "field" in c:
                    self.measured, self.measure = e["from"], c["field"]
        self.opener = next(n for n in self.order if not self.info[n]["optional"])
        self.waits = {to for _, to in self._guarded("dependency-merged")}
        fast = self._guarded("fast-lane")
        self.fast: dict[str, Any] | None = None
        if fast:
            source, target = fast[0]
            passed, at = [], self.info[source]["next"][-1]["to"]
            while at != target and at not in passed:
                passed.append(at)
                at = self.info[at]["next"][-1]["to"] if self.info[at]["next"] else target
            over = set(passed)
            todo = list(passed)
            while todo:
                for e in self.info[todo.pop()]["next"]:
                    if e["to"] != target and e["to"] not in over:
                        over.add(e["to"])
                        todo.append(e["to"])
            back = [
                c["field"]
                for e in self.info[target]["next"]
                if e["to"] in passed
                for c in conditions(e.get("when"))
                if "field" in c
            ]
            self.fast = {
                "from": source, "to": target, "passed": passed, "over": over, "back": back,
            }  # fmt: skip

    def _first(self, test) -> str | None:
        return next((n for n, i in self.info.items() if test(i)), None)

    def _guarded(self, guard: str) -> list[tuple[str, str]]:
        """`(from, to)` of every way on that asks `guard`."""
        return [
            (n, e["to"])
            for n, i in self.info.items()
            for e in i["next"]
            if any(c.get("guard") == guard for c in conditions(e.get("when")))
        ]

    def _when(self, name: str) -> str | None:
        """The field every way into `name` is conditioned on being non-empty, if one is."""
        fields = {
            c.get("field") if c.get("is") == "non-empty" else None
            for e in self.into[name]
            for c in (conditions(e.get("when")) or [{}])
        }
        if len(fields) == 1 and None not in fields and self.into[name]:
            return fields.pop()
        return None

    def file(self, name: str | None) -> str:
        return f"{name}.md" if name else ""

    def at(self, name: str | None) -> int:
        return self.order.index(name) if name in self.info else -1

    def rerun(self, how: str) -> list[str]:
        """The states a person may run again `fresh`, or that run again on `answers`."""
        return [n for n, i in self.info.items() if how in i["rerun"]]


DEFAULT = pack.DEFAULT_PROCESS
_PROCS: dict[str, Proc] = {}


def proc_of(ref) -> Proc:
    """The process `ref` names, read once; the default one for a unit that names none."""
    ref = ref if isinstance(ref, str) and pack.process(ref) is not None else DEFAULT
    if ref not in _PROCS:
        _PROCS[ref] = Proc(ref)
    return _PROCS[ref]


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
