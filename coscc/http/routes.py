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
below. A name a body carries (`answered_by`, `by`, `stopped_by`, `recorded_by`) is written as
sent, or as `kernel.OWNER` when absent; neither is an identity. Tests that build this app alone
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
from fastapi.responses import Response, StreamingResponse

from coscc import kernel
from coscc.bus import Event
from coscc.http import plugin
from coscc.kernel import Invalid
from coscc.leif.agents import AgentPage
from coscc.leif.answers import opens_with
from coscc.leif.chat import ChatHistory, ChatSessions
from coscc.leif.insights import Insights
from coscc.github.release import ReleaseView
from coscc.runner.steps import NextStep
from coscc.runner.watch import EventsPage
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
    """A release or local build the updater knows of: what it is and whether it may be applied."""

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


# Who answers for the owner, as the start of an answer's `by`.
AGENT_NAMES = ("Leif", "Claude", "agent")


class Decided(TypedDict):
    """An answer given for the owner: by Leif, or inferred by an agent."""

    unit: str
    artifact: str
    n: int
    question: str
    text: str
    by: str
    authority: str
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
    """Every bus event as server-sent events, `{subject, workspace, unit}`, `workspace` being
    the resolved path. It only says that something changed: the page reads what it shows again.
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
                data = {"subject": e.name, "workspace": e.workspace, "unit": e.unit}
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
    """The eight agents: who each is, what it runs on and may do, how its runs went, its chip;
    then `estimate` and `chat`, and what was wrong."""
    return _core(request).agents.agent_page()


@router.post("/api/agents/field")
async def set_agent_field(request: Request) -> Any:
    """`{key, field, value}` saves one field of one row; no `value` (or `null`) resets it to
    its default. Out of bounds is a 400 and nothing is written. No route writes a grant.

    It decides what every step spends: whoever holds the password or a session can move any
    agent's model or raise its ceilings. The trace is an `agent-setting` record in the run log.
    """
    body = await kernel.body(request)
    return _core(request).agents.set_agent_field(
        body.get("key"), body.get("field"), body.get("value")
    )


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
    with the `session_id`. A dropped reader ends the turn. The trace is a `chat` record in the
    run log. Refused while the app updates."""
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
        # A unit opened from a shared idea: no brief, one line under `## Units`.
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
    """A person answers one item under an artifact's `## Open questions`.

    Appends a `### Câu N` block under `## Answers` and writes nothing else. **The name is
    not checked**: whoever holds the password or a session can put words into an artifact
    under a name they chose, and the next stage reads them as a person's decision.

    `question` may also be `"F<n>"` with `artifact` `review.md`: a finding the last
    review round confirmed needs a person. That appends `### F<n>`; `coscc.loop next` reads
    it to offer `review` again, and the `ship` gate counts an `[answered]` finding as closed.
    """
    body = await kernel.body(request)
    return await _core(request).answers.answer(
        str(body.get("cwd") or ""),
        str(body.get("unit") or ""),
        str(body.get("artifact") or ""),
        body.get("question"),
        str(body.get("answer") or ""),
        str(body.get("answered_by") or ""),
    )


@router.get("/api/decided")
async def get_decided(request: Request) -> list[Decided]:
    """Every answer in one workspace that a person did not give, newest first: what Leif and
    the agents decided for the owner. An answer counts when its authority is not `person`, or
    when its `by` opens with an agent's name: Leif's answers through `/api/units/answer` are
    recorded as `person` with `by` naming Leif. Read from the board held."""
    board = await _core(request).boards.get(_cwd(request), "held")
    out: list[Decided] = [
        {
            "unit": str(u.get("name") or ""),
            "artifact": str(a.get("artifact") or ""),
            "n": int(a.get("n") or 0),
            "question": str(a.get("question") or ""),
            "text": str(a.get("text") or ""),
            "by": str(a.get("by") or ""),
            "authority": str(a.get("authority") or ""),
            "date": str(a.get("date") or ""),
        }
        for u in board.get("units") or []
        for a in u.get("answers") or []
        if (a.get("authority") or "person") != "person" or opens_with(a.get("by"), AGENT_NAMES)
    ]
    return sorted(out, key=lambda d: d["date"], reverse=True)


@router.post("/api/units/outcome")
async def record_outcome(request: Request) -> Any:
    """Record whether a finished unit met its intent's outcome.

    Appends a `### Outcome` block under `intent.md`'s `## Answers` and writes nothing
    else. `result` is `đạt`, `trượt` or `không đo được`. **Whoever holds the password or
    a session can record `đạt`**, under any name. No gate reads the block; the board
    shows it as the ground for keeping or dropping a unit.
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

    Appends a `### Paused|Dropped|Resumed` block under `intent.md ## Answers` and a
    `hold` row to the run log; the loop then offers no stage and closes every gate.
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

    Appends a `### More rounds` block under `review.md ## Answers`; the loop then adds
    one round to the limit and opens the `review` gate again. **Whoever holds the password
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
    `{stage, action, blocked}`. Asks `gh`, so it can wait up to 60s. It starts nothing;
    `/api/board/run` still asks the gate."""
    return await _core(request).steps.next_step(_cwd(request), request.query_params.get("unit", ""))


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
    return detail(unit, timeline)


def _number(request: Request, name: str) -> int | None:
    value = request.query_params.get(name, "")
    if not value:
        return None
    if not value.isdigit():
        raise Invalid(f"{name} must be a whole number")
    return int(value)


@router.get("/api/units/{name}/runs/{run}", response_model=EventsPage)
async def get_run_events(name: str, run: str, request: Request) -> Any:
    """The last `limit` events one run of a unit recorded, oldest first; `before` pages back,
    `seq` reads one event whole. Everything the step saw: commands, paths, thoughts, output."""
    limit = _number(request, "limit")
    return _core(request).watch.events_page(
        _cwd(request),
        name,
        run,
        before=_number(request, "before"),
        seq=_number(request, "seq"),
        **({"limit": limit} if limit else {}),
    )


@router.get("/api/units/{name}/runs/{run}/follow")
async def follow_run(name: str, run: str, request: Request) -> StreamingResponse:
    """The events of a running run past `after` as server-sent events, a batch a message, until
    its `end`; then `event: done`. `event: status` (the page) when it is not running here, `event:
    cut` (`{from}`) when this reader fell behind. Ends like `/api/stream` after
    `STREAM_LIFETIME_SECONDS` with `event: end`, and the page follows again from what it has."""
    loop = asyncio.get_running_loop()
    follow = _core(request).watch.follow_events(
        _cwd(request), name, run, after=_number(request, "after") or 0, gather=0.3
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
    """Streams NDJSON: chunks, then one done.

    Anything decidable before output is a status code; a refusal after streaming starts
    arrives as an `error` line.
    """
    body = await kernel.body(request)
    # `rerun` only when the body says `true` itself.
    rerun = body.get("rerun") is True
    extra = {"rerun": True, "note": str(body.get("note") or "")} if rerun else {}
    stream = _core(request).steps.run_step(
        str(body.get("cwd", "")), str(body.get("unit", "")), str(body.get("stage", "")), **extra
    )
    return await kernel.ndjson(stream, "the step")


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


@router.get("/api/release", response_model=ReleaseView | None)
async def get_release(request: Request) -> Any:
    """What a release of one workspace would gather and the one button it offers now; `null`
    for a workspace that is not a git checkout. Read from the board held."""
    return (await _core(request).boards.get(_cwd(request), "held")).get("release")


@router.post("/api/release/prepare")
async def release_prepare(request: Request) -> Any:
    """`{cwd, version}`: a `chore/release-X-Y-Z` pull request, streamed like `/api/units/integrate`.

    Whoever holds the password or a session can make this machine's `gh` login commit,
    push a branch and open a pull request. A refusal is a 400 before anything changes;
    every press leaves one `release` record."""
    body = await kernel.body(request)
    stream = _core(request).release.release_prepare(
        str(body.get("cwd", "")), str(body.get("version", ""))
    )
    return await kernel.ndjson(stream, "the release")


@router.post("/api/release/publish")
async def release_publish(request: Request) -> Any:
    """`{cwd, version}`: merge the release pull request and push `vX.Y.Z` onto its merge
    commit, which publishes the release.

    Whoever holds the password or a session can make this machine's `gh` login merge into
    `main` and push a tag no ruleset protects. A refusal is a 400 before anything changes;
    every press leaves one `release` record."""
    body = await kernel.body(request)
    stream = _core(request).release.release_publish(
        str(body.get("cwd", "")), str(body.get("version", ""))
    )
    return await kernel.ndjson(stream, "the release")


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


class FeaturePage(TypedDict):
    """A feature's own page, which the studio frames at `/feature/<name>`."""

    name: str
    label: str
    icon: str
    path: str


@router.get("/api/features/pages")
async def get_feature_pages(request: Request) -> list[FeaturePage]:
    return [
        {"name": name, "label": p.label, "icon": p.icon, "path": p.path}
        for name, p in request.app.state.pages.items()
    ]


@router.get("/api/features/scripts")
async def get_feature_scripts(request: Request) -> Response:
    """The page kit (`plugin.KIT_JS`) and every feature's scripts, which the studio loads once.
    A script draws into a slot (`slot-topbar`, `slot-unit`, `slot-backlog`) whose element
    carries `data-cwd` and, on a unit, `data-unit`."""
    body = "\n".join((plugin.KIT_JS, *request.app.state.scripts))
    return Response(body, media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@router.get("/api/features/shown")
async def get_features_shown(request: Request) -> list[plugin.Shown]:
    """Each feature as Settings shows it for one workspace: its state, whether `pilot` may be
    chosen, the sentence, whether it is locked, its schedule and the hours offered."""
    core = _core(request)
    cwd = core.ws.check(_cwd(request))
    return plugin.shown(request.app.state.ctxs, request.app.state.plugins, cwd)


@router.post("/api/features")
async def set_feature(request: Request) -> Any:
    """`{cwd, name, state}` sets one feature's state for one workspace; the older `{on: bool}`
    is read as `on` or `off`. A feature, workspace or state not known, `pilot` for a feature
    without it, or `pilot`/`on` while the feature's status forbids them is a 400. It changes the
    pref `features.state`, then tells the feature, which may start its own setup (codegraph's
    install). Whoever holds the password or a session can silence a workspace's notices.

    `{cwd, name, schedule}` instead sets how many hours apart a feature with a `schedule` runs
    on its own there, `0` for never: the pref `features.schedule`. A scheduled run may open a
    paid session (the `scan` feature's), so this is a spending choice."""
    body = await kernel.body(request)
    if "schedule" in body and "state" not in body and "on" not in body:
        hours = plugin.set_schedule_of(
            _core(request),
            request.app.state.plugins,
            str(body.get("name") or ""),
            str(body.get("cwd") or ""),
            body.get("schedule"),
        )
        return {"name": str(body.get("name")), "schedule": hours}
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


@router.get("/api/grants/impl")
async def get_command_lists(request: Request) -> Any:
    """`{allow, block}`: the commands `impl` gains and loses in one workspace."""
    core = _core(request)
    return core.ws.command_lists(core.ws.check(_cwd(request)))


@router.post("/api/grants/impl")
async def set_command_lists(request: Request) -> Any:
    """`{cwd, allow, block}` replaces both lists; a name that is not a command's is a 400. Whoever
    holds the password or a session can widen what `impl` runs in that workspace: `curl` or
    `ssh` there reach the network through `Bash`, outside every filter."""
    body = await kernel.body(request)
    return _core(request).ws.set_command_lists(
        str(body.get("cwd") or ""), body.get("allow"), body.get("block")
    )


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
