"""Where a workspace's work units live, and how a new one is started.

Units live under the app's data root, never in the repository: the repository receives only
the branch, a step's own code commits, the pull request body and one review comment per round.
`coscc.loop` stays the one place the loop is defined; `create` is a shell around `new-path`, and
`unit_dir` is the one function that answers where a unit's directory is.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Sequence

# `coscc.store.db` and `coscc.loop.run` are imported where they are used: the loop child imports
# `coscc.units.guards`, so it runs this file, and must load neither the database nor the helper
# that started it (`tests/test_layers.py`).

# The directory the loop reads, inside whatever root it is given.
COS_DIR = ".cos"

UNITS_DIR = "units"

# `NNNN_slug`: the only shape `new-path` produces and the only one accepted back.
UNIT_RE = re.compile(r"\d{4}_[a-z0-9]+(?:-[a-z0-9]+)*")

# Turns a hung `coscc.loop` child into an error; it does not bound the work.
TIMEOUT = 10.0

# Hex characters of the workspace path digest kept in the directory name (48 bits).
_DIGEST = 12


class BadUnit(ValueError):
    """A unit name, slug or workspace this module will not act on."""


class CannotCreate(RuntimeError):
    """A unit that could not be started, carrying what the loop or the disk said."""


def key(workspace: str | os.PathLike[str]) -> str:
    """How a workspace is identified: the resolved path, and nothing else."""
    return str(Path(workspace).expanduser().resolve())


def slot(workspace: str | os.PathLike[str]) -> str:
    """The directory name for one workspace: its basename, then a digest of its path."""
    identity = key(workspace)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:_DIGEST]
    name = Path(identity).name or "workspace"
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", name)[:40]
    return f"{safe}-{digest}"


def root(workspace: str | os.PathLike[str], data_dir: str | os.PathLike[str] | None = None) -> Path:
    """The directory to hand `coscc.loop --root`. Its `.cos/` holds this workspace's units."""
    from coscc.store.db import Data

    return Data(data_dir).root / UNITS_DIR / slot(workspace)


def spike_dir(
    workspace: str | os.PathLike[str], unit: str, data_dir: str | os.PathLike[str] | None = None
) -> Path:
    """The throwaway directory a `spike` step runs in, beside `units/` and outside every checkout."""
    from coscc.store.db import Data

    return Data(data_dir).root / "spikes" / slot(workspace) / unit


def cos_dir(
    workspace: str | os.PathLike[str], data_dir: str | os.PathLike[str] | None = None
) -> Path:
    return root(workspace, data_dir) / COS_DIR


def unit_dir(
    workspace: str | os.PathLike[str],
    unit: str,
    data_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """One unit's directory. The name is validated, never trusted, so a request cannot walk out."""
    if not UNIT_RE.fullmatch(unit or ""):
        raise BadUnit(f"not a work unit name: {unit!r}")
    return cos_dir(workspace, data_dir) / unit


def _cos(root_path: Path, *args: str, stdin: str | None = None) -> str:
    """Run `python -m coscc.loop` against a root and return its stdout, or raise `CannotCreate`.

    Always this app's loop, never code found inside a workspace.
    """
    from coscc.loop import run

    try:
        done = run.ask_sync(["--root", str(root_path), *args], stdin=stdin, timeout=TIMEOUT)
    except TimeoutError as e:
        raise CannotCreate(f"coscc.loop {' '.join(args)} did not finish in {TIMEOUT:.0f}s") from e
    except OSError as e:
        raise CannotCreate(f"could not run coscc.loop: {e}") from e
    if done.code != 0:
        # Its words, not ours: `new-path` explains a malformed slug better than a second validator.
        raise CannotCreate((done.err or done.out or "").strip() or "coscc.loop refused")
    return done.out.strip()


def branch_name(
    workspace: str | os.PathLike[str],
    unit: str,
    data_dir: str | os.PathLike[str] | None = None,
    state: dict[str, Any] | None = None,
) -> str:
    """The branch this unit's `Type:` implies, from the loop's `unit-branch`.

    `state` is the app's snapshot, where the `Type:` is; `intent.md` must be on disk too.
    """
    directory = unit_dir(workspace, unit, data_dir)  # validates before it reaches a command
    if not directory.is_dir():
        raise CannotCreate(f"no such work unit in this workspace: {unit}")
    if not (directory / "intent.md").is_file():
        # The loop's `unit-branch` says `No such work unit` here, false of the unit; say what is missing.
        raise CannotCreate(
            f"{unit} has no intent.md yet, and the branch name comes from the Type: "
            "declared in it — run the intent stage first"
        )
    if state is None:
        return _cos(root(workspace, data_dir), "unit-branch", unit)
    return _cos(
        root(workspace, data_dir),
        "--state",
        "-",
        "unit-branch",
        unit,
        stdin=json.dumps(state, ensure_ascii=False),
    )


def create(
    workspace: str | os.PathLike[str],
    slug: str,
    brief: str = "",
    data_dir: str | os.PathLike[str] | None = None,
    reserve_from: Sequence[str | os.PathLike[str]] = (),
) -> dict[str, Any]:
    """Start a work unit: allocate the number, make the directory, record the brief.

    `reserve_from` names directories whose `.cos/` numbers count as taken. The flags go
    **before** `new-path`: a trailing flag would be read as nothing and hand out a duplicate
    number silently, a leading one is refused with exit 2 and arrives as `CannotCreate`.
    `brief` is the originator's own words, written as `idea.md`.
    """
    store = root(workspace, data_dir)
    (store / COS_DIR).mkdir(parents=True, exist_ok=True)

    reserve = [
        a for d in reserve_from for a in ("--reserve-from", str(Path(d).expanduser().resolve()))
    ]
    printed = _cos(store, *reserve, "new-path", str(slug or "").strip())
    relative = printed.splitlines()[-1].strip() if printed else ""
    if not relative:
        raise CannotCreate("coscc.loop new-path printed nothing")

    # `new-path` prints a path relative to the root; check the join stays inside the store.
    directory = (store / relative).resolve()
    if store.resolve() not in directory.parents:
        raise CannotCreate(f"coscc.loop named a path outside the store: {relative}")

    unit = directory.name
    if not UNIT_RE.fullmatch(unit):
        raise CannotCreate(f"coscc.loop named something that is not a unit: {unit}")

    directory.mkdir(parents=True, exist_ok=False)
    text = str(brief or "").strip()
    if text:
        (directory / "idea.md").write_text(_idea(unit, text), encoding="utf-8")
    return {"unit": unit, "path": str(directory), "brief": bool(text)}


def host_unit_count(workspace: str | os.PathLike[str]) -> int:
    """How many directories in the host repository's own `.cos/` are named like units.

    Names only, no file opened, never cached. It lets the empty board say why it is empty.
    """
    directory = Path(key(workspace)) / COS_DIR
    try:
        return sum(1 for e in directory.iterdir() if e.is_dir() and _HOST_UNIT_RE.match(e.name))
    except OSError:
        return 0


# Looser than `UNIT_RE` on purpose: a directory whose slug the grammar refuses still counts.
_HOST_UNIT_RE = re.compile(r"\d{4}_")


def _idea(unit: str, brief: str) -> str:
    """The brief as an `idea.md` the loop can read, `Status: accepted` (nothing for an agent to accept)."""
    title = unit.split("_", 1)[-1].replace("-", " ")
    return (
        f"# Idea: {title}\n"
        f"Author: the originator. Status: accepted.\n\n"
        f"## In their own words\n\n{brief.strip()}\n"
    )
