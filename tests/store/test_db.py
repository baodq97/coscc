"""Tests for the data root, weighted towards the two things that would fail silently.

The concurrency claim itself is `scripts/verify_0004.py` — four real processes. What this file
covers is everything around it that a unit test can actually decide: the schema refusal, the
directory mode, the one-shot migration mark, and that `write()` really does serialise a
read-modify-write rather than merely appearing to."""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from coscc import features
from coscc.config import PROTECTED_DB_VAR
from coscc.http import plugin
from coscc.store import db
from coscc.store.db import SCHEMA_VERSION, Busy, Data, Incompatible, Protected

REPO = Path(__file__).resolve().parent.parent


class TheDirectoryIsMadeForYou(unittest.TestCase):
    def test_it_is_not_readable_by_anyone_else(self):
        """It records every workspace on the machine."""
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


class TheSchemaRefusesToGuess(unittest.TestCase):
    def test_a_newer_database_is_refused_by_name_and_number(self):
        """Refuse, and say both numbers. Guessing corrupts quietly."""
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

    def test_a_database_below_12_or_between_is_refused_by_name(self):
        """Only 12, the last release's, takes the one step; the steps before and after it are gone."""
        for found in (11, 18):
            with tempfile.TemporaryDirectory() as d, self.subTest(found=found):
                data = Data(d)
                data.ensure_dir()
                with sqlite3.connect(data.db_path) as conn:
                    conn.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY)")
                    conn.execute(f"PRAGMA user_version={found}")
                conn.close()
                with self.assertRaises(Incompatible) as caught:
                    data.version()
                self.assertIn(f"schema {found}", str(caught.exception))
                self.assertIn("upgrade through 0.15 first", str(caught.exception))

    def test_a_newer_database_is_still_refused(self):
        """Was `version_5`, then `version_6`: it follows `SCHEMA_VERSION`, so a newer number is
        never this build's own."""
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            data.version()
            with sqlite3.connect(data.db_path) as conn:
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
            with self.assertRaises(Incompatible):
                data.version()


class AStepsEvents(unittest.TestCase):
    """The two tables, as the recorder writes them and the service reads."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data = Data(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def ev(self, run, seq, at=1000, kind="text", text="x"):
        return {"run": run, "seq": seq, "at": at, "kind": kind, "text": text}

    def test_events_are_counted_once_even_when_written_twice(self):
        self.data.step_run_open("r", "/w", "/w/ws", "0001_a", "impl", 1000)
        rows = [self.ev("r", n) for n in range(1, 4)]
        self.assertEqual(self.data.step_events_add("r", rows), 3)
        self.assertEqual(self.data.step_events_add("r", rows + [self.ev("r", 4)]), 1)
        row = self.data.step_run("r")
        self.assertEqual(row["events"], 4)
        self.assertGreater(row["bytes"], 0)
        self.assertIsNone(row["ended_at"])
        self.data.step_run_close("r", 5000, 2)
        row = self.data.step_run("r")
        self.assertEqual((row["ended_at"], row["lost"]), (5000, 2))

    def test_a_page_is_the_last_ones_below_before_oldest_first(self):
        self.data.step_run_open("r", "/w", "/w/ws", "0001_a", "impl", 1000)
        self.data.step_events_add("r", [self.ev("r", n) for n in range(1, 11)])
        events, older = self.data.step_events_page("r", None, 3)
        self.assertEqual([e["seq"] for e in events], [8, 9, 10])
        self.assertTrue(older)
        events, older = self.data.step_events_page("r", 3, 5)
        self.assertEqual([e["seq"] for e in events], [1, 2])
        self.assertFalse(older)
        self.assertEqual(self.data.step_event("r", 7)["seq"], 7)
        self.assertIsNone(self.data.step_event("r", 70))

    def test_purge_takes_whole_runs_by_age_then_by_size_oldest_first(self):
        day = 24 * 3600 * 1000
        now = 100 * day
        for run, started in (("old", now - 31 * day), ("mid", now - 2 * day), ("new", now - day)):
            self.data.step_run_open(run, "/w", "/w/ws", "0001_a", "impl", started)
            self.data.step_events_add(run, [self.ev(run, n, text="y" * 100) for n in range(1, 6)])
        size = self.data.step_run("new")["bytes"]
        runs, freed = self.data.step_events_purge(now - 30 * day, size, "2026-10-01T00:00:00+00:00")
        self.assertEqual(runs, 2)
        self.assertEqual(self.data.step_run("old")["purged_at"], "2026-10-01T00:00:00+00:00")
        self.assertIsNotNone(self.data.step_run("mid")["purged_at"])
        self.assertIsNone(self.data.step_run("new")["purged_at"])
        self.assertEqual(self.data.step_events_page("old", None, 10), ([], False))
        self.assertEqual(len(self.data.step_events_page("new", None, 10)[0]), 5)
        self.assertEqual(freed, 2 * size)
        self.assertEqual(self.data.step_events_purge(now - 30 * day, size, "later"), (0, 0))

    def test_turns_are_the_stored_turn_events_of_the_run(self):
        """Three `turn`s and two `text`s count three; a run with nothing counts 0."""
        self.data.step_run_open("r", "/w", "/w/ws", "0001_a", "impl", 1000)
        kinds = ["turn", "text", "turn", "text", "turn"]
        self.data.step_events_add("r", [self.ev("r", n, kind=k) for n, k in enumerate(kinds, 1)])
        self.assertEqual(self.data.step_turns("r"), 3)
        self.assertEqual(self.data.step_turns("nothing"), 0)

    def test_open_runs_leave_out_the_closed_and_the_purged(self):
        """Only a row nobody closed or purged, with its last event's `at`."""
        for run, started in (("open", 1000), ("empty", 2000), ("closed", 3000), ("purged", 500)):
            self.data.step_run_open(run, "/w", "/w/ws", "0001_a", "impl", started)
        self.data.step_events_add(
            "open", [self.ev("open", 1, at=1500), self.ev("open", 2, at=1700)]
        )
        self.data.step_run_close("closed", 3500, 0)
        self.data.step_events_add("purged", [self.ev("purged", 1, at=600)])
        self.data.step_events_purge(900, 10**9, "2026-10-01T00:00:00+00:00")
        rows = self.data.step_runs_open()
        self.assertEqual(
            [(r["run"], r["last_at"]) for r in rows], [("open", 1700), ("empty", None)]
        )
        self.assertEqual(rows[0]["root"], "/w")

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
                "from coscc.store.db import Data;"
                "d = Data(sys.argv[1]);"
                "d.set_pref(sys.argv[2], 1)" % str(REPO)
            )
            procs = [
                subprocess.Popen(
                    [sys.executable, "-c", child, d, f"k{n}"],
                    cwd=REPO,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                for n in range(4)
            ]
            for proc in procs:
                _, err = proc.communicate(timeout=60)
                self.assertEqual(proc.returncode, 0, err.decode(errors="replace"))
            self.assertEqual(len(Data(d).prefs()), 4)


class WriteIsAWholeTransaction(unittest.TestCase):
    def test_a_read_modify_write_under_threads_loses_nothing(self):
        """Not the proof — that is four processes in `scripts/verify_0004.py`. This catches
        the cheaper mistake: forgetting `BEGIN IMMEDIATE` and letting two upgrades race."""
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
                    conn.execute("INSERT INTO prefs (key, value) VALUES ('half', '\"written\"')")
                    raise RuntimeError("something went wrong half way")
            self.assertIsNone(data.pref("half"))

    def test_a_held_database_times_out_by_name_rather_than_hanging(self):
        """The error has to name the file; a bare 'database is locked' does not."""
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

    def test_a_hand_broken_value_falls_back_rather_than_raising(self):
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            data.set_pref("k", "fine")
            with data.write() as conn:
                conn.execute("UPDATE prefs SET value = 'not json' WHERE key = 'k'")
            self.assertEqual(data.pref("k", "fallback"), "fallback")
            self.assertEqual(data.prefs(), {})

    def test_pref_rows_keeps_a_broken_value_so_it_can_be_reported(self):
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            data.set_pref("model:impl", "x")
            data.set_pref("model:plan", "y")
            data.set_pref("density", "compact")
            with data.write() as conn:
                conn.execute("UPDATE prefs SET value = '{' WHERE key = 'model:plan'")
            self.assertEqual(data.pref_rows("model:"), {"model:impl": '"x"', "model:plan": "{"})


class TheLoginStore(unittest.TestCase):
    """What the guard in `coscc/http/auth.py` stands on."""

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
        `coscc/store/journal.py:108-109` has said so since the class was written.

        It happened anyway. `tests/runner/test_step.py:323` read `Journal(Path(d) / "cos.db")` —
        one argument, where every other call in that file passes two — and for as long as the schema
        only ever grew, nothing noticed: the suite opened the developer's real database and quietly
        did nothing to it. Measured 2026-09-22 on the machine this was written on.

        **Parsed, not matched.** The first version of this check was a regular expression
        and it did not catch the line it was written for: the argument was
        `Path(d) / "cos.db"`, which carries its own brackets, and the expression excluded
        them. Reverting the fix and watching the check stay green is how that was found.
        `ast` sees the call rather than the characters, so an argument of any shape counts
        as one argument.

        What it still cannot see: a call built through a helper, or one handed a variable
        that happens to be `None`. Narrow on purpose — it catches the exact shape that has
        already cost something once."""
        watched = {"Journal", "Store", "History"}
        offenders = []
        for path in sorted(Path(__file__).resolve().parent.rglob("test_*.py")):
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
                    offenders.append(f"{path.name}:{node.lineno}: {name}(...) with no data root")
        self.assertEqual(
            offenders,
            [],
            "these build a data-root-taking class with one argument, so they write into "
            "the real ~/.cos:\n  " + "\n  ".join(offenders),
        )


class TheRunningAppsDatabaseIsNotOpened(unittest.TestCase):
    """Each test sets or removes the variable itself, through `mock.patch.dict`, so a suite run
    inside a step does not decide the answer."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _protecting(self, *paths: Path):
        return mock.patch.dict(
            os.environ, {PROTECTED_DB_VAR: os.pathsep.join(str(p) for p in paths)}
        )

    def test_a_listed_database_is_refused_before_anything_touches_it(self):
        root = self.tmp / "app"
        data = Data(root)

        def write():
            with data.write():
                pass

        with (
            self._protecting(root / "cos.db"),
            mock.patch("coscc.store.db.sqlite3.connect") as opened,
        ):
            for use in (data.version, write):
                with self.assertRaises(Protected) as caught:
                    use()
                self.assertIn(str(root / "cos.db"), str(caught.exception))
                self.assertIn(PROTECTED_DB_VAR, str(caught.exception))
        opened.assert_not_called()
        self.assertFalse(root.exists(), "the directory was made for a refused database")

    def test_a_symlink_in_the_list_still_names_the_database(self):
        real = self.tmp / "real"
        real.mkdir()
        (self.tmp / "link").symlink_to(real)
        with self._protecting(self.tmp / "link" / "cos.db"):
            with self.assertRaises(Protected):
                Data(real).version()

    def test_another_database_opens(self):
        with self._protecting(self.tmp / "app" / "cos.db"):
            self.assertEqual(Data(self.tmp / "mine").version(), SCHEMA_VERSION)

    def test_with_nothing_listed_it_behaves_as_before(self):
        with mock.patch.dict(os.environ):
            os.environ.pop(PROTECTED_DB_VAR, None)
            self.assertEqual(Data(self.tmp / "app").version(), SCHEMA_VERSION)


class DecisionsGetATable(unittest.TestCase):
    def test_the_table_and_its_index_exist(self):
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            self.assertEqual(data.version(), SCHEMA_VERSION)
            with data.connect() as conn:
                names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master")}
            self.assertLessEqual({"unit_decisions", "unit_decisions_scope"}, names)

    def test_an_unknown_kind_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            insert = (
                "INSERT INTO unit_decisions (root, workspace, unit, kind, fields, decided_by, "
                "date, via) VALUES ('/w', 'p', '0001_a', ?, '{}', 'o', 'd', 'v')"
            )
            with data.write() as conn:
                for kind in ("rerun", "more-rounds", "outcome"):
                    conn.execute(insert, (kind,))
            with self.assertRaises(sqlite3.IntegrityError), data.write() as conn:
                conn.execute(insert, ("approve",))


# A database at 12, the real one's schema; `_from_12` is the one step from it.
V12 = Path(__file__).resolve().parent / "fixtures" / "v12.sql"

TWO_QUESTIONS = (
    '{"judgement": "ready", "questions": [{"n": 1, "text": "a"}, {"n": 2, "text": "b"}]}'
)


def _shape(conn: sqlite3.Connection) -> dict[str, tuple]:
    """Every table's columns, indexes, CHECKs and foreign keys: what a fresh and a migrated
    database must agree on (their SQL text differs by comments and `ALTER`s)."""
    out = {}
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()[0]
        indexes = sorted(
            (r[1], r[2], r[4], tuple(i[2] for i in conn.execute(f"PRAGMA index_info('{r[1]}')")))
            for r in conn.execute(f"PRAGMA index_list('{table}')")
        )
        out[table] = (
            [tuple(r)[1:] for r in conn.execute(f"PRAGMA table_info('{table}')")],
            indexes,
            sorted(" ".join(c.split()) for c in re.findall(r"CHECK \((.*?\)?)\)", sql or "")),
            [tuple(r) for r in conn.execute(f"PRAGMA foreign_key_list('{table}')")],
        )
    return out


class TheOneStepFindsTodaysPack(unittest.TestCase):
    def test_what_it_derives_from_the_built_in_pack(self):
        """`_from_12` finds agents and states by what they declare; a pack that drifts fails here."""
        states, rows = db._builtin()
        self.assertEqual(db._with(rows, "type"), ["intent"])
        self.assertEqual(db._with(rows, "rests_on"), ["plan"])
        self.assertEqual(db._with(rows, "unmeasured"), ["spec"])
        self.assertEqual(
            sorted(db._with(rows, "questions")),
            # Dagaz asks before it drafts; its drafts live on run ends, never in `outputs`.
            ["dagaz", "idea", "impl", "intent", "plan", "spec", "spike"],
        )
        self.assertEqual(db._coders(rows), ["impl"])
        self.assertEqual(db._scan_row(rows), "scan")
        self.assertEqual([s for s, v in states.items() if v.get("action") == "merge"], ["ship"])


class AV12DatabaseTakesOneStep(unittest.TestCase):
    """0.15's database to this schema in one step; the end state is what each part proves."""

    ANSWERS = (
        ("person", "Leif (CoS), x"),
        ("person", "Claude (CoS), y"),
        ("person", "agent, z"),
        ("person", "owner"),
        ("person", "the originator (relayed by Leif)"),
        ("agent", "Jera"),
    )
    OUTPUTS = (
        ("0001_a", "intent", TWO_QUESTIONS),
        ("0002_b", "intent", '{"stage": "intent", "judgement": "ready"}'),
        ("0001_a", "impl", '{"stage": "impl", "needs_person": []}'),
        ("0001_a", "spec", '{"unmeasured": ["U9"]}'),
        ("0001_a", "spec", TWO_QUESTIONS.replace("{", '{"unmeasured": ["U1"], ', 1)),
        ("0001_a", "plan", '{"judgement": "ready"}'),
        ("0002_b", "plan", '{"judgement": "ready"}'),
        ("0001_a", "spike", '{"judgement": "ready"}'),
    )
    RUNS = (
        ("end", "scan", '{"kind": "end", "stage": "scan", "outcome": "done"}'),
        ("end", "estimate", '{"kind": "end", "stage": "estimate", "outcome": "done"}'),
        ("end", "impl", '{"kind": "end", "outcome": "exhausted", "status": "paused-budget"}'),
        ("attempt", "", '{"kind": "attempt", "outcome": "exhausted"}'),
        ("ship", "ship", '{"kind": "ship", "unit": "0001_a", "result": "shipped"}'),
        (
            "vault",
            "",
            '{"kind": "vault", "action": "policy", "stages": ["impl"], "modes": ["env"]}',
        ),
        ("vault", "", '{"kind": "vault", "action": "create"}'),
    )
    PREFS = {
        "features.state": {"scan": {"/w": "on", "/v": "on"}, "release": {"/w": "on"}},
        "features.schedule": {"scan": {"/w": 24, "/v": 0}},
        "model:impl": "claude-sonnet-5",
        "budget:review:novel": 9,
        "density": "compact",
    }

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Data(Path(tmp.name) / "data")
        self.data.ensure_dir()
        with sqlite3.connect(self.data.db_path) as conn:
            conn.executescript(V12.read_text(encoding="utf-8"))
            self._rows(conn)
            conn.execute("PRAGMA user_version=12")
        conn.close()
        self.assertEqual(self.data.version(), SCHEMA_VERSION)

    def _rows(self, conn):
        for unit, kind in (("0001_a", "fix"), ("0002_b", "feat")):
            conn.execute(
                "INSERT INTO unit_meta (root, workspace, unit, type, imported_at) "
                "VALUES ('/w', 'p', ?, ?, 't')",
                (unit, kind),
            )
        for unit, agent, obj in self.OUTPUTS:
            conn.execute(
                "INSERT INTO stage_results (at, root, workspace, unit, stage, run, revision, "
                "judgement, object) VALUES ('t', '/w', 'p', ?, ?, 'r', 'h', 'ready', ?)",
                (unit, agent, obj),
            )
        conn.execute("INSERT INTO idea_meta VALUES ('/w', 'p', '0001_x', '{}')")
        for pos, (kind, ref) in enumerate(
            [("idea", "p/ideas/0001_x.md"), ("repo", "p"), ("depends", "p/0002_b")]
        ):
            conn.execute(
                "INSERT INTO unit_links VALUES ('/w', 'p', '0003_c', ?, ?, ?)", (kind, ref, pos)
            )
        conn.execute("INSERT INTO unit_seen VALUES ('/w', 'p', '0001_a', 'plan.md', 'x', 1)")
        for field, raw in (("status", "approved"), ("ingest", None)):
            conn.execute(
                "INSERT INTO unit_unknowns VALUES ('/w', 'p', '0001_a', 'plan.md', ?, 'r', ?, 't')",
                (field, raw),
            )
        for key in ("unit-meta:0135:/w/p", "unit-meta:0136-authority:/w/p", "keep"):
            conn.execute("INSERT INTO migrations (key, at) VALUES (?, 't')", (key,))
        for frm, to in (("not started", "done"), ("done", "accepted"), ("draft", "accepted")):
            conn.execute(
                "INSERT INTO transitions (at, root, workspace, unit, artifact, stage, "
                "from_state, to_state, actor, session, source, machine) "
                "VALUES ('t', '/w', 'p', '0001_a', 'plan.md', 'plan', ?, ?, 'a', 's', 'src', 'unit')",
                (frm, to),
            )
        conn.execute("INSERT INTO unit_questions VALUES ('/w', 'p', '0001_a', 'spec.md', 1, 'q')")
        for n, (authority, name) in enumerate(self.ANSWERS, 1):
            conn.execute(
                "INSERT INTO unit_answers (root, workspace, unit, artifact, ref, text, "
                "answered_by, authority, date, via) VALUES ('/w', 'p', '0001_a', 'spec.md', "
                "?, 't', ?, ?, 'd', 'v')",
                (str(n), name, authority),
            )
        conn.execute(
            "INSERT INTO unit_decisions (workspace, unit, text, authority, recorded_by, date) "
            "VALUES ('p', '0001_a', 't', 'person', 'x', 'd')"
        )
        for kind, stage, record in self.RUNS:
            conn.execute(
                "INSERT INTO runs (at, root, workspace, stage, kind, record) "
                "VALUES ('t', '/w', '/w', ?, ?, ?)",
                (stage, kind, record),
            )
        conn.execute(
            "INSERT INTO step_events VALUES ('r', 1, 1, 'end', '{\"outcome\": \"exhausted\"}', 1)"
        )
        conn.execute(
            "INSERT INTO attempts (id, machine, workspace, unit) "
            "VALUES (1, 'step', '/w', 'u'), (2, 'step', '/w', 'u')"
        )
        conn.execute(
            "INSERT INTO attempt_moves (attempt, seq, moved_to, outcome, at) "
            "VALUES (1, 1, 'ended', 'exhausted', 't'), (2, 1, 'ended', 'done', 't')"
        )
        for state, unit, reason in (("pending", "", ""), ("accepted", "0007_x", "")):
            conn.execute(
                "INSERT INTO scan_proposals (workspace, run, type, slug, title, problem, "
                "sources, state, unit, by, at, decided, reason) VALUES ('/w', 1, 'fix', "
                "'s', 't', 'p', '[]', ?, ?, 'owner', 'a', 'd', ?)",
                (state, unit, reason),
            )
        conn.execute("INSERT INTO scan_cursor VALUES ('/w', '2026-10-05T17:14:13+00:00', '[]')")
        for key, value in self.PREFS.items():
            conn.execute("INSERT INTO prefs (key, value) VALUES (?, ?)", (key, json.dumps(value)))
        conn.execute(
            "INSERT INTO review_findings (round, finding, open, label, severity, rule, text) "
            "VALUES (1, 'F1', 1, 'open', 'low', 'R2', 't')"
        )
        conn.execute(
            "INSERT INTO vault_secrets VALUES ('ws:t', '/w', 'd', '[\"impl\"]', '[\"env\"]', 0, "
            "'owner', 't', 0)"
        )

    def _all(self, sql):
        with self.data.connect() as conn:
            return [tuple(r) for r in conn.execute(sql)]

    def _columns(self, table):
        return {r[1] for r in self._all(f"PRAGMA table_info({table})")}

    def test_the_schema_is_a_fresh_ones(self):
        with tempfile.TemporaryDirectory() as d:
            fresh = Data(d)
            fresh.version()
            with fresh.write() as conn:
                for statement in plugin.tables_of(features.FEATURES):
                    conn.execute(statement)
            with fresh.connect() as conn:
                want = _shape(conn)
        with self.data.connect() as conn:
            got = _shape(conn)
        self.assertEqual(got, want)

    def test_what_a_file_fed_and_the_old_tables_are_gone(self):
        names = {r[0] for r in self._all("SELECT name FROM sqlite_master")}
        gone = {"stage_results", "stage_results_scope", "idea_meta", "unit_seen"}
        self.assertFalse((gone | {"scan_proposals", "scan_runs", "scan_cursor"}) & names)
        self.assertLessEqual({"outputs_scope", "unit_links_scope", "unit_decisions_scope"}, names)
        self.assertNotIn("lane", self._columns("unit_meta"))
        self.assertNotIn("raw", self._columns("unit_unknowns"))
        self.assertEqual(self._all("SELECT field FROM unit_unknowns"), [("ingest",)])
        self.assertEqual(self._all("SELECT key FROM migrations"), [("keep",)])

    def test_links_keep_their_order_without_repo_rows(self):
        self.assertEqual(
            self._all("SELECT kind, ref, pos FROM unit_links ORDER BY pos"),
            [("idea", "p/ideas/0001_x.md", 0), ("depends", "p/0002_b", 2)],
        )
        with self.assertRaises(sqlite3.IntegrityError), self.data.connect() as conn:
            conn.execute("INSERT INTO unit_links VALUES ('/w', 'p', 'u', 'repo', 'p', 0)")

    def test_a_stored_done_is_accepted_and_an_exhausted_run_failed(self):
        self.assertEqual(
            self._all("SELECT from_state, to_state FROM transitions ORDER BY id"),
            [("not started", "accepted"), ("accepted", "accepted"), ("draft", "accepted")],
        )
        self.assertEqual(
            self._all(
                "SELECT json_extract(record, '$.outcome'), json_extract(record, '$.status') "
                "FROM runs WHERE id IN (3, 4) ORDER BY id"
            ),
            [("failed", "failed"), ("failed", "failed")],
        )
        self.assertEqual(
            self._all("SELECT outcome FROM attempt_moves ORDER BY attempt"),
            [("failed",), ("done",)],
        )
        self.assertEqual(
            self._all("SELECT json_extract(event, '$.outcome') FROM step_events"), [("failed",)]
        )

    def test_records_rise_to_their_current_contract(self):
        got = self._all(
            "SELECT id, unit, agent, version, json_extract(object, '$.type'), "
            "json_extract(object, '$.variant'), json_type(object, '$.files'), "
            "json_type(object, '$.steps'), json_extract(object, '$.rests_on'), "
            "json_extract(object, '$.questions[0].recommendation'), "
            "json_extract(object, '$.questions[1].recommendation') FROM outputs ORDER BY id"
        )
        self.assertEqual(
            got,
            [
                (1, "0001_a", "intent", 3, "fix", None, None, None, None, "", ""),
                (2, "0002_b", "intent", 3, "feat", None, None, None, None, None, None),
                (3, "0001_a", "impl", 3, None, None, None, None, None, None, None),
                (4, "0001_a", "spec", 2, None, None, None, None, None, None, None),
                (5, "0001_a", "spec", 2, None, None, None, None, None, "", ""),
                (6, "0001_a", "plan", 4, None, "novel", "array", "array", '["U1"]', None, None),
                (7, "0002_b", "plan", 4, None, "novel", "array", "array", "[]", None, None),
                (8, "0001_a", "spike", 2, None, None, None, None, None, None, None),
            ],
        )
        self.assertEqual(self._all("SELECT recommendation FROM unit_questions"), [("",)])
        self.assertEqual(
            self._all("SELECT COUNT(*) FROM outputs WHERE json_type(object, '$.impl')"), [(0,)]
        )

    def test_every_unit_walks_the_full_process_and_a_finding_keeps_its_criterion(self):
        self.assertEqual(
            self._all("SELECT unit, process FROM unit_meta ORDER BY unit"),
            [("0001_a", "coscc-sdlc/full"), ("0002_b", "coscc-sdlc/full")],
        )
        self.assertEqual(self._all("SELECT criterion FROM review_findings"), [("R2",)])
        self.assertIn("criteria", self._columns("review_rounds"))

    def test_an_old_shaped_decisions_table_is_replaced_empty(self):
        cols = self._columns("unit_decisions")
        self.assertLessEqual({"kind", "fields", "decided_by", "root"}, cols)
        self.assertFalse({"text", "authority", "recorded_by"} & cols)
        self.assertEqual(self._all("SELECT COUNT(*) FROM unit_decisions"), [(0,)])

    def test_answers_carry_by_from_the_name_rule_and_the_old_columns_are_gone(self):
        cols = self._columns("unit_answers")
        self.assertFalse({"answered_by", "authority"} & cols)
        self.assertLessEqual({"name", "by"}, cols)
        self.assertEqual(
            self._all('SELECT name, "by" FROM unit_answers ORDER BY id'),
            [
                ("Leif (CoS), x", "delegated"),
                ("Claude (CoS), y", "delegated"),
                ("agent, z", "delegated"),
                ("owner", "person"),
                ("the originator (relayed by Leif)", "person"),
                ("Jera", "delegated"),
            ],
        )
        with self.assertRaises(sqlite3.IntegrityError), self.data.write() as conn:
            conn.execute(
                "INSERT INTO unit_answers (root, workspace, unit, artifact, ref, text, name, "
                "date, via, \"by\") VALUES ('/w', 'p', '0001_a', 'spec.md', '9', 't', "
                "'n', 'd', 'v', 'agent')"
            )

    def test_the_scan_features_proposals_state_and_cursor_are_its_rows(self):
        self.assertEqual(
            self._all("SELECT agent, decision, made FROM proposals ORDER BY id"),
            [("scan", "pending", ""), ("scan", "accepted", "0007_x")],
        )
        self.assertEqual(
            self._all("SELECT stage, json_extract(record, '$.data_until') FROM runs WHERE id < 3"),
            [("scan", "2026-10-05T17:14:13+00:00"), ("estimate", None)],
        )
        self.assertEqual(self.data.pref("agents.state"), {"scan": {"/w": "on", "/v": "off"}})
        self.assertEqual(self.data.pref("features.state"), {"release": {"/w": "on"}})
        self.assertIsNone(self.data.pref("features.schedule"))
        self.assertEqual(self.data.pref("density"), "compact")

    def test_the_agent_prefs_are_the_owners_layer_and_gebo_keeps_the_coders_model(self):
        agents = self.data.root / "packs" / "local" / "agents"
        self.assertEqual(
            sorted(p.name for p in agents.iterdir()), ["impl.md", "integrate.md", "review.md"]
        )
        self.assertIn('"claude-sonnet-5"', (agents / "integrate.md").read_text(encoding="utf-8"))
        self.assertIn('"ceilings": {"usd": 9}', (agents / "review.md").read_text(encoding="utf-8"))
        self.assertEqual(self._all("SELECT key FROM prefs WHERE key LIKE '%:%'"), [])

    def test_a_merge_record_is_merge_and_a_secrets_list_is_agents(self):
        self.assertEqual(
            self._all("SELECT kind, json_extract(record, '$.kind') FROM runs WHERE id = 5"),
            [("merge", "merge")],
        )
        self.assertEqual(
            self._all(
                "SELECT json_extract(record, '$.agents'), json_type(record, '$.stages') "
                "FROM runs WHERE kind = 'vault' ORDER BY id"
            ),
            [('["impl"]', None), (None, None)],
        )
        self.assertEqual(self._all("SELECT agents FROM vault_secrets"), [('["impl"]',)])


if __name__ == "__main__":
    unittest.main()
