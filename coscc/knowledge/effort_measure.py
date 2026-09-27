"""`coscc effort measure`: did a routine `impl` at a higher effort take fewer turns?

`0123_no-one-knows-if-each-stage-runs-at-the-right-effort` R8-R11. Reads `cos.db` with
`sqlite3` in `mode=ro` through `coscc/knowledge/measure.py`'s own reader, chooses the workspace by its
rule, and imports nothing of the web app. Run it at a terminal: inside a step `cos.db` is a
tripwire (`.claude/rules/coscc-sessions.md`).

The arm is read from the `start` records, never worked out again from a name (spec Design,
part 2). The fields are the ones `coscc/runner/__init__.py` writes, by the names in
`coscc/knowledge/efforttrial.py`: rename one there and this reads nothing, silently
(`.claude/rules/coscc-data.md`), which `coscc/knowledge/effort_measure_test.py` guards by writing its
fixture through the same names.
"""

from __future__ import annotations

import json
import os
import statistics
from typing import Any, Callable

from coscc.agent import labels
from coscc.knowledge import efforttrial
from coscc.knowledge import measure as reader

# `intent.md ## Answers`, câu 1, 2, 3 and 6: 10 units a side, 20% fewer turns, by 2026-11-30
# read in UTC, and no more cost, changes-requested rounds or red-CI returns.
GROUP = 10
TARGET = 0.20
DEADLINE = "2026-11-30"
TIMEZONE = "UTC"

PASS, FAIL, SHORT = "đạt", "không đạt", "chưa đủ mẫu"
CHANGES_REQUESTED = "changes-requested"
METRICS = ("turns", "usd", "changes_requested", "ci_red")
ARMS = (efforttrial.TRIAL_ARM, efforttrial.CONTROL_ARM)

USAGE = "usage: coscc effort measure [--workspace SLOT]"


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _shipped_at(unit: dict[str, Any]) -> str | None:
    """The `at` of the first `end` of `ship` that is `done`."""
    for e in unit["ends"]:
        if e.get("stage") == "ship" and e.get("outcome") == "done":
            return str(e.get("at") or "")
    return None


def _arm_of(start: dict[str, Any]) -> str | None:
    field = start.get(efforttrial.FIELD)
    return field.get("arm") if isinstance(field, dict) else None


def failed(unit: dict[str, Any]) -> list[int]:
    """Every one of R9's six conditions `unit` fails, by number."""
    starts = [s for s in unit["starts"] if s.get("stage") == efforttrial.STAGE]
    ends = [e for e in unit["ends"] if e.get("stage") == efforttrial.STAGE]
    out = []
    arms = {_arm_of(s) for s in starts}
    if len(arms) != 1 or not arms <= set(ARMS):
        out.append(1)
    first = starts[0] if starts else {}
    if first.get("label_declared") != labels.ROUTINE or first.get("label_source") in (labels.FORCED, labels.MISSING):
        out.append(2)
    if arms == {efforttrial.TRIAL_ARM} and not all(
        (s.get(efforttrial.FIELD) or {}).get("applied") is True
        for s in starts if s.get("label") == labels.ROUTINE
    ):
        out.append(3)
    # A unit with no `end` of `impl` has no run to compare (plan step 7).
    if not ends or not all(_number(e.get("turns")) and _number(e.get("cost_usd")) for e in ends):
        out.append(4)
    if any(
        _number(s.get("impl_run")) and s["impl_run"] > 1 and s.get(efforttrial.CI_RED) is None
        for s in starts
    ):
        out.append(5)
    if len({s.get("model") for s in starts}) != 1:
        out.append(6)
    return out


def metrics(unit: dict[str, Any]) -> dict[str, Any]:
    """R10. Every `end` of `impl` counts, the fixes after a review or CI and a run escalated to
    `novel` among them (spec C3)."""
    ends = [e for e in unit["ends"] if e.get("stage") == efforttrial.STAGE]
    return {
        "turns": sum(e["turns"] for e in ends),
        "usd": round(sum(float(e["cost_usd"]) for e in ends), 6),
        "changes_requested": sum(
            1 for e in unit["ends"] if e.get("stage") == "review" and isinstance(e.get("verdicts"), list)
            for v in e["verdicts"] if v == CHANGES_REQUESTED
        ),
        "ci_red": sum(
            1 for s in unit["starts"]
            if s.get("stage") == efforttrial.STAGE and s.get(efforttrial.CI_RED) is True
        ),
    }


def _side(found: dict[str, dict[str, Any]], names: list[str]) -> dict[str, Any]:
    each = {name: metrics(found[name]) for name in names}
    return {
        "n": len(names),
        "units": list(names),
        "median": {
            m: (statistics.median(v[m] for v in each.values()) if names else None) for m in METRICS
        },
        "models": sorted({
            str(s.get("model")) for name in names for s in found[name]["starts"]
            if s.get("stage") == efforttrial.STAGE
        }),
    }


def measure(rows: list[dict[str, Any]], slot: str, today: str | None = None) -> dict[str, Any]:
    """R9-R11 over one workspace's records."""
    found = reader.units_of(rows, slot)
    taken: dict[str, list[tuple[str, str]]] = {arm: [] for arm in ARMS}
    excluded = []
    for name in sorted(found):
        unit = found[name]
        shipped = _shipped_at(unit)
        if not shipped or shipped[:10] > DEADLINE:
            continue
        starts = [s for s in unit["starts"] if s.get("stage") == efforttrial.STAGE]
        # Before the flag: in no arm, and not listed either (spec Out of scope).
        if not any(efforttrial.FIELD in s for s in starts):
            continue
        why = failed(unit)
        if why:
            excluded.append({"unit": name, "failed": why})
            continue
        taken[_arm_of(starts[0])].append((shipped, name))
    sides = {arm: _side(found, [name for _, name in sorted(taken[arm])[:GROUP]]) for arm in ARMS}
    trial, control = sides[efforttrial.TRIAL_ARM], sides[efforttrial.CONTROL_ARM]

    reduction = None
    if trial["n"] and control["n"] and control["median"]["turns"] > 0:
        reduction = round(1 - trial["median"]["turns"] / control["median"]["turns"], 4)
    if trial["n"] < GROUP or control["n"] < GROUP:
        verdict = SHORT
    elif (reduction is not None and reduction >= TARGET
          and all(trial["median"][m] <= control["median"][m] for m in ("usd", "changes_requested", "ci_red"))):
        verdict = PASS
    else:
        verdict = FAIL
    today = today or reader._now()[:10]
    return {
        "workspace": slot,
        "deadline": DEADLINE,
        "timezone": TIMEZONE,
        "past_deadline": today > DEADLINE,
        "target": TARGET,
        "group": GROUP,
        "trial": trial,
        "control": control,
        "reduction": reduction,
        "excluded": excluded,
        "verdict": verdict,
    }


def run(data_dir: str | os.PathLike[str] | None, wanted: str | None,
        say: Callable[[str], None] = print) -> int:
    db = reader._db(data_dir)
    if not db.is_file():
        say(f"coscc effort measure: no {db}")
        return 2
    rows = reader.read_rows(db)
    try:
        slot = reader.choose(rows, wanted)
    except reader.Refused as e:
        say(f"coscc effort measure: {e}")
        return 2
    say(json.dumps(measure(rows, slot), indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str], say: Callable[[str], None] = print) -> int:
    """`measure [--workspace SLOT]`, and nothing else."""
    if argv[:1] == ["measure"] and len(argv) == 1:
        wanted = None
    elif argv[:2] == ["measure", "--workspace"] and len(argv) == 3 and argv[2]:
        wanted = argv[2]
    else:
        say(f"coscc effort: unrecognised arguments {argv!r}\n{USAGE}")
        return 2
    from coscc.config import from_env

    return run(from_env().data_dir, wanted, say)
