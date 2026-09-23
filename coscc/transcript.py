"""Counting what a session's own transcript says happened, after the fact.

`spec.md` R3: the two ceilings in `intent.md`'s outcome (tiêu chí 1 and 2) have to be
countable from a session that already ran, because the transcript that would prove them is
never committed here (`.claude/CLAUDE.md`, `intent.md ## Constraints`). This module reads
the CLI's own record of a conversation — one JSON object per line, `~/.claude/projects/
<cwd-as-slug>/<session-id>.jsonl` — and counts three things: how many tool results were
errors the app's own grant produced, how many were something else, and how large the
largest tool result was.

**Pure.** Nothing here opens a session or spends anything; the transcript is handed in as
lines already read. `scripts/count_session.py` is the thin command that reads the file and
prints what this module counted.
"""

from __future__ import annotations

import json
from pathlib import Path

from coscc import policy


def _text_of(content: object) -> str:
    """Flatten a stored tool result's `content` to text.

    Same shape as `coscc/sessions.py` `_text_of`: the CLI stores it either as a bare string
    or as a list of blocks, and only text is kept.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return ""


def summarize(lines: list[str]) -> dict:
    """Count grant refusals, other errors, and the largest tool result, over one transcript.

    `lines` is the transcript file's own lines, in order — each a JSON object. A `tool_use`
    block names the tool a later `tool_result` (matched by `tool_use_id`) belongs to; a
    `tool_use` always precedes the `tool_result` that answers it in a transcript written in
    real time, so one forward pass is enough.

    A result counts as a grant refusal exactly when it is marked `is_error` and its text
    contains one of `policy.REFUSALS` — the same prefixes `check_command` and `decide`
    build every refusal from (`policy.py` R3/C9). Every other `is_error` result is "other".
    The largest-result count is over every tool result, error or not, because R9/R10 bound
    what a session reads back regardless of whether the call itself failed.
    """
    names: dict[str, str] = {}
    grant_refusals = 0
    other_errors = 0
    largest_chars = 0
    largest_tool = ""

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        content = (entry.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "tool_use":
                tool_id = block.get("id")
                if tool_id:
                    names[str(tool_id)] = str(block.get("name") or "")
            elif kind == "tool_result":
                tool = names.get(str(block.get("tool_use_id") or ""), "")
                text = _text_of(block.get("content"))
                size = len(text)
                if size > largest_chars:
                    largest_chars, largest_tool = size, tool
                if block.get("is_error"):
                    if any(prefix in text for prefix in policy.REFUSALS):
                        grant_refusals += 1
                    else:
                        other_errors += 1

    return {
        "grant_refusals": grant_refusals,
        "other_errors": other_errors,
        "largest_result_chars": largest_chars,
        "largest_result_tool": largest_tool,
    }


def find(session_id: str, root: str | Path | None = None) -> Path | None:
    """The transcript file for one session id, or `None` when there is no such file.

    `root` defaults to `~/.claude/projects` — where the CLI keeps one directory per project
    `cwd`, each holding `<session-id>.jsonl`. A caller in a test hands in a temporary root
    instead of monkeypatching `HOME`.
    """
    base = Path(root) if root is not None else Path.home() / ".claude" / "projects"
    matches = sorted(base.glob(f"*/{session_id}.jsonl"))
    return matches[0] if matches else None
