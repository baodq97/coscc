"""Tests for the one module that runs an external process and touches the network.

Most of these assert that something does **not** happen. `spec.md` C2 is explicit that a
safety failure here has no symptom — nothing crashes, nothing logs, the token is simply in
a place it should not be. So the tests have to state the absence directly.

Nothing here reaches the network. The one clone that runs points at a host that does not
resolve, which exercises the failure path without depending on anyone's uptime.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc import gitops
from coscc.gitops import GitError, check_url, child_env


class UrlsRefusedBeforeGitExists(unittest.TestCase):
    def test_a_url_that_could_be_read_as_a_flag_is_refused(self):
        # argv already makes this safe; refusing it as well means a future switch to a
        # different runner cannot quietly turn it into an option.
        for bad in ("--upload-pack=touch /tmp/pwned", "-c", "--config=x"):
            with self.assertRaises(GitError, msg=bad):
                check_url(bad)

    def test_only_https_is_accepted(self):
        for bad in (
            "git@github.com:me/repo.git",
            "ssh://git@github.com/me/repo.git",
            "file:///etc",
            "http://example.com/repo.git",
            "/etc/passwd",
            "",
            "   ",
        ):
            with self.assertRaises(GitError, msg=bad):
                check_url(bad)

    def test_an_https_url_passes_and_is_trimmed(self):
        self.assertEqual(
            check_url("  https://example.com/r.git  "), "https://example.com/r.git"
        )


class TheChildEnvironmentCarriesNoSecret(unittest.TestCase):
    def test_the_login_token_never_reaches_the_child(self):
        with mock.patch.dict(
            os.environ,
            {"CLAUDE_CODE_OAUTH_TOKEN": "sk-secret", "ANTHROPIC_API_KEY": "sk-other"},
        ):
            env = child_env()
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", env)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertFalse(any("secret" in v for v in env.values()))

    def test_no_cos_knob_reaches_the_child(self):
        with mock.patch.dict(
            os.environ,
            {"COS_TOOLS": "Bash", "COS_BYPASS_PERMISSIONS": "1", "COS_WORKING_DIR": "/x"},
        ):
            env = child_env()
        self.assertFalse([k for k in env if k.startswith("COS_")])

    def test_the_environment_is_built_not_filtered(self):
        """A new secret must be excluded by default, not by remembering to exclude it."""
        with mock.patch.dict(os.environ, {"SOME_FUTURE_SECRET": "leak-me"}):
            env = child_env()
        self.assertNotIn("SOME_FUTURE_SECRET", env)
        self.assertEqual(
            set(env),
            {
                "PATH", "HOME", "GIT_TERMINAL_PROMPT", "GIT_ASKPASS",
                "SSH_ASKPASS", "GIT_CONFIG_NOSYSTEM", "LC_ALL",
            },
        )

    def test_asking_for_a_password_is_made_to_fail(self):
        env = child_env()
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertTrue(env["GIT_ASKPASS"].endswith("false"))


class FailureIsReportedNotSwallowed(unittest.TestCase):
    def test_cloning_a_host_that_does_not_resolve_fails_within_the_deadline(self):
        # The point is that it returns at all: `spec.md` R15 wants a private or
        # unreachable repo to fail rather than hang on a password nobody can type.
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "repo"
            with self.assertRaises(GitError) as e:
                asyncio.run(
                    gitops.clone(
                        "https://coscc-nonexistent.invalid/x.git", dest, timeout=30
                    )
                )
            self.assertTrue(str(e.exception).strip())
            self.assertFalse(dest.exists())

    def test_cloning_onto_an_existing_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "repo"
            dest.mkdir()
            with self.assertRaises(GitError):
                asyncio.run(gitops.clone("https://example.com/r.git", dest))

    def test_pulling_something_that_is_not_a_repo_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(GitError):
                asyncio.run(gitops.pull(Path(d)))

    def test_a_timeout_is_reported_as_a_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(gitops, "_run", side_effect=GitError("git timed out after 1s: git clone")):
                with self.assertRaises(GitError) as e:
                    asyncio.run(gitops.clone("https://example.com/r.git", Path(d) / "x"))
            self.assertIn("timed out", str(e.exception))


if __name__ == "__main__":
    unittest.main()


class TheAppMayCreateABranchAndNothingElse(unittest.TestCase):
    """`0014` R5, one test per forbidden thing.

    Written as refusals rather than as an absence, because "the app cannot push" is not
    checkable by looking at code that does not exist. What is checkable is that the one
    entry point which touches somebody else's git takes a branch name and nothing else,
    and refuses everything that is not one.

    Real repositories, not mocks: what is under test is an agreement with `git` about what
    `switch -c <name> main` does when the branch exists, when the trunk does not, and when
    the name starts with a dash.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        self._git("init", "-q", "-b", "main")
        (self.repo / "README.md").write_text("x\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "first")

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), "-c", "user.name=T",
             "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
            capture_output=True, text=True, check=True,
        ).stdout

    @property
    def head(self) -> str:
        return self._git("rev-parse", "HEAD").strip()

    def test_it_cuts_the_branch_from_the_commit_it_is_given_and_switches_to_it(self):
        base = self.head
        asyncio.run(gitops.create_branch(self.repo, "feat/a-problem", base))
        self.assertEqual(asyncio.run(gitops.current_branch(self.repo)), "feat/a-problem")
        self.assertEqual(self._git("rev-parse", "feat/a-problem").strip(), base)

    def test_it_will_not_touch_the_trunk(self):
        with self.assertRaises(GitError) as caught:
            asyncio.run(gitops.create_branch(self.repo, "main", self.head))
        self.assertIn("trunk", str(caught.exception))
        self.assertEqual(asyncio.run(gitops.current_branch(self.repo)), "main")

    def test_it_refuses_a_branch_that_already_exists_rather_than_joining_it(self):
        asyncio.run(gitops.create_branch(self.repo, "feat/a-problem", self.head))
        self._git("switch", "-q", "main")
        with self.assertRaises(GitError) as caught:
            asyncio.run(gitops.create_branch(self.repo, "feat/a-problem", self.head))
        self.assertIn("already exists", str(caught.exception))

    def test_a_name_that_is_not_a_branch_name_never_reaches_git(self):
        # `--force`-shaped input is the one that matters: a leading dash turns a name into
        # an option, and an option is not something a caller gets to choose here.
        for bad in ("--force", "-x", "feat/../etc", "Feat/Problem", "", "x", "feat/"):
            with self.assertRaises(GitError, msg=bad):
                asyncio.run(gitops.create_branch(self.repo, bad, self.head))

    def test_a_base_that_is_not_a_full_sha_never_reaches_git(self):
        for bad in ("main", "origin/main", "--orphan", self.head[:7], "", "HEAD"):
            with self.assertRaises(GitError, msg=bad):
                asyncio.run(gitops.create_branch(self.repo, "feat/a-problem", bad))
        self.assertEqual(self._git("branch", "--list", "feat/a-problem").strip(), "")

    def test_it_is_not_a_way_to_push(self):
        asyncio.run(gitops.create_branch(self.repo, "feat/a-problem", self.head))
        # No remote is configured, so a push would fail loudly. The point is that nothing
        # tried: the branch exists only here.
        self.assertEqual(self._git("branch", "-r", "--format=%(refname:short)").strip(), "")

    def test_it_is_not_a_way_to_commit(self):
        before = self._git("rev-parse", "HEAD").strip()
        asyncio.run(gitops.create_branch(self.repo, "feat/a-problem", before))
        (self.repo / "new.txt").write_text("y\n", encoding="utf-8")
        self.assertEqual(self._git("rev-parse", "HEAD").strip(), before)
        # And the file it did not commit is still sitting there uncommitted.
        self.assertIn("new.txt", self._git("status", "--porcelain"))

    def test_a_directory_that_is_not_a_repository_is_refused_by_name(self):
        plain = Path(self._tmp.name) / "plain"
        plain.mkdir()
        for call in (
            lambda: gitops.create_branch(plain, "feat/a-problem", "0" * 40),
            lambda: gitops.current_branch(plain),
            lambda: gitops.fetch(plain),
            lambda: gitops.rev_parse(plain, "HEAD"),
        ):
            with self.assertRaises(GitError) as caught:
                asyncio.run(call())
            self.assertIn(str(plain), str(caught.exception))


class FetchingTheTrunkFromARemote(unittest.TestCase):
    """`0001_product-describes-a-state-it-is-not-in` R1 and R2, against a local bare remote.

    No network: the remote is a directory. What is under test is which ref moves, which
    does not, and that a failure carries git's own words.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.remote = root / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        seed = root / "seed"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(seed)], check=True, capture_output=True)
        self.seed = seed
        self._commit(seed, "one")
        self.repo = root / "repo"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(self.repo)], check=True)

    def _git(self, where: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(where), "-c", "user.name=T",
             "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def _commit(self, where: Path, text: str) -> str:
        (where / "f.txt").write_text(text + "\n", encoding="utf-8")
        self._git(where, "add", "-A")
        self._git(where, "commit", "-q", "-m", text)
        self._git(where, "push", "-q", "origin", "main")
        return self._git(where, "rev-parse", "HEAD")

    def test_fetch_brings_the_new_commit_and_moves_no_local_branch(self):
        local = self._git(self.repo, "rev-parse", "main")
        new = self._commit(self.seed, "two")
        asyncio.run(gitops.fetch(self.repo, "origin", "main"))
        self.assertEqual(asyncio.run(gitops.rev_parse(self.repo, "refs/remotes/origin/main")), new)
        self.assertEqual(self._git(self.repo, "rev-parse", "main"), local)

    def test_a_missing_remote_is_a_git_error_carrying_what_git_said(self):
        self._git(self.repo, "remote", "set-url", "origin", str(Path(self._tmp.name) / "gone.git"))
        with self.assertRaises(GitError) as caught:
            asyncio.run(gitops.fetch(self.repo, "origin", "main"))
        self.assertIn("gone.git", str(caught.exception))

    def test_a_remote_or_branch_shaped_like_a_flag_never_reaches_git(self):
        for remote, branch in (("--upload-pack=x", "main"), ("origin", "-x"), ("origin", "a:b"), ("", "main")):
            with self.assertRaises(GitError, msg=(remote, branch)):
                asyncio.run(gitops.fetch(self.repo, remote, branch))

    def test_rev_parse_of_an_unknown_ref_names_it(self):
        with self.assertRaises(GitError) as caught:
            asyncio.run(gitops.rev_parse(self.repo, "refs/remotes/nowhere/main"))
        self.assertIn("refs/remotes/nowhere/main", str(caught.exception))

    def test_a_branch_cut_from_the_fetched_sha_tracks_nothing(self):
        # Plan Risk 2: with a ref name as start point git would set an upstream of `main`.
        new = self._commit(self.seed, "two")
        asyncio.run(gitops.fetch(self.repo))
        asyncio.run(gitops.create_branch(self.repo, "fix/a-problem", new))
        self.assertEqual(self._git(self.repo, "rev-parse", "fix/a-problem"), new)
        got = subprocess.run(
            ["git", "-C", str(self.repo), "config", "branch.fix/a-problem.merge"],
            capture_output=True, text=True,
        )
        self.assertEqual(got.stdout.strip(), "")


class Worktrees(FetchingTheTrunkFromARemote):
    """`0017` plan step 2. The worktree commands, each with the refusal that bounds it."""

    def setUp(self):
        super().setUp()
        self.main = self._git(self.repo, "rev-parse", "main")
        self.tree = Path(self._tmp.name) / "trees" / "0001_a"

    def test_a_detached_tree_at_a_sha_is_listed_with_its_head(self):
        asyncio.run(gitops.worktree_add(self.repo, self.tree, self.main))
        trees = asyncio.run(gitops.worktree_list(self.repo))
        self.assertEqual(len(trees), 2)
        self.assertEqual(Path(trees[1]["path"]).resolve(), self.tree.resolve())
        self.assertEqual(trees[1]["head"], self.main)
        self.assertEqual(trees[1]["branch"], "")
        self.assertEqual(trees[0]["branch"], "main")

    def test_a_tree_on_an_existing_branch_carries_its_name(self):
        self._git(self.repo, "branch", "fix/a-problem", self.main)
        asyncio.run(gitops.worktree_add(self.repo, self.tree, "fix/a-problem"))
        trees = asyncio.run(gitops.worktree_list(self.repo))
        self.assertEqual(trees[1]["branch"], "fix/a-problem")

    def test_a_start_that_is_neither_a_sha_nor_a_unit_branch_never_reaches_git(self):
        for bad in ("main", "-b", "--force", "HEAD", self.main[:7], ""):
            with self.assertRaises(GitError, msg=bad):
                asyncio.run(gitops.worktree_add(self.repo, self.tree, bad))
        self.assertFalse(self.tree.exists())

    def test_remove_is_never_forced(self):
        asyncio.run(gitops.worktree_add(self.repo, self.tree, self.main))
        (self.tree / "dirty.txt").write_text("x\n", encoding="utf-8")
        with self.assertRaises(GitError):
            asyncio.run(gitops.worktree_remove(self.repo, self.tree))
        self.assertTrue((self.tree / "dirty.txt").exists())
        (self.tree / "dirty.txt").unlink()
        asyncio.run(gitops.worktree_remove(self.repo, self.tree))
        self.assertFalse(self.tree.exists())

    def test_the_workspace_itself_is_never_removed(self):
        with self.assertRaises(GitError):
            asyncio.run(gitops.worktree_remove(self.repo, self.repo))

    def test_switch_trunk_refuses_a_dirty_tree_and_moves_a_clean_one(self):
        self._git(self.repo, "switch", "-q", "-c", "fix/a-problem")
        (self.repo / "f.txt").write_text("changed\n", encoding="utf-8")
        self.assertFalse(asyncio.run(gitops.is_clean(self.repo)))
        with self.assertRaises(GitError):
            asyncio.run(gitops.switch_trunk(self.repo))
        self.assertEqual(self._git(self.repo, "branch", "--show-current"), "fix/a-problem")
        self._git(self.repo, "checkout", "--", "f.txt")
        self.assertTrue(asyncio.run(gitops.is_clean(self.repo)))
        asyncio.run(gitops.switch_trunk(self.repo))
        self.assertEqual(self._git(self.repo, "branch", "--show-current"), "main")

    def test_a_merged_branch_is_deleted_only_at_the_merged_head(self):
        self._git(self.repo, "branch", "fix/a-problem", self.main)
        merged = self.main
        # A local commit after the merged head: somebody's unpushed work.
        self._git(self.repo, "switch", "-q", "fix/a-problem")
        (self.repo / "g.txt").write_text("unpushed\n", encoding="utf-8")
        self._git(self.repo, "add", "-A")
        self._git(self.repo, "commit", "-q", "-m", "unpushed")
        self._git(self.repo, "switch", "-q", "main")
        self.assertFalse(asyncio.run(gitops.delete_merged_branch(self.repo, "fix/a-problem", merged)))
        self.assertNotEqual(self._git(self.repo, "branch", "--list", "fix/a-problem"), "")
        at = self._git(self.repo, "rev-parse", "fix/a-problem")
        self.assertTrue(asyncio.run(gitops.delete_merged_branch(self.repo, "fix/a-problem", at)))
        self.assertEqual(self._git(self.repo, "branch", "--list", "fix/a-problem"), "")
        # Gone already is not an error.
        self.assertFalse(asyncio.run(gitops.delete_merged_branch(self.repo, "fix/a-problem", at)))

    def test_main_is_never_deleted(self):
        with self.assertRaises(GitError):
            asyncio.run(gitops.delete_merged_branch(self.repo, "main", self.main))


class MeasuringAncestryAndDistance(unittest.TestCase):
    """`0030_a-unit-branch-starts-from-a-stale-main` plan step 1: `is_ancestor`, `count_missing`.

    A fixture of its own rather than a subclass of `FetchingTheTrunkFromARemote`: that
    class's own tests each push a second commit whose message is the literal `"two"`, and
    unittest runs every inherited test method against a subclass's `setUp` too — inheriting
    it here, with a `"two"` already pushed, would leave those tests nothing left to commit.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.remote = root / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        seed = root / "seed"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(seed)], check=True, capture_output=True)
        self.seed = seed
        self._commit(seed, "one")
        self.repo = root / "repo"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(self.repo)], check=True)
        self.one = self._git(self.repo, "rev-parse", "main")
        self.two = self._commit(self.seed, "two")

    def _git(self, where: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(where), "-c", "user.name=T",
             "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def _commit(self, where: Path, text: str) -> str:
        (where / "f.txt").write_text(text + "\n", encoding="utf-8")
        self._git(where, "add", "-A")
        self._git(where, "commit", "-q", "-m", text)
        self._git(where, "push", "-q", "origin", "main")
        return self._git(where, "rev-parse", "HEAD")

    def test_an_ancestor_is_reported_true(self):
        asyncio.run(gitops.fetch(self.repo))
        self.assertTrue(asyncio.run(gitops.is_ancestor(self.repo, self.one, self.two)))

    def test_a_commit_is_its_own_ancestor(self):
        self.assertTrue(asyncio.run(gitops.is_ancestor(self.repo, self.one, self.one)))

    def test_a_descendant_is_not_an_ancestor_of_its_own_parent(self):
        asyncio.run(gitops.fetch(self.repo))
        self.assertFalse(asyncio.run(gitops.is_ancestor(self.repo, self.two, self.one)))

    def test_an_unknown_commit_is_a_git_error_not_a_false(self):
        # Exit 1 means "no"; an unknown commit is neither 0 nor 1, and must not be read as no.
        with self.assertRaises(GitError):
            asyncio.run(gitops.is_ancestor(self.repo, self.one, "f" * 40))

    def test_a_ref_shaped_argument_never_reaches_git(self):
        for bad in ("main", "HEAD", self.one[:7], "", "origin/main"):
            with self.assertRaises(GitError, msg=bad):
                asyncio.run(gitops.is_ancestor(self.repo, bad, self.one))
            with self.assertRaises(GitError, msg=bad):
                asyncio.run(gitops.is_ancestor(self.repo, self.one, bad))

    def test_count_missing_counts_what_have_lacks(self):
        asyncio.run(gitops.fetch(self.repo))
        self.assertEqual(asyncio.run(gitops.count_missing(self.repo, self.one, self.two)), 1)

    def test_count_missing_is_zero_when_equal(self):
        self.assertEqual(asyncio.run(gitops.count_missing(self.repo, self.one, self.one)), 0)

    def test_count_missing_the_other_direction_is_zero(self):
        asyncio.run(gitops.fetch(self.repo))
        self.assertEqual(asyncio.run(gitops.count_missing(self.repo, self.two, self.one)), 0)

    def test_a_ref_shaped_argument_never_reaches_git_for_count_missing(self):
        for bad in ("main", "HEAD", self.one[:7], ""):
            with self.assertRaises(GitError, msg=bad):
                asyncio.run(gitops.count_missing(self.repo, bad, self.one))


class AdvancingADetachedWorktree(unittest.TestCase):
    """`0030_a-unit-branch-starts-from-a-stale-main` plan step 1: `advance_detached`.

    A fixture of its own rather than a subclass of `Worktrees`: this one's `setUp` already
    puts the tree where `worktree_add` left it, and unittest would run `Worktrees`'s own
    tests against that same already-created tree too, where several expect a virgin one.

    One test moves a clean detached tree forward; three refuse it, each leaving HEAD
    exactly where it was — a refusal that moved the tree partway would be worse than one
    that did nothing.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.remote = root / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        seed = root / "seed"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(seed)], check=True, capture_output=True)
        self.seed = seed
        self._commit(seed, "one")
        self.repo = root / "repo"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(self.repo)], check=True)
        self.main = self._git(self.repo, "rev-parse", "main")
        self.tree = root / "trees" / "0001_a"
        asyncio.run(gitops.worktree_add(self.repo, self.tree, self.main))

    def _git(self, where: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(where), "-c", "user.name=T",
             "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def _commit(self, where: Path, text: str) -> str:
        (where / "f.txt").write_text(text + "\n", encoding="utf-8")
        self._git(where, "add", "-A")
        self._git(where, "commit", "-q", "-m", text)
        self._git(where, "push", "-q", "origin", "main")
        return self._git(where, "rev-parse", "HEAD")

    def test_a_clean_detached_tree_is_moved_to_the_fetched_sha(self):
        new = self._commit(self.seed, "two")
        asyncio.run(gitops.fetch(self.tree))
        asyncio.run(gitops.advance_detached(self.tree, new))
        self.assertEqual(self._git(self.tree, "rev-parse", "HEAD"), new)
        self.assertEqual(self._git(self.tree, "branch", "--show-current"), "")
        # `main` itself never moves — only the tree does.
        self.assertEqual(self._git(self.repo, "rev-parse", "main"), self.main)

    def test_a_tree_on_its_own_branch_is_refused(self):
        self._git(self.tree, "switch", "-q", "-c", "fix/a-problem")
        new = self._commit(self.seed, "two")
        asyncio.run(gitops.fetch(self.tree))
        with self.assertRaises(GitError) as caught:
            asyncio.run(gitops.advance_detached(self.tree, new))
        self.assertIn("fix/a-problem", str(caught.exception))
        self.assertEqual(self._git(self.tree, "rev-parse", "HEAD"), self.main)

    def test_a_dirty_tree_is_refused(self):
        new = self._commit(self.seed, "two")
        asyncio.run(gitops.fetch(self.tree))
        (self.tree / "dirty.txt").write_text("x\n", encoding="utf-8")
        with self.assertRaises(GitError):
            asyncio.run(gitops.advance_detached(self.tree, new))
        self.assertEqual(self._git(self.tree, "rev-parse", "HEAD"), self.main)
        self.assertTrue((self.tree / "dirty.txt").exists())

    def test_a_head_that_is_not_an_ancestor_of_the_target_is_refused(self):
        # A commit made directly on the detached tree: unpushed, and not on the remote.
        (self.tree / "local.txt").write_text("mine\n", encoding="utf-8")
        self._git(self.tree, "add", "-A")
        self._git(self.tree, "commit", "-q", "-m", "local")
        local_head = self._git(self.tree, "rev-parse", "HEAD")
        new = self._commit(self.seed, "two")
        asyncio.run(gitops.fetch(self.tree))
        with self.assertRaises(GitError) as caught:
            asyncio.run(gitops.advance_detached(self.tree, new))
        self.assertIn("ancestor", str(caught.exception))
        self.assertEqual(self._git(self.tree, "rev-parse", "HEAD"), local_head)

    def test_a_sha_that_is_not_a_full_sha_never_reaches_git(self):
        for bad in ("main", "HEAD", self.main[:7], ""):
            with self.assertRaises(GitError, msg=bad):
                asyncio.run(gitops.advance_detached(self.tree, bad))


class ReadingAFailedAttemptsTree(unittest.TestCase):
    """`0019` plan step 1: the four read-only functions `snapshot` builds on.

    Everything here reads a temporary repository, never writes to one — these are the
    functions a stopped step's record is built from, so what they report has to match
    `git log`/`git status` run by hand, byte for byte.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        self._git("init", "-q", "-b", "main")
        (self.repo / "a.txt").write_text("one\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "first")
        self.base = self._git("rev-parse", "HEAD").strip()

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), "-c", "user.name=T",
             "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
            capture_output=True, text=True, check=True,
        ).stdout

    def test_head_and_branch_names_the_branch(self):
        self._git("switch", "-q", "-c", "fix/a-problem")
        (self.repo / "b.txt").write_text("two\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "second")
        head = self._git("rev-parse", "HEAD").strip()
        got_head, branch = asyncio.run(gitops.head_and_branch(self.repo))
        self.assertEqual(got_head, head)
        self.assertEqual(branch, "fix/a-problem")

    def test_head_and_branch_reports_detached_on_a_detached_tree(self):
        self._git("switch", "-q", "--detach", "HEAD")
        _, branch = asyncio.run(gitops.head_and_branch(self.repo))
        self.assertEqual(branch, "detached")

    def test_merge_base_falls_back_to_local_main_without_an_origin(self):
        self._git("switch", "-q", "-c", "fix/a-problem")
        (self.repo / "b.txt").write_text("two\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "second")
        sha, ref = asyncio.run(gitops.merge_base(self.repo))
        self.assertEqual(sha, self.base)
        self.assertEqual(ref, "refs/heads/main")

    def test_merge_base_prefers_origin_main_when_it_exists(self):
        remote = Path(self._tmp.name) / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
        self._git("remote", "add", "origin", str(remote))
        self._git("push", "-q", "origin", "main")
        self._git("switch", "-q", "-c", "fix/a-problem")
        (self.repo / "b.txt").write_text("two\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "second")
        sha, ref = asyncio.run(gitops.merge_base(self.repo))
        self.assertEqual(sha, self.base)
        self.assertEqual(ref, "refs/remotes/origin/main")

    def test_log_range_matches_git_log_order_and_split(self):
        self._git("switch", "-q", "-c", "fix/a-problem")
        (self.repo / "b.txt").write_text("two\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "second commit")
        (self.repo / "c.txt").write_text("three\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "third commit")
        head = self._git("rev-parse", "HEAD").strip()
        want = self._git("log", "--format=%H %s", f"{self.base}..{head}")
        commits = asyncio.run(gitops.log_range(self.repo, self.base, head))
        got = "\n".join(f"{c['sha']} {c['subject']}" for c in commits)
        self.assertEqual(got, want.strip())

    def test_log_range_refuses_anything_that_is_not_a_full_sha(self):
        for bad in ("main", "HEAD", self.base[:7], ""):
            with self.assertRaises(GitError, msg=bad):
                asyncio.run(gitops.log_range(self.repo, bad, self.base))

    def test_diff_names_matches_git_diff_name_only(self):
        (self.repo / "a.txt").write_text("one changed\n", encoding="utf-8")
        (self.repo / "b.txt").write_text("two\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "second")
        head = self._git("rev-parse", "HEAD").strip()
        want = self._git("diff", f"{self.base}..{head}", "--name-only").splitlines()
        got = asyncio.run(gitops.diff_names(self.repo, self.base, head))
        self.assertEqual(got, want)
        self.assertEqual(got, ["a.txt", "b.txt"])

    def test_diff_names_refuses_a_short_sha_before_git_runs(self):
        with mock.patch.object(gitops, "_run") as run:
            for bad in (self.base[:7], "HEAD", ""):
                with self.assertRaises(GitError, msg=bad):
                    asyncio.run(gitops.diff_names(self.repo, bad, self.base))
            run.assert_not_called()

    def test_diff_names_on_a_commit_the_repository_lacks_is_an_error(self):
        with self.assertRaises(GitError):
            asyncio.run(gitops.diff_names(self.repo, "0" * 40, self.base))

    def test_status_porcelain_keeps_the_leading_space(self):
        (self.repo / "a.txt").write_text("one changed\n", encoding="utf-8")
        want = self._git("status", "--porcelain")
        lines = asyncio.run(gitops.status_porcelain(self.repo))
        self.assertEqual("\n".join(lines) + ("\n" if lines else ""), want)
        self.assertTrue(lines[0].startswith(" M"), lines[0])

    def test_status_porcelain_is_empty_on_a_clean_tree(self):
        self.assertEqual(asyncio.run(gitops.status_porcelain(self.repo)), [])

    def test_a_directory_that_is_not_a_repository_is_refused_by_all_four(self):
        not_repo = Path(self._tmp.name) / "not-a-repo"
        not_repo.mkdir()
        with self.assertRaises(GitError):
            asyncio.run(gitops.head_and_branch(not_repo))
        with self.assertRaises(GitError):
            asyncio.run(gitops.merge_base(not_repo))
        with self.assertRaises(GitError):
            asyncio.run(gitops.log_range(not_repo, self.base, self.base))
        with self.assertRaises(GitError):
            asyncio.run(gitops.status_porcelain(not_repo))


class IntegratingABranchThatFellBehind(unittest.TestCase):
    """`0035` plan step 3. A unit branch on a bare remote, rebased "on GitHub's side" by a
    second clone, and the local tree following it — or refusing, changing nothing.

    Borrows `AdvancingADetachedWorktree`'s fixture without subclassing it, so its tests do
    not run a second time against this tree."""

    BRANCH = "feat/x"
    _git = AdvancingADetachedWorktree._git
    _commit = AdvancingADetachedWorktree._commit

    def setUp(self):
        AdvancingADetachedWorktree.setUp(self)
        # Remove the detached tree the parent made; this fixture wants the branch.
        self._git(self.repo, "worktree", "remove", "--force", str(self.tree))
        self._git(self.seed, "switch", "-q", "-c", self.BRANCH)
        (self.seed / "g.txt").write_text("branch\n", encoding="utf-8")
        self._git(self.seed, "add", "-A")
        self._git(self.seed, "commit", "-q", "-m", "branch work")
        self._git(self.seed, "push", "-q", "origin", self.BRANCH)
        self.old = self._git(self.seed, "rev-parse", "HEAD")
        self._git(self.repo, "fetch", "-q", "origin", f"+refs/heads/{self.BRANCH}:refs/remotes/origin/{self.BRANCH}")
        self._git(self.repo, "branch", self.BRANCH, f"origin/{self.BRANCH}")
        asyncio.run(gitops.worktree_add(self.repo, self.tree, self.BRANCH))
        # Main moves; the branch is rebased onto it on the remote, as update-branch would.
        self._git(self.seed, "switch", "-q", "main")
        self.main2 = self._commit(self.seed, "two")
        self._git(self.seed, "switch", "-q", self.BRANCH)
        self._git(self.seed, "rebase", "-q", "main")
        self._git(self.seed, "push", "-q", "--force", "origin", self.BRANCH)
        self.new = self._git(self.seed, "rev-parse", "HEAD")

    def test_the_read_helpers(self):
        asyncio.run(gitops.fetch(self.repo))
        base = asyncio.run(gitops.merge_base_of(self.repo, self.old, self.main2))
        self.assertEqual(base, self.main)
        commits = asyncio.run(gitops.commits_between(self.repo, base, self.main2))
        self.assertEqual([c["subject"] for c in commits], ["two"])
        self.assertEqual(asyncio.run(gitops.files_of_commit(self.repo, self.main2)), ["f.txt"])
        self.assertEqual(asyncio.run(gitops.files_between(self.repo, base, self.old)), ["g.txt"])
        self.assertTrue(asyncio.run(gitops.has_commit(self.repo, self.old)))
        self.assertFalse(asyncio.run(gitops.has_commit(self.repo, "f" * 40)))
        self.assertFalse(asyncio.run(gitops.rebase_in_progress(self.tree)))

    def test_a_clean_tree_on_its_branch_follows_the_rebased_head(self):
        asyncio.run(gitops.reset_branch_to(self.tree, self.BRANCH, self.old, self.new))
        self.assertEqual(self._git(self.tree, "rev-parse", "HEAD"), self.new)
        self.assertEqual(self._git(self.tree, "branch", "--show-current"), self.BRANCH)

    def _refused(self, expected_old=None, new=None, want="nothing was moved"):
        with self.assertRaises(GitError) as caught:
            asyncio.run(gitops.reset_branch_to(self.tree, self.BRANCH, expected_old or self.old, new or self.new))
        self.assertIn(want, str(caught.exception))
        self.assertEqual(self._git(self.tree, "rev-parse", "HEAD"), self.old)

    def test_a_dirty_tree_is_refused(self):
        (self.tree / "g.txt").write_text("mine\n", encoding="utf-8")
        self._refused(want="uncommitted")

    def test_the_wrong_branch_is_refused(self):
        self._git(self.tree, "switch", "-q", "--detach")
        with self.assertRaises(GitError):
            asyncio.run(gitops.reset_branch_to(self.tree, self.BRANCH, self.old, self.new))
        self.assertEqual(self._git(self.tree, "rev-parse", "HEAD"), self.old)

    def test_a_head_other_than_expected_is_refused(self):
        self._refused(expected_old="e" * 40)

    def test_a_sha_the_remote_does_not_have_is_refused(self):
        self._refused(new="d" * 40)

    def test_a_stopped_rebase_is_seen_and_aborted(self):
        self._git(self.tree, "fetch", "-q", "origin")
        self._git(self.seed, "switch", "-q", "main")
        (self.seed / "g.txt").write_text("main's\n", encoding="utf-8")
        self._git(self.seed, "add", "-A")
        self._git(self.seed, "commit", "-q", "-m", "main touches g")
        self._git(self.seed, "push", "-q", "origin", "main")
        self._git(self.tree, "fetch", "-q", "origin")
        subprocess.run(["git", "-C", str(self.tree), "-c", "user.name=T", "-c", "user.email=t@e.invalid",
                        "rebase", "origin/main"], capture_output=True)
        self.assertTrue(asyncio.run(gitops.rebase_in_progress(self.tree)))
        asyncio.run(gitops.abort_rebase(self.tree))
        self.assertFalse(asyncio.run(gitops.rebase_in_progress(self.tree)))
        self.assertEqual(self._git(self.tree, "rev-parse", "HEAD"), self.old)
