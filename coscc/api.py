"""The JSON surface, as a FastAPI app that stands on its own.

Reflex mounts this object via `api_transformer`; being a plain ASGI app, tests drive it
in-process with `httpx.ASGITransport` and no frontend or Node. Nothing here reads the
environment, returns configuration or runs anything the caller names, and nothing decides:
every route translates a request into a `Service` call and the result back into JSON.

The studio (`/next`, `ui/`) reads and acts only through here, and hears changes on
`/api/stream`. The Reflex page's buttons still reach `Service` over its socket until it is
removed (`docs/architecture/target.md`). The owner's own tools, the updater's trial of a new
build and `scripts/install.sh` use these routes too; a route nobody calls is not kept.

Reflex reserves `/ping/`, `/_event` and `/_upload`; the guard in `coscc/auth.py` serves
`/login`, `/setup` and `/logout`. Nothing here may use them. Every route sits behind that
guard: without a live session only `GET /api/health` gets through. One password, one user:
whoever holds it or a session cookie can call every route below. A name a body carries
(`answered_by`, `by`, `stopped_by`, `recorded_by`) is written as sent, or as `kernel.OWNER`
when absent; neither is an identity. Tests that build this app alone drive it without the guard.

A refusal is `Invalid` raised by `Service` and answered in one place: 400, or 503 while an
update is under way (`Updating`), or 409 when this install cannot be updated (`NotUpdatable`).
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any, AsyncIterator

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from coscc import kernel
from coscc import features, plugin, studio
from coscc.config import Config, from_env
from coscc.service import Service
from coscc.service.common import NotUpdatable, Updating
from coscc.kernel import Invalid
from coscc.agent.sessions import Sessions
from coscc.bus import Event
from coscc.service.agents import AgentPage
from coscc.service.board import Cards, Detail, cards, detail
from coscc.service.steps import NextStep
from coscc.service.workspaces import WorkspaceList

log = logging.getLogger(__name__)

# A comment line this often keeps a quiet stream open through proxies and tells the page it is
# still connected. Chosen, not measured.
STREAM_PING_SECONDS = 20.0


async def _refused(_: Request, e: Exception) -> JSONResponse:
    status = 503 if isinstance(e, Updating) else 409 if isinstance(e, NotUpdatable) else 400
    return JSONResponse({"error": str(e)}, status_code=status)


def _service(request: Request) -> Service:
    return request.app.state.service


def _cwd(request: Request) -> str:
    return request.query_params.get("cwd", "")


router = APIRouter()


@router.get("/api/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@router.get("/api/stream")
async def stream(request: Request) -> StreamingResponse:
    """Every bus event as server-sent events, `{subject, workspace, unit}`, `workspace` being
    the resolved path. It only says that something changed: the page reads what it shows again."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Event] = asyncio.Queue()

    def heard(e: Event) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, e)

    stop = _service(request).bus.watch(heard)

    async def events() -> AsyncIterator[str]:
        try:
            yield ": open\n\n"
            while True:
                try:
                    e = await asyncio.wait_for(queue.get(), STREAM_PING_SECONDS)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                data = {"subject": e.name, "workspace": e.workspace, "unit": e.unit}
                yield f"data: {json.dumps(data)}\n\n"
        finally:
            stop()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/workspaces")
async def get_workspaces(request: Request) -> WorkspaceList:
    return _service(request).ws.all()


@router.post("/api/workspaces")
async def add_workspace(request: Request) -> Any:
    """Adopt a directory under the working folder, or clone one into it.

    The working folder comes from the environment only; a body naming one is ignored.
    """
    body = await kernel.body(request)
    return await _service(request).ws.add(
        str(body.get("name", "")),
        label=str(body.get("label", "") or ""),
        repo_url=(body.get("repo_url") or None),
    )


@router.post("/api/workspaces/{name}/pull")
async def pull_workspace(name: str, request: Request) -> Any:
    return await _service(request).ws.pull(name)


@router.get("/api/agents")
async def get_agents(request: Request) -> AgentPage:
    """The eight agents: who each is, what it runs on and may do, how its runs went, its chip;
    then `estimate` and `chat`, and what was wrong."""
    return _service(request).agents.agent_page()


@router.post("/api/agents/field")
async def set_agent_field(request: Request) -> Any:
    """`{key, field, value}` saves one field of one row; no `value` (or `null`) resets it to
    its default. Out of bounds is a 400 and nothing is written. No route writes a grant.

    It decides what every step spends: whoever holds the password or a session can move any
    agent's model or raise its ceilings. The trace is an `agent-setting` record in the run log.
    """
    body = await kernel.body(request)
    return _service(request).agents.set_agent_field(
        body.get("key"), body.get("field"), body.get("value")
    )


@router.get("/api/settings/autopilot")
async def get_autopilot(request: Request) -> Any:
    """One workspace's autopilot switches, `max_parallel`, and the app's daily cap."""
    return _service(request).autopilot.settings(_cwd(request))


@router.post("/api/settings/autopilot")
async def set_autopilot(request: Request) -> Any:
    """`{cwd, name, value}` sets one of the four; a wrong value is a 400 and nothing is written.

    Whoever holds the password or a session can turn the autopilot on, raise the cap, or
    let it ship to `main` under this machine's `gh` login. Turning it on is refused while
    the app listens beyond loopback. The trace is a `setting` record.
    """
    body = await kernel.body(request)
    return _service(request).autopilot.set_setting(
        str(body.get("cwd", "")), body.get("name"), body.get("value")
    )


@router.post("/api/units")
async def create_unit(request: Request) -> Any:
    """Start a work unit. `brief` is the originator's own words and becomes the unit's
    `idea.md`, which the intent step reads."""
    body = await kernel.body(request)
    return await _service(request).answers.create_unit(
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
    return _service(request).ideas.create_idea(
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

    An optional `delegation: "D<n>"` writes the answer as one an agent gave under a
    delegation entered on Settings (`delegated`). What it `covers` is not checked.
    """
    body = await kernel.body(request)
    return await _service(request).answers.answer(
        str(body.get("cwd") or ""),
        str(body.get("unit") or ""),
        str(body.get("artifact") or ""),
        body.get("question"),
        str(body.get("answer") or ""),
        str(body.get("answered_by") or ""),
        str(body.get("delegation") or ""),
    )


@router.post("/api/units/outcome")
async def record_outcome(request: Request) -> Any:
    """Record whether a finished unit met its intent's outcome.

    Appends a `### Outcome` block under `intent.md`'s `## Answers` and writes nothing
    else. `result` is `đạt`, `trượt` or `không đo được`. **Whoever holds the password or
    a session can record `đạt`**, under any name. No gate reads the block; the board
    shows it as the ground for keeping or dropping a unit.
    """
    body = await kernel.body(request)
    return await _service(request).answers.record_outcome(
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
    return await _service(request).answers.hold(
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
    return await _service(request).answers.more_rounds(
        *(str(body.get(k) or "") for k in ("cwd", "unit", "by"))
    )


@router.post("/api/backlog/estimate")
async def backlog_estimate(request: Request) -> Any:
    """A person's estimate: `{cwd, unit, value, effort, basis, by}`.

    A new `estimate-value` row in the run log; no file is written and no gate reads it.
    """
    body = await kernel.body(request)
    return await _service(request).backlog.record_estimate(
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
    return await _service(request).backlog.record_relation(
        *(str(body.get(k) or "") for k in ("cwd", "unit", "other", "type", "op", "reason", "by"))
    )


@router.post("/api/backlog/shortlist")
async def backlog_shortlist(request: Request) -> Any:
    """The whole shortlist, in order: `{cwd, units, reason, by}`.

    A `shortlist` row in the run log; every later board step's `start` row reads it.
    Nothing runs because of it, and no gate or `next` reads it.
    """
    body = await kernel.body(request)
    return await _service(request).backlog.record_shortlist(
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
        _service(request).backlog.propose_estimates(str(body.get("cwd") or "")), "the proposal"
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
    return await _service(request).answers.post_review_comment(
        str(body.get("cwd") or ""), str(body.get("unit") or ""), body.get("round")
    )


@router.post("/api/units/branch")
async def start_branch(request: Request) -> Any:
    """Cut this unit's branch in the workspace.

    The only route that writes to somebody else's git; `coscc/git/gitops.py` lists what
    that may be, because this runs with the app's own authority, not a session's policy.
    """
    body = await kernel.body(request)
    return await _service(request).backlog.start_branch(
        str(body.get("cwd") or ""), str(body.get("unit") or "")
    )


@router.get("/api/board")
async def get_board(request: Request) -> Any:
    """Every unit of one workspace, with all eight stages on each.

    Answers the board held from the last read, at once, and starts the next read in the
    background; `read_at` says when the answer was read. Only a workspace never read waits.
    `?fresh=1` waits for a read begun now, which asks `gh` anew."""
    fresh = request.query_params.get("fresh") == "1"
    return await _service(request).board(_cwd(request), "new" if fresh else "held")


@router.get("/api/units")
async def get_units(request: Request) -> Cards:
    """Every unit of one workspace as a list shows it, read as `/api/board` reads it but a few
    kilobytes instead of megabytes; what is running and the autopilot beside it."""
    return cards(await _service(request).board(_cwd(request), "held"))


@router.get("/api/board/running")
async def get_board_running(request: Request) -> Any:
    """What has an agent working in one workspace now, and what ended unseen.

    Cheap enough to ask every few seconds: memory and the run log, no `git` or `gh`.
    """
    return _service(request).boards.running(_cwd(request))


@router.get("/api/units/next")
async def get_next(request: Request) -> NextStep:
    """The one stage the run button may offer for a unit, as `coscc.loop next` answered it:
    `{stage, action, blocked}`. Asks `gh`, so it can wait up to 60s. It starts nothing;
    `/api/board/run` still asks the gate."""
    return await _service(request).steps.next_step(
        _cwd(request), request.query_params.get("unit", "")
    )


@router.get("/api/units/{name}")
async def get_unit(name: str, request: Request) -> Detail:
    """One unit as its page shows it: its card, stages, questions and answers with who gave them,
    review rounds, and every run from the run log. Read from the board held, like `/api/units`."""
    service, cwd = _service(request), _cwd(request)
    board = await service.board(cwd, "held")
    unit = next((u for u in board.get("units") or [] if u.get("name") == name), None)
    if unit is None:
        raise Invalid(f"no unit {name} in {cwd}")
    journal = service.ws.journal()
    timeline = (
        await asyncio.to_thread(journal.timeline, service.ws.key(cwd), name) if journal else []
    )
    return detail(unit, timeline)


@router.post("/api/board/mode")
async def set_board_mode(request: Request) -> Any:
    """The only thing the board writes, and it writes it to the journal."""
    body = await kernel.body(request)
    return await _service(request).steps.set_mode(
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
    stream = _service(request).steps.run_step(
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
    return await _service(request).steps.stop_step(
        *(str(body.get(k, "")) for k in ("cwd", "unit", "by"))
    )


@router.get("/api/board/steps")
async def running_steps(request: Request) -> Any:
    """The steps and integrations of one workspace not yet ended, read from their attempts
    in `cos.db` (`state`: `queued`, `preparing`, `running` or `ending`; `stopping` once a Stop
    is recorded), `kind: "integration"` beside a step's `kind: "step"`, so whatever restarts
    the app on an empty list sees them. Not `/api/board/running`, which is the display."""
    return _service(request).steps.running_steps(_cwd(request))


@router.post("/api/units/integrate")
async def integrate_unit(request: Request) -> Any:
    """Integrate one unit onto `main`, on request. Streams like `/api/board/run`.

    Whoever holds the password or a session can make this machine's `gh` login rebase a
    unit's pull request, or open a paid Gebo session. A refusal is a 400 before anything changes.
    """
    body = await kernel.body(request)
    stream = _service(request).steps.integrate(str(body.get("cwd", "")), str(body.get("unit", "")))
    return await kernel.ndjson(stream, "the integration")


@router.post("/api/release/prepare")
async def release_prepare(request: Request) -> Any:
    """`{cwd, version}`: a `chore/release-X-Y-Z` pull request, streamed like `/api/units/integrate`.

    Whoever holds the password or a session can make this machine's `gh` login commit,
    push a branch and open a pull request. A refusal is a 400 before anything changes;
    every press leaves one `release` record."""
    body = await kernel.body(request)
    stream = _service(request).release.release_prepare(
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
    stream = _service(request).release.release_publish(
        str(body.get("cwd", "")), str(body.get("version", ""))
    )
    return await kernel.ndjson(stream, "the release")


@router.get("/api/features")
async def get_features(request: Request) -> Any:
    """Each feature's state in one workspace: `{name: "off" | "pilot" | "on"}`, `off` while its
    status forbids the others. With `detail=1` each value is the row Settings shows instead:
    `{state, pilot, sentence, locked, summary}`. A workspace the app does not have is a 400."""
    service = _service(request)
    cwd = service.ws.check(_cwd(request))
    rows = plugin.shown(request.app.state.ctx, features.FEATURES, cwd)
    if request.query_params.get("detail") == "1":
        return {f.name: {k: v for k, v in asdict(f).items() if k != "name"} for f in rows}
    return {f.name: f.state for f in rows}


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
            _service(request),
            features.FEATURES,
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
        _service(request),
        request.app.state.ctx,
        features.FEATURES,
        str(body.get("name") or ""),
        str(body.get("cwd") or ""),
        state,
    )
    return {"name": str(body.get("name")), "state": chosen}


@router.get("/api/grants/impl")
async def get_command_lists(request: Request) -> Any:
    """`{allow, block}`: the commands `impl` gains and loses in one workspace."""
    service = _service(request)
    return service.ws.command_lists(service.ws.check(_cwd(request)))


@router.post("/api/grants/impl")
async def set_command_lists(request: Request) -> Any:
    """`{cwd, allow, block}` replaces both lists; a name that is not a command's is a 400. Whoever
    holds the password or a session can widen what `impl` runs in that workspace: `curl` or
    `ssh` there reach the network through `Bash`, outside every filter."""
    body = await kernel.body(request)
    return _service(request).ws.set_command_lists(
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
    return {k: str(body.get(k, "") or "") for k in ("channel", "by")}


@router.get("/api/update")
async def get_update(request: Request) -> Any:
    """What runs, and what the panel shows; `build_id` is read here."""
    return _service(request).update_status()


@router.post("/api/update/apply")
async def apply_update(request: Request) -> Any:
    body = await _update_body(request)
    return await _service(request).update_apply(body["channel"], body["by"])


@router.post("/api/update/cancel")
async def cancel_update(request: Request) -> Any:
    return _service(request).update_cancel((await _update_body(request))["by"])


@router.post("/api/update/build-local")
async def build_local(request: Request) -> Any:
    return _service(request).update_build_local((await _update_body(request))["by"])


def build(config: Config | None = None) -> FastAPI:
    config = config or from_env()
    sessions = Sessions(config)
    service = Service(config, sessions)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Every client is a CLI process holding a long-lived login credential, so shutdown is
        # wired to the server's lifecycle. Nothing to do on the way up.
        yield
        # Steps first: each is a task that would otherwise write its `end` after its client
        # was closed. `shutdown` writes none, on purpose.
        await service.shutdown()
        await sessions.close_all()

    # The routes themselves, not `include_router`, which keeps them behind one entry of `routes`.
    # Read now, so a test can patch `features.FEATURES`.
    ctx = plugin.ctx_of(service, features.FEATURES)
    plugin.add_sessions(service, features.FEATURES)
    service.steps.hooks = plugin.hooks_of(features.FEATURES, ctx)
    # Checked now, created when the app starts (`coscc.py`): building the page imports this
    # module in processes that may not open the database.
    tables = plugin.tables_of(features.FEATURES)
    routes = [
        *router.routes,
        *studio.router.routes,
        *(r for f in features.FEATURES for r in f.routes(ctx)),
    ]
    api = FastAPI(title="coscc", lifespan=lifespan, routes=routes)
    api.state.config = config
    api.state.sessions = sessions
    api.state.service = service
    api.state.tables = tables
    # The names for Settings and the pages for `/feature`: only this module and the page shell
    # import `features`.
    api.state.features = tuple(f.name for f in features.FEATURES)
    api.state.pages = {f.name: f.page for f in features.FEATURES if f.page}
    # The one `Ctx` and the plugins themselves, for the Settings panel's states.
    api.state.ctx = ctx
    api.state.plugins = features.FEATURES
    api.add_exception_handler(Invalid, _refused)

    # No route for `/` and no static mount: `/` has to fall through to Reflex's compiled-frontend
    # mount, and a route defined here would win over it. The new studio is under `/next`
    # (`coscc/studio.py`) until it replaces that page.

    return api


def _ts(schema: dict[str, Any]) -> str:
    """One OpenAPI schema as a TypeScript type."""
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[1]
    if "anyOf" in schema:
        return " | ".join(_ts(s) for s in schema["anyOf"])
    if "enum" in schema:
        return " | ".join(json.dumps(v) for v in schema["enum"])
    if "const" in schema:
        return json.dumps(schema["const"])
    kind = schema.get("type")
    if kind == "array":
        item = _ts(schema.get("items") or {})
        return f"({item})[]" if "|" in item else f"{item}[]"
    if kind == "object":
        if "properties" not in schema:
            extra = schema.get("additionalProperties")
            return f"Record<string, {_ts(extra) if isinstance(extra, dict) else 'unknown'}>"
        need = set(schema.get("required") or ())
        fields = (
            f"  {json.dumps(k)}{'' if k in need else '?'}: {_ts(v)};"
            for k, v in schema["properties"].items()
        )
        return "{\n" + "\n".join(fields) + "\n}"
    return {
        "string": "string",
        "integer": "number",
        "number": "number",
        "boolean": "boolean",
        "null": "null",
    }.get(str(kind), "unknown")


def typescript() -> str:
    """The routes' shapes as TypeScript, which `ui/src/api.gen.ts` holds: each schema a type,
    and `Get` the answer of each `GET` route that names one. `tests/test_api_types.py` fails
    when the file is stale; `uv run python -m coscc.api > ui/src/api.gen.ts` writes it."""
    import tempfile

    with tempfile.TemporaryDirectory() as data:
        schema = build(Config(workspaces=(), data_dir=data)).openapi()
    out = [
        "// Made by `uv run python -m coscc.api > ui/src/api.gen.ts` from the app's routes. Do not edit.",
        "",
    ]
    for name, s in sorted(schema.get("components", {}).get("schemas", {}).items()):
        if name not in ("HTTPValidationError", "ValidationError"):
            out += [f"export type {name} = {_ts(s)};", ""]
    gets = []
    for path, ops in sorted(schema["paths"].items()):
        ok = ops.get("get", {}).get("responses", {}).get("200", {})
        answer = ok.get("content", {}).get("application/json", {}).get("schema", {})
        if "$ref" in answer:
            gets.append(f"  {json.dumps(path)}: {_ts(answer)};")
    out += ["export type Get = {", *gets, "};", ""]
    return "\n".join(out)


if __name__ == "__main__":
    print(typescript(), end="")
