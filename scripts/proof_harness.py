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

import io
import os
import secrets
import socket
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

import httpx

from coscc import auth
from coscc.store.db import Data

REPO = Path(__file__).resolve().parent.parent

BOOT_TIMEOUT_S = 60.0

# 0 the claims held, 1 they did not, 2 the environment could not answer. `EXIT_BROKEN` is
# the same number `verify_0003.py` calls `EXIT_PAGE`; the two proofs name it for what is
# broken in each.
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
# terminal, so `verify_0014.py > log` -- a run that spends up to eight sessions and can
# take half an hour -- wrote an empty file from start to finish and emitted everything at
# once at the end. Measured 2026-09-22, watching a run that had no way to be watched.
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
    readme: str = "verify_0071\n",
) -> Path:
    """A workspace: a clone of a bare-directory remote, one commit on `main`. The defaults
    are the fixture `capture_screens.py` has taken its screenshots on since `0083`."""
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


def ingest_fixture(
    work: Path, data_dir: Path, *workspaces: Path, by: str = "capture_screens"
) -> None:
    """`0135`: a fixture's files are written by hand, and since then the board reads a unit
    from `cos.db`, not its files. So they go in as a finished step's do: the store's import,
    if the app has not read it yet — which also reads any answer a file carries — then an
    ingest of every unit, which reads only what changed since."""
    from coscc import units
    from coscc.units.meta import UnitMeta

    meta = UnitMeta(work, Data(data_dir))
    for ws in workspaces:
        key, store = str(ws.resolve()), units.root(ws, data_dir)
        if not meta.imported(key):
            meta.import_store(key, store)
        for d in sorted((store / units.COS_DIR).iterdir()):
            if d.is_dir() and d.name != "ideas":
                meta.ingest(key, store, d.name, actor=by, session=by, source=by)


def fixture_state(store: Path, where: Path) -> Path:
    """`0135`: the snapshot the loop's `--state` decides on, for a store a proof wrote by hand and
    reads with `python -m coscc.loop --root <store>`. Imported into a `cos.db` of its own under
    `where`, as the app imports a store on its first read, and written to `where/state.json`
    for `--state`. Made again from nothing on every call, so a unit written since is in it."""
    import json
    import shutil

    from coscc.units.meta import UnitMeta

    shutil.rmtree(where, ignore_errors=True)
    where.mkdir(parents=True)
    meta = UnitMeta(where, Data(where / "data"))
    key = str(store.resolve())
    meta.import_store(key, store)
    path = where / "state.json"
    path.write_text(json.dumps(meta.snapshot(key, {}), ensure_ascii=False), encoding="utf-8")
    return path


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
