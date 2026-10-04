"""`coscc/features/decisions.py`: the table, the run-log rows beside it, who may reverse whom, the
tool, the block and the routes."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import httpx

from coscc import auth, features, plugin, units
from coscc.api import build
from coscc.bus import Bus
from coscc.config import Config
from coscc.data import Data
from coscc.features import decisions
from coscc.hooks import Facts
from coscc.runlog.journal import Journal
from coscc.service.common import Invalid

UNIT = "0001_thing"
TODAY = date.today().isoformat()


class Bed(unittest.TestCase):
    """A data folder with the table, a run log over it and the `Ctx` a feature gets."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.ws = root / "work" / "proj"
        self.ws.mkdir(parents=True)
        self.key = str(self.ws.resolve())
        self.data = Data(root / "data")
        self.journal = Journal(root / "work", self.data)
        self.ctx = plugin.Ctx(
            self.journal_or_none, self.workspace_key, lambda *_: True, Bus(), self.data
        )
        plugin.create_tables(self.ctx, plugin.tables_of([decisions.PLUGIN]))
        self.book = decisions.Book(self.ctx)
        self.has_journal = True

    def journal_or_none(self) -> Journal | None:
        return self.journal if self.has_journal else None

    def workspace_key(self, cwd: str) -> str:
        if Path(cwd).resolve() != self.ws.resolve():
            raise Invalid(f"not a workspace: {cwd}")
        return self.key

    def write(self, authority, text="we use sqlite", by="owner", unit=UNIT, **more):
        return self.book.record(self.key, unit, text, authority, by, **more)

    def logged(self, kind: str):
        return self.journal.records(self.key, UNIT, kind=kind)

    def table(self) -> list[tuple]:
        with self.data.connect() as conn:
            rows = conn.execute("SELECT id, authority, withdrawn FROM unit_decisions ORDER BY id")
            return [tuple(r) for r in rows.fetchall()]

    def delegate(self, **more) -> str:
        fields = {"kind": "delegation", "text": "t", "agent": "Leif", "from_day": "2000-01-01"}
        return f"D{self.data.decision_add(**{**fields, **more})}"


class EveryDecisionHasANumberThatIsNeverReusedAndNoRowIsDeleted(Bed):
    def test_numbers_count_up_across_units_and_authorities(self):
        ids = [
            self.write("person")["id"],
            self.write("agent", unit="0002_other")["id"],
            self.write("person")["id"],
        ]
        self.assertEqual(ids, ["C1", "C2", "C3"])

    def test_a_reversed_decision_stays_with_the_day_it_was_withdrawn_and_its_number_is_kept(self):
        first = self.write("agent")
        second = self.write("person", reverses=first["id"])
        self.assertEqual(self.table(), [(1, "agent", TODAY), (2, "person", "")])
        self.assertEqual(self.write("person")["id"], "C3")
        self.assertEqual(second["reverses"], "C1")

    def test_the_withdrawn_day_is_set_once(self):
        first = self.write("agent")
        self.write("person", reverses=first["id"])
        with self.data.write() as conn:
            conn.execute("UPDATE unit_decisions SET withdrawn = '2001-01-01' WHERE id = 1")
        with self.assertRaises(Invalid):
            self.write("person", reverses=first["id"])
        self.assertEqual(self.table()[0], (1, "agent", "2001-01-01"))

    def test_a_number_another_writer_took_first_is_tried_again_once(self):
        taken = iter([1, 1])
        self.write("person")
        with mock.patch.object(self.book, "_next", side_effect=lambda: next(taken)):
            with self.assertRaises(Invalid) as e:
                self.write("person")
        self.assertIn("same moment", str(e.exception))
        self.assertEqual(len(self.table()), 1)
        self.assertEqual(len(self.logged("decision")), 1)

    def test_text_that_is_empty_or_over_the_limit_or_a_bad_unit_writes_nothing(self):
        for text in ("", "   ", None, "x" * (decisions.TEXT_MAX + 1)):
            with self.assertRaises(Invalid):
                self.write("person", text=text)
        with self.assertRaises(Invalid):
            self.write("person", unit="../x")
        self.assertEqual(self.table(), [])
        self.write("person", text="x" * decisions.TEXT_MAX)


class ADelegationIsCheckedAsAnAnswersIs(Bed):
    def test_a_delegation_in_force_for_the_agent_it_names_is_accepted(self):
        d = self.delegate()
        self.assertEqual(self.book.delegation_or_refuse(self.key, "Leif (CoS)", d), d)

    def test_a_delegation_that_is_not_one_that_does_not_cover_or_is_over_is_refused(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        other = units.slot(str(self.ws.parent / "elsewhere"))
        refused = {
            "D": "named D<n>",
            "D999": "no decision D999",
            self.delegate(kind="decision"): "not a delegation",
            self.delegate(workspace=other): "does not cover",
            self.delegate(until_day=yesterday): "not in force",
            self.delegate(from_day=tomorrow): "not in force",
            self.delegate(agent="Mara"): "delegates to Mara",
        }
        for cited, why in refused.items():
            with self.assertRaises(Invalid, msg=cited) as e:
                self.book.delegation_or_refuse(self.key, "Leif", cited)
            self.assertIn(why, str(e.exception))

    def test_a_name_that_only_begins_like_the_agent_is_refused(self):
        d = self.delegate()
        with self.assertRaises(Invalid):
            self.book.delegation_or_refuse(self.key, "Leifson", d)


class NobodyOverridesAStrongerAuthority(Bed):
    def refused(self, authority, target):
        before = (self.table(), len(self.journal.records()))
        with self.assertRaises(Invalid) as e:
            self.write(authority, reverses=target["id"])
        self.assertEqual((self.table(), len(self.journal.records())), before)
        return str(e.exception)

    def test_an_agent_cannot_reverse_a_person_or_a_delegated_decision(self):
        for authority in ("person", "delegated"):
            target = self.write(authority)
            self.assertIn(authority, self.refused("agent", target))

    def test_a_delegated_decision_cannot_reverse_a_person_s(self):
        self.assertIn("person", self.refused("delegated", self.write("person")))

    def test_a_delegated_decision_reverses_agent_and_delegated_ones(self):
        for authority in ("agent", "delegated"):
            target = self.write(authority)
            self.assertEqual(
                self.write("delegated", reverses=target["id"])["reverses"], target["id"]
            )

    def test_an_agent_reverses_an_agent_decision(self):
        target = self.write("agent")
        self.write("agent", reverses=target["id"])
        self.assertEqual(self.table()[0][2], TODAY)

    def test_a_person_reverses_any_authority(self):
        targets = []
        for authority in decisions.AUTHORITIES:
            target = self.write(authority)
            self.write("person", reverses=target["id"])
            targets.append(int(target["id"][1:]))
        withdrawn = {row[0] for row in self.table() if row[2]}
        self.assertEqual(withdrawn, set(targets))

    def test_a_decision_of_another_unit_one_that_is_unknown_or_one_already_withdrawn_is_refused(
        self,
    ):
        theirs = self.write("agent", unit="0002_other")
        gone = self.write("agent")
        self.write("person", reverses=gone["id"])
        for target in (theirs["id"], "C99", gone["id"]):
            with self.assertRaises(Invalid, msg=target):
                self.write("person", reverses=target)
        with self.assertRaises(Invalid):
            self.write("person", reverses="first")
        self.assertEqual(self.table()[0][2], "")


class EveryWriteLeavesTheRunLogRowsInTheSameTransaction(Bed):
    def test_a_decision_leaves_one_row_the_kernel_reads(self):
        d = self.write("person", by="Ana", delegation="")
        (row,) = self.logged("decision")
        self.assertEqual(
            {
                k: row[k]
                for k in ("workspace", "unit", "id", "authority", "reverses", "by", "delegation")
            },
            {
                "workspace": self.key,
                "unit": UNIT,
                "id": d["id"],
                "authority": "person",
                "reverses": "",
                "by": "Ana",
                "delegation": "",
            },
        )
        self.assertEqual(self.logged("decision-withdrawn"), [])

    def test_a_delegated_decision_names_its_delegation_in_the_row(self):
        self.write("delegated", by="Leif", delegation="D7")
        self.assertEqual(self.logged("decision")[0]["delegation"], "D7")

    def test_a_reversal_leaves_a_decision_row_and_a_withdrawn_row_for_the_one_reversed(self):
        first = self.write("agent")
        second = self.write("person", by="Ana", reverses=first["id"])
        (withdrawn,) = self.logged("decision-withdrawn")
        self.assertEqual(
            {k: withdrawn[k] for k in ("workspace", "unit", "id", "by", "reversed_by")},
            {
                "workspace": self.key,
                "unit": UNIT,
                "id": "C1",
                "by": "Ana",
                "reversed_by": second["id"],
            },
        )
        self.assertEqual(self.logged("decision")[1]["reverses"], "C1")

    def test_the_kernel_reads_what_is_written(self):
        from coscc.service import outdated

        first = self.write("agent")
        self.write("person")
        self.write("person", reverses=first["id"])
        live = outdated.live_decisions(self.journal.records(self.key, UNIT))
        self.assertEqual(
            [(d["id"], d["authority"]) for d in live], [("C2", "person"), ("C3", "person")]
        )

    def test_a_refusal_after_the_withdrawal_leaves_neither_the_row_nor_the_records(self):
        person = self.write("person")
        before = (self.table(), self.journal.records())
        with self.assertRaises(Invalid):
            self.write("agent", reverses=person["id"])
        self.assertEqual((self.table(), self.journal.records()), before)

    def test_a_run_log_that_fails_after_the_insert_takes_the_row_and_the_withdrawal_with_it(self):
        first = self.write("agent")
        before = (self.table(), self.journal.records())
        with mock.patch.object(Journal, "_insert", side_effect=RuntimeError("disk")):
            with self.assertRaises(RuntimeError):
                self.write("person", reverses=first["id"])
        self.assertEqual((self.table(), self.journal.records()), before)

    def test_without_a_run_log_nothing_is_written(self):
        self.has_journal = False
        with self.assertRaises(Invalid) as e:
            self.write("person")
        self.assertIn("run log", str(e.exception))
        self.assertEqual(self.table(), [])


class TheBlockListsTheLiveDecisionsOfSpecAndPlan(Bed):
    def facts(self, stage: str, unit: str = UNIT) -> Facts:
        return mock.Mock(spec=Facts, stage=stage, unit=unit, workspace_key=self.key)

    def test_it_is_empty_outside_spec_and_plan(self):
        self.write("person")
        for stage in ("idea", "intent", "spike", "impl", "review", "ship"):
            self.assertEqual(decisions.render(self.book, self.facts(stage)), "", stage)

    def test_it_is_empty_for_a_unit_with_no_live_decision(self):
        self.assertEqual(decisions.render(self.book, self.facts("spec")), "")
        first = self.write("agent")
        self.write("person", reverses=first["id"], text="Reverses C1.")
        self.write("person", unit="0002_other")
        self.assertIn("C2", decisions.render(self.book, self.facts("spec")))
        with self.data.write() as conn:
            conn.execute("UPDATE unit_decisions SET withdrawn = '2001-01-01'")
        self.assertEqual(decisions.render(self.book, self.facts("plan")), "")

    def test_it_lists_every_live_decision_with_its_authority_and_the_instruction(self):
        first = self.write("agent", text="use sqlite")
        self.write("person", text="no, postgres\nbecause of scale", reverses=first["id"])
        self.write("delegated", by="Leif", delegation="D1", text="keep the API")
        for stage in ("spec", "plan"):
            text = decisions.render(self.book, self.facts(stage))
            self.assertTrue(text.startswith("# Decisions that came after"))
            self.assertNotIn("C1 (agent)", text)
            self.assertIn("- C2 (person): no, postgres\n  because of scale", text)
            self.assertIn("- C3 (delegated): keep the API", text)
            self.assertIn("`person` over `delegated` over `agent`", text)
            self.assertIn("`## Open questions`", text)
            self.assertIn("numbered question `N.`", text)


class TheAgentsToolWritesAnAgentDecision(Bed, unittest.IsolatedAsyncioTestCase):
    def facts(self, stage="impl") -> Facts:
        return mock.Mock(spec=Facts, stage=stage, unit=UNIT, workspace_key=self.key)

    async def call(self, **args):
        (record,) = decisions.build_tools(self.book, self.facts())
        got = await record.handler(args)
        return json.loads(got["content"][0]["text"]), bool(got.get("is_error"))

    def test_it_is_offered_to_the_stages_that_can_carry_a_tool_and_named_by_its_server(self):
        assert decisions.PLUGIN.agent is not None
        parts = decisions.PLUGIN.agent(self.ctx)
        (tool,) = parts.tools
        self.assertEqual((tool.server, tool.names), ("decisions", ("record_decision",)))
        self.assertEqual(tool.stages, ("spike", "impl"))
        self.assertEqual([b.name for b in parts.blocks], ["decisions"])
        self.assertEqual(parts.guards, ())
        plugin.hooks_of([decisions.PLUGIN], self.ctx)

    async def test_what_it_writes_is_an_agent_decision_of_the_runs_unit(self):
        got, refused = await self.call(text="use sqlite")
        self.assertEqual(
            (got, refused), ({"result": "recorded", "id": "C1", "authority": "agent"}, False)
        )
        (row,) = self.logged("decision")
        self.assertEqual(
            (row["authority"], row["by"], row["delegation"]), ("agent", "agent:impl", "")
        )

    async def test_no_argument_makes_it_anything_else(self):
        await self.call(text="x", authority="person", delegation="D1", by="owner")
        self.assertEqual({d["authority"] for d in self.book.rows(self.key, UNIT)}, {"agent"})

    async def test_it_reverses_an_agent_decision_and_is_refused_on_a_persons(self):
        agents, persons = self.write("agent"), self.write("person")
        got, refused = await self.call(text="again", reverses=persons["id"])
        self.assertTrue(refused)
        self.assertIn("person", got["reason"])
        got, refused = await self.call(text="again", reverses=agents["id"])
        self.assertFalse(refused)
        self.assertEqual(len(self.logged("decision-withdrawn")), 1)

    async def test_a_bad_argument_comes_back_as_one_refusal(self):
        for args in ({}, {"text": ""}, {"text": "x", "reverses": "nine"}, {"text": 3}):
            got, refused = await self.call(**args)
            self.assertTrue(refused, args)
            self.assertEqual(got["result"], "refused")
        self.assertEqual(self.table(), [])


class Routes(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.ws = root / "work" / "proj"
        self.ws.mkdir(parents=True)
        self.config = Config(
            workspaces=(str(self.ws),), working_dir=str(root / "work"), data_dir=str(root / "data")
        )
        self.app = build(self.config)
        self.data = Data(self.config.data_dir)
        with self.data.write() as conn:
            conn.execute(decisions.TABLE)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )
        self.addAsyncCleanup(self.client.aclose)
        self.key = str(self.ws.resolve())

    async def post(self, path: str, **sent) -> httpx.Response:
        return await self.client.post(path, json={"cwd": str(self.ws), "unit": UNIT, **sent})

    def records(self, kind: str):
        return Journal(self.config.working_dir, self.data).records(self.key, UNIT, kind=kind)

    async def test_a_decision_from_the_route_is_a_person_s_and_leaves_its_row(self):
        r = await self.post("/api/decisions", text="use sqlite")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            (r.json()["id"], r.json()["authority"], r.json()["by"]), ("C1", "person", "owner")
        )
        self.assertEqual(self.records("decision")[0]["authority"], "person")

    async def test_a_sent_authority_is_ignored(self):
        r = await self.post("/api/decisions", text="x", authority="agent")
        self.assertEqual(r.json()["authority"], "person")

    async def test_with_a_delegation_in_force_it_is_delegated_and_names_it(self):
        d = self.data.decision_add(kind="delegation", text="t", agent="Leif", from_day="2000-01-01")
        r = await self.post("/api/decisions", text="x", delegation=f"D{d}", by="Leif")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["authority"], r.json()["delegation"]), ("delegated", f"D{d}"))
        self.assertEqual(self.records("decision")[0]["delegation"], f"D{d}")

    async def test_a_bad_delegation_is_a_400_and_writes_nothing(self):
        for cited in ("D404", "nope", "D1"):
            r = await self.post("/api/decisions", text="x", delegation=cited, by="Leif")
            self.assertEqual(r.status_code, 400, cited)
            self.assertIn("error", r.json())
        self.assertEqual(self.records("decision"), [])

    async def test_bad_input_is_a_400(self):
        cases = [
            {"text": ""},
            {"text": "x" * (decisions.TEXT_MAX + 1)},
            {"text": "x", "reverses": "C9"},
            {"text": "x", "unit": ""},
            {"text": "x", "cwd": "/etc"},
        ]
        for sent in cases:
            r = await self.post("/api/decisions", **sent)
            self.assertEqual(r.status_code, 400, sent)
        self.assertEqual(self.records("decision"), [])

    async def test_reversing_an_agent_decision_writes_a_person_row_that_reverses_it(self):
        with self.data.write() as conn:
            conn.execute(
                "INSERT INTO unit_decisions (workspace, unit, text, authority, recorded_by, date) "
                "VALUES (?, ?, 'guess', 'agent', 'agent:impl', ?)",
                (self.key, UNIT, TODAY),
            )
        r = await self.post("/api/decisions/reverse", id="C1")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            (r.json()["authority"], r.json()["reverses"], r.json()["text"]),
            ("person", "C1", "Reverses C1."),
        )
        (gone,) = self.records("decision-withdrawn")
        self.assertEqual((gone["id"], gone["reversed_by"]), ("C1", "C2"))
        again = await self.post("/api/decisions/reverse", id="C1")
        self.assertEqual(again.status_code, 400)

    async def test_a_reverse_without_an_id_or_with_an_unknown_one_is_a_400(self):
        self.assertEqual((await self.post("/api/decisions/reverse")).status_code, 400)
        self.assertEqual((await self.post("/api/decisions/reverse", id="C5")).status_code, 400)

    async def test_the_list_shows_withdrawn_decisions_too(self):
        await self.post("/api/decisions", text="one")
        await self.post("/api/decisions/reverse", id="C1", text="no")
        r = await self.client.get("/api/decisions", params={"cwd": str(self.ws), "unit": UNIT})
        self.assertEqual(r.status_code, 200)
        got = r.json()
        self.assertEqual([d["id"] for d in got["decisions"]], ["C1", "C2"])
        self.assertEqual(got["decisions"][0]["withdrawn"], TODAY)
        bad = await self.client.get("/api/decisions", params={"cwd": "/etc", "unit": UNIT})
        self.assertEqual(bad.status_code, 400)

    async def test_the_routes_are_behind_the_login(self):
        paths = {r.path for r in self.app.routes}
        for path in ("/api/decisions", "/api/decisions/reverse"):
            self.assertIn(path, paths)
            self.assertNotIn(path, {p for _, p in auth.EXEMPT})


class TheUnitScreenShowsThemWithAReverseButton(unittest.TestCase):
    def test_the_script_draws_into_the_unit_slot_without_innerhtml(self):
        (script,) = decisions.PLUGIN.scripts
        self.assertIn('window.coscc.slot("slot-unit"', script)
        self.assertNotIn("innerHTML", script)
        self.assertIn("inferred by an agent", script)
        self.assertIn("/api/decisions/reverse", script)
        self.assertIn('d.authority === "agent"', script)
        self.assertIn('b.textContent = "Reverse"', script)

    def test_a_withdrawn_decision_is_dimmed_and_has_no_button(self):
        (script,) = decisions.PLUGIN.scripts
        self.assertIn("if (d.withdrawn) p.style.opacity", script)
        self.assertIn("} else if (d.authority", script)


class ThePlugin(unittest.TestCase):
    def test_it_is_carried_with_its_table_a_short_summary_and_its_doc(self):
        self.assertIn(decisions.PLUGIN, features.FEATURES)
        self.assertEqual(decisions.PLUGIN.tables, (decisions.TABLE,))
        self.assertTrue(0 < len(decisions.PLUGIN.summary) <= 100)
        doc = Path(features.__file__).parent / "decisions.md"
        self.assertIn("## What the agent sees", doc.read_text())


if __name__ == "__main__":
    unittest.main()
