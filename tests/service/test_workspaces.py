"""Tests for `Workspaces` in `coscc/service/workspaces.py`, split from
`tests/service/test_service.py`."""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.config import Config
from coscc.kernel import Invalid
from coscc.service import Service
from coscc.agent.sessions import Live, Sessions
from coscc.units import scratch


class PullStopsAtALiveSession(unittest.TestCase):
    """The refusal has to happen before `git` runs, not after."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.addCleanup(self.tmp.cleanup)
        config = Config(workspaces=(), working_dir=str(self.root), data_dir=str(self.root))
        self.s = Service(config, Sessions(config))
        self.s.ws.store.add("repo")
        self._repo(self.root / "repo")

    @staticmethod
    def _repo(path: Path) -> None:
        """A real repo, because `gitops.pull` returns before spawning anything otherwise.

        With a plain directory the "no git process" assertion would pass for the wrong
        reason — git never runs on a non-repo either way. There is no remote, so the pull
        that does get through fails locally and reaches no network.
        """
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(path)], check=True)

    def _open_session_in(self, name: str) -> None:
        target = str(self.s.ws.store.path_of(name))
        self.s.sessions._live["live-1"] = Live(client=object(), session_id="live-1", cwd=target)

    def _pull(self, name: str = "repo"):
        return asyncio.run(self.s.ws.pull(name))

    def test_no_git_process_is_spawned_while_a_session_is_live(self):
        """A refusal after the fetch would have already moved files."""
        self._open_session_in("repo")
        boom = mock.Mock(side_effect=AssertionError("git ran despite a live session"))
        with (
            mock.patch.object(subprocess, "Popen", boom),
            mock.patch.object(asyncio, "create_subprocess_exec", boom),
        ):
            with self.assertRaises(Invalid) as e:
                self._pull()
        boom.assert_not_called()
        self.assertIn("repo", str(e.exception))
        self.assertIn("live session", str(e.exception))

    def test_it_gets_as_far_as_git_once_the_session_is_gone(self):
        """A permanent block would be a different bug, not a fix.

        The directory is not a git repo, so reaching `gitops` is itself the signal: the
        message is git's complaint rather than the session refusal."""
        self._open_session_in("repo")
        with self.assertRaises(Invalid):
            self._pull()
        self.s.sessions._live.clear()
        with self.assertRaises(Invalid) as e:
            self._pull()
        self.assertNotIn("live session", str(e.exception))

    def test_a_session_in_another_workspace_does_not_block_this_one(self):
        self.s.ws.store.add("other")
        self._repo(self.root / "other")
        self._open_session_in("other")
        with self.assertRaises(Invalid) as e:
            self._pull("repo")
        self.assertNotIn("live session", str(e.exception))


class RemovingAWorkspaceSweepsScratch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "tmp").mkdir()
        patch = mock.patch.object(tempfile, "tempdir", str(self.root / "tmp"))
        patch.start()
        self.addCleanup(patch.stop)
        self.data = str(self.root / "data")
        config = Config(workspaces=(), working_dir=str(self.root / "work"), data_dir=self.data)
        self.s = Service(config, Sessions(config))
        store = self.s.ws.store
        assert store is not None
        for name in ("one", "two"):
            (self.root / "work" / name).mkdir(parents=True)
            store.add(name)

    def test_the_removed_workspaces_scratch_is_gone_and_the_others_is_kept(self):
        one = scratch.ensure(self.root / "work" / "one", "0001_a", self.data)
        two = scratch.ensure(self.root / "work" / "two", "0001_a", self.data)
        # A unit of a kept workspace that has a directory is live; one without is an orphan.
        units_dir = self.s.ws.units_root(str(self.root / "work" / "two")) / ".cos" / "0001_a"
        units_dir.mkdir(parents=True)
        orphan = scratch.ensure(self.root / "work" / "two", "0002_b", self.data)
        self.s.ws.remove("one")
        for where in (*one, *orphan):
            self.assertFalse(where.exists(), where)
        for where in two:
            self.assertTrue(where.exists(), where)
