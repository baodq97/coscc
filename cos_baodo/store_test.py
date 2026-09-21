"""Tests for the workspace list, weighted towards what must NOT happen.

`spec.md` R12 and R21 are the reason this file is longer than the module deserves: the
store is the first thing in the app a user edits by hand, and the first thing whose
contents decide which directories the app will work in.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from cos_baodo.store import BadName, Busy, Store, clean_label, require_name, valid_name


class NamesThatMayNotBecomePaths(unittest.TestCase):
    def test_the_inputs_spec_r12_lists_are_all_refused(self):
        for bad in ("../x", "/etc", "a/b", "", ".", "..", "x" * 65, "a\\b", "a b", "a\n"):
            self.assertFalse(valid_name(bad), bad)
            with self.assertRaises(BadName, msg=bad):
                require_name(bad)

    def test_ordinary_names_pass(self):
        for ok in ("repo", "my-repo", "my_repo", "repo.git", "a", "x" * 64):
            self.assertTrue(valid_name(ok), ok)

    def test_a_refused_name_never_reaches_path_of(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(BadName):
                Store(d).path_of("../escape")


class PathsAreBuiltNotStored(unittest.TestCase):
    def test_the_file_contains_no_absolute_path(self):
        """The safety property, stated as a test.

        If an absolute path ever appears in the file, the shape argument in `store.py` is
        gone and only `is_under` is left holding the boundary.
        """
        with tempfile.TemporaryDirectory() as d:
            s = Store(d)
            s.add("repo", label="My repo")
            raw = s.path.read_text()
            self.assertNotIn(str(Path(d).resolve()), raw)
            self.assertIn('"name": "repo"', raw)

    def test_path_of_is_the_root_plus_the_name(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(Store(d).path_of("repo"), Path(d).resolve() / "repo")


class AHandEditedFileCannotWidenTheBoundary(unittest.TestCase):
    def test_an_entry_naming_an_outside_path_is_dropped(self):
        """`spec.md` R21, at the store layer.

        Writing `/etc` or `../x` into the file by hand must not make it a workspace. The
        name is rejected on read, so the entry simply does not exist.
        """
        with tempfile.TemporaryDirectory() as d:
            s = Store(d)
            s.path.write_text(json.dumps({
                "version": 1,
                "workspaces": [
                    {"name": "/etc"},
                    {"name": "../../etc"},
                    {"name": "ok"},
                ],
            }))
            self.assertEqual([e.name for e in s.entries()], ["ok"])
            self.assertFalse(s.resolves_to_entry("/etc"))

    def test_a_subdirectory_nobody_added_is_not_a_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(d)
            (Path(d) / "stray").mkdir()
            self.assertTrue(s.is_under(str(Path(d) / "stray")))
            self.assertFalse(s.resolves_to_entry(str(Path(d) / "stray")))

    def test_the_working_folder_itself_is_not_a_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(Store(d).is_under(d))

    def test_a_garbage_file_reads_as_empty_rather_than_crashing(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(d)
            s.path.write_text("not json at all")
            self.assertEqual(s.entries(), [])


class ListOperations(unittest.TestCase):
    def test_add_label_remove_round_trip_survives_a_new_store_object(self):
        # A new Store reads from disk, which is what "survives a restart" reduces to.
        with tempfile.TemporaryDirectory() as d:
            Store(d).add("a", "A")
            Store(d).add("b")
            Store(d).set_label("b", "B")
            self.assertEqual(
                {e.name: e.label for e in Store(d).entries()}, {"a": "A", "b": "B"}
            )
            Store(d).remove("a")
            self.assertEqual([e.name for e in Store(d).entries()], ["b"])

    def test_remove_does_not_touch_the_directory(self):
        # `spec.md` R18 and C6: the app has no undo and a clone may hold uncommitted work.
        with tempfile.TemporaryDirectory() as d:
            s = Store(d)
            target = s.path_of("repo")
            target.mkdir()
            s.add("repo")
            s.remove("repo")
            self.assertTrue(target.is_dir())

    def test_removing_something_absent_is_an_error_not_a_silent_success(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(KeyError):
                Store(d).remove("ghost")

    def test_adding_twice_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(d)
            s.add("repo", "first")
            s.add("repo", "second")
            self.assertEqual([(e.name, e.label) for e in s.entries()], [("repo", "second")])

    def test_labels_are_bounded_and_trimmed(self):
        self.assertEqual(clean_label("  hi  "), "hi")
        self.assertEqual(len(clean_label("x" * 500)), 200)
        self.assertEqual(clean_label(None), "")

    def test_a_write_leaves_no_temp_file_behind(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(d)
            s.add("repo")
            leftovers = [p.name for p in Path(d).glob(".cos-baodo-*.tmp")]
            self.assertEqual(leftovers, [])


HOLDER = """
import fcntl, sys, time
fd = open(sys.argv[1], "a+")
fcntl.flock(fd, fcntl.LOCK_EX)
sys.stdout.write("held\\n")
sys.stdout.flush()
time.sleep(60)
"""


class TheLockIsAcrossProcesses(unittest.TestCase):
    """`spec.md` R1-R4. `0005` measured the old arrangement losing 12 of 20 entries.

    These use a real child process, not a second descriptor in this one. The loss being
    fixed was between processes, and a same-process stand-in would pass even if the lock
    were a threading lock again.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _holder(self, store: Store) -> subprocess.Popen:
        """A child holding the lock. Returns once it says it has it."""
        store.lock_path.parent.mkdir(parents=True, exist_ok=True)
        child = subprocess.Popen(
            [sys.executable, "-c", HOLDER, str(store.lock_path)],
            stdout=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        self.assertEqual(child.stdout.readline().strip(), "held", "child never took the lock")
        return child

    def test_waiting_for_a_held_lock_ends_in_an_error_not_a_hang(self):
        """`spec.md` R2. The deadline exists to turn a hang into something sayable."""
        store = Store(self.root)
        self._holder(store)
        with mock.patch("cos_baodo.store.LOCK_TIMEOUT", 0.5):
            began = time.monotonic()
            with self.assertRaises(Busy) as e:
                store.add("second")
            waited = time.monotonic() - began
        self.assertGreaterEqual(waited, 0.5, "it gave up before the deadline it was given")
        self.assertLess(waited, 5.0, "it did not give up")
        # spec.md C7: a timeout reads like a broken app unless it says what is happening.
        self.assertIn("holding", str(e.exception))
        self.assertIn(str(self.root), str(e.exception))

    def test_the_lock_dies_with_the_process_holding_it(self):
        """`spec.md` R3 in the form that needs nobody to remember anything.

        A crashed writer must not wedge the working folder. `flock` is released by the
        kernel when the last descriptor closes, which is why it was chosen over a lock
        file whose existence means "held".
        """
        store = Store(self.root)
        child = self._holder(store)
        child.kill()
        child.wait()
        with mock.patch("cos_baodo.store.LOCK_TIMEOUT", 5.0):
            store.add("after")
        self.assertEqual([e.name for e in store.entries()], ["after"])

    def test_the_lock_is_released_even_when_the_body_raises(self):
        """A failure inside the transaction must not be a failure of the next one."""
        store = Store(self.root)
        store.add("a")
        with self.assertRaises(ValueError):
            with store.transaction():
                raise ValueError("boom")
        with mock.patch("cos_baodo.store.LOCK_TIMEOUT", 5.0):
            store.add("b")  # would sit on the deadline if the lock had leaked
        self.assertEqual({e.name for e in store.entries()}, {"a", "b"})

    def test_the_lock_file_is_never_read_as_data(self):
        """`spec.md` R4. Two files, because the store is replaced by rename on every write."""
        store = Store(self.root)
        store.add("a")
        self.assertTrue(store.lock_path.is_file())
        self.assertNotEqual(store.lock_path, store.path)
        store.lock_path.write_text('{"workspaces": [{"name": "ghost"}]}')
        self.assertEqual({e.name for e in store.entries()}, {"a"})

    def test_deleting_the_lock_file_does_not_lose_data(self):
        store = Store(self.root)
        store.add("a")
        store.lock_path.unlink()
        store.add("b")
        self.assertEqual({e.name for e in store.entries()}, {"a", "b"})


if __name__ == "__main__":
    unittest.main()
