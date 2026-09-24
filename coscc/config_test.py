"""Tests for the config seam.

The first test is the important one. `spec.md` C2 puts the burden on anyone loosening the
default: it has to be a deliberate edit to a test that says what it is protecting, not a
value that drifts because nobody was watching.
"""

import dataclasses
import unittest
from pathlib import Path

from coscc.config import Config, from_env
from coscc.data import Data


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
        """OQ6, answered by deleting rather than documenting.

        `with_workspaces` was the app's one config mutator. Its docstring claimed the
        proof command used it; the proof built a `Config` directly and never called it, so
        only its own test kept it alive. The store now owns changing the workspace list,
        and a second way to do it would be a second thing to reason about.
        """
        import coscc.config as config

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

    def test_every_interface_by_default_since_0011(self):
        # `0001` R5 said the opposite and meant it: a non-loopback bind had to be typed
        # out by a human rather than inherited. `0011` reversed it by decision, not by
        # drift -- see the module docstring in `coscc/config.py` for who and when, and
        # `coscc/run_test.py` for the warning that now carries the weight this default
        # used to carry.
        self.assertEqual(from_env({}).host, "0.0.0.0")

    def test_the_old_default_is_still_reachable_by_hand(self):
        self.assertEqual(from_env({"COS_HOST": "127.0.0.1"}).host, "127.0.0.1")

    def test_an_empty_setting_reads_as_unset(self):
        # `0017` review F1: `child_env` overrides every `COS_*` with "", so this is what
        # a session started by an app launched with `COS_HOST`/`COS_PORT` set reads.
        c = from_env({"COS_HOST": "", "COS_PORT": "", "COS_BYPASS_PERMISSIONS": ""})
        self.assertEqual((c.host, c.port, c.bypass_permissions), ("0.0.0.0", 8790, False))


class WorkspaceMembership(unittest.TestCase):
    def test_a_path_outside_the_list_is_not_a_workspace(self):
        c = Config(workspaces=("/tmp",))
        self.assertFalse(c.is_workspace("/etc"))

    def test_traversal_cannot_smuggle_a_path_in(self):
        c = Config(workspaces=("/tmp",))
        self.assertFalse(c.is_workspace("/tmp/../etc"))
        self.assertTrue(c.is_workspace("/tmp/./"))


class TheCwdFallback(unittest.TestCase):
    """Found the hard way: the fallback made a count of 2 read as 3."""

    def test_no_working_folder_still_falls_back_to_cwd(self):
        # Unchanged. verify_0001.py depends on it.
        self.assertEqual(len(from_env({}).workspaces), 1)

    def test_a_working_folder_with_nothing_declared_means_no_env_workspaces(self):
        c = from_env({"COS_WORKING_DIR": "/tmp/ws"})
        self.assertEqual(c.workspaces, ())
        self.assertEqual(c.working_dir, "/tmp/ws")

    def test_declared_workspaces_are_kept_alongside_a_working_folder(self):
        c = from_env({"COS_WORKING_DIR": "/tmp/ws", "COS_WORKSPACES": "/a,/b"})
        self.assertEqual(c.workspaces, ("/a", "/b"))


class TheDataDirectory(unittest.TestCase):
    """R1. The setting that says where the app keeps its own state."""

    def test_unset_means_the_module_default_rather_than_a_path_here(self):
        """`Config` carries `None`, and `data.Data` turns that into `~/.cos`.

        Two places knowing the default would be two places to change it. This test exists
        to keep the default out of this file.
        """
        self.assertIsNone(from_env({}).data_dir)
        self.assertEqual(Data(from_env({}).data_dir).root, Path("~/.cos").expanduser().resolve())

    def test_it_is_read_from_the_environment(self):
        self.assertEqual(from_env({"COS_DATA_DIR": "/tmp/cosdata"}).data_dir, "/tmp/cosdata")

    def test_blank_reads_as_unset_rather_than_as_the_current_directory(self):
        self.assertIsNone(from_env({"COS_DATA_DIR": "   "}).data_dir)

    def test_it_is_independent_of_the_working_folder(self):
        """`spec.md` R4: the data root holds no workspace, and moving one does not move the other."""
        c = from_env({"COS_WORKING_DIR": "/tmp/ws", "COS_DATA_DIR": "/tmp/cosdata"})
        self.assertEqual(c.working_dir, "/tmp/ws")
        self.assertEqual(c.data_dir, "/tmp/cosdata")

    def test_config_is_frozen_so_nothing_downstream_can_move_it(self):
        """The same mechanism as knob 3: no setter, so a request has no path to it."""
        c = from_env({})
        with self.assertRaises(dataclasses.FrozenInstanceError):
            c.data_dir = "/somewhere/else"  # type: ignore[misc]


class TheUpdaterSettings(unittest.TestCase):
    """`.cos/0068_updating-the-app-is-a-manual-reinstall` R2, R3 and R6."""

    def test_checking_for_updates_is_on_unless_turned_off(self):
        self.assertTrue(from_env({}).update_check)
        self.assertTrue(from_env({"COS_UPDATE_CHECK": ""}).update_check)
        self.assertTrue(from_env({"COS_UPDATE_CHECK": "1"}).update_check)
        self.assertFalse(from_env({"COS_UPDATE_CHECK": "0"}).update_check)

    def test_the_local_channel_is_unset_by_default(self):
        self.assertIsNone(from_env({}).update_local_from)
        self.assertEqual(from_env({"COS_UPDATE_LOCAL_FROM": " coscc "}).update_local_from, "coscc")

    def test_uv_is_looked_for_where_install_sh_looks_and_in_that_order(self):
        c = from_env({
            "PATH": "/usr/bin:/bin", "HOME": "/h", "UV_INSTALL_DIR": "/uvi", "XDG_BIN_HOME": "/xb",
        })
        self.assertEqual(
            c.uv_candidates,
            ("/usr/bin", "/bin", "/uvi", "/xb", "/h/.local/bin", "/h/.cargo/bin"),
        )

    def test_systemd_and_the_config_home_are_read_without_the_prefix(self):
        c = from_env({"INVOCATION_ID": "abc", "HOME": "/h"})
        self.assertEqual((c.invocation_id, c.config_home, c.home), ("abc", "/h/.config", "/h"))
        c = from_env({"XDG_CONFIG_HOME": "/cfg", "HOME": "/h"})
        self.assertEqual((c.invocation_id, c.config_home), (None, "/cfg"))

    def test_nothing_comes_from_anywhere_but_the_env_it_is_given(self):
        # The source is the dict handed in: a test's `{}` sees none of the running
        # process's `PATH`, `HOME` or `INVOCATION_ID`.
        c = from_env({})
        self.assertEqual(
            (c.invocation_id, c.config_home, c.uv_candidates, c.path_env, c.home),
            (None, "", (), "", ""),
        )


if __name__ == "__main__":
    unittest.main()
