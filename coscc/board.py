"""The eight stages of a workspace's work units, read without a second parser.

`spec.md` C8 chose this shape: the rules that decide what a status means live in
`.claude/scripts/cos.mjs`, because that is the file the gate consults, and a second copy in
Python would drift from it silently. So this module runs that script and reads its JSON
rather than re-reading the Markdown.

**Which copy it runs is the security decision here.** A workspace is a repository cloned
from a URL somebody typed, so `<workspace>/.claude/scripts/cos.mjs` is a file that
repository controls. Executing it would hand a cloned repo everything this process has,
which is past every knob in `coscc/config.py`. This module therefore runs **the copy that
ships with the app**, pointed at the workspace's `.cos/` with `--root`. `coscc/harness.py`
is what makes that sentence true, and until 0012 it was not: the wheel shipped no copy at
all, and this module answered 400 on every read. The cost of running our own copy is real
and worth naming: a workspace that uses a different version of the harness is read with
this app's stage list, not its own.

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

# Which copy of the harness, and where it is, is `coscc/harness.py`'s question and is not
# asked again here. Until 0012 this module computed `parent.parent / ".claude"` for itself
# and `coscc/runner.py` computed the same thing separately -- one formula in two places,
# which is how a single packaging omission arrived as two unrelated-looking symptoms.
from coscc import harness
from coscc.harness import child_env as _child_env

# Measured 2026-09-21 on this machine: five runs over the eight units in this repository
# took 0.05s each, node v24.20.0. Ten seconds is therefore about two hundred times the
# observed cost. Like `store.LOCK_TIMEOUT` it exists to turn a hung child into an error,
# not to bound the work. **Unverifiable beyond this machine:** one machine, one day, eight
# units.
TIMEOUT = 10.0


class Unavailable(Exception):
    """The board cannot be read, carrying a reason a caller can show verbatim."""


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


async def _run(argv: list[str], timeout: float) -> tuple[int, str, str]:
    """One `cos.mjs` invocation: its exit code and both streams, decoded.

    Extracted when `gate` arrived, because the two callers want opposite things from a
    non-zero exit. `read` treats it as a failure -- it asked a question and got no answer.
    `gate` treats it as *the answer*: exit 1 is "blocked, and here are the reasons", which
    is the whole point of asking. Leaving the returncode to the caller is what lets both
    be true without a second copy of this boilerplate.
    """
    proc = await asyncio.create_subprocess_exec(
        "node",
        *argv,
        env=_child_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return (
        proc.returncode or 0,
        out.decode(errors="replace"),
        err.decode(errors="replace"),
    )


async def read(units_root: str | Path, timeout: float = TIMEOUT) -> dict[str, Any]:
    """Every unit under `units_root`, each with its eight stages.

    **`units_root` is not the workspace.** Until `0014` it was, and this module worked the
    path out for itself alongside two others doing the same (`coscc/units.py` docstring).
    It is now the product's own store for that workspace, because `0013` settled that
    nothing of coscc's goes into a repository a team shares — so the caller asks
    `coscc/units.py` and hands the answer in.

    Raises `Unavailable` only when the answer is unknown — node missing, the script gone,
    a child that failed or hung. A root with no `.cos/` is a *known* answer: no units.
    """
    path = Path(units_root)
    script = harness.script()
    if not script.exists():
        raise Unavailable(f"the harness script is missing: {script}")

    try:
        code, out_text, err_text = await _run(
            [str(script), "--root", str(path), "status", "--json"], timeout
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

    except asyncio.TimeoutError:
        raise Unavailable(f"reading the board timed out after {timeout:.0f}s") from None

    if code != 0:
        raise Unavailable((err_text or out_text).strip() or f"the harness script exited {code}")

    try:
        data = json.loads(out_text)
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
            # `0024`. The same answer as a stage name, read off the files alone. Enough for
            # the card's mode badge; the run button asks `next_step`, which can read git.
            # An older `cos.mjs` sends no `stage`, which reads as the empty string.
            "next_stage": str((u.get("next") or {}).get("stage") or ""),
            "blocked": bool((u.get("next") or {}).get("blocked")),
            "problems": u.get("problems") or [],
            # `pre-intent` or `started`, decided by `cos.mjs` `readUnit`. Copied through for
            # the same reason as `next`: the lane reads it rather than guessing it from
            # whether `problems` is empty. An older `cos.mjs` sends nothing, which reads as
            # the empty string, and no lane treats that specially.
            "phase": u.get("phase") or "",
            # `0016`. Which items under `## Open questions` a person has answered, and how
            # many are still open in the counted artifact. Both decided by `cos.mjs` and
            # copied, never recounted here (`0016` spec R7). An older `cos.mjs` sends
            # neither, which reads as no questions, the same way `phase` degrades.
            "questions": list(u.get("questions") or []),
            "open": int(u.get("open") or 0),
            "counted": u.get("counted") or "",
            # `0021`. The pull request `pr.md` names and the rounds `review.md` holds, each
            # with its text verbatim — both read by `cos.mjs` and copied, so the round a
            # comment carries is the round the gate counted. An older `cos.mjs` sends
            # neither, which reads as no pull request and no rounds.
            "pr": _pr_of(u),
            "rounds": _rounds_of(u),
            # `0028`. The findings the last review round confirmed need a person, each
            # `{id, reason, answered}`, as `cos.mjs` `readUnit` decided them. Copied, never
            # derived here: the Questions tab lists exactly these. An older `cos.mjs` sends
            # nothing, which reads as none.
            "person_findings": _person_findings_of(u),
            # `0028`. The ids `next` says a person is awaited on. Empty unless it said so.
            "waiting": [str(x) for x in ((u.get("next") or {}).get("waiting") or [])],
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


async def stages(timeout: float = TIMEOUT) -> list[str]:
    """The stage names `cos.mjs` defines, in its order, with no workspace needed.

    `0004_no-setting-says-which-model-runs-a-stage`: Settings lists a model per stage
    before anybody has added a workspace, and it must not keep a stage list of its own
    (`.claude/CLAUDE.md`: nothing may hold a second copy of the loop). So it asks the same
    script the board asks, pointed at an empty temporary root — `status --json` sends the
    stage list whether or not any unit exists. Raises `Unavailable` exactly as `read` does.
    """
    import tempfile

    with tempfile.TemporaryDirectory(prefix="coscc-stages-") as empty:
        data = await read(empty, timeout)
    return list(data["stages"])


# Chosen, not measured. Since `0015` the `review` gate asks GitHub for the pull request's
# checks, so this one call now waits on a network the board's own read never touches.
GATE_TIMEOUT = 30.0


async def gate(
    units_root: str | Path,
    unit: str,
    stage: str,
    repo: str | Path | None = None,
    timeout: float = GATE_TIMEOUT,
) -> tuple[bool, str]:
    """Ask `cos.mjs gate` whether one stage of one unit may proceed.

    `repo` is the workspace -- the git checkout the unit's code lives in -- and is passed as
    `--repo`. It is not `units_root`: since `0014` that is the product's store, which holds
    artifacts and has no git, so the `review` and `ship` gates (`0015`) would have no branch
    to read and no pull request to ask about. Without `repo` those two gates stay closed
    and say so; every other stage reads files only and does not care.

    Returns `(open, what it said)`. Exit 0 is open; exit 1 is blocked and carries the
    reasons; exit 2 is misuse, which is this app's bug and not the unit's, so it is
    reported with what the script printed rather than translated.

    **Nothing in this app asked this question until now.** `.claude/CLAUDE.md` invariant 2
    -- *"Ask `cos.mjs gate` before a stage and stop when it exits non-zero"* -- was written
    for a person at a terminal, and every stage's skill repeats it. But the six prose
    stages run with no tools at all, so four of them could never obey it, and
    `coscc/service.py` `run_step` went straight from reading the board to starting the
    session. The rule existed, the script that decides it existed, and the product walked
    past both.

    Measured 2026-09-23: the `ship` step of `0001_product-describes-a-state-it-is-not-in`
    wrote `Status: draft` and gave "the gate for this stage has not been asked" as a
    reason. Its gate was open. It had no way to find that out, so it assumed the worst
    about a question the app was already in a position to answer for it.
    """
    path = Path(units_root)
    script = harness.script()
    if not script.exists():
        raise Unavailable(f"the harness script is missing: {script}")

    try:
        argv = [str(script), "--root", str(path), "gate", unit, stage]
        if repo is not None:
            argv += ["--repo", str(Path(repo).expanduser().resolve())]
        code, out_text, err_text = await _run(argv, timeout)
    except (OSError, ValueError) as e:
        raise Unavailable(
            f"could not run node: {e} — PATH was {_child_env()['PATH']}"
        ) from e
    except asyncio.TimeoutError:
        raise Unavailable(f"asking the gate timed out after {timeout:.0f}s") from None

    said = (out_text + err_text).strip()
    return code == 0, said or f"the gate exited {code} and said nothing"


async def next_step(
    units_root: str | Path,
    unit: str,
    repo: str | Path | None = None,
    timeout: float = GATE_TIMEOUT,
) -> dict[str, Any]:
    """Ask `cos.mjs next` which one stage the run button may offer for `unit`.

    `0024`. Until then the page chose for itself -- "the first required stage with no
    artifact" -- which is a second copy of the loop `.claude/CLAUDE.md` forbids, and which
    offered `ship` after a review asked for changes. The answer is now `cos.mjs`'s, copied:
    `{"stage": <name or "">, "action": <why>, "blocked": <bool>}`. Nothing here reads
    `action` to decide anything.

    `repo` is passed as `--repo` exactly as `gate` passes it, so the two questions read the
    same checkout. It costs up to two `gh` calls, which is why only the open unit asks and
    `read` does not.
    """
    path = Path(units_root)
    script = harness.script()
    if not script.exists():
        raise Unavailable(f"the harness script is missing: {script}")

    try:
        argv = [str(script), "--root", str(path), "next", unit]
        if repo is not None:
            argv += ["--repo", str(Path(repo).expanduser().resolve())]
        code, out_text, err_text = await _run(argv, timeout)
    except (OSError, ValueError) as e:
        raise Unavailable(
            f"could not run node: {e} — PATH was {_child_env()['PATH']}"
        ) from e
    except asyncio.TimeoutError:
        raise Unavailable(f"asking what comes next timed out after {timeout:.0f}s") from None

    if code != 0:
        raise Unavailable((err_text or out_text).strip() or f"the harness script exited {code}")
    try:
        data = json.loads(out_text)
    except (json.JSONDecodeError, ValueError) as e:
        raise Unavailable(f"the harness script did not return JSON: {e}") from e
    return {
        "unit": str(data.get("unit") or unit),
        "stage": str(data.get("stage") or ""),
        "action": str(data.get("action") or ""),
        "blocked": bool(data.get("blocked")),
        # `0028`. Present only when a person is awaited; its absence reads as none.
        "waiting": [str(x) for x in data.get("waiting") or []],
    }


def _person_findings_of(unit: dict[str, Any]) -> list[dict[str, Any]]:
    """`[{id, reason, answered}]` as `cos.mjs` `readUnit` put them in `personFindings`."""
    out = []
    for p in unit.get("personFindings") or []:
        if not isinstance(p, dict) or not p.get("id"):
            continue
        out.append({
            "id": str(p["id"]),
            "reason": str(p.get("reason") or ""),
            "answered": bool(p.get("answered")),
        })
    return out


def _pr_of(unit: dict[str, Any]) -> dict[str, Any] | None:
    """`{url, number}` from `pr.md`'s `PR:` line as `cos.mjs` `parsePr` read it, or None."""
    pr = ((unit.get("artifacts") or {}).get("pr.md") or {}).get("pr")
    if not isinstance(pr, dict) or not pr.get("url"):
        return None
    return {"url": str(pr["url"]), "number": pr.get("number")}


def _rounds_of(unit: dict[str, Any]) -> list[dict[str, Any]]:
    """Each round of `review.md`: its number, verdict and text, as `cos.mjs` split them."""
    review = ((unit.get("artifacts") or {}).get("review.md") or {}).get("review") or {}
    return [
        {"n": r.get("n"), "verdict": r.get("verdict"), "text": r.get("text") or ""}
        for r in review.get("rounds") or []
        if isinstance(r, dict)
    ]


def _why_empty(path: Path) -> str:
    if not path.exists():
        return f"no such directory: {path}"
    if not (path / ".cos").exists():
        return "this workspace has no .cos/ — nothing here runs the loop yet"
    return "the .cos/ directory is there but holds no work units"
