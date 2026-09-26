"""The workspaces the app serves: listing, adopting, labelling, removing and pulling one.

Split from `coscc/service.py` (`0095`), whose `Service` inherits it; a mixin with no fields.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from coscc import gitops
from coscc.gitops import GitError
from coscc.store import BadName, Store, require_name
from coscc.service_common import Invalid


class WorkspacesMixin:

    # -- workspaces ---------------------------------------------------------

    def workspaces(self) -> dict[str, Any]:
        """Both sources, with the count the app could not answer before the store existed.

        `source` is carried per entry rather than merged away: an env workspace cannot be
        renamed or removed from here, and a caller has to be able to tell.
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
            # Kept so the original shape still reads: it only ever asked for paths.
            "paths": [r["path"] for r in rows],
        }

    # -- changing the list --------------------------------------------------

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

    async def add_workspace(
        self, name: str, label: str = "", repo_url: str | None = None
    ) -> dict[str, Any]:
        """Add by adopting a directory already under the root, or by cloning into it.

        Both are `intent.md`'s scope line, where "thêm" and "clone" are separate entries.

        Order matters and is the whole of `spec.md` R16: clone into a temp directory,
        rename into place, and only then write the store. The worst state a failure can
        leave is a temp directory nobody cleaned — never a listed workspace that does not
        work.
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

    def remove_workspace(self, name: str) -> dict[str, Any]:
        """Drops the entry only. The directory stays — `spec.md` R18 and C6."""
        store = self._store_or_refuse()
        self._name_or_refuse(name)
        try:
            store.remove(name)
        except KeyError as e:
            raise Invalid(f"no such workspace: {name}") from e
        return {"removed": name, "count": len(self.workspaces()["workspaces"])}

    async def pull_workspace(self, name: str) -> dict[str, Any]:
        """Fast-forward only. A failure comes back with its output — `spec.md` R20.

        Refused outright while a session is live here (`spec.md` R6). The refusal is an
        `Invalid` like every other reason a pull fails, so it reaches the page through the
        path R20 already built rather than through one of its own — R8.
        """
        store = self._store_or_refuse()
        self._name_or_refuse(name)
        if not any(e.name == name for e in store.entries()):
            raise Invalid(f"no such workspace: {name}")
        target = store.path_of(name)
        if not target.is_dir():
            raise Invalid(f"workspace directory is missing: {target}")
        # R6, and this has to come before `gitops`: a fast-forward rewrites files
        # under a turn that is already reading them, and the turn cannot be told. The
        # answer covers this process only (`spec.md` C2) — a second app holding a session
        # here is not seen, and the pull will go ahead.
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
