"""An idea several units share, each unit in its own repository's workspace.

`.cos/ideas/NNNN_<slug>.md` in the store of the workspace it was started in. The loop's
`new-idea` allocates the number; this module writes the file and appends one line under
`## Units` per unit opened from it. Nothing above `## Units` is ever rewritten. `Ideas` is what
the app asks of them: making one, linking a unit to it, and what the `/idea` page and a step's
prompt show.
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from coscc.config import Config
from coscc.git import gitops
from coscc.git.gitops import GitError
from coscc.store.db import Busy
from coscc.store.workspaces import valid_name
from coscc.units import COS_DIR, CannotCreate, Invalid, _cos, root
from coscc.units import board as board_reader
from coscc.units.board import Unavailable, unit_state
from coscc.units.meta import MetaError
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
_UNITS = "## Units"
# The line `append_unit` writes, read back by `read_units` and by the loop's `parseIdea`.
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
    """Allocate the number with the loop's `new-idea`, then write the file with an empty `## Units`.

    The printed path is checked the way `units.create` checks `new-path`'s: joined to the
    store, it must stay inside `ideas/`, and name a file that does not exist yet.
    """
    text = str(brief or "").strip()
    if not text:
        raise CannotCreate("an idea needs a brief: the originator's own words are the idea")
    if any(line.rstrip() == _UNITS for line in text.splitlines()):
        # The loop and `read_units` take the first such line for the app's own section.
        raise CannotCreate(
            f"a brief may not hold the line {_UNITS!r}: the idea's units are listed under it"
        )
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
            f"{_OWN_WORDS}\n\n{text}\n\n"
            f"{_UNITS}\n\n"
        )
    return {"id": idea_id, "path": str(path)}


def unit_line(workspace_name: str, unit: str, depends_on: str = "") -> str:
    """The line under `## Units` for one unit."""
    tail = f". Depends on: {depends_on}" if depends_on else ""
    return f"- {workspace_name}/{unit}{tail}.\n"


def append_unit(
    path: str | os.PathLike[str], workspace_name: str, unit: str, depends_on: str = ""
) -> None:
    """Append one line to the end of the idea; `## Units` is the last section.

    A file that does not end in a newline gets one first.
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

    A line it cannot read is left out; the loop's `status` reports it.
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

    They run to `## Units`, not the next `## `: a pasted brief may have headings of its own.
    """
    lines = text.splitlines()
    try:
        start = lines.index(_OWN_WORDS) + 1
    except ValueError:
        return ""
    end = next((i for i in range(start, len(lines)) if lines[i].rstrip() == _UNITS), len(lines))
    return "\n".join(lines[start:end]).strip()


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
        self.refresh_ideas(cwd)
        return {
            "cwd": cwd,
            "id": made["id"],
            "ref": idea_ref(name, made["id"]) if name else "",
        }

    def idea_link(self, cwd: str, idea: str, brief: str, depends_on: str) -> dict[str, Any]:
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
        if depends_on and depends_on not in [u["ref"] for u in read_units(read_text(path))]:
            raise Invalid(f"{depends_on} is not listed under {idea}.")
        return {"path": path, "ws": name, "home": home}

    def _ideas_everywhere(self) -> list[dict[str, Any]]:
        """Every idea file in every workspace's store, `{ws, id, path, text, units}`."""
        out: list[dict[str, Any]] = []
        for name, store in self.ws.peers():
            folder = store / ".cos" / IDEAS_DIR
            if not folder.is_dir():
                continue
            for f in sorted(folder.glob("*.md")):
                if not IDEA_ID_RE.fullmatch(f.name[:-3]):
                    continue
                try:
                    text = f.read_text(encoding="utf-8")
                except OSError:
                    continue
                out.append(
                    {
                        "ws": name,
                        "id": f.name[:-3],
                        "path": f,
                        "text": text,
                        "units": read_units(text),
                    }
                )
        return out

    def _idea_of(self, cwd: str, unit: str) -> dict[str, Any] | None:
        """The idea that lists `<cwd's name>/<unit>` under `## Units`, with that line, or None.

        Read off the files in each store, never the loop: which idea a unit was opened from is the
        app's own record, and asking the script would read every workspace's board.
        """
        name = self.ws.name(cwd)
        if not name:
            return None
        me = f"{name}/{unit}"
        for found in self._ideas_everywhere():
            line = next((u for u in found["units"] if u["ref"] == me), None)
            if line is not None:
                return {
                    **found,
                    "ref": idea_ref(found["ws"], found["id"]),
                    "line": line,
                    "me": me,
                }
        return None

    def idea_note(self, cwd: str, unit: str) -> str:
        """The idea's text, its units with their `Repo`, and the three header lines."""
        found = self._idea_of(cwd, unit)
        if found is None:
            return ""
        ws = found["me"].split("/", 1)[0]
        siblings = [u["ref"] for u in found["units"] if u["ref"] != found["me"]]
        header = [f"Idea: {found['ref']}.", f"Repo: {ws}."]
        if found["line"]["depends_on"]:
            header.append(f"Depends on: {', '.join(found['line']['depends_on'])}.")
        return (
            f"{found['text'].rstrip()}\n\n"
            "## The other units of this idea\n\n"
            + ("".join(f"- {s} (Repo: {s.split('/', 1)[0]})\n" for s in siblings) or "None yet.\n")
            + "\n## The header this unit's intent.md must carry\n\n"
            "Write these lines into the header of intent.md, beside Type and Status, exactly:\n\n"
            + "".join(f"    {h}\n" for h in header)
        )

    async def siblings(self, cwd: str, unit: str) -> str:
        """The note naming the checkouts `impl` may read, each with its HEAD.

        The other workspaces the idea lists, and those of the unit's dependencies; never the
        unit's own. A workspace no longer there is named as missing and not read.
        """
        found = self._idea_of(cwd, unit)
        if found is None:
            return ""
        own = found["me"].split("/", 1)[0]
        names = [u["ref"].split("/", 1)[0] for u in found["units"]]
        names += [d.split("/", 1)[0] for d in found["line"]["depends_on"]]
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

    async def idea(self, cwd: str, idea_id: str) -> dict[str, Any]:
        """One idea, its text, and a row per unit it lists.

        Each row reads the board of the unit's own workspace, once per workspace; a workspace
        that is gone is a `missing` row, not a refusal.
        """
        self.ws.check(cwd)
        name = self.ws.name(cwd)
        try:
            path = idea_path(cwd, idea_id, self.config.data_dir)
        except CannotCreate as e:
            raise Invalid(str(e)) from e
        if not path.is_file():
            raise Invalid(f"No idea {idea_id} in this workspace.")
        text = read_text(path)
        listed = read_units(text)
        peers = self.ws.peer_table()[0]
        boards: dict[str, dict[str, Any] | None] = {}
        rows: list[dict[str, Any]] = []
        for line in listed:
            ws, unit = line["ref"].split("/", 1)
            if ws not in boards:
                where = self._workspace_by_name(ws)
                try:
                    boards[ws] = (
                        None
                        if where is None
                        else await board_reader.read(
                            self.ws.units_root(where), state=self.ws.snapshot(where, peers=peers)
                        )
                    )
                except Unavailable:
                    boards[ws] = None
            board = boards[ws]
            found = next((u for u in (board or {}).get("units") or [] if u["name"] == unit), None)
            waits = [
                d["ref"]
                for d in (found or {}).get("depends_on") or []
                if d.get("merged") is not True
            ]
            rows.append(
                {
                    "ref": line["ref"],
                    "ws": ws,
                    "unit": unit,
                    "repo": ws,
                    "missing": found is None,
                    "stage": (found or {}).get("at") or "",
                    "state": unit_state(found, None, None)["label"] if found else "missing",
                    "waits_for": waits if (found or {}).get("why") == "dependency" else [],
                    "depends_on": line["depends_on"],
                }
            )
        title = next(
            (l[len("# Idea:") :].strip() for l in text.splitlines() if l.startswith("# Idea:")),
            idea_id,
        )
        return {
            "cwd": cwd,
            "ws": name,
            "id": idea_id,
            "ref": idea_ref(name, idea_id) if name else "",
            "title": title,
            "brief": brief_of(text),
            "units": rows,
            "workspaces": [n for n, _ in peers],
        }

    def refresh_ideas(self, cwd: str) -> None:
        """`cwd`'s ideas into `cos.db` again, after the app wrote one. A failure is left to
        the board: the loop then reports the idea link it cannot find."""
        try:
            self.ws.unit_meta().refresh_ideas(self.ws.key(cwd), self.ws.units_root(cwd))
        except MetaError, Busy, sqlite3.Error, OSError:
            pass
