"""The HTTP surface. Loopback only (R5).

One surface serves both the browser and the proof command (R4). `spec.md` argues against
a test-only route on the grounds that it is a route nobody exercises for real, so
`scripts/verify_0002.py` drives exactly what a tab drives.

Nothing here reads the environment — `app.config` is the only reader — and no route
returns configuration or runs anything the caller names. `spec.md` C3: a long-lived login
credential is in this process, and those two habits are what keep it there.
"""

from __future__ import annotations

import json
from pathlib import Path

from aiohttp import web

from app import sessions
from app.config import Config, from_env
from app.sessions import Refused, Sessions

PUBLIC = Path(__file__).parent / "public"

# Typed keys rather than bare strings: aiohttp warns on the latter, and a typo in a
# handler should be a lookup error here and not a silent None.
CONFIG = web.AppKey("config", Config)
SESSIONS = web.AppKey("sessions", Sessions)


def _bad(message: str, status: int = 400) -> web.Response:
    return web.json_response({"error": message}, status=status)


async def get_workspaces(request: web.Request) -> web.Response:
    config: Config = request.app[CONFIG]
    return web.json_response({"workspaces": list(config.workspaces)})


async def get_sessions(request: web.Request) -> web.Response:
    """R1. Sessions of one project, and only that project."""
    config: Config = request.app[CONFIG]
    cwd = request.query.get("cwd", "")
    if not config.is_workspace(cwd):
        return _bad(f"not a configured workspace: {cwd}")
    rows = sessions.list_for_directory(cwd, limit=_limit(request))
    owned = request.app[SESSIONS]
    for row in rows:
        # The list shows terminal sessions too — the read layer sees them. This flag is
        # what tells the page which of them it may actually write to (spec.md C1).
        row["resumable"] = config.may_resume(owned.created_here(row["session_id"]))
    return web.json_response({"cwd": cwd, "sessions": rows})


async def get_history(request: web.Request) -> web.Response:
    """R6. Read back from the SDK's store, never from a copy of our own."""
    config: Config = request.app[CONFIG]
    cwd = request.query.get("cwd", "")
    session_id = request.query.get("session_id", "")
    if not config.is_workspace(cwd):
        return _bad(f"not a configured workspace: {cwd}")
    if not session_id:
        return _bad("session_id is required")
    return web.json_response(
        {"session_id": session_id, "messages": sessions.history(session_id, cwd)}
    )


async def post_send(request: web.Request) -> web.StreamResponse:
    """R2 and R3. Streams NDJSON: any number of `chunk` lines, then one `done` or `error`.

    The status line is committed before the first chunk, so a refusal that happens after
    streaming has begun arrives as an `error` line rather than an HTTP code. Callers must
    read to the last line to know whether it worked.
    """
    config: Config = request.app[CONFIG]
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return _bad("body must be JSON")

    cwd = str(body.get("cwd", ""))
    text = str(body.get("text", ""))
    session_id = body.get("session_id") or None
    if not config.is_workspace(cwd):
        return _bad(f"not a configured workspace: {cwd}")
    if not text.strip():
        return _bad("text is required")

    response = web.StreamResponse(headers={"Content-Type": "application/x-ndjson"})
    await response.prepare(request)

    async def line(obj) -> None:
        await response.write(json.dumps(obj).encode() + b"\n")

    try:
        async for kind, payload in request.app[SESSIONS].stream(cwd, text, session_id):
            if kind == "chunk":
                await line({"type": "chunk", "text": payload})
            else:
                await line({"type": "done", **payload})
    except Refused as e:
        await line({"type": "error", "error": str(e)})
    except Exception as e:  # surfaced as data; the process keeps serving
        await line({"type": "error", "error": f"{type(e).__name__}: {e}"})
    await response.write_eof()
    return response


async def get_index(request: web.Request) -> web.Response:
    return web.FileResponse(PUBLIC / "index.html")


def _limit(request: web.Request) -> int | None:
    raw = request.query.get("limit")
    if raw is None:
        return None
    try:
        return max(1, min(200, int(raw)))
    except ValueError:
        return None


async def _close_sessions(app: web.Application) -> None:
    # Every client is a CLI process. The plan calls leaking them a risk, so shutdown is
    # wired to the server's own lifecycle rather than left to whoever remembers.
    await app[SESSIONS].close_all()


def build(config: Config | None = None) -> web.Application:
    config = config or from_env()
    app = web.Application()
    app[CONFIG] = config
    app[SESSIONS] = Sessions(config)
    app.router.add_get("/", get_index)
    app.router.add_get("/api/workspaces", get_workspaces)
    app.router.add_get("/api/sessions", get_sessions)
    app.router.add_get("/api/history", get_history)
    app.router.add_post("/api/send", post_send)
    app.router.add_static("/static/", PUBLIC)
    app.on_cleanup.append(_close_sessions)
    return app


def main() -> None:
    config = from_env()
    app = build(config)
    print(f"cos-baodo 0002 on http://{config.host}:{config.port}")
    print(f"workspaces: {', '.join(config.workspaces)}")
    print(f"tools: {config.effective_tools() or 'none (chat only)'}")
    web.run_app(app, host=config.host, port=config.port, print=None)


if __name__ == "__main__":
    main()
