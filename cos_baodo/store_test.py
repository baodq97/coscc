"""Tests for the workspace list, weighted towards what must NOT happen.

`spec.md` R12 and R21 are the reason this file is longer than the module deserves: the
store is the first thing in the app a user edits by hand, and the first thing whose
contents decide which directories the app will work in.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cos_baodo.store import BadName, Store, clean_label, require_name, valid_name


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


if __name__ == "__main__":
    unittest.main()
