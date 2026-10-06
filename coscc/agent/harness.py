"""Where the rules this app runs live (the agents' pack and its processes), the
environment the loop child gets, and what a runnable wheel must carry. The loop that decides on
them is `coscc.loop`, code of the package itself; the agents and their skills are package data
(`coscc/agent/pack.py`). Nothing here looks inside a workspace.
"""

from __future__ import annotations

import json
import os
import re
import zipfile
from pathlib import Path

import coscc
from coscc.agent import pack

# The package root, `coscc/`.
_HERE = Path(coscc.__file__).resolve().parent


def child_env() -> dict[str, str]:
    """The environment the loop child (`python -m coscc.loop`, see `coscc.loop.run`) runs in.

    Built up, never filtered down (see `gitops.child_env`): it needs no secret, so it is given
    none.
    """
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LC_ALL": "C",
        "NO_COLOR": "1",
    }
    # Passed only when set, so an unset variable keeps meaning "the loop's own default".
    # The `review` and `ship` gates run `git` and `gh`; `gh` finds its login through `HOME`, so a
    # machine logging in with `GH_TOKEN` alone sees those gates closed. No secret is passed down.
    rounds = os.environ.get("COS_REVIEW_ROUNDS")
    if rounds:
        env["COS_REVIEW_ROUNDS"] = rounds
    return env


def _outside(entry: str, roots: list[Path]) -> bool:
    try:
        p = Path(entry).resolve()
    except OSError, ValueError:
        return False
    return not any(p == r or r in p.parents for r in roots)


def clean_path(workspace: str | os.PathLike[str] | None) -> str:
    """`PATH` without any entry under the workspace or the installed package, whose `.venv/bin`
    would run the workspace's code instead of the tree's.
    """
    roots = [_HERE]
    if workspace:
        roots.append(Path(workspace).expanduser().resolve())
    parts = [e for e in os.environ.get("PATH", "/usr/bin:/bin").split(os.pathsep) if e]
    return os.pathsep.join(e for e in parts if _outside(e, roots))


# --- what a runnable wheel must contain --------------------------------------

# The build stamp `scripts/build_wheel.sh` writes; an installed copy learns its commit only here.
BUILD_STAMP = "_build.json"
_FULL_SHA = re.compile(r"[0-9a-f]{40}")


def _posix(*parts: object) -> str:
    return Path("coscc", *[str(p) for p in parts]).as_posix()


def wheel_complaints(wheel: str | Path) -> list[str]:
    """Everything wrong with `wheel`, as sentences. Empty means it would run.

    Each entry names a wheel that installs cleanly and then fails differently: no studio
    (no page), no agent rows or skills, no `process.json`, no build stamp with a 40-hex commit.
    The stamp is checked though committed: whether a file arrives by `git` or by a copy step is
    invisible to the installed copy. Rows and skills are counted, not listed by name.
    """
    path = Path(wheel)
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile) as e:
        return [f"{path} could not be read as a wheel: {e}"]

    index = _posix("_studio", "index.html")
    pack_prefix = _posix(pack.BUILTIN.relative_to(_HERE)) + "/"

    out = []
    if index not in names:
        out.append(f"no {index} — it would install and serve no page")
    for part, end, what in (
        ("agents/", ".md", "no agent would run"),
        ("skills/", "/" + pack.SKILL_FILE, "every step would refuse to run"),
    ):
        if not any(n.startswith(pack_prefix + part) and n.endswith(end) for n in names):
            out.append(f"no {pack_prefix}{part}*{end} — {what}")
    manifest = pack_prefix + pack.MANIFEST.as_posix()
    if manifest not in names:
        out.append(f"no {manifest} — no agent would run")
    processes = pack_prefix + pack.PROCESS_FILE
    if processes not in names:
        out.append(f"no {processes} — no unit could take a step")
    # Without it the board shows `commit unknown`.
    stamp = _posix(BUILD_STAMP)
    if stamp not in names:
        out.append(f"no {stamp} — the board could not say which commit it runs")
    else:
        with zipfile.ZipFile(path) as archive:
            try:
                commit = json.loads(archive.read(stamp)).get("commit")
            except ValueError, AttributeError:
                commit = None
        if not isinstance(commit, str) or not _FULL_SHA.fullmatch(commit):
            out.append(
                f"{stamp} carries no 40-hex commit — the board could not say which commit it runs"
            )

    return out
