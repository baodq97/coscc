"""Tests for `Agents` in `coscc/leif/agents.py`."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from coscc import kernel
from coscc.config import Config
from coscc.agent import models, pack, policy
from coscc.runner import run as run_mod
from coscc.units import contracts
from coscc.store.db import Data
from coscc.http.app import Core
from coscc.kernel import Invalid
from coscc.agent.sessions import Sessions

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _at(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")


def _end(stage: str, outcome: str, days_ago: float, cost=None, turns=None, unit="0001_u"):
    record = {"v": 1, "kind": "end", "workspace": "w", "unit": unit, "stage": stage}
    record.update(outcome=outcome, at=_at(days_ago))
    if cost is not None:
        record["cost_usd"] = cost
    if turns is not None:
        record["turns"] = turns
    return record


class _WithAService(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        config = Config(workspaces=(), working_dir=str(root / "work"), data_dir=str(root / "data"))
        (root / "work").mkdir()
        self.core = Core(config, Sessions(config))
        self.data = Data(config.data_dir)
        self.journal = self.core.ws.journal()

    def _seed(self, records):
        """Write `records` as they are, `at` included, the way `Journal.append` stores them."""
        self.journal.records()
        with self.data.write() as conn:
            conn.executemany(
                "INSERT INTO runs (root, workspace, unit, stage, kind, at, record) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        self.journal._root,
                        r["workspace"],
                        r["unit"],
                        r["stage"],
                        r["kind"],
                        r["at"],
                        json.dumps(r),
                    )
                    for r in records
                ],
            )

    def _settings(self):
        return [
            (r["agent"], r["field"], r["old"], r["new"], r["by"])
            for r in self.journal.records(kind="agent-setting")
        ]

    def _row(self, page, key):
        return next(r for r in page["rows"] if r["key"] == key)


class AFieldIsCheckedSavedAndLogged(_WithAService):
    def test_a_value_out_of_bounds_is_refused_and_nothing_is_written(self):
        wrong = [
            ("spec", "ceilings", {"turns": 0}),
            ("spec", "ceilings", {"turns": 501}),
            ("spec", "ceilings", {"turns": 2.5}),
            ("spec", "ceilings", {"usd": 0.09}),
            ("spec", "ceilings", {"usd": 50.01}),
            ("spec", "model", {"id": "x" * 101}),
            ("spec", "model", {"id": "m", "effort": "turbo"}),
            ("spec", "model", "m"),
            ("chat", "model", {"id": "m"}),
            ("spec", "variants", {"routine": {}}),
            ("deploy", "model", {"id": "m"}),
            ("spec", "tools", ["Bash"]),
            ("spec", "tools", {"Read": "maybe"}),
            ("spec", "tools", {"peers": "allow"}),
            ("scout", "tools", {"Agent": "allow"}),
            ("impl", "helpers", ["spec"]),
            ("spec", "skills", ["write-nothing"]),
            ("spec", "input", {"artifacts": ["intent"]}),
            ("spec", "input", {**pack.row("spec")["input"], "data": ["gossip"]}),
            ("spec", "output", {**pack.row("spec")["output"], "fields": {}}),
            ("spec", "trigger", {"state": "plan"}),
            ("spec", "body", 3),
            ("spec", "skill:write-plan", "x"),
            ("spec", "colour", "red"),
            ("", "model", {"id": "m"}),
            (None, "model", {"id": "m"}),
        ]
        for key, field, value in wrong:
            with self.assertRaises(Invalid, msg=(key, field, value)):
                self.core.agents.set_agent_field(key, field, value)
        self.assertEqual(pack.owner_fields("spec"), ({}, ""))
        self.assertEqual(self._settings(), [])

    def test_an_empty_value_puts_the_builtin_back(self):
        self.core.agents.set_agent_field("spec", "body", "Another role.")
        self.core.agents.set_agent_field("spec", "body", "")
        self.assertEqual(pack.owner_fields("spec"), ({}, ""))

    def test_identity_fields_keep_their_rules(self):
        agents = self.core.agents
        agents.set_agent_field("review", "name", "Judge")
        self.assertEqual(agents.agent("review")["name"], "Judge")
        for key, field, value in (
            ("review", "name", "Two words"),
            ("review", "glyph", "abc"),
            ("review", "description", "m" * 201),
            # Another row's name, whatever its case.
            ("spec", "name", "judge"),
            ("review", "name", "GEBO"),
            ("deploy", "name", "Nobody"),
        ):
            with self.assertRaises(Invalid, msg=(key, field, value)):
                agents.set_agent_field(key, field, value)
        agents.set_agent_field("impl", "name", "Tiwaz")
        # Resetting `review`'s name would bring `Tiwaz` back to it.
        with self.assertRaises(Invalid):
            agents.set_agent_field("review", "name", None)
        agents.set_agent_field("impl", "name", None)
        agents.set_agent_field("review", "name", None)
        self.assertEqual(agents.agent("review")["name"], "Tiwaz")
        self.assertEqual(pack.owner_fields("review")[0], {})
        # Each record carries the value in force before and after, the built-in's included.
        self.assertEqual(
            self._settings(),
            [
                ("review", "name", "Tiwaz", "Judge", "owner"),
                ("impl", "name", "Uruz", "Tiwaz", "owner"),
                ("impl", "name", "Tiwaz", "Uruz", "owner"),
                ("review", "name", "Judge", "Tiwaz", "owner"),
            ],
        )

    def test_an_unreadable_store_writes_nothing(self):
        self.core.agents.set_agent_field("review", "name", "Judge")
        with mock.patch.object(pack, "write", side_effect=OSError("read-only")):
            for field, value in (("body", "Reads it all."), ("ceilings", {"turns": 10})):
                with self.assertRaises(Invalid):
                    self.core.agents.set_agent_field("review", field, value)
        self.assertEqual(pack.owner_fields("review")[0], {"name": "Judge"})
        self.assertEqual(len(self._settings()), 1)

    def test_a_catalog_names_the_tools_a_row_may_hold(self):
        catalog = kernel.Hooks(parts=(("f", kernel.Parts(tools=(_TOOL,))),))
        self.core.agents.hooks = lambda: catalog
        self.core.agents.set_agent_field("spec", "tools", {"Read": "allow", "probe": "ask"})
        with self.assertRaises(Invalid) as said:
            self.core.agents.set_agent_field("spec", "tools", {"Read": "allow", "nope": "allow"})
        self.assertIn("nope: no such tool in the catalog", str(said.exception))
        page = self.core.agents.agent_page(now=NOW)
        [probe] = [t for t in page["catalog"] if t["name"] == "probe"]
        self.assertEqual((probe["feature"], probe["effect"], probe["on"]), ("f", "read", True))


def _probe_server(_facts):
    return {"type": "sdk", "name": "probe", "instance": None}


_TOOL = kernel.Tool("probe", "read", "low", server="probe", names=("look",), make=_probe_server)


class EveryPartReachesTheNextRun(_WithAService):
    """Per part: saved on the page, the row has it, and what the next run is given has it."""

    def _grant(self, key):
        return run_mod.issue(policy.row_for(key), self.core.sessions, cwd=self._tmp.name)

    def test_model_effort_and_ceilings(self):
        self.core.agents.set_agent_field("spec", "model", {"id": "m-1", "effort": "low"})
        self.core.agents.set_agent_field("spec", "ceilings", {"turns": 6, "usd": 0.15})
        self.assertEqual(models.resolve("spec", None, None), ("m-1", "override", "low", "override"))
        row = policy.row_for_step("spec", None)
        self.assertEqual((row.max_turns, row.max_budget_usd), (6, 0.15))

    def test_a_tool_off_leaves_the_grant_and_one_on_ask_is_refused(self):
        tools = {**pack.row("intent")["tools"], "Grep": "off", "Glob": "ask"}
        self.core.agents.set_agent_field("intent", "tools", tools)
        grant = self._grant("intent")
        self.assertNotIn("Grep", grant.tools)
        self.assertIn("Glob", grant.tools)
        self.assertEqual(grant.asks, ("Glob",))
        reason = policy.critical(grant, "Glob", {"pattern": "*.py"}, None)
        self.assertTrue(reason.startswith(policy.ASKS), reason)
        self.assertEqual(policy.lacked(reason), "asks-a-person")
        self.assertEqual(policy.critical(grant, "Read", {"file_path": "a"}, None), "")

    def test_a_write_tool_on_ask_opens_no_place_to_write(self):
        tools = {**pack.row("impl")["tools"], "Write": "ask", "Edit": "ask", "NotebookEdit": "ask"}
        tools.pop("vault"), tools.pop("codegraph")
        self.core.agents.set_agent_field("impl", "tools", tools)
        self.assertEqual(self._grant("impl").write, ())

    def test_input_output_body_and_skill_text(self):
        before = pack.hash_of(pack.row("spec"))
        given = {**pack.row("spec")["input"], "data": []}
        self.core.agents.set_agent_field("spec", "input", given)
        self.assertEqual(contracts.input_of("spec")["data"], [])
        output = dict(pack.row("spec")["output"])
        output["fields"] = {**output["fields"], "note?": "text"}
        self.core.agents.set_agent_field("spec", "output", output)
        self.assertIn("note", json.dumps(contracts.schema("spec")))
        self.core.agents.set_agent_field("spec", "body", "Begin with MARKER-M4.")
        agent = self.core.models.agent("spec", policy.row_for("spec"))
        self.assertEqual(agent.system, "Begin with MARKER-M4.")
        self.core.agents.set_agent_field("spec", "skill:write-spec", "# Write a spec\n\nShort.")
        self.assertEqual(pack.skill("write-spec"), "# Write a spec\n\nShort.\n")
        row = self._row(self.core.agents.agent_page(now=NOW), "spec")
        self.assertNotEqual(row["row_hash"], before)
        self.assertEqual(sorted(row["edited"]), ["body", "input", "output", "skill:write-spec"])
        self.assertTrue(row["skills"][0]["edited"])
        for field in ("input", "output", "body", "skill:write-spec"):
            self.core.agents.set_agent_field("spec", field, None)
        row = self._row(self.core.agents.agent_page(now=NOW), "spec")
        self.assertEqual((row["edited"], row["row_hash"]), ([], before))

    def test_the_trigger_is_shown_and_not_saved(self):
        row = self._row(self.core.agents.agent_page(now=NOW), "intent")
        self.assertEqual(row["row"]["trigger"], {"state": "intent in full, short"})
        with self.assertRaises(Invalid):
            self.core.agents.set_agent_field("intent", "trigger", {"state": "spec"})


class ThePage(_WithAService):
    def test_a_row_has_no_command_list_and_no_stage_defaults_to_haiku(self):
        page = self.core.agents.agent_page(now=NOW)
        for row in page["rows"]:
            self.assertNotIn("commands", row["row"], row["key"])
        self.assertNotIn("haiku", json.dumps(page).lower())

    def test_every_row_is_listed_in_its_group(self):
        page = self.core.agents.agent_page(now=NOW)
        by = {r["key"]: r["group"] for r in page["rows"]}
        self.assertEqual(
            sorted(k for k, g in by.items() if g == "engine"), ["estimate", "integrate", "leif"]
        )
        self.assertEqual(sorted(k for k, g in by.items() if g == "helper"), ["scout", "worker"])
        self.assertEqual(by["impl"], "stage")

    def test_runs_of_thirty_days_grouped_by_their_definition(self):
        def start(days_ago, row_hash):
            return {
                "v": 1,
                "kind": "start",
                "workspace": "w",
                "unit": "0001_u",
                "stage": "spec",
            } | {
                "at": _at(days_ago),
                "row_hash": row_hash,
            }

        seed = []
        for d, h in ((40, "a"), (20, "a"), (10, "a"), (5, "b"), (3, "b")):
            seed += [start(d, h), _end("spec", "done", d, cost=0.5, turns=7)]
        setting = {"v": 1, "kind": "agent-setting", "workspace": "", "unit": "", "stage": ""}
        setting |= {"at": _at(6), "agent": "spec", "field": "body", "old": "x", "new": "y"}
        seed += [setting, _end("impl", "done", 2, cost=1.0), _end("impl", "failed", 1, cost=0.25)]
        self._seed(sorted(seed, key=lambda r: r["at"]))
        page = self.core.agents.agent_page(now=NOW)
        spec = self._row(page, "spec")
        self.assertEqual([g["row_hash"] for g in spec["groups"]], ["b", "a"])
        newest, older = spec["groups"]
        self.assertEqual([s["field"] for s in newest["settings"]], ["body"])
        self.assertEqual([r["at"] for r in newest["runs"]], [_at(3), _at(5)])
        self.assertEqual((newest["cost_usd"], newest["turns"]), (1.0, 14))
        self.assertEqual(len(older["runs"]), 2)
        # A run names its workspace, so a page over every workspace can tell whose unit it is.
        self.assertEqual(newest["runs"][0]["workspace"], "w")
        self.assertEqual((spec["last"]["outcome"], spec["last"]["turns"]), ("done", 7))
        self.assertEqual((spec["runs_30d"], spec["cost_30d"]), (4, 2.0))
        self.assertEqual(spec["chip"], "ok")
        impl = self._row(page, "impl")
        self.assertEqual((impl["chip"], impl["cost_30d"]), ("failed", 1.25))

    def test_a_setting_since_the_last_run_heads_a_group_of_no_run(self):
        self.core.agents.set_agent_field("spec", "body", "New.")
        [group] = self._row(self.core.agents.agent_page(), "spec")["groups"]
        self.assertEqual((group["runs"], group["settings"][0]["new"]), ([], "New."))

    def test_a_bad_owner_file_shows_its_problem(self):
        path = pack.owner_dir() / "agents" / "spec.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('---\nmodel: "nope"\n---\n', encoding="utf-8")
        row = self._row(self.core.agents.agent_page(now=NOW), "spec")
        self.assertTrue(row["problems"])
        self.assertEqual(row["row"]["model"], pack.row("spec")["builtin"]["model"])

    def test_a_hand_written_owner_file_of_any_shape_is_a_problem_never_a_crash(self):
        from coscc.runner.queue import Refused
        from coscc.runner.steps import Steps

        bad = [
            "skills: 5",
            'trigger: "x"',
            "skills: [5]",
            'tools: ["Read"]',
            'output: "x"',
            "helpers: 3",
            'ceilings: {"turns": "abc"}',
        ]
        path = pack.owner_dir() / "agents" / "spec.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        for line in bad:
            with self.subTest(line=line):
                path.write_text(f"---\n{line}\n---\n", encoding="utf-8")
                page = self.core.agents.agent_page(now=NOW)
                self.assertTrue(self._row(page, "spec")["problems"])
                self.assertEqual(self._row(page, "plan")["problems"], [])
                self.assertEqual(pack.row("spec")["model"], pack.row("spec")["builtin"]["model"])
                self.core.agents.set_agent_field("plan", "ceilings", {"turns": 70})
                self.core.agents.set_agent_field("plan", "ceilings", None)
                steps = mock.Mock(hooks=kernel.Hooks())
                with self.assertRaises(Refused) as caught:
                    Steps.refuse_unready(steps, "spec", Path(self._tmp.name), None)
                self.assertEqual(caught.exception.reasons, ("agent-invalid",))

    def test_the_page_says_whose_runs_it_adds_up(self):
        self.assertEqual(self.core.agents.agent_page(now=NOW)["scope"], "all")
        scoped = self.core.agents.agent_page(self.core.ws.key("w"), now=NOW, cwd="w")
        self.assertEqual(scoped["scope"], "workspace")

    def test_a_workspace_page_counts_only_that_workspaces_runs(self):
        self._seed(
            [
                _end("spec", "done", 1, cost=1.0),
                {**_end("spec", "done", 1, cost=2.0), "workspace": "other"},
            ]
        )
        both = self._row(self.core.agents.agent_page(now=NOW), "spec")
        mine = self._row(self.core.agents.agent_page("w", now=NOW), "spec")
        self.assertEqual((both["runs_30d"], mine["runs_30d"], mine["cost_30d"]), (2, 1, 1.0))

    def test_a_novel_run_is_costly_against_its_own_ceiling(self):
        def start(label, days_ago):
            return {
                "v": 1,
                "kind": "start",
                "workspace": "w",
                "unit": "0001_u",
                "stage": "impl",
            } | {
                "at": _at(days_ago),
                "label": label,
            }

        # $7 is 88 % of the plain $8 but 44 % of the novel $16.
        self._seed([start("novel", 2), _end("impl", "done", 2, cost=7.0)])
        self.assertEqual(self._row(self.core.agents.agent_page(now=NOW), "impl")["chip"], "ok")
        self._seed([start("routine", 1), _end("impl", "done", 1, cost=7.0)])
        self.assertEqual(self._row(self.core.agents.agent_page(now=NOW), "impl")["chip"], "costly")


if __name__ == "__main__":
    unittest.main()


class TheCatalogBlockIsWhatARowIsComposedFrom(_WithAService):
    def test_it_holds_the_tools_kinds_triggers_guards_rows_and_processes(self):
        said = json.loads(self.core.agents.catalog_block())
        self.assertIn("catalog", said["data"])
        self.assertEqual(
            said["outputs"]["proposal"]["proposals"], contracts.READS["proposal"]["proposals"][1]
        )
        self.assertNotIn("draft", said["outputs"])
        self.assertNotIn("engine", said["triggers"])
        self.assertEqual(said["guards"], list(pack.PROCESS_GUARDS))
        self.assertIn("unit.shipped", said["events"])
        scan = next(r for r in said["rows"] if r["key"] == "scan")
        self.assertEqual(scan["output"]["kind"], "proposal")
        self.assertNotIn("body", scan)
        self.assertIn(pack.DEFAULT_PROCESS, said["processes"])
        self.assertIn("write-intent", said["skills"])
