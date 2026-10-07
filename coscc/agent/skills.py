"""The skills hub: every skill the packs hold, what a run is given of its row's skills, a new skill
written into the owner's layer, and how often each was used.

A skill is `skills/<name>/SKILL.md` in a pack: the built-in, an imported one, or the owner's
`local` (whose copy of a pack's skill is an edit of it, `pack.skill_path`). A row names the skills
it is given; every run records them on its `start` as `name@hash` (`pack.stamp`), which is what
`uses` counts.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypedDict

from coscc.agent import pack
from coscc.store.db import Busy, Unusable

# A new skill's name: a pack's naming rule, shorter than a sentence.
NAME = re.compile(r"[a-z][a-z0-9-]{0,39}")
# A new skill's text at most, in bytes; the largest built-in skill is about 4.4 KB.
MAX_BYTES = 16_000
WINDOW_DAYS = 30
# The `description:` line of a skill's frontmatter, as Claude Code's skills open.
_DESCRIPTION = re.compile(r"\A---\n(?:.*\n)*?description:[ \t]*(.+)\n(?:.*\n)*?---", re.M)


class Skill(TypedDict):
    name: str
    pack: str
    # Shipped with the app (its pack is the built-in one).
    builtin: bool
    description: str
    own: bool
    edited: bool
    hash: str
    chars: int
    text: str
    agents: list[str]
    uses_30d: int
    last_used: str


class SkillsPage(TypedDict):
    skills: list[Skill]
    # Why the uses could not be counted, when the run log could not be read.
    problems: list[str]


def hash_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def page(journal: Any, now: datetime | None = None) -> SkillsPage:
    """The Skills page: `catalog` over one read of the run log's `start`s."""
    if journal is None:
        return {"skills": catalog((), now), "problems": []}
    try:
        records = journal.records(None, kinds=("start",))
    except (Unusable, Busy, sqlite3.Error, OSError) as e:
        return {"skills": catalog((), now), "problems": [f"the run log could not be read: {e}"]}
    return {"skills": catalog(records, now), "problems": []}


def text(names: Iterable[str]) -> str:
    """The texts of skills `names`, in order, as a run is given them; `LookupError` names one
    that is gone."""
    return "\n\n".join(pack.skill(n) for n in names)


def system(key: str) -> str:
    """Row `key`'s body with the text of each skill it names after it: the system prompt of a run
    with no stage prompt (an agent a person or a trigger starts, Leif's chat, a helper). A skill
    that is gone adds nothing, as `pack.check` already lists it among the row's problems."""
    found = pack.row(key) or {}
    body = str(found.get(pack.BODY) or "")
    texts = []
    for name in found.get("skills") or []:
        try:
            texts.append(pack.skill(name).strip())
        except LookupError:
            continue
    return "\n\n".join([body.rstrip(), *texts]).lstrip() if texts else body


def _pack_of(path: Path) -> tuple[str, bool]:
    """The pack a skill file sits in, and whether it is the owner's."""
    root = path.parents[2]
    if root == pack.owner_dir():
        return pack.LOCAL_NAME, True
    if root == pack.BUILTIN:
        return str(pack.manifest()["name"]), False
    return root.name, False


def _names() -> list[str]:
    roots = [pack.BUILTIN, *pack.packs_dir().glob("*")]
    found = set()
    for root in roots:
        if root.name.startswith(".") or root.is_symlink() or not root.is_dir():
            continue
        found |= {p.parent.name for p in (root / "skills").glob(f"*/{pack.SKILL_FILE}")}
    return sorted(n for n in found if NAME.fullmatch(n))


def catalog(records: Iterable[Mapping[str, Any]] = (), now: datetime | None = None) -> list[Skill]:
    """Every skill once, in name order, with the agents naming it and its uses in the last
    `WINDOW_DAYS` days of `records` (the run log's `start`s)."""
    counts, last = uses(records, now)
    rows = pack.rows()
    out: list[Skill] = []
    for name in _names():
        path = pack.skill_path(name)
        if path is None:
            continue
        said = path.read_text(encoding="utf-8", errors="replace")
        owner, own = _pack_of(path)
        base = pack.skill_path(name, owner=False)
        if own and base is not None:
            owner = _pack_of(base)[0]
        described = _DESCRIPTION.search(said)
        out.append(
            Skill(
                name=name,
                pack=owner,
                builtin=owner == pack.manifest()["name"],
                description=described.group(1).strip().strip("\"'") if described else "",
                own=own and base is None,
                edited=own and base is not None,
                hash=hash_of(said),
                chars=len(said),
                text=said,
                agents=sorted(k for k, r in rows.items() if name in (r.get("skills") or [])),
                uses_30d=counts.get(name, 0),
                last_used=last.get(name, ""),
            )
        )
    return out


def uses(
    records: Iterable[Mapping[str, Any]], now: datetime | None = None
) -> tuple[Counter[str], dict[str, str]]:
    """Per skill name, the runs whose `start` named it in the last `WINDOW_DAYS` days, and the
    latest such `at`."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=WINDOW_DAYS)).isoformat(timespec="seconds")
    counts: Counter[str] = Counter()
    last: dict[str, str] = {}
    for record in records:
        at = str(record.get("at") or "")
        if record.get("kind") != "start" or at < since:
            continue
        for named in record.get("skills") or []:
            name = str(named).split("@", 1)[0]
            counts[name] += 1
            last[name] = max(last.get(name, ""), at)
    return counts, last


def new(name: Any, said: Any) -> Path:
    """Write skill `name` into the owner's layer, `local/skills/<name>/SKILL.md`; a `ValueError`
    names why not, and nothing is written. A skill any pack already has is edited on the agent
    page that names it, never replaced here."""
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError(
            "a skill name is lowercase letters, digits and hyphens, opening with a letter,"
            " at most 40 characters"
        )
    if not isinstance(said, str) or not said.strip():
        raise ValueError("a skill is text, and this one is empty")
    if "\x00" in said:
        raise ValueError("a skill is text, and this one holds a NUL byte")
    if len(said.encode("utf-8")) > MAX_BYTES:
        raise ValueError(f"a skill is at most {MAX_BYTES:,} bytes")
    if name in _names() or pack.skill_path(name) is not None:
        raise ValueError(f"{name}: a skill of that name exists; edit it on an agent naming it")
    owner = pack.owner_dir()
    for part in (owner, owner / "skills", owner / "skills" / name):
        if part.is_symlink():
            raise ValueError(f"{part.name}: a link, so the skill is not written through it")
    folder = owner / "skills" / name
    if folder.exists():
        raise ValueError(f"{name}: its folder exists without a skill in it; remove it first")
    path = folder / pack.SKILL_FILE
    if path.resolve().parent.parent != (owner / "skills").resolve():
        raise ValueError(f"{name}: would be written outside the owner's skills")
    folder.mkdir(parents=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{pack.SKILL_FILE}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write((said if said.endswith("\n") else said + "\n").encode("utf-8"))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    manifest = owner / pack.MANIFEST
    if not manifest.is_file():
        manifest.parent.mkdir(parents=True, exist_ok=True)
        said = json.dumps({"name": pack.LOCAL_NAME, "version": "1.0.0"}, indent=2)
        manifest.write_text(said + "\n", encoding="utf-8")
    return path
