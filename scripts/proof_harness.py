#!/usr/bin/env python3
"""The plumbing the browser proofs share, so a fix to it is one edit rather than two.

`verify_0003.py` and `verify_0006.py` measure different claims — that is deliberate and
recorded in `.cos/0006_demo-data-and-no-durable-store/impl.md`. What they had in common was
never the claims: it was the port check, the build guard, the browser launcher and the
app-under-test runner, which were copied verbatim from the first into the second. Two copies
of a boot loop drift the moment one of them needs a fix, and one already has.

Nothing here decides anything about a proof. It starts an app, stops it, and refuses the
environment early enough that "chromium is not installed" is never reported as "the page is
broken" — which is the exit-code split both proofs are built around.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

import httpx

from coscc import build

REPO = Path(__file__).resolve().parent.parent

BOOT_TIMEOUT_S = 60.0

# 0 the claims held, 1 they did not, 2 the environment could not answer. `EXIT_BROKEN` is
# the same number `verify_0003.py` calls `EXIT_PAGE`; the two proofs name it for what is
# broken in each.
EXIT_PASS, EXIT_BROKEN, EXIT_ENV = 0, 1, 2


def say(ok: bool, claim: str, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {claim}{': ' + detail if detail and not ok else ''}")
    return ok


def port_free(host: str, port: int) -> bool:
    with closing(socket.socket()) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) != 0


def wait_closed(host: str, port: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_free(host, port):
            return True
        time.sleep(0.2)
    return False


def require_free_port(config) -> None:
    """Both proofs need the configured port, and neither can move off it."""
    if port_free(config.host, config.port):
        return
    print(
        f"{config.host}:{config.port} is already in use — stop the running app first.\n"
        "The bundle hardcodes that address, so this proof cannot move to a free port.",
        file=sys.stderr,
    )
    raise SystemExit(EXIT_ENV)


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
        print("playwright is not installed — run:\n    uv sync --group dev", file=sys.stderr)
        raise SystemExit(EXIT_ENV)
    try:
        p = sync_playwright().start()
        return p, p.chromium.launch()
    except Exception as e:
        looked = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "~/.cache/ms-playwright")
        print(f"no usable chromium (looked in {looked}): {type(e).__name__}\n"
              f"    uv run playwright install chromium", file=sys.stderr)
        raise SystemExit(EXIT_ENV)


class RealApp:
    """The app started the way a person starts it, through `coscc.run`.

    Going through `coscc.run` means this also exercises the build guard and the
    loopback bind, rather than reaching past them into the ASGI object.

    `data_dir` defaults to `working_dir` so a proof run keeps its database in the same
    scratch folder and never touches the data root a real run would use.
    """

    def __init__(self, config, working_dir: Path, data_dir: Path | None = None):
        self.config = config
        self.working_dir = working_dir
        self.data_dir = working_dir if data_dir is None else data_dir
        self.proc: subprocess.Popen | None = None
        self.base = f"http://{config.host}:{config.port}"

    def start(self) -> "RealApp":
        env = {
            **os.environ,
            "COS_WORKING_DIR": str(self.working_dir),
            "COS_DATA_DIR": str(self.data_dir),
            "COS_WORKSPACES": "",
        }
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "coscc.run"],
            cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
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

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        # A restart, and the broken scene, both need the address actually released —
        # a lingering server would let the next page connect and make the claim vacuous.
        wait_closed(self.config.host, self.config.port)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False
