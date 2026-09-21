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
list rather than half of a new one.

**Concurrency, and why the lock is where it is.** `0005` measured the old arrangement: four
processes adding five workspaces each to one working folder left 8 of 20, with no error
anywhere. The loss was never two writes colliding — it was two read-then-write sequences
interleaving, each reading the old list and each writing back what it computed. So the lock
covers the whole transaction, not the write, and it lives on a **separate file**: the store
itself is replaced by `rename` on every write, and a lock held on it would go with it.

The in-process lock is kept as well. It is right for threads here, cheap, and always taken
in the same order as the file lock, so the two cannot deadlock against each other.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

VERSION = 1
STORE_FILENAME = ".cos-baodo.json"
LOCK_FILENAME = ".cos-baodo.lock"

# How long to wait for another process to finish its transaction. Chosen, not measured
# (`spec.md` C3): the work under the lock is reading and writing a few hundred bytes, so
# ten seconds is already enormous. It exists to turn an indefinite hang into an error, not
# to wait for anyone. Worth revisiting after real use rather than trusting.
LOCK_TIMEOUT = 10.0
LOCK_POLL = 0.01

# One path segment. No separators, no `.`/`..`, bounded length. `spec.md` R12 lists the
# inputs this has to turn away.
_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
LABEL_MAX = 200


class BadName(ValueError):
    """A name that may not become a directory under the working folder."""


class Busy(RuntimeError):
    """Another process held the workspace list for too long.

    Raised rather than waited out. `spec.md` C7: this reads like a broken app, so the
    message has to say that something else is holding it — swapping a silent loss for a
    baffling error would not be much of a trade.
    """


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
        self.lock_path = self.working_dir / LOCK_FILENAME
        self._lock = threading.Lock()

    @contextmanager
    def transaction(self, timeout: float | None = None):
        """Hold the list exclusively for one read-modify-write.

        The file lock is advisory and per descriptor, which is why it is taken on a file
        that exists only to be locked. `flock` is released by the kernel when the holder
        dies, so a crashed process cannot wedge the folder — `spec.md` R3 in the one form
        that does not depend on anyone remembering to release it.
        """
        # Read at call time, not bound as a default, so the deadline is one value a test
        # can shorten — a test that had to wait the real timeout would not be run.
        timeout = LOCK_TIMEOUT if timeout is None else timeout
        self.working_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            try:
                deadline = time.monotonic() + timeout
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError as e:
                        if e.errno not in (errno.EACCES, errno.EAGAIN):
                            raise
                        if time.monotonic() >= deadline:
                            raise Busy(
                                f"another process is holding {self.working_dir} "
                                f"(waited {timeout:.0f}s) — try again in a moment"
                            ) from e
                        time.sleep(LOCK_POLL)
                try:
                    yield
                finally:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

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
        with self.transaction():
            items = [e for e in self.entries() if e.name != name]
            entry = Entry(name=name, label=clean_label(label))
            self._write(items + [entry])
            return entry

    def set_label(self, name: str, label: str) -> Entry:
        require_name(name)
        with self.transaction():
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
        with self.transaction():
            items = self.entries()
            if not any(e.name == name for e in items):
                raise KeyError(name)
            self._write([e for e in items if e.name != name])

    # -- disk ---------------------------------------------------------------

    def _read(self) -> dict:
        # Only ever the store file. `spec.md` R4: the lock file sits beside it and is
        # never read as a list.
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
