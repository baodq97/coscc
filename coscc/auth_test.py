"""Tests for the login guard, `coscc/auth.py` (`0070`).

Driven over raw ASGI, scope by scope, so an HTTP request and a websocket handshake go
through the same door the same way and a test can see whether the app behind it was ever
reached. The app behind it is the real FastAPI surface, `coscc.api.build`, on a temporary
data root; the parts Reflex mounts are stood in for by `REFLEX_PATHS`, because composing
them needs a built bundle and `npm test` never builds. The guard decides before the inner
app sees anything, so what the inner app is does not change the count;
`scripts/verify_0070.py` makes the same count against the composed Reflex app.

Nothing here disables the guard (spec R3). Every test that needs a session gets one the
way a person does: it reads the setup token off the guard's stderr, posts `/setup`, then
`/login`.
"""

from __future__ import annotations

import asyncio
import io
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

from argon2.exceptions import VerifyMismatchError
from starlette.routing import Route

from coscc import auth, place
from coscc.api import build
from coscc.config import Config
from coscc.data import Data

PASSWORD = "correct horse battery staple"

# What Reflex serves beside `/api`, as `spike.md ## U1` listed it: the page, a static
# asset, the socket over polling, upload, the two pings, a path that does not exist, and a
# CORS preflight — the one the `api_transformer` position never saw.
REFLEX_PATHS = (
    ("GET", "/"),
    ("GET", "/_event/?EIO=4&transport=polling"),
    ("POST", "/_upload"),
    ("GET", "/ping"),
    ("GET", "/_health"),
    ("GET", "/assets/x.js"),
    ("GET", "/no/such/path"),
    ("OPTIONS", "/api/board"),
    # Every page route `coscc.py` registers, `/cost` (`0093`) included.
    *(("GET", f"/{screen}") for screen in place.SCREENS[1:]),
    ("GET", "/unit"),
)


class Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class FakeHasher:
    """Counts every hash and verify, so a test can say one did not happen."""

    def __init__(self):
        self.calls = 0

    def hash(self, password: str) -> str:
        self.calls += 1
        return "fake$" + password

    def verify(self, stored: str, password: str) -> bool:
        self.calls += 1
        if stored != "fake$" + password:
            raise VerifyMismatchError("no")
        return True


class Recorder:
    """The inner app, with a note of every scope that reached it."""

    def __init__(self, http_app, ws_app=None):
        self.http_app = http_app
        self.ws_app = ws_app
        self.seen: list[tuple[str, str, str]] = []

    async def __call__(self, scope, receive, send):
        self.seen.append((scope["type"], scope.get("method", ""), scope.get("path", "")))
        if scope["type"] == "websocket" and self.ws_app is not None:
            await self.ws_app(scope, receive, send)
            return
        await self.http_app(scope, receive, send)


class Reply:
    def __init__(self, status, headers, body, reached, sent):
        self.status = status
        self.headers = headers
        self.body = body
        self.reached = reached
        self.sent = sent

    def header(self, name: str) -> str | None:
        for key, value in self.headers:
            if key.decode().lower() == name:
                return value.decode()
        return None

    def cookie(self) -> str:
        raw = self.header("set-cookie") or ""
        return raw.split(";")[0].partition("=")[2]


def _headers(extra) -> list:
    out = [(b"host", b"testserver")]
    for key, value in extra:
        out.append((key.lower().encode(), value.encode()))
    return out


async def http(app, method, target, *, headers=(), body=b"", form=None, cookie=None,
               client=("10.0.0.9", 50000), recorder=None) -> Reply:
    path, _, query = target.partition("?")
    extra = list(headers)
    if form is not None:
        body = urlencode(form).encode()
        extra.append(("content-type", "application/x-www-form-urlencoded"))
    if cookie is not None:
        extra.append(("cookie", f"{auth.COOKIE}={cookie}"))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": query.encode(), "root_path": "", "headers": _headers(extra),
        "client": client, "server": ("testserver", 80),
    }
    inbox = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        if inbox:
            return inbox.pop(0)
        return {"type": "http.disconnect"}

    sent = []

    async def send(message):
        sent.append(message)

    before = len(recorder.seen) if recorder else 0
    await asyncio.wait_for(app(scope, receive, send), 20)
    start = next((m for m in sent if m["type"] == "http.response.start"), None)
    content = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    reached = bool(recorder and len(recorder.seen) > before)
    return Reply(start and start["status"], (start or {}).get("headers", []), content, reached, sent)


def ws_scope(target, headers=(), cookie=None, client=("10.0.0.9", 50001)):
    path, _, query = target.partition("?")
    extra = list(headers)
    if cookie is not None:
        extra.append(("cookie", f"{auth.COOKIE}={cookie}"))
    return {
        "type": "websocket", "asgi": {"version": "3.0"}, "scheme": "ws", "path": path,
        "raw_path": path.encode(), "query_string": query.encode(), "root_path": "",
        "headers": _headers(extra), "client": client, "server": ("testserver", 80),
        "subprotocols": [],
    }


async def ws_handshake(app, target, **kw) -> list:
    inbox: asyncio.Queue = asyncio.Queue()
    inbox.put_nowait({"type": "websocket.connect"})
    sent = []

    async def send(message):
        sent.append(message)
        # A socket that got in hangs up at once, so a leak is counted, not waited on.
        if message["type"] == "websocket.accept":
            inbox.put_nowait({"type": "websocket.disconnect", "code": 1000})

    await asyncio.wait_for(app(ws_scope(target, **kw), inbox.get, send), 20)
    return sent


def refused(reply: Reply) -> bool:
    """spec's narrow "refusal": 401 with an empty or `{"error": ...}` body, or 303 to
    `/login` or `/setup` with an empty body — and the app behind never reached."""
    if reply.reached:
        return False
    if reply.status == 401:
        return reply.body == b"" or set(__import__("json").loads(reply.body)) == {"error"}
    if reply.status == 303:
        return reply.header("location") in (auth.LOGIN, auth.SETUP) and reply.body == b""
    return False


def tokens(err: io.StringIO) -> list[str]:
    return [
        m.group(1) for line in err.getvalue().splitlines()
        if (m := auth.SETUP_LINE.match(line))
    ]


class Door(unittest.IsolatedAsyncioTestCase):
    """One guard around the real API, on a temporary data root."""

    hasher_factory = None

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Data(self.tmp.name)
        self.api = build(Config(data_dir=self.tmp.name))
        self.events: list[dict] = []
        self.recorder = Recorder(self.api, self._ws_app)
        self.clock = Clock()
        self.err = io.StringIO()
        self.hasher = self.hasher_factory() if self.hasher_factory else None
        self.guard = auth.Guard(
            self.recorder, self.data, clock=self.clock, err=self.err, hasher=self.hasher
        )

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _ws_app(self, scope, receive, send):
        await receive()
        await send({"type": "websocket.accept"})
        while True:
            message = await receive()
            self.events.append(message)
            if message["type"] == "websocket.disconnect":
                return

    async def call(self, method, target, **kw) -> Reply:
        return await http(self.guard, method, target, recorder=self.recorder, **kw)

    async def set_password(self, password=PASSWORD, **kw) -> Reply:
        return await self.call("POST", "/setup", form={
            "token": tokens(self.err)[-1], "password": password, "password_confirm": password,
        }, **kw)

    async def login(self, password=PASSWORD, **kw) -> Reply:
        return await self.call("POST", "/login", form={"password": password}, **kw)

    def requests(self):
        """Every route the API registers, with every method it declares, plus Reflex's."""
        out = []
        for route in self.api.routes:
            if not isinstance(route, Route):
                continue
            path = re.sub(r"\{[^}]+\}", "x", route.path)
            for method in sorted(route.methods or {"GET"}):
                out.append((method, path))
        return out + list(REFLEX_PATHS)

    async def count_not_refused(self, has_password: bool) -> list[str]:
        leaks = []
        for method, target in self.requests():
            path = target.partition("?")[0]
            exempt = (method, path) in auth.EXEMPT or (
                not has_password and (method, path) in auth.SETUP_EXEMPT
            )
            variants = [()]
            if method == "GET":
                variants.append((("accept", "text/html,application/xhtml+xml"),))
            for headers in variants:
                reply = await self.call(method, target, headers=headers)
                if not exempt and not refused(reply):
                    leaks.append(f"{method} {target} {headers} -> {reply.status}")
        for target in ("/_event/?EIO=4&transport=websocket", "/api/board", "/no/such"):
            before = len(self.recorder.seen)
            sent = await ws_handshake(self.guard, target)
            if len(self.recorder.seen) > before or sent[:1] != [
                {"type": "websocket.close", "code": 1008}
            ]:
                leaks.append(f"WS {target} -> {sent}")
        return leaks


class TheCount(Door):
    """spec R11: every route, no session, and the number that is not refused is 0."""

    async def test_nothing_but_the_exempt_list_answers_before_a_password(self):
        self.assertGreater(len(self.requests()), 40)
        self.assertEqual(await self.count_not_refused(False), [])

    async def test_nothing_but_the_exempt_list_answers_after_a_password(self):
        self.assertEqual((await self.set_password()).status, 303)
        self.assertEqual(await self.count_not_refused(True), [])
        # Once set, `/setup` is refused like any other route.
        self.assertTrue(refused(await self.call("GET", "/setup")))
        self.assertTrue(refused(await self.call("POST", "/setup", form={"token": "x"})))

    async def test_the_control_health_answers_and_workspaces_only_with_a_session(self):
        health = await self.call("GET", "/api/health")
        self.assertEqual((health.status, health.reached), (200, True))
        self.assertTrue(refused(await self.call("POST", "/api/health")))
        self.assertTrue(refused(await self.call("GET", "/api/health/")))
        self.assertTrue(refused(await self.call("GET", "/api/workspaces")))

        cookie = (await self.set_password()).cookie()
        self.assertTrue(cookie)
        after = await self.call("GET", "/api/workspaces", cookie=cookie)
        self.assertEqual((after.status, after.reached), (200, True))

    async def test_a_page_request_is_sent_to_setup_then_to_login(self):
        page = {"headers": (("accept", "text/html"),)}
        self.assertEqual((await self.call("GET", "/", **page)).header("location"), "/setup")
        await self.set_password()
        self.assertEqual((await self.call("GET", "/", **page)).header("location"), "/login")


class Setup(Door):
    async def test_a_wrong_token_a_mismatch_or_a_short_password_stores_nothing(self):
        token = tokens(self.err)[-1]
        cases = [
            {"token": "nope", "password": PASSWORD, "password_confirm": PASSWORD},
            {"token": token, "password": PASSWORD, "password_confirm": PASSWORD + "x"},
            {"token": token, "password": "a" * 11, "password_confirm": "a" * 11},
        ]
        for form in cases:
            reply = await self.call("POST", "/setup", form=form)
            self.assertEqual(reply.status, 400, form)
            self.assertIsNone(reply.header("set-cookie"))
            self.assertIsNone(self.data.auth_password_hash())
        self.assertEqual((await self.set_password("a" * 12)).status, 303)

    async def test_with_a_password_setup_is_refused_or_does_nothing(self):
        cookie = (await self.set_password()).cookie()
        stored = self.data.auth_password_hash()
        self.assertTrue(refused(await self.call("POST", "/setup", form={
            "token": "x", "password": "b" * 20, "password_confirm": "b" * 20,
        })))
        with_session = await self.call("POST", "/setup", cookie=cookie, form={
            "token": "x", "password": "b" * 20, "password_confirm": "b" * 20,
        })
        self.assertEqual((with_session.status, with_session.header("location")), (303, "/"))
        self.assertEqual(self.data.auth_password_hash(), stored)

    async def test_the_password_is_nowhere_readable(self):
        """spec R5: not in the database's bytes, not in what the guard wrote out."""
        await self.set_password()
        await self.login()
        stored = self.data.auth_password_hash()
        self.assertTrue(stored.startswith("$argon2id$"))
        for name in ("cos.db", "cos.db-wal", "cos.db-shm"):
            file = Path(self.tmp.name) / name
            if file.exists():
                self.assertNotIn(PASSWORD.encode(), file.read_bytes(), name)
        self.assertNotIn(PASSWORD, self.err.getvalue())
        self.assertNotIn(stored, self.err.getvalue())

    async def test_each_guard_without_a_password_prints_its_own_token(self):
        """spec R4: a new process with no password is a new token, flushed to stderr."""
        other = io.StringIO()
        auth.Guard(self.recorder, self.data, err=other)
        self.assertEqual(len(tokens(self.err)), 1)
        self.assertEqual(len(tokens(other)), 1)
        self.assertNotEqual(tokens(self.err), tokens(other))
        self.assertGreaterEqual(len(tokens(other)[0]), 22)  # 16 bytes, url-safe base64

    async def test_a_reset_takes_effect_on_the_next_request(self):
        """spec R10: the database is the channel; no restart."""
        cookie = (await self.set_password()).cookie()
        self.assertEqual((await self.call("GET", "/api/workspaces", cookie=cookie)).status, 200)
        self.data.auth_clear()
        reply = await self.call("GET", "/api/workspaces", cookie=cookie)
        self.assertTrue(refused(reply))
        self.assertEqual(len(tokens(self.err)), 2)
        self.assertEqual((await self.set_password()).status, 303)


class Sessions(Door):
    async def test_the_cookie_flags(self):
        await self.set_password()
        plain = (await self.login()).header("set-cookie")
        for flag in ("HttpOnly", "SameSite=Lax", "Path=/", f"Max-Age={auth.SESSION_TTL}"):
            self.assertIn(flag, plain)
        self.assertNotIn("Secure", plain)
        proxied = (await self.login(
            client=("127.0.0.1", 40000), headers=(("x-forwarded-proto", "https"),)
        )).header("set-cookie")
        self.assertIn("Secure", proxied)
        faked = (await self.login(
            client=("10.0.0.5", 40000), headers=(("x-forwarded-proto", "https"),)
        )).header("set-cookie")
        self.assertNotIn("Secure", faked)

    async def test_a_session_lives_thirty_days_from_last_use_and_is_touched_hourly(self):
        cookie = (await self.set_password()).cookie()
        sha = auth._sha(cookie)
        start = self.clock.t
        self.assertEqual(self.data.auth_state(sha)[1]["expires_at"], int(start) + auth.SESSION_TTL)

        self.clock.t = start + 1800
        quiet = await self.call("GET", "/api/workspaces", cookie=cookie)
        self.assertEqual(quiet.status, 200)
        self.assertIsNone(quiet.header("set-cookie"))
        self.assertEqual(self.data.auth_state(sha)[1]["last_used_at"], int(start))

        self.clock.t = start + 7200
        touched = await self.call("GET", "/api/workspaces", cookie=cookie)
        self.assertIn(f"Max-Age={auth.SESSION_TTL}", touched.header("set-cookie"))
        row = self.data.auth_state(sha)[1]
        self.assertEqual(row["expires_at"], int(start) + 7200 + auth.SESSION_TTL)

        self.clock.t = start + 7200 + auth.SESSION_TTL + 1
        self.assertTrue(refused(await self.call("GET", "/api/workspaces", cookie=cookie)))

    async def test_a_page_let_through_must_be_revalidated_and_data_is_left_alone(self):
        """`0070` review round 1 F2: a cached board after logout never reaches `/login`."""
        cookie = (await self.set_password()).cookie()
        page = await self.call("GET", "/docs", cookie=cookie)
        self.assertEqual((page.status, page.header("cache-control")), (200, "no-cache"))
        data = await self.call("GET", "/api/workspaces", cookie=cookie)
        self.assertEqual((data.status, data.header("cache-control")), (200, None))

    async def test_logout_ends_the_session(self):
        """spec R7."""
        cookie = (await self.set_password()).cookie()
        out = await self.call("POST", "/logout", cookie=cookie)
        self.assertEqual((out.status, out.header("location")), (303, "/login"))
        self.assertIn("Max-Age=0", out.header("set-cookie"))
        self.assertTrue(refused(await self.call("GET", "/api/workspaces", cookie=cookie)))
        self.assertTrue(refused(await self.call("POST", "/logout", cookie=cookie)))

    async def test_login_with_a_session_goes_home(self):
        cookie = (await self.set_password()).cookie()
        reply = await self.call("GET", "/login", cookie=cookie)
        self.assertEqual((reply.status, reply.header("location")), (303, "/"))

    async def test_the_login_page_warns_on_plain_http_off_loopback(self):
        """`spec.md ## Answers, câu 11`: one line on the login page, off loopback only."""
        await self.set_password()
        self.assertIn(b"plain-http", (await self.call("GET", "/login")).body)

        def page(host, scheme="http"):
            return auth._login_page({"headers": [(b"host", host)], "scheme": scheme})

        self.assertIn("plain-http", page(b"192.168.1.4:8790"))
        self.assertNotIn("plain-http", page(b"127.0.0.1:8790"))
        self.assertNotIn("plain-http", page(b"localhost:8790"))
        self.assertNotIn("plain-http", page(b"[::1]:8790"))
        self.assertNotIn("plain-http", page(b"coscc.example", "https"))


class Origins(Door):
    async def test_a_foreign_origin_is_refused_before_the_app(self):
        cookie = (await self.set_password()).cookie()
        foreign = await self.call(
            "POST", "/api/units/hold", cookie=cookie,
            headers=(("origin", "http://evil.example:8790"),),
        )
        self.assertEqual((foreign.status, foreign.reached), (403, False))
        same = await self.call(
            "POST", "/api/units/hold", cookie=cookie,
            headers=(("origin", "http://testserver"),),
        )
        self.assertTrue(same.reached)
        null = await self.call(
            "POST", "/logout", cookie=cookie, headers=(("origin", "null"),)
        )
        self.assertEqual(null.status, 403)
        before = len(self.recorder.seen)
        sent = await ws_handshake(
            self.guard, "/_event/?EIO=4&transport=websocket", cookie=cookie,
            headers=(("origin", "http://evil.example"),),
        )
        self.assertEqual(sent, [{"type": "websocket.close", "code": 1008}])
        self.assertEqual(len(self.recorder.seen), before)

    async def test_nothing_turns_the_guard_off(self):
        """spec R3: no header or origin gets past without a session."""
        for headers in (
            (("origin", "http://testserver"),),
            (("x-forwarded-for", "127.0.0.1"),),
            (("x-forwarded-proto", "https"),),
        ):
            self.assertTrue(refused(await self.call("GET", "/api/workspaces", headers=headers)))
            self.assertTrue(refused(await self.call(
                "GET", "/api/workspaces", headers=headers, client=("127.0.0.1", 1)
            )))


class Limits(Door):
    hasher_factory = FakeHasher

    async def test_five_failures_lock_and_the_lock_doubles_to_an_hour(self):
        """spec R9, with a fake clock and a hasher that counts."""
        await self.set_password()
        ip = "10.0.0.9"
        calls = self.hasher.calls
        for _ in range(5):
            self.assertEqual((await self.login("wrong")).status, 401)
        self.assertEqual(self.hasher.calls, calls + 5)
        locked = await self.login(PASSWORD)
        self.assertEqual(locked.status, 429)
        self.assertEqual(self.hasher.calls, calls + 5)
        self.assertAlmostEqual(self.guard.limiter.wait(ip), 60, delta=1)

        expected = 60
        for _ in range(8):
            self.clock.t += self.guard.limiter.wait(ip) + 1
            self.assertEqual((await self.login("wrong")).status, 401)
            expected = min(expected * 2, 3600)
            self.assertAlmostEqual(self.guard.limiter.wait(ip), expected, delta=1)
        self.assertEqual(expected, 3600)

        self.clock.t += self.guard.limiter.wait(ip) + 1
        self.assertEqual((await self.login(PASSWORD)).status, 303)
        self.assertEqual(self.guard.limiter.wait(ip), 0)
        self.assertNotIn(ip, self.guard.limiter._rows)

    async def test_setup_failures_count_too(self):
        for _ in range(5):
            await self.call("POST", "/setup", form={"token": "x", "password": "p", "password_confirm": "p"})
        self.assertEqual((await self.set_password()).status, 429)
        self.assertIsNone(self.data.auth_password_hash())


class Blocking(FakeHasher):
    def __init__(self):
        super().__init__()
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def verify(self, stored, password):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            self.release.wait(10)
            return super().verify(stored, password)
        finally:
            with self.lock:
                self.active -= 1


class HashConcurrency(Door):
    hasher_factory = Blocking

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.data.auth_set_password("fake$" + PASSWORD, int(self.clock()))

    async def _wait_active(self, n):
        for _ in range(200):
            if self.hasher.active >= n:
                return
            await asyncio.sleep(0.01)

    async def test_no_more_than_two_hash_at_once(self):
        tasks = [
            asyncio.ensure_future(self.login(client=(f"10.0.1.{i}", 1))) for i in range(3)
        ]
        await self._wait_active(2)
        await asyncio.sleep(0.2)
        self.assertEqual(self.hasher.active, 2)
        self.hasher.release.set()
        replies = await asyncio.gather(*tasks)
        self.assertEqual([r.status for r in replies], [303, 303, 303])
        self.assertEqual(self.hasher.peak, 2)

    async def test_a_request_that_waits_too_long_is_429_and_not_hashed(self):
        with mock.patch.object(auth, "HASH_WAIT", 0.2):
            tasks = [
                asyncio.ensure_future(self.login(client=(f"10.0.2.{i}", 1))) for i in range(2)
            ]
            await self._wait_active(2)
            late = await self.login(client=("10.0.2.9", 1))
            self.assertEqual(late.status, 429)
            self.hasher.release.set()
            await asyncio.gather(*tasks)
        self.assertEqual((self.hasher.peak, self.hasher.calls), (2, 2))
        self.assertEqual(self.guard.limiter.wait("10.0.2.9"), 0)


class Sockets(Door):
    async def test_a_socket_closes_after_its_session_ends(self):
        """spec R8, at the ASGI layer: handshake refused without a cookie, accepted with
        one, and closed once the session is gone — the app sees a disconnect."""
        cookie = (await self.set_password()).cookie()
        self.assertEqual(
            await ws_handshake(self.guard, "/_event/?EIO=4&transport=websocket"),
            [{"type": "websocket.close", "code": 1008}],
        )
        inbox: asyncio.Queue = asyncio.Queue()
        inbox.put_nowait({"type": "websocket.connect"})
        sent: list = []

        async def send(message):
            sent.append(message)

        with mock.patch.object(auth, "WS_RECHECK", 0.05):
            task = asyncio.ensure_future(self.guard(
                ws_scope("/_event/?EIO=4&transport=websocket", cookie=cookie), inbox.get, send
            ))
            for _ in range(100):
                if sent:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(sent, [{"type": "websocket.accept"}])
            inbox.put_nowait({"type": "websocket.receive", "text": "hello"})
            await asyncio.sleep(0.1)
            self.assertEqual(self.events[-1]["type"], "websocket.receive")

            self.data.auth_clear()
            await asyncio.wait_for(task, 1)
        self.assertEqual(sent[-1], {"type": "websocket.close", "code": 1008})
        self.assertEqual(self.events[-1]["type"], "websocket.disconnect")

    async def test_a_session_used_only_through_its_socket_lives_on(self):
        """`intent.md ## Answers, câu 4`, `0070` review round 1 F1: the board sends every
        event over `/_event`, so a handshake and the messages after it are use."""
        cookie = (await self.set_password()).cookie()
        sha = auth._sha(cookie)
        start = self.clock.t
        inbox: asyncio.Queue = asyncio.Queue()
        inbox.put_nowait({"type": "websocket.connect"})
        sent: list = []

        async def send(message):
            sent.append(message)

        async def settle():
            await asyncio.sleep(0.2)

        def expires():
            return self.data.auth_state(sha)[1]["expires_at"]

        with mock.patch.object(auth, "WS_RECHECK", 0.05):
            # The handshake two hours on is a use: touched, and the 101 renews the cookie.
            self.clock.t = start + 7200
            task = asyncio.ensure_future(self.guard(
                ws_scope("/_event/?EIO=4&transport=websocket", cookie=cookie), inbox.get, send
            ))
            await settle()
            self.assertEqual(sent[0]["type"], "websocket.accept")
            renewed = dict(sent[0]["headers"])[b"set-cookie"].decode()
            self.assertIn(f"{auth.COOKIE}={cookie}", renewed)
            self.assertIn(f"Max-Age={auth.SESSION_TTL}", renewed)
            self.assertEqual(expires(), int(start) + 7200 + auth.SESSION_TTL)

            # Two more hours with no message: not use, no write.
            self.clock.t = start + 4 * 3600
            await settle()
            self.assertEqual(expires(), int(start) + 7200 + auth.SESSION_TTL)

            # A message, then the watcher's next look: touched from the socket alone.
            inbox.put_nowait({"type": "websocket.receive", "text": "event"})
            await settle()
            self.assertEqual(expires(), int(start) + 4 * 3600 + auth.SESSION_TTL)

            # Past thirty days from the handshake, still open, because it was used since.
            self.clock.t = start + 7200 + auth.SESSION_TTL + 1
            await settle()
            self.assertFalse(task.done())
            self.assertNotIn({"type": "websocket.close", "code": 1008}, sent)

            # Thirty days from the last use, it closes like any ended session.
            self.clock.t = start + 4 * 3600 + auth.SESSION_TTL + 1
            await asyncio.wait_for(task, 1)
        self.assertEqual(sent[-1], {"type": "websocket.close", "code": 1008})

    async def test_a_recent_handshake_writes_nothing_and_sets_no_cookie(self):
        cookie = (await self.set_password()).cookie()
        start = self.clock.t
        self.clock.t = start + 1800
        sent = await ws_handshake(self.guard, "/_event/?EIO=4&transport=websocket", cookie=cookie)
        self.assertEqual(sent[0], {"type": "websocket.accept"})
        self.assertEqual(self.data.auth_state(auth._sha(cookie))[1]["last_used_at"], int(start))


if __name__ == "__main__":
    unittest.main()
