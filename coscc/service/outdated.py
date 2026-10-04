"""Which accepted spec or plan a decision, or a change of `main` it cites, came after.

Read from the run log only: the `decision` and `decision-withdrawn` rows a feature writes beside
its own table, and each run's `start` and `end`; `main` through `drift.compute_stage`. No feature
is imported: with none writing those rows, no unit has a decision. What this finds is kept on
`Workspaces.outdated`, which `snapshot` hands the loop as each unit's `outdated`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from coscc.data import Busy
from coscc.git import drift
from coscc.service.common import Invalid
from coscc.service.workspaces import Workspaces

log = logging.getLogger(__name__)

# The stages a decision or `main` makes outdated, and their artifacts, earliest first.
STAGES = (("spec", "spec.md"), ("plan", "plan.md"))
KINDS = ("decision", "decision-withdrawn", "start", "end")
# A whole workspace is worked out again at most this often; one named unit every time.
REUSE_SECONDS = 30.0


class Decision(TypedDict):
    id: str
    authority: str


class Entry(TypedDict):
    """One outdated stage: the decisions no `done` run of it took, and `main`'s check."""

    decisions: list[Decision]
    main: drift.StageDrift


class Cause(TypedDict):
    kinds: list[str]
    decisions: list[str]
    from_sha: str | None
    main_sha: str | None
    paths: list[str]


class StartFields(TypedDict):
    decisions: list[str]
    cause: NotRequired[Cause]


def live_decisions(records: Iterable[Mapping[str, Any]]) -> list[Decision]:
    """Every `decision` row no `decision-withdrawn` row names, as `{id, authority}`, oldest first."""
    rows = list(records)
    gone = {r.get("id") for r in rows if r.get("kind") == "decision-withdrawn"}
    return [
        {"id": str(r["id"]), "authority": str(r.get("authority") or "")}
        for r in rows
        if r.get("kind") == "decision" and r.get("id") and r["id"] not in gone
    ]


def taken(records: Sequence[Mapping[str, Any]], stage: str) -> set[str]:
    """The decisions some run of `stage` that ended `done` was handed in its `start`."""
    return {str(c) for r in drift.done_starts(records, stage) for c in r.get("decisions") or []}


def cause_of(entry: Entry | None) -> Cause | None:
    """What an `outdated` entry of one stage says made it so, as the run log keeps it: `kinds`
    (`decision`, `main`), the decisions, the two SHAs and the paths. `None` for no entry."""
    if not entry:
        return None
    ids = [d["id"] for d in entry["decisions"]]
    main = entry["main"]
    paths = list(main["paths"] or []) if main["checked"] else []
    kinds = [*(["decision"] if ids else []), *(["main"] if paths else [])]
    if not kinds:
        return None
    return {
        "kinds": kinds,
        "decisions": ids,
        "from_sha": main["from_sha"] if paths else None,
        "main_sha": main["main_sha"] if paths else None,
        "paths": paths,
    }


def start_fields(
    records: Sequence[Mapping[str, Any]], stage: str, cause: Cause | None
) -> StartFields | None:
    """What a `start` of `spec` or `plan` adds: every live decision, which the run takes, and
    for a rerun the app asked for, its `cause`. `None` for any other stage."""
    if stage not in dict(STAGES):
        return None
    out: StartFields = {"decisions": [d["id"] for d in live_decisions(records)]}
    if cause:
        out["cause"] = cause
    return out


async def of_unit(
    records: Sequence[Mapping[str, Any]],
    artifacts: Mapping[str, Any],
    directory: Path,
    repo: str | None,
) -> dict[str, Entry]:
    """`{stage: {decisions, main}}` for each accepted spec or plan that has a live decision no
    `done` run of it took, or a path `main` changed that it cites. A check that could not be
    made (`checked: False`) makes nothing outdated."""
    out: dict[str, Entry] = {}
    live = live_decisions(records)
    for stage, file in STAGES:
        if (artifacts.get(file) or {}).get("status") != "accepted":
            continue
        had = taken(records, stage)
        new = [d for d in live if d["id"] not in had]
        try:
            text = (directory / file).read_text(encoding="utf-8")
        except OSError:
            text = None
        main = await drift.compute_stage(records, stage, text, repo)
        if new or (main["checked"] and main["paths"]):
            out[stage] = {"decisions": new, "main": main}
    return out


async def refresh(ws: Workspaces, cwd: str, names: Iterable[str] | None = None) -> None:
    """Work out `outdated` for `cwd`'s units, or only `names`, and keep it on `ws.outdated`. A
    whole workspace read less than `REUSE_SECONDS` ago is left as it is. Never raises: a run
    log that cannot be read leaves what was kept."""
    key = ws.key(cwd)
    if names is None and time.monotonic() - ws.outdated_at.get(key, -REUSE_SECONDS) < (
        REUSE_SECONDS
    ):
        return
    journal = ws.journal()
    if journal is None:
        return
    try:
        snap = ws.snapshot(cwd, None if names is None else list(names))
        rows = journal.records(key, kinds=KINDS)
    except Exception:
        log.exception("what is outdated in %s could not be read", key)
        return
    repo = cwd if (Path(cwd).expanduser() / ".git").exists() else None
    for at, entry in snap["units"].items():
        owner, _, unit = at.rpartition("/")
        if owner != snap["workspace"]:
            continue
        artifacts = entry.get("artifacts") or {}
        if (artifacts.get("plan.md") or {}).get("status") == "done":
            ws.outdated.pop(at, None)
            continue
        try:
            found = await of_unit(
                [r for r in rows if r.get("unit") == unit],
                artifacts,
                ws.unit_dir(cwd, unit),
                repo,
            )
        except (Busy, Invalid, OSError) as e:
            log.warning("what is outdated in %s could not be read: %s", unit, e)
            continue
        if found:
            ws.outdated[at] = found
        else:
            ws.outdated.pop(at, None)
    if names is None:
        ws.outdated_at[key] = time.monotonic()
