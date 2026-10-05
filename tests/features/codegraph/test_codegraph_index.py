"""`coscc/features/codegraph/index.py`: when the index of a workspace's main is built or synced,
and what a run may read meanwhile. The engine is a fake: nothing here starts node."""

from __future__ import annotations

import asyncio
import tempfile
import threading
import time
import unittest
from collections.abc import Mapping
from pathlib import Path
from unittest import mock

from coscc.store.db import Data
from coscc.features.codegraph import (
    DB_FILE,
    INDEX_TABLE,
    NO_NPM,
    Indexes,
    Ready,
)
from coscc.kernel import Units
from tests.features.ctx import ctx_for

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40


class Main:
    """The kernel's `main_tree`: a real directory per workspace and a sha a test moves."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.sha = SHA_A
        self.error: Exception | None = None
        self.asked: list[str] = []

    async def __call__(self, workspace: str) -> tuple[str, str]:
        self.asked.append(workspace)
        if self.error:
            raise self.error
        tree = self.base / "trees" / workspace
        tree.mkdir(parents=True, exist_ok=True)
        return str(tree), self.sha


class Engine:
    """The bridge: `index` makes the db, `sync` touches it, and a test can hold or break a call."""

    def __init__(self) -> None:
        self.ops: list[tuple[str, str]] = []
        self.gate: threading.Event | None = None
        self.entered = threading.Event()
        self.error: Exception | None = None
        self.seen: list[str] = []
        self.probe = None
        self.running = 0
        self.most = 0
        self.guard = threading.Lock()
        self.work_s = 0.0

    def __call__(
        self, home: Path, binary: Path, op: str, root: str, args: Mapping[str, object], t: float
    ) -> object:
        with self.guard:
            self.ops.append((op, root))
            self.running += 1
            self.most = max(self.most, self.running)
        try:
            if self.probe:
                self.seen.append(self.probe())
            self.entered.set()
            if self.gate:
                self.gate.wait(10)
            if self.work_s:
                time.sleep(self.work_s)  # widens an overlap a missing lock would show
            if self.error:
                raise self.error
            db = Path(root) / DB_FILE
            db.parent.mkdir(parents=True, exist_ok=True)
            db.write_text(op)
            return {}
        finally:
            with self.guard:
                self.running -= 1


class Bed(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.data = Data(self.base / "data")
        with self.data.write() as conn:
            conn.execute(INDEX_TABLE)
        self.main = Main(self.base)
        self.engine = Engine()
        self.engine.probe = lambda: self.indexes.status("proj").state
        self.binary = Path("/fake/node")
        self.gate_install: threading.Event | None = None
        self.installs = 0
        self.checks = 0
        self.present = True
        self.indexes = self.make()

    def make(self, wait_s: float = 5.0) -> Indexes:
        ctx = ctx_for(units=Units(str, None, self.main), store=self.data)
        return Indexes(ctx, self.base, self._install, self._installed, self.engine, wait_s=wait_s)

    def _installed(self, home: Path) -> Path | str:
        self.checks += 1
        return self.binary if self.present else "Node is not installed."

    def _install(self, home: Path) -> Path | str:
        self.installs += 1
        if self.gate_install:
            self.gate_install.wait(10)
        self.present = True
        return self.binary

    def tree(self, workspace: str = "proj") -> str:
        return str(self.base / "trees" / workspace)

    async def entered(self) -> None:
        self.assertTrue(await asyncio.to_thread(self.engine.entered.wait, 5))
        self.engine.entered.clear()


class TheFirstEnsureBuilds(Bed):
    async def test_it_builds_with_the_index_op_and_the_row_ends_ready_at_main(self):
        got = await self.indexes.ensure("proj")
        self.assertIsInstance(got, Ready)
        self.assertEqual((got.root, got.sha), (self.tree(), SHA_A))
        self.assertGreaterEqual(got.wait_ms, 0)
        self.assertEqual(self.engine.ops, [("index", self.tree())])
        self.assertEqual(self.engine.seen, ["building"])
        status = self.indexes.status("proj")
        self.assertEqual((status.state, status.sha), ("ready", SHA_A))
        self.assertTrue(status.at)

    async def test_a_workspace_never_asked_has_no_state(self):
        self.assertEqual(self.indexes.status("proj").state, "")
        self.assertEqual(self.indexes.status("proj").sha, "")


class AMovedMainSyncs(Bed):
    async def test_a_new_sha_runs_sync_and_the_same_sha_runs_nothing(self):
        await self.indexes.ensure("proj")
        before = self.indexes.status("proj")
        await self.indexes.ensure("proj")
        self.assertEqual(self.engine.ops, [("index", self.tree())])
        self.assertEqual(self.indexes.status("proj"), before)
        self.main.sha = SHA_B
        got = await self.indexes.ensure("proj")
        self.assertEqual(got.sha, SHA_B)
        self.assertEqual(self.engine.ops[-1], ("sync", self.tree()))
        self.assertEqual(len(self.engine.ops), 2)


class AFailureIsNeverSilent(Bed):
    async def test_a_failed_sync_keeps_the_old_sha_and_a_later_success_clears_it(self):
        await self.indexes.ensure("proj")
        self.main.sha = SHA_B
        self.engine.error = RuntimeError(f"engine died in {self.tree()} at {SHA_B}")
        with self.assertLogs("coscc.features.codegraph", "ERROR"):
            got = await self.indexes.ensure("proj")
        # What is built is still readable, at the sha it is at.
        self.assertEqual((got.root, got.sha), (self.tree(), SHA_A))
        status = self.indexes.status("proj")
        self.assertEqual((status.state, status.sha), ("failed", SHA_A))
        self.assertIn("engine died", status.reason)
        self.assertNotIn(self.tree(), status.reason)
        self.assertNotIn(SHA_B, status.reason)
        self.engine.error = None
        got = await self.indexes.ensure("proj")
        self.assertEqual(got.sha, SHA_B)
        status = self.indexes.status("proj")
        self.assertEqual((status.state, status.sha, status.reason), ("ready", SHA_B, ""))

    async def test_a_build_that_fails_with_nothing_built_gives_the_reason(self):
        self.engine.error = RuntimeError("out of memory")
        with self.assertLogs("coscc.features.codegraph", "ERROR"):
            got = await self.indexes.ensure("proj")
        self.assertIsInstance(got, str)
        self.assertIn("out of memory", got)

    async def test_git_refusing_is_a_failure_with_a_reason_too(self):
        self.main.error = RuntimeError("fetch refused")
        with self.assertLogs("coscc.features.codegraph", "ERROR"):
            got = await self.indexes.ensure("proj")
        self.assertIn("fetch refused", got)
        self.assertEqual(self.engine.ops, [])
        self.assertEqual(self.indexes.status("proj").state, "failed")


class ASlowRefreshDoesNotHoldARun(Bed):
    async def test_past_the_wait_the_index_as_it_is_is_used_and_the_refresh_finishes(self):
        await self.indexes.ensure("proj")
        self.engine.gate = threading.Event()
        self.main.sha = SHA_B
        self.indexes.wait_s = 0.05
        got = await self.indexes.ensure("proj")
        self.assertEqual(got.sha, SHA_A)
        self.assertEqual(self.indexes.status("proj").state, "building")
        self.engine.gate.set()
        await self.indexes.settle()
        status = self.indexes.status("proj")
        self.assertEqual((status.state, status.sha), ("ready", SHA_B))

    async def test_with_no_index_yet_the_run_gets_a_reason(self):
        self.engine.gate = threading.Event()
        self.indexes.wait_s = 0.05
        got = await self.indexes.ensure("proj")
        self.assertIsInstance(got, str)
        self.assertIn("still being built", got)
        self.engine.gate.set()
        await self.indexes.settle()
        self.assertEqual(self.indexes.status("proj").state, "ready")


class RefreshesAreShared(Bed):
    async def test_two_asks_at_once_make_one_bridge_call(self):
        first, second = await asyncio.gather(
            self.indexes.ensure("proj"), self.indexes.ensure("proj")
        )
        self.assertEqual(first.sha, second.sha)
        self.assertEqual(len(self.engine.ops), 1)

    async def test_two_workspaces_never_build_at_the_same_time(self):
        self.engine.work_s = 0.05
        one, two = await asyncio.gather(self.indexes.ensure("a"), self.indexes.ensure("b"))
        self.assertIsInstance(one, Ready)
        self.assertIsInstance(two, Ready)
        self.assertEqual(len(self.engine.ops), 2)
        self.assertEqual(self.engine.most, 1)


class AnEngineNotThereYet(Bed):
    async def test_the_install_starts_once_is_reported_and_then_the_index_builds(self):
        self.present = False
        self.gate_install = threading.Event()
        got = await self.indexes.ensure("proj")
        self.assertEqual(got, "Node is not installed.")
        self.assertEqual(self.indexes.status("proj").state, "installing")
        again = await self.indexes.ensure("proj")
        self.assertIsInstance(again, str)
        self.gate_install.set()
        await self.indexes.settle()
        self.assertEqual(self.installs, 1)
        self.assertEqual(self.engine.ops, [])
        got = await self.indexes.ensure("proj")
        self.assertIsInstance(got, Ready)
        self.assertEqual(self.indexes.status("proj").state, "ready")

    async def test_a_failed_install_is_reported_with_its_reason(self):
        self.present = False
        self.indexes._install = lambda home: "No network to fetch the engine."
        with self.assertLogs("coscc.features.codegraph", "WARNING"):
            await self.indexes.ensure("proj")
            await self.indexes.settle()
        status = self.indexes.status("proj")
        self.assertEqual(
            (status.state, status.reason), ("failed", "No network to fetch the engine.")
        )

    async def test_a_failed_install_is_not_tried_again_by_a_run_only_by_a_retry(self):
        self.present = False
        self.indexes._install = lambda home: self._count("No network to fetch the engine.")
        with self.assertLogs("coscc.features.codegraph", "WARNING"):
            await self.indexes.ensure("proj")
            await self.indexes.settle()
            await self.indexes.ensure("proj")
            await self.indexes.settle()
            self.assertEqual(self.installs, 1)
            self.indexes.retry()
            await self.indexes.ensure("proj")
            await self.indexes.settle()
        self.assertEqual(self.installs, 2)

    def _count(self, reason: str) -> str:
        self.installs += 1
        return reason

    async def test_the_install_waits_for_a_build_running(self):
        self.engine.gate = threading.Event()
        self.indexes.wait_s = 0.05
        await self.indexes.ensure("proj")
        await self.entered()
        order: list[str] = []
        self.indexes._install = lambda home: order.append("installed") or self.binary
        self.indexes._binary = None
        self.present = False
        self.indexes.start_install()
        self.assertEqual(self.indexes.status("proj").state, "installing")
        order.append("released")
        self.engine.gate.set()
        await self.indexes.settle()
        self.assertEqual(order, ["released", "installed"])


class AnInstallThatCannotRun(Bed):
    """An install there whose bundled Node is missing or wrong is checked once, locks the
    choice and is never installed again by a run."""

    def there(self) -> None:
        (self.base / "node_modules").mkdir()
        (self.base / "package-lock.json").write_text("{}")
        self.present = False

    async def test_it_is_checked_once_locks_and_installs_nothing(self):
        self.there()
        self.assertEqual(self.indexes.lock(), "")
        for _ in range(3):
            self.assertEqual(await self.indexes.ensure("proj"), "Node is not installed.")
        await self.indexes.settle()
        self.assertEqual((self.checks, self.installs), (1, 0))
        self.assertEqual(self.indexes.lock(), "Node is not installed.")
        self.assertEqual(self.engine.ops, [])

    async def test_the_lock_runs_no_check(self):
        self.there()
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(self.indexes.lock(), "")
        self.assertEqual(self.checks, 0)

    async def test_no_npm_and_nothing_installed_locks_it(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(self.indexes.lock(), NO_NPM)
        with mock.patch("shutil.which", return_value="/usr/bin/npm"):
            self.assertEqual(self.indexes.lock(), "")


class TheBusAsksForARefresh(Bed):
    async def test_a_known_workspace_is_refreshed_and_an_unknown_one_is_left_alone(self):
        await self.indexes.ensure("proj")
        self.main.sha = SHA_C
        self.indexes.schedule("proj")
        await self.indexes.settle()
        self.assertEqual(self.engine.ops[-1][0], "sync")
        self.assertEqual(self.indexes.status("proj").sha, SHA_C)
        asked = len(self.main.asked)
        self.indexes.schedule("stranger")
        await self.indexes.settle()
        self.assertEqual(len(self.main.asked), asked)
        self.assertEqual(self.indexes.status("stranger").state, "")


class WithNoLoopRunning(unittest.TestCase):
    def test_a_known_workspace_is_left_alone_not_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Data(Path(tmp) / "data")
            with data.write() as conn:
                conn.execute(INDEX_TABLE)
                conn.execute(
                    "INSERT INTO codegraph_index (workspace, path, state, at) "
                    "VALUES ('proj', 'proj', 'ready', 'now')"
                )
            ctx = ctx_for(units=Units(str, None, None), store=data)
            indexes = Indexes(ctx, Path(tmp), None, None, None)
            indexes.schedule("proj")
            self.assertEqual(indexes.status("proj").state, "ready")


if __name__ == "__main__":
    unittest.main()
