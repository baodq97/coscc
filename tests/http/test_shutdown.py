"""`Core.shutdown` returns once nothing it started still writes: a board read's thread, a
tree being removed and a child process are waited for, never left running."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import pytest

from coscc.config import Config
from coscc.features import codegraph
from coscc.git import gh, gitops
from coscc.http import plugin
from coscc.http.app import Core
from coscc.runner import triggers
from coscc.units import board as board_reader
from coscc.units import worktrees
from coscc.units.read import Asked
from tests.github.test_integration import StandIn, git

# A child that outlives any test unless it is killed.
SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]
# One that starts a `SLEEPER`, as `git fetch` starts `ssh` and `index-pack`, and writes its pid
# to the file it is given.
PARENT = [
    sys.executable,
    "-c",
    "import subprocess, sys, time;"
    " p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
    " open(sys.argv[1], 'w').write(str(p.pid)); time.sleep(60)",
]


def files(*roots: Path) -> list[str]:
    return sorted(str(p) for root in roots for p in root.rglob("*"))


def gone(pid: int) -> bool:
    """No such process, or one killed and not yet reaped by whoever inherited it."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].startswith("Z")
    except FileNotFoundError:
        # Reaped between the two looks.
        return True
    except OSError:
        return False


class Children:
    """`asyncio.create_subprocess_exec`, with every child a `SLEEPER` (or `child`, or only those
    whose program is `only`) and each one kept, so a test can see whether it was reaped."""

    def __init__(self, only: str | None = None, child: list[str] = SLEEPER) -> None:
        self.only = only
        self.child = child
        self.procs: list[asyncio.subprocess.Process] = []
        self.started = asyncio.Event()
        self.real = asyncio.create_subprocess_exec

    async def __call__(self, program, *args, **kwargs):
        if self.only is not None and program != self.only:
            return await self.real(program, *args, **kwargs)
        proc = await self.real(*self.child, **{k: v for k, v in kwargs.items() if k != "cwd"})
        self.procs.append(proc)
        self.started.set()
        return proc

    async def reap(self) -> None:
        for proc in self.procs:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            if proc.returncode is None:
                proc.kill()
            await proc.wait()


@pytest.mark.real_loop
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
            workspaces=(self.cwd,),
            working_dir=str(self.working_dir),
            data_dir=str(self.data_dir),
            # The autopilot runs only on a local address.
            host="127.0.0.1",
        )
        self.core = Core(config, StandIn(None))
        self.boards = self.core.boards
        # The thread a board read runs `cos.db` in, held on `release` until a test lets it go.
        self.release = threading.Event()
        self.entered = threading.Event()
        self.running = 0
        real = self.core.ws.snapshot

        def snapshot(*args, **kwargs):
            self.running += 1
            self.entered.set()
            try:
                self.release.wait(10)
                (self.data_dir / "read-wrote").write_text("x", encoding="utf-8")
                return real(*args, **kwargs)
            finally:
                self.running -= 1

        patch = mock.patch.object(self.core.ws, "snapshot", snapshot)
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
        await self.core.shutdown()

    async def a_read_in_its_thread(self) -> asyncio.Task:
        read = self.boards.refresh(self.cwd)
        self.assertTrue(await asyncio.to_thread(self.entered.wait, 5))
        return read

    async def still_running(self, task: asyncio.Task) -> bool:
        done, _ = await asyncio.wait({task}, timeout=0.3)
        return not done

    async def test_a_read_in_its_thread_holds_shutdown_until_the_thread_returned(self):
        read = await self.a_read_in_its_thread()
        down = asyncio.ensure_future(self.core.shutdown())
        self.assertTrue(await self.still_running(down))
        self.release.set()
        await asyncio.wait_for(down, 5)
        self.assertEqual(self.running, 0)
        self.assertTrue(read.cancelled())

    async def test_an_autopilot_pass_in_a_board_read_holds_shutdown_too(self):
        self.core.autopilot.set_setting(self.cwd, "autopilot", True)
        self.assertTrue(await asyncio.to_thread(self.entered.wait, 5))
        [pass_] = self.core.autopilot.tasks.values()
        down = asyncio.ensure_future(self.core.shutdown())
        self.assertTrue(await self.still_running(down))
        self.release.set()
        await asyncio.wait_for(down, 5)
        self.assertEqual(self.running, 0)
        self.assertTrue(pass_.cancelled())

    async def test_a_read_cancelled_twice_still_waits_for_its_thread_and_ends_cancelled(self):
        read = await self.a_read_in_its_thread()
        read.cancel()
        self.assertTrue(await self.still_running(read))
        read.cancel()
        self.assertTrue(await self.still_running(read))
        self.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(read, 5)
        self.assertEqual(self.running, 0)

    def code_index(self, install, installed) -> codegraph.Indexes:
        """The codegraph feature's `Ctx` from the core, its engine `install`ed and `installed`."""
        ctx = plugin.ctx_of(self.core, codegraph.FEATURE)
        plugin.create_tables(ctx.store, codegraph.FEATURE.tables)
        idx = codegraph.Indexes(ctx, self.data_dir / "codegraph", install, installed, mock.Mock())
        patch = mock.patch.object(codegraph, "_indexes", return_value=idx)
        patch.start()
        self.addCleanup(patch.stop)
        return idx

    async def test_a_code_index_refresh_is_cancelled_and_waited_for(self):
        idx = self.code_index(mock.Mock(), lambda home: Path(sys.executable))
        reached = asyncio.Event()

        async def main_tree(*args):
            reached.set()
            await asyncio.Event().wait()

        patch = mock.patch.object(plugin.worktrees, "main_tree", main_tree)
        patch.start()
        self.addCleanup(patch.stop)
        before = asyncio.all_tasks()
        codegraph.on_set(idx.ctx, self.cwd, "on")
        await asyncio.wait_for(reached.wait(), 5)
        # The pick's `ensure` and the refresh it waits on.
        started = asyncio.all_tasks() - before
        await asyncio.wait_for(self.core.shutdown(), 5)
        self.assertEqual(len(started), 2)
        self.assertTrue(all(t.cancelled() for t in started))

    async def test_a_code_index_install_holds_shutdown_until_its_thread_returned(self):
        go, entered = threading.Event(), threading.Event()

        def install(home: Path) -> str:
            entered.set()
            go.wait(10)
            (self.data_dir / "installed").write_text("x", encoding="utf-8")
            return "no engine here"

        idx = self.code_index(install, lambda home: "not installed")
        self.addCleanup(go.set)
        codegraph.on_set(idx.ctx, self.cwd, "on")
        self.assertTrue(await asyncio.to_thread(entered.wait, 5))
        down = asyncio.ensure_future(self.core.shutdown())
        self.assertTrue(await self.still_running(down))
        go.set()
        await asyncio.wait_for(down, 5)
        self.assertTrue((self.data_dir / "installed").exists())
        self.assertEqual(idx.status("k").state, "failed")

    async def test_a_removal_ends_as_it_would_and_none_starts_once_shutdown_began(self):
        self.boards._remove_later(self.cwd, {"name": "0001_done", "why": "finished"})
        [removal] = self.boards._removing.values()
        down = asyncio.ensure_future(self.core.shutdown())
        self.assertTrue(await self.still_running(down))
        # What a read that ends late would start.
        self.boards._remove_later(self.cwd, {"name": "0002_late", "why": "finished"})
        self.removed.set()
        await asyncio.wait_for(down, 5)
        self.assertTrue(removal.done() and not removal.cancelled())
        self.assertEqual(self.boards._removing, {})
        self.assertTrue((self.data_dir / "removed-0001_done").exists())
        self.assertFalse((self.data_dir / "removed-0002_late").exists())

    async def test_no_read_starts_once_shutdown_began(self):
        self.release.set()
        await self.core.shutdown()
        with self.assertRaises(asyncio.CancelledError):
            await self.boards.refresh(self.cwd)
        self.assertFalse(self.entered.is_set())
        self.assertEqual(self.boards.reads, {})

    async def test_a_cancelled_read_kills_and_reaps_its_loop(self):
        self.release.set()
        children = Children(only=sys.executable)
        self.addAsyncCleanup(children.reap)
        with mock.patch.object(asyncio, "create_subprocess_exec", children):
            self.boards.refresh(self.cwd)
            await asyncio.wait_for(children.started.wait(), 5)
            await asyncio.wait_for(self.core.shutdown(), 5)
        [proc] = children.procs
        self.assertIsNotNone(proc.returncode)
        with self.assertRaises(ProcessLookupError):
            os.kill(proc.pid, 0)

    async def test_an_ask_begun_while_shutdown_waits_is_cancelled_and_waited_for(self):
        await self.a_read_in_its_thread()
        down = asyncio.ensure_future(self.core.shutdown())
        self.assertTrue(await self.still_running(down))
        # What a request the server still took starts: a feature's `gh` ask, held.
        never = asyncio.Event()
        asked = self.core.asks.setdefault("probe", Asked())
        ask = asked.ask((self.cwd, "status", "v0.1.0"), never.wait)
        self.release.set()
        await asyncio.wait_for(down, 5)
        self.assertTrue(ask.cancelled())

    async def test_what_outlives_the_deadline_is_logged_by_name(self):
        await self.a_read_in_its_thread()
        with (
            mock.patch("coscc.http.app.SHUTDOWN_WITHIN", 0.3),
            self.assertLogs("coscc.http.app", "WARNING") as logs,
        ):
            await asyncio.wait_for(self.core.shutdown(), 5)
        [line] = logs.output
        self.assertIn("shutdown returns with the board read of ", line)
        self.assertIn("still running after 0.3s", line)

    async def test_a_triggered_run_is_cancelled_and_waited_for(self):
        ended = []

        async def run():
            try:
                await asyncio.Event().wait()
            finally:
                ended.append("end")

        triggers.spawn(asyncio.get_running_loop(), run(), "r1", self.cwd)
        await asyncio.sleep(0)
        await asyncio.wait_for(self.core.shutdown(), 5)
        self.assertEqual((ended, triggers._TASKS), (["end"], set()))

    async def test_nothing_writes_once_shutdown_returned(self):
        self.boards._remove_later(self.cwd, {"name": "0001_done", "why": "finished"})
        await self.a_read_in_its_thread()
        down = asyncio.ensure_future(self.core.shutdown())
        await self.still_running(down)
        self.release.set()
        self.removed.set()
        await asyncio.wait_for(down, 5)
        before = files(self.data_dir, self.working_dir)
        await asyncio.sleep(2)
        self.assertEqual(files(self.data_dir, self.working_dir), before)
        shutil.rmtree(self.data_dir)


@pytest.mark.real_loop
class ACancelledCallKillsItsChild(unittest.IsolatedAsyncioTestCase):
    """Every runner that starts a child a cancellable task waits on kills and reaps it when
    that task is cancelled."""

    async def cancelled(
        self, call, child: list[str] = SLEEPER, ready=lambda: True
    ) -> asyncio.subprocess.Process:
        children = Children(child=child)
        self.addAsyncCleanup(children.reap)
        with mock.patch.object(asyncio, "create_subprocess_exec", children):
            task = asyncio.ensure_future(call())
            await asyncio.wait_for(children.started.wait(), 5)
            deadline = asyncio.get_running_loop().time() + 30
            while not ready() and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.05)
            self.assertTrue(ready(), "the child never said what it started")
            task.cancel()
            # Waited on, not raced: the kill and the reap are real processes on a loop that a
            # loaded machine may not run for seconds.
            await asyncio.wait({task}, timeout=30)
        self.assertTrue(task.done(), "the cancelled call never ended")
        self.assertTrue(task.cancelled(), f"the call ended without being cancelled: {task!r}")
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

    async def test_the_loop(self):
        self.assertReaped(await self.cancelled(lambda: board_reader._run(["status"], 30)))

    async def test_what_the_child_started_is_killed_with_it(self):
        for call in (
            lambda: gitops._run(["git", "fetch"], 30),
            lambda: gitops._run_code(["git", "fetch"], 30),
            lambda: gh.run(["pr", "list"], "."),
            lambda: board_reader._run(["status"], 30),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                said = Path(tmp) / "pid"
                proc = await self.cancelled(
                    call,
                    child=[*PARENT, str(said)],
                    # Cancelled only once the child named what it started.
                    ready=lambda: said.exists() and bool(said.read_text()),
                )
                self.assertReaped(proc)
                grandchild = int(said.read_text())
                deadline = asyncio.get_running_loop().time() + 15
                while not gone(grandchild) and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(0.05)
                self.assertTrue(gone(grandchild), f"pid {grandchild} outlived its parent's kill")


if __name__ == "__main__":
    unittest.main()
