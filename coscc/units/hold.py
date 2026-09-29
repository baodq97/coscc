"""A person pauses, drops or resumes a unit from the board.

`cos.mjs` decides what a hold is and which moves are allowed (`parseHold`, `HOLD_MOVES`); this
module only reads the board's `hold` and `hold_moves`. The pure functions `refusal` and
`record` shape what `Service.hold` writes; the two side effects of a drop, `close_pr` and
`remove_tree`, each return one `{effect, result, detail}` and never raise, so one failing
does not stop the other.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from coscc import units
from coscc.github import prcomment
from coscc.git import gitops, worktrees
from coscc.git.gitops import GitError
from coscc.units import BadUnit

# The block heading for each value of `to`; which move is allowed is read off `hold_moves`.
HEADS = {"paused": "Paused", "dropped": "Dropped", "active": "Resumed"}

DROP_WARNING = (
    "Dropping closes this unit's open pull request with this machine's gh login and "
    "removes its worktree; the remote branch is kept."
)


def _line_problem(what: str, value: str) -> str:
    """One line, not empty, not read as a heading."""
    if not value.strip():
        return f"the {what} is empty"
    if "\n" in value or "\r" in value:
        return f"the {what} must be one line"
    if value.lstrip().startswith("#"):
        return f"the {what} may not start with #"
    return ""


def refusal(found: dict[str, Any] | None, to: str, reason: str, by: str, busy: str) -> str:
    """The first reason this move is refused, or `""`."""
    if found is None:
        return "no such work unit in this workspace"
    moves = list(found.get("hold_moves") or [])
    now = (found.get("hold") or {}).get("state") or "active"
    if to not in moves:
        if not moves:
            # The code decides which sentence; the words shown are `next`'s own.
            code = str(found.get("why") or "")
            why = str(found.get("next") or "") if code in ("finished", "rejected") else "has no intent.md to record it in"
            return f"{found.get('name', 'this unit')} {why}; it cannot be paused or dropped"
        return f"{found.get('name', 'this unit')} is {now}; from there it can go to {', '.join(moves)}, not {to or 'nothing'}"
    for what, value in (("reason", reason), ("name", by)):
        said = _line_problem(what, value)
        if said:
            return said
    if busy:
        # `busy` names the running step, or what to wait for; a hold never stops either itself.
        return f"{busy}; a hold does not stop anything itself"
    return ""


def record(
    *, workspace: str, unit: str, from_: str, to: str, reason: str, by: str, effects: list[dict[str, str]]
) -> dict[str, Any]:
    """The one run-log row every move leaves, side effects' results included."""
    return {
        "kind": "hold",
        "workspace": workspace,
        "unit": unit,
        "stage": "",
        "from": from_,
        "to": to,
        "reason": reason,
        "by": by,
        "effects": effects,
    }


def _effect(effect: str, result: str, detail: str) -> dict[str, str]:
    return {"effect": effect, "result": result, "detail": detail}


async def close_pr(root: str, branch: str, gh: prcomment.Run | None = None) -> dict[str, str]:
    """Close the open pull request whose head is `branch`, and no other.

    Fixed argv: no `--delete-branch`, no flag from a caller.
    """
    gh = gh or prcomment._gh
    if not branch:
        return _effect("close-pr", "skipped", "the unit has no branch")
    listed = await prcomment._call(
        gh,
        ["pr", "list", "--state", "open", "--json", "number,headRefOid,headRefName,mergeable", "--limit", "200"],
        root,
        None,
    )
    if isinstance(listed, str):
        return _effect("close-pr", "failed", listed)
    code, out, err = listed
    if code != 0:
        return _effect("close-pr", "failed", prcomment._said(code, out, err))
    try:
        rows = json.loads(out or "[]")
    except ValueError as e:
        return _effect("close-pr", "failed", f"gh pr list did not return JSON: {e}")
    mine = [r for r in rows if isinstance(r, dict) and r.get("headRefName") == branch]
    if not mine:
        return _effect("close-pr", "skipped", f"no open pull request on {branch}")
    closed: list[str] = []
    for row in mine:
        number = str(int(row.get("number") or 0))
        said = await prcomment._call(gh, ["pr", "close", number], root, None)
        if not isinstance(said, str):
            code, out, err = said
            said = prcomment._said(code, out, err) if code != 0 else ""
        if said:
            # No retry: the person cleaning up by hand needs to know which were already closed.
            already = f"closed {', '.join(closed)}; " if closed else ""
            return _effect("close-pr", "failed", f"{already}#{number}: {said}")
        closed.append(f"#{number}")
    return _effect("close-pr", "done", f"closed {', '.join(closed)}")


async def remove_tree(
    workspace: str | os.PathLike[str], unit: str, data_dir: str | os.PathLike[str] | None = None
) -> dict[str, str]:
    """Remove the unit's worktree, never forced; the local branch stays.

    A tree with uncommitted changes is left where it is and reported `failed`.
    """
    try:
        found = await worktrees.find(workspace, unit, data_dir)
        if found is None:
            return _effect("remove-worktree", "skipped", "the unit has no worktree")
        tree = Path(found["path"])
        if not await gitops.is_clean(tree):
            return _effect("remove-worktree", "failed", "worktree has uncommitted changes")
        await gitops.worktree_remove(Path(units.key(workspace)), tree)
        try:
            worktrees.prepare_record(tree).unlink()
        except OSError:
            pass
        return _effect("remove-worktree", "done", f"removed {tree}")
    except (GitError, BadUnit, OSError, ValueError) as e:
        return _effect("remove-worktree", "failed", str(e))
