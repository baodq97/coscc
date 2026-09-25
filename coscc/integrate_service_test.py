"""`0035` review round 1, F1 and F2: Gebo's path through `Service`, under a stand-in session.

A bare-directory remote with a real conflict on `f.txt`, a stand-in for `integrate._gh`
that reads the pull request's head off that remote, and a stand-in `stream` that does what
a Gebo session would do with its tools. No session is opened and nothing is paid for.

`AStaleOriginMain` is `0052`'s: the case of `0039`, a unit counted `current` against an
`origin/main` the workspace had not fetched since `main` moved.
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

from coscc import fetches, integrate
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
        self.prompts: list[str] = []

    async def stream(self, cwd, prompt, session_id, **kw):
        self.prompts.append(prompt)
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
        self.directory = directory
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
        # `0052`: a press fetches through the shared coordinator; one per test, so no test
        # reuses another's fetch.
        shared = mock.patch.object(fetches, "shared", fetches.Fetches())
        shared.start()
        self.addCleanup(shared.stop)
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

    def test_the_start_record_names_the_artifacts_it_pointed_at_and_the_build(self):
        """`0094` R13, R16, review round 1 F7: Gebo's `start` as a board step's."""

        async def act(tree, gate):
            return "[needs-person] f.txt: both"

        build = {"version": "9.9.9", "commit": "0123456789abcdef0123456789abcdef01234567"}
        with mock.patch.object(self.service, "_app_identity", return_value=build):
            self.integrate_with(act)
        starts = [r for r in self.records("start") if r.get("stage") == "integrate"]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]["pointed"], ["intent.md", "spec.md", "plan.md", "impl.md"])
        self.assertEqual((starts[0]["app_version"], starts[0]["app_commit"]), (build["version"], build["commit"]))
        self.assertIn(f"- {self.directory.resolve() / 'plan.md'}", self.service.sessions.prompts[0])

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

    def test_a_step_is_refused_while_the_unit_is_being_integrated(self):
        """F1, one way: `run_step` asks `_active`, and does not clear Gebo's mark.

        Since `0050` it asks before its first `await`, and the refusal is `steps.describe`'s.
        """
        said = {}

        async def act(tree, gate):
            try:
                async for _ in self.service.run_step(self.cwd, self.unit, "review"):
                    pass
            except Invalid as e:
                said["step"] = str(e)
            said["still_marked"] = (self.key, self.unit) in self.service._active
            return "[needs-person] stand-in"

        self.integrate_with(act)
        self.assertIn("being integrated", said.get("step", ""))
        self.assertTrue(said["still_marked"])
        self.assertNotIn((self.key, self.unit), self.service._active)

    def test_gebo_is_running_under_its_name_while_it_works_and_not_after(self):
        """`0051` plan step 3, the Gebo road."""
        seen = {}

        async def act(tree, gate):
            seen["running"] = self.service.running(self.cwd)["running"]
            return "[needs-person] stand-in"

        self.integrate_with(act)
        [row] = seen["running"][self.unit]
        self.assertEqual((row["kind"], row["stage"]), ("gebo", "integrate"))
        self.assertEqual(row["agent"], {"glyph": "ᚷ", "name": "Gebo"})
        self.assertEqual(self.service._running, {})

    def test_a_mechanical_rebase_is_rebasing_with_no_agent_and_not_after(self):
        """`0051` plan step 3, the mechanical road: `behind` with no conflict.

        Since `0052` a refused `update-branch` opens Gebo (spec, answer 1), so the press
        ends `agent` carrying gh's exit code; while `update-branch` runs it is still a
        rebase with no agent.
        """
        seen = {}
        conflicting = self._gh

        async def gh(argv, cwd):
            if argv[:2] == ["pr", "list"]:
                code, out, err = await conflicting(argv, cwd)
                return code, out.replace("CONFLICTING", "MERGEABLE"), err
            if argv[:2] == ["pr", "update-branch"]:
                seen["running"] = self.service.running(self.cwd)["running"]
                return 1, "", "stand-in gh: refused"
            return await conflicting(argv, cwd)

        with mock.patch.object(integrate, "_gh", gh):
            rec = self.integrate_with(self._no_act)
        self.assertEqual(rec["mode"], "agent")
        self.assertEqual(rec["update_branch"]["code"], 1)
        [row] = seen["running"][self.unit]
        self.assertEqual((row["kind"], row["agent"]), ("rebase", None))
        self.assertEqual(self.service._running, {})

    def mergeable_gh(self, update_branch):
        """`self._gh` with the list saying MERGEABLE — the conflict shows only on rebasing,
        `spike.md ## U2`'s case — and `update-branch` answered by `update_branch`."""
        conflicting = self._gh

        async def gh(argv, cwd):
            if argv[:2] == ["pr", "list"]:
                code, out, err = await conflicting(argv, cwd)
                return code, out.replace("CONFLICTING", "MERGEABLE"), err
            if argv[:2] == ["pr", "update-branch"]:
                return await update_branch()
            return await conflicting(argv, cwd)

        return gh

    def test_a_refused_update_branch_opens_gebo_with_its_exit_code(self):
        """`0052`, spec answer 1: the code and gh's words reach the prompt and the one record."""
        seen = {}

        async def refused():
            return 1, "", "stand-in gh: refused"

        async def act(tree, gate):
            seen["running"] = self.service.running(self.cwd)["running"]
            return "[needs-person] stand-in"

        with mock.patch.object(integrate, "_gh", self.mergeable_gh(refused)):
            rec = self.integrate_with(act)
        [prompt] = self.service.sessions.prompts
        self.assertIn("# The mechanical rebase was refused", prompt)
        self.assertIn("exited 1", prompt)
        self.assertIn("stand-in gh: refused", prompt)
        [row] = seen["running"][self.unit]
        self.assertEqual(row["kind"], "gebo")
        self.assertEqual(row["agent"], {"glyph": "ᚷ", "name": "Gebo"})
        [only] = self.records("integration")
        self.assertEqual(only["mode"], "agent")
        self.assertEqual(only["update_branch"], {"code": 1, "said": "stand-in gh: refused"})
        self.assertEqual(only["outcome"], "needs-person")
        self.assertEqual(only["fetch"]["outcome"], "fetched")
        self.assertEqual(rec["update_branch"]["code"], 1)
        starts = [r for r in self.records("start") if r.get("stage") == "integrate"]
        ends = [r for r in self.records("end") if r.get("stage") == "integrate"]
        self.assertEqual((len(starts), len(ends)), (1, 1))

    def test_a_timed_out_update_branch_opens_no_session(self):
        """`0052` plan step 3: no exit code to go on, and GitHub may still be rebasing."""

        async def timed_out():
            raise integrate.IntegrateError("gh pr update-branch did not answer within 30s")

        with mock.patch.object(integrate, "_gh", self.mergeable_gh(timed_out)):
            rec = self.integrate_with(self._no_act)
        self.assertEqual((rec["mode"], rec["outcome"]), ("mechanical", "failed"))
        self.assertIsNone(rec["update_branch"])
        self.assertIn("did not answer", rec["detail"])
        self.assertEqual(self.service.sessions.prompts, [])
        self.assertEqual(self.records("start"), [])

    def test_a_refused_update_branch_that_moved_the_head_opens_no_session(self):
        """`0052` review F1: gh exits non-zero after GitHub took the command. The app takes
        GitHub's head and the tree follows it; Gebo is not opened to race it."""
        scratch = Path(self._tmp.name) / "github-side"

        async def moved_then_refused():
            subprocess.run(["git", "clone", "-q", "-b", BRANCH, str(self.remote), str(scratch)],
                           check=True, capture_output=True)
            commit(scratch, "rebased by GitHub\n", BRANCH)
            return 1, "", "stand-in gh: the connection dropped"

        with mock.patch.object(integrate, "_gh", self.mergeable_gh(moved_then_refused)):
            rec = self.integrate_with(self._no_act)
        moved = self.remote_head()
        self.assertNotEqual(moved, self.head_before)
        self.assertEqual((rec["mode"], rec["outcome"], rec["head_after"]), ("mechanical", "pushed", moved))
        self.assertEqual(rec["update_branch"], {"code": 1, "said": "stand-in gh: the connection dropped"})
        self.assertIn("no session was opened", rec["detail"])
        self.assertEqual(git(self.tree, "rev-parse", "HEAD"), moved)
        self.assertEqual(self.service.sessions.prompts, [])
        self.assertEqual(self.records("start"), [])
        self.assertEqual(len(self.records("integration")), 1)

    def test_a_refused_update_branch_with_an_unread_head_opens_no_session(self):
        """`0052` review F1: whether GitHub took the command cannot be ruled out, so no session."""
        refusing = self.mergeable_gh(self._refused)

        async def gh(argv, cwd):
            if argv[:2] == ["pr", "view"] and "headRefOid" in argv:
                return 1, "", "stand-in gh: not logged in"
            return await refusing(argv, cwd)

        with mock.patch.object(integrate, "_gh", gh):
            rec = self.integrate_with(self._no_act)
        self.assertEqual((rec["mode"], rec["outcome"]), ("mechanical", "failed"))
        self.assertEqual(rec["update_branch"]["code"], 1)
        self.assertIn("could not be read", rec["detail"])
        self.assertIn("not logged in", rec["detail"])
        self.assertEqual(self.service.sessions.prompts, [])
        self.assertEqual(self.records("start"), [])

    async def _refused(self):
        return 1, "", "stand-in gh: refused"

    def test_a_head_github_moved_under_the_session_is_not_gebos_push(self):
        """`0052` review round 2, F1: GitHub's rebase lands after the one read, while Gebo
        works. The lease refuses Gebo's push; the moved head is not counted as Gebo's, and
        the tree follows it."""
        scratch = Path(self._tmp.name) / "github-side"
        pushed = {}

        async def act(tree, gate):
            self.rebase(tree)
            (tree / "f.txt").write_text("main\nbranch\n", encoding="utf-8")
            git(tree, "add", "f.txt")
            git(tree, "-c", "core.editor=true", "rebase", "--continue")
            subprocess.run(["git", "clone", "-q", "-b", BRANCH, str(self.remote), str(scratch)],
                           check=True, capture_output=True)
            commit(scratch, "rebased by GitHub, late\n", BRANCH)
            push = subprocess.run(
                ["git", "push", f"--force-with-lease={BRANCH}:{self.head_before}", "origin", BRANCH],
                cwd=tree, capture_output=True, text=True)
            pushed["code"] = push.returncode
            return "rebased; kept both lines of f.txt"

        with mock.patch.object(integrate, "_gh", self.mergeable_gh(self._refused)):
            rec = self.integrate_with(act)
        moved = self.remote_head()
        self.assertNotEqual(pushed["code"], 0)
        self.assertNotEqual(moved, self.head_before)
        self.assertEqual((rec["mode"], rec["outcome"], rec["head_after"]), ("agent", "failed", ""))
        self.assertIn("the push was not this session's", rec["detail"])
        self.assertIn(f"the local branch was moved to {moved[:7]}", rec["detail"])
        self.assertEqual(git(self.tree, "rev-parse", "HEAD"), moved)
        self.assertEqual(len(self.records("integration")), 1)
        ends = [r for r in self.records("end") if r.get("stage") == "integrate"]
        self.assertEqual([e["outcome"] for e in ends], ["failed"])

    def test_a_refused_integration_leaves_no_entry(self):
        self.service._take(self.key, self.unit, "step", "spec").phase = "running"
        with self.assertRaises(Invalid):
            self.integrate_with(self._no_act)
        self.assertEqual(self.service._running, {})

    def test_an_integration_is_refused_while_a_step_runs(self):
        """F1, the other way: the mark a step holds refuses Gebo, and opens no session."""
        self.service._take(self.key, self.unit, "step", "spec").phase = "running"
        with self.assertRaises(Invalid) as caught:
            self.integrate_with(self._no_act)
        self.assertIn("a spec step is running", str(caught.exception))
        self.assertIn((self.key, self.unit), self.service._active)
        self.assertEqual(self.records("start"), [])



class AStaleOriginMain(unittest.TestCase):
    """`0052` R6: the case of `0039`. `main` moved on the remote after the workspace's last
    fetch; the pull request has no conflict. The board counts `current`, and the press
    fetches, finds the unit behind, and rebases it the mechanical way."""

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
        # The branch changes `g.txt`; `main` later changes `f.txt`: no line in common.
        git(seed, "switch", "-q", "-c", BRANCH)
        (seed / "g.txt").write_text("branch\n", encoding="utf-8")
        git(seed, "commit", "-q", "-am", "g.txt: branch")
        git(seed, "push", "-q", "origin", BRANCH)
        git(seed, "switch", "-q", "main")
        git(self.workspace, "fetch", "-q", "origin")
        git(self.workspace, "branch", BRANCH, f"origin/{BRANCH}")
        # The order is the point (plan step 2): the tree first, since `worktrees` fetches
        # through the same coordinator; then a fresh coordinator; then `main` moves with no
        # fetch here. The other way round, the press would reuse a fetch and measure `0048`.
        self.tree = Path(asyncio.run(self.service._worktree(self.cwd, self.unit))["path"])
        shared = mock.patch.object(fetches, "shared", fetches.Fetches())
        shared.start()
        self.addCleanup(shared.stop)
        commit(seed, "main\n", "main")
        self.head_before = self.remote_head()
        self.key = self.service._journal_key(self.cwd)
        self.updates = 0
        patch = mock.patch.object(integrate, "_gh", self._gh)
        patch.start()
        self.addCleanup(patch.stop)
        integrate_delay = mock.patch.object(integrate, "POLL_DELAY", 0.0)
        integrate_delay.start()
        self.addCleanup(integrate_delay.stop)

    async def _no_act(self, tree, gate):
        return ""

    def remote_head(self) -> str:
        return git(self.remote, "rev-parse", f"refs/heads/{BRANCH}")

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
                                   "mergeable": "MERGEABLE"}]), ""
        if argv[:2] == ["pr", "view"]:
            return 0, json.dumps({"headRefOid": head, "mergeStateStatus": "BEHIND"}), ""
        if argv[:2] == ["pr", "update-branch"]:
            self.updates += 1
            self.github_rebases()
            return 0, "", ""
        return 1, "", f"stand-in gh: unexpected {argv}"

    def press(self) -> dict:
        async def go():
            done = {}
            async for kind, payload in self.service.integrate(self.cwd, self.unit):
                if kind == "done":
                    done = payload["integration"]
            return done

        return asyncio.run(go())

    def records(self, kind: str) -> list[dict]:
        return self.service._journal().records(self.key, kind=kind)

    def integration(self) -> dict:
        board = asyncio.run(self.service.board(self.cwd))
        return next(u for u in board["units"] if u["name"] == self.unit).get("integration") or {}

    def test_the_board_says_current_with_a_button(self):
        info = self.integration()
        self.assertEqual(info["state"], "current", "a board read fetched: find who, do not bend the fixture")
        self.assertIs(info["button"], True)
        self.assertEqual(info["mode"], "mechanical")
        self.assertTrue(any("opens Gebo" in w for w in info["warnings"]))

    def test_a_press_fetches_then_rebases_once(self):
        rec = self.press()
        self.assertEqual((rec["outcome"], rec["mode"]), ("pushed", "mechanical"), rec.get("detail"))
        self.assertEqual(self.updates, 1)
        self.assertEqual(rec["fetch"]["outcome"], "fetched")
        self.assertEqual(rec["merge_state"], "BEHIND")
        self.assertIsNone(rec["update_branch"])
        self.assertEqual(rec["head_after"], self.remote_head())
        self.assertNotEqual(rec["head_after"], self.head_before)
        self.assertEqual(len(self.records("integration")), 1)
        self.assertEqual(self.records("start"), [])

    def test_a_failed_fetch_is_refused_with_gits_words(self):
        # The stand-in `gh` reads the real remote, so only the workspace's fetch fails.
        git(self.workspace, "remote", "set-url", "origin", str(self.root / "gone.git"))
        with self.assertRaises(Invalid) as caught:
            self.press()
        said = str(caught.exception)
        self.assertTrue(said.startswith("the unit is current against origin/main"), said)
        self.assertIn("fetch failed:", said)
        self.assertIn("gone.git", said)
        [only] = self.records("integration")
        self.assertEqual(only["outcome"], "refused")
        self.assertEqual(only["fetch"]["outcome"], "failed")
        self.assertEqual(self.updates, 0)

    def test_a_current_unit_after_a_fresh_fetch_is_still_refused(self):
        """R3, last bullet: the button is there, and a truly current unit is still refused."""
        self.github_rebases()
        git(self.workspace, "fetch", "-q", "origin")
        git(self.tree, "reset", "-q", "--keep", f"origin/{BRANCH}")
        with self.assertRaises(Invalid) as caught:
            self.press()
        said = str(caught.exception)
        self.assertRegex(said, r"^the unit is current against origin/main [0-9a-f]{7} \(fetched\), "
                               r"which has nothing to integrate$")
        [only] = self.records("integration")
        self.assertEqual(only["outcome"], "refused")
        self.assertEqual(self.updates, 0)


if __name__ == "__main__":
    unittest.main()
