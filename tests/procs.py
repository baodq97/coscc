"""Waiting on the child processes a test started, and no others."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path


def sleeping(base: str) -> str:
    """A `sleep` duration only this test process uses, so a scan finds its own children and no
    parallel worker's."""
    return f"{base}{os.getpid()}"


def running(marker: str) -> list[str]:
    """Command lines of the processes whose argv holds `marker`, read from /proc."""
    found = []
    for proc in Path("/proc").glob("[0-9]*"):
        if proc.name == str(os.getpid()):
            continue
        try:
            argv = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if marker in argv:
            found.append(argv)
    return found


def wait_until(done: Callable[[], bool], timeout: float = 10.0) -> bool:
    """Poll `done` every 50 ms until it holds or `timeout` passes; the last answer."""
    end = time.monotonic() + timeout
    while not done():
        if time.monotonic() > end:
            return False
        time.sleep(0.05)
    return True


def left_running(marker: str, timeout: float = 10.0) -> list[str]:
    """What still runs under `marker` once the kill has had `timeout` s to land; [] when none."""
    wait_until(lambda: not running(marker), timeout)
    return running(marker)
