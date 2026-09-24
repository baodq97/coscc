"""Tests for the data root, weighted towards the two things that would fail silently.

`spec.md` C2 says SQLite does not inherit the proof produced for the file lock.
The concurrency claim itself is `scripts/verify_0004.py` — four real processes. What this
file covers is everything around it that a unit test can actually decide: the schema
refusal, the directory mode, the one-shot migration mark, and that `write()` really does
serialise a read-modify-write rather than merely appearing to.
"""

from __future__ import annotations

import ast
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from coscc.data import SCHEMA_VERSION, Busy, Data, Incompatible

REPO = Path(__file__).resolve().parent.parent


class TheDirectoryIsMadeForYou(unittest.TestCase):
    def test_it_is_created_on_first_use(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "does" / "not" / "exist"
            data = Data(root)
            self.assertFalse(root.exists())
            data.ensure_dir()
            self.assertTrue(root.is_dir())

    def test_it_is_not_readable_by_anyone_else(self):
        """`spec.md` R2. It records every workspace on the machine."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "cos"
            Data(root).ensure_dir()
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)

    def test_an_existing_loose_directory_is_tightened(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "cos"
            root.mkdir(mode=0o755)
            Data(root).ensure_dir()
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)

    def test_the_database_sits_inside_it(self):
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            self.assertEqual(data.db_path.parent, data.root)


class TheSchemaRefusesToGuess(unittest.TestCase):
    def test_a_fresh_database_carries_this_version(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(Data(d).version(), SCHEMA_VERSION)

    def test_a_newer_database_is_refused_by_name_and_number(self):
        """`spec.md` R5: refuse, and say both numbers. Guessing corrupts quietly."""
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            data.version()
            with sqlite3.connect(data.db_path) as conn:
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION + 5}")
            with self.assertRaises(Incompatible) as caught:
                data.version()
            message = str(caught.exception)
            self.assertIn(str(SCHEMA_VERSION + 5), message)
            self.assertIn(str(SCHEMA_VERSION), message)
            self.assertIn(str(data.db_path), message)

    def test_the_version_lives_in_the_pragma_not_in_a_table(self):
        """One place to look, and reading it needs no lock — see `Data._prepare`."""
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            data.version()
            with data.connect() as conn:
                tables = {
                    row["name"]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
            self.assertNotIn("schema_version", tables)
            self.assertEqual({"migrations", "workspaces", "runs", "prefs"} - tables, set())

    def test_a_database_written_before_version_2_gains_the_new_tables_and_keeps_its_rows(self):
        """`0013` step 2: adding tables needs no bespoke migration, and must lose nothing.

        Built as a real v1 database rather than by dropping tables from a v2 one — the
        thing under test is what `_create` does when it meets a shape it did not write,
        and a v2 database with pieces removed is not that shape.
        """
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            data.ensure_dir()
            with sqlite3.connect(data.db_path) as conn:
                conn.execute(
                    "CREATE TABLE workspaces (root TEXT NOT NULL, name TEXT NOT NULL, "
                    "label TEXT NOT NULL DEFAULT '', added_at TEXT NOT NULL, "
                    "PRIMARY KEY (root, name))"
                )
                conn.execute("CREATE TABLE prefs (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                conn.execute("CREATE TABLE migrations (key TEXT PRIMARY KEY, at TEXT NOT NULL)")
                conn.execute(
                    "INSERT INTO workspaces (root, name, added_at) VALUES ('/w', 'keep-me', 'then')"
                )
                conn.execute("PRAGMA user_version=1")

            self.assertEqual(data.version(), SCHEMA_VERSION)
            with data.connect() as conn:
                tables = {
                    row["name"]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                kept = conn.execute("SELECT name FROM workspaces").fetchall()
            self.assertEqual({"transitions", "outputs"} - tables, set())
            self.assertEqual([row["name"] for row in kept], ["keep-me"])

    def test_a_version_2_database_gains_the_login_tables_and_keeps_its_rows(self):
        """`0070` step 2: 3 adds `auth` and `auth_sessions` the same way 2 added its two."""
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            data.set_pref("density", "compact")
            with data.connect() as conn:
                conn.execute("DROP TABLE auth")
                conn.execute("DROP TABLE auth_sessions")
                conn.execute("PRAGMA user_version=2")

            self.assertEqual(data.version(), 3)
            with data.connect() as conn:
                tables = {
                    row["name"]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
            self.assertEqual({"auth", "auth_sessions"} - tables, set())
            self.assertEqual(data.pref("density"), "compact")

    def test_opening_an_existing_database_writes_nothing(self):
        """The common path is one pragma read. A write on every open is a lock on every open."""
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            data.version()
            before = data.db_path.stat().st_mtime_ns
            for _ in range(5):
                data.version()
            self.assertEqual(data.db_path.stat().st_mtime_ns, before)

    def test_several_processes_creating_the_schema_at_once_all_succeed(self):
        """The race `_create` re-checks under the lock for.

        On a fresh data root every process arrives at an empty database at the same
        moment. Measured as a real failure on 2026-09-22 before `busy_timeout` was moved
        to the first statement.
        """
        with tempfile.TemporaryDirectory() as d:
            child = (
                "import sys; sys.path.insert(0, %r);"
                "from coscc.data import Data;"
                "d = Data(sys.argv[1]);"
                "d.set_pref(sys.argv[2], 1)" % str(REPO)
            )
            procs = [
                subprocess.Popen(
                    [sys.executable, "-c", child, d, f"k{n}"],
                    cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                )
                for n in range(4)
            ]
            for proc in procs:
                _, err = proc.communicate(timeout=60)
                self.assertEqual(proc.returncode, 0, err.decode(errors="replace"))
            self.assertEqual(len(Data(d).prefs()), 4)


class WriteIsAWholeTransaction(unittest.TestCase):
    def test_a_read_modify_write_under_threads_loses_nothing(self):
        """The in-process half of `spec.md` R9.

        Not the proof — that is four processes in `scripts/verify_0004.py`. This catches
        the cheaper mistake: forgetting `BEGIN IMMEDIATE` and letting two upgrades race.
        """
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            data.set_pref("n", 0)

            def bump() -> None:
                for _ in range(25):
                    with data.write() as conn:
                        row = conn.execute("SELECT value FROM prefs WHERE key = 'n'").fetchone()
                        current = int(row["value"])
                        conn.execute(
                            "UPDATE prefs SET value = ? WHERE key = 'n'", (str(current + 1),)
                        )

            threads = [threading.Thread(target=bump) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(data.pref("n"), 100)

    def test_a_failure_inside_the_transaction_leaves_nothing_behind(self):
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            with self.assertRaises(RuntimeError):
                with data.write() as conn:
                    conn.execute(
                        "INSERT INTO prefs (key, value) VALUES ('half', '\"written\"')"
                    )
                    raise RuntimeError("something went wrong half way")
            self.assertIsNone(data.pref("half"))

    def test_a_held_database_times_out_by_name_rather_than_hanging(self):
        """`spec.md` R10. The error has to name the file; a bare 'database is locked' does not."""
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            data.version()
            holder = sqlite3.connect(data.db_path, isolation_level=None)
            try:
                holder.execute("PRAGMA journal_mode=WAL")
                holder.execute("BEGIN IMMEDIATE")
                holder.execute("INSERT INTO prefs (key, value) VALUES ('held', '1')")
                with self.assertRaises(Busy) as caught:
                    with data.write(timeout=0.2):
                        pass
                self.assertIn(str(data.db_path), str(caught.exception))
            finally:
                holder.execute("ROLLBACK")
                holder.close()


class MigrationsRunOnce(unittest.TestCase):
    def test_a_mark_is_visible_afterwards(self):
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            self.assertFalse(data.has_run("import:/some/root"))
            with data.write() as conn:
                Data.mark_run(conn, "import:/some/root")
            self.assertTrue(data.has_run("import:/some/root"))

    def test_marking_twice_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            with data.write() as conn:
                Data.mark_run(conn, "k")
                Data.mark_run(conn, "k")
            self.assertTrue(data.has_run("k"))

    def test_a_rolled_back_migration_is_not_marked(self):
        """Why `mark_run` takes the caller's connection rather than opening its own."""
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            with self.assertRaises(RuntimeError):
                with data.write() as conn:
                    Data.mark_run(conn, "k")
                    raise RuntimeError("import failed")
            self.assertFalse(data.has_run("k"))


class Preferences(unittest.TestCase):
    def test_a_value_survives_a_new_handle(self):
        with tempfile.TemporaryDirectory() as d:
            Data(d).set_pref("density", "compact")
            self.assertEqual(Data(d).pref("density"), "compact")

    def test_an_unset_key_gives_the_default(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(Data(d).pref("nothing", "fallback"), "fallback")

    def test_setting_twice_replaces_rather_than_duplicates(self):
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            data.set_pref("k", "one")
            data.set_pref("k", "two")
            self.assertEqual(data.pref("k"), "two")
            self.assertEqual(data.prefs(), {"k": "two"})

    def test_a_hand_broken_value_falls_back_rather_than_raising(self):
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            data.set_pref("k", "fine")
            with data.write() as conn:
                conn.execute("UPDATE prefs SET value = 'not json' WHERE key = 'k'")
            self.assertEqual(data.pref("k", "fallback"), "fallback")
            self.assertEqual(data.prefs(), {})

    def test_non_string_values_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            data.set_pref("shown", True)
            data.set_pref("count", 3)
            self.assertIs(data.pref("shown"), True)
            self.assertEqual(data.pref("count"), 3)

    def test_a_preference_can_be_removed(self):
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            data.set_pref("model:impl", "x")
            self.assertTrue(data.delete_pref("model:impl"))
            self.assertFalse(data.delete_pref("model:impl"))
            self.assertIsNone(data.pref("model:impl"))

    def test_pref_rows_keeps_a_broken_value_so_it_can_be_reported(self):
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            data.set_pref("model:impl", "x")
            data.set_pref("model:plan", "y")
            data.set_pref("density", "compact")
            with data.write() as conn:
                conn.execute("UPDATE prefs SET value = '{' WHERE key = 'model:plan'")
            self.assertEqual(data.pref_rows("model:"), {"model:impl": '"x"', "model:plan": "{"})

    def test_pref_rows_prefix_is_literal(self):
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            data.set_pref("model_x", "a")
            data.set_pref("model:x", "b")
            self.assertEqual(list(data.pref_rows("model:")), ["model:x"])


class TheLoginStore(unittest.TestCase):
    """`0070` step 2. What the guard in `coscc/auth.py` stands on."""

    def test_prefs_never_see_the_password_hash(self):
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            data.set_pref("density", "compact")
            self.assertTrue(data.auth_set_password("$argon2id$not-a-real-hash", 1))
            data.auth_session_add("a" * 64, 1, 100)
            everything = repr(data.prefs()) + repr(data.pref_rows(""))
            self.assertNotIn("argon2id", everything)
            self.assertNotIn("a" * 64, everything)
            self.assertEqual(data.prefs(), {"density": "compact"})

    def test_a_second_password_is_refused_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            self.assertIsNone(data.auth_password_hash())
            self.assertTrue(data.auth_set_password("first", 1))
            self.assertFalse(data.auth_set_password("second", 2))
            self.assertEqual(data.auth_password_hash(), "first")

    def test_clearing_removes_the_password_and_every_session(self):
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            data.auth_set_password("h", 1)
            data.auth_session_add("s1", 1, 100)
            data.auth_session_add("s2", 1, 100)
            data.auth_clear()
            self.assertEqual(data.auth_state("s1"), (False, None))
            self.assertEqual(data.auth_state("s2"), (False, None))
            self.assertIsNone(data.auth_password_hash())

    def test_a_new_session_sweeps_the_expired_ones(self):
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            data.auth_session_add("old", 1, 50)
            data.auth_session_add("new", 60, 200)
            self.assertIsNone(data.auth_state("old")[1])
            self.assertEqual(data.auth_state("new")[1]["expires_at"], 200)

    def test_touch_and_delete(self):
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            data.auth_session_add("s", 1, 100)
            data.auth_session_touch("s", 50, 150)
            row = data.auth_state("s")[1]
            self.assertEqual((row["last_used_at"], row["expires_at"]), (50, 150))
            data.auth_session_delete("s")
            self.assertIsNone(data.auth_state("s")[1])


class NothingReachesTheRealHomeDirectory(unittest.TestCase):
    def test_constructing_a_default_data_root_touches_no_disk(self):
        """`Data()` defaults to `~/.cos`, and a test run must never create it."""
        before = Path("~/.cos").expanduser().exists()
        Data()
        self.assertEqual(Path("~/.cos").expanduser().exists(), before)

    def test_no_test_builds_a_journal_store_or_history_without_a_data_root(self):
        """The guard for the mistake above, at the only place it can be made.

        `Journal`, `Store` and `History` all take the data root as a second argument and
        all default it to `Data(None)`, which is `~/.cos`. That default is right in
        production and wrong in every test, and the docstring at
        `coscc/journal.py:108-109` has said so since the class was written.

        It happened anyway. `coscc/runner_test.py:321` read `Journal(Path(d) / "cos.db")`
        — one argument, where every other call in that file passes two — and for as long
        as the schema only ever grew, nothing noticed: the suite opened the developer's
        real database and quietly did nothing to it. `0013` took `SCHEMA_VERSION` to 2,
        and the same line then **upgraded** that database on every `npm test`, after which
        the installed `v0.2.3` answered 500 on every route that reads it while
        `/api/health` still said `ok`. Measured 2026-09-22 on the machine this was written
        on.

        **Parsed, not matched.** The first version of this check was a regular expression
        and it did not catch the line it was written for: the argument was
        `Path(d) / "cos.db"`, which carries its own brackets, and the expression excluded
        them. Reverting the fix and watching the check stay green is how that was found.
        `ast` sees the call rather than the characters, so an argument of any shape counts
        as one argument.

        What it still cannot see: a call built through a helper, or one handed a variable
        that happens to be `None`. Narrow on purpose — it catches the exact shape that has
        already cost something once.
        """
        watched = {"Journal", "Store", "History"}
        offenders = []
        for path in sorted(Path(__file__).resolve().parent.glob("*_test.py")):
            source = path.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(source, filename=str(path))):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name not in watched:
                    continue
                gives_root = len(node.args) >= 2 or any(
                    keyword.arg == "data" for keyword in node.keywords
                )
                if node.args and not gives_root:
                    offenders.append(
                        f"{path.name}:{node.lineno}: {name}(...) with no data root"
                    )
        self.assertEqual(
            offenders,
            [],
            "these build a data-root-taking class with one argument, so they write into "
            "the real ~/.cos:\n  " + "\n  ".join(offenders),
        )

if __name__ == "__main__":
    unittest.main()
