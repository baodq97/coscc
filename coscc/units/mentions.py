"""The units a unit names, and the two files of each a step of it may read.

A unit names another by its number in its own `idea.md`, in the `## Answers` a prompt shows for
any of its artifacts, or by a `Depends on:` or backlog `phụ thuộc` relation; nothing else is
read. A path is built from the app's store and a directory name found there or validated by
`unit_dir`, never from the text: the text only picks among the directories there are.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from coscc import units

# The only files of a named unit a step may read.
FILES = ("idea.md", "intent.md")

# Four digits standing alone, and not the year of a `YYYY-MM-DD` date.
_NUMBER = re.compile(r"(?<!\d)(\d{4})(?!\d|-\d{2}-\d{2}(?!\d))", re.ASCII)


def mentioned(
    unit: str,
    names: Iterable[str],
    idea: str,
    answers: Iterable[tuple[str, str]],
    meta: Mapping[str, Any] | None,
    own: str = "",
) -> list[dict[str, str]]:
    """`[{unit, source, ws}]`, each unit once, under the first source that named it.

    `names` are the directories of this workspace's store: a number counts only when exactly one
    unit there carries it, and never for `unit` itself. `answers` are `(artifact, ## Answers as
    the prompt shows it)`; `meta` is the unit's snapshot entry, whose `links` carry `dependsOn`
    and `backlog`. `own` is this workspace's name; `ws` is "" for a unit of this workspace, else
    the name of the one a link names.
    """
    by_number: dict[str, list[str]] = {}
    for name in names:
        if units.UNIT_RE.fullmatch(name) and name != unit:
            by_number.setdefault(name[:4], []).append(name)
    found: dict[tuple[str, str], str] = {}

    def numbers(text: str, source: str) -> None:
        for number in dict.fromkeys(_NUMBER.findall(text or "")):
            hit = by_number.get(number) or []
            if len(hit) == 1:
                found.setdefault(("", hit[0]), source)

    numbers(idea, "idea.md")
    for artifact, text in answers:
        numbers(text, f"## Answers of {artifact}")
    links = (meta or {}).get("links") or {}
    refs = [(r, "Depends on:") for r in links.get("dependsOn") or []]
    refs += [(r.get("ref"), "backlog") for r in links.get("backlog") or [] if isinstance(r, dict)]
    for ref, source in refs:
        ws, _, name = str(ref or "").rpartition("/")
        ws = "" if ws == own else ws
        if units.UNIT_RE.fullmatch(name) and (ws or name != unit):
            found.setdefault((ws, name), source)
    return [{"unit": name, "source": source, "ws": ws} for (ws, name), source in found.items()]


def directories(
    workspace: str,
    found: list[dict[str, str]],
    workspaces: Iterable[Mapping[str, Any]],
    data_dir: str | None = None,
) -> list[Path | None]:
    """Each found unit's directory in the app's store, or `None` when it cannot be read: its
    workspace is not the one app workspace of that name, or the directory is not there."""
    rows = list(workspaces)
    out: list[Path | None] = []
    for f in found:
        where: str | None = workspace
        if f["ws"]:
            named = [r for r in rows if r.get("name") == f["ws"]]
            where = str(named[0]["path"]) if len(named) == 1 else None
        d = units.unit_dir(where, f["unit"], data_dir) if where else None
        out.append(d if d is not None and d.is_dir() else None)
    return out


def read_paths(dirs: Iterable[Path | None]) -> tuple[str, ...]:
    """`idea.md` and `intent.md` of each directory, the ones there are."""
    return tuple(str(d / f) for d in dirs if d is not None for f in FILES if (d / f).is_file())


def note(found: list[dict[str, str]], dirs: list[Path | None]) -> str:
    """One line per found unit: its name, what named it and the files a step may read; "" for
    none."""
    lines: list[str] = []
    for f, d in zip(found, dirs):
        ref = f"{f['ws']}/{f['unit']}" if f["ws"] else f["unit"]
        files = read_paths([d])
        where = (
            ", ".join(files)
            if files
            else "missing, not readable"
            if d is None
            else "no idea.md or intent.md yet"
        )
        lines.append(f"- {ref} ({f['source']}): {where}")
    if not lines:
        return ""
    return (
        "This unit names these units. Read, Glob and Grep may open the files listed, as they stand "
        "in the app's store; every other file of theirs is refused, and so is writing any of them "
        "or pointing git at them.\n\n" + "\n".join(lines)
    )


def for_step(
    workspace: str,
    unit: str,
    idea: str,
    answers: Iterable[tuple[str, str]],
    meta: Mapping[str, Any] | None,
    own: str,
    workspaces: Iterable[Mapping[str, Any]],
    data_dir: str | None = None,
) -> tuple[tuple[str, ...], str]:
    """The paths a step of `unit` may read besides its own, and the note naming them."""
    try:
        names = [p.name for p in units.cos_dir(workspace, data_dir).iterdir() if p.is_dir()]
    except OSError:
        names = []
    found = mentioned(unit, names, idea, answers, meta, own)
    dirs = directories(workspace, found, workspaces, data_dir)
    return read_paths(dirs), note(found, dirs)
