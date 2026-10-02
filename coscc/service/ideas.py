"""An idea several units share, each unit in its own workspace. What an idea *means* to a gate is the loop's; this module writes
the file, links a unit to it, and gathers what the `/idea` page and a step's prompt show.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from coscc.git import gitops
from coscc.git.gitops import GitError
from coscc.data import Busy
from coscc.service.common import Invalid, unit_state
from coscc.units.meta import MetaError
from coscc.service.store import valid_name
from coscc.units import CannotCreate, ideas
from coscc.units import board as board_reader
from coscc.units.board import Unavailable
from coscc.config import Config
from coscc.service.workspaces import Workspaces


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
            made = ideas.create_idea(cwd, slug, brief, self.config.data_dir)
        except CannotCreate as e:
            raise Invalid(str(e)) from e
        self.refresh_ideas(cwd)
        return {
            "cwd": cwd,
            "id": made["id"],
            "ref": ideas.idea_ref(name, made["id"]) if name else "",
        }

    def idea_link(self, cwd: str, idea: str, brief: str, depends_on: str) -> dict[str, Any]:
        """Every check a unit opened from `idea` needs, before anything is made."""
        ref = ideas.parse_idea_ref(idea)
        if ref is None:
            raise Invalid(f"{idea} is not <workspace>/ideas/NNNN_<slug>.md.")
        if str(brief or "").strip():
            raise Invalid("A unit opened from an idea takes no brief: the idea is its brief.")
        home = self._workspace_by_name(ref[0])
        if home is None:
            raise Invalid(f"No single workspace is named {ref[0]}.")
        try:
            path = ideas.idea_path(home, ref[1], self.config.data_dir)
        except CannotCreate as e:
            raise Invalid(str(e)) from e
        if not path.is_file():
            raise Invalid(f"{idea} does not exist.")
        name = self.ws.name(cwd)
        if not name or not valid_name(name) or name not in dict(self.ws.peers()):
            raise Invalid("This workspace has no name of its own that another unit could refer to.")
        if depends_on and depends_on not in [
            u["ref"] for u in ideas.read_units(ideas.read_text(path))
        ]:
            raise Invalid(f"{depends_on} is not listed under {idea}.")
        return {"path": path, "ws": name, "home": home}

    def _ideas_everywhere(self) -> list[dict[str, Any]]:
        """Every idea file in every workspace's store, `{ws, id, path, text, units}`."""
        out: list[dict[str, Any]] = []
        for name, store in self.ws.peers():
            folder = store / ".cos" / ideas.IDEAS_DIR
            if not folder.is_dir():
                continue
            for f in sorted(folder.glob("*.md")):
                if not ideas.IDEA_ID_RE.fullmatch(f.name[:-3]):
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
                        "units": ideas.read_units(text),
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
                    "ref": ideas.idea_ref(found["ws"], found["id"]),
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

    async def siblings(self, cwd: str, unit: str) -> tuple[tuple[str, ...], str]:
        """The checkouts `impl` may read, and the note naming each with its HEAD.

        The other workspaces the idea lists, and those of the unit's dependencies; never the
        unit's own. A workspace no longer there is named as missing and not read.
        """
        found = self._idea_of(cwd, unit)
        if found is None:
            return (), ""
        own = found["me"].split("/", 1)[0]
        names = [u["ref"].split("/", 1)[0] for u in found["units"]]
        names += [d.split("/", 1)[0] for d in found["line"]["depends_on"]]
        paths: list[str] = []
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
            paths.append(str(Path(where).resolve()))
            lines.append(f"- {name}: {Path(where).resolve()} ({at})")
        if not lines:
            return (), ""
        note = (
            "Read these to see the other side of the contract. Do not write there: Write and Edit "
            "are refused outside this worktree, and so is git given one of them with -C, --git-dir, "
            "--work-tree or GIT_DIR. Nothing else stops a command that writes there.\n\n"
            + "\n".join(lines)
        )
        return tuple(paths), note

    async def idea(self, cwd: str, idea_id: str) -> dict[str, Any]:
        """One idea, its text, and a row per unit it lists.

        Each row reads the board of the unit's own workspace, once per workspace; a workspace
        that is gone is a `missing` row, not a refusal.
        """
        self.ws.check(cwd)
        name = self.ws.name(cwd)
        try:
            path = ideas.idea_path(cwd, idea_id, self.config.data_dir)
        except CannotCreate as e:
            raise Invalid(str(e)) from e
        if not path.is_file():
            raise Invalid(f"No idea {idea_id} in this workspace.")
        text = ideas.read_text(path)
        listed = ideas.read_units(text)
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
            "ref": ideas.idea_ref(name, idea_id) if name else "",
            "title": title,
            "brief": ideas.brief_of(text),
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
