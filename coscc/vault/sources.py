"""What a unit has written that could carry a value out: its transcripts, run log, artifacts,
commits and pull request. A source that cannot be read is left out, never an error; the commits
and the pull request are named to a caller that asks."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Iterable, Mapping

from coscc.store.db import Unusable
from coscc.git.gitops import child_env
from coscc.store.journal import Journal

# Chosen, not measured: a bound on one `git` or `gh` call and on one artifact.
CALL_TIMEOUT = 60
ARTIFACT_MAX = 5 * 1024 * 1024


def _lines(items: Iterable[Mapping[str, object]]) -> bytes:
    return "\n".join(json.dumps(i, ensure_ascii=False) for i in items).encode()


def _journal(journal: Journal | None, workspace: str, unit: str) -> list[tuple[str, bytes]]:
    if journal is None:
        return []
    try:
        records = journal.records(workspace, unit)
    except Unusable:
        return []
    out = [("run-log", _lines(records))]
    for run in dict.fromkeys(str(r["run"]) for r in records if r.get("run")):
        try:
            events, _ = journal.data.step_events_page(run, None, 10**9)
        except Unusable:
            continue
        if events:
            out.append((f"transcript:{run}", _lines(events)))
    return out


def _artifacts(directory: Path) -> list[tuple[str, bytes]]:
    out = []
    for path in sorted(directory.rglob("*")):
        try:
            if path.is_file() and path.stat().st_size <= ARTIFACT_MAX:
                out.append((f"artifact:{path.relative_to(directory)}", path.read_bytes()))
        except OSError:
            continue
    return out


def _call(argv: list[str], cwd: str) -> bytes | None:
    """The stdout of a call that succeeded, else `None`."""
    try:
        done = subprocess.run(
            argv, cwd=cwd, env=child_env(), capture_output=True, timeout=CALL_TIMEOUT
        )
    except OSError, subprocess.SubprocessError:
        return None
    return done.stdout if done.returncode == 0 else None


def _commits(tree: str, base: str) -> list[tuple[str, bytes]]:
    for ref in (base, f"origin/{base}"):
        found = _call(["git", "log", "-p", "--no-ext-diff", f"{ref}..HEAD"], tree)
        if found is not None:
            return [("commits", found)]
    return []


def _pull_request(tree: str) -> list[tuple[str, bytes]]:
    found = _call(["gh", "pr", "view", "--json", "body,comments"], tree)
    return [] if found is None else [("pull-request", found)]


def unit_sources(
    journal: Journal | None,
    workspace: str,
    unit: str,
    directory: Path,
    tree: str,
    base: str = "main",
    pull_request: bool = False,
    unread: list[str] | None = None,
) -> list[tuple[str, bytes]]:
    """`(where, bytes)` for the transcripts of the unit's runs, its run-log lines, the files under
    `directory`, `git log -p <base>..HEAD` in `tree` and, with `pull_request`, the pull request's
    body and comments read through `gh`. Blocks for as long as `git` and `gh` take.

    `commits` and `pull-request`, when `git` or `gh` failed or timed out, are appended to
    `unread`, so a caller that must not pass an unscanned unit can say so."""
    out = _journal(journal, workspace, unit) + _artifacts(directory)
    wanted = [("commits", _commits(tree, base))]
    if pull_request:
        wanted.append(("pull-request", _pull_request(tree)))
    for where, found in wanted:
        out += found
        if not found and unread is not None:
            unread.append(where)
    return out
