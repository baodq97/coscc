"""The only place business logic lives.

`spec.md` R10: the page and the JSON API are two entry points to one capability, and two
implementations of one capability is the surest way to have one of them fixed and the other
not. So neither an HTTP route nor a Reflex event handler may decide anything — they
translate a request into a call here, and a result back into their own shape.

The rule that makes this checkable: nothing in this module imports a web framework, and
nothing above it branches on business state. A conditional in a route is a bug in this
file, not in the route.

`Invalid` is how this layer refuses. Callers map it to their own vocabulary — 400 for
HTTP, an error banner for the page — and neither gets to invent a different reason.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from cos_baodo import gitops
from cos_baodo import sessions as reader
from cos_baodo.config import Config
from cos_baodo.gitops import GitError
from cos_baodo.sessions import Sessions
from cos_baodo.store import BadName, Store, require_name


class Invalid(Exception):
    """A request this layer refuses, carrying a reason a caller can show verbatim."""


@dataclass
class Service:
    config: Config
    sessions: Sessions
    store: Store | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        # No working folder means no store, and the app behaves exactly as `0002` did.
        # That is what keeps `scripts/verify_0002.py` running unchanged (`spec.md` R6).
        self.store = Store(self.config.working_dir) if self.config.working_dir else None

    # -- workspaces ---------------------------------------------------------

    def workspaces(self) -> dict[str, Any]:
        """Both sources, with the count the app could not answer before `0003`.

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
            # Kept so `0002`'s shape still reads: it only ever asked for paths.
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
        """Fast-forward only. A failure comes back with its output — `spec.md` R20."""
        store = self._store_or_refuse()
        self._name_or_refuse(name)
        if not any(e.name == name for e in store.entries()):
            raise Invalid(f"no such workspace: {name}")
        target = store.path_of(name)
        if not target.is_dir():
            raise Invalid(f"workspace directory is missing: {target}")
        try:
            output = await gitops.pull(target)
        except GitError as e:
            raise Invalid(str(e)) from e
        return {"name": name, "output": output}

    # -- sessions -----------------------------------------------------------

    def _workspace_or_refuse(self, cwd: str) -> str:
        """The single gate. Every capability below goes through it.

        `spec.md` R21 wants this asked on every read rather than cached, because after
        `0003` the workspace list is no longer fixed for the life of the process.
        """
        if self.config.is_workspace(cwd):
            return cwd
        # The store half. Membership is recomputed from the working folder every time,
        # so editing the file by hand cannot widen what this accepts — the entry has to
        # name a segment, and the segment has to resolve back under the root.
        if self.store is not None and self.store.resolves_to_entry(cwd):
            return cwd
        raise Invalid(f"not a configured workspace: {cwd}")

    def sessions_for(self, cwd: str, limit: int | None = None) -> dict[str, Any]:
        self._workspace_or_refuse(cwd)
        rows = reader.list_for_directory(cwd, limit=limit)
        for row in rows:
            # Terminal sessions show up here too — the read layer sees them. This flag is
            # what tells a caller which of them it may write to (`0002` spec.md C1).
            row["resumable"] = self.config.may_resume(
                self.sessions.created_here(row["session_id"])
            )
        return {"cwd": cwd, "sessions": rows}

    def history(self, cwd: str, session_id: str) -> dict[str, Any]:
        self._workspace_or_refuse(cwd)
        if not session_id:
            raise Invalid("session_id is required")
        return {
            "session_id": session_id,
            "messages": reader.history(session_id, cwd),
        }

    def check_send(self, cwd: str, text: str) -> None:
        """Everything a caller can reject with a status code, decided before any output.

        Split out from `stream` on purpose. `0002` draws a hard line between two kinds of
        failure: an invalid request is a status code, while a refusal that surfaces once
        the reply is already streaming has to arrive as data, because the status line is
        long gone (`web.py` docstring on `post_send`). Validating inside an async
        generator would collapse that distinction, since the first item is only pulled
        after a caller has committed to streaming. A test in `web_test.py` holds the line.
        """
        self._workspace_or_refuse(cwd)
        if not text.strip():
            raise Invalid("text is required")

    async def stream(
        self, cwd: str, text: str, session_id: str | None = None
    ) -> AsyncIterator[tuple[str, Any]]:
        """Yield `(kind, payload)` exactly as the session layer does.

        Re-runs `check_send` so the generator is safe on its own; the checks are pure, so
        doing them twice costs nothing and leaves no caller able to skip them.
        """
        self.check_send(cwd, text)
        async for item in self.sessions.stream(cwd, text, session_id):
            yield item
