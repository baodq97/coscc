#!/usr/bin/env python3
"""Proof for .cos/0004_unproven-page.

Exits 0 only when the page is reachable **and** the check that says so can be made to
fail. `0003` closed with three green claims sitting on top of a dead page, so measuring
only the good case is the mistake this command exists to not repeat.

Exit codes are kept apart deliberately (`spec.md` R1):

    0  the page works and the check is capable of failing
    1  the page is broken, or the check could not be made to fail
    2  the environment is not ready — no browser, no build, stale build, port in use

Collapsing 2 into 1 would report "chromium is not installed" as "the page is dead".

It creates no session and sends no prompt, so it spends no account quota (`spec.md` R6),
and it clones nothing, so it needs no network.
"""

from __future__ import annotations

import http.server
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from cos_baodo import build
from cos_baodo.config import from_env
from cos_baodo.service import Service
from cos_baodo.sessions import Sessions

REPO = Path(__file__).resolve().parent.parent

WORKSPACES = 2  # from intent.md. Change it there, not here.

PAGE_TIMEOUT_MS = 15_000  # how long the page gets to show live data before it has failed
BOOT_TIMEOUT_S = 60.0

EXIT_PASS, EXIT_PAGE, EXIT_ENV = 0, 1, 2


def say(ok: bool, claim: str, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {claim}{': ' + detail if detail and not ok else ''}")
    return ok


def _port_free(host: str, port: int) -> bool:
    with closing(socket.socket()) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) != 0


def _wait_closed(host: str, port: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_free(host, port):
            return True
        time.sleep(0.2)
    return False


# --------------------------------------------------------------------------
# environment
# --------------------------------------------------------------------------


def require_build(config) -> Path:
    built = build.web_dir() / "build" / "client"
    state, message = build.check(config, built)
    if state != build.OK:
        print(message, file=sys.stderr)
        raise SystemExit(EXIT_ENV)
    return built


def require_browser():
    """`spec.md` R7: never download. Say where we looked and what to run."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "playwright is not installed — run:\n    uv sync --group dev",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_ENV)
    try:
        p = sync_playwright().start()
        browser = p.chromium.launch()
    except Exception as e:
        looked = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "~/.cache/ms-playwright")
        print(
            f"no usable chromium (looked in {looked}): {type(e).__name__}\n"
            f"    uv run playwright install chromium",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_ENV)
    return p, browser


# --------------------------------------------------------------------------
# the scene
# --------------------------------------------------------------------------


def make_workspaces(root: Path, config) -> None:
    """Two workspaces, adopted from directories. No clone, so no network."""
    scoped = from_env({**os.environ, "COS_WORKING_DIR": str(root), "COS_WORKSPACES": ""})
    service = Service(scoped, Sessions(scoped))
    for i in range(WORKSPACES):
        name = f"project-{i + 1}"
        (root / name).mkdir(parents=True, exist_ok=True)
        service.store.add(name, label=f"Workspace {i + 1}")


class RealApp:
    """The app started the way a person starts it, not by a private path.

    Going through `cos_baodo.run` means this also exercises the build guard and the
    loopback bind, rather than reaching past them into the ASGI object.
    """

    def __init__(self, config, working_dir: Path):
        self.config, self.working_dir = config, working_dir
        self.proc: subprocess.Popen | None = None
        self.base = f"http://{config.host}:{config.port}"

    def __enter__(self):
        env = {**os.environ, "COS_WORKING_DIR": str(self.working_dir), "COS_WORKSPACES": ""}
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "cos_baodo.run"],
            cwd=REPO, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + BOOT_TIMEOUT_S
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                err = (self.proc.stderr.read() or b"").decode()[-400:]
                print(f"the app exited before serving:\n{err}", file=sys.stderr)
                raise SystemExit(EXIT_ENV)
            try:
                if httpx.get(f"{self.base}/api/health", timeout=2).status_code == 200:
                    return self
            except httpx.HTTPError:
                time.sleep(0.3)
        raise SystemExit(EXIT_ENV)

    def __exit__(self, *exc):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        return False


class StaticOnly:
    """The broken scene: the real bundle, served with nothing behind it.

    This is the symptom of 2026-09-21 rather than its cause (`spec.md` C2). The page
    renders and opens its WebSocket at the address baked into the bundle, and nobody
    answers — which is what a user saw.
    """

    def __init__(self, directory: Path):
        self.directory = directory

    def __enter__(self):
        d = str(self.directory)

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=d, **kw)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        return False


# --------------------------------------------------------------------------
# the measurement — one function, run against both scenes
# --------------------------------------------------------------------------


def page_renders(browser, url: str) -> str:
    """Return "" when the page itself loaded, or a reason why it did not.

    Claim 2 is only worth anything if the broken page actually rendered. Without this,
    an empty build directory would serve a 404, live data would be absent for a reason
    that has nothing to do with the backend, and the negative control would report
    success while testing nothing — the proof quietly degrading into the same shape of
    lie this unit exists to catch.
    """
    page = browser.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        try:
            page.get_by_role("heading", name="cos-baodo").first.wait_for(
                timeout=PAGE_TIMEOUT_MS
            )
        except Exception:
            body = (page.text_content("body") or "")[:200].replace("\n", " ")
            return f"the page did not render; saw: {body!r}"
        return ""
    finally:
        page.close()


def live_data_reaches_the_page(browser, url: str, working_dir: str, count: int) -> str:
    """Return "" when the page shows live backend data, or a reason why it did not.

    Deliberately not a search for Reflex's "Connection Error" text (`spec.md` R4): that
    string is Reflex's to change. These two values only appear if `on_mount` ran, and
    `on_mount` only runs if the WebSocket connected — so their presence *is* the
    connection, observed rather than inferred from a label.
    """
    page = browser.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        try:
            page.get_by_text(working_dir, exact=False).first.wait_for(
                timeout=PAGE_TIMEOUT_MS
            )
        except Exception:
            return f"the working folder {working_dir} never appeared on the page"
        try:
            page.get_by_text(f"{count} workspace(s)", exact=False).first.wait_for(
                timeout=PAGE_TIMEOUT_MS
            )
        except Exception:
            body = (page.text_content("body") or "")[:200].replace("\n", " ")
            return f"the count of {count} never appeared on the page; saw: {body!r}"
        return ""
    finally:
        page.close()


def run() -> int:
    config = from_env()
    built = require_build(config)
    if not _port_free(config.host, config.port):
        print(
            f"{config.host}:{config.port} is already in use — stop the running app first.\n"
            "The bundle hardcodes that address, so this proof cannot move to a free port.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_ENV)

    playwright, browser = require_browser()
    root = Path(tempfile.mkdtemp(prefix="cos0004-"))
    results: list[bool] = []
    try:
        make_workspaces(root, config)

        # --- claim 1: the page shows what the backend says ---
        with RealApp(config, root) as app:
            api = httpx.get(f"{app.base}/api/workspaces", timeout=30).json()
            ok = api["count"] == WORKSPACES and api["working_dir"] == str(root)
            results.append(
                say(ok, f"the backend reports {WORKSPACES} workspaces", str(api)[:120])
            )
            reason = live_data_reaches_the_page(
                browser, app.base, api["working_dir"], api["count"]
            )
            results.append(
                say(not reason, "the page shows the backend's working folder and count", reason)
            )

        # --- claim 2: the same measurement fails when nothing is behind the page ---
        # The app must be gone first. The bundle points at its address, so a lingering
        # server would let the "broken" page connect and quietly make this claim vacuous.
        if not _wait_closed(config.host, config.port):
            print(
                f"{config.host}:{config.port} did not close after the app was stopped; "
                "the broken scene would not be broken.",
                file=sys.stderr,
            )
            raise SystemExit(EXIT_ENV)

        with StaticOnly(built) as static:
            # The page must genuinely render first, or the next claim is measuring a 404.
            not_rendered = page_renders(browser, static.base)
            results.append(
                say(
                    not not_rendered,
                    "the broken scene still renders the real page",
                    not_rendered,
                )
            )
            reason = live_data_reaches_the_page(
                browser, static.base, str(root), WORKSPACES
            )
            results.append(
                say(
                    bool(reason),
                    "the same check FAILS when no backend is reachable",
                    "it passed against a page with no backend — this proof cannot go red",
                )
            )
    finally:
        browser.close()
        playwright.stop()
        import shutil

        shutil.rmtree(root, ignore_errors=True)

    print()
    if all(results):
        print("PASS — the page works, and the check that says so can fail.")
        return EXIT_PASS
    print(f"FAIL — {results.count(False)} of {len(results)} checks did not hold.")
    return EXIT_PAGE


if __name__ == "__main__":
    sys.exit(run())
