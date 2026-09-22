"""The eight stages of a workspace's work units, read without a second parser.

`spec.md` C8 chose this shape: the rules that decide what a status means live in
`.claude/scripts/cos.mjs`, because that is the file the gate consults, and a second copy in
Python would drift from it silently. So this module runs that script and reads its JSON
rather than re-reading the Markdown.

**Which copy it runs is the security decision here.** A workspace is a repository cloned
from a URL somebody typed, so `<workspace>/.claude/scripts/cos.mjs` is a file that
repository controls. Executing it would hand a cloned repo everything this process has,
which is past every knob in `coscc/config.py`. This module therefore runs **the copy
that ships with the app** -- `coscc/harness.py` is what makes that sentence true, and
before 0012 it was not: no copy shipped -- pointed at the workspace's `.cos/` with
`--root`. The cost is
real and worth naming: a workspace that uses a different version of the harness is read
with this app's stage list, not its own.

The board reports; it never writes. What a step costs and which session ran it belong to
the journal, and what a stage *says* belongs to the artifact on disk. This module only
answers "where does each unit stand".
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from coscc import harness

# Which copy, and where it is, is `coscc/harness.py`'s question -- not asked again here.
# Until 0012 this line computed `parent.parent` for itself and `coscc/runner.py:39`
# computed the same thing separately, which is how one packaging omission arrived as two
# unrelated-looking symptoms.

# Measured 2026-09-21 on this machine: five runs over the eight units in this repository
# took 0.05s each, node v24.20.0. Ten seconds is therefore about two hundred times the
# observed cost. Like `store.LOCK_TIMEOUT` it exists to turn a hung child into an error,
# not to bound the work. **Unverifiable beyond this machine:** one machine, one day, eight
# units.
TIMEOUT = 10.0


class Unavailable(Exception):
    """The board cannot be read, carrying a reason a caller can show verbatim."""


def _child_env() -> dict[str, str]:
    """Built up, never filtered down — the reasoning is in `gitops.child_env`.

    `cos.mjs` reads files and prints JSON. It needs no secret, so it is given none: a new
    variable added to this process is excluded here by default rather than by memory.
    """
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LC_ALL": "C",
        "NO_COLOR": "1",
    }


def _stage_rows(stages: list[dict[str, Any]], artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per stage, in order, whether or not the artifact exists.

    `spec.md` R1 asks for all eight on every unit, and `spec.md` open question 5 settled
    what an absent artifact means: `not started` is read off the absence, never stored.
    """
    rows = []
    for stage in stages:
        entry = artifacts.get(stage["file"]) or {}
        rows.append(
            {
                "stage": stage["name"],
                "file": stage["file"],
                "status": entry.get("status") or "not started",
                "optional": bool(stage.get("optional")),
                "skip_reason": entry.get("skipReason"),
            }
        )
    return rows


async def read(workspace: str | Path, timeout: float = TIMEOUT) -> dict[str, Any]:
    """Every unit in `workspace`, each with its eight stages.

    Raises `Unavailable` only when the answer is unknown — node missing, the script gone,
    a child that failed or hung. A workspace with no `.cos/` is a *known* answer: no units.
    """
    path = Path(workspace)
    script = harness.script()
    if not script.exists():
        raise Unavailable(f"the harness script is missing: {script}")

    argv = ["node", str(script), "--root", str(path), "status", "--json"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            env=_child_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
    except (OSError, ValueError) as e:
        # No node on PATH is the ordinary case here, and it must name itself -- *with the
        # PATH it looked on*. Measured 2026-09-22 under the systemd user service this app
        # installs as: `node` was on the machine, at `~/.nvm/versions/node/v24.20.0/bin`,
        # and the service's PATH was the systemd user default, which contains no nvm. The
        # message without this suffix said only "could not run node" and sent a reader
        # looking for a missing program that was not missing. `docs/install.md` carries the
        # fix; this is what points at it.
        raise Unavailable(
            f"could not run node: {e} — PATH was {_child_env()['PATH']}"
        ) from e

    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise Unavailable(f"reading the board timed out after {timeout:.0f}s")

    if proc.returncode != 0:
        detail = (err or out or b"").decode(errors="replace").strip()
        raise Unavailable(detail or f"the harness script exited {proc.returncode}")

    try:
        data = json.loads(out.decode(errors="replace"))
    except (json.JSONDecodeError, ValueError) as e:
        raise Unavailable(f"the harness script did not return JSON: {e}") from e

    stages = data.get("stages") or []
    units = [
        {
            "name": u.get("name", ""),
            "number": u.get("number"),
            "slug": u.get("slug"),
            "stages": _stage_rows(stages, u.get("artifacts") or {}),
            # Carried through rather than recomputed. Two answers to "what next" is the
            # drift this module exists to avoid.
            "next": (u.get("next") or {}).get("action", ""),
            "blocked": bool((u.get("next") or {}).get("blocked")),
            "problems": u.get("problems") or [],
        }
        for u in data.get("units") or []
    ]

    return {
        "workspace": str(path),
        "stages": [s["name"] for s in stages],
        "units": units,
        "count": len(units),
        # A workspace can be perfectly healthy and hold no units at all. Saying so is not
        # the same as failing to read it, and the page has to be able to tell them apart.
        "empty_because": None if units else _why_empty(path),
    }


def _why_empty(path: Path) -> str:
    if not path.exists():
        return f"no such directory: {path}"
    if not (path / ".cos").exists():
        return "this workspace has no .cos/ — nothing here runs the loop yet"
    return "the .cos/ directory is there but holds no work units"
