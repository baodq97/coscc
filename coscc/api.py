"""The JSON surface, as a FastAPI app that stands on its own.

Standing alone is the point. Reflex mounts this exact object via `api_transformer`
(`spec.md` R4), so the browser and the proof command reach the same routes in the same
process — but because it is also a plain ASGI app, the proof drives it in-process through
`httpx.ASGITransport` with no compiled frontend and no Node. That is what keeps `npm test`
free of a JavaScript toolchain (`plan.md` step 1, check d).

Nothing here reads the environment — `coscc.config` is the only reader — and no route
returns configuration or runs anything the caller names. `spec.md` C3: a long-lived
login credential is in this process, and those two habits are what keep it there.

Nothing here decides anything either (`spec.md` R10). Every route translates a request
into a `Service` call and a result back into JSON.

Reflex reserves `/ping/`, `/_event` and `/_upload`. Nothing here may use them.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from coscc.config import Config, from_env
from coscc.service import Invalid, Service
from coscc.sessions import Refused, Sessions


def _bad(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _limit(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return max(1, min(200, int(raw)))
    except ValueError:
        return None


def build(config: Config | None = None) -> FastAPI:
    config = config or from_env()
    sessions = Sessions(config)
    service = Service(config, sessions)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Every client is a CLI process holding a long-lived login credential
        # (`.cos/0001_no-session-management/spec.md:121`). Shutdown is wired to the server's
        # lifecycle rather than left to whoever remembers. Nothing to do on the way up.
        yield
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
        return service.workspaces()

    @api.post("/api/workspaces")
    async def add_workspace(request: Request) -> Any:
        """Adopt a directory under the working folder, or clone one into it.

        The working folder itself is never a parameter — it comes from the environment
        and nowhere else (`spec.md` R11). A body naming one is ignored, not overridden.
        """
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return _bad("body must be JSON")
        try:
            return await service.add_workspace(
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
        except (json.JSONDecodeError, ValueError):
            return _bad("body must be JSON")
        try:
            return service.set_label(name, str(body.get("label", "") or ""))
        except Invalid as e:
            return _bad(str(e))

    @api.delete("/api/workspaces/{name}")
    async def remove_workspace(name: str) -> Any:
        """Removes the entry. The directory on disk is left alone (`spec.md` R18)."""
        try:
            return service.remove_workspace(name)
        except Invalid as e:
            return _bad(str(e))

    @api.get("/api/settings/models")
    async def get_stage_models() -> Any:
        """`0004_no-setting-says-which-model-runs-a-stage`: each stage, then chat, with its
        agent count, its model and where that model came from."""
        return await service.stage_models()

    @api.post("/api/settings/models")
    async def set_stage_model(request: Request) -> Any:
        """`{name, model}` sets one row's model; `{name}` alone removes its override.

        No login, like every route here, and it decides what every step spends. The trace
        is a `setting` record in the run log.
        """
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return _bad("body must be JSON")
        if not isinstance(body, dict):
            return _bad("body must be a JSON object")
        try:
            return await service.set_stage_model(body.get("name"), body.get("model"))
        except Invalid as e:
            return _bad(str(e))

    @api.post("/api/workspaces/{name}/pull")
    async def pull_workspace(name: str) -> Any:
        try:
            return await service.pull_workspace(name)
        except Invalid as e:
            return _bad(str(e))

    @api.post("/api/units")
    async def create_unit(request: Request) -> Any:
        """`0014` R1. Start a work unit. The first route that makes something.

        `brief` is the originator's own words and becomes the unit's `idea.md`, which is
        what the intent step reads. `write-intent` invariant 1 asks for exactly that, and
        until now there was no way to give it to the app at all.
        """
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return _bad("send JSON")
        if not isinstance(body, dict):
            return _bad("send a JSON object")
        try:
            return await service.create_unit(
                str(body.get("cwd") or ""),
                str(body.get("slug") or ""),
                str(body.get("brief") or ""),
            )
        except Invalid as e:
            return _bad(str(e))

    @api.post("/api/units/answer")
    async def answer_question(request: Request) -> Any:
        """`0016` R2. A person answers one item under an artifact's `## Open questions`.

        Appends a `### Câu N` block under `## Answers` at the end of that artifact and
        writes nothing else. **No route here has a login and the default bind is
        `0.0.0.0`**, so anyone who reaches the port can put words into an artifact under a
        name they chose, and the next stage reads them as a person's decision.

        Since `0028` `question` may also be `"F<n>"` with `artifact` `review.md`: a finding
        the last review round confirmed needs a person. That appends `### F<n>`, and it does
        more than a numbered answer does — `cos.mjs next` reads it to offer `review` again,
        and the `ship` gate reads it to count an `[answered]` finding as closed.
        """
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return _bad("send JSON")
        if not isinstance(body, dict):
            return _bad("send a JSON object")
        try:
            return await service.answer(
                str(body.get("cwd") or ""),
                str(body.get("unit") or ""),
                str(body.get("artifact") or ""),
                body.get("question"),
                str(body.get("answer") or ""),
                str(body.get("answered_by") or ""),
            )
        except Invalid as e:
            return _bad(str(e))

    @api.post("/api/units/review-comment")
    async def post_review_comment(request: Request) -> Any:
        """`0021` R8, R9. Post one review round to the unit's pull request, once.

        Writes to GitHub **under this machine's `gh` login**, from a route with no login on
        a default bind of `0.0.0.0`. What it posts is the round as it stands in
        `review.md`; the request names a unit and a round number and nothing else, so no
        caller can choose the words. A round already on the pull request comes back
        `already` and is not posted twice. A failure is a 200 with `state: failed` and
        gh's reason, because the request was valid and GitHub said no.
        """
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return _bad("send JSON")
        if not isinstance(body, dict):
            return _bad("send a JSON object")
        try:
            return await service.post_review_comment(
                str(body.get("cwd") or ""),
                str(body.get("unit") or ""),
                body.get("round"),
            )
        except Invalid as e:
            return _bad(str(e))

    @api.post("/api/units/branch")
    async def start_branch(request: Request) -> Any:
        """`0014` R4. Cut this unit's branch in the workspace.

        The only route in this app that writes to somebody else's git.
        `coscc/gitops.py` carries the list of what that is allowed to be, because
        `coscc/policy.py` covers sessions and this runs with the app's own authority.
        """
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return _bad("send JSON")
        if not isinstance(body, dict):
            return _bad("send a JSON object")
        try:
            return await service.start_branch(
                str(body.get("cwd") or ""), str(body.get("unit") or "")
            )
        except Invalid as e:
            return _bad(str(e))

    @api.get("/api/branch")
    async def get_branch(request: Request) -> Any:
        """Which branch the workspace is on."""
        try:
            return await service.branch_here(request.query_params.get("cwd", ""))
        except Invalid as e:
            return _bad(str(e))

    @api.get("/api/board")
    async def get_board(request: Request) -> Any:
        """R1. Every unit of one workspace, with all eight stages on each."""
        try:
            return await service.board(request.query_params.get("cwd", ""))
        except Invalid as e:
            return _bad(str(e))

    @api.get("/api/units/next")
    async def get_next(request: Request) -> Any:
        """`0024`. The one stage the run button may offer for a unit, as `cos.mjs next`
        answered it: `{stage, action, blocked}`. Asks `gh` in the workspace, so it can wait
        up to 60s. It names a stage and starts nothing; `/api/board/run` still asks the gate."""
        q = request.query_params
        try:
            return await service.next_step(q.get("cwd", ""), q.get("unit", ""))
        except Invalid as e:
            return _bad(str(e))

    @api.post("/api/board/mode")
    async def set_board_mode(request: Request) -> Any:
        """R5. The only thing the board writes, and it writes it to the journal."""
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return _bad("body must be JSON")
        try:
            return await service.set_mode(
                str(body.get("cwd", "")),
                str(body.get("unit", "")),
                str(body.get("stage", "")),
                str(body.get("mode", "")),
            )
        except Invalid as e:
            return _bad(str(e))

    @api.post("/api/board/run")
    async def run_step(request: Request) -> Any:
        """R7. Streams NDJSON exactly as `/api/send` does — chunks, then one done.

        The same rule applies about the status line: anything decidable before output is a
        status code, and a refusal after streaming starts arrives as an `error` line.
        """
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return _bad("body must be JSON")

        cwd, unit = str(body.get("cwd", "")), str(body.get("unit", ""))
        stage = str(body.get("stage", ""))
        stream = service.run_step(cwd, unit, stage)
        try:
            # Pull the first item here so a refusal that happens before any output is still
            # a 400. An async generator does nothing until it is advanced.
            first = await stream.__anext__()
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
                yield out({"type": "error", "error": f"{type(e).__name__}: {e}"})

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    @api.get("/api/timeline")
    async def get_timeline(request: Request) -> Any:
        """R15. What happened to one unit, oldest first."""
        try:
            return service.timeline(
                request.query_params.get("cwd", ""),
                request.query_params.get("unit", ""),
            )
        except Invalid as e:
            return _bad(str(e))

    @api.get("/api/unit-history")
    async def get_unit_history(request: Request) -> Any:
        """`0013` R8. Every transition of one unit, and the projection over them.

        Read-only, like every other GET here. `spec.md` C6 names what it widens: this app
        has no authentication on any route and binds `0.0.0.0` by default, so one more
        readable route is one more thing readable from the network. Not a new hole — the
        same one, a little larger — and `0011` chose that posture on purpose.
        """
        try:
            return service.unit_history(
                request.query_params.get("cwd", ""),
                request.query_params.get("unit", ""),
            )
        except Invalid as e:
            return _bad(str(e))

    @api.get("/api/units-with-history")
    async def get_units_with_history(request: Request) -> Any:
        """Every unit the log knows, including ones no longer in the working tree."""
        try:
            return service.units_with_history(request.query_params.get("cwd", ""))
        except Invalid as e:
            return _bad(str(e))

    @api.get("/api/sessions")
    async def get_sessions(request: Request) -> Any:
        """R1. Sessions of one project, and only that project."""
        try:
            return service.sessions_for(
                request.query_params.get("cwd", ""),
                limit=_limit(request.query_params.get("limit")),
            )
        except Invalid as e:
            return _bad(str(e))

    @api.get("/api/history")
    async def get_history(request: Request) -> Any:
        """R6. Read back from the SDK's store, never from a copy of our own."""
        try:
            return service.history(
                request.query_params.get("cwd", ""),
                request.query_params.get("session_id", ""),
            )
        except Invalid as e:
            return _bad(str(e))

    @api.post("/api/send")
    async def post_send(request: Request) -> Any:
        """R2 and R3. Streams NDJSON: any number of `chunk` lines, then one `done` or `error`.

        The status line is committed before the first chunk, so a refusal that happens
        after streaming has begun arrives as an `error` line rather than an HTTP code.
        Callers must read to the last line to know whether it worked.
        """
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return _bad("body must be JSON")

        cwd = str(body.get("cwd", ""))
        text = str(body.get("text", ""))
        session_id = body.get("session_id") or None

        try:
            # Only what can be decided before any output. See the docstring above.
            service.check_send(cwd, text)
        except Invalid as e:
            return _bad(str(e))

        async def lines() -> AsyncIterator[bytes]:
            def out(obj: dict[str, Any]) -> bytes:
                return json.dumps(obj).encode() + b"\n"

            try:
                async for kind, payload in service.stream(cwd, text, session_id):
                    if kind == "chunk":
                        yield out({"type": "chunk", "text": payload})
                    else:
                        yield out({"type": "done", **payload})
            except Refused as e:
                yield out({"type": "error", "error": str(e)})
            except Exception as e:  # surfaced as data; the process keeps serving
                yield out({"type": "error", "error": f"{type(e).__name__}: {e}"})

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    # No route for `/` and no static mount. The page is built from Python components
    # (`spec.md` R8), and `/` has to fall through to Reflex's compiled-frontend mount —
    # a route defined here would win over it and the page would never render.

    return api
