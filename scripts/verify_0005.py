#!/usr/bin/env python3
"""Proof for .cos/0005_silent-concurrent-loss.

Exits 0 only when both claims hold, and prints both even when one fails.

    0  both hold
    1  at least one does not

Claim 1 is a real race between four separate processes. They are started ahead of time and
made to wait for a shared wall-clock moment, because four processes that merely start
"about now" may not overlap at all — and twenty surviving entries would then prove nothing
about locking. `plan.md` names that as the risk worth not writing down.

Claim 2 creates one real session, which spends a little account quota (`spec.md` C5). The
alternative was to fake the live-session state, and that would test a path nobody walks.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cos_baodo.config import from_env
from cos_baodo.service import Invalid, Service
from cos_baodo.sessions import Sessions
from cos_baodo.store import Store

REPO = Path(__file__).resolve().parent.parent

WRITERS = 4  # from intent.md. Change it there, not here.
PER_WRITER = 5
EXPECTED = WRITERS * PER_WRITER

CLONE_URL = "https://github.com/octocat/Hello-World.git"


def say(ok: bool, claim: str, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {claim}{': ' + detail if detail and not ok else ''}")
    return ok


# --------------------------------------------------------------------------
# claim 1 — four processes, one working folder
# --------------------------------------------------------------------------


def race_child(working_dir: str, tag: str, start_at: float) -> int:
    """One writer. Waits for the shared start, then adds its share."""
    # The data root is the working folder here: four processes must share one
    # database, and it must not be the real ~/.cos.
    store = Store(working_dir, working_dir)
    while time.time() < start_at:
        time.sleep(0.001)
    for i in range(PER_WRITER):
        try:
            store.add(f"{tag}-{i}")
        except Exception as e:  # a refusal is information; losing silently is not
            print(f"{tag}-{i}: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
    return 0


def claim_1() -> bool:
    root = Path(tempfile.mkdtemp(prefix="cos0005-race-"))
    try:
        start_at = time.time() + 1.5
        procs = [
            subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()),
                 "--race-child", str(root), f"w{n}", str(start_at)],
                cwd=REPO,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            for n in range(WRITERS)
        ]
        errors = []
        for p in procs:
            _, err = p.communicate(timeout=120)
            if err:
                errors.append(err.decode().strip()[:200])

        names = {e.name for e in Store(root, root).entries()}
        detail = f"{len(names)} of {EXPECTED} survived"
        if errors:
            detail += f"; writers reported: {errors[0]}"
        return say(
            len(names) == EXPECTED,
            f"{EXPECTED} entries survive {WRITERS} processes writing at once",
            detail,
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --------------------------------------------------------------------------
# claim 2 — pull refuses while a session is live, and allows once it is not
# --------------------------------------------------------------------------


async def claim_2() -> bool:
    root = Path(tempfile.mkdtemp(prefix="cos0005-pull-"))
    config = from_env(
        {
            **os.environ,
            "COS_WORKING_DIR": str(root),
            "COS_DATA_DIR": str(root),
            "COS_WORKSPACES": "",
        }
    )
    sessions = Sessions(config)
    service = Service(config, sessions)
    ok = True
    try:
        try:
            await service.add_workspace("hello", repo_url=CLONE_URL)
        except Invalid as e:
            return say(False, "pull is refused while a session is live", f"clone failed: {e}")

        cwd = str(service.store.path_of("hello"))

        # A real session, one short prompt.
        async for _ in service.stream(cwd, "Reply with exactly: READY"):
            pass

        refused = ""
        try:
            await service.pull_workspace("hello")
        except Invalid as e:
            refused = str(e)
        ok &= say(
            bool(refused) and "hello" in refused,
            "pull is refused while a session is live",
            f"it ran anyway (or said nothing useful): {refused!r}",
        )

        await sessions.close_all()

        allowed, why = True, ""
        try:
            await service.pull_workspace("hello")
        except Invalid as e:
            allowed, why = False, str(e)
        ok &= say(allowed, "pull works again once no session is live", why)
        return ok
    finally:
        await sessions.close_all()
        shutil.rmtree(root, ignore_errors=True)


def run() -> int:
    results = [claim_1(), asyncio.run(claim_2())]
    print()
    if all(results):
        print("PASS — concurrent writes keep every entry, and pull will not cut under a session.")
        return 0
    print(f"FAIL — {results.count(False)} of {len(results)} claims did not hold.")
    return 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--race-child":
        sys.exit(race_child(sys.argv[2], sys.argv[3], float(sys.argv[4])))
    sys.exit(run())
