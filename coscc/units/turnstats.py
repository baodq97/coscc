"""Reads of the run log that `features/codegraph` measures a graph's effect with.

`pairs` gives every `impl` step of a workspace as its `start` and `end` record, `impl_ends` and
`read_chars` say what each step's `Read`s and an MCP server's tools returned, and `shipped_units` and
`changes_requested` count a unit's review rounds. Times are compared as the strings the run log
stores; `until` is exclusive. A step is picked by its `start`'s time and paired with it: every `impl`
`end` with the latest earlier `start` of the same `(root, workspace, unit)`, and its own time when that
`start` is not `impl`'s.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

_ROUND = re.compile(r"^## Round \d+", re.M)
_VERDICT = re.compile(r"Verdict:\s*([\w-]+)")


def _in(at: str, since: str | None, until: str | None) -> bool:
    return (since is None or at >= since) and (until is None or at < until)


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


def read_chars(
    conn: sqlite3.Connection, runs: list[str], server: str
) -> dict[str, tuple[int, int]]:
    """`{run: (read, served)}` for each of `runs` (an `end`'s `run`) whose events remain: the
    characters every `Read` returned, and those every tool of the MCP `server`
    (`mcp__<server>__*`) returned, counted as `file_fields` counts them. An empty `run`, or one
    whose events `events.purge` deleted, is not in it."""
    prefix = f"mcp__{server}__"
    out: dict[str, tuple[int, int]] = {}
    for run in runs:
        if not run:
            continue
        index = conn.execute("SELECT purged_at FROM step_runs WHERE run = ?", (run,)).fetchone()
        if index and index[0]:
            continue
        names: dict[str, str] = {}
        read = served = 0
        for kind, raw in conn.execute(
            "SELECT kind, event FROM step_events WHERE run = ? AND kind IN ('tool_use', 'tool_result') "
            "ORDER BY seq",
            (run,),
        ).fetchall():
            event = json.loads(raw)
            if kind == "tool_use":
                names[event.get("id")] = str(event.get("name") or "")
                continue
            name = names.get(event.get("tool_use_id"), "")
            if name == "Read":
                read += _result_chars(event)
            elif name.startswith(prefix):
                served += _result_chars(event)
        out[run] = (read, served)
    return out


def impl_ends(conn: sqlite3.Connection, workspace: str) -> list[tuple[str, str, bool]]:
    """`(run, unit, purged)` of every step `pairs` gives with no window, from one query and no
    event read: whether `read_chars` has the run is `not purged`. An empty `run` is not in it."""
    rows = conn.execute(
        "SELECT json_extract(r.record, '$.run'), r.unit, s.purged_at FROM runs r "
        "LEFT JOIN step_runs s ON s.run = json_extract(r.record, '$.run') "
        "WHERE r.workspace = ? AND r.kind = 'end' AND r.stage = 'impl' ORDER BY r.id",
        (workspace,),
    ).fetchall()
    return [(str(run), unit, bool(purged)) for run, unit, purged in rows if run]


def changes_requested(text: str) -> int:
    """The rounds of a `review.md` whose first `Verdict:` is `changes-requested`."""
    parts = _ROUND.split(text)[1:]
    firsts = [_VERDICT.search(part) for part in parts]
    return sum(1 for m in firsts if m and m.group(1) == "changes-requested")


def shipped_units(
    conn: sqlite3.Connection, workspace: str, since: str | None, until: str | None
) -> list[str]:
    """The units with a `ship.md` -> `accepted` transition in the window, sorted."""
    return sorted(
        {
            unit
            for unit, at in conn.execute(
                "SELECT unit, at FROM transitions "
                "WHERE workspace = ? AND artifact = 'ship.md' AND to_state = 'accepted'",
                (workspace,),
            ).fetchall()
            if _in(at, since, until)
        }
    )
