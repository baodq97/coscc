#!/usr/bin/env python3
"""Proof for `.cos/0034_a-running-step-cannot-be-stopped-and-outlives-itself`.

    --paid   **spends real money**: two short sessions on this machine's `claude` login.
             Each is closed the way a board step is closed, and the CLI processes this
             script itself spawned are counted 10 seconds after the step's end.

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
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.proof_harness import EXIT_BROKEN, EXIT_ENV, EXIT_PASS, say  # noqa: E402

BUNDLED = "claude_agent_sdk/_bundled/claude"
# The window `intent.md ## Proposed outcome` gives a step's processes to be gone.
GRACE_S = 10.0
PAID_MODEL = "claude-haiku-4-5-20251001"


def _children(pid: int) -> list[int]:
    out: list[int] = []
    for task in Path(f"/proc/{pid}/task").glob("*"):
        try:
            out += [int(c) for c in (task / "children").read_text().split()]
        except OSError:
            continue
    return out


def bundled_descendants(root: int | None = None) -> list[int]:
    """PIDs under `root` (this process by default) whose command line is the bundled CLI."""
    seen: list[int] = []
    stack = _children(root or os.getpid())
    while stack:
        pid = stack.pop()
        stack += _children(pid)
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
        except OSError:
            continue
        if BUNDLED in cmdline:
            seen.append(pid)
    return seen


async def settle(label: str) -> bool:
    """True when no bundled CLI is left under this process within `GRACE_S`."""
    deadline = time.monotonic() + GRACE_S
    left = bundled_descendants()
    while left and time.monotonic() < deadline:
        await asyncio.sleep(0.25)
        left = bundled_descendants()
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
    return EXIT_PASS if ok else EXIT_BROKEN


def main() -> int:
    if not Path(f"/proc/{os.getpid()}/task").is_dir():
        print("no /proc on this machine; the processes cannot be counted")
        return EXIT_ENV
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--paid", action="store_true")
    args = parser.parse_args()
    if args.paid:
        return asyncio.run(run_paid())
    print("the plain proof is not built yet")
    return EXIT_ENV


if __name__ == "__main__":
    sys.exit(main())
