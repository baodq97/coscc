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
