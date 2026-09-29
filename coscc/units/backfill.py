"""Turning a repository's git history of `.cos/` into transitions.

A way of producing rows for the log, not of reading state: it infers a state from the
`Status:` line of each blob, once, over history that has happened. It only reads (`rev-parse`,
`log`, `cat-file`). The commit author is not recorded as the actor, since an agent wrote
most artifacts under one person's git identity; `source` carries `commit:<sha>` instead.
Renames are not followed (`--no-renames`), so a renumbered unit's history is split across
two names. Retired units are imported too.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from coscc.units import states
from coscc.git.gitops import child_env
from coscc.units.history import UNKNOWN, History
from coscc.units.states import Machine

# The directory a unit's artifacts live in, inside the repository being read.
COS_DIR = ".cos"

# Seconds. Bounds a child process so a page fails instead of hanging.
TIMEOUT = 120.0

# Record separators for `git log`. Start-of-heading begins a commit, unit separator splits
# its fields; neither can appear in a path, a hash or a date. Not NUL: an argv element may
# not contain one.
_COMMIT = "\x01"
_FIELD = "\x1f"


class NotAGitCheckout(RuntimeError):
    """The path given is not a git repository, or git could not be run."""


def _git(repo: Path, *args: str, binary: bool = False, stdin: bytes | None = None):
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), *args],
            env=child_env(),
            capture_output=True,
            input=stdin,
            timeout=TIMEOUT,
            check=True,
        )
    except FileNotFoundError as e:
        raise NotAGitCheckout(f"could not run git for {repo}: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise NotAGitCheckout(
            f"git {' '.join(args)} in {repo} did not finish in {TIMEOUT:.0f}s"
        ) from e
    except subprocess.CalledProcessError as e:
        # The path goes in every message; a refusal that does not name the directory is a bug.
        detail = (e.stderr or b"").decode("utf-8", "replace").strip()
        raise NotAGitCheckout(f"git {' '.join(args)} failed in {repo}: {detail}") from e
    return done.stdout if binary else done.stdout.decode("utf-8", "replace")


def require_checkout(repo: str | Path) -> Path:
    """The repository root containing `repo`, or `NotAGitCheckout` naming what went wrong.

    `--show-toplevel`, not `--git-dir`: the latter succeeds from any subdirectory and a wrong
    path would give an empty history and no complaint.
    """
    path = Path(repo).expanduser().resolve()
    if not path.is_dir():
        raise NotAGitCheckout(f"{path} is not a directory")
    top = _git(path, "rev-parse", "--show-toplevel").strip()
    if not top:
        raise NotAGitCheckout(f"{path} is not inside a git checkout")
    return Path(top).resolve()


def _utc(stamp: str) -> str:
    """One timestamp format for the whole table: UTC at second resolution.

    `%aI` carries the commit's own offset, which sorts wrongly as text.
    """
    try:
        return (
            datetime.fromisoformat(stamp)
            .astimezone(timezone.utc)
            .isoformat(timespec="seconds")
        )
    except ValueError:
        return stamp


def _commits(repo: Path, cos_dir: str) -> Iterator[tuple[str, str, list[tuple[str, str]]]]:
    """`(sha, when, [(letter, path), ...])` for every commit touching `cos_dir`, oldest first.

    `--no-renames` and `--reverse` are load-bearing: transitions chain in the order they happened.
    """
    raw = _git(
        repo,
        "log",
        "--reverse",
        "--no-renames",
        f"--format={_COMMIT}%H{_FIELD}%aI",
        "--name-status",
        "--",
        cos_dir,
    )
    for block in raw.split(_COMMIT):
        block = block.strip("\n")
        if not block:
            continue
        head, _, rest = block.partition("\n")
        sha, _, when = head.partition(_FIELD)
        changes = []
        for line in rest.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            changes.append((parts[0][:1], parts[-1]))
        yield sha.strip(), _utc(when.strip()), changes


def _split(path: str, cos_dir: str, machine: Machine) -> tuple[str, str] | None:
    """`(unit, artifact)` for a path this state set recognises, else `None`."""
    parts = Path(path).parts
    if len(parts) != 3 or parts[0] != cos_dir:
        return None
    unit, artifact = parts[1], parts[2]
    return (unit, artifact) if machine.knows(artifact) else None


def _blobs(repo: Path, wanted: list[str]) -> dict[str, str]:
    """`{"<sha>:<path>": text}` for many blobs, in one `cat-file --batch` child process.

    A missing object answers `<request> missing`, an ordinary case: a commit that deleted a
    file has no blob for it.
    """
    if not wanted:
        return {}
    payload = ("\n".join(wanted) + "\n").encode("utf-8")
    raw = _git(repo, "cat-file", "--batch", binary=True, stdin=payload)

    out: dict[str, str] = {}
    at = 0
    for request in wanted:
        end = raw.find(b"\n", at)
        if end < 0:
            break
        header = raw[at:end].decode("utf-8", "replace")
        at = end + 1
        if header.endswith(" missing") or header.endswith(" ambiguous"):
            continue
        try:
            size = int(header.rsplit(" ", 1)[1])
        except (IndexError, ValueError):
            break
        out[request] = raw[at : at + size].decode("utf-8", "replace")
        at += size + 1
    return out


def _status_of(text: str, machine: Machine, artifact: str) -> str | None:
    """The state this blob declares (the first `Status:`, case-insensitively), or `None` when none this set knows."""
    for line in text.splitlines():
        marker = line.lower().find("status:")
        if marker < 0:
            continue
        word = line[marker + len("status:") :].strip().split()
        if not word:
            continue
        candidate = word[0].strip(". ").lower()
        if machine.allows(artifact, candidate):
            return candidate
        return None
    return None


def scan(
    repo: str | Path,
    machine: Machine | None = None,
    cos_dir: str = COS_DIR,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Every transition git can see, oldest first. Reads; writes nothing.

    Rows carry no `from_state`: the log derives it from the row before it.
    """
    machine = machine or states.default()
    root = require_checkout(repo)
    key = workspace if workspace is not None else str(root)

    pending: list[tuple[str, str, str, str, str]] = []  # sha, when, path, unit, artifact
    wanted: list[str] = []
    for sha, when, changes in _commits(root, cos_dir):
        for letter, path in changes:
            split = _split(path, cos_dir, machine)
            if split is None:
                continue
            unit, artifact = split
            pending.append((sha, when, path, unit, artifact))
            if letter != "D":
                wanted.append(f"{sha}:{path}")

    blobs = _blobs(root, wanted)

    rows: list[dict[str, Any]] = []
    for sha, when, path, unit, artifact in pending:
        request = f"{sha}:{path}"
        if request in blobs:
            state = _status_of(blobs[request], machine, artifact)
            if state is None:
                # Skipped rather than guessed.
                continue
        else:
            # No blob at that commit: the artifact was deleted, a transition like any other.
            state = machine.absent
        rows.append(
            {
                "workspace": key,
                "unit": unit,
                "artifact": artifact,
                "to_state": state,
                "actor": UNKNOWN,
                "session": UNKNOWN,
                "source": f"commit:{sha}",
                "at": when,
                "once_key": f"git:{key}:{sha}:{path}",
            }
        )
    return rows


def run(
    history: History, repo: str | Path, cos_dir: str = COS_DIR, workspace: str | None = None
) -> dict[str, Any]:
    """Scan and store. Returns what it found and what it actually added.

    Re-runnable: every row carries a `once_key` from its commit and path.
    """
    root = require_checkout(repo)
    key = workspace if workspace is not None else str(root)
    rows = scan(root, history.machine, cos_dir, workspace=key)
    stored = history.record_many(rows)
    return {
        "repo": str(root),
        "workspace": key,
        "scanned": len(rows),
        "added": len(stored),
        "units": sorted({row["unit"] for row in rows}),
    }
