"""The JSON surface, as a FastAPI app that stands on its own.

Reflex mounts this object via `api_transformer`; being a plain ASGI app, tests drive it
in-process with `httpx.ASGITransport` and no frontend or Node. Nothing here reads the
environment, returns configuration or runs anything the caller names, and nothing decides:
every route translates a request into a `Service` call and the result back into JSON.

Reflex reserves `/ping/`, `/_event` and `/_upload`; the guard in `coscc/auth.py` serves
`/login`, `/setup` and `/logout`. Nothing here may use them. Every route sits behind that
guard: without a live session only `GET /api/health` gets through. One password, one user:
whoever holds it or a session cookie can call every route below. A name a body carries
(`answered_by`, `by`, `stopped_by`, `recorded_by`) is written as sent, or as `service.OWNER`
when absent; neither is an identity. Tests that build this app alone drive it without the guard.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from coscc.config import Config, from_env
from coscc.service import Service
from coscc.service.common import Invalid
from coscc.service.common import NotUpdatable
from coscc.service.common import Updating
from coscc.agent.sessions import Refused, Sessions

log = logging.getLogger(__name__)


def _bad(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _limit(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return max(1, min(200, int(raw)))
    except ValueError:
        return None


def build(config: Config | None = None) -> FastAPI:  # noqa: C901, PLR0915 - still to split
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

    api = FastAPI(title="coscc", lifespan=lifespan)
    api.state.config = config
    api.state.sessions = sessions
    api.state.service = service

    @api.get("/api/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @api.get("/api/workspaces")
    async def get_workspaces() -> Any:
        return service.ws.all()

    @api.post("/api/workspaces")
    async def add_workspace(request: Request) -> Any:
        """Adopt a directory under the working folder, or clone one into it.

        The working folder comes from the environment only; a body naming one is ignored.
        """
        try:
            body = await request.json()
        except json.JSONDecodeError, ValueError:
            return _bad("body must be JSON")
        try:
            return await service.ws.add(
                str(body.get("name", "")),
                label=str(body.get("label", "") or ""),
                repo_url=(body.get("repo_url") or None),
            )
        except Invalid as e:
            return _bad(str(e))

    @api.patch("/api/workspaces/{name}")
    async def set_label(name: str, request: Request) -> Any:
        try:
            body = await request.json()
        except json.JSONDecodeError, ValueError:
            return _bad("body must be JSON")
        try:
            return service.ws.set_label(name, str(body.get("label", "") or ""))
        except Invalid as e:
            return _bad(str(e))

    @api.delete("/api/workspaces/{name}")
    async def remove_workspace(name: str) -> Any:
        """Removes the entry. The directory on disk is left alone."""
        try:
            return service.ws.remove(name)
        except Invalid as e:
            return _bad(str(e))

    @api.get("/api/settings/models")
    async def get_stage_models() -> Any:
        """Each stage, then chat, with its agent count, its model and where that model came from."""
        return await service.models.stage_models()

    @api.post("/api/settings/models")
    async def set_stage_model(request: Request) -> Any:
        """`{name, model}` sets one row's model; `{name}` alone removes its override.

        It decides what every step spends: whoever holds the password or a session can move
        any stage's model. The trace is a `setting` record in the run log.
        """
        try:
            body = await request.json()
        except json.JSONDecodeError, ValueError:
            return _bad("body must be JSON")
        if not isinstance(body, dict):
            return _bad("body must be a JSON object")
        try:
            return await service.models.set_stage_model(body.get("name"), body.get("model"))
        except Invalid as e:
            return _bad(str(e))

    @api.post("/api/settings/efforts")
    async def set_stage_effort(request: Request) -> Any:
        """`{name, effort}` sets one row's effort; `{name}` alone removes its override.

        Same exposure and trace as the model route; `max` is accepted only here.
        """
        try:
            body = await request.json()
        except json.JSONDecodeError, ValueError:
            return _bad("body must be JSON")
        if not isinstance(body, dict):
            return _bad("body must be a JSON object")
        try:
            return await service.models.set_stage_effort(body.get("name"), body.get("effort"))
        except Invalid as e:
            return _bad(str(e))

    @api.get("/api/settings/agents")
    async def get_agents() -> Any:
        """Every agent row, each field with where it came from, and what was wrong."""
        return service.agents.agent_table()

    @api.post("/api/settings/agents")
    async def set_agent(request: Request) -> Any:
        """`{key, name?, glyph?, meaning?, role?}` sets those fields' override, `""` removes one
        field's, and `{key}` alone removes the row's. A wrong field is a 400 and nothing is written.

        Whoever holds the password or a session can rename any agent. The trace is a `setting` record.
        """
        try:
            body = await request.json()
        except json.JSONDecodeError, ValueError:
            return _bad("body must be JSON")
        if not isinstance(body, dict):
            return _bad("body must be a JSON object")
        try:
            return service.agents.set_agent(
                body.get("key"), {k: v for k, v in body.items() if k != "key"}
            )
        except Invalid as e:
            return _bad(str(e))

    @api.get("/api/settings/autopilot")
    async def get_autopilot(request: Request) -> Any:
        """One workspace's autopilot switches, `max_parallel`, and the app's daily cap."""
        try:
            return service.autopilot.settings(request.query_params.get("cwd", ""))
        except Invalid as e:
            return _bad(str(e))

    @api.post("/api/settings/autopilot")
    async def set_autopilot(request: Request) -> Any:
        """`{cwd, name, value}` sets one of the four; a wrong value is a 400 and nothing is written.

        Whoever holds the password or a session can turn the autopilot on, raise the cap, or
        let it ship to `main` under this machine's `gh` login. Turning it on is refused while
        the app listens beyond loopback. The trace is a `setting` record.
        """
        try:
            body = await request.json()
        except json.JSONDecodeError, ValueError:
            return _bad("body must be JSON")
        if not isinstance(body, dict):
            return _bad("body must be a JSON object")
        try:
            return service.autopilot.set_setting(
                str(body.get("cwd", "")), body.get("name"), body.get("value")
            )
        except Invalid as e:
            return _bad(str(e))

    @api.post("/api/workspaces/{name}/pull")
    async def pull_workspace(name: str) -> Any:
        try:
            return await service.ws.pull(name)
        except Invalid as e:
            return _bad(str(e))

    @api.post("/api/units")
    async def create_unit(request: Request) -> Any:
        """Start a work unit. `brief` is the originator's own words and becomes the unit's
        `idea.md`, which the intent step reads."""
        try:
            body = await request.json()
        except json.JSONDecodeError, ValueError:
            return _bad("send JSON")
        if not isinstance(body, dict):
            return _bad("send a JSON object")
        try:
            return await service.answers.create_unit(
                str(body.get("cwd") or ""),
                str(body.get("slug") or ""),
                str(body.get("brief") or ""),
                # A unit opened from a shared idea: no brief, one line under `## Units`.
                idea=str(body.get("idea") or ""),
                depends_on=str(body.get("depends_on") or ""),
            )
        except Invalid as e:
            return _bad(str(e))

    @api.post("/api/ideas")
    async def create_idea(request: Request) -> Any:
        """Start an idea several units share, in the store of `cwd`. Writes only into the app's own store."""
        try:
            body = await request.json()
        except json.JSONDecodeError, ValueError:
            return _bad("send JSON")
        if not isinstance(body, dict):
            return _bad("send a JSON object")
        try:
            return service.ideas.create_idea(
                str(body.get("cwd") or ""),
                str(body.get("slug") or ""),
                str(body.get("brief") or ""),
            )
        except Invalid as e:
            return _bad(str(e))

    @api.post("/api/units/answer")
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
        try:
            body = await request.json()
        except json.JSONDecodeError, ValueError:
            return _bad("send JSON")
        if not isinstance(body, dict):
            return _bad("send a JSON object")
        try:
            return await service.answers.answer(
                str(body.get("cwd") or ""),
                str(body.get("unit") or ""),
                str(body.get("artifact") or ""),
                body.get("question"),
                str(body.get("answer") or ""),
                str(body.get("answered_by") or ""),
                str(body.get("delegation") or ""),
            )
        except Invalid as e:
            return _bad(str(e))

    @api.post("/api/units/outcome")
    async def record_outcome(request: Request) -> Any:
        """Record whether a finished unit met its intent's outcome.

        Appends a `### Outcome` block under `intent.md`'s `## Answers` and writes nothing
        else. `result` is `đạt`, `trượt` or `không đo được`. **Whoever holds the password or
        a session can record `đạt`**, under any name. No gate reads the block; the board
        shows it as the ground for keeping or dropping a unit.
        """
        try:
            body = await request.json()
        except json.JSONDecodeError, ValueError:
            return _bad("send JSON")
        if not isinstance(body, dict):
            return _bad("send a JSON object")
        try:
            return await service.answers.record_outcome(
                str(body.get("cwd") or ""),
                str(body.get("unit") or ""),
                str(body.get("result") or ""),
                str(body.get("measured_by") or ""),
                str(body.get("source") or ""),
                str(body.get("reason") or ""),
                str(body.get("note") or ""),
                str(body.get("recorded_by") or ""),
            )
        except Invalid as e:
            return _bad(str(e))

    @api.post("/api/units/hold")
    async def hold_unit(request: Request) -> Any:
        """Pause, drop or resume a unit: body `{cwd, unit, to, reason, by}`.

        Appends a `### Paused|Dropped|Resumed` block under `intent.md ## Answers` and a
        `hold` row to the run log; `cos.mjs` then offers no stage and closes every gate.
        **Whoever holds the password or a session can pause every unit**, and `to: "dropped"`
        closes the unit's open pull request **with this machine's `gh` login** and removes its
        worktree. It starts nothing, a resume included.
        """
        try:
            body = await request.json()
        except json.JSONDecodeError, ValueError:
            return _bad("send JSON")
        if not isinstance(body, dict):
            return _bad("send a JSON object")
        try:
            return await service.answers.hold(
                str(body.get("cwd") or ""),
                str(body.get("unit") or ""),
                str(body.get("to") or ""),
                str(body.get("reason") or ""),
                str(body.get("by") or ""),
            )
        except Invalid as e:
            return _bad(str(e))

    @api.post("/api/units/more-rounds")
    async def more_rounds(request: Request) -> Any:
        """Allow one more review round to a unit out of rounds: body `{cwd, unit, by?}`.

        Appends a `### More rounds` block under `review.md ## Answers`; `cos.mjs` then adds
        one round to the limit and opens the `review` gate again. **Whoever holds the password
        or a session can open a paid review round**; the route starts nothing itself, but
        with the autopilot on its next sweep will.
        """
        try:
            body = await request.json()
        except json.JSONDecodeError, ValueError:
            return _bad("send JSON")
        if not isinstance(body, dict):
            return _bad("send a JSON object")
        try:
            return await service.answers.more_rounds(
                str(body.get("cwd") or ""),
                str(body.get("unit") or ""),
                str(body.get("by") or ""),
            )
        except Invalid as e:
            return _bad(str(e))

    async def _object(request: Request) -> dict[str, Any] | JSONResponse:
        try:
            body = await request.json()
        except json.JSONDecodeError, ValueError:
            return _bad("send JSON")
        return body if isinstance(body, dict) else _bad("send a JSON object")

    @api.post("/api/backlog/estimate")
    async def backlog_estimate(request: Request) -> Any:
        """A person's estimate: `{cwd, unit, value, effort, basis, by}`.

        A new `estimate-value` row in the run log; no file is written and no gate reads it.
        """
        body = await _object(request)
        if isinstance(body, JSONResponse):
            return body
        try:
            return await service.backlog.record_estimate(
                str(body.get("cwd") or ""),
                str(body.get("unit") or ""),
                body.get("value"),
                body.get("effort"),
                body.get("basis"),
                body.get("by"),
            )
        except Invalid as e:
            return _bad(str(e))

    @api.post("/api/backlog/relation")
    async def backlog_relation(request: Request) -> Any:
        """Add or remove one relation: `{cwd, unit, other, type, op, reason, by}`. A `relation` row in the run log, nothing else."""
        body = await _object(request)
        if isinstance(body, JSONResponse):
            return body
        try:
            return await service.backlog.record_relation(
                *(
                    str(body.get(k) or "")
                    for k in ("cwd", "unit", "other", "type", "op", "reason", "by")
                )
            )
        except Invalid as e:
            return _bad(str(e))

    @api.post("/api/backlog/shortlist")
    async def backlog_shortlist(request: Request) -> Any:
        """The whole shortlist, in order: `{cwd, units, reason, by}`.

        A `shortlist` row in the run log; every later board step's `start` row reads it.
        Nothing runs because of it, and no gate or `next` reads it.
        """
        body = await _object(request)
        if isinstance(body, JSONResponse):
            return body
        try:
            return await service.backlog.record_shortlist(
                str(body.get("cwd") or ""),
                body.get("units"),
                str(body.get("reason") or ""),
                str(body.get("by") or ""),
            )
        except Invalid as e:
            return _bad(str(e))

    @api.post("/api/backlog/propose")
    async def backlog_propose(request: Request) -> Any:
        """**Opens one paid session** proposing estimates: `{cwd}`. Streams NDJSON like
        `/api/board/run`. A second press while one runs is a 400.
        """
        body = await _object(request)
        if isinstance(body, JSONResponse):
            return body
        stream = service.backlog.propose_estimates(str(body.get("cwd") or ""))
        try:
            first = await stream.__anext__()
        except Updating as e:
            return _bad(str(e), 503)
        except Invalid as e:
            return _bad(str(e))
        except StopAsyncIteration:
            return _bad("the proposal produced nothing")

        async def lines() -> AsyncIterator[bytes]:
            def out(obj: dict[str, Any]) -> bytes:
                return json.dumps(obj).encode() + b"\n"

            try:
                for kind, payload in (first,):
                    yield out({"type": kind, **({"text": payload} if kind == "chunk" else payload)})
                async for kind, payload in stream:
                    if kind == "chunk":
                        yield out({"type": "chunk", "text": payload})
                    else:
                        yield out({"type": "done", **payload})
            except Exception as e:
                log.exception("the backlog proposal stream failed")
                yield out({"type": "error", "error": f"{type(e).__name__}: {e}"})

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    @api.post("/api/units/review-comment")
    async def post_review_comment(request: Request) -> Any:
        """Post one review round to the unit's pull request, once.

        Writes to GitHub **under this machine's `gh` login**. It posts the round as it stands
        in `review.md`; the request names a unit and a round number only, so no caller can
        choose the words. A round already on the pull request comes back `already`. A failure
        is a 200 with `state: failed` and gh's reason, because the request was valid.
        """
        try:
            body = await request.json()
        except json.JSONDecodeError, ValueError:
            return _bad("send JSON")
        if not isinstance(body, dict):
            return _bad("send a JSON object")
        try:
            return await service.answers.post_review_comment(
                str(body.get("cwd") or ""),
                str(body.get("unit") or ""),
                body.get("round"),
            )
        except Invalid as e:
            return _bad(str(e))

    @api.post("/api/units/branch")
    async def start_branch(request: Request) -> Any:
        """Cut this unit's branch in the workspace.

        The only route that writes to somebody else's git; `coscc/git/gitops.py` lists what
        that may be, because this runs with the app's own authority, not a session's policy.
        """
        try:
            body = await request.json()
        except json.JSONDecodeError, ValueError:
            return _bad("send JSON")
        if not isinstance(body, dict):
            return _bad("send a JSON object")
        try:
            return await service.backlog.start_branch(
                str(body.get("cwd") or ""), str(body.get("unit") or "")
            )
        except Invalid as e:
            return _bad(str(e))

    @api.get("/api/branch")
    async def get_branch(request: Request) -> Any:
        """Which branch the workspace is on."""
        try:
            return await service.backlog.branch_here(request.query_params.get("cwd", ""))
        except Invalid as e:
            return _bad(str(e))

    @api.get("/api/board")
    async def get_board(request: Request) -> Any:
        """Every unit of one workspace, with all eight stages on each."""
        try:
            return await service.board(request.query_params.get("cwd", ""))
        except Invalid as e:
            return _bad(str(e))

    @api.get("/api/board/running")
    async def get_board_running(request: Request) -> Any:
        """What has an agent working in one workspace now, and what ended unseen.

        Cheap enough to ask every few seconds: memory and the run log, no `git` or `gh`.
        """
        try:
            return service.boards.running(request.query_params.get("cwd", ""))
        except Invalid as e:
            return _bad(str(e))

    @api.get("/api/units/next")
    async def get_next(request: Request) -> Any:
        """The one stage the run button may offer for a unit, as `cos.mjs next` answered it:
        `{stage, action, blocked}`. Asks `gh`, so it can wait up to 60s. It starts nothing;
        `/api/board/run` still asks the gate."""
        q = request.query_params
        try:
            return await service.steps.next_step(q.get("cwd", ""), q.get("unit", ""))
        except Invalid as e:
            return _bad(str(e))

    @api.post("/api/board/mode")
    async def set_board_mode(request: Request) -> Any:
        """The only thing the board writes, and it writes it to the journal."""
        try:
            body = await request.json()
        except json.JSONDecodeError, ValueError:
            return _bad("body must be JSON")
        try:
            return await service.steps.set_mode(
                str(body.get("cwd", "")),
                str(body.get("unit", "")),
                str(body.get("stage", "")),
                str(body.get("mode", "")),
            )
        except Invalid as e:
            return _bad(str(e))

    @api.post("/api/board/run")
    async def run_step(request: Request) -> Any:
        """Streams NDJSON exactly as `/api/send` does: chunks, then one done.

        Anything decidable before output is a status code; a refusal after streaming starts
        arrives as an `error` line.
        """
        try:
            body = await request.json()
        except json.JSONDecodeError, ValueError:
            return _bad("body must be JSON")

        cwd, unit = str(body.get("cwd", "")), str(body.get("unit", ""))
        stage = str(body.get("stage", ""))
        # `rerun` only when the body says `true` itself.
        rerun = body.get("rerun") is True
        extra = {"rerun": True, "note": str(body.get("note") or "")} if rerun else {}
        stream = service.steps.run_step(cwd, unit, stage, **extra)
        try:
            # Pull the first item here so a refusal before any output is still a 400.
            first = await stream.__anext__()
        except Updating as e:
            return _bad(str(e), 503)
        except Invalid as e:
            return _bad(str(e))
        except StopAsyncIteration:
            return _bad("the step produced nothing")

        async def lines() -> AsyncIterator[bytes]:
            def out(obj: dict[str, Any]) -> bytes:
                return json.dumps(obj).encode() + b"\n"

            try:
                for kind, payload in (first,):
                    yield out({"type": kind, **({"text": payload} if kind == "chunk" else payload)})
                async for kind, payload in stream:
                    if kind == "chunk":
                        yield out({"type": "chunk", "text": payload})
                    else:
                        yield out({"type": "done", **payload})
            except Exception as e:
                log.exception("the step stream failed")
                yield out({"type": "error", "error": f"{type(e).__name__}: {e}"})

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    @api.post("/api/board/stop")
    async def stop_step(request: Request) -> Any:
        """Stop the step running on one unit: `{cwd, unit, by}`.

        Whoever holds the password or a session can stop any step. `by` is what the `end`
        record's `stopped_by` says (nothing when the cancel lands before the first turn) and
        is a claim, not an identity. It opens no gate and starts nothing.
        """
        try:
            body = await request.json()
        except json.JSONDecodeError, ValueError:
            return _bad("body must be JSON")
        try:
            return await service.steps.stop_step(
                str(body.get("cwd", "")), str(body.get("unit", "")), str(body.get("by", ""))
            )
        except Invalid as e:
            return _bad(str(e))

    @api.get("/api/board/steps")
    async def running_steps(request: Request) -> Any:
        """The board steps running now in one workspace, as the registry a Stop reads holds
        them, and the integrations beside them, which no Stop reaches (`kind: "integration"`
        beside a step's `kind: "step"`, so whatever restarts the app on an empty list sees
        them). This process only. Not `/api/board/running`, which is the display."""
        try:
            return service.steps.running_steps(request.query_params.get("cwd", ""))
        except Invalid as e:
            return _bad(str(e))

    def _ints(request: Request, *names: str) -> dict[str, int | None] | None:
        """The named query parameters as integers, absent as `None`; `None` if one is not."""
        out: dict[str, int | None] = {}
        for name in names:
            raw = request.query_params.get(name)
            if raw is None or raw == "":
                out[name] = None
                continue
            try:
                out[name] = int(raw)
            except ValueError:
                return None
        return out

    @api.get("/api/board/events")
    async def step_events(request: Request) -> Any:
        """One page of a step's events: `cwd`, `unit`, `run`, and `before` and `limit`, or
        `seq` for one event whole. Reads only.

        Whoever holds the password or a session reads everything the step saw (commands,
        paths, thinking, tool output) unfiltered."""
        q = request.query_params
        nums = _ints(request, "before", "limit", "seq")
        if nums is None:
            return _bad("before, limit and seq must be whole numbers")
        try:
            return service.watch.events_page(
                q.get("cwd", ""),
                q.get("unit", ""),
                q.get("run", ""),
                before=nums["before"],
                **({"limit": nums["limit"]} if nums["limit"] is not None else {}),
                seq=nums["seq"],
            )
        except Invalid as e:
            return _bad(str(e))

    @api.get("/api/board/events/follow")
    async def follow_step_events(request: Request) -> Any:
        """NDJSON: one `event` line per event past `after`, until the step's `end`; a `cut` line
        (`from`) when this reader fell too far behind; one `status` line for a step not running
        in this process. A refusal is a 400, as `/api/board/run`'s is."""
        q = request.query_params
        nums = _ints(request, "after")
        if nums is None:
            return _bad("after must be a whole number")
        stream = service.watch.follow_events(
            q.get("cwd", ""), q.get("unit", ""), q.get("run", ""), nums["after"] or 0
        )
        try:
            first = await stream.__anext__()
        except Invalid as e:
            return _bad(str(e))
        except StopAsyncIteration:
            return _bad("nothing to follow")

        async def lines() -> AsyncIterator[bytes]:
            def out(kind: str, payload: Any) -> list[bytes]:
                if kind == "events":
                    return [json.dumps({"type": "event", **e}).encode() + b"\n" for e in payload]
                if kind == "cut":
                    return [json.dumps({"type": "cut", "from": payload}).encode() + b"\n"]
                return [json.dumps({"type": "status", **payload}).encode() + b"\n"]

            try:
                for line in out(*first):
                    yield line
                async for kind, payload in stream:
                    for line in out(kind, payload):
                        yield line
            finally:
                await stream.aclose()

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    @api.get("/api/notices/follow")
    async def follow_notices_route(request: Request) -> Any:
        """NDJSON: a `head` line when there is no `after`, a `notice` line per run-log record
        past it that is one, and a `beat` line after `notices.BEAT_SECONDS` without one.
        `workspace` narrows to one. Reads only. It ends after `notices.LIFETIME_SECONDS`, so a
        listener comes back through the login door.

        Holds a connection per listener (`.claude/docs/coscc-notices.md`). A refusal is a 400
        before the stream starts; the first line is not waited for, since with `after` it may
        be a `beat` 15 s away."""
        nums = _ints(request, "after")
        if nums is None or (nums["after"] is not None and nums["after"] < 0):
            return _bad("after must be a whole number")
        try:
            scope = service.notices.notice_scope(request.query_params.get("workspace", ""))
        except Invalid as e:
            return _bad(str(e))
        stream = service.notices.follow_notices(scope, nums["after"])

        async def lines() -> AsyncIterator[bytes]:
            try:
                async for line in stream:
                    yield json.dumps(line).encode() + b"\n"
            finally:
                await stream.aclose()

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    @api.post("/api/units/integrate")
    async def integrate_unit(request: Request) -> Any:
        """Integrate one unit onto `main`, on request. Streams like `/api/board/run`.

        Whoever holds the password or a session can make this machine's `gh` login rebase a
        unit's pull request, or open a paid Gebo session. A refusal is a 400 before anything changes.
        """
        try:
            body = await request.json()
        except json.JSONDecodeError, ValueError:
            return _bad("body must be JSON")
        stream = service.steps.integrate(str(body.get("cwd", "")), str(body.get("unit", "")))
        try:
            first = await stream.__anext__()
        except Updating as e:
            return _bad(str(e), 503)
        except Invalid as e:
            return _bad(str(e))
        except StopAsyncIteration:
            return _bad("the integration produced nothing")

        async def lines() -> AsyncIterator[bytes]:
            def out(obj: dict[str, Any]) -> bytes:
                return json.dumps(obj).encode() + b"\n"

            try:
                for kind, payload in (first,):
                    yield out({"type": kind, **({"text": payload} if kind == "chunk" else payload)})
                async for kind, payload in stream:
                    if kind == "chunk":
                        yield out({"type": "chunk", "text": payload})
                    else:
                        yield out({"type": "done", **payload})
            except Exception as e:
                log.exception("the integration stream failed")
                yield out({"type": "error", "error": f"{type(e).__name__}: {e}"})

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    async def _release_route(request: Request, phase: str) -> Any:
        try:
            body = await request.json()
        except json.JSONDecodeError, ValueError:
            return _bad("body must be JSON")
        if not isinstance(body, dict):
            return _bad("body must be a JSON object")
        run = (
            service.release.release_prepare
            if phase == "prepare"
            else service.release.release_publish
        )
        stream = run(str(body.get("cwd", "")), str(body.get("version", "")))
        try:
            first = await stream.__anext__()
        except Updating as e:
            return _bad(str(e), 503)
        except Invalid as e:
            return _bad(str(e))
        except StopAsyncIteration:
            return _bad("the release produced nothing")

        async def lines() -> AsyncIterator[bytes]:
            def out(obj: dict[str, Any]) -> bytes:
                return json.dumps(obj).encode() + b"\n"

            try:
                for kind, payload in (first,):
                    yield out({"type": kind, **({"text": payload} if kind == "chunk" else payload)})
                async for kind, payload in stream:
                    yield out({"type": kind, **({"text": payload} if kind == "chunk" else payload)})
            except Exception as e:
                log.exception("the release stream failed")
                yield out({"type": "error", "error": f"{type(e).__name__}: {e}"})

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    @api.post("/api/release/prepare")
    async def release_prepare(request: Request) -> Any:
        """`{cwd, version}`: a `chore/release-X-Y-Z` pull request, streamed like `/api/units/integrate`.

        Whoever holds the password or a session can make this machine's `gh` login commit,
        push a branch and open a pull request. A refusal is a 400 before anything changes;
        every press leaves one `release` record."""
        return await _release_route(request, "prepare")

    @api.post("/api/release/publish")
    async def release_publish(request: Request) -> Any:
        """`{cwd, version}`: merge the release pull request and push `vX.Y.Z` onto its merge
        commit, which publishes the release.

        Whoever holds the password or a session can make this machine's `gh` login merge into
        `main` and push a tag no ruleset protects. A refusal is a 400 before anything changes;
        every press leaves one `release` record."""
        return await _release_route(request, "publish")

    @api.get("/api/timeline")
    async def get_timeline(request: Request) -> Any:
        """What happened to one unit, oldest first."""
        try:
            return service.backlog.timeline(
                request.query_params.get("cwd", ""),
                request.query_params.get("unit", ""),
            )
        except Invalid as e:
            return _bad(str(e))

    @api.get("/api/unit-history")
    async def get_unit_history(request: Request) -> Any:
        """Every transition of one unit, and the projection over them. Read-only."""
        try:
            return service.backlog.unit_history(
                request.query_params.get("cwd", ""),
                request.query_params.get("unit", ""),
            )
        except Invalid as e:
            return _bad(str(e))

    @api.get("/api/units-with-history")
    async def get_units_with_history(request: Request) -> Any:
        """Every unit the log knows, including ones no longer in the working tree."""
        try:
            return service.backlog.units_with_history(request.query_params.get("cwd", ""))
        except Invalid as e:
            return _bad(str(e))

    @api.get("/api/sessions")
    async def get_sessions(request: Request) -> Any:
        """Sessions of one project, and only that project."""
        try:
            return service.chat.sessions_for(
                request.query_params.get("cwd", ""),
                limit=_limit(request.query_params.get("limit")),
            )
        except Invalid as e:
            return _bad(str(e))

    @api.get("/api/history")
    async def get_history(request: Request) -> Any:
        """Read back from the SDK's store, never from a copy of our own."""
        try:
            return service.chat.history(
                request.query_params.get("cwd", ""),
                request.query_params.get("session_id", ""),
            )
        except Invalid as e:
            return _bad(str(e))

    @api.post("/api/send")
    async def post_send(request: Request) -> Any:
        """Streams NDJSON: any number of `chunk` lines, then one `done` or `error`.

        The status line is committed before the first chunk, so a refusal after streaming has
        begun arrives as an `error` line. Callers must read to the last line.
        """
        try:
            body = await request.json()
        except json.JSONDecodeError, ValueError:
            return _bad("body must be JSON")

        cwd = str(body.get("cwd", ""))
        text = str(body.get("text", ""))
        session_id = body.get("session_id") or None

        try:
            # Only what can be decided before any output.
            service.chat.check_send(cwd, text)
        except Updating as e:
            return _bad(str(e), 503)
        except Invalid as e:
            return _bad(str(e))

        async def lines() -> AsyncIterator[bytes]:
            def out(obj: dict[str, Any]) -> bytes:
                return json.dumps(obj).encode() + b"\n"

            try:
                async for kind, payload in service.chat.stream(cwd, text, session_id):
                    if kind == "chunk":
                        yield out({"type": "chunk", "text": payload})
                    else:
                        yield out({"type": "done", **payload})
            except Refused as e:
                yield out({"type": "error", "error": str(e)})
            except Exception as e:
                # Surfaced as data; the process keeps serving.
                log.exception("the chat stream failed")
                yield out({"type": "error", "error": f"{type(e).__name__}: {e}"})

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    # -- updating the app ----------------------------------------------------
    #
    # Whoever holds the password or a session can apply an update (which pauses every running
    # session and restarts), cancel a wait or start a local build. They cannot choose what gets
    # installed: a body is read for `channel` and `by` only, and a URL, path, version, ref or
    # `mode` in it is never read.

    def _refused(e: Invalid) -> JSONResponse:
        if isinstance(e, NotUpdatable):
            return _bad(str(e), 409)
        if isinstance(e, Updating):
            return _bad(str(e), 503)
        return _bad(str(e))

    async def _update_body(request: Request) -> dict[str, str] | None:
        try:
            body = await request.json()
        except json.JSONDecodeError, ValueError:
            return None
        if not isinstance(body, dict):
            return None
        return {k: str(body.get(k, "") or "") for k in ("channel", "by")}

    @api.get("/api/update")
    async def get_update() -> Any:
        """What runs, and what the panel shows; `build_id` is read here."""
        return service.update_status()

    @api.post("/api/update/apply")
    async def apply_update(request: Request) -> Any:
        body = await _update_body(request)
        if body is None:
            return _bad("body must be a JSON object")
        try:
            return await service.update_apply(body["channel"], body["by"])
        except Invalid as e:
            return _refused(e)

    @api.post("/api/update/cancel")
    async def cancel_update(request: Request) -> Any:
        body = await _update_body(request)
        if body is None:
            return _bad("body must be a JSON object")
        try:
            return service.update_cancel(body["by"])
        except Invalid as e:
            return _refused(e)

    @api.post("/api/update/build-local")
    async def build_local(request: Request) -> Any:
        body = await _update_body(request)
        if body is None:
            return _bad("body must be a JSON object")
        try:
            return service.update_build_local(body["by"])
        except Invalid as e:
            return _refused(e)

    # No route for `/` and no static mount: `/` has to fall through to Reflex's compiled-frontend
    # mount, and a route defined here would win over it.

    return api
