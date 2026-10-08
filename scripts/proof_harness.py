#!/usr/bin/env python3
"""The plumbing the browser runs share: the port check, the build guard, the browser launcher and
the app-under-test runner, kept in one place so a fix to it is one edit.

Nothing here decides anything about a run. It starts an app, stops it, and refuses the
environment early enough that "chromium is not installed" is never reported as "the page is
broken", which is the exit-code split `e2e.py` and `capture_screens.py` are built around.
"""

from __future__ import annotations

import io
import os
import secrets
import socket
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx

from coscc.http import auth
from coscc.store.db import Data

REPO = Path(__file__).resolve().parent.parent

BOOT_TIMEOUT_S = 60.0

# 0 the claims held, 1 they did not, 2 the environment could not answer.
EXIT_PASS, EXIT_BROKEN, EXIT_ENV = 0, 1, 2

GIT_ID = (
    "-c",
    "user.name=verify",
    "-c",
    "user.email=verify@example.invalid",
    "-c",
    "commit.gpgsign=false",
)


# Every line a proof prints, as it prints it. Python buffers stdout whenever it is not a
# terminal, so a long run redirected to a file wrote nothing until the end.
#
# Done here, once, rather than as `flush=True` on each call: the proofs print from `say`
# and from bare `print` both, and a second mechanism is how half of them keep the old
# behaviour. Importing this module is already what a proof does first.
if isinstance(sys.stdout, io.TextIOWrapper):  # else not a real stream; nothing to configure
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except ValueError, OSError:
        pass


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


def ensure_studio() -> None:
    """Build the studio (`coscc/_studio/`) when it is missing or older than `ui/src` or
    `ui/index.html`; a failed build is exit 2."""
    ui, built = REPO / "ui", REPO / "coscc" / "_studio" / "index.html"
    sources = [*(ui / "src").rglob("*"), ui / "index.html"]
    newest = max((f.stat().st_mtime for f in sources if f.is_file()), default=0.0)
    if built.is_file() and built.stat().st_mtime >= newest:
        return
    if not (ui / "node_modules").is_dir():
        steps = [["npm", "--prefix", str(ui), "ci"]]
    else:
        steps = []
    steps.append(["npm", "--prefix", str(ui), "run", "build"])
    for step in steps:
        if subprocess.run(step, cwd=REPO, capture_output=True, text=True).returncode != 0:
            print(f"the studio could not be built: {' '.join(step)}", file=sys.stderr)
            raise SystemExit(EXIT_ENV)


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
    except Exception as e:  # noqa: BLE001 - whatever failed, the fix is the same install
        looked = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "~/.cache/ms-playwright")
        print(
            f"no usable chromium (looked in {looked}): {type(e).__name__}\n"
            f"    uv run playwright install chromium",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_ENV)


def free_port(host: str) -> int:
    with closing(socket.socket()) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


class RealApp:
    """The app, `coscc.run:served`, under uvicorn on a free loopback port, the way
    `coscc.run` serves it (same factory, `proxy_headers=False`).

    `data_dir` defaults to `working_dir` so a proof keeps its database in its own scratch
    folder and never touches the data root a real run would use.
    """

    def __init__(self, working_dir: Path, data_dir: Path | None = None, host: str = "127.0.0.1"):
        self.working_dir = working_dir
        self.data_dir = working_dir if data_dir is None else data_dir
        self.host, self.port = host, free_port(host)
        self.proc: subprocess.Popen | None = None
        self.base = f"http://{host}:{self.port}"

    def start(self) -> "RealApp":
        env = {
            **os.environ,
            "COS_WORKING_DIR": str(self.working_dir),
            "COS_DATA_DIR": str(self.data_dir),
            "COS_WORKSPACES": "",
            "COS_HOST": self.host,
            "COS_PORT": str(self.port),
        }
        code = (
            "import uvicorn; "
            f"uvicorn.run('coscc.run:served', factory=True, host={self.host!r}, "
            f"port={self.port}, log_level='warning', proxy_headers=False)"
        )
        self.proc = subprocess.Popen(
            [sys.executable, "-c", code],
            cwd=REPO,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + BOOT_TIMEOUT_S
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                err = ((self.proc.stderr.read() if self.proc.stderr else b"") or b"").decode()[
                    -400:
                ]
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
        wait_closed(self.host, self.port)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False


def git(where: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(where), *GIT_ID, *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def make_repo(
    root: Path,
    outside: Path,
    name: str = "proj",
    remote: str = "remote.git",
    readme: str = "demo\n",
) -> Path:
    """A workspace: a clone of a bare-directory remote, one commit on `main`. The defaults
    are the fixture `capture_screens.py` takes its screenshots on."""
    origin = outside / remote
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    proj = root / name
    subprocess.run(["git", "clone", "-q", str(origin), str(proj)], check=True, capture_output=True)
    git(proj, "symbolic-ref", "HEAD", "refs/heads/main")
    (proj / "README.md").write_text(readme, encoding="utf-8")
    git(proj, "add", "-A")
    git(proj, "commit", "-q", "-m", "a repository")
    git(proj, "push", "-q", "origin", "main")
    return proj


def seed_fixture(
    work: Path, data_dir: Path, units: Iterable[tuple[Path, str, Mapping[str, Any]]]
) -> None:
    """A fixture's units as the board holds them: rows in `cos.db`, one transition per
    artifact state, written as `by`'s own. A unit is `(workspace, unit, kw)`, `kw` holding any
    of `statuses` (`{artifact: state}`), `type` (an intent record), `plan` (the files a plan record
    names, `[path]`), `shipped` (the merge
    row the PR machine writes), `questions` (`{artifact: [text or (text, recommendation)]}`,
    numbered from 1), `pr` (the number the PR machine's `open` row names), `rounds` (`[(n,
    head, verdict, [finding], [criterion])]`, a finding as `review_findings` takes it), `answers` (`[(artifact,
    n, text, by, name, date)]`) and `decisions` (`[(kind, fields, date)]`, a person's). The
    files a proof writes beside them are prose only; the app reads no state from them."""
    from coscc.units.meta import UnitMeta

    meta = UnitMeta(work, Data(data_dir))
    for ws, unit, kw in units:
        key = str(ws.resolve())
        with meta.data.write() as conn:
            meta.add_unit(conn, key, unit)
            if kw.get("type") is not None:
                submitted = {
                    "run": "r",
                    "revision": "h",
                    "object": {"judgement": "ready", "type": kw["type"]},
                }
                meta.record_result(conn, key, unit, "intent", "intent.md", submitted)
            if kw.get("plan") is not None:
                plan = {
                    "judgement": "ready",
                    "questions": [],
                    "variant": "novel",
                    "files": kw["plan"],
                }
                plan.update(steps=[], rests_on=[])
                meta.record_result(conn, key, unit, "plan", "plan.md", {"object": plan})
            for artifact, asked in (kw.get("questions") or {}).items():
                conn.executemany(
                    "INSERT INTO unit_questions (root, workspace, unit, artifact, n, text, "
                    "recommendation) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (meta.root, key, unit, artifact, n, *((q, "") if isinstance(q, str) else q))
                        for n, q in enumerate(asked, 1)
                    ],
                )
            for n, head, verdict, findings, *graded in kw.get("rounds") or ():
                submitted = {"n": n, "run": f"r{n}", "head": head}
                submitted["object"] = {
                    "verdict": verdict,
                    "findings": findings,
                    "criteria": graded[0] if graded else [],
                }
                meta.record_round(conn, key, unit, submitted)
            for artifact, n, text, by, name, date in kw.get("answers") or ():
                meta.add_answer(key, unit, artifact, n, text, by, name, date, "product", conn=conn)
            for kind, fields, date in kw.get("decisions") or ():
                meta.add_decision(key, unit, kind, fields, "owner", date, "product", conn=conn)
        for artifact, state in (kw.get("statuses") or {}).items():
            meta.history.record(
                key, unit, artifact, state, actor="fixture", session="fixture", source="fixture"
            )
        if kw.get("pr"):
            n = int(kw["pr"])
            meta.history.record(
                key,
                unit,
                "pr.md",
                "accepted",
                source="prmachine:open",
                guard="branch-named",
                authority="code",
                inputs={"number": n, "url": f"https://github.com/o/r/pull/{n}", "branch_ok": True},
            )
        if kw.get("shipped"):
            meta.history.record(
                key,
                unit,
                "ship.md",
                "accepted",
                source="prmachine:merged",
                guard="merge-read",
                authority="code",
            )


def seed_session(data_dir: Path) -> str:
    """`0070`: a password nobody types and one live session, so a page opens past the login
    without `/setup`. Returns the cookie's value."""
    import argon2

    data = Data(data_dir)
    now = int(time.time())
    data.auth_set_password(argon2.PasswordHasher().hash(secrets.token_urlsafe(24)), now)
    token = secrets.token_urlsafe(32)
    data.auth_session_add(auth._sha(token), now, now + auth.SESSION_TTL)
    return token
