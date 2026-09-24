"""`0035` review round 1, F1 and F2: Gebo's path through `Service`, under a stand-in session.

A bare-directory remote with a real conflict on `f.txt`, a stand-in for `integrate._gh`
that reads the pull request's head off that remote, and a stand-in `stream` that does what
a Gebo session would do with its tools. No session is opened and nothing is paid for.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc import integrate
from coscc.config import Config
from coscc.service import Invalid, Service

SLUG = "proof-of-gebo"
PR = 7
BRANCH = f"feat/{SLUG}"


def git(cwd: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
        cwd=cwd, capture_output=True, text=True, check=check,
    ).stdout.strip()


def commit(where: Path, text: str, push: str) -> None:
    (where / "f.txt").write_text(text, encoding="utf-8")
    git(where, "commit", "-q", "-am", f"f.txt: {text.strip()}")
    git(where, "push", "-q", "origin", push)


class StandIn:
    """`Sessions` as `run_gebo` uses it: `stream` runs `act` in the tree, then replies."""

    def __init__(self, act):
        self.act = act
        self.membership = None

    async def stream(self, cwd, prompt, session_id, **kw):
        reply = await self.act(Path(cwd), kw["can_use_tool"])
        yield ("chunk", reply)
        yield ("done", {"session_id": "stand-in", "cost": {"cost_usd": 0.25, "turns": 3}})


class GeboThroughTheService(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.remote = root / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        seed = root / "seed"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(seed)], check=True, capture_output=True)
        (seed / "f.txt").write_text("one\n", encoding="utf-8")
        git(seed, "add", "-A")
        git(seed, "commit", "-q", "-m", "seed")
        git(seed, "push", "-q", "origin", "main")
        self.workspace = root / "work" / "proj"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(self.workspace)], check=True, capture_output=True)
        self.cwd = str(self.workspace)
        env = {"COS_DATA_DIR": str(root / "data"), "COS_WORKING_DIR": str(root / "work")}
        self._env = mock.patch.dict(os.environ, env)
        self._env.start()
        self.addCleanup(self._env.stop)
        config = Config(workspaces=(self.cwd,), working_dir=str(root / "work"), data_dir=str(root / "data"))
        self.service = Service(config, StandIn(self._no_act))
        made = asyncio.run(self.service.create_unit(self.cwd, SLUG, "fixture"))
        self.unit, directory = made["unit"], Path(made["path"])
        for name in ("intent.md", "spec.md", "plan.md", "impl.md"):
            extra = " Type: feat." if name == "intent.md" else ""
            (directory / name).write_text(f"# X: fixture\nAuthor: t.{extra} Status: accepted.\n", encoding="utf-8")
        (directory / "pr.md").write_text(
            f"# PR: fixture\nPR: https://github.com/o/r/pull/{PR}. Status: accepted.\n", encoding="utf-8")
        # Both sides change the same line: a real conflict when the branch is rebased.
        git(seed, "switch", "-q", "-c", BRANCH)
        commit(seed, "branch\n", BRANCH)
        git(seed, "switch", "-q", "main")
        commit(seed, "main\n", "main")
        git(self.workspace, "fetch", "-q", "origin")
        git(self.workspace, "branch", BRANCH, f"origin/{BRANCH}")
        self.tree = Path(asyncio.run(self.service._worktree(self.cwd, self.unit))["path"])
        self.head_before = self.remote_head()
        self.key = self.service._journal_key(self.cwd)
        patch = mock.patch.object(integrate, "_gh", self._gh)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self._tmp.cleanup)

    async def _no_act(self, tree, gate):
        return ""

    def remote_head(self) -> str:
        return git(self.remote, "rev-parse", f"refs/heads/{BRANCH}")

    async def _gh(self, argv, cwd):
        head = self.remote_head()
        if argv[:2] == ["pr", "list"]:
            return 0, json.dumps([{"number": PR, "headRefOid": head, "headRefName": BRANCH,
                                   "mergeable": "CONFLICTING"}]), ""
        if argv[:2] == ["pr", "view"]:
            return 0, json.dumps({"headRefOid": head}), ""
        if argv[:2] == ["pr", "checks"]:
            return 0, json.dumps([{"name": "tests", "bucket": "pass"}]), ""
        return 1, "", f"stand-in gh: unexpected {argv}"

    def integrate_with(self, act) -> dict:
        self.service.sessions = StandIn(act)

        async def go():
            done = {}
            async for kind, payload in self.service.integrate(self.cwd, self.unit):
                if kind == "done":
                    done = payload["integration"]
            return done

        return asyncio.run(go())

    def records(self, kind: str) -> list[dict]:
        return self.service._journal().records(self.key, kind=kind)

    def rebase(self, tree: Path) -> None:
        git(tree, "rebase", "origin/main", check=False)

    def test_a_push_under_the_lease_is_pushed_with_the_new_head(self):
        allowed = {}

        async def act(tree, gate):
            self.rebase(tree)
            (tree / "f.txt").write_text("main\nbranch\n", encoding="utf-8")
            git(tree, "add", "f.txt")
            git(tree, "-c", "core.editor=true", "rebase", "--continue")
            push = f"git push --force-with-lease={BRANCH}:{self.head_before} origin {BRANCH}"
            allowed["push"] = type(await gate("Bash", {"command": push}, None)).__name__
            git(tree, *push.split()[1:])
            return "kept both lines of f.txt"

        rec = self.integrate_with(act)
        self.assertEqual(allowed["push"], "PermissionResultAllow")
        self.assertEqual(rec["outcome"], "pushed")
        self.assertEqual(rec["mode"], "agent")
        self.assertEqual(rec["head_before"], self.head_before)
        self.assertEqual(rec["head_after"], self.remote_head())
        self.assertNotEqual(rec["head_after"], self.head_before)
        self.assertEqual(len(self.records("integration")), 1)

    def test_start_and_end_are_one_pair_and_every_view_reads_them(self):
        """Plan risk 5: a stage that is not one of the eight, in the timeline and usage."""

        async def act(tree, gate):
            return "[needs-person] f.txt: one side wants `main`, the other `branch`"

        rec = self.integrate_with(act)
        self.assertEqual(rec["outcome"], "needs-person")
        self.assertEqual(rec["needs_person"], ["f.txt: one side wants `main`, the other `branch`"])
        self.assertEqual(self.remote_head(), self.head_before)
        starts = [r for r in self.records("start") if r.get("stage") == "integrate"]
        ends = [r for r in self.records("end") if r.get("stage") == "integrate"]
        self.assertEqual((len(starts), len(ends)), (1, 1))
        self.assertEqual(starts[0]["mode"], "manual")
        self.assertEqual(ends[0]["outcome"], "done")
        runs = self.service.timeline(self.cwd, self.unit)
        self.assertEqual([r.get("stage") for r in runs["runs"]], ["integrate"])
        self.assertEqual(runs["cost"]["cost_usd"], 0.25)
        self.assertEqual(self.service.usage(self.cwd)["per_unit"][self.unit]["cost_usd"], 0.25)
        self.service.unit_history(self.cwd, self.unit)
        self.service.activity(self.cwd)

    def test_a_rebase_left_stopped_is_aborted_by_the_app(self):
        async def act(tree, gate):
            self.rebase(tree)
            return "stopped on a conflict"

        rec = self.integrate_with(act)
        self.assertEqual(rec["outcome"], "failed")
        self.assertIn("aborted", rec["detail"])
        self.assertNotIn("rebase in progress", git(self.tree, "status"))
        self.assertEqual(git(self.tree, "rev-parse", "HEAD"), self.head_before)
        self.assertEqual(git(self.tree, "status", "--porcelain"), "")
        ends = [r for r in self.records("end") if r.get("stage") == "integrate"]
        self.assertEqual([e["outcome"] for e in ends], ["failed"])

    def test_an_integration_is_refused_while_a_step_runs(self):
        """F1, the other way: the mark a step holds refuses Gebo, and opens no session."""
        self.service._active.add((self.key, self.unit))
        with self.assertRaises(Invalid) as caught:
            self.integrate_with(self._no_act)
        self.assertIn("a step is running", str(caught.exception))
        self.assertIn((self.key, self.unit), self.service._active)
        self.assertEqual(self.records("start"), [])


if __name__ == "__main__":
    unittest.main()
