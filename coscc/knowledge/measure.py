"""`coscc knowledge measure`: did the store make a unit take fewer turns?

While `COS_KNOWLEDGE` is on every unit is in the `on` or `off` arm (`knowledge.arm`), and every
`start` records which; this compares the arms over every stage from the recorded arm. Reads
`cos.db` read-only and imports nothing of the web app; the Knowledge page calls `measure` on
the same rows. Run it at a terminal: inside a step `cos.db` is a tripwire. The fields read here
are written by other modules: rename one there and this reads nothing, silently.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from coscc import knowledge, units
from coscc.knowledge import efforttrial, gather

GROUP = 10
TARGET = 0.20
DEADLINE = "2026-10-16"
TIMEZONE = "UTC"
DEFAULT_WORKSPACE = "coscc"

PASS, FAIL, SHORT = "đạt", "không đạt", "chưa đủ mẫu"
CHANGES_REQUESTED = "changes-requested"
KINDS = ("start", "end", gather.KIND)
ARMS = (knowledge.ON, knowledge.OFF)
# The first two are compared by median, the last two by mean.
METRICS = ("turns", "usd", "changes_requested", "ci_red")
# The gathers whose cost is the `on` arm's.
GATHER_MODES = ("unit", "new")


class Refused(Exception):
    """Exit 2 with this reason."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_rows(db: Path) -> list[dict[str, Any]]:
    """Every `start`, `end` and `knowledge` record, oldest first; unparseable rows are skipped."""
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    try:
        # First on the connection.
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
    """`--workspace`, or the one slot named `coscc`; anything else is refused, listing the slots."""
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
    """`{unit: {starts, ends, plan_done_at}}` of one workspace; `plan_done_at` is the first
    `end` of `plan` that is `done`."""
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


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _utc_day(at: Any) -> str:
    """The UTC day of an `at`, `""` when it does not read."""
    try:
        dt = datetime.fromisoformat(str(at or "").replace("Z", "+00:00"))
    except ValueError:
        return ""
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).date().isoformat()


def arm_of(start: dict[str, Any]) -> str | None:
    field = start.get(knowledge.TRIAL_FIELD)
    return field.get("arm") if isinstance(field, dict) else None


def _shipped_at(unit: dict[str, Any]) -> str | None:
    """The `at` of the last `end` of `ship` that is `done`."""
    found = [str(e.get("at") or "") for e in unit["ends"] if e.get("stage") == "ship" and e.get("outcome") == "done"]
    return max(found) if found else None


def failed(unit: dict[str, Any]) -> list[int]:
    """The numbers of the three inclusion conditions `unit` fails."""
    out = []
    arms = {arm_of(s) for s in unit["starts"]}
    if len(arms) != 1 or not arms <= set(ARMS):
        out.append(1)
    shipped = _shipped_at(unit)
    if not shipped or not _utc_day(shipped) or _utc_day(shipped) > DEADLINE:
        out.append(2)
    if not unit["ends"] or not all(_number(e.get("turns")) and _number(e.get("cost_usd")) for e in unit["ends"]):
        out.append(3)
    return out


def metrics(unit: dict[str, Any]) -> dict[str, Any]:
    """Sums over every stage."""
    return {
        "turns": sum(e["turns"] for e in unit["ends"]),
        "usd": round(sum(float(e["cost_usd"]) for e in unit["ends"]), 6),
        "changes_requested": sum(
            1 for e in unit["ends"] if e.get("stage") == "review" and isinstance(e.get("verdicts"), list)
            for v in e["verdicts"] if v == CHANGES_REQUESTED
        ),
        "ci_red": sum(1 for s in unit["starts"]
                      if s.get("stage") == "impl" and s.get(efforttrial.CI_RED) is True),
    }


def _side(each: dict[str, dict[str, Any]]) -> dict[str, Any]:
    names = sorted(each)
    return {
        "n": len(names),
        "units": names,
        "median": {m: (statistics.median(v[m] for v in each.values()) if names else None) for m in METRICS},
        "mean": {m: (round(statistics.mean(v[m] for v in each.values()), 4) if names else None) for m in METRICS},
    }


def measure(rows: list[dict[str, Any]], slot: str, today: str | None = None) -> dict[str, Any]:
    """The measure over one workspace's records, pure; `origin_main` and `health` are only read
    by the command."""
    found = units_of(rows, slot)
    each: dict[str, dict[str, dict[str, Any]]] = {arm: {} for arm in ARMS}
    excluded = []
    window: list[str] = []
    for name in sorted(found):
        unit = found[name]
        # Before the flag: in no arm, and not listed either.
        if not any(knowledge.TRIAL_FIELD in s for s in unit["starts"]):
            continue
        why = failed(unit)
        if why:
            excluded.append({"unit": name, "failed": why})
            continue
        each[arm_of(unit["starts"][0])][name] = metrics(unit)
        window += [min(str(s.get("at") or "") for s in unit["starts"]), str(_shipped_at(unit))]
    on, off = _side(each[knowledge.ON]), _side(each[knowledge.OFF])

    # What gathering cost while the counted units ran is the `on` arm's, per `on` unit.
    begin, end = (min(window), max(window)) if window else ("", "")
    gathered = round(sum(
        float(r["cost_usd"]) for r in rows
        if r.get("kind") == gather.KIND and r.get("mode") in GATHER_MODES and _number(r.get("cost_usd"))
        and window and begin <= str(r.get("at") or "") <= end
    ), 6)
    share = round(gathered / on["n"], 6) if on["n"] else None
    usd_on = round(on["median"]["usd"] + share, 6) if on["n"] else None

    reduction = None
    if on["n"] and off["n"] and off["median"]["turns"] > 0:
        reduction = round(1 - on["median"]["turns"] / off["median"]["turns"], 4)
    if on["n"] < GROUP or off["n"] < GROUP:
        verdict = SHORT
    elif (reduction is not None and reduction >= TARGET and usd_on <= off["median"]["usd"]
          and all(on["mean"][m] <= off["mean"][m] for m in ("changes_requested", "ci_red"))):
        verdict = PASS
    else:
        verdict = FAIL
    # The effort trial's arms are worked out from the name: that flag may be off, its field absent.
    crosstab = {arm: {e: 0 for e in (efforttrial.TRIAL_ARM, efforttrial.CONTROL_ARM)} for arm in ARMS}
    for arm in ARMS:
        for name in each[arm]:
            crosstab[arm][efforttrial.arm(name)] += 1
    today = today or _now()[:10]
    return {
        "workspace": slot,
        "deadline": DEADLINE,
        "timezone": TIMEZONE,
        "past_deadline": today > DEADLINE,
        "target": TARGET,
        "group": GROUP,
        "on": on,
        "off": off,
        "reduction": reduction,
        "window": {"from": begin, "to": end} if window else None,
        "gather_cost_usd": gathered,
        "gather_per_on_unit": share,
        "usd_on_with_gather": usd_on,
        "crosstab": crosstab,
        "excluded": excluded,
        "verdict": verdict,
    }


def _db(data_dir: str | os.PathLike[str] | None) -> Path:
    from coscc.data import Data

    return Data(data_dir).db_path


def run_measure(data_dir: str | os.PathLike[str] | None, wanted: str | None,
                say: Callable[[str], None] = print) -> int:
    """Fetch the workspace's `origin/main`, check the store on it, write `knowledge.HEALTH`, then
    print the measure. A failed fetch is `1` and prints no verdict."""
    from coscc.knowledge import admit

    db = _db(data_dir)
    if not db.is_file():
        say(f"coscc knowledge measure: no {db}")
        return 1
    try:
        rows = read_rows(db)
    except sqlite3.Error as e:
        say(f"coscc knowledge measure: the run log cannot be read: {e}")
        return 2
    try:
        slot = choose(rows, wanted)
    except Refused as e:
        say(f"coscc knowledge measure: {e}")
        return 2
    path = admit.workspaces([r for r in rows if r.get("kind") == "end"]).get(slot)
    if not path:
        say(f"coscc knowledge measure: no run of {slot} names a directory there now, so its origin/main "
            "cannot be fetched; no verdict")
        return 1
    try:
        sha = asyncio.run(admit.fresh_main(path))
    except admit.GitError as e:
        say(f"coscc knowledge measure: no verdict: {e}")
        return 1
    try:
        text = knowledge.load(knowledge.path_of(data_dir) / knowledge.STORE)
    except FileNotFoundError:
        text = ""
    except (OSError, UnicodeDecodeError) as e:
        say(f"coscc knowledge measure: the store cannot be read: {type(e).__name__}: {e}")
        return 2
    entries = knowledge.for_workspace(knowledge.parse(text)["entries"], slot)
    try:
        found = admit.health(entries, {slot: path}, admit.Reader("origin/main"))
    except admit.GitError as e:
        say(f"coscc knowledge measure: {e}")
        return 2
    admit.save_health(data_dir, {slot: sha}, found)
    result = measure(rows, slot)
    result["origin_main"] = sha
    result["health"] = {"entries": len(found), "broken": {k: v for k, v in sorted(found.items()) if v}}
    say(json.dumps(result, indent=2, ensure_ascii=False))
    return 0
