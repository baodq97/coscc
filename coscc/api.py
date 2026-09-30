"""The JSON surface, as a FastAPI app that stands on its own.

Reflex mounts this object via `api_transformer`; being a plain ASGI app, tests drive it
in-process with `httpx.ASGITransport` and no frontend or Node. Nothing here reads the
environment, returns configuration or runs anything the caller names, and nothing decides:
every route translates a request into a `Service` call and the result back into JSON.

The page does not come through here: its buttons reach `Service` over Reflex's socket. It only
fetches `/api/update` and the routes of the features in `coscc/features/` (notices). The rest
is for the owner's own tools, the updater's trial of a new build and `scripts/install.sh`; a
route nobody calls is not kept.

Reflex reserves `/ping/`, `/_event` and `/_upload`; the guard in `coscc/auth.py` serves
`/login`, `/setup` and `/logout`. Nothing here may use them. Every route sits behind that
guard: without a live session only `GET /api/health` gets through. One password, one user:
whoever holds it or a session cookie can call every route below. A name a body carries
(`answered_by`, `by`, `stopped_by`, `recorded_by`) is written as sent, or as `service.OWNER`
when absent; neither is an identity. Tests that build this app alone drive it without the guard.

A refusal is `Invalid` raised by `Service` and answered in one place: 400, or 503 while an
update is under way (`Updating`), or 409 when this install cannot be updated (`NotUpdatable`).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from coscc import features, plugin
from coscc.config import Config, from_env
from coscc.service import Service
from coscc.service.common import Invalid, NotUpdatable, Updating
from coscc.agent.sessions import Sessions

log = logging.getLogger(__name__)


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


@router.get("/api/workspaces")
async def get_workspaces(request: Request) -> Any:
    return _service(request).ws.all()


@router.post("/api/workspaces")
async def add_workspace(request: Request) -> Any:
    """Adopt a directory under the working folder, or clone one into it.

    The working folder comes from the environment only; a body naming one is ignored.
    """
    body = await plugin.body(request)
    return await _service(request).ws.add(
        str(body.get("name", "")),
        label=str(body.get("label", "") or ""),
        repo_url=(body.get("repo_url") or None),
    )


@router.post("/api/workspaces/{name}/pull")
async def pull_workspace(name: str, request: Request) -> Any:
    return await _service(request).ws.pull(name)


@router.get("/api/settings/models")
async def get_stage_models(request: Request) -> Any:
    """Each stage, then chat, with its agent count, its model and where that model came from."""
    return await _service(request).models.stage_models()


@router.post("/api/settings/models")
async def set_stage_model(request: Request) -> Any:
    """`{name, model}` sets one row's model; `{name}` alone removes its override.

    It decides what every step spends: whoever holds the password or a session can move
    any stage's model. The trace is a `setting` record in the run log.
    """
    body = await plugin.body(request)
    return await _service(request).models.set_stage_model(body.get("name"), body.get("model"))


@router.post("/api/settings/efforts")
async def set_stage_effort(request: Request) -> Any:
    """`{name, effort}` sets one row's effort; `{name}` alone removes its override.

    Same exposure and trace as the model route; `max` is accepted only here.
    """
    body = await plugin.body(request)
    return await _service(request).models.set_stage_effort(body.get("name"), body.get("effort"))


@router.get("/api/settings/agents")
async def get_agents(request: Request) -> Any:
    """Every agent row, each field with where it came from, and what was wrong."""
    return _service(request).agents.agent_table()


@router.post("/api/settings/agents")
async def set_agent(request: Request) -> Any:
    """`{key, name?, glyph?, meaning?, role?}` sets those fields' override, `""` removes one
    field's, and `{key}` alone removes the row's. A wrong field is a 400 and nothing is written.

    Whoever holds the password or a session can rename any agent. The trace is a `setting` record.
    """
    body = await plugin.body(request)
    return _service(request).agents.set_agent(
        body.get("key"), {k: v for k, v in body.items() if k != "key"}
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
    body = await plugin.body(request)
    return _service(request).autopilot.set_setting(
        str(body.get("cwd", "")), body.get("name"), body.get("value")
    )


@router.post("/api/units")
async def create_unit(request: Request) -> Any:
    """Start a work unit. `brief` is the originator's own words and becomes the unit's
    `idea.md`, which the intent step reads."""
    body = await plugin.body(request)
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
    body = await plugin.body(request)
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
    review round confirmed needs a person. That appends `### F<n>`; `cos.mjs next` reads
    it to offer `review` again, and the `ship` gate counts an `[answered]` finding as closed.

    An optional `delegation: "D<n>"` writes the answer as one an agent gave under a
    delegation entered on Settings (`delegated`). What it `covers` is not checked.
    """
    body = await plugin.body(request)
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
    body = await plugin.body(request)
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
    `hold` row to the run log; `cos.mjs` then offers no stage and closes every gate.
    **Whoever holds the password or a session can pause every unit**, and `to: "dropped"`
    closes the unit's open pull request **with this machine's `gh` login** and removes its
    worktree. It starts nothing, a resume included.
    """
    body = await plugin.body(request)
    return await _service(request).answers.hold(
        *(str(body.get(k) or "") for k in ("cwd", "unit", "to", "reason", "by"))
    )


@router.post("/api/units/more-rounds")
async def more_rounds(request: Request) -> Any:
    """Allow one more review round to a unit out of rounds: body `{cwd, unit, by?}`.

    Appends a `### More rounds` block under `review.md ## Answers`; `cos.mjs` then adds
    one round to the limit and opens the `review` gate again. **Whoever holds the password
    or a session can open a paid review round**; the route starts nothing itself, but
    with the autopilot on its next sweep will.
    """
    body = await plugin.body(request)
    return await _service(request).answers.more_rounds(
        *(str(body.get(k) or "") for k in ("cwd", "unit", "by"))
    )


@router.post("/api/backlog/estimate")
async def backlog_estimate(request: Request) -> Any:
    """A person's estimate: `{cwd, unit, value, effort, basis, by}`.

    A new `estimate-value` row in the run log; no file is written and no gate reads it.
    """
    body = await plugin.body(request)
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
    body = await plugin.body(request)
    return await _service(request).backlog.record_relation(
        *(str(body.get(k) or "") for k in ("cwd", "unit", "other", "type", "op", "reason", "by"))
    )


@router.post("/api/backlog/shortlist")
async def backlog_shortlist(request: Request) -> Any:
    """The whole shortlist, in order: `{cwd, units, reason, by}`.

    A `shortlist` row in the run log; every later board step's `start` row reads it.
    Nothing runs because of it, and no gate or `next` reads it.
    """
    body = await plugin.body(request)
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
    body = await plugin.body(request)
    return await plugin.ndjson(
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
    body = await plugin.body(request)
    return await _service(request).answers.post_review_comment(
        str(body.get("cwd") or ""), str(body.get("unit") or ""), body.get("round")
    )


@router.post("/api/units/branch")
async def start_branch(request: Request) -> Any:
    """Cut this unit's branch in the workspace.

    The only route that writes to somebody else's git; `coscc/git/gitops.py` lists what
    that may be, because this runs with the app's own authority, not a session's policy.
    """
    body = await plugin.body(request)
    return await _service(request).backlog.start_branch(
        str(body.get("cwd") or ""), str(body.get("unit") or "")
    )


@router.get("/api/board")
async def get_board(request: Request) -> Any:
    """Every unit of one workspace, with all eight stages on each."""
    return await _service(request).board(_cwd(request))


@router.get("/api/board/running")
async def get_board_running(request: Request) -> Any:
    """What has an agent working in one workspace now, and what ended unseen.

    Cheap enough to ask every few seconds: memory and the run log, no `git` or `gh`.
    """
    return _service(request).boards.running(_cwd(request))


@router.get("/api/units/next")
async def get_next(request: Request) -> Any:
    """The one stage the run button may offer for a unit, as `cos.mjs next` answered it:
    `{stage, action, blocked}`. Asks `gh`, so it can wait up to 60s. It starts nothing;
    `/api/board/run` still asks the gate."""
    return await _service(request).steps.next_step(
        _cwd(request), request.query_params.get("unit", "")
    )


@router.post("/api/board/mode")
async def set_board_mode(request: Request) -> Any:
    """The only thing the board writes, and it writes it to the journal."""
    body = await plugin.body(request)
    return await _service(request).steps.set_mode(
        *(str(body.get(k, "")) for k in ("cwd", "unit", "stage", "mode"))
    )


@router.post("/api/board/run")
async def run_step(request: Request) -> Any:
    """Streams NDJSON: chunks, then one done.

    Anything decidable before output is a status code; a refusal after streaming starts
    arrives as an `error` line.
    """
    body = await plugin.body(request)
    # `rerun` only when the body says `true` itself.
    rerun = body.get("rerun") is True
    extra = {"rerun": True, "note": str(body.get("note") or "")} if rerun else {}
    stream = _service(request).steps.run_step(
        str(body.get("cwd", "")), str(body.get("unit", "")), str(body.get("stage", "")), **extra
    )
    return await plugin.ndjson(stream, "the step")


@router.post("/api/board/stop")
async def stop_step(request: Request) -> Any:
    """Stop the step running on one unit: `{cwd, unit, by}`.

    Whoever holds the password or a session can stop any step. `by` is what the `end`
    record's `stopped_by` says (nothing when the cancel lands before the first turn) and
    is a claim, not an identity. It opens no gate and starts nothing.
    """
    body = await plugin.body(request)
    return await _service(request).steps.stop_step(
        *(str(body.get(k, "")) for k in ("cwd", "unit", "by"))
    )


@router.get("/api/board/steps")
async def running_steps(request: Request) -> Any:
    """The board steps running now in one workspace, as the registry a Stop reads holds
    them, and the integrations beside them, which no Stop reaches (`kind: "integration"`
    beside a step's `kind: "step"`, so whatever restarts the app on an empty list sees
    them). This process only. Not `/api/board/running`, which is the display."""
    return _service(request).steps.running_steps(_cwd(request))


@router.post("/api/units/integrate")
async def integrate_unit(request: Request) -> Any:
    """Integrate one unit onto `main`, on request. Streams like `/api/board/run`.

    Whoever holds the password or a session can make this machine's `gh` login rebase a
    unit's pull request, or open a paid Gebo session. A refusal is a 400 before anything changes.
    """
    body = await plugin.body(request)
    stream = _service(request).steps.integrate(str(body.get("cwd", "")), str(body.get("unit", "")))
    return await plugin.ndjson(stream, "the integration")


@router.post("/api/release/prepare")
async def release_prepare(request: Request) -> Any:
    """`{cwd, version}`: a `chore/release-X-Y-Z` pull request, streamed like `/api/units/integrate`.

    Whoever holds the password or a session can make this machine's `gh` login commit,
    push a branch and open a pull request. A refusal is a 400 before anything changes;
    every press leaves one `release` record."""
    body = await plugin.body(request)
    stream = _service(request).release.release_prepare(
        str(body.get("cwd", "")), str(body.get("version", ""))
    )
    return await plugin.ndjson(stream, "the release")


@router.post("/api/release/publish")
async def release_publish(request: Request) -> Any:
    """`{cwd, version}`: merge the release pull request and push `vX.Y.Z` onto its merge
    commit, which publishes the release.

    Whoever holds the password or a session can make this machine's `gh` login merge into
    `main` and push a tag no ruleset protects. A refusal is a 400 before anything changes;
    every press leaves one `release` record."""
    body = await plugin.body(request)
    stream = _service(request).release.release_publish(
        str(body.get("cwd", "")), str(body.get("version", ""))
    )
    return await plugin.ndjson(stream, "the release")


@router.get("/api/features")
async def get_features(request: Request) -> Any:
    """Each feature and whether it is on for one workspace: `{name: on}`. A workspace the app
    does not have is a 400."""
    service = _service(request)
    cwd = service.ws.check(_cwd(request))
    ctx = plugin.ctx_of(service)
    plugin.create_tables(ctx, features.FEATURES)
    return {f.name: ctx.enabled(f.name, cwd) for f in features.FEATURES}


@router.post("/api/features")
async def set_feature(request: Request) -> Any:
    """`{cwd, name, on}` turns one feature on or off for one workspace; a feature or workspace
    not known, or an `on` that is not a boolean, is a 400. It changes the pref `features.off`
    and nothing else. Whoever holds the password or a session can silence a workspace's notices."""
    body = await plugin.body(request)
    on = body.get("on")
    if not isinstance(on, bool):
        raise Invalid("on must be true or false")
    service = _service(request)
    plugin.set_enabled(
        service,
        [f.name for f in features.FEATURES],
        str(body.get("name") or ""),
        str(body.get("cwd") or ""),
        on,
    )
    return {"name": str(body.get("name")), "on": on}


# -- updating the app ----------------------------------------------------
#
# Whoever holds the password or a session can apply an update (which pauses every running
# session and restarts), cancel a wait or start a local build. They cannot choose what gets
# installed: a body is read for `channel` and `by` only, and a URL, path, version, ref or
# `mode` in it is never read.


async def _update_body(request: Request) -> dict[str, str]:
    body = await plugin.body(request)
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
    ctx = plugin.ctx_of(service)
    plugin.create_tables(ctx, features.FEATURES)
    routes = [*router.routes, *(r for f in features.FEATURES for r in f.routes(ctx))]
    api = FastAPI(title="coscc", lifespan=lifespan, routes=routes)
    api.state.config = config
    api.state.sessions = sessions
    api.state.service = service
    api.add_exception_handler(Invalid, _refused)

    # No route for `/` and no static mount: `/` has to fall through to Reflex's compiled-frontend
    # mount, and a route defined here would win over it.

    return api
