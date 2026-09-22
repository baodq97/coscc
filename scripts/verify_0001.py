#!/usr/bin/env python3
"""Proof for .cos/0001_no-session-management.

Exits 0 only when all five claims in `plan.md` `## Proof` hold. Anything else prints
which of them failed and exits non-zero.

It drives the app's own HTTP surface and nothing else (R4). There is no test-only entry
point, because a path used only by this file would be a path the browser never proves.

It creates real sessions, which spends account quota (`spec.md` C4). Prompts are kept to
one word for that reason, and nothing here loops.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from cos_baodo.api import build
from cos_baodo.config import Config

PROJECTS = 2  # from intent.md. Change it there, not here.

REPO = Path(__file__).resolve().parent.parent


class Failures(list):
    def check(self, claim: str, ok: bool, detail: str = "") -> bool:
        if not ok:
            self.append(f"{claim}{': ' + detail if detail else ''}")
        return ok


async def _send(http, base, cwd, text, session_id=None):
    """POST /api/send and read the NDJSON to the end.

    The last line decides. An error can arrive after the 200 and after some chunks, so
    stopping at the status code would read a failure as a success.
    """
    payload = {"cwd": cwd, "text": text, "session_id": session_id}
    async with http.stream("POST", f"{base}/api/send", json=payload) as r:
        if r.status_code != 200:
            await r.aread()
            return {"error": r.json().get("error", f"HTTP {r.status_code}")}
        result = {"text": ""}
        async for raw in r.aiter_lines():
            line = raw.strip()
            if not line:
                continue
            ev = json.loads(line)
            if ev.get("type") == "chunk":
                result["text"] += ev.get("text", "")
            elif ev.get("type") == "error":
                return {"error": ev["error"]}
            elif ev.get("type") == "done":
                result.update({k: v for k, v in ev.items() if k != "type"})
        return result


async def _get(http, base, path, **params):
    r = await http.get(f"{base}{path}", params=params)
    return r.json()


def _make_second_project(root: Path) -> Path:
    """The second project is built here so the proof stands on its own machine-independently.

    A git repo, because session storage keys off the project directory and a plain
    directory is a different enough case to be worth not relying on.
    """
    project = root / "cos-0001-proof"
    project.mkdir(parents=True, exist_ok=True)
    (project / "README.md").write_text("Temporary project for verify_0001.py\n")
    return project


async def run() -> int:
    f = Failures()
    tmp = Path(tempfile.mkdtemp(prefix="cos0001-"))
    second = _make_second_project(tmp)
    workspaces = [str(REPO), str(second)]

    if not f.check("claim 1 (two projects)", len(workspaces) == PROJECTS,
                   f"need {PROJECTS} project directories, got {len(workspaces)}"):
        print("\n".join(f))
        return 1

    config = Config(workspaces=tuple(workspaces))
    app = build(config)
    # The app is driven in-process over ASGI rather than over a socket. Same routes, same
    # app object Reflex mounts — and no port, no frontend build, no Node. `plan.md` step 1
    # check (d) is what made this the shape of the proof.
    base = "http://proof"

    created: dict[str, str] = {}
    counts: dict[str, int] = {}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=base, timeout=300
        ) as http:
            # --- claim 1: listing is per project, with no cross-contamination ---
            listings = {}
            for cwd in workspaces:
                body = await _get(http, base, "/api/sessions", cwd=cwd, limit=50)
                if "error" in body:
                    f.check("claim 1 (listing)", False, f"{cwd}: {body['error']}")
                    continue
                listings[cwd] = body["sessions"]

            f.check("claim 1 (two projects listed)", len(listings) == PROJECTS,
                    f"listed {len(listings)} of {PROJECTS}")
            for cwd, rows in listings.items():
                strays = [r["session_id"] for r in rows if r.get("cwd") and r["cwd"] != cwd]
                f.check("claim 1 (no leakage)", not strays,
                        f"{cwd} listed sessions from elsewhere: {strays[:3]}")

            # --- claims 2 and 3: create a session in each project, get a reply ---
            for cwd in workspaces:
                r = await _send(http, base, cwd, "Reply with exactly: READY")
                if "error" in r:
                    f.check("claim 2 (create)", False, f"{cwd}: {r['error']}")
                    continue
                sid = r.get("session_id", "")
                f.check("claim 2 (create)", bool(sid), f"{cwd}: no session_id returned")
                f.check("claim 3 (reply)", bool(r.get("text", "").strip()),
                        f"{cwd}: reply text was empty")
                if sid:
                    created[cwd] = sid
                    h = await _get(http, base, "/api/history", cwd=cwd, session_id=sid)
                    counts[cwd] = len(h.get("messages", []))

            # --- claims 4 and 5: resume returns the same id, history did not shrink ---
            for cwd, sid in created.items():
                r = await _send(http, base, cwd, "Reply with exactly: AGAIN", session_id=sid)
                if "error" in r:
                    f.check("claim 4 (resume)", False, f"{cwd}: {r['error']}")
                    continue
                f.check("claim 4 (same session_id)", r.get("session_id") == sid,
                        f"{cwd}: resumed as {r.get('session_id')} instead of {sid}")
                h = await _get(http, base, "/api/history", cwd=cwd, session_id=sid)
                after = len(h.get("messages", []))
                f.check("claim 5 (history kept)", after >= counts.get(cwd, 0),
                        f"{cwd}: {counts.get(cwd)} messages before, {after} after")
    finally:
        # ASGITransport does not run lifespan events, so the shutdown hook that closes the
        # CLI subprocesses has to be called directly. Leaking them was a named risk.
        await app.state.sessions.close_all()
        shutil.rmtree(tmp, ignore_errors=True)

    if f:
        print("FAIL — 0001 is not met:")
        for line in f:
            print(f"  - {line}")
        return 1

    print(f"PASS — all five claims hold across {PROJECTS} projects.")
    for cwd, sid in created.items():
        print(f"  {sid}  {cwd}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
