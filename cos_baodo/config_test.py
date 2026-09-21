"""Tests for the config seam.

The first test is the important one. `spec.md` C2 puts the burden on anyone loosening the
default: it has to be a deliberate edit to a test that says what it is protecting, not a
value that drifts because nobody was watching.
"""

import unittest

from cos_baodo.config import Config, from_env


class DefaultsAreTheSafePosture(unittest.TestCase):
    def test_default_is_no_tools_at_all(self):
        # spec.md C2: the first agent profile is chat only — not even a read tool.
        # Loosening this means editing this test by name. That is the point of it.
        self.assertEqual(from_env({}).effective_tools(), [])

    def test_write_and_exec_off_by_default(self):
        self.assertFalse(from_env({}).allow_write_and_exec)

    def test_bypass_permissions_off_by_default(self):
        self.assertFalse(from_env({}).bypass_permissions)
        self.assertEqual(from_env({}).permission_mode(), "default")

    def test_resuming_foreign_sessions_off_by_default(self):
        self.assertFalse(from_env({}).resume_foreign_sessions)


class Knob2ActuallySubtracts(unittest.TestCase):
    def test_write_and_exec_tools_are_stripped_while_the_knob_is_off(self):
        c = Config(tools=("Read", "Bash", "Write", "Glob"))
        self.assertEqual(c.effective_tools(), ["Read", "Glob"])

    def test_the_knob_lets_them_through_when_on(self):
        c = Config(tools=("Read", "Bash"), allow_write_and_exec=True)
        self.assertEqual(c.effective_tools(), ["Read", "Bash"])

    def test_the_knob_alone_grants_nothing(self):
        # Knob 2 permits; knob 1 supplies. On its own it must not add a tool.
        self.assertEqual(Config(allow_write_and_exec=True).effective_tools(), [])


class Knob3IsReachableOnlyFromTheEnvironment(unittest.TestCase):
    def test_bypass_turns_on_from_the_environment(self):
        c = from_env({"COS_BYPASS_PERMISSIONS": "1"})
        self.assertTrue(c.bypass_permissions)
        self.assertEqual(c.permission_mode(), "bypassPermissions")

    def test_config_is_frozen_so_a_request_handler_cannot_raise_privilege(self):
        # "Not settable over HTTP" has to be structural. A handler holding the config
        # object must not be able to write to it.
        c = from_env({})
        with self.assertRaises(Exception):
            c.bypass_permissions = True  # type: ignore[misc]

    def test_there_is_no_mutator_left_on_the_config(self):
        """`0003` OQ6, answered by deleting rather than documenting.

        `with_workspaces` was the app's one config mutator. Its docstring claimed the
        proof command used it; the proof built a `Config` directly and never called it, so
        only its own test kept it alive. The store now owns changing the workspace list,
        and a second way to do it would be a second thing to reason about.
        """
        import cos_baodo.config as config

        self.assertFalse([n for n in dir(config) if n.startswith("with_")])


class Knob4GuardsResume(unittest.TestCase):
    def test_off_means_only_sessions_the_app_created(self):
        c = from_env({})
        self.assertTrue(c.may_resume(session_created_here=True))
        self.assertFalse(c.may_resume(session_created_here=False))

    def test_on_means_any_session(self):
        c = from_env({"COS_RESUME_FOREIGN_SESSIONS": "yes"})
        self.assertTrue(c.may_resume(session_created_here=False))


class Parsing(unittest.TestCase):
    def test_flags_accept_the_usual_spellings_and_reject_the_rest(self):
        for raw in ("1", "true", "TRUE", "yes", "on"):
            self.assertTrue(from_env({"COS_ALLOW_WRITE_AND_EXEC": raw}).allow_write_and_exec, raw)
        for raw in ("0", "false", "no", "off", "", "maybe"):
            self.assertFalse(from_env({"COS_ALLOW_WRITE_AND_EXEC": raw}).allow_write_and_exec, raw)

    def test_lists_drop_blanks_and_whitespace(self):
        self.assertEqual(from_env({"COS_TOOLS": "Read, ,Glob , "}).tools, ("Read", "Glob"))

    def test_workspaces_default_to_the_working_directory(self):
        self.assertEqual(len(from_env({}).workspaces), 1)

    def test_loopback_by_default(self):
        # R5. A non-loopback bind has to be typed out by a human, not inherited.
        self.assertEqual(from_env({}).host, "127.0.0.1")


class WorkspaceMembership(unittest.TestCase):
    def test_a_path_outside_the_list_is_not_a_workspace(self):
        c = Config(workspaces=("/tmp",))
        self.assertFalse(c.is_workspace("/etc"))

    def test_traversal_cannot_smuggle_a_path_in(self):
        c = Config(workspaces=("/tmp",))
        self.assertFalse(c.is_workspace("/tmp/../etc"))
        self.assertTrue(c.is_workspace("/tmp/./"))


class TheCwdFallback(unittest.TestCase):
    """`0003` found this the hard way: the fallback made a count of 2 read as 3."""

    def test_no_working_folder_still_falls_back_to_cwd(self):
        # `0002` behaviour, unchanged. verify_0002.py depends on it.
        self.assertEqual(len(from_env({}).workspaces), 1)

    def test_a_working_folder_with_nothing_declared_means_no_env_workspaces(self):
        c = from_env({"COS_WORKING_DIR": "/tmp/ws"})
        self.assertEqual(c.workspaces, ())
        self.assertEqual(c.working_dir, "/tmp/ws")

    def test_declared_workspaces_are_kept_alongside_a_working_folder(self):
        c = from_env({"COS_WORKING_DIR": "/tmp/ws", "COS_WORKSPACES": "/a,/b"})
        self.assertEqual(c.workspaces, ("/a", "/b"))


if __name__ == "__main__":
    unittest.main()
