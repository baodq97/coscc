"""The workspace list, and the reason a bad entry cannot become a bad path.

The safety property here is a data shape, not a check. A stored entry holds a `name` —
**one path segment** — and never an absolute path, so there is no field in which a
hand-edited file could put `/etc`. The real path is built from the working folder on every
read (`spec.md` R12). `is_under` stays as a second layer, but the first layer is that the
dangerous value has nowhere to live.

This is the first state the app owns (`spec.md` C8). It holds workspaces and labels, and
nothing else: conversation content belongs to the SDK's session store, which stays the one
source of truth for anything said.

Writes go to a temp file and are renamed into place, so a crash mid-write leaves the old
list rather than half of a new one. A lock serialises writers inside this process; two
processes sharing one working folder is a known gap (`spec.md` open question 11).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

VERSION = 1
STORE_FILENAME = ".cos-baodo.json"

# One path segment. No separators, no `.`/`..`, bounded length. `spec.md` R12 lists the
# inputs this has to turn away.
_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
LABEL_MAX = 200


class BadName(ValueError):
    """A name that may not become a directory under the working folder."""


def valid_name(name: str) -> bool:
    return bool(_NAME.fullmatch(name or "")) and name not in {".", ".."}


def require_name(name: str) -> str:
    if not valid_name(name):
        raise BadName(
            f"invalid workspace name: {name!r} "
            "(1-64 chars of letters, digits, dot, dash or underscore; not '.' or '..')"
        )
    return name


def clean_label(label: str | None) -> str:
    return (label or "").strip()[:LABEL_MAX]


@dataclass(frozen=True)
class Entry:
    name: str
    label: str = ""


class Store:
    """The workspace list for one working folder.

    Every method re-reads the file. That is deliberate: `spec.md` R21 wants membership
    decided at read time, and a cached list is exactly the thing that made `0002`'s gate
    safe for a reason that no longer holds.
    """

    def __init__(self, working_dir: str | os.PathLike[str]):
        self.working_dir = Path(working_dir).expanduser().resolve()
        self.path = self.working_dir / STORE_FILENAME
        self._lock = threading.Lock()

    # -- reading ------------------------------------------------------------

    def entries(self) -> list[Entry]:
        """Entries from disk, with anything unusable dropped rather than repaired.

        A name that does not pass `valid_name` is ignored, not corrected: the file is
        editable by hand, and a repair would write back something the user did not ask
        for. Dropping it keeps the invariant without touching their file.
        """
        raw = self._read()
        out: list[Entry] = []
        for item in raw.get("workspaces", []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", ""))
            if not valid_name(name):
                continue
            out.append(Entry(name=name, label=clean_label(item.get("label"))))
        return out

    def path_of(self, name: str) -> Path:
        """The only way a stored entry becomes a path. Built, never read from the file."""
        return self.working_dir / require_name(name)

    def is_under(self, directory: str) -> bool:
        """Second layer: does this resolve to something inside the working folder?"""
        try:
            target = Path(directory).expanduser().resolve()
        except OSError:
            return False
        return target != self.working_dir and self.working_dir in target.parents

    def resolves_to_entry(self, directory: str) -> bool:
        """Membership: some entry's built path equals this directory.

        Both layers apply. `is_under` alone would accept any subdirectory of the working
        folder, including ones nobody added.
        """
        try:
            target = Path(directory).expanduser().resolve()
        except OSError:
            return False
        if not self.is_under(directory):
            return False
        return any(self.path_of(e.name) == target for e in self.entries())

    # -- writing ------------------------------------------------------------

    def add(self, name: str, label: str = "") -> Entry:
        require_name(name)
        with self._lock:
            items = [e for e in self.entries() if e.name != name]
            entry = Entry(name=name, label=clean_label(label))
            self._write(items + [entry])
            return entry

    def set_label(self, name: str, label: str) -> Entry:
        require_name(name)
        with self._lock:
            items = self.entries()
            if not any(e.name == name for e in items):
                raise KeyError(name)
            updated = [
                Entry(e.name, clean_label(label)) if e.name == name else e for e in items
            ]
            self._write(updated)
            return next(e for e in updated if e.name == name)

    def remove(self, name: str) -> None:
        """Drops the entry. Never touches the directory — `spec.md` R18 and C6."""
        require_name(name)
        with self._lock:
            items = self.entries()
            if not any(e.name == name for e in items):
                raise KeyError(name)
            self._write([e for e in items if e.name != name])

    # -- disk ---------------------------------------------------------------

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return {"version": VERSION, "workspaces": []}
        if not isinstance(data, dict):
            return {"version": VERSION, "workspaces": []}
        return data

    def _write(self, entries: list[Entry]) -> None:
        self.working_dir.mkdir(parents=True, exist_ok=True)
        payload = {"version": VERSION, "workspaces": [asdict(e) for e in entries]}
        fd, tmp = tempfile.mkstemp(dir=self.working_dir, prefix=".cos-baodo-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(payload, fh, indent=2)
                fh.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
