"""The fixture store (`testdata/meta_store`) holds one unit of each kind the plan's step 1 names."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.agent import harness
from coscc.store.db import Data
from coscc.store.journal import Journal
from coscc.units.contracts import ContractError
from coscc.units.history import History
from coscc.units.meta import SOURCE, MetaError, UnitMeta

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "testdata" / "meta_store"
WS = "/w/proj"
NAMES = {"proj": WS}


REPO = HERE.parents[1]


def loop(*args: str, input: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """`python -m coscc.loop <args>` as the app runs it, for a test that reads the loop's own words."""
    return subprocess.run(
        [sys.executable, "-m", "coscc.loop", *args],
        input=input,
        capture_output=True,
        text=True,
        env=harness.child_env(),
        cwd=REPO,
        check=check,
    )


def status(store: Path, snapshot: dict) -> dict:
    done = loop(
        "--root", str(store), "--state", "-", "status", "--json", input=json.dumps(snapshot)
    )
    return json.loads(done.stdout)


def ingest(core, cwd: str, unit: str) -> None:
    """Test glue (plan Risk 4): a file a test wrote by hand reaches `cos.db` the way a finished
    step's does, through `Answers.ingest`."""
    done = asyncio.run(core.answers.ingest(cwd, unit, {"outcome": "done", "stage": "test"}))
    assert not done, done


def snapshot_of(root, peers=(), units_=None) -> dict:
    """Test glue (plan step 8): the snapshot the app would hand the loop for the store
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


def _link(meta, *args):
    """`UnitMeta.link` in a transaction of its own, as the press that opens a unit writes it."""
    with meta.data.write() as conn:
        meta.link(conn, *args)


class WithSnapshot:
    """Test glue (plan step 8): `coscc/units/board.py` as a test module sees it, each question
    to the loop handed `snapshot_of` its store when the test gave no `state` — what
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
            {"idea": None, "dependsOn": None},
        )
        self.assertNotIn("ideas", snap)
        self.assertEqual(
            [h["state"] for h in snap["units"]["proj/0011_paused-then-resumed"]["holds"]],
            ["paused", "active"],
        )
        rows = History(self.tmp / "work", self.data).transitions(WS, "0010_full-loop")
        self.assertEqual({r["source"] for r in rows}, {SOURCE})


class TheSnapshotDecides(Base):
    def test_a_snapshot_for_one_unit_carries_it_and_what_it_depends_on(self):
        self.meta.import_store(WS, self.store)
        _link(self.meta, WS, "0017_linked", "proj/ideas/0001_x.md", ["0010_full-loop"])
        snap = self.meta.snapshot(WS, NAMES, ["0017_linked"])
        self.assertEqual(sorted(snap["units"]), ["proj/0010_full-loop", "proj/0017_linked"])
        done = loop(
            "--root", str(self.store), "--state", "-", "next", "0017_linked", input=json.dumps(snap)
        )
        full = loop(
            "--root",
            str(self.store),
            "--state",
            "-",
            "next",
            "0017_linked",
            input=json.dumps(self.meta.snapshot(WS, NAMES)),
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
        _link(self.meta, WS, "0017_linked", "proj/ideas/0001_x.md", ["0010_full-loop"])
        self.relate("0017_linked", "0013_open-question")
        snap = self.meta.snapshot(WS, NAMES, ["0017_linked"])
        self.assertEqual(
            sorted(snap["units"]),
            ["proj/0010_full-loop", "proj/0013_open-question", "proj/0017_linked"],
        )
        gate = loop(
            "--root",
            str(self.store),
            "--state",
            "-",
            "gate",
            "0017_linked",
            "impl",
            "--json",
            input=json.dumps(snap),
            check=False,
        )
        self.assertIn("waiting-on", json.loads(gate.stdout)["reasons"])


class TheIdeasUnitsAreItsRows(Base):
    IDEA = "proj/ideas/0001_x.md"

    def test_the_units_of_an_idea_are_the_rows_that_name_it_in_every_workspace(self):
        self.meta.import_store(WS, self.store)
        _link(self.meta, WS, "0017_linked", self.IDEA, ["proj/0010_full-loop", "other/0001_a"])
        _link(self.meta, "/w/other", "0001_a", self.IDEA, [])
        _link(self.meta, WS, "0010_full-loop", "proj/ideas/0002_y.md", [])
        self.assertEqual(
            self.meta.idea_units(self.IDEA),
            [
                ("/w/other", "0001_a", []),
                (WS, "0017_linked", ["proj/0010_full-loop", "other/0001_a"]),
            ],
        )
        self.assertEqual(self.meta.idea_units("proj/ideas/0009_none.md"), [])

    def test_a_unit_linked_again_has_only_its_new_rows(self):
        _link(self.meta, WS, "0017_linked", self.IDEA, ["proj/0010_full-loop"])
        _link(self.meta, WS, "0017_linked", self.IDEA, [])
        self.assertEqual(self.meta.idea_units(self.IDEA), [(WS, "0017_linked", [])])
        self.assertEqual(self.count("unit_links"), 1)

    def test_the_snapshot_links_carry_the_idea_and_the_dependencies_and_no_repo(self):
        self.meta.import_store(WS, self.store)
        _link(self.meta, WS, "0017_linked", self.IDEA, ["proj/0010_full-loop"])
        snap = self.meta.snapshot(WS, NAMES)
        self.assertEqual(
            snap["units"]["proj/0017_linked"]["links"],
            {"idea": self.IDEA, "dependsOn": ["proj/0010_full-loop"]},
        )
        self.assertNotIn("ideas", snap)


class TheOutputsTheLoopReads(Base):
    UNIT = "0010_full-loop"

    def record(self, stage: str, obj: dict, version: int | None = None) -> None:
        self.meta.import_store(WS, self.store)
        submitted = {"run": "r", "revision": "h", "object": obj}
        with self.data.write() as conn:
            self.meta.record_result(conn, WS, self.UNIT, stage, f"{stage}.md", submitted)
            if version is not None:
                conn.execute("UPDATE outputs SET version = ? WHERE agent = ?", (version, stage))

    def artifact(self, name: str) -> dict:
        return self.meta.snapshot(WS, NAMES)["units"][f"proj/{self.UNIT}"]["artifacts"][name]

    def spike(self, *rounds: str) -> None:
        for verdict in rounds:
            verdicts = [{"id": "U1", "verdict": verdict}]
            self.record("spike", {"judgement": "ready", "verdicts": verdicts})

    def test_an_output_of_another_version_is_refused_when_read(self):
        self.record("spec", {"judgement": "ready", "questions": []}, version=2)
        with self.assertRaises(ContractError) as raised:
            self.meta.snapshot(WS, NAMES)
        self.assertEqual(raised.exception.code, "output-version")

    def test_the_loop_sees_only_the_fields_it_reads(self):
        self.record(
            "spec",
            {"stage": "spec", "judgement": "ready", "questions": [], "unmeasured": [], "extra": 1},
        )
        self.assertEqual(
            self.artifact("spec.md")["result"],
            {"judgement": "ready", "questions": [], "unmeasured": []},
        )

    def test_the_app_counts_the_spike_round(self):
        self.spike("fails", "holds")
        self.assertEqual(self.artifact("spike.md")["round"], 2)

    def test_a_spike_that_failed_only_last_is_still_round_one(self):
        self.spike("holds", "fails")
        self.assertEqual(self.artifact("spike.md")["round"], 1)

    def unit(self) -> dict:
        return self.meta.snapshot(WS, NAMES)["units"][f"proj/{self.UNIT}"]

    def test_the_intents_record_sets_the_type(self):
        self.meta.import_store(WS, self.store)
        self.assertEqual(self.unit()["type"], "feat")
        self.record("intent", {"stage": "intent", "judgement": "ready", "type": "refactor"})
        self.assertEqual(self.unit()["type"], "refactor")
        self.record("intent", {"stage": "intent", "judgement": "ready", "type": "docs"})
        self.assertEqual(self.unit()["type"], "docs")

    def test_a_markdown_type_never_overwrites_a_record(self):
        self.record("intent", {"stage": "intent", "judgement": "ready", "type": "docs"})
        intent = self.store / ".cos" / self.UNIT / "intent.md"
        intent.write_text(
            intent.read_text().replace("Type: feat", "Type: fix") + "\n## Answers\n\nQ1: x\n"
        )
        self.meta.ingest(WS, self.store, self.UNIT, actor="a", session="s", source="run:intent")
        self.assertEqual(self.unit()["type"], "docs")

    def test_the_snapshot_has_no_lane(self):
        self.meta.import_store(WS, self.store)
        self.assertNotIn("lane", self.unit())
        with self.data.connect() as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(unit_meta)")}
        self.assertNotIn("lane", cols)

    def test_the_unit_page_gets_the_latest_output_of_each_agent(self):
        self.record("spec", {"stage": "spec", "judgement": "draft", "questions": []})
        self.record("spec", {"stage": "spec", "judgement": "ready", "questions": []})
        self.record("intent", {"stage": "intent", "judgement": "ready"})
        got = self.meta.outputs(WS, self.UNIT)
        self.assertEqual([o["agent"] for o in got], ["spec", "intent"])
        self.assertEqual(got[0]["version"], 1)
        self.assertEqual(got[0]["fields"], {"judgement": "ready", "questions": []})


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
        rows = History(self.tmp / "work", self.data).transitions(
            WS, "0014_changes-requested", "review.md"
        )
        self.assertEqual(rows[-1]["to_state"], "changes-requested")
        self.assertEqual(
            (rows[-1]["actor"], rows[-1]["session"], rows[-1]["source"]),
            ("stage:review", "s1", "run:review"),
        )

    def test_impl_writing_plan_md_done_folds_plan_md_to_done(self):
        self.meta.import_store(WS, self.store)
        plan = self.store / ".cos" / "0014_changes-requested" / "plan.md"
        plan.write_text(plan.read_text().replace("Status: accepted", "Status: done"))
        self.ingest("0014_changes-requested")
        rows = History(self.tmp / "work", self.data).transitions(
            WS, "0014_changes-requested", "plan.md"
        )
        self.assertEqual(rows[-1]["to_state"], "done")

    def test_an_artifact_unchanged_since_the_last_ingest_is_not_read_again(self):
        self.meta.import_store(WS, self.store)
        before = self.count("transitions"), self.count("unit_questions")
        self.assertEqual(self.ingest("0013_open-question"), [])
        self.assertEqual((self.count("transitions"), self.count("unit_questions")), before)

    def test_ingest_writes_no_link(self):
        self.meta.import_store(WS, self.store)
        intent = self.store / ".cos" / "0017_linked" / "intent.md"
        intent.write_text(
            intent.read_text().replace(
                "Status:",
                "Idea: proj/ideas/0001_x.md. Repo: proj. Depends on: 0010_full-loop. Status:",
            )
        )
        self.ingest("0017_linked")
        self.assertEqual(self.count("unit_links"), 0)
        _link(self.meta, WS, "0017_linked", "proj/ideas/0001_x.md", ["0010_full-loop"])
        self.ingest("0017_linked")
        self.assertEqual(self.count("unit_links"), 2)

    def test_an_intent_its_run_submitted_leaves_the_type_to_the_record(self):
        self.meta.import_store(WS, self.store)
        intent = self.store / ".cos" / "0003_old-unit" / "intent.md"
        intent.write_text(intent.read_text() + "\nThêm một dòng.\n")
        unknowns = self.meta.ingest(
            WS,
            self.store,
            "0003_old-unit",
            actor="stage:intent",
            session="s1",
            source="run:intent",
            decided=("intent.md",),
        )
        self.assertEqual([u for u in unknowns if u["field"] == "type"], [])
        self.assertEqual([u for u in self.meta.unknowns([WS]) if u["field"] == "type"], [])

    def test_a_failed_ingest_is_a_problem_on_the_card(self):
        self.meta.import_store(WS, self.store)
        self.meta.ingest_failed(WS, "0013_open-question", "coscc.loop meta exited 2")
        unit = next(
            u
            for u in status(self.store, self.meta.snapshot(WS, NAMES))["units"]
            if u["name"] == "0013_open-question"
        )
        self.assertTrue(
            any("coscc.loop meta exited 2" in p for p in unit["problems"]), unit["problems"]
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
        _link(self.meta, WS, "0017_linked", "proj/ideas/0001_x.md", ["0010_full-loop"])
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

    # `shipped` is whether a merge was ever recorded, on either road: what moved after it does not undo it.
    def shipped(self, unit: str) -> bool:
        return self.meta.snapshot(WS, NAMES)["units"][f"proj/{unit}"]["shipped"]

    def test_a_merge_then_a_branch_named_is_shipped_and_not_merged(self):
        self.meta.import_store(WS, self.store)
        unit = "0013_open-question"
        self.assertFalse(self.shipped(unit))
        self.machine(unit, "ship.md", "accepted", "merged", "merge-read")
        self.machine(unit, "pr.md", "accepted", "open", "branch-named")
        self.assertTrue(self.shipped(unit))
        self.assertFalse(self.merged(unit))

    def test_a_ship_md_accepted_by_a_ship_session_is_shipped(self):
        self.meta.import_store(WS, self.store)
        unit = "0013_open-question"
        (self.store / ".cos" / unit / "ship.md").write_text("# Ship\nStatus: accepted.\n")
        self.meta.ingest(
            WS,
            self.store,
            unit,
            actor="stage:ship",
            session="s1",
            source="run:ship",
            wrote="ship.md",
        )
        self.assertTrue(self.shipped(unit))

    def test_a_unit_with_neither_is_not_shipped(self):
        self.meta.import_store(WS, self.store)
        unit = "0013_open-question"
        self.machine(unit, "pr.md", "accepted", "open", "branch-named")
        self.machine(unit, "ship.md", "draft", "merge-requested", "ship-ready")
        self.assertFalse(self.shipped(unit))


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
    precedent.` — and every other is a person's."""

    UNIT = "0013_open-question"
    BLOCKS = (
        "\n### Câu 2\nAnswered by: Jera. Date: 2026-09-24. Via: precedent.\n\nBao duyệt, như D1.\n"
        "\n### Câu 3\nAnswered by: Leif. Date: 2026-09-24. Via: product.\n\nKhông.\n"
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
        self.assertEqual(self.authorities(), {"1": "person", "2": "agent", "3": "person"})

    def test_answers_imported_before_are_classified_once(self):
        self.meta.import_store(WS, self.store)
        with self.data.write() as conn:
            conn.execute("UPDATE unit_answers SET authority = 'unknown'")
            conn.execute("DELETE FROM migrations WHERE key = ?", (self.meta.authority_key(WS),))
        self.assertEqual(set(self.authorities().values()), {"unknown"})
        self.assertIsNone(self.meta.import_store(WS, self.store))
        self.assertEqual(self.authorities(), {"1": "person", "2": "agent", "3": "person"})
        # Once: a row that says `unknown` afterwards is left as it is.
        with self.data.write() as conn:
            conn.execute("UPDATE unit_answers SET authority = 'unknown' WHERE ref = '1'")
        self.meta.import_store(WS, self.store)
        self.assertEqual(self.authorities()["1"], "unknown")


class TheImportReport(unittest.TestCase):
    """A store the import cannot read fails the snapshot with a sentence that names the workspace."""

    def setUp(self):
        from coscc import units
        from coscc.config import Config
        from coscc.agent.sessions import Sessions
        from coscc.http.app import Core

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        self.cwd = str(tmp / "work" / "proj")
        Path(self.cwd).mkdir(parents=True)
        config = Config(
            workspaces=(self.cwd,), working_dir=str(tmp / "work"), data_dir=str(tmp / "data")
        )
        self.core = Core(config, Sessions(config))
        self.store = units.root(self.cwd, config.data_dir)

    def test_an_import_that_fails_names_the_workspace_and_logs_the_error(self):
        from coscc.store.db import Busy
        from coscc.kernel import Invalid

        shutil.copytree(FIXTURE, self.store)
        busy = Busy(self.core.config.data_dir + "/cos.db")
        with (
            mock.patch("coscc.units.meta.UnitMeta.import_store", side_effect=busy),
            self.assertLogs("coscc", "WARNING") as log,
        ):
            with self.assertRaises(Invalid) as said:
                self.core.ws.snapshot(self.cwd)
        self.assertEqual(str(said.exception), "the units of proj could not be imported")
        self.assertIn("cos.db", log.output[-1])
        self.assertIn(self.core.ws.key(self.cwd), log.output[-1])


if __name__ == "__main__":
    unittest.main()
