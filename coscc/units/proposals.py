"""Work an agent proposes for the Backlog: the `proposals` table, the rules one proposal keeps,
and the owner's press that accepts it as a unit or dismisses it with a reason.

An agent whose output is `proposal` (`coscc/agent/pack.py`) hands back
`{proposals: [{type, slug, title, problem, sources}]}`; the engine (`coscc/runner/triggers.py`)
keeps those that pass `problems_of`, at most `PER_RUN` a run, as `pending`. Only a person's press
moves one on (`decide`, `by: owner`): no agent holds a tool that does. Nothing here touches the
shortlist, and the autopilot never reads this table.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Literal, TypedDict, get_args

from coscc.store.db import Data, now
from coscc.units import Invalid
from coscc.units.contracts import BranchType

# The loop's slug grammar (`coscc/loop/__init__.py`'s `SLUG_RE`, `SLUG_MAX`), which this layer may
# not import; `test_proposals` pins the copies.
TYPES: tuple[str, ...] = get_args(BranchType)
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", re.ASCII)
SLUG_MAX = 60
TITLE_MAX = 120
PROBLEM_MIN, PROBLEM_MAX = 200, 1_000
REASON_MAX = 500
PER_RUN = 8
# How much of the proposals already made a run is handed (`lists_of`).
LISTS_MAX = 2_000
OWNER = "owner"

State = Literal["pending", "accepted", "dismissed"]
STATES: tuple[State, ...] = get_args(State)


class Source(TypedDict):
    """What a proposal rests on: an intervention (`id`, `kind`, `unit`, `at`), or a citation."""

    id: str
    kind: str
    unit: str
    at: str


class Proposal(TypedDict):
    id: int
    agent: str
    # The unit it is about, `""` for none; `made` the unit accepting it made.
    unit: str
    run: str
    type: str
    slug: str
    title: str
    problem: str
    sources: list[Source]
    state: str
    made: str
    by: str
    at: str
    decided: str
    reason: str


def problems_of(proposal: Mapping[str, Any], ids: set[str] | None) -> list[str]:
    """Why one proposal breaks a rule of its type, slug, lengths and sources, `[]` when none.
    `ids` are the sources the run was handed; `None` takes any."""
    out = []
    if proposal.get("type") not in TYPES:
        out.append(f"type {proposal.get('type')!r} is not a branch type")
    slug = str(proposal.get("slug") or "")
    if not SLUG.match(slug) or len(slug) > SLUG_MAX:
        out.append(f"slug {slug!r} is not a slug of at most {SLUG_MAX} characters")
    if not 0 < len(str(proposal.get("title") or "").strip()) <= TITLE_MAX:
        out.append(f"the title is empty or over {TITLE_MAX} characters")
    if not PROBLEM_MIN <= len(str(proposal.get("problem") or "")) <= PROBLEM_MAX:
        out.append(f"the problem is not {PROBLEM_MIN} to {PROBLEM_MAX} characters")
    sources = proposal.get("sources") or []
    if not sources:
        out.append("it names no source")
    unknown = [s for s in sources if ids is not None and s not in ids]
    if unknown:
        out.append(f"{', '.join(unknown[:3])} is not in this run's input")
    return out


def kept(
    items: Sequence[Mapping[str, Any]], ids: set[str] | None
) -> tuple[list[Mapping[str, Any]], list[str]]:
    """The proposals that keep the rules, at most `PER_RUN`, and why each other was dropped."""
    keep, rejected = [], []
    for n, p in enumerate(items, start=1):
        said = problems_of(p, ids) if n <= PER_RUN else [f"past the {PER_RUN} a run may propose"]
        if said:
            rejected.append(f"{p.get('slug') or n}: {'; '.join(said)}")
        else:
            keep.append(p)
    return keep, rejected


def _proposal(row: Any) -> Proposal:
    return Proposal(
        id=int(row["id"]),
        agent=str(row["agent"]),
        unit=str(row["unit"]),
        run=str(row["run"]),
        type=str(row["type"]),
        slug=str(row["slug"]),
        title=str(row["title"]),
        problem=str(row["problem"]),
        sources=json.loads(row["sources"]),
        state=str(row["decision"]),
        made=str(row["made"]),
        by=str(row["by"]),
        at=str(row["at"]),
        decided=str(row["decided"]),
        reason=str(row["reason"]),
    )


def add(
    data: Data,
    workspace: str,
    agent: str,
    unit: str,
    items: Sequence[Mapping[str, Any]],
    *,
    run: str = "",
    sources: Mapping[str, Source] | None = None,
) -> list[int]:
    """Each item as a `pending` proposal of `agent`, about `unit` (`""`: none), in one
    transaction; the ids. `sources` maps a source id to what it is; an id it does not hold is
    kept as a citation."""
    known = sources or {}
    at, ids = now(), []
    with data.write() as conn:
        for p in items:
            cited = [
                known.get(s) or Source(id=str(s), kind="", unit=unit, at="")
                for s in dict.fromkeys(p.get("sources") or [])
            ]
            cur = conn.execute(
                "INSERT INTO proposals (workspace, agent, unit, run, type, slug, title, problem, "
                "sources, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    workspace,
                    agent,
                    unit,
                    run,
                    str(p.get("type") or ""),
                    str(p.get("slug") or ""),
                    str(p.get("title") or "").strip(),
                    str(p.get("problem") or ""),
                    json.dumps(cited),
                    at,
                ),
            )
            ids.append(int(cur.lastrowid or 0))
    return ids


def listed(data: Data, workspace: str, agent: str | None = None) -> list[Proposal]:
    """Newest first; only `agent`'s when given."""
    sql, args = "SELECT * FROM proposals WHERE workspace = ?", [workspace]
    if agent is not None:
        sql += " AND agent = ?"
        args.append(agent)
    with data.connect() as conn:
        rows = conn.execute(sql + " ORDER BY id DESC", args).fetchall()
    return [_proposal(r) for r in rows]


def one(data: Data, workspace: str, pid: int) -> Proposal:
    with data.connect() as conn:
        row = conn.execute(
            "SELECT * FROM proposals WHERE workspace = ? AND id = ?", (workspace, pid)
        ).fetchone()
    if row is None:
        raise Invalid(f"no proposal {pid} in this workspace")
    return _proposal(row)


def lists_of(made: Sequence[Proposal]) -> str:
    """The proposals already made, newest first, as three lists within `LISTS_MAX`: what a run
    must not propose again, and why a person dismissed one."""
    out, used = [], 0
    for state, head in (
        ("pending", "Pending"),
        ("accepted", "Accepted"),
        ("dismissed", "Dismissed, with why"),
    ):
        lines = []
        for p in made:
            if p["state"] != state:
                continue
            line = f"- [{p['type']}] {p['title']}"
            if state == "accepted":
                line += f" ({p['made']})"
            if state == "dismissed":
                line += f": {p['reason']}"
            if used + len(line) + 1 > LISTS_MAX:
                continue
            used += len(line) + 1
            lines.append(line)
        out.append(f"{head}:\n" + ("\n".join(lines) if lines else "- none"))
    return "\n\n".join(out)


def claim(data: Data, workspace: str, pid: int, to: State, reason: str = "") -> Proposal:
    """Move a `pending` proposal to `to`, by `owner`; one already decided is refused."""
    with data.write() as conn:
        done = conn.execute(
            "UPDATE proposals SET decision = ?, by = ?, decided = ?, reason = ? "
            "WHERE workspace = ? AND id = ? AND decision = 'pending'",
            (to, OWNER, now(), reason, workspace, pid),
        ).rowcount
    if not done:
        raise Invalid(f"proposal {pid} is not pending")
    return one(data, workspace, pid)


def set_made(data: Data, workspace: str, pid: int, unit: str) -> None:
    with data.write() as conn:
        conn.execute(
            "UPDATE proposals SET made = ? WHERE workspace = ? AND id = ?", (unit, workspace, pid)
        )


def unclaim(data: Data, workspace: str, pid: int) -> None:
    """Back to `pending`, after a unit could not be made."""
    with data.write() as conn:
        conn.execute(
            "UPDATE proposals SET decision = 'pending', by = '', decided = '', reason = '' "
            "WHERE workspace = ? AND id = ?",
            (workspace, pid),
        )


def brief_of(p: Proposal) -> str:
    """The new unit's `idea.md` words: the title, the problem, and what it rests on."""
    sources = "\n".join(
        f"- {s['id']}" + (f" ({s['kind']}, {s['unit'] or '-'}, {s['at']})" if s["kind"] else "")
        for s in p["sources"]
    )
    return f"{p['title']}\n\n{p['problem']}\n\nProposed by {p['agent']}, from:\n{sources}"


def check_slug(slug: str) -> str:
    slug = slug.strip()
    if not SLUG.match(slug) or len(slug) > SLUG_MAX:
        raise Invalid(f"a slug is lowercase words joined by hyphens, at most {SLUG_MAX} characters")
    return slug


def check_reason(reason: str) -> str:
    reason = " ".join(reason.split())
    if not 0 < len(reason) <= REASON_MAX:
        raise Invalid(f"a dismissal needs a reason of 1 to {REASON_MAX} characters")
    return reason


async def accept(
    data: Data, workspace: str, pid: int, slug: str, create: Callable[[str, str], Awaitable[str]]
) -> Proposal:
    """A unit made through `create(slug, brief)`, the app's own way of making one, its brief the
    proposal's; the slug may differ from the proposal's. Back to `pending` when it fails."""
    slug = check_slug(slug)
    p = await asyncio.to_thread(claim, data, workspace, pid, "accepted")
    try:
        unit = await create(slug, brief_of(p))
    except BaseException:
        await asyncio.to_thread(unclaim, data, workspace, pid)
        raise
    await asyncio.to_thread(set_made, data, workspace, pid, unit)
    return await asyncio.to_thread(one, data, workspace, pid)


async def dismiss(data: Data, workspace: str, pid: int, reason: str) -> Proposal:
    """Put aside with a reason of 1 to `REASON_MAX` characters, which the next run reads."""
    return await asyncio.to_thread(claim, data, workspace, pid, "dismissed", check_reason(reason))
