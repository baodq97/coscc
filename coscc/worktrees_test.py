"""`0017` plan step 3. Where a unit's tree is, how it is made, prepared and removed.

No network: the remote is a bare directory, and `gh` is a function handed in.
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

import coscc
from coscc import fetches, units, worktrees
from coscc.gitops import GitError
from coscc.units import BadUnit


def git(where: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(where), "-c", "user.name=T", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


class Repo(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.data = base / "data"
        self.remote = base / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        self.repo = base / "repo"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(self.repo)], check=True, capture_output=True)
        (self.repo / "f.txt").write_text("one\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "one")
        git(self.repo, "push", "-q", "origin", "main")
        self.main = git(self.repo, "rev-parse", "main")

    def _advance_remote(self, name: str = "g.txt", text: str = "from elsewhere\n") -> str:
        """Push a commit from a second clone, so the bare remote moves out from under
        `self.repo` without `self.repo` itself, or any tree of it, fetching."""
        other = Path(self._tmp.name) / "other"
        if not other.exists():
            subprocess.run(
                ["git", "clone", "-q", str(self.remote), str(other)], check=True, capture_output=True
            )
        (other / name).write_text(text, encoding="utf-8")
        git(other, "add", "-A")
        git(other, "commit", "-q", "-m", f"advance: {name}")
        git(other, "push", "-q", "origin", "main")
        return git(other, "rev-parse", "HEAD")


class WhereATreeLives(Repo):
    def test_the_path_is_a_function_of_workspace_and_unit(self):
        a = worktrees.path(self.repo, "0001_a", self.data)
        self.assertEqual(a, worktrees.path(self.repo, "0001_a", self.data))
        self.assertNotEqual(a, worktrees.path(self.repo, "0002_b", self.data))
        self.assertIn(units.slot(self.repo), a.parts)

    def test_a_bad_unit_name_is_refused(self):
        for bad in ("../x", "0001", "", "0001_A"):
            with self.assertRaises(BadUnit, msg=bad):
                worktrees.path(self.repo, bad, self.data)

    def test_a_tree_inside_the_workspace_or_the_package_is_refused(self):
        with self.assertRaises(BadUnit):
            worktrees.path(self.repo, "0001_a", self.repo / "data")
        pkg = Path(coscc.__file__).resolve().parent
        with self.assertRaises(BadUnit):
            worktrees.path(self.repo, "0001_a", pkg / "d")


class Ensuring(Repo):
    def test_a_new_unit_gets_a_detached_tree_at_main_and_the_root_does_not_move(self):
        made = asyncio.run(worktrees.ensure(self.repo, "0001_a", None, self.data))
        self.assertTrue(made["created"])
        self.assertFalse(made["switched"])
        tree = Path(made["path"])
        self.assertEqual(git(tree, "rev-parse", "HEAD"), self.main)
        self.assertEqual(git(self.repo, "branch", "--show-current"), "main")
        again = asyncio.run(worktrees.ensure(self.repo, "0001_a", None, self.data))
        self.assertFalse(again["created"])

    def test_an_existing_branch_is_opened_in_the_tree(self):
        git(self.repo, "branch", "fix/a", self.main)
        made = asyncio.run(worktrees.ensure(self.repo, "0001_a", "fix/a", self.data))
        self.assertEqual(made["branch"], "fix/a")
        self.assertTrue(made["base"]["fresh"])

    def test_a_broken_origin_refuses_to_open_a_branch_when_the_unit_never_had_a_tree(self):
        """`0030` review round 1, F2: this path — no tree yet, the branch already cut at a
        terminal — fetches and refuses on failure just like `SwitchingOntoAnExistingBranch`
        does for a unit that already had a detached tree. The two must not disagree about
        when opening onto an existing branch is safe merely because one of them happens to
        have a tree already."""
        git(self.repo, "branch", "fix/a", self.main)
        git(self.repo, "remote", "set-url", "origin", str(Path(self._tmp.name) / "gone.git"))
        with self.assertRaises(GitError) as caught:
            asyncio.run(worktrees.ensure(self.repo, "0001_a", "fix/a", self.data))
        self.assertIn("was not opened", str(caught.exception))
        self.assertFalse(worktrees.path(self.repo, "0001_a", self.data).exists())

    def test_a_clean_root_on_the_units_branch_is_moved_back_to_main(self):
        git(self.repo, "switch", "-q", "-c", "fix/a")
        made = asyncio.run(worktrees.ensure(self.repo, "0001_a", "fix/a", self.data))
        self.assertTrue(made["switched"])
        self.assertEqual(git(self.repo, "branch", "--show-current"), "main")
        self.assertEqual(made["branch"], "fix/a")

    def test_a_tree_made_before_the_branch_is_moved_onto_it_when_it_is_cut_at_a_terminal(self):
        asyncio.run(worktrees.ensure(self.repo, "0001_a", None, self.data))
        git(self.repo, "switch", "-q", "-c", "fix/a")  # `.claude/CLAUDE.md` step 4, by hand
        made = asyncio.run(worktrees.ensure(self.repo, "0001_a", "fix/a", self.data))
        self.assertEqual((made["branch"], made["switched"], made["created"]), ("fix/a", True, False))
        self.assertEqual(git(self.repo, "branch", "--show-current"), "main")

    def test_creating_a_unit_never_moves_the_workspace(self):
        git(self.repo, "switch", "-q", "-c", "fix/other")
        made = asyncio.run(worktrees.ensure(self.repo, "0001_a", None, self.data))
        self.assertFalse(made["switched"])
        self.assertEqual(git(self.repo, "branch", "--show-current"), "fix/other")

    def test_a_dirty_root_on_the_units_branch_is_refused_and_left_alone(self):
        git(self.repo, "switch", "-q", "-c", "fix/a")
        (self.repo / "f.txt").write_text("mine\n", encoding="utf-8")
        with self.assertRaises(GitError) as caught:
            asyncio.run(worktrees.ensure(self.repo, "0001_a", "fix/a", self.data))
        self.assertIn("uncommitted", str(caught.exception))
        self.assertEqual(git(self.repo, "branch", "--show-current"), "fix/a")
        self.assertEqual((self.repo / "f.txt").read_text(), "mine\n")

    def test_a_broken_origin_leaves_the_root_on_the_units_branch(self):
        """`0030` review round 2, F5: the workspace must not move to `main` before the
        fetch that can still refuse this call has run — this is the "no tree yet" path
        (`ensure`'s line naming `_fetch_or_refuse(root, branch)`), where the fetch itself
        runs in the workspace. Before the fix, `switch_trunk` ran first, so the workspace
        ended up on `main` anyway even though `_fetch_or_refuse` then raised saying
        "Nothing in the repository changed"."""
        git(self.repo, "switch", "-q", "-c", "fix/a")
        git(self.repo, "remote", "set-url", "origin", str(Path(self._tmp.name) / "gone.git"))
        with self.assertRaises(GitError) as caught:
            asyncio.run(worktrees.ensure(self.repo, "0001_a", "fix/a", self.data))
        self.assertIn("was not opened", str(caught.exception))
        self.assertEqual(git(self.repo, "branch", "--show-current"), "fix/a")

    def test_a_broken_origin_in_the_tree_also_leaves_the_root_on_the_units_branch(self):
        """`0030` review round 2, F5, the other of the two paths it names: a unit that
        already has a detached tree fetches inside that tree, not the workspace, but the
        workspace must still not have moved to `main` when that fetch fails."""
        asyncio.run(worktrees.ensure(self.repo, "0001_a", None, self.data))
        tree = worktrees.path(self.repo, "0001_a", self.data)
        git(tree, "remote", "set-url", "origin", str(Path(self._tmp.name) / "gone.git"))
        git(self.repo, "switch", "-q", "-c", "fix/a")  # `.claude/CLAUDE.md` step 4, by hand
        with self.assertRaises(GitError) as caught:
            asyncio.run(worktrees.ensure(self.repo, "0001_a", "fix/a", self.data))
        self.assertIn("was not opened", str(caught.exception))
        self.assertEqual(git(self.repo, "branch", "--show-current"), "fix/a")
        self.assertEqual(git(tree, "branch", "--show-current"), "")


class RefreshingTheBase(Repo):
    """`0030_a-unit-branch-starts-from-a-stale-main` plan step 2: `refresh_base`."""

    def setUp(self):
        super().setUp()
        made = asyncio.run(worktrees.ensure(self.repo, "0001_a", None, self.data))
        self.tree = Path(made["path"])

    def test_the_tree_is_moved_to_the_fetched_tip_when_it_is_behind(self):
        new = self._advance_remote()
        got = asyncio.run(worktrees.refresh_base(self.repo, "0001_a", self.data))
        fetched = got.pop("fetch")
        self.assertEqual(got, {"ref": "origin/main", "sha": new[:7], "fresh": True, "reason": ""})
        self.assertEqual((fetched["outcome"], fetched["attempts"]), ("fetched", 1))
        self.assertLess(fetched["age"], fetches.REUSE_SECONDS)
        self.assertEqual(git(self.tree, "rev-parse", "HEAD"), new)
        # The workspace's own `main` never moves — only the tree does.
        self.assertEqual(git(self.repo, "rev-parse", "main"), self.main)

    def test_a_broken_origin_leaves_the_tree_and_says_why(self):
        before = git(self.tree, "rev-parse", "HEAD")
        git(self.tree, "remote", "set-url", "origin", str(Path(self._tmp.name) / "gone.git"))
        got = asyncio.run(worktrees.refresh_base(self.repo, "0001_a", self.data))
        self.assertFalse(got["fresh"])
        self.assertTrue(got["reason"])
        self.assertEqual(git(self.tree, "rev-parse", "HEAD"), before)
        # `0048` R6: not retried — it is not a ref-lock race — and it says it failed.
        self.assertEqual(got["fetch"], {"outcome": "failed", "attempts": 1, "age": None})

    def test_a_dirty_tree_is_left_alone(self):
        before = git(self.tree, "rev-parse", "HEAD")
        self._advance_remote()
        (self.tree / "dirty.txt").write_text("mine\n", encoding="utf-8")
        got = asyncio.run(worktrees.refresh_base(self.repo, "0001_a", self.data))
        self.assertFalse(got["fresh"])
        self.assertEqual(git(self.tree, "rev-parse", "HEAD"), before)
        self.assertTrue((self.tree / "dirty.txt").exists())

    def test_a_commit_made_directly_on_the_tree_is_never_left_behind(self):
        (self.tree / "local.txt").write_text("mine\n", encoding="utf-8")
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-q", "-m", "local")
        local_head = git(self.tree, "rev-parse", "HEAD")
        self._advance_remote()
        got = asyncio.run(worktrees.refresh_base(self.repo, "0001_a", self.data))
        self.assertFalse(got["fresh"])
        self.assertIn("ancestor", got["reason"])
        self.assertEqual(git(self.tree, "rev-parse", "HEAD"), local_head)

    def _own_clock(self) -> list[float]:
        """A coordinator of this test's own, on a clock the test moves by hand."""
        now = [1000.0]
        patcher = mock.patch.object(fetches, "shared", fetches.Fetches(clock=lambda: now[0]))
        patcher.start()
        self.addCleanup(patcher.stop)
        return now

    def test_the_prepare_record_is_dropped_only_when_the_tree_actually_moves(self):
        now = self._own_clock()
        record = worktrees.prepare_record(self.tree)
        record.write_text(json.dumps({"ok": True}), encoding="utf-8")
        # Nothing new on the remote yet: the tree is already at the fetched tip.
        got = asyncio.run(worktrees.refresh_base(self.repo, "0001_a", self.data))
        self.assertTrue(got["fresh"])
        self.assertTrue(record.exists())
        self._advance_remote()
        record.write_text(json.dumps({"ok": True}), encoding="utf-8")
        # `0048`: a second step under 30s later would reuse the first fetch
        # (`spec.md ## Answers, câu 2`), so this one starts 30s later, as a separate press.
        now[0] += fetches.REUSE_SECONDS
        got = asyncio.run(worktrees.refresh_base(self.repo, "0001_a", self.data))
        self.assertTrue(got["fresh"])
        self.assertEqual(got["fetch"]["outcome"], "fetched")
        self.assertFalse(record.exists())

    def test_a_fetch_under_thirty_seconds_old_is_reused_and_the_tree_stays(self):
        """`0048` spec C1, recorded as behaviour: a step alone, started under 30s after
        another fetch of the same clone, does not fetch — and so does not see a commit
        pushed in between."""
        self._own_clock()
        before = git(self.tree, "rev-parse", "HEAD")
        asyncio.run(worktrees.refresh_base(self.repo, "0001_a", self.data))
        self._advance_remote()
        got = asyncio.run(worktrees.refresh_base(self.repo, "0001_a", self.data))
        self.assertEqual(got["fetch"], {"outcome": "reused", "attempts": 0, "age": 0.0})
        self.assertTrue(got["fresh"])
        self.assertEqual(got["sha"], before[:7])
        self.assertEqual(git(self.tree, "rev-parse", "HEAD"), before)

    def test_no_worktree_to_refresh_is_reported_not_raised(self):
        got = asyncio.run(worktrees.refresh_base(self.repo, "0002_b", self.data))
        self.assertEqual(got, {
            "ref": "origin/main", "sha": "", "fresh": False, "reason": "no worktree to refresh",
            "fetch": {"outcome": "failed", "attempts": 0, "age": None},
        })


class SwitchingOntoAnExistingBranch(Repo):
    """`0030_a-unit-branch-starts-from-a-stale-main` plan step 2 and R2/R4/R7: opening a
    branch already cut at a terminal fetches first, and reports how far behind it is.

    This is the "unit already has a (still detached) tree" half of `_fetch_or_refuse`'s
    two callers (review round 1 F2); `Ensuring`'s
    `test_a_broken_origin_refuses_to_open_a_branch_when_the_unit_never_had_a_tree` is the
    other half, and both must refuse alike."""

    def setUp(self):
        super().setUp()
        asyncio.run(worktrees.ensure(self.repo, "0001_a", None, self.data))
        git(self.repo, "branch", "fix/a", self.main)

    def test_a_broken_origin_refuses_to_open_the_branch(self):
        tree = worktrees.path(self.repo, "0001_a", self.data)
        git(tree, "remote", "set-url", "origin", str(Path(self._tmp.name) / "gone.git"))
        with self.assertRaises(GitError) as caught:
            asyncio.run(worktrees.ensure(self.repo, "0001_a", "fix/a", self.data))
        self.assertIn("was not opened", str(caught.exception))
        self.assertEqual(git(tree, "branch", "--show-current"), "")

    def test_the_branch_is_opened_behind_and_says_so(self):
        new = self._advance_remote()
        made = asyncio.run(worktrees.ensure(self.repo, "0001_a", "fix/a", self.data))
        self.assertEqual(made["branch"], "fix/a")
        self.assertEqual(made["base"]["ref"], "origin/main")
        self.assertEqual(made["base"]["sha"], new[:7])
        self.assertFalse(made["base"]["fresh"])
        self.assertEqual(made["base"]["behind"], 1)
        self.assertIn("update-branch", made["base"]["reason"])
        tree = worktrees.path(self.repo, "0001_a", self.data)
        self.assertEqual(git(tree, "branch", "--show-current"), "fix/a")

    def test_the_branch_is_fresh_when_nothing_new_landed(self):
        made = asyncio.run(worktrees.ensure(self.repo, "0001_a", "fix/a", self.data))
        self.assertTrue(made["base"]["fresh"])
        self.assertEqual(made["base"]["behind"], 0)
        self.assertEqual(made["base"]["reason"], "")

    def test_the_base_says_how_its_fetch_went(self):
        # `0048` R6, on the path that opens a tree onto an existing branch.
        made = asyncio.run(worktrees.ensure(self.repo, "0001_a", "fix/a", self.data))
        fetched = made["base"]["fetch"]
        self.assertEqual((fetched["outcome"], fetched["attempts"]), ("fetched", 1))
        self.assertLess(fetched["age"], fetches.REUSE_SECONDS)

    def test_a_fetch_older_than_thirty_seconds_is_not_fresh_and_says_so(self):
        # `0048` R7. Only reachable through a slow fetch; the age is handed in here.
        tree = worktrees.path(self.repo, "0001_a", self.data)
        head = git(tree, "rev-parse", "HEAD")
        old = {"outcome": "joined", "attempts": 1, "age": 30.0}
        got = asyncio.run(worktrees._base_against_origin(tree, "fix/a", head, old))
        self.assertEqual((got["behind"], got["fresh"]), (0, False))
        self.assertIn("30.0s ago", got["reason"])
        self.assertIs(got["fetch"], old)


class Preparing(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tree = Path(self._tmp.name) / "0001_a"
        self.tree.mkdir()

    def test_commands_are_guessed_from_the_files_present(self):
        self.assertEqual(worktrees.commands(self.tree), [])
        (self.tree / "uv.lock").write_text("", encoding="utf-8")
        (self.tree / "package-lock.json").write_text("{}", encoding="utf-8")
        (self.tree / "pyproject.toml").write_text(
            '[project]\nname="x"\n[project.scripts]\ncoscc-build = "coscc.build:main"\n',
            encoding="utf-8",
        )
        self.assertEqual(
            worktrees.commands(self.tree),
            [["uv", "sync", "--frozen"], ["npm", "ci"], ["uv", "run", "coscc-build"]],
        )

    def test_nothing_to_prepare_is_ok_and_recorded_beside_the_tree(self):
        got = asyncio.run(worktrees.prepare(self.tree))
        self.assertTrue(got["ok"])
        self.assertEqual(worktrees.read_prepare(self.tree), got)
        self.assertFalse((self.tree / f"{self.tree.name}.prepare.json").exists())

    def test_a_broken_package_lock_reports_npm_ci_and_its_exit_code(self):
        (self.tree / "package.json").write_text('{"name":"x","version":"1.0.0"}', encoding="utf-8")
        (self.tree / "package-lock.json").write_text("{ not json", encoding="utf-8")
        got = asyncio.run(worktrees.prepare(self.tree))
        self.assertFalse(got["ok"])
        self.assertEqual(got["command"], "npm ci")
        self.assertNotEqual(got["exit_code"], 0)
        self.assertIn("npm ci", worktrees.describe_failure(got))

    def test_it_stops_at_the_first_failure(self):
        (self.tree / "uv.lock").write_text("", encoding="utf-8")
        (self.tree / "package-lock.json").write_text("{}", encoding="utf-8")
        ran = []

        async def run(argv, cwd, env):
            ran.append(argv[0])
            return 3, "boom"

        got = asyncio.run(worktrees.prepare(self.tree, run=run))
        self.assertEqual(ran, ["uv"])
        self.assertEqual((got["command"], got["exit_code"], got["tail"]), ("uv sync --frozen", 3, "boom"))

    def test_the_environment_is_built_and_points_into_the_tree(self):
        pkg_bin = str(Path(coscc.__file__).resolve().parent / "bin")
        ws = Path(self._tmp.name) / "ws"
        with mock.patch.dict(os.environ, {
            "CLAUDE_CODE_OAUTH_TOKEN": "sk", "ANTHROPIC_API_KEY": "sk", "COS_TOOLS": "Bash",
            "__REFLEX_SKIP_COMPILE": "1", "REFLEX_WEB_WORKDIR": "/installed/_web",
            "PATH": os.pathsep.join([str(ws / ".venv" / "bin"), pkg_bin, "/usr/bin"]),
        }):
            env = worktrees.prepare_env(self.tree, ws)
        self.assertEqual(env["VIRTUAL_ENV"], str(self.tree / ".venv"))
        self.assertEqual(env["REFLEX_WEB_WORKDIR"], str(self.tree / ".web"))
        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertFalse([k for k in env if k.startswith(("CLAUDE", "ANTHROPIC", "COS_", "__REFLEX_"))])


class Removing(Repo):
    """R10: all four conditions, or nothing is touched."""

    URL = "https://github.com/o/r/pull/1"

    def setUp(self):
        super().setUp()
        git(self.repo, "branch", "fix/a", self.main)
        asyncio.run(worktrees.ensure(self.repo, "0001_a", "fix/a", self.data))
        self.tree = worktrees.path(self.repo, "0001_a", self.data)
        self.unit = {"name": "0001_a", "next": "finished", "pr": {"url": self.URL}}

    def gh(self, state="MERGED", head=None):
        async def run(argv, cwd, stdin):
            return 0, json.dumps({"state": state, "headRefOid": head or self.main}), ""
        return run

    def remove(self, unit=None, gh=None):
        return asyncio.run(worktrees.remove_if_finished(
            self.repo, "0001_a", unit or self.unit, self.data, gh=gh or self.gh()))

    def test_all_four_hold_and_the_tree_and_branch_go(self):
        got = self.remove()
        self.assertTrue(got["removed"], got)
        self.assertFalse(self.tree.exists())
        self.assertEqual(git(self.repo, "branch", "--list", "fix/a"), "")

    def test_not_finished_touches_nothing(self):
        got = self.remove(unit={**self.unit, "next": "ship"})
        self.assertFalse(got["removed"])
        self.assertTrue(self.tree.exists())

    def test_an_open_pull_request_touches_nothing(self):
        self.assertFalse(self.remove(gh=self.gh(state="OPEN"))["removed"])
        self.assertTrue(self.tree.exists())

    def test_a_dirty_tree_touches_nothing(self):
        (self.tree / "x.txt").write_text("x", encoding="utf-8")
        self.assertFalse(self.remove()["removed"])
        self.assertTrue(self.tree.exists())

    def test_a_dirty_tree_is_refused_without_asking_gh(self):
        """`0017` review F4: the board calls this on every read; a dirty tree must not
        cost a network call each time."""
        asked = []

        async def gh(argv, cwd, stdin):
            asked.append(argv)
            return 0, json.dumps({"state": "MERGED", "headRefOid": self.main}), ""
        (self.tree / "x.txt").write_text("x", encoding="utf-8")
        got = self.remove(gh=gh)
        self.assertEqual((got["removed"], asked), (False, []))

    def test_a_local_commit_after_the_merged_head_keeps_tree_and_branch(self):
        (self.tree / "g.txt").write_text("unpushed\n", encoding="utf-8")
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-q", "-m", "unpushed")
        got = self.remove()
        self.assertFalse(got["removed"])
        self.assertTrue(self.tree.exists())
        self.assertNotEqual(git(self.repo, "branch", "--list", "fix/a"), "")

    def test_gh_failing_is_a_reason_not_an_exception(self):
        async def broken(argv, cwd, stdin):
            return 1, "", "not logged in"
        got = self.remove(gh=broken)
        self.assertEqual((got["removed"], got["reason"]), (False, "not logged in"))


if __name__ == "__main__":
    unittest.main()
