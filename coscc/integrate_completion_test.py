"""`0114` R9: an integration cut after its rebase and before its push, and what comes next.

The case of `0096`: Gebo rebased the unit's tree onto a new `main`, and the app restarted
before it pushed. The run log keeps a `start` of `integrate` with no `end` and no
`integration`; the tree is at `L`, the pull request still at `P`. Before `0114` every press
after that was refused with "the local head … is not the pull request's head", and the
autopilot stopped on it.

A bare-directory remote, a stand-in `gh` reading the pull request's head off it, and a
stand-in session doing what Gebo would (`integrate_service_test`). No session is opened and
nothing is paid for.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc import fetches, integrate
from coscc.config import Config
from coscc.integrate_service_test import BRANCH, PR, SLUG, StandIn, git
from coscc.service import Invalid, Service

REFUSED = ("is not the pull request's head", "the last integration was refused")


class ACutIntegration(unittest.TestCase):
    """The branch changes `g.txt` and is pushed at `P`; `main` then changes `MAIN_FILE`."""

    MAIN_FILE = "f.txt"
    MERGEABLE = "MERGEABLE"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.root = root
        self.remote = root / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        self.seed = seed = root / "seed"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(seed)], check=True, capture_output=True)
        (seed / "f.txt").write_text("one\n", encoding="utf-8")
        (seed / "g.txt").write_text("one\n", encoding="utf-8")
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
        git(seed, "switch", "-q", "-c", BRANCH)
        (seed / "g.txt").write_text("branch\n", encoding="utf-8")
        git(seed, "commit", "-q", "-am", "g.txt: branch")
        git(seed, "push", "-q", "origin", BRANCH)
        self.P = git(seed, "rev-parse", "HEAD")
        git(seed, "switch", "-q", "main")
        (seed / self.MAIN_FILE).write_text("main\n", encoding="utf-8")
        git(seed, "commit", "-q", "-am", f"{self.MAIN_FILE}: main")
        git(seed, "push", "-q", "origin", "main")
        git(self.workspace, "fetch", "-q", "origin")
        git(self.workspace, "branch", BRANCH, f"origin/{BRANCH}")
        self.tree = Path(asyncio.run(self.service._worktree(self.cwd, self.unit))["path"])
        # A fresh coordinator after the tree's own fetch, so a press fetches for itself.
        shared = mock.patch.object(fetches, "shared", fetches.Fetches())
        shared.start()
        self.addCleanup(shared.stop)
        self.key = self.service._journal_key(self.cwd)
        self.updates = 0
        patch = mock.patch.object(integrate, "_gh", self._gh)
        patch.start()
        self.addCleanup(patch.stop)
        delay = mock.patch.object(integrate, "POLL_DELAY", 0.0)
        delay.start()
        self.addCleanup(delay.stop)

    async def _no_act(self, tree, gate):
        return ""

    def remote_head(self) -> str:
        return git(self.remote, "rev-parse", f"refs/heads/{BRANCH}")

    def cut(self) -> None:
        """What the restart left: Gebo's `start`, and nothing after it."""
        self.service._journal().started(self.key, self.unit, "integrate", "manual",
                                        started_by="autopilot", head=self.P)

    def github_rebases(self) -> None:
        """What GitHub's `update-branch --rebase` does, in a scratch clone of the remote."""
        scratch = Path(tempfile.mkdtemp(dir=self.root))
        subprocess.run(["git", "clone", "-q", str(self.remote), str(scratch)], check=True, capture_output=True)
        git(scratch, "switch", "-q", BRANCH)
        git(scratch, "rebase", "-q", "origin/main")
        git(scratch, "push", "-q", "--force", "origin", BRANCH)

    async def _gh(self, argv, cwd):
        head = self.remote_head()
        if argv[:2] == ["pr", "list"]:
            return 0, json.dumps([{"number": PR, "headRefOid": head, "headRefName": BRANCH,
                                   "mergeable": self.MERGEABLE}]), ""
        if argv[:2] == ["pr", "view"]:
            return 0, json.dumps({"headRefOid": head, "mergeStateStatus": "BEHIND"}), ""
        if argv[:2] == ["pr", "checks"]:
            return 0, json.dumps([{"name": "tests", "bucket": "pass"}]), ""
        if argv[:2] == ["pr", "update-branch"]:
            self.updates += 1
            self.github_rebases()
            return 0, "", ""
        return 1, "", f"stand-in gh: unexpected {argv}"

    def records(self, kind: str) -> list[dict]:
        return self.service._journal().records(self.key, kind=kind)

    def press(self, act=None) -> dict:
        self.service.sessions = StandIn(act or self._no_act)

        async def go():
            done = {}
            async for kind, payload in self.service.integrate(self.cwd, self.unit):
                if kind == "done":
                    done = payload["integration"]
            return done

        return asyncio.run(go())

    def autopilot_pass(self, act) -> dict:
        """One autopilot pass with the real `integrate`, read to its end, and the pass the
        integration's end nudges. `next` is a stand-in, as in `0112`'s proof: the real one asks
        `gh` for the head. Returns the stops left once both are done."""
        self.service.sessions = StandIn(act)
        self.service.config = dataclasses.replace(self.service.config, host="127.0.0.1")

        async def next_step(cwd, unit):
            return {"stage": "", "blocked": True, "waiting": [], "hold": None,
                    "action": f"#{PR} is 1 commit(s) behind origin/main — integrate, then review again"}

        async def go():
            self.service.set_autopilot(self.cwd, "autopilot", True)
            # A loop that never passes on its own: this test asks for its passes.
            self.service.autopilot_stop(self.key)
            self.service._autopilot_tasks[self.key] = asyncio.get_running_loop().create_future()
            self.service._autopilot_cwd[self.key] = self.cwd
            self.service._journal().append({
                "kind": "shortlist", "workspace": self.key, "unit": "", "units": [self.unit],
                "reason": "for the proof", "by": "proof",
            })
            self.service.next_step = next_step
            await self.service._autopilot_pass(self.key)
            runs = dict(self.service._autopilot_runs.get(self.key) or {})
            self.assertEqual([stage for stage, _ in runs.values()], ["integrate"],
                             f"the pass did not start integrate: {self.service._autopilot_stops.get(self.key)}")
            await asyncio.gather(*(task for _, task in runs.values()))
            await asyncio.gather(*list(self.service._autopilot_pending))
            stops = dict(self.service._autopilot_stops.get(self.key) or {})
            self.service.autopilot_stop(self.key)
            return stops

        return asyncio.run(go())

    def assertNoRefusalStop(self, stops: dict) -> None:
        for stop in stops.values():
            for said in REFUSED:
                self.assertNotIn(said, stop.get("reason", ""))


class TheCaseOf0096(ACutIntegration):
    def rebase_here(self) -> str:
        """Gebo's clean rebase, never pushed: the tree at `L`."""
        git(self.tree, "rebase", "-q", "origin/main")
        return git(self.tree, "rev-parse", "HEAD")

    def pushing_act(self, L: str, allowed: dict):
        async def act(tree, gate):
            compare = f"git range-diff origin/main {self.P} {L}"
            allowed["range-diff"] = type(await gate("Bash", {"command": compare}, None)).__name__
            git(tree, *compare.split()[1:])
            push = f"git push --force-with-lease={BRANCH}:{self.P} origin {BRANCH}"
            allowed["push"] = type(await gate("Bash", {"command": push}, None)).__name__
            git(tree, *push.split()[1:])
            return "pushed; the range-diff showed context only"
        return act

    def test_r9_a_cut_integration_completes_on_the_next_autopilot_pass(self):
        L = self.rebase_here()
        self.assertNotEqual(L, self.P)
        self.cut()
        allowed: dict = {}
        stops = self.autopilot_pass(self.pushing_act(L, allowed))
        self.assertEqual(allowed, {"range-diff": "PermissionResultAllow", "push": "PermissionResultAllow"})
        [rec] = self.records("integration")
        self.assertEqual((rec["outcome"], rec["mode"], rec["head_after"]), ("pushed", "agent", L), rec["detail"])
        self.assertEqual(rec["started_by"], "autopilot")
        self.assertEqual(rec["completion"]["relation"], "diverged")
        self.assertEqual(rec["completion"]["local_head"], L)
        self.assertEqual(rec["completion"]["cut"]["head"], self.P)
        self.assertEqual(self.remote_head(), L)
        [prompt] = self.service.sessions.prompts
        self.assertIn(L, prompt)
        self.assertIn(self.P, prompt)
        self.assertIn("# Commits that were never pushed", prompt)
        self.assertEqual(self.updates, 0)
        self.assertNoRefusalStop(stops)

    def test_ahead(self):
        """A local commit on top of `P`, never pushed. A press, not the autopilot."""
        (self.tree / "g.txt").write_text("branch\nmore\n", encoding="utf-8")
        git(self.tree, "commit", "-q", "-am", "g.txt: more")
        L = git(self.tree, "rev-parse", "HEAD")

        async def act(tree, gate):
            git(tree, "push", f"--force-with-lease={BRANCH}:{self.P}", "origin", BRANCH)
            return "pushed"

        rec = self.press(act)
        self.assertEqual((rec["outcome"], rec["head_after"]), ("pushed", L), rec["detail"])
        self.assertEqual(rec["completion"]["relation"], "ahead")
        self.assertIsNone(rec["completion"]["cut"])
        self.assertNotIn("git range-diff origin/main", self.service.sessions.prompts[0])

    def test_behind_opens_no_session(self):
        """The pull request moved past `P` from elsewhere; the tree is still at `P`."""
        scratch = self.root / "elsewhere"
        subprocess.run(["git", "clone", "-q", "-b", BRANCH, str(self.remote), str(scratch)],
                       check=True, capture_output=True)
        (scratch / "g.txt").write_text("branch\nfrom elsewhere\n", encoding="utf-8")
        git(scratch, "commit", "-q", "-am", "g.txt: from elsewhere")
        git(scratch, "push", "-q", "origin", BRANCH)
        git(self.workspace, "fetch", "-q", "origin")
        self.assertEqual(git(self.tree, "rev-parse", "HEAD"), self.P)
        rec = self.press()
        self.assertEqual(self.service.sessions.prompts, [])
        self.assertEqual((rec["mode"], rec["outcome"]), ("mechanical", "pushed"), rec["detail"])
        self.assertEqual(rec["completion"]["relation"], "behind")
        self.assertEqual(rec["completion"]["local_head"], self.P)
        self.assertEqual(self.updates, 1)
        self.assertEqual(git(self.tree, "rev-parse", "HEAD"), self.remote_head())
        self.assertIn("the local branch followed the pull request's head", rec["detail"])

    def test_diverged_with_a_changed_line_needs_a_person(self):
        self.rebase_here()
        (self.tree / "g.txt").write_text("branch, changed\n", encoding="utf-8")
        git(self.tree, "commit", "-q", "--amend", "-am", "g.txt: branch")
        L = git(self.tree, "rev-parse", "HEAD")
        self.cut()

        async def act(tree, gate):
            return f"[needs-person] {L[:7]} changes g.txt beyond the new base"

        stops = self.autopilot_pass(act)
        self.assertEqual(self.remote_head(), self.P)
        [rec] = self.records("integration")
        self.assertEqual(rec["outcome"], "needs-person")
        self.assertEqual(rec["completion"]["relation"], "diverged")
        self.assertEqual(stops[self.unit]["kind"], "d")
        self.assertIn("changes g.txt beyond the new base", stops[self.unit]["reason"])
        self.assertNoRefusalStop(stops)

    def test_r7_a_head_other_than_the_local_one_is_failed(self):
        """The session commits once more before pushing: the head moved, but not to `L`."""
        L = self.rebase_here()
        self.cut()

        async def act(tree, gate):
            (tree / "g.txt").write_text("branch\nextra\n", encoding="utf-8")
            git(tree, "commit", "-q", "-am", "g.txt: extra")
            git(tree, "push", f"--force-with-lease={BRANCH}:{self.P}", "origin", BRANCH)
            return "pushed"

        rec = self.press(act)
        self.assertNotEqual(self.remote_head(), L)
        self.assertEqual(rec["outcome"], "failed")
        self.assertIn(f"not to the local head {L[:7]}", rec["detail"])

    def test_review_f1_a_pull_request_rebased_elsewhere_is_not_pushed_back(self):
        """The other way round from `0096`: GitHub rebased the pull request, the tree kept `P`.
        The heads diverge, but the tree is on the older base, and a press opens no session."""
        self.github_rebases()
        rebased = self.remote_head()
        git(self.workspace, "fetch", "-q", "origin")
        self.assertEqual(git(self.tree, "rev-parse", "HEAD"), self.P)
        with self.assertRaises(Invalid) as said:
            self.press(self.pushing_act(self.P, {}))
        self.assertIn("is not the pull request's head", str(said.exception))
        self.assertIn(integrate.STALE, str(said.exception))
        self.assertEqual(self.service.sessions.prompts, [])
        self.assertEqual(self.remote_head(), rebased)
        [rec] = self.records("integration")
        self.assertEqual(rec["outcome"], "refused")
        self.assertEqual(rec["completion"]["relation"], "stale")


class AStoppedRebase(ACutIntegration):
    """`main` changes `g.txt` too: rebasing the branch stops on a conflict."""

    MAIN_FILE = "g.txt"
    MERGEABLE = "CONFLICTING"

    def stop_a_rebase(self) -> None:
        git(self.tree, "rebase", "origin/main", check=False)
        self.assertIn("rebase in progress", git(self.tree, "status"))

    def test_a_stopped_rebase_with_a_cut_integration_is_aborted(self):
        self.cut()
        self.stop_a_rebase()

        async def act(tree, gate):
            return "[needs-person] g.txt: one side wants `main`, the other `branch`"

        rec = self.press(act)
        self.assertNotIn("rebase in progress", git(self.tree, "status"))
        self.assertIn("aborted", rec["detail"])
        self.assertEqual(rec["outcome"], "needs-person")
        self.assertIsNone(rec["completion"])
        self.assertEqual(git(self.tree, "rev-parse", "HEAD"), self.P)

    def test_a_stopped_rebase_without_a_cut_integration_is_refused(self):
        self.stop_a_rebase()
        with self.assertRaises(Invalid):
            self.press()
        [rec] = self.records("integration")
        self.assertEqual(rec["outcome"], "refused")
        self.assertNotIn("aborted", rec["detail"])
        self.assertIn("rebase in progress", git(self.tree, "status"))
        self.assertEqual(self.service.sessions.prompts, [])


if __name__ == "__main__":
    unittest.main()
