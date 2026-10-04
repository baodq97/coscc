"""The workspaces the app serves, and where each keeps its units, run log and metadata.

`Workspaces` is the one object that answers both: which workspaces there are (listing,
adopting, labelling, removing and pulling one), and, for a workspace, its journal key, its
units' root, a unit's directory and the `cos.db` snapshot `coscc.loop --state` decides on.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any, TypedDict

from coscc import units
from coscc.agent.policy import COMMAND_NAME, GRANTS_PREF, lists_of
from coscc.agent.sessions import Sessions
from coscc.config import Config
from coscc.data import Busy, Data
from coscc.git import gitops
from coscc.git.gitops import GitError
from coscc.runlog.journal import BadRecord, Journal
from coscc.service.common import Invalid
from coscc.service.store import BadName, Store, require_name, valid_name
from coscc.units import BadUnit, scratch
from coscc.units.history import BadTransition
from coscc.units.meta import MetaError, UnitMeta

log = logging.getLogger(__name__)


class CommandLists(TypedDict):
    allow: list[str]
    block: list[str]


def _command_names(raw: object, field: str) -> list[str]:
    """`raw` as a list of distinct command names, or `Invalid` naming the first that is not one."""
    if not isinstance(raw, list):
        raise Invalid(f"{field} must be a list of command names")
    names: list[str] = []
    for n in raw:
        if not isinstance(n, str) or not COMMAND_NAME.fullmatch(n.strip()):
            raise Invalid(f"not a command name: {str(n)[:64]!r}")
        if n.strip() not in names:
            names.append(n.strip())
    return names


def live_units(workspaces: Iterable[str], data_dir: str | None) -> dict[str, set[str]]:
    """Each workspace's `units.slot` mapped to the unit directories it has, the shape
    `scratch.sweep` takes as what to keep."""
    live: dict[str, set[str]] = {}
    for path in workspaces:
        try:
            found = {
                e.name
                for e in os.scandir(units.cos_dir(path, data_dir))
                if e.is_dir() and units.UNIT_RE.fullmatch(e.name)
            }
        except OSError:
            found = set()
        live[units.slot(path)] = found
    return live


class Workspaces:
    def __init__(self, config: Config, sessions: Sessions) -> None:
        self.config = config
        self.sessions = sessions
        # No working folder means no store.
        self.store = Store(config.working_dir, config.data_dir) if config.working_dir else None
        # Journal keys whose `cos.db` store was imported: an import is never undone, so
        # `snapshot` stops asking once it is.
        self.imported: set[str] = set()
        # `"<workspace>/<unit>"` to `{stage: {decisions, main}}`, as `outdated.refresh` last found
        # it, and when it last read a whole workspace (`time.monotonic`). Per process.
        self.outdated: dict[str, dict[str, Any]] = {}
        self.outdated_at: dict[str, float] = {}

    # -- the list -------------------------------------------------------------

    def all(self) -> dict[str, Any]:
        """Both sources, with the count the app could not answer before the store existed.

        `source` is carried per entry: an env workspace cannot be renamed or removed from here.
        """
        rows: list[dict[str, Any]] = []
        for path in self.config.workspaces:
            rows.append(
                {
                    "name": Path(path).name,
                    "path": path,
                    "label": "",
                    "source": "env",
                    "missing": not Path(path).expanduser().is_dir(),
                }
            )
        if self.store is not None:
            for entry in self.store.entries():
                target = self.store.path_of(entry.name)
                rows.append(
                    {
                        "name": entry.name,
                        "path": str(target),
                        "label": entry.label,
                        "source": "store",
                        "missing": not target.is_dir(),
                    }
                )
        return {
            "working_dir": self.config.working_dir,
            "count": len(rows),
            "workspaces": rows,
            "paths": [r["path"] for r in rows],
        }

    def is_member(self, cwd: str) -> bool:
        """The single membership question: env list, or a store entry under the root."""
        if self.config.is_workspace(cwd):
            return True
        return self.store is not None and self.store.resolves_to_entry(cwd)

    def check(self, cwd: str) -> str:
        """The single gate. Every capability goes through it."""
        # Asked on every read, never cached: the entry has to name a segment that
        # resolves back under the root, so hand-editing the store cannot widen it.
        if self.is_member(cwd):
            return cwd
        raise Invalid(f"not a configured workspace: {cwd}")

    def name(self, cwd: str) -> str:
        """The name the app shows for `cwd`, or "" when it is not one of the workspaces."""
        here = Path(cwd).expanduser().resolve()
        for r in self.all()["workspaces"]:
            if Path(r["path"]).expanduser().resolve() == here:
                return str(r["name"])
        return ""

    def peer_table(self) -> tuple[list[tuple[str, str]], list[str]]:
        """Every workspace as `(name, path)`, and what was left out and why.

        A name two workspaces share is given for neither: a reference to it could mean
        either store. A name `valid_name` refuses (an env workspace's basename can be one)
        could not be a reference at all.
        """
        rows = self.all()["workspaces"]
        count = Counter(str(r["name"]) for r in rows)
        peers: list[tuple[str, str]] = []
        problems: list[str] = []
        for name, n in count.items():
            if n > 1:
                problems.append(
                    f"Two workspaces are named {name}, so neither is linked by that name."
                )
        for r in rows:
            name = str(r["name"])
            if count[name] == 1 and valid_name(name):
                peers.append((name, str(r["path"])))
        return peers, problems

    def peers(self) -> list[tuple[str, Path]]:
        """Every named workspace as `(name, store root)`, the stores an `Idea:` may name."""
        return [(name, self.units_root(path)) for name, path in self.peer_table()[0]]

    # -- changing the list ----------------------------------------------------

    def _store_or_refuse(self) -> Store:
        if self.store is None:
            raise Invalid(
                "no working folder configured; set COS_WORKING_DIR and restart "
                "(it is deliberately not settable over HTTP)"
            )
        return self.store

    def _name_or_refuse(self, name: str) -> str:
        try:
            return require_name(name)
        except BadName as e:
            raise Invalid(str(e)) from e

    def _row(self, name: str, label: str) -> dict[str, Any]:
        store = self._store_or_refuse()
        target = store.path_of(name)
        return {
            "name": name,
            "path": str(target),
            "label": label,
            "source": "store",
            "missing": not target.is_dir(),
        }

    async def add(self, name: str, label: str = "", repo_url: str | None = None) -> dict[str, Any]:
        """Add by adopting a directory already under the root, or by cloning into it.

        Order matters: clone into a temp directory, rename into place, and only then write the store. The worst a failure leaves is a temp directory, never a listed workspace that does not work.
        """
        store = self._store_or_refuse()
        self._name_or_refuse(name)
        if any(e.name == name for e in store.entries()):
            raise Invalid(f"workspace already exists: {name}")

        target = store.path_of(name)
        if repo_url:
            if target.exists():
                raise Invalid(f"directory already exists: {target}")
            store.working_dir.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(dir=store.working_dir, prefix=".cos-clone-"))
            try:
                await gitops.clone(repo_url, staging / name)
                os.replace(staging / name, target)
            except GitError as e:
                raise Invalid(str(e)) from e
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        elif not target.is_dir():
            raise Invalid(f"no such directory under the working folder: {target}")

        entry = store.add(name, label)
        return self._row(entry.name, entry.label)

    def set_label(self, name: str, label: str) -> dict[str, Any]:
        store = self._store_or_refuse()
        self._name_or_refuse(name)
        try:
            entry = store.set_label(name, label)
        except KeyError as e:
            raise Invalid(f"no such workspace: {name}") from e
        return self._row(entry.name, entry.label)

    def remove(self, name: str) -> dict[str, Any]:
        """Drops the entry, and the scratch of every unit that is not in a workspace left. The
        directory stays."""
        store = self._store_or_refuse()
        self._name_or_refuse(name)
        try:
            store.remove(name)
        except KeyError as e:
            raise Invalid(f"no such workspace: {name}") from e
        scratch.sweep(live_units(self.all()["paths"], self.config.data_dir), self.config.data_dir)
        return {"removed": name, "count": len(self.all()["workspaces"])}

    async def pull(self, name: str) -> dict[str, Any]:
        """Fast-forward only. A failure comes back with its output.

        Refused while a session is live here, as an `Invalid` like every other reason a pull fails.
        """
        store = self._store_or_refuse()
        self._name_or_refuse(name)
        if not any(e.name == name for e in store.entries()):
            raise Invalid(f"no such workspace: {name}")
        target = store.path_of(name)
        if not target.is_dir():
            raise Invalid(f"workspace directory is missing: {target}")
        # This has to come before `gitops`: a fast-forward rewrites files under a turn that is
        # already reading them. The answer covers this process only; a second app holding a
        # session here is not seen.
        live = self.sessions.live_in(str(target))
        if live:
            raise Invalid(
                f"workspace {name} has {len(live)} live session(s) — "
                "pull would change files under them. Finish or reload, then try again."
            )
        try:
            output = await gitops.pull(target)
        except GitError as e:
            raise Invalid(str(e)) from e
        return {"name": name, "output": output}

    # -- where a workspace keeps its things -----------------------------------

    @staticmethod
    def key(cwd: str) -> str:
        """How a workspace is named in the journal.

        The resolved path, not a store name: an env-declared workspace has no name at all
        (`config.is_workspace`), and a path is the one identifier both kinds have. The
        cost is that moving a workspace detaches its history from it.
        """
        return str(Path(cwd).expanduser().resolve())

    # -- what `impl` may run here ---------------------------------------------

    def command_lists(self, cwd: str) -> CommandLists:
        """The commands `impl` gains and loses in this workspace."""
        allow, block = lists_of(Data(self.config.data_dir).pref(GRANTS_PREF, {}), self.key(cwd))
        return {"allow": list(allow), "block": list(block)}

    def set_command_lists(self, cwd: str, allow: object, block: object) -> CommandLists:
        """Replace both lists; `block` wins over `allow` and over `impl`'s own commands. A name
        that is not a command's (`COMMAND_NAME`) is `Invalid` and nothing changes. Leaves one
        `grant-config` record, whose failure does not undo the save."""
        self.check(cwd)
        lists: CommandLists = {
            "allow": _command_names(allow, "allow"),
            "block": _command_names(block, "block"),
        }
        data = Data(self.config.data_dir)
        key = self.key(cwd)
        stored = data.pref(GRANTS_PREF, {})
        stored = stored if isinstance(stored, dict) else {}
        stored[key] = lists
        data.set_pref(GRANTS_PREF, stored)
        journal = self.journal()
        if journal is not None:
            try:
                journal.append(
                    {"kind": "grant-config", "workspace": key, **lists, "actor": "human:owner"}
                )
            except BadRecord, Busy:
                log.warning("the grant-config record for %s was not written", key)
        return lists

    def journal(self) -> Journal | None:
        """The run log, or `None` when there is no working folder to keep it in.

        Unset `COS_WORKING_DIR` means the board is read-only: there is nowhere to record a mode,
        so every step reads `manual` and nothing can be started. That is the safe way to fail.
        """
        return (
            Journal(self.config.working_dir, self.config.data_dir)
            if self.config.working_dir
            else None
        )

    def unit_meta(self) -> UnitMeta:
        """The unit metadata store. Unlike the journal there is one with no working folder too,
        keyed by the data directory: every board read needs a snapshot."""
        data = Data(self.config.data_dir)
        return UnitMeta(self.config.working_dir or data.root, data)

    def units_root(self, cwd: str) -> Path:
        """Where this workspace's units live. One question, asked of one module.

        `coscc/units/__init__.py` owns the answer; this is the only place in the service that asks.
        """
        return units.root(cwd, self.config.data_dir)

    def unit_dir(self, cwd: str, unit: str) -> Path:
        try:
            return units.unit_dir(cwd, unit, self.config.data_dir)
        except BadUnit as e:
            raise Invalid(str(e)) from e

    def snapshot(
        self,
        cwd: str,
        units_: Iterable[str] | None = None,
        peers: list[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        """What `coscc.loop --state` decides on: `cwd`'s units and those of every workspace a link
        may name, from `cos.db`; `units_` narrows it as `UnitMeta.snapshot` says. A store not
        imported yet is imported first.

        Raises `Invalid` when an import cannot run: a board read on metadata nobody could
        read would show every unit as not started. So also when `cos.db` cannot be read, in one
        sentence that names the workspace; the error, which may name the database's path, goes
        to the log. `peers` is `peer_table`'s, when the caller read it already.
        """
        meta = self.unit_meta()
        own = self.key(cwd)
        names = {
            name: self.key(path)
            for name, path in (self.peer_table()[0] if peers is None else peers)
        }
        try:
            for key in {own, *names.values()} - self.imported:
                # A workspace with no units yet has nothing to import: one `stat`, not a query.
                if not (units.root(key, self.config.data_dir) / units.COS_DIR).is_dir():
                    continue
                if meta.imported(key):
                    # The answers imported before a row said whose each was.
                    meta.classify_answers(key)
                    self.imported.add(key)
                else:
                    self._import(meta, key)
            snap = meta.snapshot(own, names, units_)
        except (Busy, sqlite3.Error, OSError) as e:
            log.warning("the units of %s could not be read: %s", own, e)
            raise Invalid(
                f"the units of {self.name(own) or 'a workspace'} could not be read"
            ) from e
        # The app's own finding, which no store holds: what came after an accepted spec or plan.
        for at, entry in snap["units"].items():
            if at in self.outdated:
                entry["outdated"] = self.outdated[at]
        return snap

    def meta_of(self, cwd: str, unit: str) -> dict[str, Any]:
        """`unit`'s entry in the snapshot, `{}` when the app has none."""
        snap = self.snapshot(cwd, [unit])
        return snap["units"].get(f"{snap['workspace']}/{unit}") or {}

    def _import(self, meta: UnitMeta, key: str) -> None:
        """One store into `cos.db`, once; what it could not read, if anything, goes to
        the log and to one `import` row of the run log. A store with no `.cos/` yet is left
        for later."""
        store = units.root(key, self.config.data_dir)
        if not (store / units.COS_DIR).is_dir():
            return
        try:
            unknowns = meta.import_store(key, store)
        except (MetaError, BadTransition, Busy, sqlite3.Error, OSError) as e:
            # The workspace by name and the error in the log: `key` is a path, and `Busy` and
            # `MetaError` carry the database's path or the loop's stderr.
            log.warning("the units of %s could not be imported: %s", key, e)
            raise Invalid(
                f"the units of {self.name(key) or 'a workspace'} could not be imported"
            ) from e
        # Only when there is something to report, so a store read cleanly adds no row.
        if not unknowns:
            return
        for u in unknowns:
            log.warning(
                "import %s: %s %s %s: %s", key, u["unit"], u["artifact"], u["field"], u["reason"]
            )
        journal = self.journal()
        if journal is not None:
            try:
                journal.append({"kind": "import", "workspace": key, "unknowns": unknowns})
            except BadRecord, Busy:
                pass
