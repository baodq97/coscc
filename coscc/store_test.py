"""Tests for the workspace list, weighted towards what must NOT happen.

`spec.md` R12 and R21 are the reason this file is longer than the module deserves:
the store is the first thing in the app a user edits by hand, and the first thing whose
contents decide which directories the app will work in.

It moved from a JSON file under the working folder into the app's SQLite database.
Every test here that was about the *shape* of the file is now about the shape of the table,
and the ones about `flock` are about `BEGIN IMMEDIATE`. The claims did not change; the
thing they are claimed of did.

Every `Store` below is constructed with an explicit data root. A missing second argument
would send the test at the real `~/.cos`, which is exactly the accident worth making loud.
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

from coscc.store import BadName, Busy, Store, clean_label, require_name, valid_name


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
                Store(d, d).path_of("../escape")


class PathsAreBuiltNotStored(unittest.TestCase):
    def test_the_table_has_no_column_for_a_workspace_path(self):
        """The safety property, stated as a test.

        `root` is an absolute path and is meant to be: it is the working folder, and it is
        what lets one database serve several of them. What must not exist is a column
        holding a path to a *workspace* — if one did, the shape argument in `store.py`
        would be gone and only `is_under` would be left holding the boundary.
        """
        with tempfile.TemporaryDirectory() as d:
            store = Store(d, d)
            store.add("repo", label="My repo")
            with store.data.connect() as conn:
                columns = {
                    row["name"] for row in conn.execute("PRAGMA table_info(workspaces)")
                }
                row = conn.execute("SELECT * FROM workspaces").fetchone()
            self.assertEqual(columns, {"root", "name", "label", "added_at"})
            self.assertEqual(row["root"], str(Path(d).resolve()))
            self.assertEqual(row["name"], "repo")
            self.assertNotIn("/", row["name"])
            # The workspace's own path appears nowhere in the row.
            stored = " ".join(str(row[c]) for c in columns)
            self.assertNotIn(str(Path(d).resolve() / "repo"), stored)

    def test_path_of_is_the_root_plus_the_name(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(Store(d, d).path_of("repo"), Path(d).resolve() / "repo")


class AHandEditedStoreCannotWidenTheBoundary(unittest.TestCase):
    @staticmethod
    def _insert(store: Store, *names: str) -> None:
        """Write rows the way somebody with `sqlite3` on the command line would."""
        with store.data.write() as conn:
            for name in names:
                conn.execute(
                    "INSERT OR REPLACE INTO workspaces (root, name, label, added_at) "
                    "VALUES (?, ?, '', '2026-01-01T00:00:00+00:00')",
                    (str(store.working_dir), name),
                )

    def test_an_entry_naming_an_outside_path_is_dropped(self):
        """`spec.md` R21, at the store layer.

        Writing `/etc` or `../x` into the table by hand must not make it a workspace. The
        name is rejected on read, so the entry simply does not exist.
        """
        with tempfile.TemporaryDirectory() as d:
            store = Store(d, d)
            self._insert(store, "/etc", "../../etc", "ok")
            self.assertEqual([e.name for e in store.entries()], ["ok"])
            self.assertFalse(store.resolves_to_entry("/etc"))

    def test_a_subdirectory_nobody_added_is_not_a_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(d, d)
            (Path(d) / "stray").mkdir()
            self.assertTrue(store.is_under(str(Path(d) / "stray")))
            self.assertFalse(store.resolves_to_entry(str(Path(d) / "stray")))

    def test_the_working_folder_itself_is_not_a_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(Store(d, d).is_under(d))

    def test_another_working_folders_entries_are_not_visible_here(self):
        """One database, several roots. A row is scoped or the boundary means nothing."""
        with tempfile.TemporaryDirectory() as data_dir, \
                tempfile.TemporaryDirectory() as one, \
                tempfile.TemporaryDirectory() as two:
            Store(one, data_dir).add("mine")
            self.assertEqual([e.name for e in Store(two, data_dir).entries()], [])
            self.assertEqual([e.name for e in Store(one, data_dir).entries()], ["mine"])


class ListOperations(unittest.TestCase):
    def test_add_label_remove_round_trip_survives_a_new_store_object(self):
        # A new Store re-reads, which is what "survives a restart" reduces to.
        with tempfile.TemporaryDirectory() as d:
            Store(d, d).add("a", "A")
            Store(d, d).add("b")
            Store(d, d).set_label("b", "B")
            self.assertEqual(
                {e.name: e.label for e in Store(d, d).entries()}, {"a": "A", "b": "B"}
            )
            Store(d, d).remove("a")
            self.assertEqual([e.name for e in Store(d, d).entries()], ["b"])

    def test_remove_does_not_touch_the_directory(self):
        # `spec.md` R18 and C6: the app has no undo and a clone may hold uncommitted work.
        with tempfile.TemporaryDirectory() as d:
            store = Store(d, d)
            target = store.path_of("repo")
            target.mkdir()
            store.add("repo")
            store.remove("repo")
            self.assertTrue(target.is_dir())

    def test_removing_something_absent_is_an_error_not_a_silent_success(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(KeyError):
                Store(d, d).remove("ghost")

    def test_relabelling_something_absent_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(KeyError):
                Store(d, d).set_label("ghost", "x")

    def test_adding_twice_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(d, d)
            store.add("repo", "first")
            store.add("repo", "second")
            self.assertEqual([(e.name, e.label) for e in store.entries()], [("repo", "second")])

    def test_readding_moves_an_entry_to_the_end(self):
        """The JSON list did this. Keeping it means a board's order does not shuffle."""
        with tempfile.TemporaryDirectory() as d:
            store = Store(d, d)
            store.add("a")
            store.add("b")
            store.add("a")
            self.assertEqual([e.name for e in store.entries()], ["b", "a"])

    def test_labels_are_bounded_and_trimmed(self):
        self.assertEqual(clean_label("  hi  "), "hi")
        self.assertEqual(len(clean_label("x" * 500)), 200)
        self.assertEqual(clean_label(None), "")

    def test_a_long_label_is_cut_on_the_way_in(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(d, d)
            store.add("repo", "x" * 500)
            self.assertEqual(len(store.entries()[0].label), 200)



HOLDER = """
import sqlite3, sys, time
conn = sqlite3.connect(sys.argv[1], isolation_level=None)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("BEGIN IMMEDIATE")
conn.execute("INSERT INTO prefs (key, value) VALUES ('holder', '1')")
sys.stdout.write("held\\n")
sys.stdout.flush()
time.sleep(60)
"""


class TheTransactionIsAcrossProcesses(unittest.TestCase):
    """The old arrangement was measured leaving 8 of 20 entries behind
    (`.cos/0004_silent-concurrent-loss/plan.md:115`).

    These use a real child process, not a second connection in this one. The loss being
    fixed was between processes, and a same-process stand-in would pass even if the
    exclusion were a threading lock again.

    The full claim is `scripts/verify_0004.py` with four writers and twenty entries. What
    is here is the behaviour around it: a bounded wait, an error that says what happened,
    and a crash that does not wedge the database.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _holder(self, store: Store) -> subprocess.Popen:
        """A child holding a write transaction. Returns once it says it has it."""
        store.data.ensure_dir()
        store.data.version()  # create the schema before the child opens it
        child = subprocess.Popen(
            [sys.executable, "-c", HOLDER, str(store.data.db_path)],
            stdout=subprocess.PIPE,
            text=True,
        )
        # addCleanup runs last-registered-first, so this order kills, waits, then closes the
        # pipe. Closing it before the child is reaped would leave the read end open in the
        # only window that matters and hand the child an EPIPE on the way out.
        self.addCleanup(child.stdout.close)
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        self.assertEqual(child.stdout.readline().strip(), "held", "child never took the lock")
        return child

    def test_waiting_for_a_held_database_ends_in_an_error_not_a_hang(self):
        """`spec.md` R10. The deadline turns a hang into something sayable."""
        store = Store(self.root, self.root)
        self._holder(store)
        with mock.patch("coscc.store.LOCK_TIMEOUT", 0.5):
            began = time.monotonic()
            with self.assertRaises(Busy) as caught:
                store.add("second")
            waited = time.monotonic() - began
        self.assertGreaterEqual(waited, 0.4, "it gave up before the deadline it was given")
        self.assertLess(waited, 5.0, "it did not give up")
        # `spec.md` C7: a timeout reads like a broken app unless it says what is
        # happening, and which file it is happening to.
        self.assertIn("holding", str(caught.exception))
        self.assertIn(str(store.data.db_path), str(caught.exception))

    def test_the_transaction_dies_with_the_process_holding_it(self):
        """The property that needs nobody to remember anything.

        A crashed writer must not wedge the database. SQLite's locks are released by the
        kernel when the descriptor closes, which is the same reason `flock` was chosen
        before it.
        """
        store = Store(self.root, self.root)
        child = self._holder(store)
        child.kill()
        child.wait()
        with mock.patch("coscc.store.LOCK_TIMEOUT", 5.0):
            store.add("after")
        self.assertEqual([e.name for e in store.entries()], ["after"])

    def test_a_killed_writer_leaves_the_previous_state_whole(self):
        """No half-written list. The transaction is the unit, not the statement."""
        store = Store(self.root, self.root)
        store.add("before")
        child = self._holder(store)
        child.kill()
        child.wait()
        self.assertEqual([e.name for e in store.entries()], ["before"])

    def test_the_transaction_is_released_even_when_the_body_raises(self):
        """A failure inside the transaction must not be a failure of the next one."""
        store = Store(self.root, self.root)
        store.add("a")
        with self.assertRaises(ValueError):
            with store.transaction():
                raise ValueError("boom")
        with mock.patch("coscc.store.LOCK_TIMEOUT", 5.0):
            store.add("b")  # would sit on the deadline if the transaction had leaked
        self.assertEqual({e.name for e in store.entries()}, {"a", "b"})

    def test_a_raise_inside_the_transaction_rolls_its_writes_back(self):
        store = Store(self.root, self.root)
        store.add("kept")
        with self.assertRaises(ValueError):
            with store.transaction() as conn:
                conn.execute(
                    "INSERT INTO workspaces (root, name, label, added_at) "
                    "VALUES (?, 'ghost', '', 'x')",
                    (str(store.working_dir),),
                )
                raise ValueError("boom")
        self.assertEqual([e.name for e in store.entries()], ["kept"])


class NothingIsWrittenIntoTheWorkingFolder(unittest.TestCase):
    """Three tests used to live here, one per artifact a pre-`0006` version left in the
    working folder: a JSON list, a lock file, a temp file. Each named its file as a
    literal, and those names carried the author's own — which is what
    `.cos/0008_personal-name-blocks-publishing` exists to remove. Renaming the literals
    would have been worse than deleting them: it would claim files once existed under a
    name they never had.

    So they are replaced by the invariant they were three samples of. It is the stronger
    claim anyway: the working folder is somebody else's git checkout, and the store writes
    into `COS_DATA_DIR` or nowhere. The old assertions could only catch the three names
    somebody thought to list; this catches a fourth.
    """

    def test_a_write_leaves_the_working_folder_untouched(self):
        with tempfile.TemporaryDirectory() as work, tempfile.TemporaryDirectory() as data:
            store = Store(work, data)
            store.add("repo", "a label")
            store.remove("repo")
            self.assertEqual(sorted(p.name for p in Path(work).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
