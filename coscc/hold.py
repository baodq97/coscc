"""`0045`. A person pauses, drops or resumes a unit from the board.

What a hold is, and which moves are allowed from each, is `cos.mjs`'s decision
(`parseHold`, `HOLD_MOVES`): the board carries `hold` and `hold_moves` and this module only
reads them. Nothing here keeps a second copy of the table (`.claude/CLAUDE.md`: "nothing may
hold a second copy of it").

Two kinds of function live here. The pure ones — `refusal`, `block`, `record` — shape what
`Service.hold` writes. The two side effects of a drop (spec R12) — `close_pr` and
`remove_tree` — each return one `{effect, result, detail}` and never raise: a failure in one
must not undo the block already written, nor stop the other.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from coscc import gitops, prcomment, units, worktrees
from coscc.gitops import GitError
from coscc.units import BadUnit

# The value `to` takes for each block heading, the other way round from `cos.mjs` `HOLD_TO`.
# A spelling, not a rule: which move is allowed is still read off `hold_moves`.
HEADS = {"paused": "Paused", "dropped": "Dropped", "active": "Resumed"}

DROP_WARNING = (
    "Dropping closes this unit's open pull request with this machine's gh login and "
    "removes its worktree. The remote branch is kept."
)


def _line_problem(what: str, value: str) -> str:
    """R7: one line, not empty, not read as a heading."""
    if not value.strip():
        return f"the {what} is empty"
    if "\n" in value or "\r" in value:
        return f"the {what} must be one line"
    if value.lstrip().startswith("#"):
        return f"the {what} may not start with #"
    return ""


def refusal(found: dict[str, Any] | None, to: str, reason: str, by: str, active: bool) -> str:
    """The first reason this move is refused, or `""`. Spec R5, R6, R7, R13, in that order."""
    if found is None:
        return "no such work unit in this workspace"
    moves = list(found.get("hold_moves") or [])
    now = (found.get("hold") or {}).get("state") or "active"
    if to not in moves:
        if not moves:
            nxt = str(found.get("next") or "")
            why = nxt if nxt == "finished" or nxt.startswith("closed") else "has no intent.md to record it in"
            return f"{found.get('name', 'this unit')} {why}; it cannot be paused or dropped"
        return f"{found.get('name', 'this unit')} is {now}; from there it can go to {', '.join(moves)}, not {to or 'nothing'}"
    for what, value in (("reason", reason), ("name", by)):
        said = _line_problem(what, value)
        if said:
            return said
    if active:
        return f"a step or an integration is running on {found.get('name')} — stop it first (0034); nothing here stops it"
    return ""


def block(to: str, by: str, today: str, reason: str) -> str:
    """R8: the block appended under `## Answers`, blank line first."""
    return f"\n### {HEADS[to]}\nDecided by: {by}. Date: {today}. Via: product.\n\n{reason}\n"


def record(
    *, workspace: str, unit: str, from_: str, to: str, reason: str, by: str, effects: list[dict[str, str]]
) -> dict[str, Any]:
    """R10: the one run-log row every move leaves, side effects' results included."""
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
    """R12(a). Close the open pull request whose head is `branch`, and no other.

    The list is the one `integrate.open_prs` asks for, filtered here on `headRefName`. The
    close is `gh pr close <number>` with a fixed argv: no `--delete-branch`, no flag from a
    caller. `prcomment.TIMEOUT` (30s, chosen) bounds each call.
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
        if isinstance(said, str):
            return _effect("close-pr", "failed", said)
        code, out, err = said
        if code != 0:
            return _effect("close-pr", "failed", prcomment._said(code, out, err))
        closed.append(f"#{number}")
    return _effect("close-pr", "done", f"closed {', '.join(closed)}")


async def remove_tree(
    workspace: str | os.PathLike[str], unit: str, data_dir: str | os.PathLike[str] | None = None
) -> dict[str, str]:
    """R12(b). Remove the unit's worktree, never forced; the local branch stays.

    A tree with uncommitted changes is left where it is and reported `failed` (spec C4):
    keeping somebody's work beats removing it on the word of a route with no login.
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
