"""One `gh` call: its exit code and both streams, or the reason it could not be made.

`run` is the call; `call` turns what it raises into a sentence; `said` is what a failed call
said. Every caller takes a `Run` so a test can stand in for `gh`.
"""

from __future__ import annotations

import asyncio
import re
from typing import Awaitable, Callable

from coscc.agent.harness import child_env

# Chosen, not measured: matches `coscc/units/board.py` `GATE_TIMEOUT`, the other wait on `gh`.
TIMEOUT = 30.0

# A pull request URL; also keeps a `-` prefix from reaching `gh` as a flag.
PR_URL_RE = re.compile(r"^https://[^\s/]+/[^\s/]+/[^\s/]+/pull/\d+$")

Run = Callable[[list[str], str, "str | None"], Awaitable[tuple[int, str, str]]]


async def run(argv: list[str], cwd: str, stdin: str | None) -> tuple[int, str, str]:
    """One `gh` call: exit code and both streams. Raises what the child raised."""
    proc = await asyncio.create_subprocess_exec(
        "gh",
        *argv,
        cwd=cwd,
        env=child_env(),
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(stdin.encode() if stdin is not None else None), timeout=TIMEOUT
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


async def call(
    runner: Run, argv: list[str], cwd: str, stdin: str | None
) -> tuple[int, str, str] | str:
    """The call's answer, or a reason it could not be had."""
    try:
        return await runner(argv, cwd, stdin)
    except FileNotFoundError:
        return "gh is not installed or not on PATH"
    except asyncio.TimeoutError:
        return f"gh {' '.join(argv[:2])} timed out after {TIMEOUT:.0f}s"
    except (OSError, ValueError) as e:
        return f"could not run gh: {e}"


def said(code: int, out: str, err: str) -> str:
    return (err or out).strip() or f"gh exited {code} and said nothing"
