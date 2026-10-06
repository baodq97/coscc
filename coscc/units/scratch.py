"""Where a unit's two scratch directories are, and the one place that makes and removes them.

A unit writes what it will commit in its worktree; everything else goes to one of two
directories the app makes before a step's session opens and removes once the unit ends:

- the **ram** one, `<OS temp>/coscc-scratch-<uid>/<slot>/<unit>/`, for small throwaway files;
  `RAM_CAP` bounds it, checked by `policy.critical` before each write tool's write;
- the **disk** one, `<data root>/scratch/<slot>/<unit>/`, beside `worktrees/`, for large files
  and what a later stage reads back. A session's `TMPDIR` points here.

Every step of a unit shares both. Nothing about them is kept in `cos.db`: the directories on
disk are the record. `remove` and `sweep` never raise, and delete only under the two roots,
never through a symlink, and only a directory named `NNNN_slug`.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
import tempfile
from collections.abc import Collection, Mapping
from pathlib import Path

from coscc import units
from coscc.store.db import Data

log = logging.getLogger(__name__)

SCRATCH_DIR = "scratch"

# Bytes the files of one unit's ram directory may add up to before a write there is refused.
# Chosen, not measured: small enough that a few units fit in a laptop's RAM.
RAM_CAP = 64 * 2**20

_MODE = 0o700


class Unsafe(RuntimeError):
    """A scratch directory this app will not write into, carrying why."""


def ram_root() -> Path:
    """`coscc-scratch-<uid>` in the OS temp directory, shared with every user of the machine."""
    return Path(tempfile.gettempdir()).resolve() / f"coscc-scratch-{os.getuid()}"


def disk_root(data_dir: str | os.PathLike[str] | None = None) -> Path:
    return Data(data_dir).root / SCRATCH_DIR


def places(
    workspace: str | os.PathLike[str], unit: str, data_dir: str | os.PathLike[str] | None = None
) -> tuple[Path, Path]:
    """`(ram, disk)` for the unit. Pure: nothing is made or checked but the unit's name."""
    if not units.UNIT_RE.fullmatch(unit or ""):
        raise units.BadUnit(f"not a work unit name: {unit!r}")
    slot = units.slot(workspace)
    return ram_root() / slot / unit, disk_root(data_dir) / slot / unit


def _owned(where: Path) -> None:
    """Refuse `where` unless it is a real directory of this uid with mode `0700`."""
    st = os.lstat(where)
    if stat.S_ISLNK(st.st_mode):
        raise Unsafe(f"{where} is a symlink; the app does not write through it")
    if not stat.S_ISDIR(st.st_mode):
        raise Unsafe(f"{where} is not a directory")
    if st.st_uid != os.getuid():
        raise Unsafe(f"{where} belongs to uid {st.st_uid}, not this app's uid {os.getuid()}")
    if stat.S_IMODE(st.st_mode) != _MODE:
        raise Unsafe(f"{where} has mode {stat.S_IMODE(st.st_mode):04o}, not 0700")


def _made(where: Path) -> None:
    try:
        os.mkdir(where, _MODE)
    except FileExistsError:
        pass
    else:
        # The umask can only take bits away; this puts back none and drops any group bit.
        os.chmod(where, _MODE)
    _owned(where)


def ensure(
    workspace: str | os.PathLike[str], unit: str, data_dir: str | os.PathLike[str] | None = None
) -> tuple[Path, Path]:
    """`places(...)`, both made `0700` if missing. Raises `Unsafe` saying why a root, or a
    directory under it, is a symlink, another uid's, or not `0700`; nothing is written there."""
    ram, disk = places(workspace, unit, data_dir)
    try:
        disk_root(data_dir).parent.mkdir(parents=True, exist_ok=True)
        for where in (ram, disk):
            for level in (where.parent.parent, where.parent, where):
                _made(level)
    except OSError as e:
        raise Unsafe(f"could not make the unit's scratch directory: {e}") from e
    return ram, disk


def _remove(root: Path, slot: str, unit: str) -> None:
    """Delete `root/slot/unit` when every part of it is a real directory. Never raises."""
    if not units.UNIT_RE.fullmatch(unit or "") or slot in ("", ".", "..") or "/" in slot:
        log.warning("scratch: refused to remove %r under %s", f"{slot}/{unit}", root)
        return
    where = root / slot / unit
    try:
        for level in (root, root / slot, where):
            if not stat.S_ISDIR(os.lstat(level).st_mode):
                log.warning("scratch: %s is not a real directory, left alone", level)
                return
        shutil.rmtree(where)
    except FileNotFoundError:
        return
    except OSError as e:
        log.warning("scratch: could not remove %s: %s", where, e)


def remove(
    workspace: str | os.PathLike[str], unit: str, data_dir: str | os.PathLike[str] | None = None
) -> None:
    """Delete both of the unit's scratch directories. Never raises; a failure is logged."""
    try:
        slot = units.slot(workspace)
        roots = (ram_root(), disk_root(data_dir))
    except (OSError, ValueError) as e:
        log.warning("scratch: could not find %s's scratch: %s", unit, e)
        return
    for root in roots:
        _remove(root, slot, unit)


def sweep(
    live: Mapping[str, Collection[str]], data_dir: str | os.PathLike[str] | None = None
) -> None:
    """Delete every unit scratch directory whose slot is not a key of `live`, or whose unit is
    not in `live[slot]`. Keys are `units.slot(workspace)`. A slot left empty is removed too.
    Never raises; a failure is logged.
    """
    try:
        roots = (ram_root(), disk_root(data_dir))
    except OSError as e:
        log.warning("scratch: could not find the scratch roots: %s", e)
        return
    for root in roots:
        try:
            _owned(root)
        except FileNotFoundError:
            continue
        except (OSError, Unsafe) as e:
            log.warning("scratch: not sweeping %s: %s", root, e)
            continue
        try:
            slots = [e.name for e in os.scandir(root) if e.is_dir(follow_symlinks=False)]
        except OSError as e:
            log.warning("scratch: could not list %s: %s", root, e)
            continue
        for slot in slots:
            keep = live.get(slot, ())
            try:
                found = [e.name for e in os.scandir(root / slot) if e.is_dir(follow_symlinks=False)]
            except OSError as e:
                log.warning("scratch: could not list %s: %s", root / slot, e)
                continue
            for unit in found:
                if units.UNIT_RE.fullmatch(unit) and unit not in keep:
                    _remove(root, slot, unit)
            if slot not in live:
                try:
                    os.rmdir(root / slot)
                except OSError:
                    pass
