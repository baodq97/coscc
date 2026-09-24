"""The one door: every request this app serves is decided here first.

`.cos/0070_anyone-who-reaches-the-port-can-run-anything`. Until that unit no route had a
login and the default bind was `0.0.0.0`, so anyone who reached the port could do what the
person at the board could. Now one master password stands in front, and this module is
the whole of it.

**Where it sits is the property.** `coscc.coscc.served` wraps what `rx.App` returns, and
uvicorn serves that wrapper — the `outer` position `spike.md ## U1` measured to see every
scope: `/api/*`, the page, its static files, `/_event` over polling and websocket,
`/_upload`, `/ping`, CORS preflight and paths that do not exist. The `api_transformer`
position lets Reflex's CORS answer `OPTIONS` without this ever seeing it.

**Default is refusal.** The guard knows nothing about which routes exist. It knows the
closed list in `EXEMPT` (spec R2) and refuses everything else that carries no live
session, so a route added later is behind the door without anybody remembering to put it
there. Exempt paths are compared by equality, never by prefix, and `lifespan` is the only
scope type handed straight through.

**What it is not.** One password, one user: every name typed into `answered_by`, `by`,
`stopped_by` or `recorded_by` still is only a word, typed by whoever holds the password or
a live session cookie. On plain HTTP the password, the cookie and the setup token cross
the network readable (spec C8); the banner and the login page say so.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import secrets
import sys
import time
from dataclasses import dataclass, field
from html import escape
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import argon2
from argon2.exceptions import InvalidHashError, VerificationError

from coscc.data import Data
from coscc.run import LOOPBACK

COOKIE = "coscc_session"

HEALTH = "/api/health"
LOGIN = "/login"
SETUP = "/setup"
LOGOUT = "/logout"

# spec R2, and the only place the list is written. `/api/health` stays open because
# `scripts/install.sh` probes it after installing; `/login` because without it nobody gets
# in. Compared as (method, path) by equality: `/api/health/`, `POST /api/health` and
# `/login/x` are not on it.
EXEMPT = frozenset({
    ("GET", HEALTH),
    ("HEAD", HEALTH),
    ("GET", LOGIN),
    ("POST", LOGIN),
})
# Exempt **only while no password is stored**. Once one is, these are refused like any
# other route, and with a session they do nothing: there is no way to set or change the
# password over the web (`intent.md ## Answers, câu 3`).
SETUP_EXEMPT = frozenset({("GET", SETUP), ("POST", SETUP)})

# 30 days from last use, `intent.md ## Answers, câu 4`.
SESSION_TTL = 30 * 86400
# Chosen (spec R6): push the expiry forward at most once an hour, so a board asking every
# 5 s is not a write every 5 s.
TOUCH_EVERY = 3600
# `spec.md ## Answers, câu 12`.
MIN_PASSWORD = 12
# Chosen. A login or setup form is three short fields; anything bigger is not one.
MAX_FORM = 4096

# Chosen, for spec C4. One argon2 verify with the library defaults peaks at 64.1 MiB and
# takes about 67 ms (`spike.md ## U2`), and memory grows linearly with how many run at
# once: 8 was 512.5 MiB. Two at once caps it near 128 MiB.
HASH_CONCURRENCY = 2
# Chosen. A request that waits longer than this for a slot gets 429, is not hashed and is
# not counted as a failure.
HASH_WAIT = 5.0

# Chosen, under the 60 s spec R8 allows: how often an open websocket's session is asked
# about again. Read at each sleep, so a test can shorten it.
WS_RECHECK = 30.0

# `intent.md ## Answers, câu 5` says "for example 5 a minute, then a growing wait"; the
# numbers are chosen (spec R9). 5 failures inside a sliding 60 s lock the address for 60 s;
# the first failure after a lock ends doubles it, up to an hour.
FAIL_LIMIT = 5
FAIL_WINDOW = 60.0
LOCK_FIRST = 60.0
LOCK_MAX = 3600.0
# Chosen. Past this many addresses, rows with no lock and no failure in the window go.
LIMITER_KEYS = 10000

# The line the setup token is written on. `coscc/updater.py` reads a trial's output for it.
SETUP_LINE = re.compile(r"^coscc setup token: (\S+)$")

_JSON = [(b"content-type", b"application/json")]
_HTML = [(b"content-type", b"text/html; charset=utf-8"), (b"cache-control", b"no-store")]


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _header(scope: dict, name: bytes) -> str | None:
    for key, value in scope.get("headers") or ():
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def _cookie(scope: dict) -> str:
    raw = _header(scope, b"cookie") or ""
    for part in raw.split(";"):
        key, _, value = part.strip().partition("=")
        if key == COOKIE:
            return value.strip()
    return ""


def _peer(scope: dict) -> str:
    client = scope.get("client")
    return str(client[0]) if client else ""


def is_https(scope: dict) -> bool:
    """Whether the browser reached us over TLS.

    uvicorn runs with `proxy_headers=False` (`coscc/run.py`, spec answer 14), so a proxy's
    `X-Forwarded-Proto` is read here, and only from a loopback peer — a proxy on the same
    machine. Any process on this machine can set it too (`spike.md ## U3`); all it buys is
    `Secure` on its own cookie. `X-Forwarded-For` is never read.
    """
    if scope.get("scheme") in ("https", "wss"):
        return True
    if _peer(scope) in LOOPBACK:
        proto = (_header(scope, b"x-forwarded-proto") or "").split(",")[0].strip().lower()
        return proto == "https"
    return False


def _host_port(scheme: str, netloc: str) -> str:
    netloc = netloc.strip().lower()
    default = ":443" if scheme in ("https", "wss") else ":80"
    return netloc[: -len(default)] if netloc.endswith(default) else netloc


def _origin_ok(scope: dict) -> bool:
    """`spec.md ## Answers, câu 13`: a state-changing request from another origin is refused.

    Only host and port are compared, not scheme: behind a TLS proxy the scope is always
    `http` while the browser's `Origin` says `https`. A request with no `Origin` passes —
    clients that are not browsers send none, and they have no browser cookie to borrow.
    `Origin: null` does not.
    """
    origin = _header(scope, b"origin")
    if origin is None:
        return True
    host = _header(scope, b"host")
    parts = urlsplit(origin.strip())
    if not host or not parts.netloc:
        return False
    return _host_port(parts.scheme, parts.netloc) == _host_port(parts.scheme, host)


def _hostname(scope: dict) -> str:
    host = (_header(scope, b"host") or "").strip().lower()
    if host.startswith("["):
        return host[1 : host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


async def _respond(send, status: int, headers: list, body: bytes = b"") -> None:
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": headers + [(b"content-length", str(len(body)).encode())],
    })
    await send({"type": "http.response.body", "body": body})


async def _json(send, status: int, error: str, extra: list | None = None) -> None:
    await _respond(send, status, _JSON + (extra or []), json.dumps({"error": error}).encode())


async def _redirect(send, location: str, extra: list | None = None) -> None:
    await _respond(send, 303, [(b"location", location.encode())] + (extra or []))


@dataclass
class _Row:
    fails: list[float] = field(default_factory=list)
    locked_until: float = 0.0
    lock_len: float = 0.0


class Limiter:
    """spec R9: failed logins and setups, per client address, in memory only (spec C5).

    The address is the peer uvicorn saw. Behind a proxy every client shares the proxy's,
    so a stranger's five wrong tries lock the owner out too — accepted in
    `spec.md ## Answers, câu 14`.
    """

    def __init__(self, clock: Callable[[], float]):
        self._clock = clock
        self._rows: dict[str, _Row] = {}

    def wait(self, key: str) -> float:
        """Seconds until this address may try again; 0 when it may now."""
        row = self._rows.get(key)
        if row is None:
            return 0.0
        return max(0.0, row.locked_until - self._clock())

    def fail(self, key: str) -> None:
        now = self._clock()
        row = self._rows.setdefault(key, _Row())
        if row.lock_len and now >= row.locked_until:
            # The first failure after a lock ended: twice as long, at once.
            row.lock_len = min(row.lock_len * 2, LOCK_MAX)
            row.locked_until = now + row.lock_len
            row.fails = []
            return
        row.fails = [t for t in row.fails if now - t < FAIL_WINDOW] + [now]
        if len(row.fails) >= FAIL_LIMIT:
            row.lock_len = LOCK_FIRST
            row.locked_until = now + LOCK_FIRST
            row.fails = []
        if len(self._rows) > LIMITER_KEYS:
            self._sweep(now)

    def clear(self, key: str) -> None:
        self._rows.pop(key, None)

    def _sweep(self, now: float) -> None:
        for key in [
            k for k, r in self._rows.items()
            if r.locked_until <= now and not any(now - t < FAIL_WINDOW for t in r.fails)
        ]:
            del self._rows[key]


_BUSY = object()


class Guard:
    """The ASGI app uvicorn serves. Decides, then hands the request in or does not."""

    def __init__(
        self,
        inner,
        data: Data,
        *,
        clock: Callable[[], float] = time.time,
        err=None,
        hasher: argon2.PasswordHasher | None = None,
    ):
        self.inner = inner
        self.data = data
        self.clock = clock
        self.err = err if err is not None else sys.stderr
        # Library defaults: argon2id, m=65536, t=3, p=4 (`spike.md ## U2`).
        self.hasher = hasher or argon2.PasswordHasher()
        self.limiter = Limiter(clock)
        self._slots = asyncio.Semaphore(HASH_CONCURRENCY)
        self._token: str | None = None
        # spec R4: every start with no password gets a fresh token.
        if data.auth_password_hash() is None:
            self._issue_token()

    # -- the setup token ------------------------------------------------------

    def _issue_token(self) -> None:
        """Mint a token and write it, once, to stderr, flushed at once.

        Flushed because `spike.md ## U4` measured it: a flushed stderr line reached
        `journalctl --user -u <unit>` after 0.05 s, an unflushed stdout line not in 10 s.
        Nothing else holds the token — no file, no database row.
        """
        self._token = secrets.token_urlsafe(16)
        self.err.write(f"coscc setup token: {self._token}\n")
        self.err.write(
            f"  open {SETUP} on this address and paste the token there to set the master "
            "password\n"
        )
        self.err.flush()

    def _saw(self, has_password: bool) -> None:
        """Keep the token in step with the database (spec R10: no restart needed)."""
        if has_password:
            self._token = None
        elif self._token is None:
            self._issue_token()

    # -- sessions ---------------------------------------------------------------

    async def _state(self, sha: str):
        return await asyncio.to_thread(self.data.auth_state, sha)

    def _live(self, has_password: bool, row, now: float) -> bool:
        return bool(has_password and row is not None and row["expires_at"] > now)

    def _cookie_header(self, scope: dict, value: str, max_age: int) -> tuple[bytes, bytes]:
        parts = [f"{COOKIE}={value}", "HttpOnly", "SameSite=Lax", "Path=/", f"Max-Age={max_age}"]
        if is_https(scope):
            parts.append("Secure")
        return (b"set-cookie", "; ".join(parts).encode())

    async def _touch(self, sha: str, now: float) -> None:
        await asyncio.to_thread(
            self.data.auth_session_touch, sha, int(now), int(now) + SESSION_TTL
        )

    async def _new_session(self, scope: dict) -> tuple[bytes, bytes]:
        token = secrets.token_urlsafe(32)
        now = int(self.clock())
        await asyncio.to_thread(self.data.auth_session_add, _sha(token), now, now + SESSION_TTL)
        return self._cookie_header(scope, token, SESSION_TTL)

    # -- hashing ----------------------------------------------------------------

    async def _hashing(self, work: Callable[[], Any]) -> Any:
        """Run one argon2 call under the concurrency cap; `_BUSY` when no slot came."""
        try:
            await asyncio.wait_for(self._slots.acquire(), HASH_WAIT)
        except TimeoutError:
            return _BUSY
        try:
            return await asyncio.to_thread(work)
        finally:
            self._slots.release()

    def _verify(self, stored: str, password: str) -> bool:
        try:
            return bool(self.hasher.verify(stored, password))
        except (VerificationError, InvalidHashError):
            return False

    # -- the entry point --------------------------------------------------------

    async def __call__(self, scope, receive, send) -> None:
        kind = scope["type"]
        if kind == "lifespan":
            await self.inner(scope, receive, send)
            return
        if kind not in ("http", "websocket"):
            return
        path = scope.get("path", "")
        method = scope.get("method", "GET").upper() if kind == "http" else "WEBSOCKET"

        if kind == "http" and path == HEALTH and (method, path) in EXEMPT:
            await self.inner(scope, receive, send)
            return

        if (kind == "websocket" or method not in ("GET", "HEAD", "OPTIONS")) and not _origin_ok(scope):
            if kind == "websocket":
                await self._close_socket(receive, send)
            else:
                await _json(send, 403, "origin")
            return

        sha = _sha(_cookie(scope)) if _cookie(scope) else ""
        has_password, row = await self._state(sha)
        self._saw(has_password)
        now = self.clock()
        live = self._live(has_password, row, now)

        if kind == "http":
            if (method, path) in EXEMPT and path == LOGIN:
                await self._login(scope, receive, send, method, has_password, live)
                return
            if (method, path) in SETUP_EXEMPT:
                if not has_password:
                    await self._setup(scope, receive, send, method)
                    return
                if live:
                    # A password exists; with a session this does nothing (spec R2).
                    await _redirect(send, "/")
                    return
            if (method, path) == ("POST", LOGOUT) and live:
                await asyncio.to_thread(self.data.auth_session_delete, sha)
                await _redirect(send, LOGIN, [self._cookie_header(scope, "", 0)])
                return

        if not live:
            await self._refuse(scope, receive, send, method, has_password)
            return

        if now - row["last_used_at"] >= TOUCH_EVERY:
            await self._touch(sha, now)
            cookie = self._cookie_header(scope, _cookie(scope), SESSION_TTL)
            send = _with_header(send, cookie)
            touched_at = now
        else:
            touched_at = row["last_used_at"]

        if kind == "websocket":
            await self._serve_socket(scope, receive, send, sha, touched_at)
            return
        await self.inner(scope, receive, send)

    # -- refusal ----------------------------------------------------------------

    async def _refuse(self, scope, receive, send, method: str, has_password: bool) -> None:
        """The three shapes spec's "refusal" allows, and nothing of the app in any of them."""
        if scope["type"] == "websocket":
            await self._close_socket(receive, send)
            return
        if method == "GET" and "text/html" in (_header(scope, b"accept") or ""):
            await _redirect(send, LOGIN if has_password else SETUP)
            return
        await _json(send, 401, "login required")

    @staticmethod
    async def _close_socket(receive, send) -> None:
        # Closed before accept; uvicorn answers the handshake 403 (`spike.md ## U1`).
        await receive()
        await send({"type": "websocket.close", "code": 1008})

    # -- /login and /setup --------------------------------------------------------

    async def _form(self, receive) -> dict[str, str] | int:
        body = b""
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return 400
            body += message.get("body", b"")
            if len(body) > MAX_FORM:
                return 413
            if not message.get("more_body"):
                break
        fields = parse_qs(body.decode("ascii", "replace"), keep_blank_values=True)
        return {k: v[0] for k, v in fields.items()}

    async def _login(self, scope, receive, send, method, has_password, live) -> None:
        if not has_password:
            await _redirect(send, SETUP)
            return
        if method == "GET":
            if live:
                await _redirect(send, "/")
            else:
                await self._page(send, scope, 200, _login_page(scope))
            return
        ip = _peer(scope)
        wait = self.limiter.wait(ip)
        if wait:
            await self._page(send, scope, 429, _login_page(scope, _locked(wait)))
            return
        form = await self._form(receive)
        if isinstance(form, int):
            await _json(send, form, "form")
            return
        stored = await asyncio.to_thread(self.data.auth_password_hash)
        if stored is None:
            await _redirect(send, SETUP)
            return
        ok = await self._hashing(lambda: self._verify(stored, form.get("password", "")))
        if ok is _BUSY:
            await self._page(send, scope, 429, _login_page(scope, _BUSY_TEXT))
            return
        if not ok:
            self.limiter.fail(ip)
            await self._page(send, scope, 401, _login_page(scope, "Wrong password."))
            return
        self.limiter.clear(ip)
        await _redirect(send, "/", [await self._new_session(scope)])

    async def _setup(self, scope, receive, send, method) -> None:
        if method == "GET":
            await self._page(send, scope, 200, _setup_page())
            return
        ip = _peer(scope)
        wait = self.limiter.wait(ip)
        if wait:
            await self._page(send, scope, 429, _setup_page(_locked(wait)))
            return
        form = await self._form(receive)
        if isinstance(form, int):
            await _json(send, form, "form")
            return
        token = self._token or ""
        given = form.get("token", "").strip()
        password = form.get("password", "")
        problem = None
        if not token or not hmac.compare_digest(given.encode(), token.encode()):
            problem = "That is not the setup token this process printed."
        elif password != form.get("password_confirm", ""):
            problem = "The two passwords differ."
        elif len(password) < MIN_PASSWORD:
            problem = f"The password must be at least {MIN_PASSWORD} characters."
        if problem is not None:
            self.limiter.fail(ip)
            await self._page(send, scope, 400, _setup_page(problem))
            return
        hashed = await self._hashing(lambda: self.hasher.hash(password))
        if hashed is _BUSY:
            await self._page(send, scope, 429, _setup_page(_BUSY_TEXT))
            return
        stored = await asyncio.to_thread(self.data.auth_set_password, hashed, int(self.clock()))
        if not stored:
            # Another request set one first. That is a refusal, not an overwrite.
            self._token = None
            await self._refuse(scope, receive, send, "POST", True)
            return
        self._token = None
        self.limiter.clear(ip)
        await _redirect(send, "/", [await self._new_session(scope)])

    @staticmethod
    async def _page(send, scope, status: int, html: str) -> None:
        await _respond(send, status, list(_HTML), html.encode("utf-8"))

    # -- a socket that got through --------------------------------------------------

    async def _serve_socket(self, scope, receive, send, sha: str, touched_at: float) -> None:
        """spec R8: an open socket closes within `WS_RECHECK` of its session ending.

        A watcher asks the database again every `WS_RECHECK` seconds. When the session is
        gone, the next `receive` the page's socket handler awaits — raced against the
        watcher — sends `websocket.close` (1008) to the browser and hands the app a
        `websocket.disconnect`, so no page handler runs on that socket again.

        Use through the socket counts as use (`intent.md ## Answers, câu 4`): the board
        sends every event over `/_event`, so a tab worked in for a month may make no HTTP
        request at all. When a message arrived since the last touch and that touch is
        `TOUCH_EVERY` old, the watcher pushes the expiry forward as an HTTP request would
        (`0070` review round 1, F1). It cannot refresh the browser's cookie — only a
        response can — so that happens at the next handshake or page load.
        """
        gone = asyncio.Event()
        closed = False
        used = False

        async def watch() -> None:
            nonlocal used, touched_at
            while True:
                await asyncio.sleep(WS_RECHECK)
                try:
                    has_password, row = await self._state(sha)
                    now = self.clock()
                    if not self._live(has_password, row, now):
                        gone.set()
                        return
                    if used and now - touched_at >= TOUCH_EVERY:
                        await self._touch(sha, now)
                        touched_at, used = now, False
                except Exception:
                    # A busy database is not a logout; ask again next time.
                    continue

        async def kick() -> dict:
            nonlocal closed
            if not closed:
                closed = True
                try:
                    await send({"type": "websocket.close", "code": 1008})
                except Exception:
                    pass
            return {"type": "websocket.disconnect", "code": 1008}

        async def guarded_receive() -> dict:
            nonlocal used
            if gone.is_set():
                return await kick()
            real = asyncio.ensure_future(receive())
            ended = asyncio.ensure_future(gone.wait())
            done, _ = await asyncio.wait({real, ended}, return_when=asyncio.FIRST_COMPLETED)
            if real in done:
                ended.cancel()
                message = real.result()
                if message["type"] == "websocket.receive":
                    used = True
                return message
            real.cancel()
            return await kick()

        async def guarded_send(message: dict) -> None:
            if closed:
                return
            await send(message)

        watcher = asyncio.ensure_future(watch())
        try:
            await self.inner(scope, guarded_receive, guarded_send)
        finally:
            watcher.cancel()


def _with_header(send, header: tuple[bytes, bytes]):
    # A websocket's refreshed cookie rides its `101`: ASGI's `websocket.accept` carries
    # headers, and uvicorn's wsproto adds them to the handshake reply.
    async def wrapped(message: dict) -> None:
        if message["type"] in ("http.response.start", "websocket.accept"):
            message = {**message, "headers": list(message.get("headers") or []) + [header]}
        await send(message)
    return wrapped


_BUSY_TEXT = "Too many logins are being checked at once. Try again in a few seconds."


def _locked(wait: float) -> str:
    return f"Too many wrong attempts from this address. Try again in {int(wait) + 1} s."


_STYLE = (
    "body{font-family:system-ui,sans-serif;background:#f6f6f4;color:#1c1c1a;display:flex;"
    "justify-content:center;padding-top:12vh}form{background:#fff;border:1px solid #ddd;"
    "border-radius:8px;padding:24px 28px;width:340px}h1{font-size:18px;margin:0 0 16px}"
    "label{display:block;font-size:13px;margin:12px 0 4px}input{width:100%;box-sizing:"
    "border-box;padding:8px;font-size:14px}button{margin-top:18px;padding:8px 14px;"
    "font-size:14px}p{font-size:13px}.err{color:#a3261a}.warn{color:#8a5a00}"
)


def _shell(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<meta name=\"viewport\" content=\"width=device-width\"><title>{escape(title)}</title>"
        f"<style>{_STYLE}</style></head><body>{body}</body></html>"
    )


def _error(text: str | None) -> str:
    return f"<p class=\"err\" id=\"error\">{escape(text)}</p>" if text else ""


def _login_page(scope: dict, error: str | None = None) -> str:
    warn = ""
    # `spec.md ## Answers, câu 11`: one line here, none on the board.
    if not is_https(scope) and _hostname(scope) not in LOOPBACK:
        warn = (
            "<p class=\"warn\" id=\"plain-http\">This page is served over plain HTTP: the "
            "password and the session cookie cross the network readable. Put coscc behind a "
            "TLS reverse proxy or on a private network.</p>"
        )
    return _shell("coscc — log in", (
        f"<form method=\"post\" action=\"{LOGIN}\"><h1>coscc</h1>{warn}{_error(error)}"
        "<label for=\"password\">Master password</label>"
        "<input id=\"password\" name=\"password\" type=\"password\" autocomplete="
        "\"current-password\" autofocus required>"
        "<button type=\"submit\">Log in</button>"
        "<p>Forgot it? Run <code>coscc reset-password</code> on the machine running coscc.</p>"
        "</form>"
    ))


def _setup_page(error: str | None = None) -> str:
    return _shell("coscc — set the master password", (
        f"<form method=\"post\" action=\"{SETUP}\"><h1>Set the master password</h1>"
        f"{_error(error)}"
        "<p>The setup token is printed in this process's log: "
        "<code>journalctl --user -u coscc | grep 'setup token'</code>, or the terminal "
        "running <code>coscc</code>.</p>"
        "<label for=\"token\">Setup token</label>"
        "<input id=\"token\" name=\"token\" autocomplete=\"off\" required>"
        f"<label for=\"password\">Password (at least {MIN_PASSWORD} characters)</label>"
        "<input id=\"password\" name=\"password\" type=\"password\" autocomplete="
        "\"new-password\" required>"
        "<label for=\"password_confirm\">Again</label>"
        "<input id=\"password_confirm\" name=\"password_confirm\" type=\"password\" "
        "autocomplete=\"new-password\" required>"
        "<button type=\"submit\">Set password</button></form>"
    ))
