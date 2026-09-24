"""Proof for `0076_a-step-can-migrate-the-running-apps-database`, R8 (and R1, R2, R4).

**No session, no quota, no network.** A temporary `HOME` whose `~/.cos` plays the running
app, so `Data()` with no argument lands on it too. A child process stands in for a step: it
gets the environment `Sessions` builds (`sessions.child_env` over this process's own,
composed the way the SDK composes it), raises `coscc.data.SCHEMA_VERSION` by one, and
opens (a) `Data(from_env().data_dir)`, (b) `Data()` and (c) `Data(<the app's root>)`.
Passing needs (a) at the raised version, (b) and (c) `Protected`, the app's `user_version`
and table names unchanged, and `GET /api/workspaces` on that app `200` afterwards.

`--suite` is R7: `npm test` of this repository, in the environment a session would give
it, with the `cos.db` `config.from_env` names for this machine protected. It reads that
database's `user_version` and table names before and after (`mode=ro`, never through
`Data`) and needs them unchanged. Run it at a terminal: a step is not to read the real
database (`spec.md ## Answers, câu 3`).

What neither mode measures: the intent's outcome, a real `impl` step from the board on a
branch that raises the schema (`spec.md` C5). A child process is not a session.

Exit 0 every claim passed; 1 one failed; 2 `httpx` or `coscc` cannot be imported, or
`npm` is missing for `--suite`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

RESULTS: list[tuple[str, bool, str]] = []

# The stand-in for a step's code on a branch that migrates the schema one version on.
CHILD = r"""
import json, sys
sys.path.insert(0, {repo!r})
from coscc import data
from coscc.config import from_env
data.SCHEMA_VERSION += 1
data._SCHEMA = data._SCHEMA + ("CREATE TABLE IF NOT EXISTS verify_0076 (x)",)
out = {{}}
for label, make in (
    ("a", lambda: data.Data(from_env().data_dir)),
    ("b", lambda: data.Data()),
    ("c", lambda: data.Data({app!r})),
):
    try:
        out[label] = make().version()
    except Exception as e:
        out[label] = type(e).__name__
print(json.dumps(out))
"""


def say(ok: bool, claim: str, detail: str = "") -> bool:
    RESULTS.append((claim, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {claim}" + (f"  [{detail}]" if detail else ""))
    return ok


def snapshot(db: Path) -> tuple[int, list[str]] | None:
    """`user_version` and every name in `sqlite_master`, read-only. `None` if no file."""
    if not db.exists():
        return None
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        names = sorted(r[0] for r in conn.execute("SELECT name FROM sqlite_master"))
    finally:
        conn.close()
    return version, names


def brief(before, after) -> str:
    """Versions and table counts, and the names that appeared or went."""
    if before is None or after is None:
        return f"{before} -> {after}"
    gained = sorted(set(after[1]) - set(before[1]))
    lost = sorted(set(before[1]) - set(after[1]))
    return (f"version {before[0]} -> {after[0]}, {len(before[1])} -> {len(after[1])} names"
            + (f", new {gained}" if gained else "") + (f", gone {lost}" if lost else ""))


def session_env(cwd: Path, app_root: Path) -> tuple[dict[str, str], Path]:
    """What a session's process reads, and the data root it was given."""
    from coscc import sessions

    scratch = sessions.scratch_dir(app_root)
    inherited = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    inherited.update(
        sessions.child_env(str(cwd), data_dir=str(scratch), app_db=app_root / "cos.db")
    )
    return inherited, scratch


def outside(path: Path, root: Path) -> bool:
    p, r = path.resolve(), root.resolve()
    return p != r and r not in p.parents and p not in r.parents


def check_env(envs: list[tuple[dict[str, str], Path]], app_root: Path) -> None:
    from coscc.config import PROTECTED_DB_VAR, protected_databases

    for i, (env, scratch) in enumerate(envs, 1):
        given = Path(env.get("COS_DATA_DIR", ""))
        say(
            bool(env.get("COS_DATA_DIR")) and given.is_absolute() and given.is_dir()
            and given == scratch and outside(given, app_root),
            f"R1 session {i}: COS_DATA_DIR is absolute, exists, apart from the app's root",
            str(given),
        )
        say(
            (app_root / "cos.db").resolve() in protected_databases(env),
            f"R4 session {i}: {PROTECTED_DB_VAR} lists the app's cos.db",
            env.get(PROTECTED_DB_VAR, ""),
        )
    if len(envs) >= 2:
        say(envs[0][1] != envs[1][1], "R2 two sessions get two directories")


async def workspaces_status(app_root: Path, tmp: Path) -> int:
    import httpx

    from coscc.api import build
    from coscc.config import Config

    work = tmp / "work"
    work.mkdir(exist_ok=True)
    app = build(Config(workspaces=(), working_dir=str(work), data_dir=str(app_root)))
    # `raise_app_exceptions=False`: the incident was a `500`, and it must read as one here
    # rather than as this script crashing.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://proof",
    ) as client:
        return (await client.get("/api/workspaces")).status_code


def plain(tmp: Path) -> None:
    from coscc import data, sessions
    from coscc.config import PROTECTED_DB_VAR

    app_root = Path(os.environ["HOME"]) / ".cos"
    data.Data(app_root).version()  # the running app's database, at this build's schema
    before = snapshot(app_root / "cos.db")
    print(f"app database before: version {before[0]}, {len(before[1])} names")

    ws = tmp / "ws"
    ws.mkdir()
    envs = [session_env(ws, app_root), session_env(ws, app_root)]
    try:
        check_env(envs, app_root)
        env, scratch = envs[0]
        done = subprocess.run(
            [sys.executable, "-c", CHILD.format(repo=str(REPO), app=str(app_root))],
            cwd=str(ws), env=env, capture_output=True, text=True, timeout=120,
        )
        try:
            got = json.loads(done.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            got = {}
        print(f"child: exit {done.returncode} {got} {done.stderr.strip()[-400:]}")
        raised = data.SCHEMA_VERSION + 1
        say(got.get("a") == raised,
            "R8 (a) the step's own data root migrated to the raised schema", str(got.get("a")))
        say(got.get("b") == "Protected", "R8 (b) Data() refused", str(got.get("b")))
        say(got.get("c") == "Protected", "R8 (c) Data(<app root>) refused", str(got.get("c")))
        after = snapshot(app_root / "cos.db")
        say(before is not None and after == before,
            "R8 the app's user_version and tables are unchanged", brief(before, after))
        status = asyncio.run(workspaces_status(app_root, tmp))
        say(status == 200, "R8 GET /api/workspaces answers 200 afterwards", str(status))
    finally:
        for _, scratch in envs:
            sessions._drop(scratch)
    say(not any(s.exists() for _, s in envs), "the session data roots are removed")
    # Nothing in this process may carry the variable into the next check by accident.
    os.environ.pop(PROTECTED_DB_VAR, None)


def suite() -> None:
    from coscc import sessions
    from coscc.config import from_env
    from coscc.data import Data

    app_db = Data(from_env().data_dir).db_path
    before = snapshot(app_db)
    print(f"{app_db} before: {before if before is not None else 'no file'}")
    env, scratch = session_env(REPO, app_db.parent)
    try:
        check_env([(env, scratch)], app_db.parent)
        code = subprocess.run(["npm", "test"], cwd=str(REPO), env=env).returncode
        say(code == 0, "R7 npm test passes in a session's environment", f"exit {code}")
    finally:
        sessions._drop(scratch)
    after = snapshot(app_db)
    say(after == before, f"R7 {app_db} user_version and tables unchanged", brief(before, after))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--suite", action="store_true", help="R7: npm test in a session's environment")
    args = parser.parse_args()

    if args.suite:
        if shutil.which("npm") is None:
            print("no npm on PATH — --suite cannot answer without it")
            return 2
        try:
            import coscc.sessions  # noqa: F401
        except ImportError as e:
            print(f"cannot import coscc: {e}")
            return 2
        suite()
    else:
        with tempfile.TemporaryDirectory(prefix="verify-0076-") as d:
            tmp = Path(d).resolve()
            home = tmp / "home"
            home.mkdir()
            # Before `coscc` is imported, so every default it computes lands in here.
            os.environ["HOME"] = str(home)
            for name in ("COS_DATA_DIR", "COSCC_PROTECTED_DB"):
                os.environ.pop(name, None)
            try:
                import httpx  # noqa: F401

                import coscc.api  # noqa: F401
                import coscc.sessions  # noqa: F401
            except ImportError as e:
                print(f"cannot import what this proof needs: {e}")
                return 2
            plain(tmp)
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)} of {len(RESULTS)} claims passed")
    return 1 if failed or not RESULTS else 0


if __name__ == "__main__":
    sys.exit(main())
