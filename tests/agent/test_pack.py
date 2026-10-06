"""Every agent is one row of the built-in pack, the owner's layer laid over it.

The built-in pack resolves to exactly the tables it replaced (`fixtures/m4-rows.json`, dumped from
them before they went); `check` refuses each bad row by name; an owner's file changes only what it
sets, and a bad one refuses its agent's runs; `row_hash` follows every part; the old prefs move
into the owner's layer once."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.agent import agents, helpers, models, modeltrial, pack, policy
from coscc.kernel import Hooks
from coscc.runner.prompt import skill_for
from coscc.runner.reply import RunError
from coscc.runner.queue import Refused
from coscc.runner.steps import Steps
from coscc.store.db import Data
from coscc.units import contracts

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "m4-rows.json").read_text())
# The one line `_BESIDE`'s words became in the spec skill.
SPEC_LINE = (
    "  After a spike `fails`, drop that id and every requirement resting on it; if no direction "
    "holds, submit `not-ready` with the question.\n"
)
SPEC_ADDED = (
    "  When a `spike.md` is handed to you, rewrite the spec on its results; a new question takes "
    "a new `U<n>`.\n"
)
CATALOG = {n: t.effect for n, t in Hooks().catalog().items()} | {
    "vault": "external",
    "codegraph": "read",
}


def _today(key: str) -> dict:
    """What the engine reads of `key` now, in the fixture's shape."""
    row = policy.row_for(key)
    novel = policy.row_for_step(key, policy.NOVEL)
    model, _, effort, _ = models.resolve(key, None, None)
    nm, _, ne, _ = models.resolve(key, policy.NOVEL, None)
    changed = (nm, ne, novel.max_turns, novel.max_budget_usd) != (
        model,
        effort,
        row.max_turns,
        row.max_budget_usd,
    )
    found = pack.row(key) or {}
    decl = contracts.declarations().get(key)
    who = agents.agent_for(key) or {}
    try:
        skill = skill_for(key)
    except RunError:
        skill = None
    return {
        "glyph": who.get("glyph"),
        "name": who.get("name"),
        "meaning": who.get("meaning"),
        "role": who.get("role"),
        "model": model,
        "effort": effort,
        "novel": {"model": nm, "effort": ne, "turns": novel.max_turns, "usd": novel.max_budget_usd}
        if changed
        else None,
        "trial": sorted(modeltrial.arms(key).values()) or None,
        "turns": row.max_turns,
        "usd": row.max_budget_usd,
        "tools": list(row.tools),
        "warning": row.warning,
        "app_writes_artifact": row.app_writes_artifact,
        "prose": row.prose,
        "submits": row.submits,
        "input": contracts.input_of(key) if "input" in found else None,
        "output": {k: decl[k] for k in ("kind", "version", "fields")} if decl else None,
        "purpose": (decl or {}).get("purpose") or None,
        "skill": skill,
        "helpers": list(row.helpers) if policy.AGENT_TOOL in row.tools else [],
        "consequence": found.get("consequence"),
    }


class TheBuiltInPackIsTheTablesItReplaced(unittest.TestCase):
    def test_the_rows_are_the_twelve_agents(self):
        self.assertEqual(
            sorted(p.stem for p in (pack.BUILTIN / "agents").glob("*.md")), sorted(FIXTURE)
        )
        self.assertEqual(
            list(pack.rows()),
            "idea intent spec spike plan impl review integrate estimate leif scout worker".split(),
        )

    def test_every_row_resolves_to_the_fixture(self):
        for key, want in FIXTURE.items():
            with self.subTest(key=key):
                if "prompt" in want:
                    self.assertEqual(helpers.definitions((key,))[key], want)
                    continue
                got = _today(key)
                if want["name"] is None:
                    # No identity before: estimate and Leif had none.
                    for field in agents.FIELDS:
                        got[field] = want[field] = None
                if key == "spec":
                    want = {
                        **want,
                        "skill": want["skill"].replace(SPEC_LINE, SPEC_LINE + SPEC_ADDED),
                    }
                if key == "plan":
                    # M5: plan's label is `variant`, version 4.
                    fields = dict(want["output"]["fields"])
                    fields["variant"] = fields.pop("impl")
                    want = {
                        **want,
                        "output": {**want["output"], "version": 4, "fields": fields},
                        "skill": want["skill"].replace("- `impl`: `novel`", "- `variant`: `novel`"),
                    }
                self.assertEqual(got, want)

    def test_every_built_in_row_passes_check_with_the_catalog(self):
        rows = {k: r["builtin"] for k, r in pack.rows().items()}
        for key, row in rows.items():
            with self.subTest(key=key):
                self.assertEqual(pack.check(row, CATALOG, rows), [])


def _row(**over) -> dict:
    base = {k: v for k, v in pack.rows()["plan"]["builtin"].items()}
    return {**base, **over}


class CheckRefusesABadRowByName(unittest.TestCase):
    def reasons(self, row: dict) -> str:
        rows = {k: r["builtin"] for k, r in pack.rows().items()}
        return "\n".join(pack.check(row, CATALOG, rows))

    def test_an_unknown_key(self):
        self.assertIn("modle: no such key", self.reasons(_row(modle={"id": "x"})))

    def test_an_unknown_tool(self):
        self.assertIn(
            "tools.Telepathy: no such tool in the catalog",
            self.reasons(_row(tools={"Read": "allow", "Telepathy": "allow"})),
        )

    def test_a_write_tool_on_a_row_the_app_writes(self):
        self.assertIn(
            "holds only reading tools, not Write",
            self.reasons(_row(tools={"Read": "allow", "Write": "allow"})),
        )

    def test_an_engine_tool_named(self):
        self.assertIn(
            "tools.submit: the engine issues it",
            self.reasons(_row(tools={"submit": "allow"})),
        )

    def test_a_policy_that_is_none_of_the_three(self):
        self.assertIn(
            "tools.Read must be one of allow, ask, off", self.reasons(_row(tools={"Read": "yes"}))
        )

    def test_a_row_names_no_state_the_process_does(self):
        self.assertIn(
            'trigger is {"engine": <engine>}', self.reasons(_row(trigger={"state": "spec"}))
        )

    def test_a_name_another_agent_has(self):
        self.assertIn("name: kenaz is another agent's", self.reasons(_row(name="kenaz")))

    def test_ceilings_out_of_bounds(self):
        said = self.reasons(_row(ceilings={"turns": 0, "usd": 99}))
        self.assertIn("ceilings.turns must be a whole number", said)
        self.assertIn("ceilings.usd must be from", said)

    def test_an_effort_the_cli_does_not_take(self):
        self.assertIn(
            "model.effort must be one of",
            self.reasons(_row(model={"id": "claude-opus-5-5[1m]", "effort": "huge"})),
        )

    def test_a_helper_that_is_no_helper_row(self):
        self.assertIn("helpers: plan is no helper row", self.reasons(_row(helpers=["plan"])))

    def test_a_helper_holding_agent(self):
        worker = dict(pack.rows()["worker"]["builtin"])
        worker["tools"] = {**worker["tools"], "Agent": "allow"}
        self.assertIn("a helper holds no Agent", self.reasons(worker))

    def test_a_skill_that_is_not_there(self):
        self.assertIn(
            "skills: no skill write-nothing", self.reasons(_row(skills=["write-nothing"]))
        )

    def test_a_removed_required_output_field(self):
        output = json.loads(json.dumps(pack.rows()["plan"]["output"]))
        del output["fields"]["variant"]
        rows = {k: dict(r) for k, r in pack.rows().items()}
        rows["plan"]["output"] = output
        with self.assertRaises(contracts.ContractError) as caught:
            contracts.load(rows)
        self.assertEqual(caught.exception.code, "contract-field-missing")
        self.assertIn("plan.variant", caught.exception.words)

    def test_a_bad_built_in_pack_stops_the_load_with_every_reason(self):
        with tempfile.TemporaryDirectory() as d:
            copy = Path(d) / "coscc-sdlc"
            import shutil

            shutil.copytree(pack.BUILTIN, copy)
            row = copy / "agents" / "plan.md"
            row.write_text(row.read_text().replace('"Read": "allow"', '"Read": "maybe"'))
            with mock.patch.object(pack, "BUILTIN", copy), self.assertRaises(pack.PackError) as e:
                pack.rows()
        self.assertIn("plan: tools.Read must be one of", str(e.exception))


def _process(**states) -> dict:
    """`short` with `states` laid over its own: a state given `None` goes."""
    base = json.loads(json.dumps(pack.process("coscc-sdlc/short")))
    for k, v in states.items():
        if v is None:
            base["states"].pop(k)
        else:
            base["states"][k] = v
    return base


class AProcessIsCheckedAtLoad(unittest.TestCase):
    """`check_process`: one test per reason, on `short` changed in one place."""

    def reasons(self, process: dict) -> str:
        return "\n".join(pack.check_process("p", process, pack.builtin_rows()))

    def test_full_and_short_load_clean(self):
        self.assertEqual(list(pack.processes()), ["coscc-sdlc/full", "coscc-sdlc/short"])
        for ref, process in pack.processes().items():
            self.assertEqual(pack.check_process(ref, process, pack.builtin_rows()), [], ref)

    def test_a_way_on_to_no_state(self):
        bad = _process(pr={"action": "open-pr", "next": [{"to": "reveiw"}]})
        self.assertIn("p.pr.next[0]: 'reveiw' is no state", self.reasons(bad))

    def test_a_start_that_is_no_state(self):
        self.assertIn("p: start 'idea' is no state", self.reasons({**_process(), "start": "idea"}))

    def test_a_state_no_path_reaches(self):
        bad = _process(spec={"agent": "spec", "next": [{"to": "impl"}]})
        self.assertIn("p.spec: no path from start reaches it", self.reasons(bad))

    def test_no_path_to_the_end(self):
        bad = _process(ship={"action": "merge", "next": [{"to": "review"}]})
        self.assertIn("p: no path reaches the end", self.reasons(bad))

    def test_a_guard_not_listed(self):
        bad = _process(
            pr={"action": "open-pr", "next": [{"to": "review", "when": {"guard": "ci-green"}}]}
        )
        self.assertIn("p.pr.next[0]: no guard ci-green", self.reasons(bad))

    def test_a_field_not_in_the_output(self):
        impl = {
            **_process()["states"]["impl"],
            "next": [{"to": "pr", "when": {"field": "verdict", "is": "pass"}}],
        }
        self.assertIn(
            "p.impl.next[0]: verdict is no field of the agent's output",
            self.reasons(_process(impl=impl)),
        )

    def test_an_action_has_no_field_to_read(self):
        bad = _process(
            pr={
                "action": "open-pr",
                "next": [{"to": "review", "when": {"field": "judgement", "is": "ready"}}],
            }
        )
        self.assertIn(
            "p.pr.next[0]: an action has no output to read judgement from", self.reasons(bad)
        )

    def test_a_value_the_field_never_takes(self):
        impl = {
            **_process()["states"]["impl"],
            "next": [{"to": "pr", "when": {"field": "judgement", "is": "done"}}],
        }
        self.assertIn(
            "p.impl.next[0]: judgement is never 'done'", self.reasons(_process(impl=impl))
        )
        listed = {**impl, "next": [{"to": "pr", "when": {"field": "needs_person", "is": "ready"}}]}
        self.assertIn("(it may be non-empty, empty)", self.reasons(_process(impl=listed)))

    def test_an_input_missing_on_one_path(self):
        # A way from the start straight to impl, past intent: impl's `intent` is not there on it.
        bad = {**_process(), "start": "begin"}
        bad["states"] = {
            "begin": {"action": "open-pr", "next": [{"to": "intent"}, {"to": "impl"}]},
            **bad["states"],
        }
        self.assertIn(
            "p.impl: its input intent is not produced on every path to it", self.reasons(bad)
        )

    def test_agent_and_action_together(self):
        bad = _process(pr={"agent": "review", "action": "open-pr", "next": [{"to": "review"}]})
        self.assertIn("p.pr: a state names exactly one of agent or action", self.reasons(bad))

    def test_a_helper_runs_no_state_and_an_action_is_one_the_engine_has(self):
        self.assertIn(
            "p.impl: agent worker is no row that runs a state",
            self.reasons(_process(impl={"agent": "worker", "next": [{"to": "pr"}]})),
        )
        self.assertIn(
            "p.pr.action must be one of open-pr, merge",
            self.reasons(_process(pr={"action": "deploy", "next": [{"to": "review"}]})).replace(
                ": action", ".action"
            ),
        )

    def test_a_bad_built_in_process_stops_the_load_with_its_reasons(self):
        with tempfile.TemporaryDirectory() as d:
            import shutil

            copy = Path(d) / "coscc-sdlc"
            shutil.copytree(pack.BUILTIN, copy)
            path = copy / pack.PROCESS_FILE
            path.write_text(path.read_text().replace('"to": "review"}', '"to": "reveiw"}', 1))
            with (
                mock.patch.object(pack, "BUILTIN", copy),
                mock.patch.dict(pack._PROCESSES, clear=True),
                self.assertRaises(pack.PackError) as e,
            ):
                pack.processes()
        self.assertIn("full.pr.next[0]: 'reveiw' is no state", str(e.exception))

    def test_the_state_names_its_agent_and_the_hash_is_the_processes(self):
        self.assertEqual(pack.agent_for("coscc-sdlc/short", "impl"), "impl")
        self.assertIsNone(pack.agent_for("coscc-sdlc/short", "ship"))
        self.assertIsNone(pack.agent_for("coscc-sdlc/short", "spec"))
        self.assertEqual(pack.states_of("impl"), "impl in full, short")
        self.assertEqual(pack.states_of("spec"), "spec in full")
        self.assertEqual(len(pack.process_hash("coscc-sdlc/full")), 12)
        self.assertNotEqual(
            pack.process_hash("coscc-sdlc/full"), pack.process_hash("coscc-sdlc/short")
        )
        stamp = pack.stamp("impl", "coscc-sdlc/short")
        self.assertEqual(
            (stamp["process"], stamp["process_hash"]),
            ("coscc-sdlc/short", pack.process_hash("coscc-sdlc/short")),
        )


class TheOwnersLayer(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        patcher = mock.patch.object(pack, "ROOT", self.d.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.agents = pack.owner_dir() / "agents"

    def own(self, key: str, text: str) -> None:
        self.agents.mkdir(parents=True, exist_ok=True)
        (self.agents / f"{key}.md").write_text(text)

    def test_one_key_replaces_the_built_ins_and_the_rest_stays(self):
        self.own("intent", '---\nmodel: {"id": "claude-haiku-4-5", "effort": "low"}\n---\n')
        row = pack.row("intent")
        self.assertEqual(row["model"], {"id": "claude-haiku-4-5", "effort": "low"})
        self.assertEqual(row["tools"], pack.row("intent")["builtin"]["tools"])
        self.assertEqual(row["edited"], ["model"])
        self.assertEqual(
            models.resolve("intent", None, None),
            ("claude-haiku-4-5", models.OVERRIDE, "low", models.OVERRIDE),
        )
        self.assertEqual(pack.stamp("intent")["edited"], ["model"])

    def test_a_body_replaces_the_body(self):
        self.own("intent", "---\n---\nBegin your reply with MARKER.\n")
        self.assertEqual(pack.row("intent")[pack.BODY], "Begin your reply with MARKER.")
        self.assertEqual(agents.agent_for("intent")["role"], "Begin your reply with MARKER.")

    def test_a_bad_owner_file_is_not_skipped_it_refuses_the_agent(self):
        self.own("intent", '---\ntools: {"Read": "allow", "Bash": "allow"}\n---\n')
        self.assertEqual(pack.problems("intent"), [])
        said = pack.problems("intent", CATALOG)
        self.assertTrue(any("not Bash" in p for p in said), said)
        steps = mock.Mock(hooks=Hooks())
        with self.assertRaises(Refused) as caught:
            Steps.refuse_unready(steps, "intent", Path(self.d.name), None)
        self.assertEqual(caught.exception.reasons, ("agent-invalid",))

    def test_a_file_that_does_not_parse_names_its_line(self):
        self.own("plan", "---\nmodel: not json\n---\n")
        self.assertIn("line 2: model is not one line of JSON", pack.problems("plan")[0])

    def test_write_keeps_only_what_differs_and_reset_deletes_the_file(self):
        old, new = pack.write("plan", "ceilings", {"turns": 60, "usd": 4.0})
        self.assertEqual((old, new), ({"turns": 40, "usd": 4.0}, {"turns": 60, "usd": 4.0}))
        self.assertEqual(pack.owner_fields("plan"), ({"ceilings": {"turns": 60, "usd": 4.0}}, ""))
        self.assertEqual(policy.row_for("plan").max_turns, 60)
        pack.write("plan", "ceilings", None)
        self.assertFalse((self.agents / "plan.md").exists())
        self.assertEqual(policy.row_for("plan").max_turns, 40)

    def test_a_write_that_fails_check_writes_nothing(self):
        with self.assertRaises(ValueError):
            pack.write("plan", "name", "Kenaz")
        self.assertFalse((self.agents / "plan.md").exists())

    def test_the_hash_moves_with_a_field_the_body_and_a_skill(self):
        seen = {pack.hash_of(pack.row("spec"))}
        pack.write("spec", "ceilings", {"turns": 41, "usd": 4.0})
        seen.add(pack.hash_of(pack.row("spec")))
        pack.write("spec", "body", "Another role.")
        seen.add(pack.hash_of(pack.row("spec")))
        skill = pack.owner_dir() / "skills" / "write-spec" / pack.SKILL_FILE
        skill.parent.mkdir(parents=True)
        skill.write_text("# my rules\n")
        seen.add(pack.hash_of(pack.row("spec")))
        self.assertEqual(len(seen), 4)
        self.assertIn("skill:write-spec", pack.row("spec")["edited"])
        self.assertEqual(skill_for("spec"), "# my rules\n")


class TheOldPrefsMoveOnce(unittest.TestCase):
    PREFS = {
        "model:impl": "claude-opus-5-5[1m]",
        "effort:intent": "low",
        "turns:impl:novel": 300,
        "budget:spec": 2.5,
        "model:chat": "claude-haiku-4-5",
        "agent:plan": {"name": "Rad", "role": "Plans."},
        "model:nothing": "x",
    }

    def test_every_override_lands_in_the_owners_layer_and_the_prefs_go(self):
        with tempfile.TemporaryDirectory() as d:
            data = Data(d)
            data.version()
            for k, v in self.PREFS.items():
                data.set_pref(k, v)
            data.set_pref("autopilot", True)
            with sqlite3.connect(data.db_path) as conn:
                conn.execute("PRAGMA user_version=14")
            data.version()
            with mock.patch.object(pack, "ROOT", d):
                self.assertEqual(
                    models.resolve("impl", None, None)[:2], ("claude-opus-5-5[1m]", models.OVERRIDE)
                )
                # Gebo ran impl's model when it had none of its own, and keeps it.
                self.assertEqual(models.resolve("integrate", None, None)[0], "claude-opus-5-5[1m]")
                self.assertEqual(models.resolve("intent", None, None)[2:], ("low", models.OVERRIDE))
                self.assertEqual(models.ceilings("impl", policy.NOVEL)["max_turns"], 300)
                self.assertEqual(models.ceilings("impl", None)["max_turns"], 120)
                self.assertEqual(models.ceilings("spec", None)["max_budget_usd"], 2.5)
                self.assertEqual(models.resolve("leif", None, None)[0], "claude-haiku-4-5")
                self.assertEqual(agents.agent_for("plan")["name"], "Rad")
                self.assertEqual(agents.agent_for("plan")["role"], "Plans.")
                self.assertEqual(pack.stray(), [])
            self.assertEqual(data.prefs(), {"autopilot": True})
