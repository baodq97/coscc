"""The JSON surface, as a FastAPI app that stands on its own.

Standing alone is the point. Reflex mounts this exact object via `api_transformer`
(`spec.md` R4), so the browser and the proof command reach the same routes in the same
process — but because it is also a plain ASGI app, the proof drives it in-process through
`httpx.ASGITransport` with no compiled frontend and no Node. That is what keeps `npm test`
free of a JavaScript toolchain (`plan.md` step 1, check d).

Nothing here reads the environment — `cos_baodo.config` is the only reader — and no route
returns configuration or runs anything the caller names. `0002` spec.md C3: a long-lived
login credential is in this process, and those two habits are what keep it there.

Nothing here decides anything either (`spec.md` R10). Every route translates a request
into a `Service` call and a result back into JSON.

Reflex reserves `/ping/`, `/_event` and `/_upload`. Nothing here may use them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from cos_baodo.config import Config, from_env
from cos_baodo.service import Invalid, Service
from cos_baodo.sessions import Refused, Sessions

PUBLIC = Path(__file__).parent / "public"


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

    api = FastAPI(title="cos-baodo")
    api.state.config = config
    api.state.sessions = sessions
    api.state.service = service

    @api.get("/api/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @api.get("/api/workspaces")
    async def get_workspaces() -> Any:
        return service.workspaces()

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

    # Step 9 removes this route: once the page is built from Python components, `/` has to
    # fall through to Reflex's compiled-frontend mount, and a route defined here would win
    # over it. Until then it serves `0002`'s page so this step changes nothing.
    @api.get("/")
    async def get_index() -> Any:
        return FileResponse(PUBLIC / "index.html")

    api.mount("/static", StaticFiles(directory=PUBLIC), name="static")

    @api.on_event("shutdown")
    async def _close() -> None:
        # Every client is a CLI process. Shutdown is wired to the server's lifecycle
        # rather than left to whoever remembers.
        await sessions.close_all()

    return api
