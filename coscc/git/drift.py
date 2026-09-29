"""Which files `main` changed since a unit's plan was written.

Read before an `impl` step from the run log, `plan.md` and two local `git` commands; no
session. Pure helpers plus `compute`, the only part that calls `git`.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from coscc.git import gitops

log = logging.getLogger(__name__)

HEADING = "## Files that change"
TRUNK_REF = "refs/remotes/origin/main"

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def files_section(plan_text: str) -> str | None:
    """`## Files that change` up to the next `## ` heading or the end, `None` without one."""
    lines = plan_text.splitlines()
    for i, line in enumerate(lines):
        if line.rstrip() == HEADING:
            end = next(
                (j for j in range(i + 1, len(lines)) if lines[j].startswith("## ")),
                len(lines),
            )
            return "\n".join(lines[i:end])
    return None


def mentioned(section: str, paths: list[str]) -> list[str]:
    """The `paths` that appear in `section` whole, not as part of a longer path.

    Before a path: start of line, whitespace, `` ` ``, `(`, `|` or `*`; after it: end of line,
    whitespace, `` ` ``, `:`, `)`, `,`, `|` or `*`. The negated class also matches at line ends.
    """
    found = {
        d
        for d in paths
        if d and re.search(r"(?<![^\s`(|*])" + re.escape(d) + r"(?![^\s`:),|*])", section, re.M)
    }
    return sorted(found, key=lambda s: s.encode())


def plan_head(records: list[dict[str, Any]]) -> tuple[str, str]:
    """`(sha, "")` for the commit the last `done` run of `plan` ran on, or `("", reason)`.

    Uses the `head` of the `start` record, not `base.sha`.
    """
    seq = [
        r
        for r in records
        if str(r.get("stage") or "") == "plan" and r.get("kind") in ("start", "end")
    ]
    chosen = None
    for i, r in enumerate(seq[:-1]):
        after = seq[i + 1]
        if (
            r.get("kind") == "start"
            and after.get("kind") == "end"
            and after.get("outcome") == "done"
        ):
            chosen = r
    if chosen is None:
        return "", "the run log has no run of plan that ended done"
    head = str(chosen.get("head") or "")
    if not head:
        return "", "the run of plan that wrote it recorded no commit"
    if not _SHA_RE.fullmatch(head):
        return "", f"the run of plan that wrote it recorded {head!r}, not a full commit SHA"
    return head, ""


def describe(drift: dict[str, Any]) -> str:
    """The prompt's section body: `""` when nothing changed, one sentence when unchecked."""
    if not drift.get("checked"):
        return (
            "The app could not check whether main changed the files this plan names "
            f"since it was written: {drift.get('reason') or 'no reason given'}"
        )
    files = drift.get("files") or []
    if not files:
        return ""
    plan_sha, main_sha = drift["plan_sha"], drift["main_sha"]
    lines = [
        f"The plan was written on {plan_sha}. `origin/main` is now {main_sha}. Since then "
        "main has changed these files that the plan's `## Files that change` names:",
        "",
    ]
    lines += [f"- `{f}`: `git diff {plan_sha}..{main_sha} -- {f}`" for f in files]
    lines += [
        "",
        "Before you edit any of them:",
        "",
        "1. The line numbers the plan cites in these files may no longer point where they did.",
        "2. If what was merged contradicts what the plan sets out to do, stop before editing "
        "that file.",
        "3. Record the contradiction in `impl.md` under `## What is still open`, set "
        "`Status: draft`, and do not edit `plan.md`.",
    ]
    return "\n".join(lines)


async def compute(
    records: list[dict[str, Any]], plan_text: str | None, tree: str | Path | None
) -> dict[str, Any]:
    """`{plan_sha, main_sha, files, checked, reason}`. Never raises.

    A failure is `checked: False` with its reason and `files: None`, never an empty list,
    which would claim "nothing changed" about something nobody checked.
    """
    out: dict[str, Any] = {
        "plan_sha": None,
        "main_sha": None,
        "files": None,
        "checked": False,
        "reason": "",
    }
    try:
        plan_sha, why = plan_head(records)
        if not plan_sha:
            out["reason"] = why
            return out
        out["plan_sha"] = plan_sha
        section = files_section(plan_text or "")
        if section is None:
            out["reason"] = f"plan.md has no {HEADING} section"
            return out
        if tree is None:
            out["reason"] = "no worktree"
            return out
        path = Path(tree)
        # The ref as the step's own preparation left it; no fetch here.
        out["main_sha"] = await gitops.rev_parse(path, TRUNK_REF)
        changed = await gitops.diff_names(path, plan_sha, out["main_sha"])
        out["files"] = mentioned(section, changed)
        out["checked"] = True
        return out
    except Exception as e:
        # A failure here must never stop the step.
        log.exception("the plan drift could not be checked")
        out["files"] = None
        out["checked"] = False
        out["reason"] = str(e) or type(e).__name__
        return out
