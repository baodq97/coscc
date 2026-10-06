"""Scan: one paid session reads what people had to step in for and proposes work.

A scan reads a workspace's interventions past its cursor (`Ctx.runs.interventions`), puts at most
`LIMIT` of them and the proposals already made into one prompt, and opens one `scan` session
(`Ctx.agents.session`). Each proposal it hands back that keeps the rules (`problems_of`) waits in
`scan_proposals` as `pending` until a person accepts it, which makes a unit through
`Ctx.units.create_unit`, or dismisses it with a reason the next scan reads. Nothing here touches the
shortlist, and the autopilot never reads these tables.

Off by default. Once on it runs every 24 h unless Settings says otherwise; *Scan now* on the
Backlog runs one at any time. A scan that cost more than `CAP_USD` turns the schedule off.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, TypedDict, cast, get_args

from fastapi import APIRouter, Request
from starlette.routing import BaseRoute

from coscc.kernel import (
    OWNER,
    Ctx,
    Feature,
    Grant,
    Intervention,
    Invalid,
    Schedule,
    Session,
    State,
    body,
    now,
)

NAME = "scan"

# The loop's branch types and slug grammar (`coscc/loop/__init__.py`'s `BRANCH_TYPES`, `SLUG_RE`
# and `SLUG_MAX`), which a feature may not import; `test_scan` pins the copies.
SCAN_TYPES = ("feat", "fix", "docs", "refactor", "test", "chore", "perf", "build", "ci", "revert")
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", re.ASCII)
SLUG_MAX = 60

# Said beside *Scan now* on the Backlog, before it is pressed.
CONSEQUENCE = "Opens one paid session, about $1 at most, that proposes work from the run log."
# No tools and no commands, like the estimate; it hands its proposals back through `submit`,
# whose declaration holds types and leaves their rules (`problems_of`) to this file, so one bad
# proposal drops alone.
# Two turns is what a measured scan took (`submit`, then the end). The budget is checked only
# once a turn is paid for, so $0.68 is $1 less the dearest whole scan measured ($0.32, one
# sample): a scan stays near $1 at worst, not under it for sure. One more turn could pass it.
SESSION = Session(
    NAME,
    Grant(
        max_turns=2,
        max_budget_usd=0.68,
        warning="Scanning opens one paid session (2 turns, $0.68 ceiling, about $1 at most) on "
        "the model of the Agents page row `estimate`.",
    ),
    {
        "kind": "session",
        "version": 1,
        "fields": {
            "proposals": {
                "list": {
                    "type": "text",
                    "slug": "text",
                    "title": "text",
                    "problem": "text",
                    "sources": {"list": "text"},
                }
            }
        },
    },
    "Hand the app the work you propose, each item with the interventions it gathers.",
    own_turns=True,
)
# The bounds of a scan's input, its output, a dismissal and its cost.
LIMIT = 25
PROMPT_MAX = 12_000
LISTS_MAX = 2_000
PROPOSALS_MAX = 8
TITLE_MAX = 120
PROBLEM_MIN, PROBLEM_MAX = 200, 1_000
REASON_MAX = 500
CAP_USD = 1.0
HOURS = (0, 12, 24, 168)
DEFAULT_HOURS = 24

ProposalState = Literal["pending", "accepted", "dismissed"]
PROPOSAL_STATES: tuple[ProposalState, ...] = get_args(ProposalState)
Outcome = Literal["done", "skipped", "failed"]

TABLES = (
    """CREATE TABLE IF NOT EXISTS scan_proposals (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace TEXT NOT NULL,
    run       INTEGER NOT NULL,
    type      TEXT NOT NULL,
    slug      TEXT NOT NULL,
    title     TEXT NOT NULL,
    problem   TEXT NOT NULL,
    sources   TEXT NOT NULL,
    state     TEXT NOT NULL DEFAULT 'pending',
    unit      TEXT NOT NULL DEFAULT '',
    by        TEXT NOT NULL DEFAULT '',
    at        TEXT NOT NULL,
    decided   TEXT NOT NULL DEFAULT '',
    reason    TEXT NOT NULL DEFAULT ''
)""",
    """CREATE TABLE IF NOT EXISTS scan_runs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace TEXT NOT NULL,
    at        TEXT NOT NULL,
    by        TEXT NOT NULL,
    outcome   TEXT NOT NULL,
    cost_usd  REAL NOT NULL DEFAULT 0,
    session   TEXT NOT NULL DEFAULT '',
    taken     INTEGER NOT NULL DEFAULT 0,
    cut       INTEGER NOT NULL DEFAULT 0,
    rejected  TEXT NOT NULL DEFAULT '[]',
    stopped   INTEGER NOT NULL DEFAULT 0,
    detail    TEXT NOT NULL DEFAULT ''
)""",
    """CREATE TABLE IF NOT EXISTS scan_cursor (
    workspace TEXT PRIMARY KEY,
    after     TEXT NOT NULL,
    seen      TEXT NOT NULL DEFAULT '[]'
)""",
)

INSTRUCTIONS = f"""You read the times a person had to step in on this workspace's work, and \
propose the work that would stop them happening again.

Each line under "Interventions" is one: its id, kind, unit, stage, time, and what the app \
recorded. The kinds:
- refused: the gate refused a step a person started.
- ci-red: CI went red on a pull request.
- rerun: a person ran a step again by hand, with their note.
- review-round: a review asked for changes; its findings follow.
- impl-draft: impl ended as a draft and had to be run again.
- integrate: a person integrated a branch with main.

Group the interventions that share one cause, and propose one work item per group:
- type: one of {", ".join(SCAN_TYPES)}.
- slug: lowercase words joined by hyphens, at most {SLUG_MAX} characters.
- title: at most {TITLE_MAX} characters.
- problem: {PROBLEM_MIN} to {PROBLEM_MAX} characters, as an intent's Problem section says \
it: what goes wrong, how often, and what it costs. Name no fix.
- sources: the ids of the interventions it gathers, copied exactly from the list below.

Propose at most {PROPOSALS_MAX} items. Do not propose again what is pending or accepted, nor \
what was dismissed, for the reason given. An intervention that fits no item may be left out.

Call submit once with every item, then end your turn. Write nothing else."""


class Source(TypedDict):
    id: str
    kind: str
    unit: str
    at: str


class Proposal(TypedDict):
    id: int
    run: int
    type: str
    slug: str
    title: str
    problem: str
    sources: list[Source]
    state: str
    unit: str
    by: str
    at: str
    decided: str
    reason: str


class Run(TypedDict):
    id: int
    at: str
    by: str
    outcome: str
    cost_usd: float
    taken: int
    cut: int
    rejected: list[str]
    stopped: bool
    detail: str


class Proposals(TypedDict):
    on: bool
    proposals: list[Proposal]
    runs: list[Run]
    scanning: bool
    schedule: int
    note: str
    consequence: str
    warning: str


# The workspaces a scan of this process runs in now: a second press is refused at once.
_scanning: set[str] = set()


def problems_of(proposal: Mapping[str, Any], ids: set[str]) -> list[str]:
    """Why one proposal breaks a rule of its type, slug, lengths and sources, `[]` when none."""
    out = []
    if proposal.get("type") not in SCAN_TYPES:
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
    unknown = [s for s in sources if s not in ids]
    if unknown:
        out.append(f"{', '.join(unknown[:3])} is not in this scan's input")
    return out


def _line(i: Intervention) -> str:
    return f"- {i.id} | {i.kind} | {i.unit or '-'} | {i.stage or '-'} | {i.at} | {i.detail}"


def _lists(proposals: Sequence[Proposal]) -> tuple[str, int]:
    """The three lists of proposals already made, newest first, within `LISTS_MAX`; and how many
    did not fit."""
    out, cut, used = [], 0, 0
    for state, head in (
        ("pending", "Pending"),
        ("accepted", "Accepted"),
        ("dismissed", "Dismissed, with why"),
    ):
        lines = []
        for p in proposals:
            if p["state"] != state:
                continue
            line = f"- [{p['type']}] {p['title']}"
            if state == "accepted":
                line += f" ({p['unit']})"
            if state == "dismissed":
                line += f": {p['reason']}"
            if used + len(line) + 1 > LISTS_MAX:
                cut += 1
                continue
            used += len(line) + 1
            lines.append(line)
        out.append(f"{head}:\n" + ("\n".join(lines) if lines else "- none"))
    return "\n\n".join(out), cut


def prompt_of(
    found: Sequence[Intervention], proposals: Sequence[Proposal]
) -> tuple[str, list[Intervention], int]:
    """The prompt, the interventions it holds (oldest first, at most `LIMIT`, within
    `PROMPT_MAX`) and how many proposals were cut from its lists."""
    lists, cut = _lists(proposals)
    fixed = f"{INSTRUCTIONS}\n\nProposals already made:\n\n{lists}\n\nInterventions:\n"
    taken: list[Intervention] = []
    size = len(fixed)
    for i in found[:LIMIT]:
        if size + len(_line(i)) + 1 > PROMPT_MAX:
            break
        taken.append(i)
        size += len(_line(i)) + 1
    return fixed + "\n".join(_line(i) for i in taken), taken, cut


def _second_before(after: str) -> str:
    """`after` one second earlier, so a read past it holds the rest of `after`'s second."""
    try:
        return (datetime.fromisoformat(after) - timedelta(seconds=1)).isoformat(timespec="seconds")
    except ValueError:
        return after


def _proposal(row: Any) -> Proposal:
    got = {k: row[k] for k in Proposal.__annotations__}
    return cast(Proposal, {**got, "sources": json.loads(row["sources"])})


def _run(row: Any) -> Run:
    got = {k: row[k] for k in Run.__annotations__}
    return cast(
        Run, {**got, "rejected": json.loads(row["rejected"]), "stopped": bool(row["stopped"])}
    )


class Tables:
    """The feature's three tables, for one `Ctx`. Blocking: call it from a thread."""

    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx

    def cursor(self, key: str) -> tuple[str, list[str]]:
        """The time of the last intervention taken, and the ids taken at that second: a cut
        may split a second, and its rest is read by the next scan."""
        with self.ctx.store.connect() as conn:
            row = conn.execute(
                "SELECT after, seen FROM scan_cursor WHERE workspace = ?", (key,)
            ).fetchone()
        return (row["after"], json.loads(row["seen"])) if row else ("", [])

    def proposals(self, key: str) -> list[Proposal]:
        """Newest first."""
        with self.ctx.store.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM scan_proposals WHERE workspace = ? ORDER BY id DESC", (key,)
            ).fetchall()
        return [_proposal(r) for r in rows]

    def proposal(self, key: str, pid: int) -> Proposal:
        with self.ctx.store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM scan_proposals WHERE workspace = ? AND id = ?", (key, pid)
            ).fetchone()
        if row is None:
            raise Invalid(f"no proposal {pid} in this workspace")
        return _proposal(row)

    def runs(self, key: str, limit: int = 20) -> list[Run]:
        """Newest first."""
        with self.ctx.store.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM scan_runs WHERE workspace = ? ORDER BY id DESC LIMIT ?", (key, limit)
            ).fetchall()
        return [_run(r) for r in rows]

    def record(
        self,
        key: str,
        by: str,
        outcome: Outcome,
        *,
        cost: float = 0.0,
        session: str = "",
        taken: Sequence[Intervention] = (),
        cut: int = 0,
        rejected: Sequence[str] = (),
        stopped: bool = False,
        detail: str = "",
        proposals: Sequence[Mapping[str, Any]] = (),
    ) -> Run:
        """One scan's row, its proposals, and the cursor moved to the last intervention it took,
        in one transaction."""
        at = now()
        by_id = {i.id: Source(id=i.id, kind=i.kind, unit=i.unit, at=i.at) for i in taken}
        with self.ctx.store.write() as conn:
            cur = conn.execute(
                "INSERT INTO scan_runs (workspace, at, by, outcome, cost_usd, session, taken, cut, "
                "rejected, stopped, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    key,
                    at,
                    by,
                    outcome,
                    cost,
                    session,
                    len(taken),
                    cut,
                    json.dumps(list(rejected)),
                    int(stopped),
                    detail,
                ),
            )
            run = int(cur.lastrowid or 0)
            for p in proposals:
                conn.execute(
                    "INSERT INTO scan_proposals (workspace, run, type, slug, title, problem, "
                    "sources, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        key,
                        run,
                        p["type"],
                        p["slug"],
                        p["title"].strip(),
                        p["problem"],
                        json.dumps([by_id[s] for s in dict.fromkeys(p["sources"])]),
                        at,
                    ),
                )
            if outcome == "done" and taken:
                last = taken[-1].at
                was = conn.execute(
                    "SELECT after, seen FROM scan_cursor WHERE workspace = ?", (key,)
                ).fetchone()
                seen = json.loads(was["seen"]) if was and was["after"] == last else []
                seen += [i.id for i in taken if i.at == last]
                conn.execute(
                    "INSERT INTO scan_cursor (workspace, after, seen) VALUES (?, ?, ?) "
                    "ON CONFLICT (workspace) DO UPDATE SET after = excluded.after, "
                    "seen = excluded.seen",
                    (key, last, json.dumps(seen)),
                )
            row = conn.execute("SELECT * FROM scan_runs WHERE id = ?", (run,)).fetchone()
        return _run(row)

    def claim(self, key: str, pid: int, to: ProposalState, reason: str = "") -> Proposal:
        """Move a `pending` proposal to `to`, by `owner`; one already decided is refused."""
        with self.ctx.store.write() as conn:
            done = conn.execute(
                "UPDATE scan_proposals SET state = ?, by = ?, decided = ?, reason = ? "
                "WHERE workspace = ? AND id = ? AND state = 'pending'",
                (to, OWNER, now(), reason, key, pid),
            ).rowcount
        if not done:
            raise Invalid(f"proposal {pid} is not pending")
        return self.proposal(key, pid)

    def set_unit(self, key: str, pid: int, unit: str) -> None:
        with self.ctx.store.write() as conn:
            conn.execute(
                "UPDATE scan_proposals SET unit = ? WHERE workspace = ? AND id = ?",
                (unit, key, pid),
            )

    def unclaim(self, key: str, pid: int) -> None:
        """Back to `pending`, after a unit could not be made."""
        with self.ctx.store.write() as conn:
            conn.execute(
                "UPDATE scan_proposals SET state = 'pending', by = '', decided = '', reason = '' "
                "WHERE workspace = ? AND id = ?",
                (key, pid),
            )


def _check_on(ctx: Ctx, cwd: str) -> None:
    if not ctx.settings.enabled(cwd):
        raise Invalid("scan is off in this workspace; turn it on in Settings")


async def scan(ctx: Ctx, cwd: str, by: str) -> Run:
    """One scan of the workspace `cwd`, in five steps: read, skip, prompt, session, keep. `by` is `owner` for
    *Scan now*, `schedule` for a tick. `Invalid` while the feature is off here or a scan of the
    workspace already runs."""
    key = ctx.units.key(cwd)
    _check_on(ctx, cwd)
    if key in _scanning:
        raise Invalid("a scan of this workspace is already running; wait for it to end")
    _scanning.add(key)
    store = Tables(ctx)
    try:
        after, seen = await asyncio.to_thread(store.cursor, key)
        found = await asyncio.to_thread(
            ctx.runs.interventions, cwd, _second_before(after), LIMIT + 1 + len(seen)
        )
        found = [i for i in found if i.at >= after and i.id not in seen]
        if not found:
            return await asyncio.to_thread(
                store.record, key, by, "skipped", detail="no intervention since the last scan"
            )
        made = await asyncio.to_thread(store.proposals, key)
        prompt, taken, cut = prompt_of(found, made)
        got = await ctx.agents.session(cwd, NAME, prompt)
        cost = float(got.cost.get("cost_usd") or 0.0)
        stopped = cost > CAP_USD and ctx.settings.schedule(cwd) != 0
        if stopped:
            ctx.settings.set_schedule(cwd, 0)
        if got.object is None:
            return await asyncio.to_thread(
                store.record,
                key,
                by,
                "failed",
                cost=cost,
                session=got.run,
                cut=cut,
                stopped=stopped,
                detail=got.failure,
            )
        ids = {i.id for i in taken}
        kept, rejected = [], []
        for n, p in enumerate(got.object.get("proposals") or [], start=1):
            said = (
                problems_of(p, ids)
                if n <= PROPOSALS_MAX
                else [f"past the {PROPOSALS_MAX} a scan may propose"]
            )
            if said:
                rejected.append(f"{p.get('slug') or n}: {'; '.join(said)}")
            else:
                kept.append(p)
        return await asyncio.to_thread(
            store.record,
            key,
            by,
            "done",
            cost=cost,
            session=got.run,
            taken=taken,
            cut=cut,
            rejected=rejected,
            stopped=stopped,
            proposals=kept,
        )
    finally:
        _scanning.discard(key)


def brief_of(p: Proposal) -> str:
    """The new unit's `idea.md` words: the title, the problem, and the interventions."""
    sources = "\n".join(
        f"- {s['id']} ({s['kind']}, {s['unit'] or '-'}, {s['at']})" for s in p["sources"]
    )
    return f"{p['title']}\n\n{p['problem']}\n\nProposed by a scan of the run log, from:\n{sources}"


async def accept(ctx: Ctx, cwd: str, pid: int, slug: str) -> Proposal:
    """A unit through the app's own way of making one, its brief the proposal's. The slug
    may differ from the proposal's. The shortlist is not touched."""
    key = ctx.units.key(cwd)
    _check_on(ctx, cwd)
    slug = slug.strip()
    if not SLUG.match(slug) or len(slug) > SLUG_MAX:
        raise Invalid(f"a slug is lowercase words joined by hyphens, at most {SLUG_MAX} characters")
    store = Tables(ctx)
    p = await asyncio.to_thread(store.claim, key, pid, "accepted")
    try:
        unit = await ctx.units.create_unit(cwd, slug, brief_of(p))
    except BaseException:
        await asyncio.to_thread(store.unclaim, key, pid)
        raise
    await asyncio.to_thread(store.set_unit, key, pid, unit)
    return await asyncio.to_thread(store.proposal, key, pid)


async def dismiss(ctx: Ctx, cwd: str, pid: int, reason: str) -> Proposal:
    """Dismissed with a reason of 1 to `REASON_MAX` characters, which the next scan reads."""
    key = ctx.units.key(cwd)
    _check_on(ctx, cwd)
    reason = " ".join(reason.split())
    if not 0 < len(reason) <= REASON_MAX:
        raise Invalid(f"a dismissal needs a reason of 1 to {REASON_MAX} characters")
    return await asyncio.to_thread(Tables(ctx).claim, key, pid, "dismissed", reason)


def _hours_since(at: str) -> float:
    try:
        then = datetime.fromisoformat(at)
    except ValueError:
        return float("inf")
    return (datetime.now(timezone.utc) - then) / timedelta(hours=1)


async def tick(ctx: Ctx, cwd: str, hours: int) -> None:
    """A scheduled scan, once `hours` passed since the last scan of the workspace or never."""
    runs = await asyncio.to_thread(Tables(ctx).runs, ctx.units.key(cwd), 1)
    if runs and _hours_since(runs[0]["at"]) < hours:
        return
    try:
        await scan(ctx, cwd, "schedule")
    except Invalid:
        return  # a scan already runs, or an update is under way: the next tick asks again


def status(ctx: Ctx, cwd: str) -> tuple[str, bool]:
    """The last scan, for the Settings row."""
    if ctx.settings.state(cwd) == "off":
        return "Off in this workspace.", True
    runs = Tables(ctx).runs(ctx.units.key(cwd), 1)
    if not runs:
        return "On: no scan yet.", True
    last = runs[0]
    if last["outcome"] == "skipped":
        return "On: the last scan found nothing new.", True
    if last["outcome"] == "failed":
        return f"On: the last scan failed, ${last['cost_usd']:.2f}.", True
    return (
        f"On: the last scan read {last['taken']} interventions for ${last['cost_usd']:.2f}.",
        True,
    )


def on_set(ctx: Ctx, cwd: str, state: State) -> None:
    """Turned on, it scans every 24 h until Settings says otherwise."""
    if state == "on":
        ctx.settings.set_schedule(cwd, DEFAULT_HOURS)


def note_of(ctx: Ctx, cwd: str, runs: Sequence[Run]) -> str:
    """The one sentence while a scan's cost keeps the schedule off."""
    if ctx.settings.schedule(cwd) != 0:
        return ""
    for r in runs:
        if r["by"] == "schedule" and not r["stopped"]:
            return ""
        if r["stopped"]:
            return f"The schedule is off: a scan cost ${r['cost_usd']:.2f}, over ${CAP_USD:.2f}."
    return ""


def routes(ctx: Ctx) -> Sequence[BaseRoute]:
    router = APIRouter()
    store = Tables(ctx)

    @router.post("/api/scan")
    async def run_scan(request: Request) -> Run:
        """**Opens one paid session** (about $1 at most) unless nothing happened since the last
        scan: `?cwd=`. The scan's row, its proposals in `GET /api/scan/proposals`."""
        return await scan(ctx, request.query_params.get("cwd", ""), OWNER)

    @router.get("/api/scan/proposals")
    async def proposals(request: Request) -> Proposals:
        """`?cwd=`: `on` false and nothing else while the feature is off; else every proposal,
        newest first, the last scans, why the schedule is off, and the sentence beside *Scan now*."""
        cwd = request.query_params.get("cwd", "")
        key = ctx.units.key(cwd)
        if not ctx.settings.enabled(cwd):
            return {
                "on": False,
                "proposals": [],
                "runs": [],
                "scanning": False,
                "schedule": 0,
                "note": "",
                "consequence": "",
                "warning": "",
            }
        made = await asyncio.to_thread(store.proposals, key)
        runs = await asyncio.to_thread(store.runs, key)
        return {
            "on": True,
            "proposals": made,
            "runs": runs,
            "scanning": key in _scanning,
            "schedule": ctx.settings.schedule(cwd),
            "note": note_of(ctx, cwd, runs),
            "consequence": CONSEQUENCE,
            "warning": SESSION.grant.warning,
        }

    @router.post("/api/scan/proposals/{pid}")
    async def decide(pid: int, request: Request) -> Proposal:
        """`{cwd, action: accept, slug}` makes a unit from it; `{cwd, action: dismiss, reason}`
        puts it aside. Either acts for whoever holds the password, as `owner`, and is refused
        while the feature is off."""
        sent = await body(request)
        cwd = str(sent.get("cwd") or "")
        action = sent.get("action")
        if action == "accept":
            return await accept(ctx, cwd, pid, str(sent.get("slug") or ""))
        if action == "dismiss":
            return await dismiss(ctx, cwd, pid, str(sent.get("reason") or ""))
        raise Invalid("action must be accept or dismiss")

    return router.routes


FEATURE = Feature(
    NAME,
    routes,
    tables=TABLES,
    default="off",
    status=status,
    on_set=on_set,
    schedule=Schedule(HOURS, DEFAULT_HOURS, tick),
    sessions=(SESSION,),
    summary="Reads the run log on a schedule and proposes units for what keeps needing a person.",
)
