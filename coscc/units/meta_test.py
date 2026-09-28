"""`0135` plan steps 4-6: the import, the ingest and the snapshot `cos.mjs --state` reads.

The fixture store (`testdata/meta_store`) holds one unit of each kind the plan's step 1
names, and `testdata/meta_store_before.json` is `status --json` of it, taken by `cos.mjs`
before this unit changed it. The R5 test reads the same store through the database and asks
for the same answer on every field R5 lists.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from coscc.agent import harness
from coscc.data import Data
from coscc.units.history import History
from coscc.units.meta import SOURCE, MetaError, UnitMeta

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "testdata" / "meta_store"
BEFORE = HERE / "testdata" / "meta_store_before.json"
WS = "/w/proj"
NAMES = {"proj": WS}


def status(store: Path, snapshot: dict) -> dict:
    done = subprocess.run(
        ["node", str(harness.script()), "--root", str(store), "--state", "-", "status", "--json"],
        input=json.dumps(snapshot), capture_output=True, text=True, env=harness.child_env(), check=True,
    )
    return json.loads(done.stdout)


def r5(unit: dict) -> dict:
    """The fields spec R5 compares, and nothing else: never `problems` (C6)."""
    return {
        "artifacts": {f: a.get("status") for f, a in unit["artifacts"].items()},
        "phase": unit.get("phase"), "type": unit.get("type"), "hold": unit.get("hold"),
        "holdMoves": unit.get("holdMoves"), "questions": unit.get("questions"), "open": unit.get("open"),
        "dependsOn": [(d["ref"], d["merged"]) for d in unit.get("dependsOn") or []],
        "next": (unit["next"]["stage"], unit["next"]["why"]),
    }


def ingest(service, cwd: str, unit: str) -> None:
    """Test glue (plan Risk 4): a file a test wrote by hand reaches `cos.db` the way a
    finished step's does, through `Service._ingest`. Since `0135` nothing else reads it."""
    done = asyncio.run(service._ingest(cwd, unit, {"outcome": "done", "stage": "test"}))
    assert not done, done


def snapshot_of(root, peers=(), units_=None) -> dict:
    """Test glue (plan step 8): the snapshot the app would hand `cos.mjs` for the store
    `root`, and for each `(name, store)` of `peers`, built by the app's own import into a
    throwaway database. A store is keyed by its resolved path."""
    with tempfile.TemporaryDirectory() as d:
        meta = UnitMeta(Path(d) / "work", Data(Path(d) / "data"))
        own = str(Path(root).resolve())
        names = {name: str(Path(store).resolve()) for name, store in peers}
        for key in {own, *names.values()}:
            if (Path(key) / ".cos").is_dir():
                meta.import_store(key, key)
        return meta.snapshot(own, names, units_)


class WithSnapshot:
    """Test glue (plan step 8): `coscc/units/board.py` as a test module sees it, each question
    to `cos.mjs` handed `snapshot_of` its store when the test gave no `state` — what
    `Service._snapshot` hands it in the app. Every other attribute, and every patch a test
    sets on it, is the module's own."""

    ASKS = ("read", "gate", "next_step", "pr_text", "rerun", "screens")

    def __init__(self, module):
        object.__setattr__(self, "_module", module)

    def __getattr__(self, name):
        found = getattr(self._module, name)
        if name not in self.ASKS:
            return found

        def asked(units_root, *args, **kwargs):
            if kwargs.get("state") is None:
                try:
                    kwargs["state"] = snapshot_of(units_root)
                except MetaError:
                    # No script to read with: the question fails as it would in the app.
                    kwargs["state"] = self._module.EMPTY_STATE
            return found(units_root, *args, **kwargs)

        return asked

    def __setattr__(self, name, value):
        setattr(self._module, name, value)

    def __delattr__(self, name):
        delattr(self._module, name)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.store = self.tmp / "store"
        shutil.copytree(FIXTURE, self.store)
        self.data = Data(self.tmp / "data")
        self.meta = UnitMeta(self.tmp / "work", self.data)

    def count(self, table: str) -> int:
        with self.data.connect() as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class TheImport(Base):
    def test_every_directory_under_cos_has_a_unit_meta_row_after_import(self):
        self.meta.import_store(WS, self.store)
        with self.data.connect() as conn:
            rows = {r["unit"] for r in conn.execute("SELECT unit FROM unit_meta")}
        wanted = {d.name for d in (self.store / ".cos").iterdir() if d.is_dir() and d.name != "ideas"}
        self.assertEqual(rows, wanted)
        self.assertIn("not_a-unit", rows)
        self.assertIn("0003_old-unit", rows)

    def test_every_imported_unit_has_lane_full(self):
        self.meta.import_store(WS, self.store)
        with self.data.connect() as conn:
            self.assertEqual({r[0] for r in conn.execute("SELECT lane FROM unit_meta")}, {"full"})

    def test_an_unreadable_field_is_listed_not_skipped(self):
        unknowns = self.meta.import_store(WS, self.store)
        found = {(u["unit"], u["artifact"], u["field"], u["raw"]) for u in unknowns}
        self.assertIn(("0015_no-status", "intent.md", "status", None), found)
        self.assertIn(("0016_bad-status", "spec.md", "status", "approved"), found)
        self.assertIn(("0003_old-unit", "intent.md", "type", None), found)
        self.assertEqual(len(self.meta.unknowns([WS])), len(unknowns))

    def test_a_second_import_adds_no_row(self):
        self.assertIsNotNone(self.meta.import_store(WS, self.store))
        tables = ("unit_meta", "unit_links", "unit_answers", "unit_holds", "unit_unknowns",
                  "unit_questions", "unit_seen", "idea_meta", "transitions")
        before = {t: self.count(t) for t in tables}
        self.assertIsNone(self.meta.import_store(WS, self.store))
        # Past the `migrations` key too: the rows' own keys hold.
        with self.data.write() as conn:
            conn.execute("DELETE FROM migrations")
        self.meta.import_store(WS, self.store)
        self.assertEqual({t: self.count(t) for t in ("unit_answers", "unit_holds", "transitions")},
                         {t: before[t] for t in ("unit_answers", "unit_holds", "transitions")})
        self.assertEqual(before["unit_meta"], self.count("unit_meta"))

    def test_the_snapshot_of_an_imported_store_carries_each_artifacts_status(self):
        self.meta.import_store(WS, self.store)
        snap = self.meta.snapshot(WS, NAMES)
        self.assertEqual(snap["workspace"], "proj")
        full = snap["units"]["proj/0010_full-loop"]["artifacts"]
        self.assertEqual(full["plan.md"]["status"], "done")
        self.assertEqual(snap["units"]["proj/0014_changes-requested"]["artifacts"]["review.md"]["status"], "changes-requested")
        self.assertEqual(snap["units"]["proj/0016_bad-status"]["artifacts"]["spec.md"], {"status": None, "raw": "approved", "questions": None})
        self.assertEqual(snap["units"]["proj/0017_linked"]["links"],
                         {"idea": "ideas/0001_x.md", "repo": "proj", "dependsOn": ["0010_full-loop"]})
        self.assertEqual([h["state"] for h in snap["units"]["proj/0011_paused-then-resumed"]["holds"]], ["paused", "active"])
        rows = History(self.tmp / "work", self.data).transitions(WS, "0010_full-loop")
        self.assertEqual({r["source"] for r in rows}, {SOURCE})


class TheSnapshotDecides(Base):
    def test_status_json_of_the_imported_fixture_equals_the_before_file_on_r5s_fields(self):
        self.meta.import_store(WS, self.store)
        after = status(self.store, self.meta.snapshot(WS, NAMES))
        before = json.loads(BEFORE.read_text(encoding="utf-8"))
        self.assertEqual([u["name"] for u in after["units"]], [u["name"] for u in before["units"]])
        for old, new in zip(before["units"], after["units"]):
            self.assertEqual(r5(new), r5(old), old["name"])

    def test_a_snapshot_for_one_unit_carries_it_and_what_it_depends_on(self):
        self.meta.import_store(WS, self.store)
        snap = self.meta.snapshot(WS, NAMES, ["0017_linked"])
        self.assertEqual(sorted(snap["units"]), ["proj/0010_full-loop", "proj/0017_linked"])
        self.assertEqual(snap["ideas"]["proj"][0]["id"], "0001_x")
        done = subprocess.run(
            ["node", str(harness.script()), "--root", str(self.store), "--state", "-", "next", "0017_linked"],
            input=json.dumps(snap), capture_output=True, text=True, env=harness.child_env(), check=True,
        )
        full = subprocess.run(
            ["node", str(harness.script()), "--root", str(self.store), "--state", "-", "next", "0017_linked"],
            input=json.dumps(self.meta.snapshot(WS, NAMES)), capture_output=True, text=True,
            env=harness.child_env(), check=True,
        )
        self.assertEqual(json.loads(done.stdout), json.loads(full.stdout))

    def test_changing_a_status_in_the_snapshot_changes_the_output(self):
        self.meta.import_store(WS, self.store)
        snap = self.meta.snapshot(WS, NAMES)
        snap["units"]["proj/0013_open-question"]["artifacts"]["spec.md"]["status"] = "accepted"
        unit = next(u for u in status(self.store, snap)["units"] if u["name"] == "0013_open-question")
        self.assertEqual(unit["artifacts"]["spec.md"]["status"], "accepted")
        self.assertEqual(unit["next"]["stage"], "plan")


class TheIngest(Base):
    def ingest(self, unit: str) -> list:
        return self.meta.ingest(WS, self.store, unit, actor="stage:review", session="s1", source="run:review")

    def test_a_review_replying_changes_requested_folds_to_changes_requested(self):
        self.meta.import_store(WS, self.store)
        review = self.store / ".cos" / "0014_changes-requested" / "review.md"
        review.write_text(review.read_text().replace("Status: changes-requested", "Status: accepted"))
        self.ingest("0014_changes-requested")
        review.write_text(review.read_text().replace("Status: accepted", "Status: changes-requested"))
        self.ingest("0014_changes-requested")
        state = History(self.tmp / "work", self.data).state(WS, "0014_changes-requested")
        self.assertEqual(state["review.md"], "changes-requested")
        rows = History(self.tmp / "work", self.data).transitions(WS, "0014_changes-requested", "review.md")
        self.assertEqual((rows[-1]["actor"], rows[-1]["session"], rows[-1]["source"]), ("stage:review", "s1", "run:review"))

    def test_impl_writing_plan_md_done_folds_plan_md_to_done(self):
        self.meta.import_store(WS, self.store)
        plan = self.store / ".cos" / "0014_changes-requested" / "plan.md"
        plan.write_text(plan.read_text().replace("Status: accepted", "Status: done"))
        self.ingest("0014_changes-requested")
        self.assertEqual(History(self.tmp / "work", self.data).state(WS, "0014_changes-requested")["plan.md"], "done")

    def test_an_artifact_unchanged_since_the_last_ingest_is_not_read_again(self):
        self.meta.import_store(WS, self.store)
        before = self.count("transitions"), self.count("unit_questions")
        self.assertEqual(self.ingest("0013_open-question"), [])
        self.assertEqual((self.count("transitions"), self.count("unit_questions")), before)

    def test_an_intent_read_again_replaces_its_links(self):
        self.meta.import_store(WS, self.store)
        intent = self.store / ".cos" / "0017_linked" / "intent.md"
        intent.write_text(intent.read_text().replace(" Depends on: 0010_full-loop.", ""))
        self.ingest("0017_linked")
        links = self.meta.snapshot(WS, NAMES)["units"]["proj/0017_linked"]["links"]
        self.assertEqual(links["dependsOn"], None)

    def test_a_failed_ingest_is_a_problem_on_the_card(self):
        self.meta.import_store(WS, self.store)
        self.meta.ingest_failed(WS, "0013_open-question", "cos.mjs meta exited 2")
        unit = next(u for u in status(self.store, self.meta.snapshot(WS, NAMES))["units"] if u["name"] == "0013_open-question")
        self.assertTrue(any("cos.mjs meta exited 2" in p for p in unit["problems"]), unit["problems"])
        self.assertEqual(self.meta.unknowns([WS]), [u for u in self.meta.unknowns([WS]) if u["field"] != "ingest"])


class AnswersAndHolds(Base):
    def test_an_answer_and_a_hold_reach_the_snapshot_in_order(self):
        self.meta.import_store(WS, self.store)
        self.meta.add_answer(WS, "0013_open-question", "spec.md", 2, "Bao duyệt.", "Bao", "2026-09-28", "product")
        self.meta.add_hold(WS, "0013_open-question", "paused", "chờ", "Bao", "2026-09-28", "product")
        unit = next(u for u in status(self.store, self.meta.snapshot(WS, NAMES))["units"] if u["name"] == "0013_open-question")
        self.assertEqual(unit["open"], 0)
        self.assertEqual(unit["hold"]["state"], "paused")
        self.assertEqual(unit["next"]["why"], "paused")


class TheImportReport(unittest.TestCase):
    """R4, through `Service.settings`: what the import could not read, by workspace name."""

    def setUp(self):
        from coscc import units
        from coscc.config import Config
        from coscc.agent.sessions import Sessions
        from coscc.service import Service

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        self.cwd = str(tmp / "work" / "proj")
        Path(self.cwd).mkdir(parents=True)
        config = Config(workspaces=(self.cwd,), working_dir=str(tmp / "work"), data_dir=str(tmp / "data"))
        self.service = Service(config, Sessions(config))
        self.store = units.root(self.cwd, config.data_dir)

    def test_settings_lists_each_unreadable_field_by_workspace_name_and_not_a_failed_ingest(self):
        shutil.copytree(FIXTURE, self.store)
        asyncio.run(self.service.board(self.cwd))
        self.service._unit_meta().ingest_failed(self.service._journal_key(self.cwd), "0013_open-question", "boom")
        report = self.service.settings()["import_report"]
        self.assertEqual(report["problem"], "")
        found = {(r["workspace"], r["unit"], r["artifact"], r["field"]) for r in report["rows"]}
        self.assertIn(("proj", "0016_bad-status", "spec.md", "status"), found)
        self.assertIn(("proj", "0015_no-status", "intent.md", "status"), found)
        self.assertNotIn("ingest", {r["field"] for r in report["rows"]})
        self.assertFalse(any("/" in r["workspace"] for r in report["rows"]))

    def test_a_store_read_cleanly_has_no_row(self):
        self.store.mkdir(parents=True)
        asyncio.run(self.service.create_unit(self.cwd, "a-problem", "x"))
        asyncio.run(self.service.board(self.cwd))
        self.assertEqual(self.service.settings()["import_report"], {"rows": [], "problem": ""})


if __name__ == "__main__":
    unittest.main()
