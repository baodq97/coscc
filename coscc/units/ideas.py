"""An idea several units share, each unit in its own repository's workspace.

`.cos/ideas/NNNN_<slug>.md` in the store of the workspace it was started in. The loop's
`new-idea` allocates the number; this module writes the file, once. An idea's units are the rows of
`unit_links` that name it (`UnitMeta.link`, written when a unit is opened from it). `Ideas` is what
the app asks of them: making one, checking a unit may link to it, and what a step's prompt shows.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from coscc.config import Config
from coscc.git import gitops
from coscc.git.gitops import GitError
from coscc.store.workspaces import valid_name
from coscc.units import COS_DIR, CannotCreate, Invalid, _cos, root
from coscc.units.workspaces import Workspaces

IDEAS_DIR = "ideas"

_ID = r"\d{4}_[a-z0-9]+(?:-[a-z0-9]+)*"
# A workspace name as `coscc/store/workspaces.py` `valid_name` has it; it holds no `/`.
_WS = r"[A-Za-z0-9._-]{1,64}"
IDEA_ID_RE = re.compile(_ID)
# `<ws>/ideas/NNNN_<slug>.md`: the one form a request may name an idea by.
IDEA_REF_RE = re.compile(rf"({_WS})/{IDEAS_DIR}/({_ID})\.md")
UNIT_REF_RE = re.compile(rf"({_WS})/(\d{{4}}_[a-z0-9]+(?:-[a-z0-9]+)*)")
_OWN_WORDS = "## In their own words"


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
    """Allocate the number with the loop's `new-idea`, then write the file.

    The printed path is checked the way `units.create` checks `new-path`'s: joined to the
    store, it must stay inside `ideas/`, and name a file that does not exist yet.
    """
    text = str(brief or "").strip()
    if not text:
        raise CannotCreate("an idea needs a brief: the originator's own words are the idea")
    store = root(workspace, data_dir)
    (store / COS_DIR).mkdir(parents=True, exist_ok=True)
    printed = _cos(store, "new-idea", str(slug or "").strip())
    relative = printed.splitlines()[-1].strip() if printed else ""
    if not relative:
        raise CannotCreate("coscc.loop new-idea printed nothing")
    path = (store / relative).resolve()
    ideas = (store / COS_DIR / IDEAS_DIR).resolve()
    if (
        path.parent != ideas
        or not path.name.endswith(".md")
        or not IDEA_ID_RE.fullmatch(path.name[:-3])
    ):
        raise CannotCreate(f"coscc.loop named something that is not an idea: {relative}")
    ideas.mkdir(parents=True, exist_ok=True)
    idea_id = path.name[:-3]
    title = idea_id.split("_", 1)[-1].replace("-", " ")
    with path.open("x", encoding="utf-8") as f:
        f.write(
            f"# Idea: {title}\n"
            f"Author: the originator. Status: accepted.\n\n"
            f"{_OWN_WORDS}\n\n{text}\n"
        )
    return {"id": idea_id, "path": str(path)}


def read_text(path: str | os.PathLike[str]) -> str:
    return Path(path).read_text(encoding="utf-8")


class Ideas:
    def __init__(self, config: Config, ws: Workspaces) -> None:
        self.config = config
        self.ws = ws

    def _workspace_by_name(self, name: str) -> str | None:
        """The one workspace called `name`, or None when there is none or more than one."""
        rows = [r for r in self.ws.all()["workspaces"] if r["name"] == name]
        return str(rows[0]["path"]) if len(rows) == 1 else None

    def create_idea(self, cwd: str, slug: str, brief: str) -> dict[str, Any]:
        """The idea's home is `cwd`'s store; its text is the brief, and nothing else."""
        self.ws.check(cwd)
        if not str(brief or "").strip():
            raise Invalid("An idea needs a brief.")
        name = self.ws.name(cwd)
        try:
            made = create_idea(cwd, slug, brief, self.config.data_dir)
        except CannotCreate as e:
            raise Invalid(str(e)) from e
        return {
            "cwd": cwd,
            "id": made["id"],
            "ref": idea_ref(name, made["id"]) if name else "",
        }

    def _units_of(self, idea: str) -> list[tuple[str, str, list[str]]]:
        """`(workspace name, unit, depends_on)` of every unit opened from `idea`: the rows that
        name it, in every workspace the app names."""
        names = {self.ws.key(path): name for name, path in self.ws.peer_table()[0]}
        return [
            (names[w], u, deps) for w, u, deps in self.ws.unit_meta().idea_units(idea) if w in names
        ]

    def idea_link(self, cwd: str, idea: str, brief: str, depends_on: str) -> None:
        """Every check a unit opened from `idea` needs, before anything is made."""
        ref = parse_idea_ref(idea)
        if ref is None:
            raise Invalid(f"{idea} is not <workspace>/ideas/NNNN_<slug>.md.")
        if str(brief or "").strip():
            raise Invalid("A unit opened from an idea takes no brief: the idea is its brief.")
        home = self._workspace_by_name(ref[0])
        if home is None:
            raise Invalid(f"No single workspace is named {ref[0]}.")
        try:
            path = idea_path(home, ref[1], self.config.data_dir)
        except CannotCreate as e:
            raise Invalid(str(e)) from e
        if not path.is_file():
            raise Invalid(f"{idea} does not exist.")
        name = self.ws.name(cwd)
        if not name or not valid_name(name) or name not in dict(self.ws.peers()):
            raise Invalid("This workspace has no name of its own that another unit could refer to.")
        if depends_on and depends_on not in [f"{w}/{u}" for w, u, _ in self._units_of(idea)]:
            raise Invalid(f"{depends_on} is not a unit of {idea}.")

    def _idea_of(self, cwd: str, unit: str) -> dict[str, Any] | None:
        """The idea `unit` was opened from, with its text, its units and what this unit depends
        on, or None. The unit's own `idea` row says which; the idea file is only read for its text."""
        links = self.ws.meta_of(cwd, unit).get("links") or {}
        idea = str(links.get("idea") or "")
        ref = parse_idea_ref(idea)
        home = self._workspace_by_name(ref[0]) if ref else None
        if ref is None or home is None:
            return None
        try:
            text = read_text(idea_path(home, ref[1], self.config.data_dir))
        except CannotCreate, OSError:
            return None
        return {
            "text": text,
            "me": f"{self.ws.name(cwd)}/{unit}",
            "units": self._units_of(idea),
            "depends_on": links.get("dependsOn") or [],
        }

    def idea_note(self, cwd: str, unit: str) -> str:
        """The idea's text and the other units of it, for the intent prompt."""
        found = self._idea_of(cwd, unit)
        if found is None:
            return ""
        others = [f"{w}/{u}" for w, u, _ in found["units"] if f"{w}/{u}" != found["me"]]
        return f"{found['text'].rstrip()}\n\n## The other units of this idea\n\n" + (
            "".join(f"- {o}\n" for o in others) or "None yet.\n"
        )

    async def siblings(self, cwd: str, unit: str) -> str:
        """The note naming the checkouts `impl` may read, each with its HEAD.

        The other workspaces the idea's units are in, and those of the unit's dependencies; never
        the unit's own. A workspace no longer there is named as missing and not read.
        """
        found = self._idea_of(cwd, unit)
        if found is None:
            return ""
        own = found["me"].split("/", 1)[0]
        names = [w for w, _, _ in found["units"]]
        names += [d.split("/", 1)[0] for d in found["depends_on"] if "/" in d]
        lines: list[str] = []
        for name in dict.fromkeys(n for n in names if n != own):
            where = self._workspace_by_name(name)
            if where is None or not Path(where).is_dir():
                lines.append(f"- {name}: missing, not readable")
                continue
            try:
                head, branch = await gitops.head_and_branch(Path(where))
                at = f"HEAD {head[:12]} on {branch}"
            except GitError:
                at = "HEAD unknown"
            lines.append(f"- {name}: {Path(where).resolve()} ({at})")
        if not lines:
            return ""
        return (
            "Read these to see the other side of the contract. Do not write there: Write and Edit "
            "are refused outside this worktree, and a commit there is not this unit's.\n\n"
            + "\n".join(lines)
        )
