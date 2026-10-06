"""The fixture store (`testdata/meta_store`) holds one unit of each kind the plan's step 1 names;
`seed` states each unit's state as rows, which is where the app keeps it."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from unittest import mock

from coscc.agent import harness
from coscc.store.db import Data
from coscc.store.journal import Journal
from coscc.units.contracts import ContractError
from coscc.units.meta import UnitMeta

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


def seed(
    meta: UnitMeta,
    ws: str,
    unit: str,
    statuses: Mapping[str, str] | None = None,
    type: str | None = None,
    shipped: bool = False,
    questions: Mapping[str, Sequence[str]] | None = None,
) -> None:
    """Test glue: a unit in the state a test names, as rows. `statuses` is `{artifact: state}`,
    each one transition (source `test`); `type` is an intent record, as `record_result` writes
    it; `shipped` is the merge row the PR machine writes; `questions` is `{artifact: [text]}`,
    numbered from 1. A file the test writes beside it is prose."""
    with meta.data.write() as conn:
        meta.add_unit(conn, ws, unit)
        if type is not None:
            submitted = {
                "run": "r",
                "revision": "h",
                "object": {"judgement": "ready", "type": type},
            }
            meta.record_result(conn, ws, unit, "intent", "intent.md", submitted)
        for artifact, asked in (questions or {}).items():
            conn.executemany(
                "INSERT INTO unit_questions (root, workspace, unit, artifact, n, text) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(meta.root, ws, unit, artifact, n, text) for n, text in enumerate(asked, 1)],
            )
    for artifact, state in (statuses or {}).items():
        meta.history.record(ws, unit, artifact, state, actor="test", session="test", source="test")
    if shipped:
        meta.history.record(
            ws,
            unit,
            "ship.md",
            "accepted",
            source="prmachine:merged",
            guard="merge-read",
            authority="code",
        )


def _link(meta, *args):
    """`UnitMeta.link` in a transaction of its own, as the press that opens a unit writes it."""
    with meta.data.write() as conn:
        meta.link(conn, *args)


class WithSnapshot:
    """Test glue: `coscc/units/board.py` as a test module sees it, each question to the loop
    handed `state()` when the test gave no `state` — what `Workspaces.snapshot` hands it in the
    app. Every other attribute, and every patch a test sets on it, is the module's own."""

    ASKS = ("read", "gate", "next_step", "pr_text", "rerun", "screens")

    def __init__(self, module, state: Callable[[], dict]):
        object.__setattr__(self, "_module", module)
        object.__setattr__(self, "_state", state)

    def __getattr__(self, name):
        found = getattr(self._module, name)
        if name not in self.ASKS:
            return found

        def asked(units_root, *args, **kwargs):
            if kwargs.get("state") is None:
                kwargs["state"] = self._state()
            return found(units_root, *args, **kwargs)

        return asked

    def __setattr__(self, name, value):
        setattr(self._module, name, value)

    def __delattr__(self, name):
        delattr(self._module, name)


# The fixture store's units as the app holds them: what each file's header says, as rows.
UNITS = {
    "0003_old-unit": dict(statuses={"intent.md": "accepted", "spec.md": "skipped"}),
    "0010_full-loop": dict(
        statuses={
            "intent.md": "accepted",
            "spec.md": "accepted",
            "plan.md": "accepted",
            "impl.md": "accepted",
            "pr.md": "accepted",
        },
        type="feat",
        shipped=True,
    ),
    "0011_paused-then-resumed": dict(statuses={"intent.md": "accepted"}, type="fix"),
    "0012_dropped": dict(statuses={"intent.md": "accepted"}, type="chore"),
    "0013_open-question": dict(
        statuses={"intent.md": "accepted", "spec.md": "draft"},
        type="feat",
        questions={"spec.md": ["Ngưỡng là bao nhiêu?", "Ai duyệt?"]},
    ),
    "0014_changes-requested": dict(
        statuses={
            "intent.md": "accepted",
            "spec.md": "accepted",
            "plan.md": "accepted",
            "impl.md": "accepted",
            "pr.md": "accepted",
            "review.md": "changes-requested",
        },
        type="fix",
    ),
    "0015_no-status": dict(type="fix"),
    "0016_bad-status": dict(statuses={"intent.md": "accepted"}, type="fix"),
    "0017_linked": dict(statuses={"intent.md": "accepted"}, type="feat"),
    "not_a-unit": dict(statuses={"intent.md": "draft"}, type="fix"),
}


def seed_fixture(meta: UnitMeta, ws: str) -> None:
    """The fixture store's units, with the answers and holds its files carry."""
    for unit, kw in UNITS.items():
        seed(meta, ws, unit, **kw)
    meta.add_answer(
        ws, "0013_open-question", "spec.md", 1, "Năm giây.", "Bao", "2026-09-23", "product"
    )
    meta.add_answer(
        ws, "0014_changes-requested", "review.md", "F1", "Đã chạy.", "Bao", "2026-09-24", "product"
    )
    meta.add_hold(
        ws,
        "0011_paused-then-resumed",
        "paused",
        "Chờ bản sửa khác.",
        "Leif",
        "2026-09-20",
        "product",
    )
    meta.add_hold(
        ws, "0011_paused-then-resumed", "active", "Bản sửa đã vào.", "Leif", "2026-09-21", "product"
    )
    meta.add_hold(ws, "0012_dropped", "dropped", "Không cần nữa.", "Leif", "2026-09-22", "product")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.store = self.tmp / "store"
        shutil.copytree(FIXTURE, self.store)
        self.data = Data(self.tmp / "data")
        self.meta = UnitMeta(self.tmp / "work", self.data)
        seed_fixture(self.meta, WS)

    def count(self, table: str) -> int:
        with self.data.connect() as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class TheSnapshotIsTheRowsOnly(Base):
    def test_a_file_that_says_otherwise_changes_nothing(self):
        before = self.meta.snapshot(WS, NAMES)
        unit = self.store / ".cos" / "0013_open-question"
        (unit / "spec.md").write_text(
            "# Spec: x\nStatus: rejected.\n\n## Open questions\n\n1. Is this read?\n",
            encoding="utf-8",
        )
        (unit / "intent.md").write_text(
            "# Intent: x\nAuthor: t. Type: docs. Status: rejected.\n", encoding="utf-8"
        )
        self.assertEqual(self.meta.snapshot(WS, NAMES), before)
        spec = before["units"]["proj/0013_open-question"]["artifacts"]["spec.md"]
        self.assertEqual(spec["status"], "draft")
        self.assertEqual([q["n"] for q in spec["questions"]], [1, 2])
        self.assertEqual(before["units"]["proj/0013_open-question"]["type"], "feat")

    def test_the_snapshot_of_the_seeded_store_carries_each_artifacts_status(self):
        snap = self.meta.snapshot(WS, NAMES)
        self.assertEqual(snap["workspace"], "proj")
        full = snap["units"]["proj/0010_full-loop"]["artifacts"]
        self.assertEqual(full["plan.md"]["status"], "accepted")
        self.assertTrue(snap["units"]["proj/0010_full-loop"]["merged"])
        self.assertEqual(
            snap["units"]["proj/0014_changes-requested"]["artifacts"]["review.md"]["status"],
            "changes-requested",
        )
        self.assertIsNone(snap["units"]["proj/0015_no-status"]["artifacts"]["intent.md"]["status"])
        self.assertEqual(
            snap["units"]["proj/0017_linked"]["links"], {"idea": None, "dependsOn": None}
        )
        self.assertNotIn("ideas", snap)
        self.assertEqual(
            [h["state"] for h in snap["units"]["proj/0011_paused-then-resumed"]["holds"]],
            ["paused", "active"],
        )
        for artifact in full.values():
            self.assertNotIn("raw", artifact)

    def test_no_table_holds_what_a_file_said(self):
        with self.data.connect() as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(unit_unknowns)")}
        self.assertNotIn("unit_seen", tables)
        self.assertNotIn("raw", cols)


class AnArtifactWithARecordHasNoQuestionsYet(Base):
    UNIT = "0017_linked"

    def artifact(self, name: str) -> dict:
        return self.meta.snapshot(WS, NAMES)["units"][f"proj/{self.UNIT}"]["artifacts"][name]

    def test_a_record_without_questions_says_there_are_none(self):
        with self.data.write() as conn:
            self.meta.record_result(
                conn,
                WS,
                self.UNIT,
                "spec",
                "spec.md",
                {"run": "r", "revision": "h", "object": {"judgement": "ready", "questions": []}},
            )
        self.assertEqual(self.artifact("spec.md")["questions"], [])

    def test_a_review_with_a_round_says_there_are_none(self):
        obj = {"verdict": "pass", "findings": [], "screens": []}
        with self.data.write() as conn:
            self.meta.record_round(
                conn, WS, self.UNIT, {"n": 1, "run": "r", "head": "h", "object": obj}
            )
        self.assertEqual(self.artifact("review.md")["questions"], [])

    def test_an_artifact_with_a_state_and_no_rows_asks_none_and_one_with_neither_is_unknown(self):
        self.meta.history.record(WS, self.UNIT, "plan.md", "accepted", source="test")
        self.assertEqual(self.artifact("plan.md")["questions"], [])
        self.assertNotIn(
            "spec.md", self.meta.snapshot(WS, NAMES)["units"][f"proj/{self.UNIT}"]["artifacts"]
        )


class TheSnapshotDecides(Base):
    def test_a_snapshot_for_one_unit_carries_it_and_what_it_depends_on(self):
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
        self.assertEqual(self.unit()["type"], "feat")
        self.record("intent", {"stage": "intent", "judgement": "ready", "type": "refactor"})
        self.assertEqual(self.unit()["type"], "refactor")
        self.record("intent", {"stage": "intent", "judgement": "ready", "type": "docs"})
        self.assertEqual(self.unit()["type"], "docs")

    def test_the_snapshot_has_no_lane(self):
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
        spec = got[0]
        self.assertEqual(spec["version"], 1)
        self.assertEqual(spec["fields"], {"judgement": "ready", "questions": []})


class TheIngestAppliesTheRecordOnly(unittest.TestCase):
    """`Answers.ingest` writes what a run submitted and reads no file."""

    UNIT = "0001_a"

    def setUp(self):
        from coscc.agent.sessions import Sessions
        from coscc.config import Config
        from coscc.http.app import Core

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.cwd = str(root / "work" / "proj")
        Path(self.cwd).mkdir(parents=True)
        config = Config(
            workspaces=(self.cwd,), working_dir=str(root / "work"), data_dir=str(root / "data")
        )
        self.core = Core(config, Sessions(config))
        self.meta = self.core.ws.unit_meta()
        self.key = self.core.ws.key(self.cwd)
        seed(self.meta, self.key, self.UNIT, {"intent.md": "accepted"}, type="feat")

    def ingest(self, done: dict, wrote: str | None = "spec.md") -> dict:
        return asyncio.run(self.core.answers.ingest(self.cwd, self.UNIT, done, wrote))

    def rows(self) -> list:
        return self.meta.history.transitions(self.key, self.UNIT, "spec.md")

    def test_a_done_run_with_a_record_has_its_transition(self):
        submitted = {
            "run": "r",
            "open_run": "r",
            "revision": "h",
            "computed_revision": "h",
            "object": {"stage": "spec", "judgement": "ready", "questions": [], "unmeasured": []},
        }
        done = {"outcome": "done", "stage": "spec", "session_id": "s", "submitted": submitted}
        self.assertEqual(self.ingest(done), {})
        [row] = self.rows()
        self.assertEqual(
            (row["to_state"], row["guard"], row["source"]), ("accepted", "stage-result", "run:spec")
        )

    def test_a_run_without_a_record_writes_nothing_and_opens_no_file(self):
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("a file was read")):
            self.assertEqual(self.ingest({"outcome": "done", "stage": "spec"}), {})
            self.assertEqual(self.ingest({"outcome": "exhausted", "stage": "spec"}), {})
        self.assertEqual(self.rows(), [])

    def test_a_failed_write_is_a_problem_on_the_card(self):
        done = {"outcome": "done", "stage": "spec", "submitted": {"object": {"judgement": "ready"}}}
        with mock.patch(
            "coscc.units.transitions.apply", side_effect=__import__("sqlite3").OperationalError("x")
        ):
            self.assertIn("ingest_error", self.ingest(done))
        unit = self.core.ws.meta_of(self.cwd, self.UNIT)
        self.assertEqual([u["field"] for u in unit["unknowns"]], ["ingest"])


class TheIngest(Base):
    def test_a_failed_ingest_is_a_problem_on_the_card(self):
        self.meta.ingest_failed(WS, "0013_open-question", "the database could not be written")
        unit = next(
            u
            for u in status(self.store, self.meta.snapshot(WS, NAMES))["units"]
            if u["name"] == "0013_open-question"
        )
        self.assertTrue(
            any("the database could not be written" in p for p in unit["problems"]),
            unit["problems"],
        )


class AMergeIsARow(Base):
    """`merged`, which a `Depends on:` is judged on, is the PR machine's row, or a ship recorded
    before the machine; never the status of `ship.md` alone."""

    def merged(self, unit: str) -> bool:
        return self.meta.snapshot(WS, NAMES)["units"][f"proj/{unit}"]["merged"]

    def machine(self, unit: str, artifact: str, to_state: str, transition: str, guard: str) -> None:
        self.meta.history.record(
            WS,
            unit,
            artifact,
            to_state,
            source=f"prmachine:{transition}",
            guard=guard,
            authority="code",
        )

    def test_a_shipped_unit_is_merged_and_nothing_else_is(self):
        self.assertTrue(self.merged("0010_full-loop"))
        self.assertFalse(self.merged("0013_open-question"))

    def test_an_accepted_ship_md_of_a_step_is_not_merged(self):
        self.meta.history.record(
            WS, "0013_open-question", "ship.md", "accepted", actor="stage:impl", source="run:impl"
        )
        self.assertFalse(self.merged("0013_open-question"))

    def test_the_machines_merged_row_is_merged_and_its_other_rows_are_not(self):
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
        unit = "0013_open-question"
        self.assertFalse(self.shipped(unit))
        self.machine(unit, "ship.md", "accepted", "merged", "merge-read")
        self.machine(unit, "pr.md", "accepted", "open", "branch-named")
        self.assertTrue(self.shipped(unit))
        self.assertFalse(self.merged(unit))

    def test_a_ship_md_accepted_by_a_ship_session_is_shipped(self):
        unit = "0013_open-question"
        self.meta.history.record(
            WS, unit, "ship.md", "accepted", actor="stage:ship", session="s1", source="run:ship"
        )
        self.assertTrue(self.shipped(unit))

    def test_a_unit_with_neither_is_not_shipped(self):
        unit = "0013_open-question"
        self.machine(unit, "pr.md", "accepted", "open", "branch-named")
        self.machine(unit, "ship.md", "draft", "merge-requested", "ship-ready")
        self.assertFalse(self.shipped(unit))


class AnswersAndHolds(Base):
    def test_an_answer_and_a_hold_reach_the_snapshot_in_order(self):
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


class TheSnapshotReport(unittest.TestCase):
    """A database that cannot be read fails the snapshot with a sentence that names the workspace."""

    def setUp(self):
        from coscc import units
        from coscc.agent.sessions import Sessions
        from coscc.config import Config
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

    def test_a_read_that_fails_names_the_workspace_and_logs_the_error(self):
        from coscc.kernel import Invalid
        from coscc.store.db import Busy

        busy = Busy(self.core.config.data_dir + "/cos.db")
        with (
            mock.patch("coscc.units.meta.UnitMeta.snapshot", side_effect=busy),
            self.assertLogs("coscc", "WARNING") as log,
        ):
            with self.assertRaises(Invalid) as said:
                self.core.ws.snapshot(self.cwd)
        self.assertEqual(str(said.exception), "the units of proj could not be read")
        self.assertIn("cos.db", log.output[-1])


if __name__ == "__main__":
    unittest.main()
