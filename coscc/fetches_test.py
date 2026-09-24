"""`0048` plan step 2. The fetch coordinator: join, reuse, retry once, and never hang.

Most tests hand in a fake `run` and a fake clock; the git dir each key is taken from is
still a real `git init`. The last class fetches for real, from a bare-directory remote.
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import unittest
from pathlib import Path

from coscc import fetches, gitops
from coscc.fetches import FetchFailed, Fetches
from coscc.gitops import GitError

RACE = (
    "error: fetching ref refs/remotes/origin/main failed: incorrect old value provided"
)


def git(where: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(where), "-c", "user.name=T", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeRun:
    """Stands in for `gitops.fetch`: fails with each queued error in turn, then succeeds.
    Holds briefly, so a second call started together has time to find it running."""

    def __init__(self, *errors: str, hold: float = 0.2):
        self.errors = list(errors)
        self.hold = hold
        self.calls = 0

    async def __call__(self, path, remote, branch):
        self.calls += 1
        await asyncio.sleep(self.hold)
        if self.errors:
            raise GitError(self.errors.pop(0))
        return ""


class Slept:
    def __init__(self):
        self.delays: list[float] = []

    async def __call__(self, delay: float):
        self.delays.append(delay)


class WithAFakeRun(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.clock = Clock()
        self.slept = Slept()

    def coordinator(self, run: FakeRun) -> Fetches:
        return Fetches(run=run, clock=self.clock, sleep=self.slept)

    def test_two_calls_together_run_one_fetch(self):
        run = FakeRun()
        f = self.coordinator(run)

        async def both():
            return await asyncio.gather(f.fetch(self.repo), f.fetch(self.repo))

        got = asyncio.run(both())
        self.assertEqual(run.calls, 1)
        self.assertEqual(sorted(g["outcome"] for g in got), ["fetched", "joined"])
        self.assertEqual([g["attempts"] for g in got], [1, 1])

    def test_a_call_that_joined_a_failed_fetch_gets_the_same_failure(self):
        run = FakeRun("fatal: could not read from remote")
        f = self.coordinator(run)

        async def both():
            return await asyncio.gather(
                f.fetch(self.repo), f.fetch(self.repo), return_exceptions=True
            )

        got = asyncio.run(both())
        self.assertEqual(run.calls, 1)
        for e in got:
            self.assertIsInstance(e, FetchFailed)
            self.assertEqual((e.outcome, e.attempts), ("failed", 1))
            self.assertIn("could not read from remote", str(e))

    def test_a_success_is_reused_under_thirty_seconds_and_not_at_thirty(self):
        run = FakeRun(hold=0)
        f = self.coordinator(run)
        first = asyncio.run(f.fetch(self.repo))
        self.assertEqual((first["outcome"], first["attempts"], first["age"]), ("fetched", 1, 0.0))
        self.clock.now += 29.9
        again = asyncio.run(f.fetch(self.repo))
        self.assertEqual((again["outcome"], again["attempts"], again["age"]), ("reused", 0, 29.9))
        self.assertEqual(run.calls, 1)
        self.clock.now += 0.1
        third = asyncio.run(f.fetch(self.repo))
        self.assertEqual(third["outcome"], "fetched")
        self.assertEqual(run.calls, 2)

    def test_one_ref_lock_race_is_retried_after_one_second(self):
        run = FakeRun(RACE, hold=0)
        got = asyncio.run(self.coordinator(run).fetch(self.repo))
        self.assertEqual((got["outcome"], got["attempts"]), ("fetched", 2))
        self.assertEqual(self.slept.delays, [1.0])
        self.assertEqual(run.calls, 2)

    def test_cannot_lock_ref_is_retried_too(self):
        run = FakeRun("error: cannot lock ref 'refs/remotes/origin/main'", hold=0)
        got = asyncio.run(self.coordinator(run).fetch(self.repo))
        self.assertEqual(got["attempts"], 2)

    def test_a_second_race_is_not_retried_and_the_reason_says_it_was(self):
        run = FakeRun(RACE, RACE + " (again)", hold=0)
        with self.assertRaises(FetchFailed) as caught:
            asyncio.run(self.coordinator(run).fetch(self.repo))
        self.assertEqual(caught.exception.attempts, 2)
        self.assertIn("retried", str(caught.exception))
        self.assertIn(RACE + " (again)", str(caught.exception))
        self.assertEqual(run.calls, 2)

    def test_any_other_failure_and_a_timeout_are_not_retried(self):
        for error in ("fatal: 'gone.git' does not appear to be a git repository",
                      "git timed out after 20s: git -C"):
            run = FakeRun(error, hold=0)
            with self.assertRaises(FetchFailed, msg=error) as caught:
                asyncio.run(self.coordinator(run).fetch(self.repo))
            self.assertEqual(caught.exception.attempts, 1)
            self.assertEqual(str(caught.exception), error)
        self.assertEqual(self.slept.delays, [])

    def test_a_failure_is_never_reused(self):
        run = FakeRun("fatal: no", hold=0)
        f = self.coordinator(run)
        with self.assertRaises(FetchFailed):
            asyncio.run(f.fetch(self.repo))
        got = asyncio.run(f.fetch(self.repo))
        self.assertEqual(got["outcome"], "fetched")
        self.assertEqual(run.calls, 2)

    def test_a_cancelled_leader_fails_the_calls_that_joined_it_instead_of_hanging(self):
        run = FakeRun(hold=5)
        f = self.coordinator(run)

        async def scene():
            leader = asyncio.create_task(f.fetch(self.repo))
            while run.calls == 0:
                await asyncio.sleep(0.01)
            follower = asyncio.create_task(f.fetch(self.repo))
            await asyncio.sleep(0.2)
            leader.cancel()
            return await asyncio.wait_for(follower, timeout=2)

        with self.assertRaises(FetchFailed) as caught:
            asyncio.run(scene())
        self.assertIn("cancelled", str(caught.exception))
        # Nothing left behind: the next call fetches afresh.
        self.assertEqual(asyncio.run(f.fetch(self.repo))["outcome"], "fetched")

    def test_a_path_that_is_not_a_repository_fails_with_no_attempt(self):
        with self.assertRaises(FetchFailed) as caught:
            asyncio.run(self.coordinator(FakeRun()).fetch(Path(self._tmp.name) / "nowhere"))
        self.assertEqual(caught.exception.attempts, 0)


class WithRealGit(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.remote = base / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        self.repo = self._clone("repo")
        (self.repo / "f.txt").write_text("one\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "one")
        git(self.repo, "push", "-q", "origin", "main")

    def _clone(self, name: str) -> Path:
        where = Path(self._tmp.name) / name
        subprocess.run(["git", "clone", "-q", str(self.remote), str(where)], check=True, capture_output=True)
        return where

    def _counting(self) -> tuple[Fetches, list[Path]]:
        seen: list[Path] = []

        async def run(path, remote, branch):
            seen.append(path)
            return await gitops.fetch(path, remote, branch)

        return Fetches(run=run), seen

    def test_two_worktrees_of_one_clone_share_a_fetch_and_two_clones_do_not(self):
        tree = Path(self._tmp.name) / "tree"
        git(self.repo, "worktree", "add", "-q", "--detach", str(tree))
        other = self._clone("other")
        f, seen = self._counting()

        async def together():
            return await asyncio.gather(f.fetch(self.repo), f.fetch(tree), f.fetch(other))

        got = asyncio.run(together())
        self.assertEqual(len(seen), 2)
        self.assertEqual(got[2]["outcome"], "fetched")
        self.assertEqual(sorted(g["outcome"] for g in got[:2]), ["fetched", "joined"])

    def test_one_call_alone_fetches_once_and_moves_the_ref(self):
        other = self._clone("other")
        (other / "g.txt").write_text("two\n", encoding="utf-8")
        git(other, "add", "-A")
        git(other, "commit", "-q", "-m", "two")
        git(other, "push", "-q", "origin", "main")
        tip = git(other, "rev-parse", "HEAD")
        got = asyncio.run(Fetches().fetch(self.repo))
        self.assertEqual((got["outcome"], got["attempts"]), ("fetched", 1))
        self.assertLess(got["age"], fetches.REUSE_SECONDS)
        self.assertEqual(git(self.repo, "rev-parse", "refs/remotes/origin/main"), tip)

    def test_the_default_run_is_the_plain_git_fetch(self):
        self.assertIs(Fetches().run, gitops.fetch)


if __name__ == "__main__":
    unittest.main()
