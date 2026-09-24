#!/usr/bin/env python3
"""Proof for `.cos/0034_a-running-step-cannot-be-stopped-and-outlives-itself`.

    --paid   **spends real money**: three short sessions on this machine's `claude` login.
             Each is closed the way a board step is closed, and the CLI processes this
             script itself spawned are counted 10 seconds after the step's end -- for the
             third, stopped inside a Bash `sleep`, 10 seconds after the Stop.

    0  every claim held
    1  at least one did not
    2  the environment could not answer: no `/proc`, no login

**Counts only this process's own descendants** (`spec.md` R10). A `claude` somebody opened
at a terminal on the same machine is never looked at: the walk starts at `os.getpid()` and
follows `/proc/<pid>/task/*/children`, never a name across the machine.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.proof_harness import EXIT_BROKEN, EXIT_ENV, EXIT_PASS, say  # noqa: E402

BUNDLED = "claude_agent_sdk/_bundled/claude"
# The window `intent.md ## Proposed outcome` gives a step's processes to be gone.
GRACE_S = 10.0
PAID_MODEL = "claude-haiku-4-5-20251001"
# Case (c) holds `Bash`, and on `PAID_MODEL` it could not start: measured 2026-09-24,
# twice, its reply was "API Error: 400 The long context beta is not yet available for this
# subscription." while (a) and (b) on the same model ran. Which call asked for it was not
# found. This is the model `coscc/models.json` gives `impl`, whose steps run `Bash`.
TOOL_MODEL = "claude-sonnet-5[1m]"


def _children(pid: int) -> list[int]:
    out: list[int] = []
    for task in Path(f"/proc/{pid}/task").glob("*"):
        try:
            out += [int(c) for c in (task / "children").read_text().split()]
        except OSError:
            continue
    return out


def descendants(needle: str, root: int | None = None) -> list[int]:
    """PIDs under `root` (this process by default) whose command line contains `needle`."""
    seen: list[int] = []
    stack = _children(root or os.getpid())
    while stack:
        pid = stack.pop()
        stack += _children(pid)
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
        except OSError:
            continue
        if needle in cmdline:
            seen.append(pid)
    return seen


def bundled_descendants(root: int | None = None) -> list[int]:
    """PIDs under `root` (this process by default) whose command line is the bundled CLI."""
    return descendants(BUNDLED, root)


async def settle(label: str, since: float | None = None) -> bool:
    """True when no bundled CLI is left under this process within `GRACE_S` of `since`
    (now, by default)."""
    start = since if since is not None else time.monotonic()
    deadline = start + GRACE_S
    left = bundled_descendants()
    while left and time.monotonic() < deadline:
        await asyncio.sleep(0.25)
        left = bundled_descendants()
    if not left:
        print(f"  {label}: the last one was gone {time.monotonic() - start:.1f}s in")
    return say(not left, f"{label}: {len(left)} bundled claude process(es) left after {GRACE_S:.0f}s",
               f"pids {left}")


async def paid_case(sessions, cwd: str, prompt: str, stop_after_first_chunk: bool) -> dict:
    from coscc.sessions import StepHandle

    handle = StepHandle()
    seen: dict = {"chunks": 0, "done": None, "during": 0}
    agen = sessions.stream(cwd, prompt, max_turns=1, tools=[], model=PAID_MODEL, step=handle)
    try:
        async for kind, payload in agen:
            if kind == "chunk":
                seen["chunks"] += 1
                seen["during"] = max(seen["during"], len(bundled_descendants()))
                if stop_after_first_chunk:
                    # What `Service.stop_step` does: close the handle, then cancel the task.
                    await handle.close()
                    break
            elif kind == "done":
                seen["done"] = payload
    finally:
        await agen.aclose()
    return seen


# What case (c) asks the CLI to run. Distinct enough to find among this process's own
# descendants, and longer than `GRACE_S`, so a step that waited for it would fail.
SLEEP = "sleep 47"


async def paid_mid_tool(sessions, cwd: str) -> dict:
    """Stop a step while its CLI is inside a tool call (`0034` review round 2, F3).

    The clock starts at the Stop, before `handle.close()` is awaited -- not after it
    returns -- so a close that takes long counts against the 10 seconds.
    """
    import claude_agent_sdk as sdk

    from coscc.sessions import StepHandle

    handle = StepHandle()
    seen: dict = {"tool": False, "during": 0, "done": None, "stopped_at": 0.0}

    async def allow(tool: str, tool_input: dict, context) -> object:
        if tool == "Bash" and str((tool_input or {}).get("command", "")).strip() == SLEEP:
            return sdk.PermissionResultAllow()
        return sdk.PermissionResultDeny(message=f"only `{SLEEP}` is allowed in this proof")

    async def consume() -> None:
        async for kind, payload in sessions.stream(
            cwd, f"Run exactly this shell command with the Bash tool: {SLEEP}\nThen reply: done",
            max_turns=3, tools=["Bash"], can_use_tool=allow, model=TOOL_MODEL, step=handle,
        ):
            if kind == "done":
                seen["done"] = payload

    task = asyncio.create_task(consume())
    deadline = time.monotonic() + 120
    while not task.done() and time.monotonic() < deadline:
        if [p for p in descendants(SLEEP) if p not in bundled_descendants()]:
            seen["tool"] = True
            seen["during"] = len(bundled_descendants())
            break
        await asyncio.sleep(0.25)
    seen["stopped_at"] = time.monotonic()
    # What `Service.stop_step` does: close the handle, then cancel the task.
    await handle.close()
    seen["closed_in"] = time.monotonic() - seen["stopped_at"]
    task.cancel()
    try:
        await task
    except BaseException:  # noqa: BLE001 - the cancel, or whatever the stopped step raised
        pass
    return seen


async def run_paid() -> int:
    from coscc.config import from_env
    from coscc.sessions import Sessions

    cwd = str(Path.cwd())
    config = from_env()
    sessions = Sessions(config)
    sessions.membership = lambda _d: True
    ok = True

    try:
        full = await paid_case(sessions, cwd, "Reply with the single word: ready", False)
    except Exception as exc:  # noqa: BLE001 - a login that does not work is the environment
        print(f"the session could not start: {exc!r}")
        return EXIT_ENV
    ok &= say(full["done"] is not None, "(a) a short step ran to its end")
    ok &= await settle("(a) ran to its end")

    stopped = await paid_case(
        sessions, cwd,
        "Count from 1 to 400, one number per line, with no other text.", True,
    )
    ok &= say(stopped["during"] >= 1 and stopped["done"] is None,
              "(b) the step was stopped while its CLI was running",
              f"{stopped['during']} process(es) seen during, done={stopped['done'] is not None}")
    ok &= await settle("(b) stopped after its first chunk")

    mid = await paid_mid_tool(sessions, cwd)
    # Not `done is None`: once the client is closed the real stream ends and yields its
    # `done`, empty, before the cancel lands. The runner reads `stop_requested`, not that.
    ok &= say(mid["tool"] and mid["during"] >= 1,
              f"(c) the step was stopped while its CLI ran `{SLEEP}` through Bash",
              f"tool seen={mid['tool']}, {mid['during']} process(es) during, "
              f"reply {(mid['done'] or {}).get('text', '')[-300:]!r}")
    print(f"  (c) handle.close() returned {mid['closed_in']:.1f}s after the Stop")
    ok &= await settle("(c) stopped inside a tool call, counted from the Stop", mid["stopped_at"])
    # The CLI's own children are outside this unit (`spec.md ## Answers`, câu 2): reported,
    # not judged.
    print(f"  (c) `{SLEEP}` processes still under this one: {len(descendants(SLEEP))}")
    return EXIT_PASS if ok else EXIT_BROKEN


# --- plain: no session, no quota, no network --------------------------------------------


class _Client:
    """Stands in for the SDK client a real step would hand its `StepHandle`."""

    def __init__(self) -> None:
        self.disconnects = 0

    async def disconnect(self) -> None:
        self.disconnects += 1


class _Sessions:
    """A session that sends one chunk, then waits for its unit to be released.

    It does what `Sessions.stream(step=...)` does with a handle: gives it a client, and
    closes it in `finally` however the step ends.
    """

    def __init__(self) -> None:
        self.release: dict[str, asyncio.Event] = {}
        self.handles: dict[str, object] = {}
        self.units: list[str] = []

    def gate(self, unit: str) -> asyncio.Event:
        return self.release.setdefault(unit, asyncio.Event())

    async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
        step = kw.get("step")
        [unit] = [u for u in self.units if u in text]
        if step is not None:
            step.client = _Client()
            self.handles[unit] = step
        try:
            yield ("chunk", "# Spec: a problem\n")
            await asyncio.wait_for(self.gate(unit).wait(), 20)
            yield ("chunk", "Author: proof. Status: accepted.\n")
            yield ("done", {"session_id": f"s-{unit}", "terminal_reason": "success",
                            "cost": {"output_tokens": 3, "turns": 1, "cost_usd": 0.01}})
        finally:
            if step is not None:
                await step.close()


async def _run_dropped_after_first_chunk(app, cwd: str, unit: str) -> list[bytes]:
    """POST /api/board/run over raw ASGI, and hang up after the first line.

    `httpx.ASGITransport` reads the whole body before it returns, so it cannot hang up
    early. This speaks ASGI itself, with an `http.disconnect` sent once a line arrived:
    the message a server sends when the NDJSON client goes away. `spec_version` 2.3 is what
    makes Starlette's `StreamingResponse` listen for it.
    """
    import json

    body = json.dumps({"cwd": cwd, "unit": unit, "stage": "spec"}).encode()
    got_line = asyncio.Event()
    sent: list[bytes] = []
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await got_line.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            sent.append(message["body"])
            got_line.set()

    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": "POST", "scheme": "http", "path": "/api/board/run",
        "raw_path": b"/api/board/run", "query_string": b"", "root_path": "",
        "headers": [(b"content-type", b"application/json"), (b"host", b"proof")],
        "client": ("127.0.0.1", 1), "server": ("proof", 80),
    }
    await asyncio.wait_for(app(scope, receive, send), 20)
    return sent


async def run_plain(root: Path) -> bool:
    import httpx

    from coscc import board as board_reader
    from coscc.api import build
    from coscc.config import Config

    workspace = root / "work" / "proj"
    workspace.mkdir(parents=True)
    config = Config(
        workspaces=(str(workspace),), working_dir=str(root / "work"), data_dir=str(root / "data"),
    )
    app = build(config)
    service = app.state.service
    fake = _Sessions()
    service.sessions = fake
    ws = str(workspace)

    made = []
    for slug in ("runs-to-done", "is-stopped", "loses-its-reader"):
        unit = await service.create_unit(ws, slug, "words for the proof")
        (Path(unit["path"]) / "intent.md").write_text(
            "# Intent: x\nAuthor: proof. Type: fix. Status: accepted.\n", encoding="utf-8"
        )
        made.append(unit)
    u1, u2, u3 = (m["unit"] for m in made)
    paths = {m["unit"]: Path(m["path"]) for m in made}
    fake.units = [u1, u2, u3]
    units_root = service._units_root(ws)
    gate_before = await board_reader.gate(units_root, u2, "plan", repo=None)

    ok = True
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proof")

    async def post_run(unit):
        return await client.post("/api/board/run", json={"cwd": ws, "unit": unit, "stage": "spec"})

    async def listed():
        return {r["unit"] for r in (await client.get("/api/board/running", params={"cwd": ws})).json()}

    async def until(predicate, what):
        for _ in range(400):
            if await predicate():
                return True
            await asyncio.sleep(0.01)
        print(f"timed out waiting for {what}")
        return False

    run1 = asyncio.create_task(post_run(u1))
    run2 = asyncio.create_task(post_run(u2))
    both = await until(lambda: _has(listed(), {u1, u2}), "two steps listed")
    ok &= say(both, "R12 two units' steps are running at the same moment")

    again = await post_run(u1)
    ok &= say(again.status_code == 400, "R11 a second run on a unit already running is a 400",
              f"{again.status_code} {again.text[:200]}")

    stop = await client.post("/api/board/stop", json={"cwd": ws, "unit": u2, "by": "Proof person"})
    ok &= say(stop.status_code == 200 and stop.json().get("stopped_by") == "Proof person",
              "R6 POST /api/board/stop stops the step", f"{stop.status_code} {stop.text[:200]}")
    again_stop = await client.post("/api/board/stop", json={"cwd": ws, "unit": u2, "by": "Someone else"})
    await run2
    ok &= say(again_stop.status_code in (200, 400), "R5 a second Stop is not an error of the server",
              f"{again_stop.status_code}")

    dropped = asyncio.create_task(_run_dropped_after_first_chunk(app, ws, u3))
    lines = await dropped
    ok &= say(len(lines) >= 1, "R3 the NDJSON client of the third step hung up after its first line")
    still = await listed()
    ok &= say(u3 in still, "R3 the third step is still running after its reader went away",
              f"listed {sorted(still)}")

    fake.gate(u1).set()
    fake.gate(u3).set()
    first = await run1
    ok &= say(first.status_code == 200, "the first step's stream ended", str(first.status_code))
    await until(lambda: _empty(listed()), "the running list to empty")

    ends = {u: [] for u in (u1, u2, u3)}
    for r in service._journal().records():
        if r["kind"] == "end" and r["unit"] in ends:
            ends[r["unit"]].append(r)
    for unit, label in ((u1, "U1 ran to its end"), (u3, "U3 lost its reader")):
        written = (paths[unit] / "spec.md").exists()
        outcomes = [e["outcome"] for e in ends[unit]]
        ok &= say(written and outcomes == ["done"], f"{label}: spec.md written and one end, done",
                  f"written={written} ends={outcomes}")
    stopped = ends[u2]
    ok &= say(
        len(stopped) == 1 and stopped[0]["outcome"] == "stopped"
        and stopped[0].get("stopped_by") == "Proof person",
        "R5 R7 U2 has exactly one end: stopped, by the first name", f"{stopped}",
    )
    ok &= say(not (paths[u2] / "spec.md").exists(), "R7 U2 has no spec.md")
    ok &= say(
        bool(stopped) and "cost_usd" not in stopped[0] and "turns" not in stopped[0]
        and stopped[0].get("cost_unknown") is True,
        "R8 U2's end claims no cost it never saw", f"{stopped}",
    )
    gate_after = await board_reader.gate(units_root, u2, "plan", repo=None)
    ok &= say(gate_before == gate_after, "R9 the stop opened and closed no gate",
              f"{gate_before} -> {gate_after}")
    closes = {u: getattr(h.client, "disconnects", None) for u, h in fake.handles.items()}
    ok &= say(sorted(closes) == sorted([u1, u2, u3]) and set(closes.values()) == {1},
              "R1 every step's client was closed exactly once", f"{closes}")
    ok &= say((await listed()) == set(), "R13 the running list is empty at the end")
    await client.aclose()
    return ok


async def _has(listing, want: set) -> bool:
    return want <= await listing


async def _empty(listing) -> bool:
    return not await listing


def main() -> int:
    if not Path(f"/proc/{os.getpid()}/task").is_dir():
        print("no /proc on this machine; the processes cannot be counted")
        return EXIT_ENV
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--paid", action="store_true")
    args = parser.parse_args()
    if args.paid:
        return asyncio.run(run_paid())
    if shutil.which("node") is None:
        print("no `node` on PATH; cos.mjs cannot answer")
        return EXIT_ENV
    with tempfile.TemporaryDirectory(prefix="verify-0034-") as d:
        print(f"temporary data root: {d}")
        return EXIT_PASS if asyncio.run(run_plain(Path(d))) else EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(main())
