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
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cos_baodo import gitops
from cos_baodo.gitops import GitError, check_url, child_env


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
                        "https://cos-baodo-nonexistent.invalid/x.git", dest, timeout=30
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
