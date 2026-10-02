"""`Service.shutdown` returns once nothing it started still writes: a board read's thread, a
tree being removed and a child process are waited for, never left running."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from coscc.config import Config
from coscc.git import gh, gitops
from coscc.service import Service
from coscc.units import board as board_reader
from coscc.units import worktrees
from tests.service.test_steps_integrate import StandIn, git

# A child that outlives any test unless it is killed.
SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


def files(*roots: Path) -> list[str]:
    return sorted(str(p) for root in roots for p in root.rglob("*"))


class Children:
    """`asyncio.create_subprocess_exec`, with every child a `SLEEPER` (or only those whose
    program is `only`) and each one kept, so a test can see whether it was reaped."""

    def __init__(self, only: str | None = None) -> None:
        self.only = only
        self.procs: list[asyncio.subprocess.Process] = []
        self.started = asyncio.Event()
        self.real = asyncio.create_subprocess_exec

    async def __call__(self, program, *args, **kwargs):
        if self.only is not None and program != self.only:
            return await self.real(program, *args, **kwargs)
        proc = await self.real(*SLEEPER, **{k: v for k, v in kwargs.items() if k != "cwd"})
        self.procs.append(proc)
        self.started.set()
        return proc

    async def reap(self) -> None:
        for proc in self.procs:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()


class ShutdownWaits(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.workspace = root / "work" / "proj"
        self.workspace.mkdir(parents=True)
        git(self.workspace, "init", "-q", "-b", "main")
        git(self.workspace, "commit", "-q", "--allow-empty", "-m", "seed")
        self.cwd = str(self.workspace)
        self.data_dir, self.working_dir = root / "data", root / "work"
        self.data_dir.mkdir()
        config = Config(
            workspaces=(self.cwd,), working_dir=str(self.working_dir), data_dir=str(self.data_dir)
        )
        self.service = Service(config, StandIn(None))
        self.boards = self.service.boards
        # The thread a board read runs `cos.db` in, held on `release` until a test lets it go.
        self.release = threading.Event()
        self.entered = threading.Event()
        self.running = 0
        real = self.service.ws.snapshot

        def snapshot(*args, **kwargs):
            self.running += 1
            self.entered.set()
            try:
                self.release.wait(10)
                (self.data_dir / "read-wrote").write_text("x", encoding="utf-8")
                return real(*args, **kwargs)
            finally:
                self.running -= 1

        patch = mock.patch.object(self.service.ws, "snapshot", snapshot)
        patch.start()
        self.addCleanup(patch.stop)
        # A finished unit's tree being removed, held on `removed` until a test lets it go.
        self.removed = asyncio.Event()

        async def remove_if_finished(cwd, unit, info, data_dir):
            await self.removed.wait()
            (Path(data_dir) / f"removed-{unit}").write_text("x", encoding="utf-8")
            return {"removed": False}

        patch = mock.patch.object(worktrees, "remove_if_finished", remove_if_finished)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.release.set)

    async def asyncTearDown(self):
        self.removed.set()
        self.release.set()
        await self.service.shutdown()

    async def a_read_in_its_thread(self) -> asyncio.Task:
        read = self.boards.refresh(self.cwd)
        self.assertTrue(await asyncio.to_thread(self.entered.wait, 5))
        return read

    async def still_running(self, task: asyncio.Task) -> bool:
        done, _ = await asyncio.wait({task}, timeout=0.3)
        return not done

    async def test_r2_a_read_in_its_thread_holds_shutdown_until_the_thread_returned(self):
        read = await self.a_read_in_its_thread()
        down = asyncio.ensure_future(self.service.shutdown())
        self.assertTrue(await self.still_running(down))
        self.release.set()
        await asyncio.wait_for(down, 5)
        self.assertEqual(self.running, 0)
        self.assertTrue(read.cancelled())

    async def test_r2_a_read_cancelled_twice_still_waits_for_its_thread_and_ends_cancelled(self):
        read = await self.a_read_in_its_thread()
        read.cancel()
        self.assertTrue(await self.still_running(read))
        read.cancel()
        self.assertTrue(await self.still_running(read))
        self.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(read, 5)
        self.assertEqual(self.running, 0)

    async def test_r3_a_removal_ends_as_it_would_and_none_starts_once_shutdown_began(self):
        self.boards._remove_later(self.cwd, {"name": "0001_done", "why": "finished"})
        [removal] = self.boards._removing.values()
        down = asyncio.ensure_future(self.service.shutdown())
        self.assertTrue(await self.still_running(down))
        # What a read that ends late would start.
        self.boards._remove_later(self.cwd, {"name": "0002_late", "why": "finished"})
        self.removed.set()
        await asyncio.wait_for(down, 5)
        self.assertTrue(removal.done() and not removal.cancelled())
        self.assertEqual(self.boards._removing, {})
        self.assertTrue((self.data_dir / "removed-0001_done").exists())
        self.assertFalse((self.data_dir / "removed-0002_late").exists())

    async def test_r3_no_read_starts_once_shutdown_began(self):
        self.release.set()
        await self.service.shutdown()
        with self.assertRaises(asyncio.CancelledError):
            await self.boards.refresh(self.cwd)
        self.assertFalse(self.entered.is_set())
        self.assertEqual(self.boards.reads, {})

    async def test_r4_a_cancelled_read_kills_and_reaps_its_cos_mjs(self):
        self.release.set()
        children = Children(only="node")
        self.addAsyncCleanup(children.reap)
        with mock.patch.object(asyncio, "create_subprocess_exec", children):
            self.boards.refresh(self.cwd)
            await asyncio.wait_for(children.started.wait(), 5)
            await asyncio.wait_for(self.service.shutdown(), 5)
        [proc] = children.procs
        self.assertIsNotNone(proc.returncode)
        with self.assertRaises(ProcessLookupError):
            os.kill(proc.pid, 0)

    async def test_r6_nothing_writes_once_shutdown_returned(self):
        self.boards._remove_later(self.cwd, {"name": "0001_done", "why": "finished"})
        await self.a_read_in_its_thread()
        down = asyncio.ensure_future(self.service.shutdown())
        await self.still_running(down)
        self.release.set()
        self.removed.set()
        await asyncio.wait_for(down, 5)
        before = files(self.data_dir, self.working_dir)
        await asyncio.sleep(2)
        self.assertEqual(files(self.data_dir, self.working_dir), before)
        shutil.rmtree(self.data_dir)


class ACancelledCallKillsItsChild(unittest.IsolatedAsyncioTestCase):
    """Every runner that starts a child a cancellable task waits on kills and reaps it when
    that task is cancelled."""

    async def cancelled(self, call) -> asyncio.subprocess.Process:
        children = Children()
        self.addAsyncCleanup(children.reap)
        with mock.patch.object(asyncio, "create_subprocess_exec", children):
            task = asyncio.ensure_future(call())
            await asyncio.wait_for(children.started.wait(), 5)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.assertTrue(task.cancelled())
        [proc] = children.procs
        return proc

    def assertReaped(self, proc: asyncio.subprocess.Process) -> None:
        self.assertIsNotNone(proc.returncode)
        with self.assertRaises(ProcessLookupError):
            os.kill(proc.pid, 0)

    async def test_git(self):
        self.assertReaped(await self.cancelled(lambda: gitops._run(["git", "status"], 30)))

    async def test_git_with_its_code(self):
        self.assertReaped(await self.cancelled(lambda: gitops._run_code(["git", "status"], 30)))

    async def test_gh(self):
        self.assertReaped(await self.cancelled(lambda: gh.run(["pr", "list"], ".")))

    async def test_cos_mjs(self):
        self.assertReaped(await self.cancelled(lambda: board_reader._run(["cos.mjs"], 30)))


if __name__ == "__main__":
    unittest.main()
