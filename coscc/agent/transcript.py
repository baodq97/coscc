"""Reading a session's transcript at the moment an update cut it (`0138`).

Pure: every function here reads, none writes. The CLI keeps a session as JSONL under
`~/.claude/projects`, outside `COS_DATA_DIR` (`0138 spike.md ## U2`, point 1), one line per
block: an assistant turn that calls two tools is two lines with one `message.id`.

The **boundary** is how many whole lines the file had just before `interrupt()`. Everything
the CLI writes after it -- a `tool_result` saying "The user doesn't want to proceed…", the
`[Request interrupted…]` line, the `cost-state` of its exit -- is the interruption's own and
is never counted as the session's work (`0138` R4).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# How much of a dropped call's input a record keeps (`0138` R6: "phần đầu của input").
INPUT_HEAD = 200


class Unreadable(Exception):
    """A line before the boundary that is not JSON (`0138` R8)."""


def projects_root() -> Path:
    return Path.home() / ".claude" / "projects"


def path_for(cwd: str, session_id: str, root: Path | None = None) -> Path:
    """Where the CLI keeps `session_id` for a session run in `cwd`.

    Every character that is not a letter or a digit becomes `-`, `_` and `.` included: a
    copy placed under a name that kept `_` was not found, and the CLI wrote into the same id
    in another project instead (`0138 spike.md ## U5`, "Sự cố").
    """
    return (root or projects_root()) / re.sub(r"[^A-Za-z0-9]", "-", cwd) / f"{session_id}.jsonl"


def boundary(path: Path) -> int:
    """How many whole lines `path` holds now; `0` when it is not there."""
    try:
        return Path(path).read_bytes().count(b"\n")
    except OSError:
        return 0


def _lines(path: Path) -> list[bytes]:
    return Path(path).read_bytes().split(b"\n")


def _entries(path: Path, until: int) -> list[dict[str, Any]]:
    out = []
    for n, raw in enumerate(_lines(path)[:until], start=1):
        if not raw.strip():
            continue
        try:
            item = json.loads(raw)
        except ValueError as e:
            raise Unreadable(f"line {n} of {path} is not JSON: {e}") from e
        if isinstance(item, dict):
            out.append(item)
    return out


def _blocks(entry: dict[str, Any]) -> list[Any]:
    content = (entry.get("message") or {}).get("content")
    return content if isinstance(content, list) else []


def _dropped(block: dict[str, Any]) -> dict[str, str]:
    given = block.get("input")
    if isinstance(given, dict) and isinstance(given.get("command"), str):
        text = given["command"]
    else:
        text = json.dumps(given, ensure_ascii=False)
    return {"name": str(block.get("name") or ""), "input": text[:INPUT_HEAD]}


def cut(path: Path, until: int) -> dict[str, Any]:
    """The safe point of `path` read up to `until` whole lines, and what lies past it.

    `safe_uuid` is the last user entry such that every `tool_use` before it has its
    `tool_result` before the boundary: a `tool_result` or a turn's prompt (R4). A turn left
    half answered, a parallel one included, is dropped whole. `None` when no user entry is.

    `dropped` is every `tool_use` after the safe point; `api_calls` the distinct
    `message.id`s of the assistant lines before the boundary, the call under way included
    (R10, spec C7); `pieces` the assistant's text up to the safe point, split at each tool
    call the way `Runner` splits a reply.
    """
    entries = [e for e in _entries(path, until) if not e.get("isSidechain")]
    # Every call is opened before its result, so one walk in order knows at each user entry
    # whether a call before it is still waiting.
    open_calls: set[str] = set()
    safe = -1
    for i, e in enumerate(entries):
        if e.get("type") == "assistant":
            for b in _blocks(e):
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    open_calls.add(str(b.get("id")))
        elif e.get("type") == "user":
            open_calls -= {str(b.get("tool_use_id")) for b in _blocks(e)
                           if isinstance(b, dict) and b.get("type") == "tool_result"}
            if not open_calls:
                safe = i
    pieces = [""]
    dropped: list[dict[str, str]] = []
    ids: set[str] = set()
    for i, e in enumerate(entries):
        if e.get("type") != "assistant":
            continue
        mid = (e.get("message") or {}).get("id")
        if mid:
            ids.add(str(mid))
        for b in _blocks(e):
            if not isinstance(b, dict):
                continue
            if i < safe:
                if b.get("type") == "text":
                    pieces[-1] += str(b.get("text") or "")
                elif b.get("type") in ("tool_use", "server_tool_use"):
                    pieces.append("")
            elif b.get("type") == "tool_use":
                dropped.append(_dropped(b))
    return {
        "safe_uuid": entries[safe].get("uuid") if safe >= 0 else None,
        "dropped": dropped,
        "api_calls": len(ids),
        "pieces": pieces,
    }


def ceilings_left(
    max_turns: int, max_budget_usd: float | None, record: dict[str, Any]
) -> tuple[int, float | None, str]:
    """`0138` R10. `(max_turns, max_budget_usd, used_up)` for a session taken up again.

    The CLI counts both ceilings from zero in every process (`spike.md ## U4`), so what the
    part before the cut used is taken off the grant's: its API calls, and what it had cost
    when the CLI exited. With that cost unknown the budget is the grant's whole (spec C9).
    `used_up` is the `terminal` a step reaching its ceiling today would have, or `""`.
    """
    turns = int(max_turns) - int(record.get("api_calls") or 0)
    budget = float(max_budget_usd) if max_budget_usd else None
    if budget is not None and record.get("spent_usd") is not None:
        budget = round(budget - float(record["spent_usd"]), 6)
    used_up = (
        "error_max_turns" if turns <= 0
        else "error_max_budget_usd" if budget is not None and budget <= 0
        else ""
    )
    return turns, budget, used_up


def spent_after(path: Path, until: int) -> float | None:
    """`totalCostUSD` of the last `cost-state` line past the boundary: what the whole session
    had cost when the CLI exited (`spike.md ## U4`). `None` when the CLI wrote none, as one
    killed with SIGKILL does -- `cost_unknown` (R11)."""
    try:
        lines = _lines(path)[until:]
    except OSError:
        return None
    found = None
    for raw in lines:
        try:
            item = json.loads(raw)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get("type") == "cost-state":
            try:
                found = float(item.get("totalCostUSD"))
            except (TypeError, ValueError):
                continue
    return found
