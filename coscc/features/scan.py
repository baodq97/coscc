"""Scan: one paid session reads what people had to step in for and proposes work.

A scan reads a workspace's interventions past its cursor (`Ctx.interventions`), puts at most
`LIMIT` of them and the proposals already made into one prompt, and opens one `scan` session
(`Ctx.session`). Each proposal it hands back that keeps the rules (`problems_of`) waits in
`scan_proposals` as `pending` until a person accepts it, which makes a unit through
`Ctx.create_unit`, or dismisses it with a reason the next scan reads. Nothing here touches the
shortlist, and the autopilot never reads these tables.

Off by default. Once on it runs every 24 h unless Settings says otherwise; *Scan now* on the
Backlog runs one at any time. A scan that cost more than `CAP_USD` turns the schedule off.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, TypedDict, get_args

from fastapi import APIRouter, Request
from starlette.routing import BaseRoute

from coscc.agent.policy import grant_for
from coscc.data import now
from coscc.plugin import Ctx, Plugin, Schedule, State, body
from coscc.runlog.journal import Intervention
from coscc.service.common import CONSEQUENCE, OWNER, Invalid
from coscc.units.submit import SCAN_TYPES, SLUG, SLUG_MAX

FEATURE = "scan"
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
    after     TEXT NOT NULL
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
    `PROMPT_MAX`) and how many proposals were cut from its lists. A cut never splits one
    second: what is left out would otherwise be behind the cursor."""
    lists, cut = _lists(proposals)
    fixed = f"{INSTRUCTIONS}\n\nProposals already made:\n\n{lists}\n\nInterventions:\n"
    taken: list[Intervention] = []
    size = len(fixed)
    for i in found[:LIMIT]:
        if size + len(_line(i)) + 1 > PROMPT_MAX:
            break
        taken.append(i)
        size += len(_line(i)) + 1
    left = found[len(taken) :]
    if taken and left and left[0].at == taken[-1].at:
        kept = [t for t in taken if t.at != left[0].at]
        taken = kept or taken
    return fixed + "\n".join(_line(i) for i in taken), taken, cut


def _proposal(row: Any) -> Proposal:
    return {
        "id": row["id"],
        "run": row["run"],
        "type": row["type"],
        "slug": row["slug"],
        "title": row["title"],
        "problem": row["problem"],
        "sources": json.loads(row["sources"]),
        "state": row["state"],
        "unit": row["unit"],
        "by": row["by"],
        "at": row["at"],
        "decided": row["decided"],
        "reason": row["reason"],
    }


def _run(row: Any) -> Run:
    return {
        "id": row["id"],
        "at": row["at"],
        "by": row["by"],
        "outcome": row["outcome"],
        "cost_usd": row["cost_usd"],
        "taken": row["taken"],
        "cut": row["cut"],
        "rejected": json.loads(row["rejected"]),
        "stopped": bool(row["stopped"]),
        "detail": row["detail"],
    }


class Tables:
    """The feature's three tables, for one `Ctx`. Blocking: call it from a thread."""

    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx

    def cursor(self, key: str) -> str:
        with self.ctx.data.connect() as conn:
            row = conn.execute(
                "SELECT after FROM scan_cursor WHERE workspace = ?", (key,)
            ).fetchone()
        return row["after"] if row else ""

    def proposals(self, key: str) -> list[Proposal]:
        """Newest first."""
        with self.ctx.data.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM scan_proposals WHERE workspace = ? ORDER BY id DESC", (key,)
            ).fetchall()
        return [_proposal(r) for r in rows]

    def proposal(self, key: str, pid: int) -> Proposal:
        with self.ctx.data.connect() as conn:
            row = conn.execute(
                "SELECT * FROM scan_proposals WHERE workspace = ? AND id = ?", (key, pid)
            ).fetchone()
        if row is None:
            raise Invalid(f"no proposal {pid} in this workspace")
        return _proposal(row)

    def runs(self, key: str, limit: int = 20) -> list[Run]:
        """Newest first."""
        with self.ctx.data.connect() as conn:
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
        with self.ctx.data.write() as conn:
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
                conn.execute(
                    "INSERT INTO scan_cursor (workspace, after) VALUES (?, ?) "
                    "ON CONFLICT (workspace) DO UPDATE SET after = excluded.after",
                    (key, taken[-1].at),
                )
            row = conn.execute("SELECT * FROM scan_runs WHERE id = ?", (run,)).fetchone()
        return _run(row)

    def claim(self, key: str, pid: int, to: ProposalState, reason: str = "") -> Proposal:
        """Move a `pending` proposal to `to`, by `owner`; one already decided is refused."""
        with self.ctx.data.write() as conn:
            done = conn.execute(
                "UPDATE scan_proposals SET state = ?, by = ?, decided = ?, reason = ? "
                "WHERE workspace = ? AND id = ? AND state = 'pending'",
                (to, OWNER, now(), reason, key, pid),
            ).rowcount
        if not done:
            raise Invalid(f"proposal {pid} is not pending")
        return self.proposal(key, pid)

    def set_unit(self, key: str, pid: int, unit: str) -> None:
        with self.ctx.data.write() as conn:
            conn.execute(
                "UPDATE scan_proposals SET unit = ? WHERE workspace = ? AND id = ?",
                (unit, key, pid),
            )

    def unclaim(self, key: str, pid: int) -> None:
        """Back to `pending`, after a unit could not be made."""
        with self.ctx.data.write() as conn:
            conn.execute(
                "UPDATE scan_proposals SET state = 'pending', by = '', decided = '', reason = '' "
                "WHERE workspace = ? AND id = ?",
                (key, pid),
            )


async def scan(ctx: Ctx, cwd: str, by: str) -> Run:
    """One scan of the workspace `cwd`, in five steps: read, skip, prompt, session, keep. `by` is `owner` for
    *Scan now*, `schedule` for a tick. `Invalid` while the feature is off here or a scan of the
    workspace already runs."""
    key = ctx.workspace_key(cwd)
    if not ctx.enabled(FEATURE, cwd):
        raise Invalid("scan is off in this workspace; turn it on in Settings")
    if key in _scanning:
        raise Invalid("a scan of this workspace is already running; wait for it to end")
    _scanning.add(key)
    store = Tables(ctx)
    try:
        after = await asyncio.to_thread(store.cursor, key)
        found = await asyncio.to_thread(ctx.interventions, cwd, after, LIMIT + 1)
        if not found:
            return await asyncio.to_thread(
                store.record, key, by, "skipped", detail="no intervention since the last scan"
            )
        made = await asyncio.to_thread(store.proposals, key)
        prompt, taken, cut = prompt_of(found, made)
        got = await ctx.session(cwd, "scan", prompt)
        cost = float(got.cost.get("cost_usd") or 0.0)
        stopped = cost > CAP_USD and ctx.schedule(FEATURE, cwd) != 0
        if stopped:
            ctx.set_schedule(FEATURE, cwd, 0)
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
    key = ctx.workspace_key(cwd)
    slug = slug.strip()
    if not SLUG.match(slug) or len(slug) > SLUG_MAX:
        raise Invalid(f"a slug is lowercase words joined by hyphens, at most {SLUG_MAX} characters")
    store = Tables(ctx)
    p = await asyncio.to_thread(store.claim, key, pid, "accepted")
    try:
        unit = await ctx.create_unit(cwd, slug, brief_of(p))
    except BaseException:
        await asyncio.to_thread(store.unclaim, key, pid)
        raise
    await asyncio.to_thread(store.set_unit, key, pid, unit)
    return await asyncio.to_thread(store.proposal, key, pid)


async def dismiss(ctx: Ctx, cwd: str, pid: int, reason: str) -> Proposal:
    """Dismissed with a reason of 1 to `REASON_MAX` characters, which the next scan reads."""
    key = ctx.workspace_key(cwd)
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
    runs = await asyncio.to_thread(Tables(ctx).runs, ctx.workspace_key(cwd), 1)
    if runs and _hours_since(runs[0]["at"]) < hours:
        return
    try:
        await scan(ctx, cwd, "schedule")
    except Invalid:
        return  # a scan already runs, or an update is under way: the next tick asks again


def status(ctx: Ctx, cwd: str) -> tuple[str, bool]:
    """The last scan, for the Settings row."""
    if ctx.state(FEATURE, cwd) == "off":
        return "Off in this workspace.", True
    runs = Tables(ctx).runs(ctx.workspace_key(cwd), 1)
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
        ctx.set_schedule(FEATURE, cwd, DEFAULT_HOURS)


def note_of(ctx: Ctx, cwd: str, runs: Sequence[Run]) -> str:
    """The one sentence while a scan's cost keeps the schedule off."""
    if ctx.schedule(FEATURE, cwd) != 0:
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
    async def proposals(request: Request) -> dict[str, object]:
        """`?cwd=`: `{on}` alone while the feature is off; else every proposal, newest first,
        the last scans, why the schedule is off, and the sentence beside *Scan now*."""
        cwd = request.query_params.get("cwd", "")
        key = ctx.workspace_key(cwd)
        if not ctx.enabled(FEATURE, cwd):
            return {"on": False}
        made = await asyncio.to_thread(store.proposals, key)
        runs = await asyncio.to_thread(store.runs, key)
        return {
            "on": True,
            "proposals": made,
            "runs": runs,
            "scanning": key in _scanning,
            "schedule": ctx.schedule(FEATURE, cwd),
            "note": note_of(ctx, cwd, runs),
            "consequence": CONSEQUENCE[FEATURE],
            "warning": grant_for(FEATURE).warning,
        }

    @router.post("/api/scan/proposals/{pid}")
    async def decide(pid: int, request: Request) -> Proposal:
        """`{cwd, action: accept, slug}` makes a unit from it; `{cwd, action: dismiss, reason}`
        puts it aside. Either acts for whoever holds the password, as `owner`."""
        sent = await body(request)
        cwd = str(sent.get("cwd") or "")
        action = sent.get("action")
        if action == "accept":
            return await accept(ctx, cwd, pid, str(sent.get("slug") or ""))
        if action == "dismiss":
            return await dismiss(ctx, cwd, pid, str(sent.get("reason") or ""))
        raise Invalid("action must be accept or dismiss")

    return router.routes


# The *Proposals* panel at the foot of the Backlog: a table of `<details>` rows, a filter by state,
# *Scan now* with its cost beside it, and each row's sources, *Accept* and *Dismiss* once opened.
# Hidden while the feature is off. `#proposal-<id>` opens that row. The colours are the Radix
# variables `screens/studio.py` uses; a feature may not import `screens`.
_JS = """
(function () {
  if (window.__coscc_scan) return;
  window.__coscc_scan = true;
  var C = window.coscc, filter = "pending", listed = null;
  var CHIP = {pending: "amber", accepted: "grass", dismissed: "gray"};
  var KIND = {"refused": "Refused", "ci-red": "CI red", "rerun": "Rerun",
    "review-round": "Review round", "impl-draft": "Impl draft", "integrate": "Integrate"};
  function here() {
    listed = listed || C.api("/api/workspaces")
      .then(function (r) { return r.ok ? r.json() : {}; })
      .then(function (j) { return j.workspaces || []; })
      .catch(function () { return []; });
    var ws = new URLSearchParams(window.location.search).get("ws");
    return listed.then(function (list) {
      return list.filter(function (w) { return w.name === ws; })[0] || list[0] || null;
    });
  }
  function el(tag, css, text) {
    var e = document.createElement(tag);
    if (css) e.style.cssText = css;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function chip(state) {
    var c = CHIP[state] || "gray";
    return el("span", "display:inline-block;border-radius:999px;padding:1px 8px;font-size:12px;" +
      "background:var(--" + c + "-3);color:var(--" + c + "-11);flex-shrink:0;width:72px;" +
      "text-align:center", state);
  }
  function button(text, soft) {
    var b = el("button", "border-radius:6px;padding:4px 10px;font-size:13px;cursor:pointer;" +
      "border:1px solid var(--" + (soft ? "gray-6" : "accent-9") + ");background:var(--" +
      (soft ? "gray-2" : "accent-9") + ");color:var(--" + (soft ? "gray-12" : "accent-contrast") + ")", text);
    b.type = "button";
    return b;
  }
  function input(value, label) {
    var i = el("input", "flex:1;min-width:160px;border:1px solid var(--gray-6);border-radius:6px;" +
      "padding:4px 8px;font-size:13px;background:var(--gray-1);color:var(--gray-12)");
    i.value = value;
    i.setAttribute("aria-label", label);
    return i;
  }
  function muted(text) { return el("div", "color:var(--gray-11);font-size:13px", text); }
  function act(w, p, sent, err) {
    sent.cwd = w.path;
    return C.api("/api/scan/proposals/" + p.id, {method: "POST",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify(sent)})
      .then(function (r) { return r.json().then(function (j) { return [r.ok, j]; }); })
      .then(function (got) {
        if (got[0]) { window.location.hash = "proposal-" + p.id; return draw(); }
        err.textContent = got[1].detail || got[1].error || "That did not work.";
      });
  }
  function sources(p) {
    var t = el("table", "width:100%;border-collapse:collapse;font-size:13px;margin:8px 0");
    var head = el("tr");
    ["Kind", "Unit", "When"].forEach(function (h) {
      head.appendChild(el("th", "text-align:left;color:var(--gray-11);font-weight:500;" +
        "padding:4px 8px 4px 0;border-bottom:1px solid var(--gray-5)", h));
    });
    t.appendChild(head);
    p.sources.forEach(function (s) {
      var r = el("tr");
      [KIND[s.kind] || s.kind, s.unit || "-", C.ago(s.at)].forEach(function (v) {
        r.appendChild(el("td", "padding:4px 8px 4px 0;border-bottom:1px solid var(--gray-4)", v));
      });
      t.appendChild(r);
    });
    return t;
  }
  function actions(w, p) {
    var box = el("div", "display:flex;flex-direction:column;gap:8px;margin-top:8px");
    var err = el("div", "color:var(--red-11);font-size:13px");
    var one = el("div", "display:flex;gap:8px;align-items:center;flex-wrap:wrap");
    var slug = input(p.slug, "Slug of the new unit");
    var ok = button("Accept");
    ok.onclick = function () { act(w, p, {action: "accept", slug: slug.value}, err); };
    one.appendChild(slug); one.appendChild(ok);
    var two = el("div", "display:flex;gap:8px;align-items:center;flex-wrap:wrap");
    var why = input("", "Why it is dismissed");
    why.placeholder = "Why it is dismissed";
    var no = button("Dismiss", true);
    var hint = muted("Dismiss needs a reason.");
    function check() {
      var empty = !why.value.trim();
      no.disabled = empty; no.style.opacity = empty ? "0.5" : "1";
      hint.style.display = empty ? "block" : "none";
    }
    why.oninput = check; check();
    no.onclick = function () { act(w, p, {action: "dismiss", reason: why.value}, err); };
    two.appendChild(why); two.appendChild(no);
    box.appendChild(one); box.appendChild(two); box.appendChild(hint); box.appendChild(err);
    return box;
  }
  function row(w, p) {
    var d = el("details", "border-bottom:1px solid var(--gray-5)");
    d.id = "proposal-" + p.id;
    var s = el("summary", "display:flex;gap:12px;align-items:center;padding:10px 0;cursor:pointer;" +
      "flex-wrap:wrap");
    s.appendChild(chip(p.state));
    s.appendChild(el("span", "flex:1;min-width:160px;color:var(--gray-12)", p.title));
    s.appendChild(el("span", "color:var(--gray-11);font-size:13px;width:64px", p.type));
    s.appendChild(el("span", "color:var(--gray-11);font-size:13px;width:84px",
      p.sources.length + (p.sources.length === 1 ? " source" : " sources")));
    s.appendChild(el("span", "color:var(--gray-11);font-size:13px;width:110px", C.ago(p.at)));
    d.appendChild(s);
    var body = el("div", "padding:0 0 14px");
    body.appendChild(el("p", "margin:4px 0;white-space:pre-wrap;color:var(--gray-12);font-size:14px",
      p.problem));
    body.appendChild(sources(p));
    if (p.state === "pending") body.appendChild(actions(w, p));
    if (p.state === "accepted") body.appendChild(muted("Accepted as " + p.unit + ", " + C.ago(p.decided) + "."));
    if (p.state === "dismissed") body.appendChild(muted("Dismissed " + C.ago(p.decided) + ": " + p.reason));
    d.appendChild(body);
    return d;
  }
  function last(runs) {
    if (!runs.length) return "No scan yet.";
    var r = runs[0], cost = "$" + r.cost_usd.toFixed(2);
    if (r.outcome === "skipped") return "Last scan " + C.ago(r.at) + ": nothing new, $0.00.";
    if (r.outcome === "failed") return "Last scan " + C.ago(r.at) + " failed, " + cost + ".";
    return "Last scan " + C.ago(r.at) + ": " + r.taken + " interventions read, " + cost + ".";
  }
  var slotEl = null;
  function draw() {
    var target = slotEl;
    return here().then(function (w) {
      if (!w || !target) return;
      return C.api("/api/scan/proposals?cwd=" + encodeURIComponent(w.path))
        .then(function (r) { return r.ok ? r.json() : {on: false}; })
        .then(function (j) { paint(target, w, j); });
    }).catch(function () {});
  }
  function paint(target, w, j) {
    target.textContent = "";
    if (!j.on) return;
    var panel = el("section", "background:var(--gray-2);border:1px solid var(--gray-5);" +
      "border-radius:14px;padding:22px;width:100%;box-sizing:border-box");
    panel.id = "scan-proposals";
    var head = el("div", "display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px");
    head.appendChild(el("h3", "margin:0;font-size:18px;font-weight:500;flex:1", "Proposals"));
    var go = button(j.scanning ? "Scanning" : "Scan now", true);
    go.id = "scan-now";
    go.disabled = !!j.scanning;
    var said = muted(j.consequence);
    var err = el("div", "color:var(--red-11);font-size:13px");
    go.onclick = function () {
      go.disabled = true; go.textContent = "Scanning";
      C.api("/api/scan?cwd=" + encodeURIComponent(w.path), {method: "POST"})
        .then(function (r) { return r.json().then(function (b) { return [r.ok, b]; }); })
        .then(function (got) { if (!got[0]) err.textContent = got[1].detail || "The scan did not run."; })
        .catch(function () {}).then(draw);
    };
    head.appendChild(go);
    panel.appendChild(head);
    var line = el("div", "display:flex;gap:16px;flex-wrap:wrap;margin-bottom:6px");
    line.appendChild(said);
    line.appendChild(muted(last(j.runs)));
    panel.appendChild(line);
    if (j.note) panel.appendChild(el("div", "color:var(--amber-11);font-size:13px;margin-bottom:6px", j.note));
    panel.appendChild(err);
    var tabs = el("div", "display:flex;gap:6px;margin:10px 0;flex-wrap:wrap");
    tabs.setAttribute("role", "group");
    tabs.setAttribute("aria-label", "Filter proposals by state");
    ["pending", "accepted", "dismissed", "all"].forEach(function (f) {
      var n = j.proposals.filter(function (p) { return f === "all" || p.state === f; }).length;
      var t = button(f.charAt(0).toUpperCase() + f.slice(1) + " " + n, f !== filter);
      t.setAttribute("aria-pressed", f === filter ? "true" : "false");
      t.onclick = function () { filter = f; paint(target, w, j); };
      tabs.appendChild(t);
    });
    panel.appendChild(tabs);
    var shown = j.proposals.filter(function (p) { return filter === "all" || p.state === filter; });
    shown.forEach(function (p) { panel.appendChild(row(w, p)); });
    if (!shown.length) panel.appendChild(muted(j.proposals.length ? "No proposal in this state." :
      "No proposal yet: a scan makes them from the run log."));
    target.appendChild(panel);
    var want = window.location.hash.slice(1);
    var open = want && document.getElementById(want);
    if (open && open.tagName === "DETAILS") { open.open = true; open.scrollIntoView({block: "start"}); }
  }
  C.slot("slot-backlog", function (target) {
    slotEl = target;
    var want = window.location.hash.match(/^#proposal-(\\d+)$/);
    if (want) filter = "all";
    draw();
  });
})();
"""

PLUGIN = Plugin(
    FEATURE,
    routes,
    scripts=(_JS,),
    tables=TABLES,
    default="off",
    status=status,
    on_set=on_set,
    schedule=Schedule(HOURS, DEFAULT_HOURS, tick),
)
