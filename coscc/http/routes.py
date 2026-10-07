"""The JSON surface: every route of the app, on one `APIRouter`.

Being a plain ASGI app, tests drive it in-process with `httpx.ASGITransport` and no Node.
Nothing here reads the environment, returns configuration or runs anything the caller names,
and nothing decides: every route translates a request into a `Core` call and the result
back into JSON.

The studio (`ui/`, served by `coscc/http/studio.py` at every path no route takes) reads and acts
only through here, and hears changes on `/api/stream`. The owner's own tools, the updater's
trial of a new build and `scripts/install.sh` use these routes too; a route nobody calls is
not kept.

The guard in `coscc/http/auth.py` serves `/login`, `/setup` and `/logout`; nothing here may use
them. Every route sits behind that guard: without a live session only `GET /api/health` gets
through. One password, one user: whoever holds it or a session cookie can call every route
below. A name a body carries (`name`, `by`, `stopped_by`, `recorded_by`) is written as
sent, or as `kernel.OWNER` when absent; neither is an identity. An answer's `by` (`person` or
`delegated`) is written as sent too: a label, not an identity check. Tests that build this app alone
drive it without the guard.

A refusal is `Invalid`, answered in one place (`coscc/http/app.py`).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, AsyncIterator, NotRequired, TypedDict

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from coscc import kernel
from coscc.agent import pack
from coscc.agent.pack import PackShown
from coscc.store.db import Data
from coscc.bus import Event
from coscc.http import plugin
from coscc.kernel import Invalid
from coscc.leif.agents import AgentPage, ProposalsView
from coscc.leif.chat import ChatHistory, ChatSessions
from coscc.leif.insights import Insights
from coscc.runner import triggers
from coscc.runner.steps import NextStep
from coscc.runner.watch import EventsPage
from coscc.units import proposals
from coscc.units.backlog import SHORTLIST_MAX
from coscc.units.read import Cards, Detail, UpNext, cards, detail
from coscc.units.workspaces import WorkspaceList
from coscc.update.updater import refusals, update_words

if TYPE_CHECKING:
    from coscc.http.app import Core

log = logging.getLogger(__name__)


class AutopilotSettings(TypedDict):
    cwd: str
    autopilot: bool
    autopilot_may_ship: bool
    max_parallel: int
    daily_cap_usd: float
    # Why the autopilot cannot run on this bind, or empty.
    refused_because: str


class Build(TypedDict, total=False):
    """A published or local build the updater knows of: what it is and whether it may be applied."""

    state: str
    version: str
    commit: str
    started: str
    by: str
    workspace: str
    wheel: str
    sha256: str
    error: str | None
    log: str


class UpdateError(TypedDict):
    message: str
    log: str
    log_tail: NotRequired[str]


class UpdateStatus(TypedDict):
    version: str
    build_id: str
    commit: str
    commit_label: str
    install: str
    shape: str
    reason: str
    # The updater's own state; absent where updates are not available (a checkout).
    state: NotRequired[str]
    window: NotRequired[bool]
    pending: NotRequired[dict[str, Any] | None]
    release: NotRequired[Build | None]
    local: NotRequired[Build | None]
    last: NotRequired[dict[str, Any] | None]
    checked_at: NotRequired[str | None]
    error: NotRequired[UpdateError | None]
    warning: NotRequired[str]
    log: NotRequired[str]
    # The panel's sentences, and the buttons it may show: `build-local`, `apply-<channel>`, `cancel`.
    line: str
    local_line: str
    actions: list[str]


class Decided(TypedDict):
    """An answer given for the owner: one sent `by: delegated`."""

    unit: str
    artifact: str
    n: int
    question: str
    text: str
    # The name it was sent with.
    name: str
    date: str


# A comment line this often keeps a quiet stream open through proxies and tells the page it is
# still connected. Chosen, not measured.
STREAM_PING_SECONDS = 10.0
# How long one stream lasts before it ends and the page connects again. Bounded because the
# server, stopping for an update, waits for every open response to end: an endless stream held
# an Apply for 7 minutes (10-04). It also bounds a session signed out while connected.
STREAM_LIFETIME_SECONDS = kernel.STREAM_SECONDS


def _core(request: Request) -> Core:
    return request.app.state.core


def _cwd(request: Request) -> str:
    return request.query_params.get("cwd", "")


router = APIRouter()


@router.get("/api/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@router.get("/api/stream")
async def stream(request: Request) -> StreamingResponse:
    """Every bus event as server-sent events, `{subject, ...payload}`, the payload its subject
    declares (`bus.SCHEMAS`). It mostly says that something changed: the page reads what it shows
    again.
    It ends after `STREAM_LIFETIME_SECONDS` with an `end` event, and the page connects again at
    once; an event in that second is missed, and the page's slow refresh covers it."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Event] = asyncio.Queue()

    def heard(e: Event) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, e)

    stop = _core(request).bus.watch(heard)

    async def events() -> AsyncIterator[str]:
        ends = loop.time() + STREAM_LIFETIME_SECONDS
        try:
            yield "retry: 1000\n: open\n\n"
            while (left := ends - loop.time()) > 0:
                try:
                    e = await asyncio.wait_for(queue.get(), min(STREAM_PING_SECONDS, left))
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                data = {"subject": e.name, **e.payload}
                yield f"data: {json.dumps(data)}\n\n"
            yield "event: end\ndata: {}\n\n"
        finally:
            stop()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/workspaces")
async def get_workspaces(request: Request) -> WorkspaceList:
    return _core(request).ws.all()


@router.post("/api/workspaces")
async def add_workspace(request: Request) -> Any:
    """Adopt a directory under the working folder, or clone one into it.

    The working folder comes from the environment only; a body naming one is ignored.
    """
    body = await kernel.body(request)
    return await _core(request).ws.add(
        str(body.get("name", "")),
        label=str(body.get("label", "") or ""),
        repo_url=(body.get("repo_url") or None),
    )


@router.post("/api/workspaces/{name}/pull")
async def pull_workspace(name: str, request: Request) -> Any:
    return await _core(request).ws.pull(name)


@router.post("/api/workspaces/{name}/label")
async def label_workspace(name: str, request: Request) -> Any:
    """`{label}`: the line a workspace is described by."""
    body = await kernel.body(request)
    return _core(request).ws.set_label(name, str(body.get("label") or ""))


@router.post("/api/workspaces/{name}/remove")
async def remove_workspace(name: str, request: Request) -> Any:
    """Stop listing a workspace. Its directory, units and run log stay; adding it again brings
    them back. The scratch of units no listed workspace holds is swept."""
    return _core(request).ws.remove(name)


@router.get("/api/agents")
async def get_agents(request: Request) -> AgentPage:
    """Every agent, every part of its row as it stands and as built in, which keys the owner set,
    its problems, skills, hash and runs grouped by definition; the tool catalog, each feature on
    or off for `cwd`; what was wrong."""
    core, cwd = _core(request), _cwd(request)
    return core.agents.agent_page(core.ws.key(cwd) if cwd else None, cwd=cwd)


@router.post("/api/agents/field")
async def set_agent_field(request: Request) -> AgentPage:
    """`{key, field, value}` saves one part of one agent's row in the owner's layer: a frontmatter
    key whole, `body`, or `skill:<name>`; `value` `null` puts the built-in's back. A row that would
    not pass its checks is a 400 naming every reason, and nothing is written.

    Whoever holds the password or a session can give any agent another model, larger ceilings,
    another prompt or more of the catalog's tools, never past the critical calls every session is
    refused (`policy.critical`). The trace is an `agent-setting` record in the run log, and each
    run's `row_hash` and `edited`.
    """
    body = await kernel.body(request)
    return _core(request).agents.set_agent_field(
        body.get("key"), body.get("field"), body.get("value"), cwd=str(body.get("cwd") or "")
    )


@router.post("/api/agents/state")
async def set_agent_state(request: Request) -> AgentPage:
    """`{cwd, key, on}` turns a row's event or schedule on or off in one workspace: the pref
    `agents.state`, the owner's own setting like `/api/packs`, logged as an `agent-state` row
    `by: owner`. A row with neither is a 400. On, a row may open paid read-only sessions on its
    own there, under its ceilings and the daily cap."""
    body = await kernel.body(request)
    cwd = str(body.get("cwd") or "")
    return _core(request).agents.set_state(cwd, body.get("key"), body.get("on"))


class Started(TypedDict):
    agent: str
    started: bool


@router.post("/api/agents/run")
async def run_agent(request: Request) -> Started:
    """`{cwd, key, unit?, text?}` **opens one paid, read-only session** of a row whose trigger
    says `manual` (*Run now*), in the background, `started_by: manual`. Refused before spend
    (`code`): a row with no `manual` trigger, a unit it does not read, an update under way, the
    daily cap reached, a run of it in this workspace already going. Bounded by the row's
    ceilings; its `start` and `end` are in the run log."""
    body = await kernel.body(request)
    key = str(body.get("key") or "")
    triggers.start(
        _core(request),
        key,
        str(body.get("cwd") or ""),
        str(body.get("unit") or ""),
        by="manual",
        text=str(body.get("text") or ""),
    )
    return {"agent": key, "started": True}


@router.get("/api/proposals")
async def get_proposals(request: Request) -> ProposalsView:
    """`?cwd=`: every agent's proposals in the workspace, newest first, and the rows that
    propose."""
    return await asyncio.to_thread(_core(request).agents.proposals_view, _cwd(request))


@router.post("/api/proposals/{pid}")
async def decide_proposal(pid: int, request: Request) -> proposals.Proposal:
    """`{cwd, action: accept, slug}` makes a unit from it; `{cwd, action: dismiss, reason}` puts
    it aside with 1 to 500 characters of why. Either acts for whoever holds the password, as
    `owner`; no agent holds a tool that reaches it. The scan's press, moved here."""
    body = await kernel.body(request)
    core = _core(request)
    cwd = core.ws.check(str(body.get("cwd") or ""))
    ws, data = core.ws.key(cwd), Data(core.config.data_dir)
    action = body.get("action")
    if action == "accept":

        async def create(slug: str, brief: str) -> str:
            return str((await core.answers.create_unit(cwd, slug, brief))["unit"])

        return await proposals.accept(data, ws, pid, str(body.get("slug") or ""), create)
    if action == "dismiss":
        return await proposals.dismiss(data, ws, pid, str(body.get("reason") or ""))
    raise Invalid("action must be accept or dismiss")


@router.get("/api/insights")
async def get_insights(request: Request) -> Insights:
    """How one workspace did over the last 30 days against the owner's targets: what it shipped
    at what cost and how many review rounds, its money by day and by stage, and what was spent
    again. Read only; the run log and the board held."""
    core, cwd = _core(request), _cwd(request)
    board = await core.boards.get(cwd, "held")
    return core.activity.insights(cwd, board.get("units") or [])


@router.get("/api/chat/sessions", response_model=ChatSessions)
async def get_chat_sessions(request: Request) -> Any:
    """The Claude sessions started in one workspace's folder, newest first: the app's chats and
    any begun in a terminal there, each saying whether the app may write to it."""
    return _core(request).chat.sessions_for(_cwd(request), limit=40)


@router.get("/api/chat/history", response_model=ChatHistory)
async def get_chat_history(request: Request) -> Any:
    """Every message of one session, as its transcript holds it."""
    return _core(request).chat.history(_cwd(request), request.query_params.get("session_id", ""))


@router.post("/api/chat")
async def chat(request: Request) -> Any:
    """**Opens a paid Claude session** in a workspace's folder, or continues one the app may
    resume: `{cwd, text, session_id?}`. One turn on the `chat` row's model; the tools it gets
    are the chat setting's. Streams NDJSON: `chunk` lines, `tool` lines (`name`), then `done`
    with the `session_id` and the `run`. A dropped reader ends the turn. The turn is a run of the
    `chat` agent, with its `start` and `end` in the run log. Refused while the app updates."""
    body = await kernel.body(request)
    cwd, text = str(body.get("cwd") or ""), str(body.get("text") or "")
    core = _core(request)
    core.chat.check_send(cwd, text)

    async def turn() -> AsyncIterator[tuple[str, Any]]:
        async for kind, payload in core.chat.stream(cwd, text, body.get("session_id") or None):
            yield (kind, {"name": payload}) if kind == "tool" else (kind, payload)

    return await kernel.ndjson(turn(), "the chat turn")


@router.get("/api/settings/autopilot", response_model=AutopilotSettings)
async def get_autopilot(request: Request) -> Any:
    """One workspace's autopilot switches, `max_parallel`, and the app's daily cap."""
    return _core(request).autopilot.settings(_cwd(request))


@router.post("/api/settings/autopilot")
async def set_autopilot(request: Request) -> Any:
    """`{cwd, name, value}` sets one of the four; a wrong value is a 400 and nothing is written.

    Whoever holds the password or a session can turn the autopilot on, raise the cap, or
    let it ship to `main` under this machine's `gh` login. Turning it on is refused while
    the app listens beyond loopback. The trace is a `setting` record.
    """
    body = await kernel.body(request)
    return _core(request).autopilot.set_setting(
        str(body.get("cwd", "")), body.get("name"), body.get("value")
    )


@router.post("/api/units")
async def create_unit(request: Request) -> Any:
    """Start a work unit. `brief` is the originator's own words and becomes the unit's
    `idea.md`, which the intent step reads."""
    body = await kernel.body(request)
    return await _core(request).answers.create_unit(
        str(body.get("cwd") or ""),
        str(body.get("slug") or ""),
        str(body.get("brief") or ""),
        # A unit opened from a shared idea: no brief; its link is a row of `unit_links`.
        idea=str(body.get("idea") or ""),
        depends_on=str(body.get("depends_on") or ""),
    )


@router.post("/api/ideas")
async def create_idea(request: Request) -> Any:
    """Start an idea several units share, in the store of `cwd`. Writes only into the app's own store."""
    body = await kernel.body(request)
    return _core(request).ideas.create_idea(
        str(body.get("cwd") or ""),
        str(body.get("slug") or ""),
        str(body.get("brief") or ""),
    )


@router.post("/api/units/answer")
async def answer_question(request: Request) -> Any:
    """One answer to an open question: body `{cwd, unit, artifact, question, answer, by, name?}`.

    Writes one `unit_answers` row and nothing else; no file is touched. `by` is required:
    `person` for a person's press (the Inbox, *Take it*), `delegated` for an answer given for
    them; any other value, or none, is refused. **Neither `by` nor `name` is checked**: whoever
    holds the password or a session can answer under a name and a `by` they chose, and the next
    stage reads it as a decision already made. No gate reads `by`.

    `question` may also be `"F<n>"` with `artifact` `review.md`: a finding the last
    review round confirmed needs a person. `coscc.loop next` reads its row to offer `review`
    again, and the `ship` gate counts an `[answered]` finding as closed.
    """
    body = await kernel.body(request)
    return await _core(request).answers.answer(
        str(body.get("cwd") or ""),
        str(body.get("unit") or ""),
        str(body.get("artifact") or ""),
        body.get("question"),
        str(body.get("answer") or ""),
        body.get("by"),
        str(body.get("name") or ""),
    )


@router.get("/api/decided")
async def get_decided(request: Request) -> list[Decided]:
    """Every answer in one workspace sent `by: delegated`, newest first: what Leif and the
    agents decided for the owner. Read from the board held."""
    board = await _core(request).boards.get(_cwd(request), "held")
    out: list[Decided] = [
        {
            "unit": str(u.get("name") or ""),
            "artifact": str(a.get("artifact") or ""),
            "n": int(a.get("n") or 0),
            "question": str(a.get("question") or ""),
            "text": str(a.get("text") or ""),
            "name": str(a.get("name") or ""),
            "date": str(a.get("date") or ""),
        }
        for u in board.get("units") or []
        for a in u.get("answers") or []
        if a.get("by") == "delegated"
    ]
    return sorted(out, key=lambda d: d["date"], reverse=True)


@router.post("/api/units/outcome")
async def record_outcome(request: Request) -> Any:
    """Record whether a finished unit met its intent's outcome.

    Writes one `outcome` row in `unit_decisions` and nothing else; no file is touched.
    `result` is `đạt`, `trượt` or `không đo được`. **Whoever holds the password or a session
    can record `đạt`**, under any name. No gate reads the row; the unit's history shows it.
    """
    body = await kernel.body(request)
    return await _core(request).answers.record_outcome(
        *(
            str(body.get(k) or "")
            for k in (
                "cwd",
                "unit",
                "result",
                "measured_by",
                "source",
                "reason",
                "note",
                "recorded_by",
            )
        )
    )


@router.post("/api/units/hold")
async def hold_unit(request: Request) -> Any:
    """Pause, drop or resume a unit: body `{cwd, unit, to, reason, by}`.

    Writes one `unit_holds` row and a `hold` record in the run log; the loop then offers no stage and closes every gate.
    **Whoever holds the password or a session can pause every unit**, and `to: "dropped"`
    closes the unit's open pull request **with this machine's `gh` login** and removes its
    worktree. It starts nothing, a resume included.
    """
    body = await kernel.body(request)
    return await _core(request).answers.hold(
        *(str(body.get(k) or "") for k in ("cwd", "unit", "to", "reason", "by"))
    )


@router.post("/api/units/more-rounds")
async def more_rounds(request: Request) -> Any:
    """Allow one more review round to a unit out of rounds: body `{cwd, unit, by?}`.

    Writes one `more-rounds` row in `unit_decisions`; the loop then adds one round to the
    limit and opens the `review` gate again. **Whoever holds the password
    or a session can open a paid review round**; the route starts nothing itself, but
    with the autopilot on its next sweep will.
    """
    body = await kernel.body(request)
    return await _core(request).answers.more_rounds(
        *(str(body.get(k) or "") for k in ("cwd", "unit", "by"))
    )


@router.get("/api/backlog", response_model=UpNext)
async def get_backlog(request: Request) -> Any:
    """The shortlist the autopilot works through, the other estimated units in the order their
    estimates and relations give, and the units with no estimate. Read from the board held."""
    board = await _core(request).boards.get(_cwd(request), "held")
    return {**(board.get("backlog") or {}), "max": SHORTLIST_MAX}


@router.post("/api/backlog/estimate")
async def backlog_estimate(request: Request) -> Any:
    """A person's estimate: `{cwd, unit, value, effort, basis, by}`.

    A new `estimate-value` row in the run log; no file is written and no gate reads it.
    """
    body = await kernel.body(request)
    return await _core(request).backlog.record_estimate(
        str(body.get("cwd") or ""),
        str(body.get("unit") or ""),
        body.get("value"),
        body.get("effort"),
        body.get("basis"),
        body.get("by"),
    )


@router.post("/api/backlog/relation")
async def backlog_relation(request: Request) -> Any:
    """Add or remove one relation: `{cwd, unit, other, type, op, reason, by}`. A `relation` row in the run log, nothing else."""
    body = await kernel.body(request)
    return await _core(request).backlog.record_relation(
        *(str(body.get(k) or "") for k in ("cwd", "unit", "other", "type", "op", "reason", "by"))
    )


@router.post("/api/backlog/shortlist")
async def backlog_shortlist(request: Request) -> Any:
    """The whole shortlist, in order: `{cwd, units, reason, by}`.

    A `shortlist` row in the run log; every later board step's `start` row reads it.
    Nothing runs because of it, and no gate or `next` reads it.
    """
    body = await kernel.body(request)
    return await _core(request).backlog.record_shortlist(
        str(body.get("cwd") or ""),
        body.get("units"),
        str(body.get("reason") or ""),
        str(body.get("by") or ""),
    )


@router.post("/api/backlog/propose")
async def backlog_propose(request: Request) -> Any:
    """**Opens one paid session** proposing estimates: `{cwd}`. Streams NDJSON like
    `/api/board/run`. A second press while one runs is a 400.
    """
    body = await kernel.body(request)
    return await kernel.ndjson(
        _core(request).backlog.propose_estimates(str(body.get("cwd") or "")), "the proposal"
    )


@router.post("/api/units/review-comment")
async def post_review_comment(request: Request) -> Any:
    """Post one review round to the unit's pull request, once.

    Writes to GitHub **under this machine's `gh` login**. It posts the round as it stands
    in `review.md`; the request names a unit and a round number only, so no caller can
    choose the words. A round already on the pull request comes back `already`. A failure
    is a 200 with `state: failed` and gh's reason, because the request was valid.
    """
    body = await kernel.body(request)
    return await _core(request).answers.post_review_comment(
        str(body.get("cwd") or ""), str(body.get("unit") or ""), body.get("round")
    )


@router.post("/api/units/branch")
async def start_branch(request: Request) -> Any:
    """Cut this unit's branch in the workspace.

    The only route that writes to somebody else's git; `coscc/git/gitops.py` lists what
    that may be, because this runs with the app's own authority, not a session's policy.
    """
    body = await kernel.body(request)
    return await _core(request).backlog.start_branch(
        str(body.get("cwd") or ""), str(body.get("unit") or "")
    )


@router.get("/api/units")
async def get_units(request: Request) -> Cards:
    """Every unit of one workspace as a list shows it, from the held board: a few
    kilobytes; what is running and the autopilot beside it."""
    return cards(await _core(request).board(_cwd(request), "held"))


@router.get("/api/units/next")
async def get_next(request: Request) -> NextStep:
    """The one stage the run button may offer for a unit, as `coscc.loop next` answered it:
    `{stage, action, blocked, gate}`, `gate` being what the gate says of that stage when it is
    closed. Asks `gh`, so it can wait up to 60s. It starts nothing;
    `/api/board/run` still asks the gate."""
    return await _core(request).steps.next_step(
        _cwd(request), request.query_params.get("unit", ""), with_gate=True
    )


@router.get("/api/units/{name}")
async def get_unit(name: str, request: Request) -> Detail:
    """One unit as its page shows it: its card, stages, questions and answers with who gave them,
    review rounds, and every run from the run log. Read from the board held, like `/api/units`."""
    core, cwd = _core(request), _cwd(request)
    board = await core.boards.get(cwd, "held")
    unit = next((u for u in board.get("units") or [] if u.get("name") == name), None)
    if unit is None:
        raise Invalid(f"no unit {name} in {cwd}")
    journal = core.ws.journal()
    timeline = await asyncio.to_thread(journal.timeline, core.ws.key(cwd), name) if journal else []
    meta = core.ws.unit_meta()
    outputs = await asyncio.to_thread(meta.outputs, core.ws.key(cwd), name)
    decisions = await asyncio.to_thread(meta.decisions, core.ws.key(cwd), name)
    return detail(unit, timeline, outputs, decisions)


def _number(request: Request, name: str) -> int | None:
    value = request.query_params.get(name, "")
    if not value:
        return None
    if not value.isdigit():
        raise Invalid(f"{name} must be a whole number")
    return int(value)


@router.get("/api/runs/{run}", response_model=EventsPage)
async def get_run_events(run: str, request: Request) -> Any:
    """The last `limit` events one run of the workspace recorded, any agent's, with a unit or none
    (`unit` is `""`), oldest first; `before` pages back, `seq` reads one event whole. Everything
    the run saw: commands, paths, thoughts, output."""
    limit = _number(request, "limit")
    return _core(request).watch.events_page(
        _cwd(request),
        run,
        before=_number(request, "before"),
        seq=_number(request, "seq"),
        **({"limit": limit} if limit else {}),
    )


@router.get("/api/runs/{run}/follow")
async def follow_run(run: str, request: Request) -> StreamingResponse:
    """The events of a running run past `after` as server-sent events, a batch a message, until
    its `end`; then `event: done`. `event: status` (the page) when it is not running here, `event:
    cut` (`{from}`) when this reader fell behind. Ends like `/api/stream` after
    `STREAM_LIFETIME_SECONDS` with `event: end`, and the page follows again from what it has."""
    loop = asyncio.get_running_loop()
    follow = _core(request).watch.follow_events(
        _cwd(request), run, after=_number(request, "after") or 0, gather=0.3
    )

    async def events() -> AsyncIterator[str]:
        ends = loop.time() + STREAM_LIFETIME_SECONDS
        try:
            yield "retry: 1000\n: open\n\n"
            while loop.time() < ends:
                try:
                    kind, value = await anext(follow)
                except StopAsyncIteration:
                    yield "event: done\ndata: {}\n\n"
                    return
                if kind == "events" and value:
                    yield f"data: {json.dumps(value, ensure_ascii=False, default=str)}\n\n"
                elif kind == "events":
                    yield ": ping\n\n"
                elif kind == "cut":
                    yield f"event: cut\ndata: {json.dumps({'from': value})}\n\n"
                    return
                else:
                    yield f"event: status\ndata: {json.dumps(value, default=str)}\n\n"
            yield "event: end\ndata: {}\n\n"
        finally:
            await follow.aclose()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/board/mode")
async def set_board_mode(request: Request) -> Any:
    """The only thing the board writes, and it writes it to the journal."""
    body = await kernel.body(request)
    return await _core(request).steps.set_mode(
        *(str(body.get(k, "")) for k in ("cwd", "unit", "stage", "mode"))
    )


@router.post("/api/board/run")
async def run_step(request: Request) -> Any:
    """Streams NDJSON: chunks, then one done. `raise: {usd?, turns?}` goes on with the session a
    ceiling paused, under the higher ceiling; a person's request only, never the autopilot's.

    Anything decidable before output is a status code; a refusal after streaming starts
    arrives as an `error` line.
    """
    body = await kernel.body(request)
    cwd, unit, stage = (str(body.get(k, "")) for k in ("cwd", "unit", "stage"))
    steps = _core(request).steps
    if "raise" in body:
        # A stage paused at a ceiling goes on in its own session: `raise: {usd?, turns?}`.
        return await kernel.ndjson(steps.raise_step(cwd, unit, stage, body["raise"]), "the step")
    # `rerun` only when the body says `true` itself.
    rerun = body.get("rerun") is True
    extra = {"rerun": True, "note": str(body.get("note") or "")} if rerun else {}
    return await kernel.ndjson(steps.run_step(cwd, unit, stage, **extra), "the step")


@router.post("/api/board/stop")
async def stop_step(request: Request) -> Any:
    """Stop the step running on one unit: `{cwd, unit, by}`.

    Whoever holds the password or a session can stop any step. `by` is what the `end`
    record's `stopped_by` says (nothing when the cancel lands before the first turn) and
    is a claim, not an identity. It opens no gate and starts nothing.
    """
    body = await kernel.body(request)
    return await _core(request).steps.stop_step(
        *(str(body.get(k, "")) for k in ("cwd", "unit", "by"))
    )


@router.get("/api/board/steps")
async def running_steps(request: Request) -> Any:
    """The steps and integrations of one workspace not yet ended, read from their attempts
    in `cos.db` (`state`: `queued`, `preparing`, `running` or `ending`; `stopping` once a Stop
    is recorded), `kind: "integration"` beside a step's `kind: "step"`, so whatever restarts
    the app on an empty list sees them."""
    return _core(request).steps.running_steps(_cwd(request))


@router.post("/api/units/integrate")
async def integrate_unit(request: Request) -> Any:
    """Integrate one unit onto `main`, on request. Streams like `/api/board/run`.

    Whoever holds the password or a session can make this machine's `gh` login rebase a
    unit's pull request, or open a paid Gebo session. A refusal is a 400 before anything changes.
    """
    body = await kernel.body(request)
    stream = _core(request).integration.integrate(
        str(body.get("cwd", "")), str(body.get("unit", ""))
    )
    return await kernel.ndjson(stream, "the integration")


@router.get("/api/features")
async def get_features(request: Request) -> Any:
    """Each feature's state in one workspace: `{name: "off" | "pilot" | "on"}`, `off` while its
    status forbids the others. With `detail=1` each value is the row Settings shows instead:
    `{state, pilot, sentence, locked, summary}`. A workspace the app does not have is a 400."""
    core = _core(request)
    cwd = core.ws.check(_cwd(request))
    rows = plugin.shown(request.app.state.ctxs, request.app.state.plugins, cwd)
    if request.query_params.get("detail") == "1":
        return {f.name: {k: v for k, v in asdict(f).items() if k != "name"} for f in rows}
    return {f.name: f.state for f in rows}


@router.get("/api/features/shown")
async def get_features_shown(request: Request) -> list[plugin.Shown]:
    """Each feature as Settings shows it for one workspace: its state, whether `pilot` may be
    chosen, the sentence and whether it is locked."""
    core = _core(request)
    cwd = core.ws.check(_cwd(request))
    return plugin.shown(request.app.state.ctxs, request.app.state.plugins, cwd)


@router.post("/api/features")
async def set_feature(request: Request) -> Any:
    """`{cwd, name, state}` sets one feature's state for one workspace; the older `{on: bool}`
    is read as `on` or `off`. A feature, workspace or state not known, `pilot` for a feature
    without it, or `pilot`/`on` while the feature's status forbids them is a 400. It changes the
    pref `features.state`, then tells the feature, which may start its own setup (codegraph's
    install). Whoever holds the password or a session can silence a workspace's notices."""
    body = await kernel.body(request)
    state, on = body.get("state"), body.get("on")
    if state is None and isinstance(on, bool):
        state = "on" if on else "off"
    if not isinstance(state, str):
        raise Invalid(f"state must be one of {', '.join(kernel.STATES)}")
    chosen = plugin.set_state(
        _core(request),
        request.app.state.ctxs,
        request.app.state.plugins,
        str(body.get("name") or ""),
        str(body.get("cwd") or ""),
        state,
    )
    return {"name": str(body.get("name")), "state": chosen}


@router.get("/api/packs")
async def get_packs(request: Request) -> list[PackShown]:
    """Each pack in one workspace: name, version, `on`, the default `process` a new unit walks
    and every process's states. A workspace the app does not have is a 400."""
    core = _core(request)
    key = core.ws.key(core.ws.check(_cwd(request)))
    return pack.packs_shown(Data(core.config.data_dir), key)


@router.post("/api/packs")
async def set_pack(request: Request) -> Any:
    """`{cwd, name, on?, process?}` switches a pack on or off for one workspace and/or chooses
    the process its new units walk (`<pack>/<name>`). A pack, workspace or process not known is
    a 400. It writes the prefs `packs.state` and `packs.process`, the owner's own settings, and
    no decision; off, a new unit or idea is refused `no-process` and running units still step.
    Whoever holds the password or a session can stop a workspace opening units."""
    body = await kernel.body(request)
    core = _core(request)
    key = core.ws.key(core.ws.check(str(body.get("cwd") or "")))
    on, chosen = body.get("on"), body.get("process")
    if (on is not None and not isinstance(on, bool)) or (
        chosen is not None and not isinstance(chosen, str)
    ):
        raise Invalid("on is true or false, process is <pack>/<name>")
    try:
        pack.set_packs(Data(core.config.data_dir), key, str(body.get("name") or ""), on, chosen)
    except pack.PackError as e:
        raise Invalid(str(e)) from e
    return pack.packs_shown(Data(core.config.data_dir), key)


# -- updating the app ----------------------------------------------------
#
# Whoever holds the password or a session can apply an update (which pauses every running
# session and restarts), cancel a wait or start a local build. They cannot choose what gets
# installed: a body is read for `channel` and `by` only, and a URL, path, version, ref or
# `mode` in it is never read.


async def _update_body(request: Request) -> dict[str, str]:
    body = await kernel.body(request)
    got = {k: str(body.get(k, "") or "") for k in ("channel", "by")}
    return {**got, "by": got["by"].strip()}


@router.get("/api/update", response_model=UpdateStatus)
async def get_update(request: Request) -> Any:
    """What runs, and what the panel shows; `build_id` is read here."""
    status = _core(request).updater.status()
    return {**status, **update_words(status)}


@router.post("/api/update/apply")
async def apply_update(request: Request) -> Any:
    body = await _update_body(request)
    with refusals():
        return await _core(request).updater.apply(body["channel"], body["by"] or kernel.OWNER)


@router.post("/api/update/cancel")
async def cancel_update(request: Request) -> Any:
    body = await _update_body(request)
    with refusals():
        return _core(request).updater.cancel(body["by"] or kernel.OWNER)


@router.post("/api/update/build-local")
async def build_local(request: Request) -> Any:
    body = await _update_body(request)
    with refusals():
        return _core(request).updater.build_local(body["by"] or kernel.OWNER)
