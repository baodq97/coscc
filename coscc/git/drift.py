"""Which files `main` changed since a unit's plan, or its spec, was written.

Read before an `impl` step from the run log, `plan.md` and two local `git` commands; no
session. For `spec` and `plan` themselves, `compute_stage` with the paths the artifact cites.
Pure helpers plus `compute` and `compute_stage`, the only parts that call `git`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TypedDict

from coscc.git import gitops

log = logging.getLogger(__name__)

HEADING = "## Files that change"
TRUNK_REF = "refs/remotes/origin/main"

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class StageDrift(TypedDict):
    """`compute_stage`'s answer: `paths` is `None` whenever `checked` is false."""

    stage: str
    from_sha: str | None
    main_sha: str | None
    paths: list[str] | None
    checked: bool
    reason: str


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


def done_starts(records: Sequence[Mapping[str, Any]], stage: str) -> list[Mapping[str, Any]]:
    """Every `start` of `stage` whose next `start` or `end` of that stage is an `end` that says
    `done`, oldest first."""
    seq = [
        r
        for r in records
        if str(r.get("stage") or "") == stage and r.get("kind") in ("start", "end")
    ]
    return [
        r
        for i, r in enumerate(seq[:-1])
        if r.get("kind") == "start"
        and seq[i + 1].get("kind") == "end"
        and seq[i + 1].get("outcome") == "done"
    ]


def stage_head(records: Sequence[Mapping[str, Any]], stage: str) -> tuple[str, str]:
    """`(sha, "")` for the commit the last `done` run of `stage` ran on, or `("", reason)`.

    Uses the `head` of the `start` record, not `base.sha`.
    """
    done = done_starts(records, stage)
    if not done:
        return "", f"the run log has no run of {stage} that ended done"
    head = str(done[-1].get("head") or "")
    if not head:
        return "", f"the run of {stage} that wrote it recorded no commit"
    if not _SHA_RE.fullmatch(head):
        return "", f"the run of {stage} that wrote it recorded {head!r}, not a full commit SHA"
    return head, ""


def plan_head(records: list[dict[str, Any]]) -> tuple[str, str]:
    """`stage_head` of `plan`."""
    return stage_head(records, "plan")


def cited(text: str, paths: list[str]) -> list[str]:
    """The `paths` the part of `text` above `## Answers` names: whole, as `mentioned` finds them,
    under a directory it names ending in `/`, or as a dotted module (`coscc.loop.rules` for
    `coscc/loop/rules.py`, `coscc.loop` for `coscc/loop/__init__.py`)."""
    lines = text.splitlines()
    at = next((i for i, line in enumerate(lines) if line.rstrip() == "## Answers"), len(lines))
    above = "\n".join(lines[:at])
    dirs = set(re.findall(r"(?<![^\s`(|*])([\w.-][\w./-]*/)(?![^\s`:),|*])", above, re.M))
    found = set(mentioned(above, paths))
    for p in paths:
        if any(p.startswith(d) for d in dirs):
            found.add(p)
        elif p.endswith(".py"):
            module = p.removesuffix(".py").removesuffix("/__init__").replace("/", ".")
            if re.search(r"(?<![\w.])" + re.escape(module) + r"(?!\w|\.\w)", above):
                found.add(p)
    return sorted(found, key=lambda s: s.encode())


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


async def compute_stage(
    records: Sequence[Mapping[str, Any]], stage: str, text: str | None, repo: str | Path | None
) -> StageDrift:
    """`{stage, from_sha, main_sha, paths, checked, reason}`: the paths `origin/main` changed
    that the artifact of `stage` cites (`cited`), since the last `done` run of it. Never raises.

    That run's own `cause.main_sha`, when the app handed it one, counts as where it read main
    up to; else its `head`. The diff starts at the merge base with `origin/main`, so a branch's
    own commits are not main's. As `compute`, a failure is `checked: False` and `paths: None`.
    """
    out: StageDrift = {
        "stage": stage,
        "from_sha": None,
        "main_sha": None,
        "paths": None,
        "checked": False,
        "reason": "",
    }
    try:
        head, why = stage_head(records, stage)
        if not head:
            out["reason"] = why
            return out
        told = str((done_starts(records, stage)[-1].get("cause") or {}).get("main_sha") or "")
        since = told if _SHA_RE.fullmatch(told) else head
        if repo is None:
            out["reason"] = "no repository"
            return out
        path = Path(repo)
        main_sha = await gitops.rev_parse(path, TRUNK_REF)
        base = await gitops.merge_base_of(path, since, main_sha)
        out["from_sha"], out["main_sha"] = base, main_sha
        changed = [] if base == main_sha else await gitops.diff_names(path, base, main_sha)
        out["paths"] = cited(text or "", changed)
        out["checked"] = True
        return out
    except Exception as e:
        # A check that could not be made makes nothing outdated.
        log.exception("the %s drift could not be checked", stage)
        out["paths"] = None
        out["checked"] = False
        out["reason"] = str(e) or type(e).__name__
        return out
