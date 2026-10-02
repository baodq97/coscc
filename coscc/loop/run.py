"""The one place the app asks the loop: `python -m coscc.loop` of this app, in a child process.

A child, not a call, so a hung answer is bounded and killed with all it started, and no module
state (`model.LINKS`) lives on between two questions. The interpreter is this process's own and
`-P` keeps the cwd off `sys.path`: a workspace's `coscc/` is never what runs, even with `cwd` in
it. The env is `harness.child_env()`, which carries no secret. `ask` is for the board and a
release, `ask_sync` for `units` and `meta`; both raise `TimeoutError` past `timeout` and `OSError`
when the child cannot start, and leave the exit code to the caller.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from coscc.agent.harness import child_env
from coscc.git.gitops import kill_group

TIMEOUT = 10.0


@dataclass(frozen=True)
class Answer:
    code: int
    out: str
    err: str


def argv(args: list[str]) -> list[str]:
    return [sys.executable, "-P", "-m", "coscc.loop", *args]


def _answer(code: int | None, out: bytes, err: bytes) -> Answer:
    return Answer(code or 0, out.decode(errors="replace"), err.decode(errors="replace"))


async def ask(
    args: list[str],
    *,
    stdin: str | None = None,
    cwd: str | Path | None = None,
    timeout: float = TIMEOUT,
) -> Answer:
    proc = await asyncio.create_subprocess_exec(
        *argv(args),
        cwd=cwd,
        env=child_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL if stdin is None else asyncio.subprocess.PIPE,
        process_group=0,
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(None if stdin is None else stdin.encode()), timeout=timeout
        )
    except asyncio.TimeoutError, asyncio.CancelledError:
        # A cancelled caller leaves no child behind it either, nor what the child started.
        await kill_group(proc)
        raise
    return _answer(proc.returncode, out, err)


def ask_sync(
    args: list[str],
    *,
    stdin: str | None = None,
    cwd: str | Path | None = None,
    timeout: float = TIMEOUT,
) -> Answer:
    with subprocess.Popen(
        argv(args),
        cwd=cwd,
        env=child_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL if stdin is None else subprocess.PIPE,
        process_group=0,
    ) as proc:
        try:
            out, err = proc.communicate(None if stdin is None else stdin.encode(), timeout)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise TimeoutError(
                f"python -m coscc.loop {' '.join(args)}: no answer in {timeout:.0f}s"
            )
    return _answer(proc.returncode, out, err)
