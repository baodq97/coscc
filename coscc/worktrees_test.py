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
from coscc import units, worktrees
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
        remote = base / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
        self.repo = base / "repo"
        subprocess.run(["git", "clone", "-q", str(remote), str(self.repo)], check=True, capture_output=True)
        (self.repo / "f.txt").write_text("one\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "one")
        git(self.repo, "push", "-q", "origin", "main")
        self.main = git(self.repo, "rev-parse", "main")


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

    def test_a_clean_root_on_the_units_branch_is_moved_back_to_main(self):
        git(self.repo, "switch", "-q", "-c", "fix/a")
        made = asyncio.run(worktrees.ensure(self.repo, "0001_a", "fix/a", self.data))
        self.assertTrue(made["switched"])
        self.assertEqual(git(self.repo, "branch", "--show-current"), "main")
        self.assertEqual(made["branch"], "fix/a")

    def test_a_dirty_root_on_the_units_branch_is_refused_and_left_alone(self):
        git(self.repo, "switch", "-q", "-c", "fix/a")
        (self.repo / "f.txt").write_text("mine\n", encoding="utf-8")
        with self.assertRaises(GitError) as caught:
            asyncio.run(worktrees.ensure(self.repo, "0001_a", "fix/a", self.data))
        self.assertIn("uncommitted", str(caught.exception))
        self.assertEqual(git(self.repo, "branch", "--show-current"), "fix/a")
        self.assertEqual((self.repo / "f.txt").read_text(), "mine\n")


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
