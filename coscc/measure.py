"""`coscc knowledge baseline` and `coscc knowledge measure`: did the store make planning cheaper?

`0090_agents-relearn-what-earlier-units-already-knew` R15-R17, and `spec.md ## Answers`,
câu 8, 9 and 10. Both read `cos.db` with `sqlite3` in `mode=ro`, as `scripts/verify_0093.py`
does, and the store's own three files; they import nothing of the web app. Run them at a
terminal: inside a step `cos.db` is a tripwire (`.claude/rules/coscc-sessions.md`).

The fields read here are the ones `coscc/runner.py` and `coscc/gather.py` write, by the
names in `coscc/knowledge.py` and `coscc/gather.py`: rename one there and this reads
nothing, silently (`.claude/rules/coscc-data.md`), which `coscc/measure_test.py` guards by
writing its fixture through the same names.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from coscc import gather, knowledge, units

# `intent.md`: 10 units a side, 20% cheaper by 2026-10-16. The day is read in UTC (plan
# Risk 9), and `spec.md ## Answers, câu 10`: the date does not move by itself.
GROUP = 10
TARGET = 0.20
DEADLINE = "2026-10-16"
TIMEZONE = "UTC"
DEFAULT_WORKSPACE = "coscc"

PASS, FAIL, SHORT = "đạt", "không đạt", "chưa đủ mẫu"
CHANGES_REQUESTED = "changes-requested"
KINDS = ("start", "end", gather.KIND)


class Refused(Exception):
    """Exit 2 with this reason."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_rows(db: Path) -> list[dict[str, Any]]:
    """Every `start`, `end` and `knowledge` record, oldest first, read-only. A row whose JSON
    does not parse is skipped, as `Journal.records` skips it."""
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    try:
        # First on the connection, as every connection here sets it (`coscc-app.md`).
        conn.execute("PRAGMA busy_timeout = 5000")
        found = conn.execute(
            f"SELECT id, record FROM runs WHERE kind IN ({','.join('?' * len(KINDS))}) ORDER BY id",
            KINDS,
        ).fetchall()
    finally:
        conn.close()
    rows = []
    for row_id, raw in found:
        try:
            record = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(record, dict):
            rows.append({**record, "_id": row_id})
    return rows


def _slot_of() -> Callable[[str], str]:
    cache: dict[str, str] = {}

    def slot(workspace: str) -> str:
        if workspace not in cache:
            cache[workspace] = units.slot(workspace) if workspace else ""
        return cache[workspace]

    return slot


def slots(rows: list[dict[str, Any]]) -> list[str]:
    slot = _slot_of()
    return sorted({slot(str(r.get("workspace") or "")) for r in rows if r.get("kind") in ("start", "end")} - {""})


def choose(rows: list[dict[str, Any]], wanted: str | None) -> str:
    """`--workspace`, or the one slot whose name is `coscc`; anything else is refused with
    the slots there are."""
    known = slots(rows)
    if wanted:
        if wanted not in known:
            raise Refused(f"no run of {wanted} in cos.db; the workspaces there are: {', '.join(known) or '(none)'}")
        return wanted
    named = [s for s in known if s.rsplit("-", 1)[0] == DEFAULT_WORKSPACE]
    if len(named) != 1:
        raise Refused(
            f"{len(named)} workspaces are named {DEFAULT_WORKSPACE}; say which with --workspace: "
            + (", ".join(known) or "(none)")
        )
    return named[0]


def units_of(rows: list[dict[str, Any]], slot: str) -> dict[str, dict[str, Any]]:
    """`{unit: {starts, ends, plan_done_at}}` of one workspace. `plan_done_at` is the first
    `end` of `plan` with outcome `done`."""
    of = _slot_of()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.get("kind") not in ("start", "end") or not r.get("unit"):
            continue
        if of(str(r.get("workspace") or "")) != slot:
            continue
        u = out.setdefault(str(r["unit"]), {"starts": [], "ends": [], "plan_done_at": None})
        u["starts" if r["kind"] == "start" else "ends"].append(r)
        if r["kind"] == "end" and r.get("stage") == "plan" and r.get("outcome") == "done" and not u["plan_done_at"]:
            u["plan_done_at"] = str(r.get("at") or "")
    return out


def _entries(start: dict[str, Any]) -> int:
    field = start.get("knowledge")
    if not isinstance(field, dict):
        return 0
    try:
        return int(field.get("entries") or 0)
    except (TypeError, ValueError):
        return 0


def state(unit: dict[str, Any]) -> str:
    """`off`: no `start` of `spec`, `spike` or `plan` carries `knowledge`. `on`: every one
    carries it with `entries > 0`. `mixed`: anything else (R16)."""
    starts = [r for r in unit["starts"] if r.get("stage") in knowledge.STAGES]
    if not any("knowledge" in r for r in starts):
        return "off"
    if starts and all(_entries(r) > 0 for r in starts):
        return "on"
    return "mixed"


def pick_baseline(rows: list[dict[str, Any]], slot: str) -> list[dict[str, str]]:
    """R15. The last `GROUP` units, by when `plan` was done, that ran with the flag off."""
    found = units_of(rows, slot)
    done = sorted(
        ((u["plan_done_at"], name) for name, u in found.items() if u["plan_done_at"] and state(u) == "off"),
    )
    return [{"unit": name, "plan_done_at": at} for at, name in done[-GROUP:]]


def _money(value: float) -> float:
    return round(value, 6)


def group(found: dict[str, dict[str, Any]], names: list[str]) -> dict[str, Any]:
    """R16, R17 for one side."""
    cost, unknown, spikes = 0.0, 0, 0
    reviewed, requested = 0, 0
    for name in names:
        u = found.get(name) or {"starts": [], "ends": []}
        for e in u["ends"]:
            if e.get("stage") in knowledge.STAGES:
                value = e.get("cost_usd")
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    cost += float(value)
                else:
                    unknown += 1
        spikes += sum(1 for s in u["starts"] if s.get("stage") == "spike")
        # `spec.md ## Answers, câu 8`: only units that have had at least one round count.
        rounds = [v for e in u["ends"] if e.get("stage") == "review" and isinstance(e.get("verdicts"), list)
                  for v in e["verdicts"]]
        if any(e.get("stage") == "review" and isinstance(e.get("verdicts"), list) for e in u["ends"]):
            reviewed += 1
            requested += sum(1 for v in rounds if v == CHANGES_REQUESTED)
    n = len(names)
    return {
        "units": list(names),
        "n": n,
        "cost_usd": _money(cost),
        "cost_unknown": unknown,
        "cost_per_unit": _money(cost / n) if n else None,
        "spikes_per_unit": round(spikes / n, 4) if n else None,
        "review": {"n": reviewed, "changes_requested_per_unit": round(requested / reviewed, 4) if reviewed else None},
    }


def _reduction(off: float | None, on: float | None) -> float | None:
    if off is None or on is None or off <= 0:
        return None
    return round(1 - on / off, 4)


def measure(rows: list[dict[str, Any]], baseline: dict[str, Any], today: str | None = None) -> dict[str, Any]:
    """R16, R17 and `## Answers` câu 8-10. Raises `Refused` when the baseline was written after
    the first step that carried the store."""
    slot = str(baseline.get("workspace") or "")
    of = _slot_of()
    flagged = [
        str(r.get("at") or "") for r in rows
        if r.get("kind") == "start" and of(str(r.get("workspace") or "")) == slot and _entries(r) > 0
    ]
    first_on = min(flagged) if flagged else None
    written = str(baseline.get("written_at") or "")
    if first_on is not None and written > first_on:
        raise Refused(
            f"the baseline was written at {written}, after the first step that carried the store "
            f"({first_on}): it has to be chosen before the flag is turned on"
        )

    found = units_of(rows, slot)
    off_names = [str(u.get("unit")) for u in baseline.get("units") or []]
    on_all = sorted(
        (u["plan_done_at"], name) for name, u in found.items()
        if u["plan_done_at"] and state(u) == "on" and u["plan_done_at"][:10] <= DEADLINE
    )
    on_names = [name for _, name in on_all[:GROUP]]
    mixed = sorted(name for name, u in found.items() if u["plan_done_at"] and state(u) == "mixed")
    off, on = group(found, off_names), group(found, on_names)

    # `## Answers, câu 9`: gathering in the window is the flag's cost; the backfill is not.
    window = None
    gathered = 0.0
    if on_names:
        begin = min(str(s.get("at") or "") for n in on_names for s in found[n]["starts"]
                    if s.get("stage") in knowledge.STAGES)
        end = max(found[n]["plan_done_at"] for n in on_names)
        window = {"from": begin, "to": end}
        gathered = sum(
            float(r.get("cost_usd") or 0.0) for r in rows
            if r.get("kind") == gather.KIND and r.get("mode") == "new" and begin <= str(r.get("at") or "") <= end
        )
    backfill = sum(float(r.get("cost_usd") or 0.0) for r in rows
                   if r.get("kind") == gather.KIND and r.get("mode") == "all")
    on_with = _money((on["cost_usd"] + gathered) / on["n"]) if on["n"] else None
    reduction = _reduction(off["cost_per_unit"], on["cost_per_unit"])
    reduction_with = _reduction(off["cost_per_unit"], on_with)
    saving = (off["cost_per_unit"] - on_with) if off["cost_per_unit"] is not None and on_with is not None else None
    if saving is None:
        even, even_why = None, "a side has no unit"
    elif saving <= 0:
        even, even_why = None, f"the flag saves nothing per unit ({_money(saving)} USD)"
    else:
        even, even_why = round(backfill / saving, 2), ""

    if off["n"] < GROUP or on["n"] < GROUP or not off["review"]["n"] or not on["review"]["n"]:
        verdict = SHORT
    elif (reduction_with is not None and reduction_with >= TARGET
          and on["review"]["changes_requested_per_unit"] <= off["review"]["changes_requested_per_unit"]):
        verdict = PASS
    else:
        verdict = FAIL
    today = today or _now()[:10]
    return {
        "workspace": slot,
        "baseline_written_at": written,
        "deadline": DEADLINE,
        "timezone": TIMEZONE,
        "past_deadline": today > DEADLINE,
        "target": TARGET,
        "off": off,
        "on": on,
        "mixed": mixed,
        "reduction": reduction,
        "window": window,
        "gather_cost_usd": _money(gathered),
        "reduction_with_gather": reduction_with,
        "backfill_cost_usd": _money(backfill),
        "break_even_units": even,
        **({"break_even_reason": even_why} if even_why else {}),
        "verdict": verdict,
    }


def _db(data_dir: str | os.PathLike[str] | None) -> Path:
    from coscc.data import Data

    return Data(data_dir).db_path


def run_baseline(data_dir: str | os.PathLike[str] | None, wanted: str | None,
                 say: Callable[[str], None] = print) -> int:
    path = knowledge.path_of(data_dir) / knowledge.BASELINE
    if path.exists():
        say(f"coscc knowledge baseline: {path} already exists, and a baseline is never overwritten")
        return 2
    db = _db(data_dir)
    if not db.is_file():
        say(f"coscc knowledge baseline: no {db}")
        return 1
    rows = read_rows(db)
    try:
        slot = choose(rows, wanted)
    except Refused as e:
        say(f"coscc knowledge baseline: {e}")
        return 2
    picked = pick_baseline(rows, slot)
    record = {"written_at": _now(), "workspace": slot, "n": len(picked), "units": picked}
    knowledge.save(path, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    say(f"coscc knowledge baseline: {len(picked)} unit(s) of {slot} written to {path}"
        + ("" if len(picked) >= GROUP else f" — fewer than {GROUP}"))
    return 0


def run_measure(data_dir: str | os.PathLike[str] | None, wanted: str | None,
                say: Callable[[str], None] = print) -> int:
    path = knowledge.path_of(data_dir) / knowledge.BASELINE
    try:
        baseline = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        say(f"coscc knowledge measure: no baseline at {path} — run `coscc knowledge baseline` "
            "before turning COS_KNOWLEDGE on")
        return 2
    except ValueError as e:
        say(f"coscc knowledge measure: {path} is not JSON: {e}")
        return 1
    if wanted and wanted != baseline.get("workspace"):
        say(f"coscc knowledge measure: the baseline is of {baseline.get('workspace')}, not {wanted}")
        return 2
    db = _db(data_dir)
    if not db.is_file():
        say(f"coscc knowledge measure: no {db}")
        return 1
    try:
        result = measure(read_rows(db), baseline)
    except Refused as e:
        say(f"coscc knowledge measure: {e}")
        return 2
    say(json.dumps(result, indent=2, ensure_ascii=False))
    return 0
