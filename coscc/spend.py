"""Where the money went, from a workspace's run log (`0093`). Pure: no I/O, no clock but `now`.

Named `spend`, not `cost`, so it is not confused with `cost_test.py`, which tests
`sessions._cumulative`.

Only `end` records are added. An `attempt` repeats the cost of the step it belongs to, and
an `estimate` summary repeats its own step's (`0093` spec C1): adding them would count one
dollar twice. An `end` whose `cost_usd` is absent or null is a step whose cost is not known,
never zero — the same as SQLite's `SUM` skipping a `NULL`, which is what R4 compares against.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone, tzinfo
from typing import Any, Iterable

from coscc import journal
from coscc.journal import TOKEN_FIELDS

# `0093` R10. Chosen, not measured (spec C3), except the 15 USD, `review` > 3 and `spec` > 2
# that `intent.md ## Answers, câu 2` names. Leif (CoS) decides a change to any of them.
BUDGET_USD = 15.0
RERUN_LIMIT = {"review": 3}
RERUN_DEFAULT = 2
TOKENS_PER_TURN_TIMES = 3
TOKENS_PER_TURN_MIN_STEPS = 5

FAILED = ("exhausted", "failed")
CHANGES_REQUESTED = "changes-requested"

WASTE_KINDS = (
    "exhausted-or-failed",
    "run-again",
    "integrate-conflict",
    "integrate-other",
    "integrate-not-recorded",
    "changes-requested",
)
ANOMALY_KINDS = ("over-budget", "failed", "reruns", "tokens-per-turn")


def _usd(record: dict[str, Any]) -> float | None:
    value = record.get("cost_usd")
    return None if value is None else float(value)


def _zero() -> dict[str, Any]:
    return {"usd": None, "steps": 0, "unknown": 0, **{f: 0 for f in TOKEN_FIELDS}}


def _add(into: dict[str, Any], record: dict[str, Any]) -> None:
    into["steps"] += 1
    usd = _usd(record)
    if usd is None:
        into["unknown"] += 1
    else:
        into["usd"] = (into["usd"] or 0.0) + usd
    for f in TOKEN_FIELDS:
        into[f] += int(record.get(f) or 0)


def _rows(groups: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Groups as rows, the most money first; a group with no known cost last."""
    rows = [{"key": key, **_rounded(acc)} for key, acc in groups.items()]
    rows.sort(key=lambda r: r["key"])
    rows.sort(key=lambda r: (r["usd"] is None, -(r["usd"] or 0.0)))
    return rows


def _rounded(acc: dict[str, Any]) -> dict[str, Any]:
    out = dict(acc)
    if out["usd"] is not None:
        out["usd"] = round(out["usd"], journal.USD_PLACES)
    return out


def _day(at: Any, tz: tzinfo | None) -> str:
    """The local calendar day of an `at`, `""` when it is no time (SQLite's `date` is NULL)."""
    try:
        moment = datetime.fromisoformat(str(at or "").replace("Z", "+00:00"))
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(tz).date().isoformat()


def local_day(at: Any, tz: tzinfo | None = None) -> str:
    """`_day`, public: `0043`'s daily cap counts days the way this screen does."""
    return _day(at, tz)


def offset(tz: tzinfo | None = None) -> str:
    """`UTC+07:00`: today's offset of the zone the days are counted in (R3)."""
    delta = datetime.now(tz).astimezone(tz).utcoffset()
    minutes = int(delta.total_seconds() // 60) if delta is not None else 0
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    return f"UTC{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def _tokens(acc: dict[str, Any]) -> dict[str, Any]:
    out = {f: acc[f] for f in TOKEN_FIELDS}
    out["total"] = sum(out.values())
    return out


def _waste_row(kind: str, acc: dict[str, Any], note: int | None = None) -> dict[str, Any]:
    usd = acc["usd"]
    return {
        "kind": kind,
        "count": acc["steps"],
        "usd": None if usd is None else round(usd, journal.USD_PLACES),
        "unknown": acc["unknown"],
        "note": note,
    }


def _per_turn(record: dict[str, Any]) -> float | None:
    """Tokens per turn of one `end`, or None when it is not a valid step for R10."""
    try:
        turns = int(record.get("turns") or 0)
    except (TypeError, ValueError):
        return None
    if turns <= 0 or not any(f in record for f in TOKEN_FIELDS):
        return None
    return sum(int(record.get(f) or 0) for f in TOKEN_FIELDS) / turns


def model(
    records: Iterable[dict[str, Any]],
    rounds: dict[str, list[str]] | None = None,
    tz: tzinfo | None = None,
) -> dict[str, Any]:
    """Every figure of the *Cost* screen (`0093` R1–R3, R6–R11), from one workspace's records.

    `records` in the order they were written, as `Journal.records` returns them. `rounds` is
    each unit's review verdicts, oldest first, from the board's read (R9). `tz` is the zone
    days are counted in; None is this machine's, which is what `date(at, 'localtime')` uses.
    """
    records = list(records)
    rounds = rounds or {}
    ends = [r for r in records if r.get("kind") == "end"]

    total = _zero()
    by_unit: dict[str, dict[str, Any]] = {}
    by_stage: dict[str, dict[str, Any]] = {}
    by_day: dict[str, dict[str, Any]] = {}
    unit_stages: dict[str, dict[str, dict[str, Any]]] = {}
    for r in ends:
        unit, stage = str(r.get("unit") or ""), str(r.get("stage") or "")
        _add(total, r)
        _add(by_unit.setdefault(unit, _zero()), r)
        _add(by_stage.setdefault(stage, _zero()), r)
        _add(unit_stages.setdefault(unit, {}).setdefault(stage, _zero()), r)
        day = _day(r.get("at"), tz)
        if day:
            _add(by_day.setdefault(day, _zero()), r)

    unit_rows = _rows(by_unit)
    for row in unit_rows:
        row["over"] = bool(row["key"]) and (row["usd"] or 0.0) > BUDGET_USD
    day_rows = sorted(
        ({"key": key, **_rounded(acc)} for key, acc in by_day.items()),
        key=lambda r: r["key"], reverse=True,
    )

    # R7, one kind at a time. A step may sit in several (C7), so nothing adds them up.
    failed = _zero()
    again = _zero()
    seen: set[tuple[str, str]] = set()
    per_pair: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in ends:
        unit, stage = str(r.get("unit") or ""), str(r.get("stage") or "")
        if r.get("outcome") in FAILED:
            _add(failed, r)
        if not unit:
            continue
        per_pair.setdefault((unit, stage), []).append(r)
        if (unit, stage) in seen:
            _add(again, r)
        seen.add((unit, stage))

    # R8. Paired the way the timeline pairs them, to read the `start`'s `integrate_state`.
    # `_fold` counts an `end` as reporting its cost when the key is there, null or not; a
    # null is dropped first so it is unknown here too, as in every other total (R5).
    integrate = {"conflicting": _zero(), "other": _zero(), "none": _zero()}
    paired = [
        {k: v for k, v in r.items() if k != "cost_usd"} if r.get("kind") == "end" and _usd(r) is None else r
        for r in records
    ]
    for rows in journal.timelines_of(paired).values():
        for row in rows:
            if row.get("stage") != "integrate" or row.get("ended") is None:
                continue
            state = row.get("integrate_state")
            acc = integrate["none" if state is None else "conflicting" if state == "conflicting" else "other"]
            acc["steps"] += 1
            if journal._cost_unknown(row):
                acc["unknown"] += 1
            else:
                acc["usd"] = (acc["usd"] or 0.0) + float((row.get("cost") or {}).get("cost_usd") or 0.0)

    # R9. Rounds counted from `review.md`; money only from the `review` steps that said which
    # verdicts they wrote. A round no step claims is counted, and its money is not recorded.
    requested = _zero()
    claimed: dict[str, int] = {}
    for r in ends:
        if r.get("stage") != "review":
            continue
        verdicts = [str(v) for v in (r.get("verdicts") or [])]
        if CHANGES_REQUESTED not in verdicts:
            continue
        _add(requested, r)
        unit = str(r.get("unit") or "")
        claimed[unit] = claimed.get(unit, 0) + verdicts.count(CHANGES_REQUESTED)
    round_count = 0
    not_recorded = 0
    for unit, verdicts in rounds.items():
        asked = sum(1 for v in verdicts if v == CHANGES_REQUESTED)
        round_count += asked
        not_recorded += max(0, asked - claimed.get(unit, 0))
    requested_row = _waste_row(CHANGES_REQUESTED, requested, not_recorded)
    requested_row["count"] = round_count

    waste = [
        _waste_row("exhausted-or-failed", failed),
        _waste_row("run-again", again),
        _waste_row("integrate-conflict", integrate["conflicting"]),
        _waste_row("integrate-other", integrate["other"]),
        _waste_row("integrate-not-recorded", integrate["none"]),
        requested_row,
    ]

    anomalies = _anomalies(ends, unit_rows, per_pair)

    return {
        "total": _rounded(total),
        "by_unit": unit_rows,
        "by_stage": _rows(by_stage),
        "by_day": day_rows,
        "offset": offset(tz),
        "unit_stages": {unit: _rows(stages) for unit, stages in unit_stages.items()},
        "tokens": {
            "workspace": _tokens(total),
            "by_stage": [{"stage": stage, **_tokens(acc)} for stage, acc in sorted(by_stage.items())],
        },
        "waste": waste,
        "anomalies": anomalies,
    }


def _anomalies(
    ends: list[dict[str, Any]],
    unit_rows: list[dict[str, Any]],
    per_pair: dict[tuple[str, str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """R10's four kinds, in that order, each the latest first."""
    def row(kind, unit, stage, ended, value, limit, usd) -> dict[str, Any]:
        return {
            "kind": kind, "unit": unit, "stage": stage, "ended": ended,
            "value": value, "limit": limit,
            "usd": None if usd is None else round(usd, journal.USD_PLACES),
        }

    over = [
        row("over-budget", r["key"], "", None, r["usd"], BUDGET_USD, r["usd"])
        for r in unit_rows if r["over"]
    ]
    failed = [
        row("failed", str(r.get("unit") or ""), str(r.get("stage") or ""), r.get("at"),
            r.get("outcome"), None, _usd(r))
        for r in ends if r.get("outcome") in FAILED
    ]
    reruns = []
    for (unit, stage), steps in per_pair.items():
        limit = RERUN_LIMIT.get(stage, RERUN_DEFAULT)
        if len(steps) <= limit:
            continue
        acc = _zero()
        for r in steps:
            _add(acc, r)
        reruns.append(row("reruns", unit, stage, steps[-1].get("at"), len(steps), limit, acc["usd"]))

    valid: dict[str, list[tuple[dict[str, Any], float]]] = {}
    for r in ends:
        value = _per_turn(r)
        if value is not None:
            valid.setdefault(str(r.get("stage") or ""), []).append((r, value))
    heavy = []
    for stage, steps in valid.items():
        if len(steps) < TOKENS_PER_TURN_MIN_STEPS:
            continue
        limit = TOKENS_PER_TURN_TIMES * statistics.median(v for _, v in steps)
        for r, value in steps:
            if value > limit:
                heavy.append(row("tokens-per-turn", str(r.get("unit") or ""), stage, r.get("at"),
                                 value, limit, _usd(r)))

    out: list[dict[str, Any]] = []
    for group in (over, failed, reruns, heavy):
        out.extend(sorted(group, key=lambda a: str(a["ended"] or ""), reverse=True))
    return out
