#!/usr/bin/env python3
"""Proof for .cos/0003_no-workspace-management.

Exits 0 only when all three claims in `intent.md` hold. Every claim prints its own
verdict even when an earlier one has already failed — `plan.md` names that as the
mitigation for putting three claims in one outcome: a single red link must not hide which
parts stood up.

It drives the app's own surfaces and nothing else: the filesystem for claim 1, the service
gate for claim 2, and the HTTP routes for claim 3. There is no test-only entry point.

Claim 3 clones from the network and creates two real sessions, which spends account quota
(`spec.md` C10). Prompts are one word for that reason, and nothing here loops.
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
from cos_baodo.config import from_env
from cos_baodo.service import Invalid, Service
from cos_baodo.sessions import Sessions
from cos_baodo.store import Store

REPO = Path(__file__).resolve().parent.parent

WORKSPACES = 2  # from intent.md. Change it there, not here.

# Small, public, long-lived. https only — `spec.md` R15 puts private repos out of scope.
REPOS = [
    ("hello", "https://github.com/octocat/Hello-World.git"),
    ("spoon", "https://github.com/octocat/Spoon-Knife.git"),
]


class Claim:
    def __init__(self, number: int, title: str):
        self.number, self.title = number, title
        self.failures: list[str] = []

    def check(self, what: str, ok: bool, detail: str = "") -> bool:
        if not ok:
            self.failures.append(f"{what}{': ' + detail if detail else ''}")
        return ok

    def report(self) -> bool:
        if self.failures:
            print(f"FAIL  claim {self.number}: {self.title}")
            for line in self.failures:
                print(f"        - {line}")
            return False
        print(f"PASS  claim {self.number}: {self.title}")
        return True


# --------------------------------------------------------------------------
# claim 1 — no hand-written markup
# --------------------------------------------------------------------------


def claim_1() -> Claim:
    c = Claim(1, "no hand-written HTML or CSS serves the app page")
    found = sorted(
        str(p.relative_to(REPO))
        for p in list(REPO.glob("cos_baodo/**/*.html")) + list(REPO.glob("cos_baodo/**/*.css"))
    )
    c.check("no markup under cos_baodo/", not found, ", ".join(found))
    c.check(
        "0002's page is gone",
        not (REPO / "cos_baodo" / "public" / "index.html").exists(),
    )
    return c


# --------------------------------------------------------------------------
# claim 2 — a hand-edited store cannot widen the boundary
# --------------------------------------------------------------------------


def claim_2() -> Claim:
    c = Claim(2, "a hand-edited store cannot widen the workspace boundary")
    root = Path(tempfile.mkdtemp(prefix="cos0003-gate-"))
    try:
        (root / "real").mkdir()
        config = from_env({"COS_WORKING_DIR": str(root), "COS_DATA_DIR": str(root)})
        service = Service(config, Sessions(config))

        service.store.add("real")
        c.check("a real workspace passes the gate", _passes(service, str(root / "real")))

        # Hand-edit: the store is the user's, and this is what they could type into it
        # with `sqlite3` on the command line. Since `0011` that is a table rather than a
        # file, and the claim is unchanged: the names are rejected on read.
        hand = Store(root, root)
        with hand.data.write() as conn:
            for name in ("/etc", "../../etc", "../.."):
                conn.execute(
                    "INSERT OR REPLACE INTO workspaces (root, name, label, added_at) "
                    "VALUES (?, ?, '', '2026-01-01T00:00:00+00:00')",
                    (str(hand.working_dir), name),
                )
        fresh = Service(config, Sessions(config))
        for outside in ("/etc", str(root.parent), str(REPO)):
            c.check(
                f"refuses {outside}",
                not _passes(fresh, outside),
                "the gate accepted a directory outside the working folder",
            )
        c.check(
            "a subdirectory nobody added is refused",
            not _passes(fresh, str(root / "unlisted")),
        )
        c.check("the real workspace still passes", _passes(fresh, str(root / "real")))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return c


def _passes(service: Service, directory: str) -> bool:
    """Every working path, not just one. R21 is about the gate, not about a function."""
    for call in (
        lambda: service.sessions_for(directory),
        lambda: service.history(directory, "x"),
        lambda: service.check_send(directory, "hi"),
    ):
        try:
            call()
        except Invalid:
            return False
        except Exception:
            # Anything else means it got past the gate and failed later, which counts as
            # passing the gate — the thing being measured here.
            continue
    return True


# --------------------------------------------------------------------------
# claim 3 — the workspace chain
# --------------------------------------------------------------------------


async def _json(http, method, path, **kw):
    r = await http.request(method, path, **kw)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"error": r.text[:200]}


async def _send(http, cwd, text):
    async with http.stream("POST", "/api/send", json={"cwd": cwd, "text": text}) as r:
        if r.status_code != 200:
            await r.aread()
            return {"error": r.json().get("error", f"HTTP {r.status_code}")}
        out = {"text": ""}
        async for raw in r.aiter_lines():
            line = raw.strip()
            if not line:
                continue
            ev = json.loads(line)
            if ev.get("type") == "chunk":
                out["text"] += ev.get("text", "")
            elif ev.get("type") == "error":
                return {"error": ev["error"]}
            elif ev.get("type") == "done":
                out.update({k: v for k, v in ev.items() if k != "type"})
        return out


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proof", timeout=300
    )


async def claim_3() -> Claim:
    c = Claim(3, "workspaces can be cloned, labelled, used, removed — across restarts")
    root = Path(tempfile.mkdtemp(prefix="cos0003-ws-"))
    # Set once. Nothing below changes the environment again: "restart" has to mean the
    # state came off disk, not out of a fresh Config someone built by hand.
    env = {"COS_WORKING_DIR": str(root), "COS_DATA_DIR": str(root)}
    built: list = []

    def start():
        app = build(from_env(env))
        built.append(app)
        return app

    try:
        app = start()
        async with _client(app) as http:
            for name, url in REPOS:
                status, body = await _json(
                    http, "POST", "/api/workspaces", json={"name": name, "repo_url": url}
                )
                c.check(f"clone {name}", status == 200, str(body.get("error", "")))

            status, body = await _json(http, "GET", "/api/workspaces")
            c.check(
                f"count is {WORKSPACES} after cloning",
                body.get("count") == WORKSPACES,
                f"got {body.get('count')}",
            )
            c.check(
                "no COS_WORKSPACES leaked in",
                all(r["source"] == "store" for r in body.get("workspaces", [])),
            )

            status, _ = await _json(
                http, "PATCH", "/api/workspaces/hello", json={"label": "Ngôi nhà"}
            )
            c.check("set a label", status == 200)

        # --- restart, same environment ---
        app = start()
        async with _client(app) as http:
            status, body = await _json(http, "GET", "/api/workspaces")
            c.check(
                f"count is still {WORKSPACES} after restart",
                body.get("count") == WORKSPACES,
                f"got {body.get('count')}",
            )
            labels = {r["name"]: r["label"] for r in body.get("workspaces", [])}
            c.check(
                "the label survived the restart",
                labels.get("hello") == "Ngôi nhà",
                f"got {labels.get('hello')!r}",
            )

            status, body = await _json(http, "POST", "/api/workspaces/hello/pull")
            c.check("pull latest", status == 200, str(body.get("error", "")))

            # Real sessions from here. Two of them, one word each.
            paths = {
                r["name"]: r["path"]
                for r in (await _json(http, "GET", "/api/workspaces"))[1]["workspaces"]
            }
            for name, _ in REPOS:
                r = await _send(http, paths[name], "Reply with exactly: READY")
                if "error" in r:
                    c.check(f"session in {name}", False, r["error"])
                    continue
                c.check(f"session in {name} returned an id", bool(r.get("session_id")))
                c.check(f"session in {name} replied", bool(r.get("text", "").strip()))

            status, body = await _json(http, "DELETE", "/api/workspaces/spoon")
            c.check("remove one", status == 200, str(body.get("error", "")))

            status, body = await _json(http, "GET", "/api/workspaces")
            c.check("count is 1 after removal", body.get("count") == 1, f"got {body.get('count')}")
            c.check(
                "removal did not touch the directory",
                (root / "spoon").is_dir(),
                "the directory was deleted; spec.md R18 says de-list only",
            )

        # --- restart again ---
        app = start()
        async with _client(app) as http:
            status, body = await _json(http, "GET", "/api/workspaces")
            c.check(
                "count is still 1 after the second restart",
                body.get("count") == 1,
                f"got {body.get('count')}",
            )

        leftovers = sorted(p.name for p in root.glob(".cos-clone-*"))
        c.check("no clone staging directory left behind", not leftovers, ", ".join(leftovers))
    finally:
        for app in built:
            await app.state.sessions.close_all()
        shutil.rmtree(root, ignore_errors=True)
    return c


# --------------------------------------------------------------------------


async def run() -> int:
    claims = [claim_1(), claim_2(), await claim_3()]
    print()
    ok = [c.report() for c in claims]
    print()
    if all(ok):
        print(f"PASS — all three claims of 0003 hold ({WORKSPACES} workspaces).")
        return 0
    print(f"FAIL — {ok.count(False)} of {len(ok)} claims did not hold.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
