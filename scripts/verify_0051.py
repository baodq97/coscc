#!/usr/bin/env python3
"""Proof for the store's `0051_the-board-does-not-show-which-units-have-an-agent-working`.

`intent.md ## Proposed outcome`: a Board tab open and never reloaded; ten steps started in
parallel through `POST /api/board/run` from outside that tab. Within 10 s of each step
starting its card says an agent is working and on which stage; within 10 s of it ending the
sign is gone; and no card without a session ever shows it. One card missing one of the
three is a fail.

    0  every claim held
    1  at least one did not
    2  the environment could not answer: no `node`, `uv` or `git`

**The page's own state, driven through Reflex's own event processor, not a browser**, as
`verify_0024.py` does it. The tab is one `StudioState` that loads, chooses the workspace and
navigates to the Board through its own handlers; its `poll_running` loop is the one the
running app would start. The ten steps go through the ASGI app `coscc/state.py` itself
serves (`API`), never through that state — the "another tab, or the API" case. What is not
exercised is the compiled JavaScript and the socket between it and the state.

Times are taken where they happen: the moment `Service` adds or removes an entry is
recorded by wrapping `_running`, and the cards are read every 0.5 s. So "within 10 s" is
measured to the half-second.

**No session, no quota, no network.** The session is a stand-in that holds each step open
on an `asyncio.Event` until this script lets it go, then replies with an accepted plan. `gh`
is a fake first on `PATH` that refuses everything; `plan` asks it for nothing. Everything
lives under one temporary directory, so `~/.cos` is never opened.

Autopilot (`0043`) is not on `main`, so it is not one of the ways a step is started here.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.proof_harness import EXIT_BROKEN, EXIT_ENV, EXIT_PASS, say  # noqa: E402

PARALLEL = 10          # the intent's number, from `idea.md`'s measurement of 2026-09-24
LIMIT_S = 10.0         # the intent's threshold
SAMPLE_S = 0.5         # how often the cards are read
IDLE = 2               # units that never run anything: the ground for claim (3)

FAKE_GH = """#!/bin/sh
echo "the fake gh for verify_0051 knows nothing: $*" >&2
exit 1
"""


def require_environment() -> None:
    for tool in ("node", "uv", "git"):
        if shutil.which(tool) is None:
            print(f"no {tool} on PATH — this proof cannot answer without it")
            raise SystemExit(EXIT_ENV)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=proof", "-c", "user.email=proof@example.invalid", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout.strip()


class Held:
    """A session per step that waits until `release(unit)`, then writes an accepted plan."""

    def __init__(self) -> None:
        self.gates: dict[str, asyncio.Event] = {}
        self.calls = 0

    def gate(self, unit: str) -> asyncio.Event:
        return self.gates.setdefault(unit, asyncio.Event())

    async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
        self.calls += 1
        unit = Path(cwd).name
        await self.gate(unit).wait()
        yield ("chunk", f"# Plan: {unit}\nIntent: intent.md. Author: verify_0051. Status: accepted.\n\n## Body\n")
        yield ("done", {"session_id": f"verify-0051-{self.calls}", "cost": {}})


class Timed(dict):
    """`Service._running`, remembering when each unit's entry came and went."""

    def __init__(self) -> None:
        super().__init__()
        self.added: dict[str, float] = {}
        self.removed: dict[str, float] = {}

    def __setitem__(self, key, value) -> None:
        super().__setitem__(key, value)
        self.added.setdefault(value["unit"], time.monotonic())

    def pop(self, key, *default):
        found = super().pop(key, *default)
        if isinstance(found, dict) and "unit" in found:
            self.removed[found["unit"]] = time.monotonic()
        return found


class Page:
    """One tab's `StudioState`. `fire` waits for the handler it sent, not for the poll loop."""

    TOKEN = "verify-0051-tab"

    def __init__(self, processor, manager, studio, root_cls) -> None:
        self.processor, self.manager = processor, manager
        self.studio, self.root_cls = studio, root_cls

    async def fire(self, handler: str, settle: float = 1.0, **payload) -> None:
        from reflex.event import Event
        from reflex_base.utils.format import format_event_handler

        name = format_event_handler(self.studio.event_handlers[handler])
        await self.processor.enqueue(self.TOKEN, Event(name=name, payload=payload))
        await asyncio.sleep(settle)

    async def arrive_at(self, href: str, sid: str = "verify", settle: float = 1.0) -> None:
        """`0056`. What a browser sends on landing at `href`: every route's `on_load`,
        `arrive`, with the address and the socket's id in `router_data`. A navigation button
        only returns a redirect since `0056`, and no browser here follows it."""
        from reflex.event import Event
        from reflex.istate.manager.token import BaseStateToken
        from reflex_base.constants import RouteVar
        from reflex_base.utils.format import format_event_handler

        async with self.manager.modify_state(
            BaseStateToken(ident=self.TOKEN, cls=self.root_cls)
        ) as root:
            if not root.router_data:
                # Else the processor rehydrates first, which needs a registered App.
                root.router_data = {RouteVar.CLIENT_TOKEN: self.TOKEN}
        path = href.partition("?")[0]
        router_data = {RouteVar.PATH: path, RouteVar.ORIGIN: href, RouteVar.SESSION_ID: sid,
                       RouteVar.CLIENT_TOKEN: self.TOKEN,
                       RouteVar.HEADERS: {"origin": "http://verify"}}
        name = format_event_handler(self.studio.event_handlers["arrive"])
        await self.processor.enqueue(
            self.TOKEN, Event(name=name, payload={}, router_data=router_data))
        await asyncio.sleep(settle)

    async def read(self) -> dict:
        from reflex.istate.manager.token import BaseStateToken

        async with self.manager.modify_state(
            BaseStateToken(ident=self.TOKEN, cls=self.root_cls)
        ) as root:
            s = await root.get_state(self.studio)
            return {
                "cwd": s.cwd, "screen": s.screen, "error": s.error,
                "cards": {u.id: [(a.label, a.stage, a.agent) for a in u.live] for u in s.units},
            }


async def run(root: Path, workspace: Path) -> bool:
    import httpx
    from reflex.istate.manager.memory import StateManagerMemory
    from reflex.state import State
    from reflex_base.event.processor import BaseStateEventProcessor

    from coscc.state import API, RUNNING_POLL, SERVICE, StudioState

    cwd = str(workspace)
    held = Held()
    SERVICE.sessions = held
    timed = Timed()
    SERVICE._running = timed
    journal = SERVICE._journal()
    key = SERVICE._journal_key(cwd)

    async def make(slug: str) -> str:
        made = await SERVICE.create_unit(cwd, slug, "verify_0051 fixture")
        d = Path(made["path"])
        (d / "intent.md").write_text(
            f"# Intent: {slug}\nAuthor: verify_0051. Type: feat. Status: accepted.\n", encoding="utf-8")
        (d / "spec.md").write_text(
            f"# Spec: {slug}\nAuthor: verify_0051. Status: accepted.\n", encoding="utf-8")
        return made["unit"]

    busy = [await make(f"proof-busy-{i:02d}") for i in range(PARALLEL)]
    idle = [await make(f"proof-idle-{i}") for i in range(IDLE)]
    orphan = await make("proof-orphan")
    # (4) A `start` nobody ended, an hour ago: an app that died mid-step.
    hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    journal.started(key, orphan, "spec", "manual", at=hour_ago)
    # Worktrees first, one at a time: this proof is about the card, not about ten
    # `git worktree add`s racing (`0048`).
    for unit in busy:
        await SERVICE._worktree(cwd, unit)

    ok = True
    manager = StateManagerMemory()
    processor = BaseStateEventProcessor().configure(state_manager=manager)
    async with processor:
        page = Page(processor, manager, StudioState, State)
        # `0056`: the load, the choice of workspace and *Board* are one arrival at the
        # address the last of them redirects to.
        await page.arrive_at(f"/board?ws={workspace.name}", settle=2.0)
        got = await page.read()
        ok &= say(got["cwd"] == cwd and got["screen"] == "board" and set(busy) <= set(got["cards"]),
                  "the tab loaded, chose the workspace and shows the Board, through its own handlers",
                  f"cwd={got['cwd']!r}, screen={got['screen']!r}, error={got['error']!r}")

        shown: dict[str, float] = {}
        cleared: dict[str, float] = {}
        wrong: list[str] = []
        stop = asyncio.Event()

        async def sample() -> None:
            while not stop.is_set():
                now = time.monotonic()
                cards = (await page.read())["cards"]
                live_units = {e["unit"] for e in dict.values(timed)}
                for unit, lines in cards.items():
                    running = [ln for ln in lines if ln[0] == "running"]
                    if unit in busy:
                        if running and running[0][1:] == ("plan", "ᚱ Raidho"):
                            shown.setdefault(unit, now)
                        if not running and unit in timed.removed:
                            cleared.setdefault(unit, now)
                    # (3) A running line with no entry behind it, beyond the lag the intent
                    # allows after an entry goes.
                    if running and unit not in live_units:
                        gone = timed.removed.get(unit)
                        if gone is None or now - gone > LIMIT_S:
                            wrong.append(f"{unit} at +{now - t0:.1f}s")
                    if unit in idle and lines:
                        wrong.append(f"idle {unit} shows {lines}")
                await asyncio.sleep(SAMPLE_S)

        t0 = time.monotonic()
        sampler = asyncio.create_task(sample())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=API), base_url="http://proof", timeout=300
        ) as client:
            async def press(unit: str):
                return await client.post("/api/board/run", json={"cwd": cwd, "unit": unit, "stage": "plan"})

            # (5) Read while the steps are open. A route answer with a session id in it
            # would hand a stop button to anyone who reaches the port (`0034`).
            steps = [asyncio.create_task(press(u)) for u in busy]
            await asyncio.sleep(LIMIT_S + 2)
            body = (await client.get("/api/board/running", params={"cwd": cwd})).text
            ok &= say(set(busy) <= set(timed.added) and "session_id" not in body
                      and "verify-0051-" not in body,
                      "(5) the route answers while ten steps run, with no session id in it",
                      f"entries={len(timed.added)}, body={body[:300]}")

            late = {u: shown[u] - timed.added[u] for u in busy if u in shown and u in timed.added}
            ok &= say(len(late) == PARALLEL and max(late.values()) <= LIMIT_S,
                      f"(1) each of the {PARALLEL} cards shows `ᚱ Raidho plan` within {LIMIT_S:.0f}s of its step starting",
                      f"shown={len(late)}, never={sorted(set(busy) - set(late))}, "
                      f"worst={max(late.values(), default=None)}")
            print(f"      (slowest: {max(late.values(), default=0):.1f}s)")

            for unit in busy:
                held.gate(unit).set()
                await asyncio.sleep(0.7)
            answers = await asyncio.gather(*steps)
            await asyncio.sleep(LIMIT_S + 1)
            ok &= say(all(a.status_code == 200 and '"done"' in a.text for a in answers),
                      "the ten steps ended done", f"{[a.status_code for a in answers]}")

            ended = {u: cleared[u] - timed.removed[u] for u in busy if u in cleared and u in timed.removed}
            ok &= say(len(ended) == PARALLEL and max(ended.values()) <= LIMIT_S and not dict(timed),
                      f"(2) each card loses the sign within {LIMIT_S:.0f}s of its step ending",
                      f"cleared={len(ended)}, never={sorted(set(busy) - set(ended))}, "
                      f"worst={max(ended.values(), default=None)}, left={list(dict.values(timed))}")
            print(f"      (slowest: {max(ended.values(), default=0):.1f}s)")

            ok &= say(not wrong,
                      f"(3) no card showed running without an entry, and the {IDLE} idle cards showed nothing",
                      "; ".join(wrong[:10]))

            # (4) The orphan: ended, unknown, never running; retired by a later start.
            cards = (await page.read())["cards"]
            ok &= say(cards.get(orphan) == [("ended, unknown", "spec", "")],
                      "(4) a start nobody ended shows `ended, unknown`, not running",
                      f"{cards.get(orphan)}")
            journal.started(key, orphan, "plan", "manual")
            journal.finished(key, orphan, "plan", "done")
            await asyncio.sleep(LIMIT_S)
            cards = (await page.read())["cards"]
            ok &= say(cards.get(orphan) == [],
                      "(4) and it goes once a later start of the unit is written",
                      f"{cards.get(orphan)}")

        stop.set()
        await sampler
        # Leave the Board so the tab's loop ends before the processor is closed under it.
        await page.arrive_at(f"/?ws={workspace.name}", settle=RUNNING_POLL + 1)
    return bool(ok)


def main() -> int:
    require_environment()
    with tempfile.TemporaryDirectory(prefix="verify-0051-") as d:
        root = Path(d)
        fakebin = root / "fakebin"
        fakebin.mkdir()
        gh = fakebin / "gh"
        gh.write_text(FAKE_GH, encoding="utf-8")
        gh.chmod(0o755)
        remote = root / "remote.git"
        workspace = root / "work" / "proj"
        workspace.mkdir(parents=True)
        git(root, "init", "-q", "--bare", "-b", "main", str(remote))
        git(workspace, "init", "-q", "-b", "main")
        git(workspace, "commit", "-q", "--allow-empty", "-m", "the only commit")
        git(workspace, "remote", "add", "origin", str(remote))
        git(workspace, "push", "-q", "-u", "origin", "main")
        # Before `coscc` is imported: `coscc/state.py` builds its `SERVICE` from the
        # environment at import, and that must open this directory, not `~/.cos`.
        os.environ["PATH"] = f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}"
        os.environ["COS_DATA_DIR"] = str(root / "data")
        os.environ["COS_WORKING_DIR"] = str(root / "work")
        os.environ["COS_WORKSPACES"] = str(workspace)
        for name, value in (("GIT_AUTHOR_NAME", "proof"), ("GIT_COMMITTER_NAME", "proof"),
                            ("GIT_AUTHOR_EMAIL", "proof@example.invalid"),
                            ("GIT_COMMITTER_EMAIL", "proof@example.invalid")):
            os.environ[name] = value
        for name, fallback in (("COS_PORT", "8790"), ("COS_HOST", "127.0.0.1")):
            if not os.environ.get(name, "").strip():
                os.environ[name] = fallback
        print(f"temporary data root: {root}")
        ok = asyncio.run(run(root, workspace))
        return EXIT_PASS if ok else EXIT_BROKEN


if __name__ == "__main__":
    raise SystemExit(main())
