"""An idea several units share, each unit in its own repository's workspace (`0040`).

`.cos/ideas/NNNN_<slug>.md` in the store of the workspace it was started in. `cos.mjs
new-idea` allocates the number, this module writes the file and appends one line under
`## Units` for each unit opened from it, and `cos.mjs` reads it (`.claude/docs/ideas.md`).
Nothing above `## Units` is ever rewritten, and nothing here decides a gate.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from coscc.units import COS_DIR, CannotCreate, _cos, root

IDEAS_DIR = "ideas"

_ID = r"\d{4}_[a-z0-9]+(?:-[a-z0-9]+)*"
# A workspace name as `coscc/service/store.py` `valid_name` has it; it holds no `/`.
_WS = r"[A-Za-z0-9._-]{1,64}"
IDEA_ID_RE = re.compile(_ID)
# `<ws>/ideas/NNNN_<slug>.md`: the one form a request may name an idea by (spec R11).
IDEA_REF_RE = re.compile(rf"({_WS})/{IDEAS_DIR}/({_ID})\.md")
UNIT_REF_RE = re.compile(rf"({_WS})/(\d{{4}}_[a-z0-9]+(?:-[a-z0-9]+)*)")
_OWN_WORDS = "## In their own words"
_UNITS = "## Units"
# The line `append_unit` writes, read back by `read_units` and by `cos.mjs` `parseIdea`.
_UNIT_LINE = re.compile(r"- (\S+?)(?:\. Depends on: (.+?))?\.?")


def _named(ref: str, pattern: re.Pattern[str]) -> tuple[str, str] | None:
    m = pattern.fullmatch(ref or "")
    if not m or m.group(1) in {".", ".."}:
        return None
    return m.group(1), m.group(2)


def parse_idea_ref(ref: str) -> tuple[str, str] | None:
    """`(workspace, NNNN_slug)` of `<ws>/ideas/NNNN_<slug>.md`, or None."""
    return _named(ref, IDEA_REF_RE)


def parse_unit_ref(ref: str) -> tuple[str, str] | None:
    """`(workspace, unit)` of `<ws>/NNNN_<slug>`, or None."""
    return _named(ref, UNIT_REF_RE)


def idea_ref(workspace_name: str, idea_id: str) -> str:
    return f"{workspace_name}/{IDEAS_DIR}/{idea_id}.md"


def idea_path(
    workspace: str | os.PathLike[str], idea_id: str, data_dir: str | os.PathLike[str] | None = None
) -> Path:
    """One idea's file. The id is validated, never trusted, as `units.unit_dir` does a unit."""
    if not IDEA_ID_RE.fullmatch(idea_id or ""):
        raise CannotCreate(f"not an idea id: {idea_id!r}")
    return root(workspace, data_dir) / COS_DIR / IDEAS_DIR / f"{idea_id}.md"


def create_idea(
    workspace: str | os.PathLike[str],
    slug: str,
    brief: str,
    data_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Allocate the number with `cos.mjs new-idea`, then write the file with an empty `## Units`.

    The printed path is checked the way `units.create` checks `new-path`'s: joined to the
    store, it must stay inside `ideas/`, and name a file that does not exist yet.
    """
    text = str(brief or "").strip()
    if not text:
        raise CannotCreate("an idea needs a brief: the originator's own words are the idea")
    if any(line.rstrip() == _UNITS for line in text.splitlines()):
        # `cos.mjs` and `read_units` take the first such line for the app's own section.
        raise CannotCreate(f"a brief may not hold the line {_UNITS!r}: the idea's units are listed under it")
    store = root(workspace, data_dir)
    (store / COS_DIR).mkdir(parents=True, exist_ok=True)
    printed = _cos(store, "new-idea", str(slug or "").strip())
    relative = printed.splitlines()[-1].strip() if printed else ""
    if not relative:
        raise CannotCreate("cos.mjs new-idea printed nothing")
    path = (store / relative).resolve()
    ideas = (store / COS_DIR / IDEAS_DIR).resolve()
    if path.parent != ideas or not path.name.endswith(".md") or not IDEA_ID_RE.fullmatch(path.name[:-3]):
        raise CannotCreate(f"cos.mjs named something that is not an idea: {relative}")
    ideas.mkdir(parents=True, exist_ok=True)
    idea_id = path.name[:-3]
    title = idea_id.split("_", 1)[-1].replace("-", " ")
    with path.open("x", encoding="utf-8") as f:
        f.write(
            f"# Idea: {title}\n"
            f"Author: the originator. Status: accepted.\n\n"
            f"{_OWN_WORDS}\n\n{text}\n\n"
            f"{_UNITS}\n\n"
        )
    return {"id": idea_id, "path": str(path)}


def unit_line(workspace_name: str, unit: str, depends_on: str = "") -> str:
    """The line under `## Units` for one unit (spec R3)."""
    tail = f". Depends on: {depends_on}" if depends_on else ""
    return f"- {workspace_name}/{unit}{tail}.\n"


def append_unit(path: str | os.PathLike[str], workspace_name: str, unit: str, depends_on: str = "") -> None:
    """Append one line to the end of the idea. Nothing already in the file is rewritten.

    `## Units` is the file's last section, so its end is the file's end. A file that does
    not end in a newline gets one first, so the line starts a line of its own.
    """
    target = Path(path)
    needs_newline = False
    with target.open("rb") as f:
        if f.seek(0, os.SEEK_END) > 0:
            f.seek(-1, os.SEEK_END)
            needs_newline = f.read(1) != b"\n"
    with target.open("a", encoding="utf-8") as f:
        f.write(("\n" if needs_newline else "") + unit_line(workspace_name, unit, depends_on))


def read_text(path: str | os.PathLike[str]) -> str:
    return Path(path).read_text(encoding="utf-8")


def read_units(text: str) -> list[dict[str, Any]]:
    """The units listed under `## Units`, `{ref, depends_on: [...]}`, in the file's order.

    Used by the route to check a `depends_on` it was sent against what the idea lists (R11).
    A line it cannot read is left out; `cos.mjs status` is what reports it.
    """
    out: list[dict[str, Any]] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line.rstrip() == _UNITS
            continue
        if not inside or not line.strip():
            continue
        m = _UNIT_LINE.fullmatch(line.rstrip())
        if m and parse_unit_ref(m.group(1)):
            deps = [d.strip() for d in (m.group(2) or "").split(",") if d.strip()]
            out.append({"ref": m.group(1), "depends_on": deps})
    return out


def brief_of(text: str) -> str:
    """The originator's words: the idea's `## In their own words`, without its heading.

    They run to `## Units`, not to the next `## `: `create_idea` writes the brief as it was
    given, and one pasted from a markdown file has headings of its own (`0040` review round 1,
    F2).
    """
    lines = text.splitlines()
    try:
        start = lines.index(_OWN_WORDS) + 1
    except ValueError:
        return ""
    end = next((i for i in range(start, len(lines)) if lines[i].rstrip() == _UNITS), len(lines))
    return "\n".join(lines[start:end]).strip()
