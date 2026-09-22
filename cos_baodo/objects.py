"""Blobs on disk, named after what is in them.

`spec.md` R3 splits the app's state two ways: metadata in SQLite, objects as files. This
is the file half. The only thing living here today is the reply text of a step run — see
`spec.md` open question 1, which says plainly that one content type may not justify a
whole layer, and what would settle it.

Two properties, and each is one line of code that would be easy to get wrong:

**Atomic** (`spec.md` R11). The bytes go to a temporary file in the same directory and are
`rename`d into place. `rename` within one filesystem is atomic, so a reader sees either no
file or the whole file — never a half-written one under the real name. Writing straight to
the final path would make a crash mid-write leave a truncated object whose *name still
claims a digest it no longer has*, which is worse than losing it.

**Immutable.** The name is the SHA-256 of the contents, so writing the same bytes twice is
a no-op and two different contents cannot collide onto one name. Nothing here ever
overwrites or deletes; a caller that wants an object gone has to say so itself, and today
nothing does.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

# The first two hex characters become a directory. One flat directory with tens of
# thousands of entries is slow to list on some filesystems, and this is the cheapest fix
# that needs no index. Not measured on this machine -- taken from common practice.
FANOUT = 2

DIR_MODE = 0o700
FILE_MODE = 0o600


class Missing(KeyError):
    """Asked for an object that is not here."""


class Objects:
    """The object directory under the data root."""

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root).expanduser().resolve()

    @staticmethod
    def digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def path_of(self, digest: str) -> Path:
        """Where an object with this digest lives. Built, never stored.

        Same reasoning as `store.path_of`: a path that is computed cannot be a path
        somebody wrote into a database.
        """
        clean = (digest or "").strip().lower()
        if len(clean) != 64 or any(c not in "0123456789abcdef" for c in clean):
            raise ValueError(f"not a sha-256 digest: {digest!r}")
        return self.root / clean[:FANOUT] / clean

    # -- writing ------------------------------------------------------------

    def put(self, data: bytes) -> str:
        """Store bytes, return their digest. Storing the same bytes again does nothing."""
        digest = self.digest(data)
        target = self.path_of(digest)
        if target.exists():
            return digest

        target.parent.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                # The rename is atomic with respect to other processes, but it does not
                # order the data against a power cut. `fsync` before the rename is what
                # makes the file's contents durable before its name exists.
                os.fsync(fh.fileno())
            os.chmod(tmp, FILE_MODE)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return digest

    def put_text(self, text: str) -> str:
        return self.put(text.encode("utf-8"))

    # -- reading ------------------------------------------------------------

    def has(self, digest: str) -> bool:
        try:
            return self.path_of(digest).is_file()
        except ValueError:
            return False

    def get(self, digest: str) -> bytes:
        target = self.path_of(digest)
        try:
            return target.read_bytes()
        except OSError as e:
            raise Missing(digest) from e

    def get_text(self, digest: str, default: str | None = None) -> str:
        """Text of an object, or `default` when it is not here.

        A default is offered because the caller that reads these is a page rendering a
        timeline: an object that has been removed by hand should leave a gap in the page,
        not a traceback in the middle of a list.
        """
        try:
            # `errors="replace"`: the bytes came from a model reply and were written as
            # UTF-8, but an object directory is editable by hand like everything else here.
            return self.get(digest).decode("utf-8", errors="replace")
        except Missing:
            if default is None:
                raise
            return default
