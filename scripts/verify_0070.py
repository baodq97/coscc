#!/usr/bin/env python3
"""Proof of `.cos/0070_anyone-who-reaches-the-port-can-run-anything`: one door, and it holds.

    uv run coscc-build && uv run python scripts/verify_0070.py      # in-process, the composed app
    COS_URL=http://127.0.0.1:8790 uv run python scripts/verify_0070.py --url   # a running service

**In-process** composes the real Reflex app exactly as `coscc/run.py` serves it —
`coscc.coscc.served()`, the compiled frontend mounted — on a temporary data root, reads the
setup token off stderr and sets a password through `POST /setup` the way a person does. It
then walks every route that app registers (`Route`, `WebSocketRoute`, `Mount`, recursively:
`/api/*`, `/ping`, `/_health`, `/_event`, the static mount), adds a real file under
`assets/` and a path that does not exist, and sends each one, with every method it
declares, a request carrying no cookie. It prints one line per request and then
`not refused and not exempt: N`. Three controls carry the cookie and must get in:
`/api/workspaces`, `/`, and the `/_event` websocket handshake — which then measures spec R8
on the real socket: the session is deleted and the socket must close within 5 s.

**`--url`** counts the same way against a service at `COS_URL`. The route list comes from
this checkout's `coscc.api.build()`, so run it from a checkout at the installed version. A
service with no password yet is exit 2: the intent's outcome is about an app that has one.
`POST /login` and `POST /setup` are not sent there — an empty password would count as a
failure against this address and could lock its owner out.

Exit 0 every claim held, 1 a request was not refused, 2 the environment could not answer
(no bundle, no `COS_URL`, no password on the service). No session, no quota, no network
beyond `COS_URL`.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.proof_harness import EXIT_BROKEN, EXIT_ENV, EXIT_PASS, REPO, say  # noqa: E402

PASSWORD = "verify-0070-" + "p" * 12

# What Reflex serves beside `/api`, for `--url`, where the composed app's own routes cannot
# be walked. The same list as `coscc/auth_test.py`, from `spike.md ## U1`.
REFLEX_PATHS = (
    ("GET", "/"),
    ("GET", "/_event/?EIO=4&transport=polling"),
    ("POST", "/_upload"),
    ("GET", "/ping"),
    ("GET", "/_health"),
    ("GET", "/no/such/path"),
    ("OPTIONS", "/api/board"),
)
SOCKETS = ("/_event/?EIO=4&transport=websocket", "/api/board", "/no/such/path")


class Tee(io.TextIOBase):
    """stderr as it was, and a copy — the guard's setup token is read from the copy."""

    def __init__(self, real):
        self.real = real
        self.copy = io.StringIO()

    def write(self, text):
        self.copy.write(text)
        return self.real.write(text)

    def flush(self):
        self.real.flush()


def _param(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


def _is_refusal(status: int | None, location: str | None, body: bytes) -> bool:
    if status == 401:
        if not body:
            return True
        try:
            return set(json.loads(body)) == {"error"}
        except ValueError:
            return False
    return status == 303 and location in ("/login", "/setup") and not body


# -- in-process ---------------------------------------------------------------------------


class Recorder:
    def __init__(self, inner):
        self.inner = inner
        self.count = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            self.count += 1
        await self.inner(scope, receive, send)


def _scope(kind, method, target, headers):
    path, _, query = target.partition("?")
    scope = {
        "type": kind, "asgi": {"version": "3.0"}, "http_version": "1.1",
        "scheme": "http" if kind == "http" else "ws", "path": path,
        "raw_path": path.encode(), "query_string": query.encode(), "root_path": "",
        "headers": [(b"host", b"127.0.0.1:8790")] + [
            (k.lower().encode(), v.encode()) for k, v in headers
        ],
        "client": ("10.0.0.9", 50000), "server": ("127.0.0.1", 8790),
    }
    if kind == "http":
        scope["method"] = method
    else:
        scope["subprotocols"] = []
        # What a browser's handshake carries; engine.io refuses an upgrade without them.
        scope["headers"] += [
            (b"upgrade", b"websocket"), (b"connection", b"Upgrade"),
            (b"sec-websocket-version", b"13"), (b"sec-websocket-key", b"dGhlIHNhbXBsZSBub25jZQ=="),
        ]
    return scope


async def asgi_http(app, method, target, headers=(), body=b""):
    inbox = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return inbox.pop(0) if inbox else {"type": "http.disconnect"}

    sent = []

    async def send(message):
        sent.append(message)

    await asyncio.wait_for(app(_scope("http", method, target, headers), receive, send), 30)
    start = next((m for m in sent if m["type"] == "http.response.start"), {})
    head = {k.decode().lower(): v.decode() for k, v in start.get("headers", [])}
    content = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start.get("status"), head, content


async def asgi_ws(app, target, headers=(), hang_up=True):
    """A handshake. Returns what the app sent, and the task when `hang_up` is False."""
    inbox: asyncio.Queue = asyncio.Queue()
    inbox.put_nowait({"type": "websocket.connect"})
    sent: list = []

    async def send(message):
        sent.append(message)
        if hang_up and message["type"] == "websocket.accept":
            inbox.put_nowait({"type": "websocket.disconnect", "code": 1000})

    task = asyncio.ensure_future(app(_scope("websocket", "", target, headers), inbox.get, send))
    if hang_up:
        await asyncio.wait_for(task, 30)
        return sent, None
    return sent, task


def walk(app, prefix=""):
    """Every (kind, method, target) the composed app registers, recursively."""
    from starlette.routing import Mount, Route, WebSocketRoute

    out = []
    for route in getattr(app, "routes", None) or []:
        if isinstance(route, Route):
            for method in sorted(route.methods or {"GET", "POST"}):
                out.append(("http", method, prefix + _param(route.path)))
        elif isinstance(route, WebSocketRoute):
            out.append(("websocket", "", prefix + _param(route.path)))
        elif isinstance(route, Mount):
            inner = prefix + _param(route.path)
            if getattr(route.app, "routes", None):
                out.extend(walk(route.app, inner))
            elif type(route.app).__name__.endswith("StaticFiles"):
                out.append(("static", "", inner))
            else:
                out.append(("http", "GET", inner + "/?EIO=4&transport=polling"))
                out.append(("http", "POST", inner + "/?EIO=4&transport=polling"))
                out.append(("websocket", "", inner + "/?EIO=4&transport=websocket"))
    return out


async def in_process() -> int:
    from coscc import frontend

    data_dir = tempfile.mkdtemp(prefix="verify-0070-")
    os.environ["COS_DATA_DIR"] = data_dir
    for name in ("COS_WORKING_DIR", "COS_WORKSPACES"):
        os.environ.pop(name, None)
    # As `coscc/run.py` sets them, before `coscc.coscc` is imported.
    os.environ["__REFLEX_MOUNT_FRONTEND_COMPILED_APP"] = "1"
    os.environ[frontend.WEB_WORKDIR_VAR] = str(frontend.web_dir(REPO))
    os.environ[frontend.SKIP_COMPILE_VAR] = "1"
    static = frontend.static_dir(REPO)
    if frontend.missing_compile_marker(REPO) is not None or not static.is_dir():
        print("no compiled page in this checkout — run `uv run coscc-build` first")
        return EXIT_ENV
    assets = sorted(p for p in (static / "assets").glob("*.js") if p.is_file())
    if not assets:
        print(f"no assets under {static / 'assets'} — run `uv run coscc-build` first")
        return EXIT_ENV
    print(f"temporary data root: {data_dir}")

    from coscc import auth
    from coscc.data import Data

    tee = Tee(sys.stderr)
    sys.stderr = tee
    try:
        import coscc.coscc as composed

        guard = composed.served()
    finally:
        sys.stderr = tee.real
    guard.err = tee.real
    recorder = Recorder(guard.inner)
    guard.inner = recorder
    tokens = [m.group(1) for line in tee.copy.getvalue().splitlines()
              if (m := auth.SETUP_LINE.match(line))]
    ok = say(len(tokens) == 1, "R4 a guard with no password prints one setup token", repr(tokens))
    if not tokens:
        return EXIT_BROKEN

    from urllib.parse import urlencode

    form = urlencode({"token": tokens[0], "password": PASSWORD, "password_confirm": PASSWORD})
    status, head, _ = await asgi_http(
        guard, "POST", "/setup", (("content-type", "application/x-www-form-urlencoded"),),
        form.encode(),
    )
    cookie = head.get("set-cookie", "").split(";")[0].partition("=")[2]
    ok &= say(status == 303 and bool(cookie), "R3 the password is set through POST /setup",
              f"{status} {head}")
    if not cookie:
        return EXIT_BROKEN

    requests = walk(recorder.inner)
    expanded = []
    for kind, method, target in requests:
        if kind == "static":
            expanded.append(("http", "GET", f"{target}/assets/{assets[0].name}"))
            expanded.append(("http", "GET", f"{target}/assets/no-such-file.js"))
        else:
            expanded.append((kind, method, target))
    expanded.append(("http", "GET", "/no/such/path"))
    expanded.append(("http", "OPTIONS", "/api/board"))

    leaks = 0
    for kind, method, target in expanded:
        path = target.partition("?")[0]
        exempt = kind == "http" and (method, path) in auth.EXEMPT
        before = recorder.count
        if kind == "websocket":
            sent, _ = await asgi_ws(guard, target)
            is_refused = sent == [{"type": "websocket.close", "code": 1008}]
            shown = "close 1008" if is_refused else repr(sent)[:80]
        else:
            status, head, body = await asgi_http(guard, method, target)
            is_refused = _is_refusal(status, head.get("location"), body)
            shown = f"{status} {head.get('location', '')}".strip()
        is_refused = is_refused and recorder.count == before
        leak = not is_refused and not exempt
        leaks += leak
        label = "WS" if kind == "websocket" else method
        mark = "LEAK" if leak else ("exempt" if exempt else "refused")
        print(f"  {label:<8} {target:<50} {shown:<20} {mark}")
    print(f"requests sent: {len(expanded)}")
    print(f"not refused and not exempt: {leaks}")
    ok &= say(leaks == 0, "R1 R11 every request without a session is refused, bar the exempt list")
    ok &= say(len(expanded) >= 45, "R11 the walk found the API and what Reflex mounts",
              str(len(expanded)))

    with_cookie = (("cookie", f"{auth.COOKIE}={cookie}"),)
    status, _, _ = await asgi_http(guard, "GET", "/api/workspaces", with_cookie)
    ok &= say(status == 200, "control: /api/workspaces with the cookie is 200", str(status))
    status, _, body = await asgi_http(
        guard, "GET", "/", with_cookie + (("accept", "text/html"),)
    )
    ok &= say(status == 200 and b"<html" in body.lower(), "control: / with the cookie is the page",
              str(status))

    auth.WS_RECHECK = 0.2
    sent, task = await asgi_ws(guard, "/_event/?EIO=4&transport=websocket", with_cookie,
                               hang_up=False)
    for _ in range(200):
        if sent:
            break
        await asyncio.sleep(0.01)
    accepted = bool(sent) and sent[0]["type"] == "websocket.accept"
    ok &= say(accepted, "control: the /_event handshake with the cookie is accepted",
              repr(sent[:1]))
    Data(data_dir).auth_session_delete(auth._sha(cookie))
    try:
        await asyncio.wait_for(task, 5)
        ended = True
    except asyncio.TimeoutError:
        task.cancel()
        ended = False
    closed = any(m == {"type": "websocket.close", "code": 1008} for m in sent)
    ok &= say(ended and closed, "R8 the open /_event socket closes within 5 s of its session ending",
              f"ended={ended} closed={closed}")
    return EXIT_PASS if ok else EXIT_BROKEN


# -- a running service --------------------------------------------------------------------


def _handshake(base: str, target: str) -> int:
    """A websocket handshake by hand, with no cookie; the status line's code, 0 if none.

    By hand rather than through a client library: `websockets` is not a dependency here
    (uvicorn 0.53.0 in `uv.lock` serves through `wsproto`), and only the status matters.
    """
    import socket
    from urllib.parse import urlsplit

    parts = urlsplit(base)
    host, port = parts.hostname or "127.0.0.1", parts.port or 80
    request = (
        f"GET {target} HTTP/1.1\r\nHost: {parts.netloc}\r\nUpgrade: websocket\r\n"
        "Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
    )
    try:
        with socket.create_connection((host, port), timeout=10) as conn:
            conn.sendall(request.encode())
            first = conn.recv(4096).split(b"\r\n", 1)[0].decode("latin-1")
    except OSError:
        return 0
    found = re.match(r"HTTP/1\.[01] (\d{3})", first)
    return int(found.group(1)) if found else 0


def over_url(base: str) -> int:
    import httpx

    from coscc import auth, frontend
    from coscc.api import build
    from coscc.config import Config
    from starlette.routing import Route

    base = base.rstrip("/")
    try:
        first = httpx.get(base + "/", headers={"accept": "text/html"}, timeout=10)
    except httpx.HTTPError as e:
        print(f"{base} did not answer: {e}")
        return EXIT_ENV
    if first.status_code == 303 and first.headers.get("location") == "/setup":
        print("chưa đặt master password, outcome không đo được")
        return EXIT_ENV

    with tempfile.TemporaryDirectory() as d:
        api = build(Config(data_dir=d))
        requests = []
        for route in api.routes:
            if isinstance(route, Route):
                for method in sorted(route.methods or {"GET"}):
                    requests.append((method, _param(route.path)))
    requests += list(REFLEX_PATHS)
    static = frontend.static_dir(REPO)
    assets = sorted((static / "assets").glob("*.js")) if static.is_dir() else []
    if assets:
        requests.append(("GET", f"/assets/{assets[0].name}"))

    leaks = 0
    with httpx.Client(base_url=base, follow_redirects=False, timeout=15) as client:
        for method, target in requests:
            path = target.partition("?")[0]
            if (method, path) in auth.EXEMPT:
                if method == "POST":
                    print(f"  {method:<8} {target:<50} {'not sent':<20} exempt")
                    continue
            reply = client.request(method, target)
            refused = _is_refusal(reply.status_code, reply.headers.get("location"), reply.content)
            exempt = (method, path) in auth.EXEMPT
            leak = not refused and not exempt
            leaks += leak
            mark = "LEAK" if leak else ("exempt" if exempt else "refused")
            print(f"  {method:<8} {target:<50} {reply.status_code:<20} {mark}")

    for target in SOCKETS:
        status = _handshake(base, target)
        leak = status == 101
        leaks += leak
        print(f"  {'WS':<8} {target:<50} {status:<20} {'LEAK' if leak else 'refused'}")
    print(f"requests sent: {len(requests) + len(SOCKETS)}")
    print(f"not refused and not exempt: {leaks}")
    return EXIT_PASS if leaks == 0 else EXIT_BROKEN


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", action="store_true", help="count against the service at COS_URL")
    args = parser.parse_args()
    if args.url:
        url = os.environ.get("COS_URL", "")
        if not url:
            print("--url needs COS_URL, e.g. http://127.0.0.1:8790")
            return EXIT_ENV
        return over_url(url)
    return asyncio.run(in_process())


if __name__ == "__main__":
    sys.exit(main())
