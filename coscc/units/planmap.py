"""The files a plan changes, as they stand, for its `impl` step.

Lists each file the plan's `## Files that change` names with its line count and, for Python
and JavaScript, the line each top-level and class-level definition starts on. It returns the
prompt section and the record `Runner.run` puts into `start`. It reads only inside the tree.
"""

from __future__ import annotations

import io
import os
import re
import tokenize
from pathlib import Path
from typing import Any

from coscc.units.autopilot import files_of
from coscc.git.drift import files_section

# The bytes of the whole section, advice aside: twice `priorfindings.CAP_BYTES`.
CAP_BYTES = 12288

_PY = re.compile(r"^(async\s+def|def|class)\s+(\w+)")
_PY_METHOD = re.compile(r"^    (async\s+def|def)\s+(\w+)")
_JS = (
    re.compile(r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?(function)\*?\s*(\w+)"),
    re.compile(r"^(?:export\s+)?(?:default\s+)?(class)\s+(\w+)"),
    re.compile(r"^(?:export\s+)?(const)\s+(\w+)\s*="),
)


def _empty() -> dict[str, Any]:
    return {"bytes": 0, "files": 0, "full": 0, "short": 0, "new": 0, "outside": 0}


def _statements(text: str) -> set[int] | None:
    """The lines a logical line of Python starts on, from 1; `None` when `text` does not tokenize."""
    starts: set[int] = set()
    fresh = True
    skip = (tokenize.NL, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.NEWLINE:
                fresh = True
            elif fresh and tok.type not in skip:
                starts.add(tok.start[0])
                fresh = False
    except (tokenize.TokenError, SyntaxError):
        return None
    return starts


def definitions(name: str, text: str) -> list[tuple[int, str]]:
    """`(line, "def name")` for every definition listed, lines from 1.

    `.py`: `def` and `class` at column 0, and a `def` four spaces in under a `class` as
    `Class.method`. `.js`/`.mjs`: `function`, `class` and `const … =` at column 0.
    """
    out: list[tuple[int, str]] = []
    if name.endswith(".py"):
        owner = ""
        starts = _statements(text)
        for i, line in enumerate(text.splitlines(), 1):
            if starts is not None and i not in starts:
                continue
            if line[:1] and not line[0].isspace() and not line.startswith("#"):
                m = _PY.match(line)
                owner = m.group(2) if m and m.group(1) == "class" else ""
                if m:
                    out.append((i, f"{' '.join(m.group(1).split())} {m.group(2)}"))
                continue
            m = _PY_METHOD.match(line)
            if m and owner:
                out.append((i, f"{' '.join(m.group(1).split())} {owner}.{m.group(2)}"))
    elif name.endswith((".js", ".mjs")):
        for i, line in enumerate(text.splitlines(), 1):
            for pattern in _JS:
                m = pattern.match(line)
                if m:
                    out.append((i, f"{m.group(1)} {m.group(2)}"))
                    break
    return out


def _paths(plan_text: str) -> list[str]:
    """The plan's paths in the order the section first names them; `::name` is cut."""
    section = files_section(plan_text) or ""
    seen: dict[str, int] = {}
    for token in files_of(plan_text) or ():
        path = token.split("::", 1)[0]
        at = section.find(token)
        at = len(section) if at < 0 else at
        seen[path] = min(at, seen.get(path, at))
    return sorted(seen, key=lambda p: (seen[p], p.encode()))


def _join(lines: list[str]) -> int:
    return len("\n".join(lines).encode("utf-8"))


def select(plan_text: str, tree: str | os.PathLike[str]) -> tuple[str, dict[str, Any]]:
    """`(section, record)`: one entry per file the plan names, never over `CAP_BYTES`.

    The record is `{bytes, files, full, short, new, outside}`. A path resolving outside `tree`
    is counted `outside` and never opened; a directory is skipped; a missing path with a `/`
    is `new`. Files get a whole entry while it fits, then only a short line each.
    """
    if files_section(plan_text) is None:
        return "", _empty()
    base = Path(tree).expanduser().resolve()
    record = _empty()
    entries: list[tuple[str, str]] = []
    for path in _paths(plan_text):
        target = (base / path).resolve()
        if target != base and base not in target.parents:
            record["outside"] += 1
            continue
        if target.is_dir():
            continue
        if not target.exists():
            if "/" in path:
                record["new"] += 1
                entries.append((f"- `{path}` — new", ""))
            continue
        raw = target.read_bytes()
        lines = raw.count(b"\n")
        short = f"- `{path}` — {lines} lines"
        defs = definitions(path, raw.decode("utf-8", errors="replace"))
        entries.append((short, "\n".join(f"  - {n} {d}" for n, d in defs)))
    record["files"] = len(entries)

    chosen: list[str] = []
    for i, (short, defs) in enumerate(entries):
        whole = f"{short}\n{defs}" if defs else short
        rest = [s for s, _ in entries[i + 1:]]
        if _join(chosen + [whole] + rest) <= CAP_BYTES:
            chosen.append(whole)
            record["full"] += 1
            continue
        shorts = [s for s, _ in entries[i:]]
        if _join(chosen + shorts) <= CAP_BYTES:
            chosen += shorts
            record["short"] += len(shorts)
        else:
            for m in range(len(shorts) - 1, -1, -1):
                more = f"… and {len(shorts) - m} more files"
                if _join(chosen + shorts[:m] + [more]) <= CAP_BYTES:
                    chosen += shorts[:m] + [more]
                    record["short"] += m
                    break
        break
    section = "\n".join(chosen)
    record["bytes"] = len(section.encode("utf-8"))
    return section, record


def for_step(plan_path: str | os.PathLike[str], tree: str | os.PathLike[str]) -> dict[str, Any]:
    """`{plan_map, plan_map_record}` for `Runner.run`. Never raises: a failure is no section and a record with `error`."""
    try:
        section, record = select(Path(plan_path).read_text(encoding="utf-8"), tree)
    except Exception as e:  # noqa: BLE001 — recorded, never a reason to refuse the step
        return {"plan_map": "", "plan_map_record": {**_empty(), "error": f"{type(e).__name__}: {e}"}}
    return {"plan_map": section, "plan_map_record": record}
