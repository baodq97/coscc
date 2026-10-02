"""The fixture store (`testdata/meta_store`) holds one unit of each kind the plan's step 1 names,
and `testdata/meta_store_before.json` is `status --json` of it, taken by `cos.mjs` before this unit
changed it."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.agent import harness
from coscc.data import Data
from coscc.runlog.journal import Journal
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
        input=json.dumps(snapshot),
        capture_output=True,
        text=True,
        env=harness.child_env(),
        check=True,
    )
    return json.loads(done.stdout)


def kept_fields(unit: dict) -> dict:
    return {
        "artifacts": {f: a.get("status") for f, a in unit["artifacts"].items()},
        "phase": unit.get("phase"),
        "type": unit.get("type"),
        "hold": unit.get("hold"),
        "holdMoves": unit.get("holdMoves"),
        "questions": unit.get("questions"),
        "open": unit.get("open"),
        "dependsOn": [(d["ref"], d["merged"]) for d in unit.get("dependsOn") or []],
        "next": (unit["next"]["stage"], unit["next"]["why"]),
    }


def ingest(service, cwd: str, unit: str) -> None:
    """Test glue (plan Risk 4): a file a test wrote by hand reaches `cos.db` the way a finished
    step's does, through `Answers.ingest`."""
    done = asyncio.run(service.answers.ingest(cwd, unit, {"outcome": "done", "stage": "test"}))
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
    `Workspaces.snapshot` hands it in the app. Every other attribute, and every patch a test
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
        wanted = {
            d.name for d in (self.store / ".cos").iterdir() if d.is_dir() and d.name != "ideas"
        }
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
        tables = (
            "unit_meta",
            "unit_links",
            "unit_answers",
            "unit_holds",
            "unit_unknowns",
            "unit_questions",
            "unit_seen",
            "idea_meta",
            "transitions",
        )
        before = {t: self.count(t) for t in tables}
        self.assertIsNone(self.meta.import_store(WS, self.store))
        # Past the `migrations` key too: the rows' own keys hold.
        with self.data.write() as conn:
            conn.execute("DELETE FROM migrations")
        self.meta.import_store(WS, self.store)
        self.assertEqual(
            {t: self.count(t) for t in ("unit_answers", "unit_holds", "transitions")},
            {t: before[t] for t in ("unit_answers", "unit_holds", "transitions")},
        )
        self.assertEqual(before["unit_meta"], self.count("unit_meta"))

    def test_the_snapshot_of_an_imported_store_carries_each_artifacts_status(self):
        self.meta.import_store(WS, self.store)
        snap = self.meta.snapshot(WS, NAMES)
        self.assertEqual(snap["workspace"], "proj")
        full = snap["units"]["proj/0010_full-loop"]["artifacts"]
        self.assertEqual(full["plan.md"]["status"], "done")
        self.assertEqual(
            snap["units"]["proj/0014_changes-requested"]["artifacts"]["review.md"]["status"],
            "changes-requested",
        )
        self.assertEqual(
            snap["units"]["proj/0016_bad-status"]["artifacts"]["spec.md"],
            {"status": None, "raw": "approved", "questions": None},
        )
        self.assertEqual(
            snap["units"]["proj/0017_linked"]["links"],
            {"idea": "ideas/0001_x.md", "repo": "proj", "dependsOn": ["0010_full-loop"]},
        )
        self.assertEqual(
            [h["state"] for h in snap["units"]["proj/0011_paused-then-resumed"]["holds"]],
            ["paused", "active"],
        )
        rows = History(self.tmp / "work", self.data).transitions(WS, "0010_full-loop")
        self.assertEqual({r["source"] for r in rows}, {SOURCE})


class TheSnapshotDecides(Base):
    def test_status_json_of_the_imported_fixture_equals_the_before_file_on_fields(self):
        self.meta.import_store(WS, self.store)
        after = status(self.store, self.meta.snapshot(WS, NAMES))
        before = json.loads(BEFORE.read_text(encoding="utf-8"))
        self.assertEqual([u["name"] for u in after["units"]], [u["name"] for u in before["units"]])
        moved = {"0003_old-unit": {"next": ("", "agent-cannot-skip")}}
        for old, new in zip(before["units"], after["units"]):
            self.assertEqual(
                kept_fields(new), {**kept_fields(old), **moved.get(old["name"], {})}, old["name"]
            )

    def test_a_snapshot_for_one_unit_carries_it_and_what_it_depends_on(self):
        self.meta.import_store(WS, self.store)
        snap = self.meta.snapshot(WS, NAMES, ["0017_linked"])
        self.assertEqual(sorted(snap["units"]), ["proj/0010_full-loop", "proj/0017_linked"])
        self.assertEqual(snap["ideas"]["proj"][0]["id"], "0001_x")
        done = subprocess.run(
            [
                "node",
                str(harness.script()),
                "--root",
                str(self.store),
                "--state",
                "-",
                "next",
                "0017_linked",
            ],
            input=json.dumps(snap),
            capture_output=True,
            text=True,
            env=harness.child_env(),
            check=True,
        )
        full = subprocess.run(
            [
                "node",
                str(harness.script()),
                "--root",
                str(self.store),
                "--state",
                "-",
                "next",
                "0017_linked",
            ],
            input=json.dumps(self.meta.snapshot(WS, NAMES)),
            capture_output=True,
            text=True,
            env=harness.child_env(),
            check=True,
        )
        self.assertEqual(json.loads(done.stdout), json.loads(full.stdout))

    def relate(self, unit: str, other: str, op: str = "add", rtype: str = "phụ thuộc") -> None:
        Journal(self.tmp / "work", self.data).append(
            {
                "kind": "relation",
                "workspace": WS,
                "unit": unit,
                "other": other,
                "type": rtype,
                "op": op,
                "reason": "r",
                "by": "owner",
            }
        )

    def test_a_snapshot_carries_the_dependencies_the_backlog_holds_in_force(self):
        self.meta.import_store(WS, self.store)
        self.relate("0017_linked", "0013_open-question")
        self.relate("0013_open-question", "0010_full-loop")
        self.relate("0013_open-question", "0010_full-loop", op="remove")
        self.relate("0010_full-loop", "0013_open-question", rtype="liên quan")
        units = self.meta.snapshot(WS, NAMES)["units"]
        self.assertEqual(
            units["proj/0017_linked"]["links"]["backlog"],
            [{"ref": "0013_open-question", "source": "backlog"}],
        )
        # One removed, one of another type, one never related: no key at all.
        for name in ("0013_open-question", "0010_full-loop", "0011_paused-then-resumed"):
            self.assertNotIn("backlog", units[f"proj/{name}"]["links"], name)

    def test_a_snapshot_for_one_unit_carries_the_unit_its_relation_names(self):
        self.meta.import_store(WS, self.store)
        self.relate("0017_linked", "0013_open-question")
        snap = self.meta.snapshot(WS, NAMES, ["0017_linked"])
        self.assertEqual(
            sorted(snap["units"]),
            ["proj/0010_full-loop", "proj/0013_open-question", "proj/0017_linked"],
        )
        gate = subprocess.run(
            [
                "node",
                str(harness.script()),
                "--root",
                str(self.store),
                "--state",
                "-",
                "gate",
                "0017_linked",
                "impl",
                "--json",
            ],
            input=json.dumps(snap),
            capture_output=True,
            text=True,
            env=harness.child_env(),
        )
        self.assertIn("waiting-on", json.loads(gate.stdout)["reasons"])

    def test_changing_a_status_in_the_snapshot_changes_the_output(self):
        self.meta.import_store(WS, self.store)
        snap = self.meta.snapshot(WS, NAMES)
        snap["units"]["proj/0013_open-question"]["artifacts"]["spec.md"]["status"] = "accepted"
        unit = next(
            u for u in status(self.store, snap)["units"] if u["name"] == "0013_open-question"
        )
        self.assertEqual(unit["artifacts"]["spec.md"]["status"], "accepted")
        self.assertEqual(unit["next"]["stage"], "plan")


class TheIngest(Base):
    def ingest(self, unit: str) -> list:
        return self.meta.ingest(
            WS, self.store, unit, actor="stage:review", session="s1", source="run:review"
        )

    def test_a_review_replying_changes_requested_folds_to_changes_requested(self):
        self.meta.import_store(WS, self.store)
        review = self.store / ".cos" / "0014_changes-requested" / "review.md"
        review.write_text(
            review.read_text().replace("Status: changes-requested", "Status: accepted")
        )
        self.ingest("0014_changes-requested")
        review.write_text(
            review.read_text().replace("Status: accepted", "Status: changes-requested")
        )
        self.ingest("0014_changes-requested")
        state = History(self.tmp / "work", self.data).state(WS, "0014_changes-requested")
        self.assertEqual(state["review.md"], "changes-requested")
        rows = History(self.tmp / "work", self.data).transitions(
            WS, "0014_changes-requested", "review.md"
        )
        self.assertEqual(
            (rows[-1]["actor"], rows[-1]["session"], rows[-1]["source"]),
            ("stage:review", "s1", "run:review"),
        )

    def test_impl_writing_plan_md_done_folds_plan_md_to_done(self):
        self.meta.import_store(WS, self.store)
        plan = self.store / ".cos" / "0014_changes-requested" / "plan.md"
        plan.write_text(plan.read_text().replace("Status: accepted", "Status: done"))
        self.ingest("0014_changes-requested")
        self.assertEqual(
            History(self.tmp / "work", self.data).state(WS, "0014_changes-requested")["plan.md"],
            "done",
        )

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
        unit = next(
            u
            for u in status(self.store, self.meta.snapshot(WS, NAMES))["units"]
            if u["name"] == "0013_open-question"
        )
        self.assertTrue(
            any("cos.mjs meta exited 2" in p for p in unit["problems"]), unit["problems"]
        )
        self.assertEqual(
            self.meta.unknowns([WS]),
            [u for u in self.meta.unknowns([WS]) if u["field"] != "ingest"],
        )


class AMergeIsARow(Base):
    """`merged`, which a `Depends on:` is judged on, is the PR machine's row, or a ship recorded
    before the machine; never the status of `ship.md` alone."""

    def merged(self, unit: str) -> bool:
        return self.meta.snapshot(WS, NAMES)["units"][f"proj/{unit}"]["merged"]

    def machine(self, unit: str, artifact: str, to_state: str, transition: str, guard: str) -> None:
        History(self.tmp / "work", self.data).record(
            WS,
            unit,
            artifact,
            to_state,
            source=f"prmachine:{transition}",
            guard=guard,
            authority="code",
        )

    def test_a_ship_imported_before_the_machine_is_merged_and_nothing_else_is(self):
        self.meta.import_store(WS, self.store)
        self.assertTrue(self.merged("0010_full-loop"))
        self.assertFalse(self.merged("0013_open-question"))

    def test_an_accepted_ship_md_read_at_the_end_of_another_step_is_not_merged(self):
        self.meta.import_store(WS, self.store)
        (self.store / ".cos" / "0013_open-question" / "ship.md").write_text(
            "# Ship\nStatus: accepted.\n"
        )
        self.meta.ingest(
            WS,
            self.store,
            "0013_open-question",
            actor="stage:impl",
            session="s1",
            source="run:impl",
        )
        self.assertFalse(self.merged("0013_open-question"))
        self.meta.ingest(
            WS,
            self.store,
            "0013_open-question",
            actor="stage:ship",
            session="s2",
            source="run:ship",
            wrote="ship.md",
        )
        self.assertTrue(self.merged("0013_open-question"))

    def test_the_machines_merged_row_is_merged_and_its_other_rows_are_not(self):
        self.meta.import_store(WS, self.store)
        unit = "0013_open-question"
        self.machine(unit, "pr.md", "accepted", "open", "branch-named")
        self.assertFalse(self.merged(unit))
        self.machine(unit, "ship.md", "draft", "merge-requested", "ship-ready")
        self.assertFalse(self.merged(unit))
        self.machine(unit, "ship.md", "accepted", "merged", "merge-read")
        self.assertTrue(self.merged(unit))
        # A CI read after it moves nothing.
        self.machine(unit, "pr.md", "accepted", "ci", "ci-at-head")
        self.assertTrue(self.merged(unit))

    def test_a_dependency_waits_until_the_row_says_merged(self):
        self.meta.import_store(WS, self.store)
        # The machine's fold wins over a ship read before it: here it closed the pull request.
        self.machine("0010_full-loop", "pr.md", "accepted", "closed", "close-read")
        unit = next(
            u
            for u in status(self.store, self.meta.snapshot(WS, NAMES))["units"]
            if u["name"] == "0017_linked"
        )
        self.assertEqual(
            unit["dependsOn"],
            [
                {
                    "ref": "0010_full-loop",
                    "merged": False,
                    "why": "not merged: the app holds no merge of it",
                }
            ],
        )
        self.machine("0010_full-loop", "ship.md", "accepted", "merged", "merge-read")
        unit = next(
            u
            for u in status(self.store, self.meta.snapshot(WS, NAMES))["units"]
            if u["name"] == "0017_linked"
        )
        self.assertEqual(
            unit["dependsOn"], [{"ref": "0010_full-loop", "merged": True, "why": "merged"}]
        )


class AnswersAndHolds(Base):
    def test_an_answer_and_a_hold_reach_the_snapshot_in_order(self):
        self.meta.import_store(WS, self.store)
        self.meta.add_answer(
            WS, "0013_open-question", "spec.md", 2, "Bao duyệt.", "Bao", "2026-09-28", "product"
        )
        self.meta.add_hold(
            WS, "0013_open-question", "paused", "chờ", "Bao", "2026-09-28", "product"
        )
        unit = next(
            u
            for u in status(self.store, self.meta.snapshot(WS, NAMES))["units"]
            if u["name"] == "0013_open-question"
        )
        self.assertEqual(unit["open"], 0)
        self.assertEqual(unit["hold"]["state"], "paused")
        self.assertEqual(unit["next"]["why"], "paused")


class AnImportedAnswerSaysWhoseItIs(Base):
    """An answer read from a file says whose it was by what the file carries — Jera's `Via:
    precedent.`, a delegation's line — and every other is a person's."""

    UNIT = "0013_open-question"
    BLOCKS = (
        "\n### Câu 2\nAnswered by: Jera. Date: 2026-09-24. Via: precedent.\n\nBao duyệt, như D1.\n"
        "\n### Câu 3\nAnswered by: Leif. Date: 2026-09-24. Via: product.\n\nKhông.\n\nTheo ủy quyền: D2, Bao, 2026-09-20.\n"
    )

    def setUp(self):
        super().setUp()
        spec = self.store / ".cos" / self.UNIT / "spec.md"
        spec.write_text(spec.read_text(encoding="utf-8") + self.BLOCKS, encoding="utf-8")

    def authorities(self) -> dict[str, str]:
        unit = self.meta.snapshot(WS, NAMES)["units"][f"proj/{self.UNIT}"]
        return {str(a["n"]): a["authority"] for a in unit["answers"]}

    def test_the_import_reads_whose_answer_each_was(self):
        self.meta.import_store(WS, self.store)
        self.assertEqual(self.authorities(), {"1": "person", "2": "agent", "3": "delegated"})

    def test_answers_imported_before_are_classified_once(self):
        self.meta.import_store(WS, self.store)
        with self.data.write() as conn:
            conn.execute("UPDATE unit_answers SET authority = 'unknown'")
            conn.execute("DELETE FROM migrations WHERE key = ?", (self.meta.authority_key(WS),))
        self.assertEqual(set(self.authorities().values()), {"unknown"})
        self.assertIsNone(self.meta.import_store(WS, self.store))
        self.assertEqual(self.authorities(), {"1": "person", "2": "agent", "3": "delegated"})
        # Once: a row that says `unknown` afterwards is left as it is.
        with self.data.write() as conn:
            conn.execute("UPDATE unit_answers SET authority = 'unknown' WHERE ref = '1'")
        self.meta.import_store(WS, self.store)
        self.assertEqual(self.authorities()["1"], "unknown")


class TheImportReport(unittest.TestCase):
    """Through `Activity.settings`: what the import could not read, by workspace name."""

    def setUp(self):
        from coscc import units
        from coscc.config import Config
        from coscc.agent.sessions import Sessions
        from coscc.service import Service

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        self.cwd = str(tmp / "work" / "proj")
        Path(self.cwd).mkdir(parents=True)
        config = Config(
            workspaces=(self.cwd,), working_dir=str(tmp / "work"), data_dir=str(tmp / "data")
        )
        self.service = Service(config, Sessions(config))
        self.store = units.root(self.cwd, config.data_dir)

    def test_settings_lists_each_unreadable_field_by_workspace_name_and_not_a_failed_ingest(self):
        shutil.copytree(FIXTURE, self.store)
        asyncio.run(self.service.board(self.cwd))
        self.service.ws.unit_meta().ingest_failed(
            self.service.ws.key(self.cwd), "0013_open-question", "boom"
        )
        report = self.service.activity.settings()["import_report"]
        self.assertEqual(report["problem"], "")
        found = {(r["workspace"], r["unit"], r["artifact"], r["field"]) for r in report["rows"]}
        self.assertIn(("proj", "0016_bad-status", "spec.md", "status"), found)
        self.assertIn(("proj", "0015_no-status", "intent.md", "status"), found)
        self.assertNotIn("ingest", {r["field"] for r in report["rows"]})
        self.assertFalse(any("/" in r["workspace"] for r in report["rows"]))

    def test_a_store_read_cleanly_has_no_row(self):
        self.store.mkdir(parents=True)
        asyncio.run(self.service.answers.create_unit(self.cwd, "a-problem", "x"))
        asyncio.run(self.service.board(self.cwd))
        self.assertEqual(
            self.service.activity.settings()["import_report"], {"rows": [], "problem": ""}
        )

    def test_a_database_that_cannot_be_read_is_one_sentence_without_its_path(self):
        from coscc.data import Busy

        busy = Busy(self.service.config.data_dir + "/cos.db")
        with (
            mock.patch("coscc.units.meta.UnitMeta.unknowns", side_effect=busy),
            self.assertLogs("coscc", "WARNING") as log,
        ):
            report = self.service.activity.settings()["import_report"]
        self.assertEqual(report, {"rows": [], "problem": "The import report could not be read."})
        self.assertIs(log.records[-1].exc_info[1], busy)

    def test_an_import_that_fails_names_the_workspace_and_logs_the_error(self):
        from coscc.data import Busy
        from coscc.service.common import Invalid

        shutil.copytree(FIXTURE, self.store)
        busy = Busy(self.service.config.data_dir + "/cos.db")
        with (
            mock.patch("coscc.units.meta.UnitMeta.import_store", side_effect=busy),
            self.assertLogs("coscc", "WARNING") as log,
        ):
            with self.assertRaises(Invalid) as said:
                self.service.ws.snapshot(self.cwd)
        self.assertEqual(str(said.exception), "the units of proj could not be imported")
        self.assertIn("cos.db", log.output[-1])
        self.assertIn(self.service.ws.key(self.cwd), log.output[-1])


if __name__ == "__main__":
    unittest.main()
