"""What an `impl` step costs, and whether units ship on fewer of them (`0096` R1, R3, R4, R7).

`0096_an-impl-session-grows-until-every-turn-is-expensive`. Reads `cos.db` and the unit store
and prints one JSON object: turns, cost and time per `impl` step, the share of shipped units
that ran `impl` twice or more, and the `changes-requested` rounds per shipped unit. With
`--outcome` it then says whether R7 holds.

    uv run python -m coscc.turnstats --workspace <path> [--since T] [--until T] [--outcome]
        [--files PATH... [--first N]]

`--files` adds `0095` R9's `touched_*` fields: the `impl` steps that aimed a `Read` or `Grep`
at one of those paths, and over the first N of them their turns, those calls and what each
such `Read` returned. `touched_purged` counts the steps whose events the app had already
purged, which none of those fields can see: measure a window before its runs age out.

Run it at a terminal. A board step carries this app's `cos.db` in `COSCC_PROTECTED_DB`, and
this command opens the file with `sqlite3` rather than through `Data`, so it asks the same
question `Data.connect` does before it opens anything, and exits 2 on a listed database. It
opens the file read-only, creates no schema and writes nothing.

`--until` is exclusive. Times are compared as the strings the run log stores. A step is
picked by its `start`'s time and paired with it the way `spike.md ## Probe code` `pairs.py`
does: every `impl` `end` with the latest earlier `start` of the same `(root, workspace,
unit)`, and its own time when that `start` is not `impl`'s.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
import sys
from pathlib import Path
from typing import Any

from coscc import config, units
from coscc.data import DB_FILENAME, DEFAULT_DIR

# `spec.md` R7. `reimpl_share` is 34/47 and `duration_mean_ms` R4's mark, both from
# `spike.md ## U3`; the others are `intent.md ## Answers`.
OUTCOME: tuple[tuple[str, float], ...] = (
    ("turns_mean", 49.5),
    ("over100_share", 0.116),
    ("cost_mean", 2.205),
    ("duration_mean_ms", 660267),
    ("reimpl_share", 34 / 47),
    ("changes_requested_mean", 0.91),
)

_ROUND = re.compile(r"^## Round \d+", re.M)
_VERDICT = re.compile(r"Verdict:\s*([\w-]+)")


class Refused(RuntimeError):
    """A database this command must not open, or cannot."""


def _in(at: str, since: str | None, until: str | None) -> bool:
    return (since is None or at >= since) and (until is None or at < until)


def _mean(values: list[float], digits: int | None) -> float | None:
    return round(statistics.mean(values), digits) if values else None


def open_db(data_root: str | Path) -> sqlite3.Connection:
    """`<data_root>/cos.db`, read-only. Refused before anything is opened when
    `config.protected_databases()` lists it, as `Data.connect` refuses (`0076` R5). The file
    is resolved, not only its directory, so a `cos.db` that links to a listed one is refused."""
    db = (Path(data_root).expanduser() / DB_FILENAME).resolve()
    if db in config.protected_databases():
        raise Refused(
            f"{db} belongs to the app that started this process; "
            f"{config.PROTECTED_DB_VAR} lists it, so it is not opened here. Run this at a terminal."
        )
    if not db.is_file():
        raise Refused(f"{db} does not exist")
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def pairs(
    conn: sqlite3.Connection, workspace: str, since: str | None, until: str | None
) -> list[dict[str, Any]]:
    """`{unit, start, end}` for every `impl` step of `workspace` whose time is in the window."""
    rows = conn.execute(
        "SELECT at, root, unit, stage, kind, record FROM runs "
        "WHERE kind IN ('start', 'end') AND workspace = ? ORDER BY id",
        (workspace,),
    ).fetchall()
    last: dict[tuple[str, str], tuple[str, str, dict[str, Any]]] = {}
    out: list[dict[str, Any]] = []
    for at, root, unit, stage, kind, record in rows:
        if kind == "start":
            last[(root, unit)] = (at, stage, json.loads(record))
            continue
        if stage != "impl":
            continue
        start = last.get((root, unit))
        s_at, s_rec = (start[0], start[2]) if start and start[1] == "impl" else (at, {})
        if _in(s_at, since, until):
            out.append({"unit": unit, "start": s_rec, "end": json.loads(record)})
    return out


def step_fields(steps: list[dict[str, Any]]) -> dict[str, Any]:
    """R1. A step whose `end` carries no `turns` (a `failed` one) is 0 turns; tokens per turn
    are over the steps with at least one."""
    turns = [p["end"].get("turns") or 0 for p in steps]
    cost = [p["end"].get("cost_usd") or 0 for p in steps]
    duration = [p["end"].get("duration_ms") or 0 for p in steps]
    per_turn = [
        (e.get("input_tokens", 0) + e.get("cache_creation_tokens", 0) + e.get("cache_read_tokens", 0)) / t
        for e, t in ((p["end"], p["end"].get("turns") or 0) for p in steps)
        if t
    ]
    over = sum(1 for t in turns if t > 100)
    return {
        "steps": len(steps),
        "turns_mean": _mean(turns, 2),
        "over100": over,
        "over100_share": round(over / len(steps), 4) if steps else None,
        "cost_mean": _mean(cost, 4),
        "cost_median": round(statistics.median(cost), 4) if cost else None,
        "duration_mean_ms": _mean(duration, None),
        "tokens_per_turn_mean": _mean(per_turn, None),
        "tokens_per_turn_max": round(max(per_turn)) if per_turn else None,
    }


def event_fields(conn: sqlite3.Connection, steps: list[dict[str, Any]]) -> dict[str, Any]:
    """R8's after-ship figure: `tool_use` events per `turn` event, over the steps whose `run`
    still has events; and R11's, how many steps carried each new part of the prompt."""
    event_steps = turn_events = tool_uses = 0
    for p in steps:
        run = p["end"].get("run")
        if not run:
            continue
        counts = dict(conn.execute(
            "SELECT kind, count(*) FROM step_events WHERE run = ? AND kind IN ('turn', 'tool_use') "
            "GROUP BY kind",
            (run,),
        ).fetchall())
        if not counts:
            continue
        event_steps += 1
        turn_events += counts.get("turn", 0)
        tool_uses += counts.get("tool_use", 0)
    included = [p["start"].get("included") or [] for p in steps]
    return {
        "event_steps": event_steps,
        "turn_events": turn_events,
        "tool_uses": tool_uses,
        "tool_uses_per_turn": round(tool_uses / turn_events, 2) if turn_events else None,
        "carried_plan_map": sum(1 for i in included if "plan-map" in i),
        "carried_commands": sum(1 for i in included if "commands" in i),
    }


def _aimed_at(tool_input: Any, files: list[str]) -> bool:
    """`0095` R9: a `Read` or `Grep` input whose `file_path` or `path` ends in `/<file>`. A
    `Grep` with no `path`, or one on a directory, names none of `files`."""
    if not isinstance(tool_input, dict):
        return False
    target = tool_input.get("file_path") or tool_input.get("path")
    return isinstance(target, str) and any(target.endswith("/" + f) for f in files)


def _result_chars(event: dict[str, Any]) -> int:
    """What a `tool_result` carried back: the CLI's own size when it persisted the output,
    the length before `events._cut` when the content was cut, else the content's length."""
    if isinstance(event.get("persisted_size"), int):
        return event["persisted_size"]
    if "content" in (event.get("truncated_fields") or []) and isinstance(event.get("length"), int):
        return event["length"]
    content = event.get("content")
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(b.get("text") or "") for b in content if isinstance(b, dict))
    return 0


def file_fields(
    conn: sqlite3.Connection, steps: list[dict[str, Any]], files: list[str], first: int
) -> dict[str, Any]:
    """`0095` R9: how many steps aimed a `Read` or `Grep` at one of `files`, and, over the
    first `first` of them in `start` order, their turns, those calls per step, and the
    characters per such `Read`. A step whose events `events.purge` deleted cannot be told
    either way; `touched_purged` counts those, since the sample slides past them."""
    touched: list[tuple[int, int, list[int]]] = []
    purged = 0
    for p in steps:
        run = p["end"].get("run")
        if not run:
            continue
        index = conn.execute("SELECT purged_at FROM step_runs WHERE run = ?", (run,)).fetchone()
        if index and index[0]:
            purged += 1
            continue
        reads: dict[str, bool] = {}
        results: dict[str, int] = {}
        for kind, raw in conn.execute(
            "SELECT kind, event FROM step_events WHERE run = ? AND kind IN ('tool_use', 'tool_result') "
            "ORDER BY seq",
            (run,),
        ).fetchall():
            event = json.loads(raw)
            if kind == "tool_result":
                results[event.get("tool_use_id")] = _result_chars(event)
            elif event.get("name") in ("Read", "Grep") and _aimed_at(event.get("input"), files):
                reads[event.get("id")] = event.get("name") == "Read"
        if not reads:
            continue
        chars = [results.get(i, 0) for i, is_read in reads.items() if is_read]
        touched.append((p["end"].get("turns") or 0, len(reads), chars))
    sample = touched[:first]
    return {
        "touched_steps": len(touched),
        "touched_turns_mean": _mean([t for t, _, _ in sample], 2),
        "touched_reads_greps_mean": _mean([n for _, n, _ in sample], 2),
        "touched_read_chars_mean": _mean([c for _, _, cs in sample for c in cs], None),
        "touched_n": len(sample),
        "touched_purged": purged,
    }


def changes_requested(text: str) -> int:
    """The rounds of a `review.md` whose first `Verdict:` is `changes-requested`."""
    parts = _ROUND.split(text)[1:]
    firsts = [_VERDICT.search(part) for part in parts]
    return sum(1 for m in firsts if m and m.group(1) == "changes-requested")


def quality_fields(
    conn: sqlite3.Connection, workspace: str, cos_dir: Path, since: str | None, until: str | None
) -> dict[str, Any]:
    """R3. Shipped is a `ship.md` → `accepted` transition in the window; (a) counts only the
    `impl` starts in the window, (b) reads `review.md` from the unit store."""
    shipped = sorted({
        unit for unit, at in conn.execute(
            "SELECT unit, at FROM transitions "
            "WHERE workspace = ? AND artifact = 'ship.md' AND to_state = 'accepted'",
            (workspace,),
        ).fetchall()
        if _in(at, since, until)
    })
    starts: dict[str, int] = {}
    for unit, at in conn.execute(
        "SELECT unit, at FROM runs WHERE workspace = ? AND kind = 'start' AND stage = 'impl'",
        (workspace,),
    ).fetchall():
        if _in(at, since, until):
            starts[unit] = starts.get(unit, 0) + 1
    reimpl = sum(1 for u in shipped if starts.get(u, 0) >= 2)
    rounds = missing = 0
    for unit in shipped:
        try:
            rounds += changes_requested((cos_dir / unit / "review.md").read_text(encoding="utf-8"))
        except FileNotFoundError:
            missing += 1
    read = len(shipped) - missing
    return {
        "shipped": len(shipped),
        "shipped_reimpl": reimpl,
        "reimpl_share": round(reimpl / len(shipped), 4) if shipped else None,
        "changes_requested_rounds": rounds,
        "changes_requested_mean": round(rounds / read, 2) if read else None,
        "reviews_missing": missing,
    }


def measure(
    workspace: str, data_root: str | Path, since: str | None, until: str | None,
    files: list[str] | None = None, first: int = 20,
) -> dict[str, Any]:
    key = units.key(workspace)
    conn = open_db(data_root)
    try:
        steps = pairs(conn, key, since, until)
        return {
            **step_fields(steps),
            **quality_fields(conn, key, units.cos_dir(key, data_root), since, until),
            **event_fields(conn, steps),
            **(file_fields(conn, steps, files, first) if files else {}),
        }
    finally:
        conn.close()


def outcome(fields: dict[str, Any]) -> list[str]:
    """R7's conditions `fields` misses, `[]` when it holds. A figure with nothing under it —
    no unit shipped, no review read — is not over its mark; no step at all is."""
    missed = [] if (fields.get("steps") or 0) >= 1 else ["steps 0 < 1"]
    for name, mark in OUTCOME:
        value = fields.get(name)
        if value is not None and value > mark:
            missed.append(f"{name} {value} > {round(mark, 4)}")
    return missed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m coscc.turnstats", description=__doc__.split("\n\n")[0])
    parser.add_argument("--workspace", required=True, help="the repository the steps ran for")
    parser.add_argument("--since", help="first start time counted, as the run log writes it")
    parser.add_argument("--until", help="first start time not counted")
    parser.add_argument("--data-root", default=DEFAULT_DIR, help=f"where cos.db is (default {DEFAULT_DIR})")
    parser.add_argument("--outcome", action="store_true", help="then say whether spec.md R7 holds")
    parser.add_argument("--files", nargs="+", metavar="PATH",
                        help="paths from the repository's root; adds the touched_* fields (0095 R9)")
    parser.add_argument("--first", type=int, default=20, help="touched steps the touched_* means are over")
    args = parser.parse_args(argv)
    try:
        fields = measure(args.workspace, args.data_root, args.since, args.until, args.files, args.first)
    except (Refused, sqlite3.Error) as e:
        print(f"turnstats: {e}", file=sys.stderr)
        return 2
    print(json.dumps(fields, indent=1, ensure_ascii=False))
    if not args.outcome:
        return 0
    missed = outcome(fields)
    print("đạt" if not missed else "không đạt: " + "; ".join(missed))
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
