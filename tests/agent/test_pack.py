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
from coscc.units import contracts, states

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
    def test_the_rows_are_the_twelve_agents_the_scan_the_grader_and_dagaz(self):
        # The scan, the outcome grader and Dagaz replaced no table: they have no fixture.
        self.assertEqual(
            sorted(p.stem for p in (pack.BUILTIN / "agents").glob("*.md")),
            sorted([*FIXTURE, "scan", "outcome", "dagaz"]),
        )
        self.assertEqual(
            list(pack.rows()),
            "idea intent spec spike plan impl review integrate estimate leif scan outcome dagaz scout worker".split(),
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

    def test_a_session_writer_on_an_output_that_is_not_an_artifact(self):
        out = dict(_row()["output"])
        self.assertIn(
            "output.by session",
            self.reasons(_row(output={**out, "kind": "review", "by": "session"})),
        )

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
            "trigger.state: no such trigger", self.reasons(_row(trigger={"state": "spec"}))
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

    def test_a_row_that_writes_files_holds_bash_to_commit_them(self):
        said = self.reasons(_row(tools={"Read": "allow", "Edit": "allow"}))
        self.assertIn("a row holding Edit holds Bash too", said)
        with_bash = self.reasons(_row(tools={"Read": "allow", "Edit": "allow", "Bash": "allow"}))
        self.assertFalse([r for r in with_bash if "holds Bash too" in r])

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
        bad = _process(spec={"agent": "spec", "rerun": ["answers"], "next": [{"to": "impl"}]})
        self.assertIn("p.spec: no path from start reaches it", self.reasons(bad))

    def test_a_state_whose_agent_asks_goes_on_from_answers(self):
        bad = _process(intent={"agent": "intent", "next": [{"to": "impl"}]})
        self.assertIn("p.intent: tick 'go on after an answer'", self.reasons(bad))

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

    def test_a_held_scope_walks_the_packs_once_and_an_edit_shows_once_it_ends(self):
        haiku = '---\nmodel: {"id": "claude-haiku-4-5", "effort": "low"}\n---\n'
        self.own("intent", haiku)
        with mock.patch.object(pack, "_stamp", wraps=pack._stamp) as walked:
            with pack.held():
                for _ in range(50):
                    pack.row("intent"), pack.processes()
                self.own("intent", haiku.replace("haiku-4-5", "sonnet-5-5"))
                self.assertEqual(pack.row("intent")["model"]["id"], "claude-haiku-4-5")
            self.assertEqual(walked.call_count, 1)
            self.assertEqual(pack.row("intent")["model"]["id"], "claude-sonnet-5-5")
            pack.row("intent")
            self.assertEqual(walked.call_count, 3)

    def test_a_bad_row_of_the_owners_refuses_its_runs_and_never_stops_the_app(self):
        from coscc.http.plugin import hooks_of

        self.own(
            "notes",
            '---\nname: "Notes"\nmodel: {"id": "claude-sonnet-5-5", "effort": "low"}\n'
            'tools: {"Edit": "allow"}\noutput: {"kind": "artifact", "version": 1}\n---\nWrite.\n',
        )
        self.assertIn("holds Bash too", "; ".join(pack.row("notes")["problems"]))
        # With no feature the catalog lacks the built-ins' feature tools: only the owner's row
        # is asked here.
        real = pack.check
        with mock.patch.object(
            pack, "check", lambda row, *a: real(row, *a) if row.get("name") == "Notes" else []
        ):
            hooks_of([], {})

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

    def test_a_skill_name_off_the_pattern_is_never_written(self):
        row = pack.row("spec")
        for name in ("../../x", "A", "a/b", ""):
            with self.subTest(name=name):
                with mock.patch.dict(row, {"skills": [name]}), self.assertRaises(ValueError):
                    pack._write_skill(row, name, "text")
        self.assertFalse((pack.owner_dir() / "x").exists())

    def test_only_an_opus_or_sonnet_id_takes_the_1m_suffix(self):
        for good in ("claude-opus-5-5[1m]", "claude-sonnet-5-5[1m]", "claude-haiku-4-5"):
            self.assertEqual(pack.check_model("model", {"id": good}), [])
            self.assertEqual(models.check("model", good), (good, ""))
        bad = "claude-haiku-4-5[1m]"
        self.assertTrue(pack.check_model("model", {"id": bad}))
        self.assertTrue(pack.check_model("model", {"id": "x", "trial": ["claude-opus-5-5", bad]}))
        self.assertIsNone(models.check("model", bad)[0])
        with self.assertRaises(ValueError):
            pack.write("intent", "model", {"id": bad, "effort": "low"})

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


# A database at 12 (0.15), the one the agent prefs move out of.
V12 = Path(__file__).resolve().parents[1] / "store" / "fixtures" / "v12.sql"


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
            data.ensure_dir()
            with sqlite3.connect(data.db_path) as conn:
                conn.executescript(V12.read_text(encoding="utf-8"))
                for k, v in {**self.PREFS, "autopilot": True}.items():
                    conn.execute("INSERT INTO prefs VALUES (?, ?)", (k, json.dumps(v)))
                conn.execute("PRAGMA user_version=12")
            conn.close()
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
            self.assertEqual(data.prefs(), {"autopilot": True})


class AProcessIsHeldToWhatTheLoopAssumes(unittest.TestCase):
    """`check_process`'s tighter reasons, one test each."""

    def reasons(self, process: dict) -> str:
        return "\n".join(pack.check_process("p", process, pack.builtin_rows()))

    def test_a_key_the_state_does_not_have(self):
        impl = {**_process()["states"]["impl"], "nxt": []}
        self.assertIn("p.impl: no such key nxt", self.reasons(_process(impl=impl)))
        self.assertIn("p: no such key stat", self.reasons({**_process(), "stat": 1}))

    def test_a_field_of_the_wrong_type(self):
        impl = {**_process()["states"]["impl"], "optional": "yes", "hint": 3}
        got = self.reasons(_process(impl=impl))
        self.assertIn("p.impl.optional must be bool", got)
        self.assertIn("p.impl.hint must be str", got)
        self.assertIn("p.end must be str", self.reasons({**_process(), "end": 1}))

    def test_skip_is_only_the_skip_decision(self):
        impl = {**_process()["states"]["impl"], "skip": "ship-ready"}
        self.assertIn("p.impl.skip: only skip-decision", self.reasons(_process(impl=impl)))

    def test_an_agent_state_reads_no_when(self):
        impl = {**_process()["states"]["impl"], "when": {"guard": "ship-ready"}}
        self.assertIn("p.impl.when: an agent state reads none", self.reasons(_process(impl=impl)))

    def test_every_state_optional_leaves_no_opener(self):
        bad = _process()
        for st in bad["states"].values():
            st["optional"] = True
        self.assertIn("p: every state is optional", self.reasons(bad))

    def test_a_review_that_cannot_send_the_unit_back(self):
        review = {"agent": "review", "next": [{"to": "ship"}]}
        self.assertIn("p.review: a review state needs a way", self.reasons(_process(review=review)))

    def test_a_fast_lane_way_that_is_the_last_way(self):
        intent = {
            **_process()["states"]["intent"],
            "next": [{"to": "impl", "when": {"guard": "fast-lane"}}],
        }
        self.assertIn(
            "p.intent: the fast-lane way is the last way", self.reasons(_process(intent=intent))
        )

    def test_a_merge_with_no_review_before_it(self):
        impl = {**_process()["states"]["impl"], "next": [{"to": "ship"}]}
        got = self.reasons(_process(impl=impl))
        self.assertIn("p.ship: a review state is not on every path to it", got)


def _scan(**over) -> dict:
    return {**pack.rows()["scan"]["builtin"], **over}


class TriggersAreChecked(unittest.TestCase):
    def reasons(self, row: dict) -> str:
        rows = {k: r["builtin"] for k, r in pack.rows().items()}
        return "\n".join(pack.check(row, CATALOG, rows))

    def test_the_scan_row_passes(self):
        self.assertEqual(self.reasons(_scan()), "")

    def test_the_scan_reads_the_trunk_and_writes_nothing(self):
        row = _scan()
        self.assertEqual(pack.tools(row), ("Read", "Glob", "Grep"))
        self.assertTrue(pack.reads_only(row))
        self.assertEqual((row["cwd"], row["ceilings"]), ("trunk", {"turns": 16, "usd": 1.5}))
        self.assertIn("($1.50 ceiling)", row["warning"])
        self.assertNotIn("Name no fix", row["body"])

    def test_an_unknown_trigger_key(self):
        self.assertIn("trigger.cron: no such trigger", self.reasons(_scan(trigger={"cron": "x"})))

    def test_an_unknown_event(self):
        said = self.reasons(_scan(trigger={"event": {"name": "unit.exploded"}}))
        self.assertIn("no bus event 'unit.exploded'", said)
        said = self.reasons(_scan(trigger={"event": {"name": "chat-turn.ended"}}))
        self.assertIn("names no workspace", said)

    def test_an_agent_run_fact_does_not_start_a_row_by_itself(self):
        # Every run publishes these, the row's own too: on one, the row would start itself again.
        for name in ("agent-run.ended", "agent-run.started"):
            said = self.reasons(_scan(trigger={"event": {"name": name}}))
            self.assertIn("it would start itself", said)
            with self.assertRaises(ValueError):
                pack.write("scan", "trigger", {"event": {"name": name}})
        # The neighbour stays open: another fact naming a workspace.
        self.assertEqual(self.reasons(_scan(trigger={"event": {"name": "unit.shipped"}})), "")

    def follower(self, key: str, after: str, **over) -> dict:
        return _scan(
            key=key,
            name=key.capitalize(),
            trigger={"event": {"name": "agent-run.ended", "from": after}, "manual": True},
            **over,
        )

    def chain(self, row: dict, *others: dict) -> str:
        rows = {k: r["builtin"] for k, r in pack.rows().items()}
        rows.update({o["key"]: o for o in others})
        return "\n".join(pack.check(row, CATALOG, {**rows, row["key"]: row}))

    def test_a_row_runs_after_another_agents_done_run(self):
        self.assertEqual(self.chain(self.follower("b", "scan")), "")
        self.assertEqual(pack.after_of(self.follower("b", "scan")), "scan")
        self.assertEqual(pack.after_of(_scan()), "")

    def test_what_a_follower_must_say_and_the_neighbours_refused(self):
        said = self.chain(self.follower("b", "scan", default="on"))
        self.assertIn("off until you turn it on in a workspace", said)
        self.assertIn("cannot run after itself", self.chain(self.follower("b", "b")))
        self.assertIn("no agent nobody", self.chain(self.follower("b", "nobody")))
        # A stage's agent ends no run of its own through a trigger.
        self.assertIn("so it never ends a run", self.chain(self.follower("b", "plan")))
        shipped = self.follower("b", "scan")
        shipped["trigger"] = {"event": {"name": "unit.shipped", "from": "scan"}}
        self.assertIn("only agent-run.ended names", self.chain(shipped))

    def test_agents_cannot_start_each_other_in_a_circle(self):
        b = self.follower("b", "c")
        c = self.follower("c", "b")
        self.assertIn("B runs after C runs after B", self.chain(b, c))
        d = self.follower("d", "f")
        e = self.follower("e", "d")
        f = self.follower("f", "e")
        self.assertIn("in a circle", self.chain(d, e, f))

    def test_a_chain_holds_at_most_two_after_the_first(self):
        b, c = self.follower("b", "scan"), self.follower("c", "b")
        self.assertEqual(self.chain(c, b), "")
        d = self.follower("d", "c")
        self.assertIn(
            "at most 2 agents after the first, not 3 (Sowilo → B → C → D)", self.chain(d, b, c)
        )
        # From below too: putting b after scan, when c and d already follow it.
        self.assertIn("not 3", self.chain(b, c, d))

    def test_a_chain_and_its_default_are_one_write(self):
        pack.write("scan", "default", "on")
        chain = {"event": {"name": "agent-run.ended", "from": "outcome"}, "manual": True}
        with self.assertRaises(ValueError):
            pack.write("scan", "trigger", chain)
        # Refused together: neither is saved.
        with self.assertRaises(ValueError):
            pack.write(
                "scan",
                "trigger",
                {**chain, "event": {**chain["event"], "from": "x"}},
                None,
                {"default": "off"},
            )
        self.assertEqual((pack.row("scan")["default"], pack.after_of(pack.row("scan"))), ("on", ""))
        pack.write("scan", "trigger", chain, None, {"default": "off"})
        self.assertEqual(
            (pack.row("scan")["default"], pack.after_of(pack.row("scan"))), ("off", "outcome")
        )
        # Undone with its default back, in one write too.
        pack.write("scan", "trigger", None, None, {"default": "on"})
        self.assertEqual((pack.row("scan")["default"], pack.after_of(pack.row("scan"))), ("on", ""))
        # Nothing else rides with a part past its own checks.
        for other in ({"body": "x"}, {"output": {"kind": "x"}}, {"input": {"data": ["gossip"]}}):
            with self.assertRaises(ValueError, msg=other):
                pack.write("scan", "trigger", chain, None, other)

    def test_a_reserved_name_is_refused_in_any_case(self):
        for name in ("Ansuz", "othala", "JERA"):
            self.assertIn("is reserved", self.reasons(_scan(name=name)), name)
            with self.assertRaises(ValueError):
                pack.write("scan", "name", name)
        # Neighbours stay open: a name holding one, and a row's own name kept.
        self.assertEqual(self.reasons(_scan(name="Ansuz2")), "")
        self.assertEqual(self.reasons(_scan(name="Sowilo")), "")
        self.assertIn("is another agent's", self.reasons(_scan(name="tiwaz")))

    def test_an_engine_mixed_with_others(self):
        said = self.reasons(_scan(trigger={"engine": "estimate", "manual": True}, default=None))
        self.assertIn("an engine row has no other trigger", said)

    def test_a_write_tool_on_a_row_a_schedule_starts(self):
        said = self.reasons(_scan(tools={"Read": "allow", "Bash": "allow"}))
        self.assertIn("holds only reading tools, not Bash", said)
        # Checked with no catalog too, as the owner's file is at load.
        self.assertIn("not Edit", "\n".join(pack.check(_scan(tools={"Edit": "allow"}))))
        # A row only a press starts holds only reading tools too.
        manual = _scan(trigger={"manual": True}, tools={"Read": "allow", "Write": "allow"})
        del manual["default"]
        self.assertIn("holds only reading tools, not Write", self.reasons(manual))
        pack.write("scan", "trigger", {"manual": True})
        with self.assertRaises(ValueError):
            pack.write("scan", "tools", {"Write": "allow"})

    def test_a_triggered_row_says_what_one_run_may_spend(self):
        said = self.reasons(_scan(ceilings={"turns": 4}))
        self.assertIn("ceilings.usd: a row a trigger starts says what one run may spend", said)
        self.assertIn(
            "ceilings.usd", self.reasons({k: v for k, v in _scan().items() if k != "ceilings"})
        )
        # An engine row is bounded by the engine; a state's agent by its process step.
        engine = _scan(trigger={"engine": "estimate"}, ceilings={"turns": 4}, default=None)
        self.assertNotIn("ceilings.usd", self.reasons(engine))
        for key, row in pack.rows().items():
            if pack.triggered(row):
                self.assertIn("usd", row["ceilings"], key)

    def test_a_triggered_row_has_no_one_to_ask(self):
        said = self.reasons(_scan(tools={"Read": "ask"}))
        self.assertIn("no one to ask, so Read is allow or off", said)
        self.assertEqual(self.reasons(_scan(tools={"Read": "allow", "Grep": "off"})), "")
        # A row a state runs may still ask; a trigger of the engine's is not a person-less start.
        self.assertTrue(pack.reads_only(_scan()))
        self.assertFalse(pack.reads_only(_scan(trigger={"engine": "estimate"})))
        self.assertFalse(pack.reads_only({"tools": {"Write": "ask"}}))
        # Both ask one rule: an engine row with another key, an unknown key, an empty trigger.
        for trigger in ({"engine": "estimate", "manual": True}, {"cron": "x"}, {}, "x", None):
            row = {"trigger": trigger}
            self.assertEqual(pack.reads_only(row), pack.triggered(row), trigger)
        self.assertEqual(
            pack.check(_scan(tools={"Read": "ask"})),
            pack.check(_scan(tools={"Read": "ask"}), CATALOG),
        )

    def test_a_triggered_row_holds_bash_only_in_the_sandbox(self):
        boxed = {"Read": "allow", "Bash": {"sandbox": {"network": ["127.0.0.1:3000"]}}}
        self.assertEqual(self.reasons(_scan(tools=boxed)), "")
        self.assertEqual(pack.check(_scan(tools=boxed)), [])
        for hosts in (["localhost:9090", "[::1]:8080"], []):
            fine = {"Bash": {"sandbox": {"network": hosts}}}
            self.assertEqual(self.reasons(_scan(tools=fine)), "")
        self.assertEqual(pack.sandbox_of(_scan(tools=boxed)), ("127.0.0.1:3000",))
        self.assertEqual(pack.tools(_scan(tools=boxed)), ("Read", "Bash"))
        self.assertIsNone(pack.sandbox_of(_scan(tools={"Bash": "allow"})))

    def test_the_sandbox_refuses_what_is_not_loopback_or_has_an_escape(self):
        for host in ("example.com:443", "10.0.0.2:80", "127.0.0.1", "localhost:0", "::1:80", "*"):
            said = self.reasons(_scan(tools={"Bash": {"sandbox": {"network": [host]}}}))
            self.assertIn(f"{host} is no loopback host with its port", said)
        # No other key: no unsandboxed escape, no write place, no other tool in this form.
        for given in (
            {"sandbox": {"network": [], "allowUnsandboxedCommands": True}},
            {"sandbox": {"network": []}, "dangerouslyDisableSandbox": True},
            {"sandbox": {"network": [], "write": ["/"]}},
            {"sandbox": True},
        ):
            self.assertIn(
                "tools.Bash is allow, ask, off or", self.reasons(_scan(tools={"Bash": given}))
            )
        said = self.reasons(_scan(tools={"Write": {"sandbox": {"network": []}}}))
        self.assertIn("tools.Write must be one of allow, ask, off", said)
        # Bash with no sandbox stays refused, and saving it is too.
        self.assertIn("(Bash only as", self.reasons(_scan(tools={"Bash": "allow"})))
        with self.assertRaises(ValueError):
            pack.write("scan", "tools", {"Bash": {"sandbox": {"network": ["example.com:80"]}}})
        pack.write("scan", "tools", {"Bash": {"sandbox": {"network": ["127.0.0.1:3000"]}}})
        self.assertEqual(pack.sandbox_of(pack.row("scan")), ("127.0.0.1:3000",))

    def test_only_a_triggered_row_is_sandboxed(self):
        rows = {k: r["builtin"] for k, r in pack.rows().items()}
        impl = {**rows["impl"], "tools": {"Bash": {"sandbox": {"network": []}}}}
        said = "\n".join(pack.check(impl, CATALOG, rows))
        self.assertIn("only a row a trigger starts runs Bash in the sandbox", said)

    def test_with_no_catalog_claude_codes_own_tools_must_be_known_to_read(self):
        """A feature's tool waits for the catalog: the run's check has it and refuses one that does
        more than read."""
        self.assertIn(
            "not Bash", "\n".join(pack.check(_scan(tools={"Read": "allow", "Bash": "allow"})))
        )
        self.assertEqual(pack.check(_scan(tools={"Read": "allow", "codegraph": "allow"})), [])
        catalog = {"Read": "read", "codegraph": "read", "vault": "external"}
        self.assertIn("not vault", "\n".join(pack.check(_scan(tools={"vault": "allow"}), catalog)))
        self.assertEqual(pack.check(_scan(tools={"codegraph": "allow"}), catalog), [])

    def test_a_schedule_needs_hours_and_a_default(self):
        row = _scan(trigger={"schedule": {"hours": 0}})
        del row["default"]
        said = self.reasons(row)
        self.assertIn("trigger.schedule.hours must be a whole number from 1", said)
        self.assertIn("says whether it is on or off", said)
        self.assertIn("default must be one of on, off", self.reasons(_scan(default="maybe")))

    def test_the_grader_row_passes_and_then_and_cwd_are_its_kinds(self):
        grader = {"key": "outcome", **pack.rows()["outcome"]["builtin"]}
        self.assertEqual(self.reasons(grader), "")
        said = self.reasons(_scan(output={**_scan()["output"], "then": "proposal-if-no"}))
        self.assertIn("output.then: a verdict may have proposal-if-no", said)
        said = self.reasons({**grader, "output": {**grader["output"], "then": "page-me"}})
        self.assertIn("output.then", said)
        self.assertIn("cwd: a row a trigger starts", self.reasons({**grader, "cwd": "branch"}))
        said = self.reasons({**grader, "trigger": {"engine": "estimate"}, "default": None})
        self.assertIn("cwd: a row a trigger starts", said)

    def test_an_owner_edit_is_held_to_the_same_rules(self):
        with self.assertRaises(ValueError):
            pack.write("scan", "tools", {"Write": "allow"})
        old, new = pack.write("scan", "trigger", {"schedule": {"hours": 6}, "manual": True})
        self.assertEqual(new["schedule"]["hours"], 6)


class AnAgentIsOnOrOffPerWorkspace(unittest.TestCase):
    def setUp(self):
        from coscc.store.db import Data

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = Data(self._tmp.name)

    def test_its_default_until_chosen_and_per_workspace(self):
        self.assertFalse(pack.agent_on(self.data, "scan", "/a"))
        pack.set_agent_on(self.data, "scan", "/a", True)
        self.assertTrue(pack.agent_on(self.data, "scan", "/a"))
        self.assertFalse(pack.agent_on(self.data, "scan", "/b"))

    def test_a_row_with_no_event_or_schedule_is_refused(self):
        with self.assertRaises(pack.PackError):
            pack.set_agent_on(self.data, "estimate", "/a", True)


class APackIsOnOrOffPerWorkspace(unittest.TestCase):
    def setUp(self):
        from coscc.store.db import Data

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = Data(self._tmp.name)

    def test_on_with_full_until_chosen_and_per_workspace(self):
        self.assertEqual(pack.default_process(self.data, "/a"), "coscc-sdlc/full")
        pack.set_packs(self.data, "/a", "coscc-sdlc", chosen="coscc-sdlc/short")
        self.assertEqual(pack.default_process(self.data, "/a"), "coscc-sdlc/short")
        self.assertEqual(pack.default_process(self.data, "/b"), "coscc-sdlc/full")

    def test_off_gives_no_process_but_keeps_the_choice(self):
        pack.set_packs(self.data, "/a", "coscc-sdlc", on=False, chosen="coscc-sdlc/short")
        self.assertIsNone(pack.default_process(self.data, "/a"))
        self.assertEqual(pack.default_process(self.data, "/b"), "coscc-sdlc/full")
        pack.set_packs(self.data, "/a", "coscc-sdlc", on=True)
        self.assertEqual(pack.default_process(self.data, "/a"), "coscc-sdlc/short")

    def test_a_pack_or_process_that_is_not_there_is_refused(self):
        for args in (
            ("other", True, None),
            ("coscc-sdlc", None, "coscc-sdlc/none"),
            ("coscc-sdlc", None, "x/full"),
        ):
            with self.assertRaises(pack.PackError):
                pack.set_packs(self.data, "/a", args[0], args[1], args[2])


def tiny(agent: str = "tidy") -> dict:
    """`short` with `agent` running its `impl` state (review reads the `impl` artifact)."""
    found = json.loads(json.dumps(pack.process("coscc-sdlc/short")))
    found["states"]["impl"]["agent"] = agent
    return found


def zipped(files: dict[str, bytes | str], links: tuple[str, ...] = ()) -> bytes:
    import io
    import stat
    import zipfile

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, blob in files.items():
            z.writestr(name, blob)
        for name in links:
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, "/etc/passwd")
    return out.getvalue()


ROW = '---\nname: "NAME"\nmodel: {"id": "claude-sonnet-5-5[1m]", "effort": "low"}\ntools: {"Read": "allow"}\noutput: {"kind": "reply"}\ntrigger: {"manual": true}\nceilings: {"turns": 4, "usd": 0.5}\n---\nReads.\n'


def a_pack(name: str = "mine", **more: bytes | str) -> dict[str, bytes | str]:
    """A pack with one row, `<name>-row`."""
    return {
        ".claude-plugin/plugin.json": json.dumps({"name": name, "version": "0.1.0"}),
        f"agents/{name}-row.md": ROW.replace("NAME", f"{name}-row"),
        **more,
    }


class ManyPacks(unittest.TestCase):
    """The built-in, each imported pack and the owner's own: one key space; new agents and
    processes in `local`; export and import."""

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        patcher = mock.patch.object(pack, "ROOT", self.d.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def left(self) -> list[str]:
        root = pack.packs_dir()
        return sorted(p.name for p in root.iterdir()) if root.is_dir() else []

    def test_a_whole_row_of_local_loads_as_an_agent(self):
        pack.new_row("tidy", "Tidy", "impl", CATALOG)
        pack.new_row("look", "Look", None, CATALOG)
        tidy, look = pack.row("tidy"), pack.row("look")
        self.assertEqual((tidy["pack"], tidy["own"], tidy["problems"]), ("local", True, []))
        self.assertEqual(tidy["tools"], pack.row("impl")["tools"])
        self.assertEqual(tidy[pack.BODY], pack.row("impl")[pack.BODY])
        self.assertEqual(pack.problems("look", CATALOG), [])
        self.assertEqual(look["trigger"], {"manual": True})
        self.assertEqual(look["tools"], {"Read": "allow", "Glob": "allow", "Grep": "allow"})
        self.assertNotIn("glyph", tidy)
        self.assertNotEqual(tidy["description"], pack.row("impl")["description"])
        self.assertIn("Tidy", tidy["description"])
        self.assertNotIn("state", tidy.get("trigger", {}))
        self.assertEqual(list(pack.rows())[-2:], ["look", "tidy"])
        self.assertEqual(
            json.loads((pack.owner_dir() / pack.MANIFEST).read_text())["name"], "local"
        )
        # The copy is the owner's row: an edit writes it whole, a key removed is gone.
        pack.write("tidy", "ceilings", {"turns": 20, "usd": 1.0})
        self.assertEqual(pack.row("tidy")["ceilings"], {"turns": 20, "usd": 1.0})
        self.assertEqual(pack.stamp("tidy")["pack"], "local@1.0.0")

    def test_a_copy_drops_the_variants_and_model_trial_it_would_run_unseen(self):
        pack.new_row("tidy", "Tidy", "impl", CATALOG)
        src, copy = pack.row("impl"), pack.row("tidy")
        self.assertNotIn("variants", copy)
        self.assertNotIn("trial", copy["model"])
        self.assertEqual(copy["model"].get("id"), src["model"].get("id"))
        self.assertEqual(copy["model"].get("effort"), src["model"].get("effort"))

    def test_a_taken_or_bad_key_or_name_is_refused_and_nothing_written(self):
        for key, name, why in (
            ("impl", "Other", "impl is taken"),
            ("Bad_Key", "Other", "a key is 1 to 24"),
            ("fresh", "Tiwaz", "name: Tiwaz is another agent's"),
        ):
            with self.subTest(key=key), self.assertRaises(pack.PackError) as e:
                pack.new_row(key, name, "review", CATALOG)
            self.assertIn(why, str(e.exception))
        self.assertFalse((pack.owner_dir() / "agents").exists())

    def test_delete_is_refused_while_a_process_names_it(self):
        pack.new_row("tidy", "Tidy", "impl", CATALOG)
        pack.write_process("tiny", tiny())
        with self.assertRaises(pack.PackError) as e:
            pack.delete_row("tidy")
        self.assertEqual(
            (e.exception.code, e.exception.reasons), ("in-use", ["tidy runs in local/tiny"])
        )
        with self.assertRaises(pack.PackError):
            pack.delete_row("impl")
        pack.write_process("tiny", None)
        pack.delete_row("tidy")
        self.assertIsNone(pack.row("tidy"))

    def test_which_states_do_what_is_worked_out_again_when_a_process_is_written(self):
        self.assertEqual(states.states_where(action="open-pr", process="local/tiny"), ())
        pack.new_row("tidy", "Tidy", "impl", CATALOG)
        pack.write_process("tiny", tiny())
        self.assertEqual(states.states_where(action="open-pr", process="local/tiny"), ("pr",))

    def test_a_local_process_runs_on_any_packs_rows_and_a_bad_one_is_a_problem(self):
        pack.new_row("tidy", "Tidy", "impl", CATALOG)
        pack.write_process("tiny", tiny())
        self.assertEqual(pack.agent_for("local/tiny", "impl"), "tidy")
        bad = tiny()
        del bad["states"]["review"]
        bad["states"]["pr"]["next"] = [{"to": "ship"}]
        with self.assertRaises(pack.PackError) as e:
            pack.write_process("tiny", bad)
        self.assertIn("a review state is not on every path to it", str(e.exception))
        # A hand edit of the file: the process is a problem on the page, never a crash.
        raw = json.loads((pack.owner_dir() / pack.PROCESS_FILE).read_text())
        raw["processes"]["tiny"] = bad
        (pack.owner_dir() / pack.PROCESS_FILE).write_text(json.dumps(raw))
        self.assertIsNone(pack.process("local/tiny"))
        shown = {p["name"]: p for p in pack.packs_shown(Data(self.d.name), "/ws")}
        self.assertIn(
            "tiny.ship: a review state is not on every path to it", shown["local"]["problems"]
        )
        self.assertEqual(shown["local"]["processes"], [])

    def test_an_imported_pack_is_off_until_turned_on_and_its_rows_load(self):
        data = Data(self.d.name)
        self.assertEqual(pack.import_zip(zipped(a_pack()), CATALOG), "mine")
        self.assertEqual(pack.row("mine-row")["pack"], "mine")
        shown = {p["name"]: p for p in pack.packs_shown(data, "/ws")}
        self.assertEqual(list(shown), ["coscc-sdlc", "mine", "local"])
        self.assertEqual((shown["mine"]["on"], shown["mine"]["imported"]), (False, True))
        self.assertEqual(self.left(), ["mine"])
        pack.set_packs(data, "/ws", "mine", on=True)
        self.assertTrue(pack.pack_on(data, "mine", "/ws"))
        pack.remove_pack("mine")
        self.assertIsNone(pack.row("mine-row"))

    def test_each_pack_shows_its_own_agents_faces(self):
        pack.import_zip(zipped(a_pack()), CATALOG)
        shown = {p["name"]: p for p in pack.packs_shown(Data(self.d.name), "/ws")}
        mine = pack.row("mine-row")["name"]
        self.assertEqual(
            shown["mine"]["agents"], [{"key": "mine-row", "name": mine, "glyph": mine[0]}]
        )
        impl = next(a for a in shown["coscc-sdlc"]["agents"] if a["key"] == "impl")
        self.assertEqual(impl["name"], pack.row("impl")["name"])
        self.assertNotIn("mine-row", [a["key"] for a in shown["coscc-sdlc"]["agents"]])

    def test_an_off_packs_scheduled_row_does_not_run_on_its_schedule(self):
        data = Data(self.d.name)
        timed = ROW.replace('{"manual": true}', '{"schedule": {"hours": 24}}\ndefault: "on"')
        pack.import_zip(zipped(a_pack(**{"agents/mine-row.md": timed.replace("NAME", "Mine")})))
        self.assertFalse(pack.agent_on(data, "mine-row", "/ws"))
        pack.set_packs(data, "/ws", "mine", on=True)
        self.assertTrue(pack.agent_on(data, "mine-row", "/ws"))

    def test_a_colliding_key_in_an_imported_pack_loads_none_of_its_rows(self):
        folder = pack.packs_dir() / "mine"
        for rel, text in a_pack(**{"agents/impl.md": ROW.replace("NAME", "Other")}).items():
            (folder / rel).parent.mkdir(parents=True, exist_ok=True)
            (folder / rel).write_text(str(text))
        self.assertIsNone(pack.row("mine-row"))
        self.assertEqual(pack.row("impl")["pack"], "coscc-sdlc")
        shown = {p["name"]: p for p in pack.packs_shown(Data(self.d.name), "/ws")}
        self.assertEqual(
            shown["mine"]["problems"], ["agents/impl.md: impl is another pack's agent"]
        )

    def test_each_import_refusal_fires_and_leaves_nothing(self):
        pack.import_zip(zipped(a_pack("taken")), CATALOG)
        cases = {
            "..": (zipped({**a_pack(), "../x.md": "x"}), "../x.md: not a path inside the pack"),
            "absolute": (zipped({**a_pack(), "/etc/x.md": "x"}), "/etc/x.md: not a path"),
            "link": (zipped(a_pack(), links=("agents/x.md",)), "agents/x.md: a link"),
            "kind": (zipped({**a_pack(), "run.sh": "x"}), "run.sh: a pack holds only"),
            "big": (b"x" * (pack.ZIP_MAX + 1), "over 1000000 bytes"),
            "many": (
                zipped({**a_pack(), **{f"skills/s{i}/SKILL.md": "x" for i in range(200)}}),
                "over 200 entries",
            ),
            "row": (
                zipped(a_pack(**{"agents/mine-row.md": ROW.replace('"Read"', '"Telepathy"')})),
                "mine-row: tools.Telepathy: no such tool in the catalog",
            ),
            "name": (zipped(a_pack("taken")), "name taken is taken"),
            "key": (
                zipped(a_pack(**{"agents/review.md": ROW.replace("NAME", "Rev")})),
                "review is another pack's agent",
            ),
            "own": (zipped(a_pack("local")), "name local is the app's own pack"),
            "skill": (
                zipped(a_pack(**{"skills/write-impl/SKILL.md": "x"})),
                "write-impl is another pack's skill",
            ),
            "process": (
                zipped(
                    a_pack(**{"process.json": json.dumps({"processes": {"p": {"start": "x"}}})})
                ),
                "p: a process is",
            ),
        }
        for what, (blob, why) in cases.items():
            with self.subTest(what), self.assertRaises(pack.PackError) as e:
                pack.import_zip(blob, CATALOG)
            self.assertIn(why, str(e.exception))
            self.assertEqual(self.left(), ["taken"])
        self.assertIsNone(pack.row("mine-row"))

    def test_export_then_import_under_another_name_is_the_same_rows_byte_for_byte(self):
        import io
        import zipfile

        pack.new_row("tidy", "Tidy", "impl", CATALOG)
        pack.write_process("tiny", tiny())
        # An override of a built-in row is the workspace's tuning, not the pack's.
        pack.write("review", "ceilings", {"turns": 10, "usd": 1.0})
        blob = pack.export_zip("local")
        files = {
            n: zipfile.ZipFile(io.BytesIO(blob)).read(n)
            for n in zipfile.ZipFile(io.BytesIO(blob)).namelist()
        }
        self.assertEqual(
            sorted(files), [".claude-plugin/plugin.json", "agents/tidy.md", "process.json"]
        )
        pack.write_process("tiny", None)
        pack.delete_row("tidy")
        files[".claude-plugin/plugin.json"] = json.dumps(
            {"name": "again", "version": "1.0.0"}
        ).encode()
        self.assertEqual(pack.import_zip(zipped(files), CATALOG), "again")
        for rel in ("agents/tidy.md", "process.json"):
            self.assertEqual((pack.packs_dir() / "again" / rel).read_bytes(), files[rel])
        self.assertEqual(pack.row("tidy")["pack"], "again")
        self.assertIsNotNone(pack.process("again/tiny"))
        # The built-in exports too.
        self.assertIn(
            "agents/impl.md", zipfile.ZipFile(io.BytesIO(pack.export_zip("coscc-sdlc"))).namelist()
        )


class WhatTheSecurityReviewFound(unittest.TestCase):
    """Each hole the review of the import proved, closed."""

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        patcher = mock.patch.object(pack, "ROOT", self.d.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def left(self) -> list[str]:
        root = pack.packs_dir()
        return sorted(p.name for p in root.iterdir()) if root.is_dir() else []

    def test_a_state_name_is_never_a_path(self):
        for bad in ("../x", "a/b", "a\\b"):
            p = tiny("impl")
            p["states"][bad] = {"action": "merge"}
            with self.subTest(bad):
                self.assertIn("a state name is", "; ".join(pack.check_process("p", p, pack.rows())))

    def test_review_before_merge_is_not_fooled_by_an_agent_keyed_as_a_state(self):
        pack.new_row("rv", "Rv", "impl", CATALOG)
        p = {
            "start": "intent",
            "end": "shipped",
            "states": {
                "intent": {
                    "agent": "intent",
                    "rerun": ["answers"],
                    "next": [{"to": "rv", "when": {"guard": "skip-decision"}}, {"to": "x"}],
                },
                "x": {"agent": "rv", "rerun": ["answers"], "next": [{"to": "pr"}]},
                "pr": {"action": "open-pr", "next": [{"to": "ship"}]},
                "rv": {
                    "agent": "review",
                    "next": [
                        {"to": "x", "when": {"field": "verdict", "is": "changes-requested"}},
                        {"to": "ship", "when": {"field": "verdict", "is": "pass"}},
                    ],
                },
                "ship": {"action": "merge"},
            },
        }
        self.assertIn(
            "p.ship: a review state is not on every path to it",
            pack.check_process("p", p, pack.rows()),
        )

    def test_a_bad_imported_input_is_its_rows_problem_never_an_error(self):
        bad = ROW.replace("NAME", "mine-row").replace("tools:", 'input: {"artifacts": "x"}\ntools:')
        with self.assertRaises(pack.PackError) as e:
            pack.import_zip(zipped(a_pack(**{"agents/mine-row.md": bad})), CATALOG)
        self.assertIn("mine-row.input", str(e.exception))
        folder = pack.packs_dir() / "mine"
        for rel, text in a_pack(**{"agents/mine-row.md": bad}).items():
            (folder / rel).parent.mkdir(parents=True, exist_ok=True)
            (folder / rel).write_text(str(text))
        self.assertTrue(pack.row("mine-row")["problems"])
        self.assertEqual(contracts.input_of("mine-row")["artifacts"], [])
        self.assertTrue(contracts.input_of("review")["artifacts"])

    def test_an_encrypted_odd_or_broken_zip_is_refused_and_leaves_nothing(self):
        import io
        import zipfile

        def one(info: zipfile.ZipInfo, compress: int = zipfile.ZIP_STORED) -> bytes:
            out = io.BytesIO()
            with zipfile.ZipFile(out, "w", compress) as z:
                z.writestr(".claude-plugin/plugin.json", json.dumps({"name": "x"}))
                z.writestr(info, "y" * 5000)
            return out.getvalue()

        locked = zipfile.ZipInfo("agents/a.md")
        locked.flag_bits |= 1
        odd = zipfile.ZipInfo("agents/a.md")
        odd.compress_type = zipfile.ZIP_BZIP2
        broken = bytearray(one(zipfile.ZipInfo("agents/a.md"), zipfile.ZIP_DEFLATED))
        at = broken.index(b"agents/a.md") + len("agents/a.md") + 2
        broken[at : at + 8] = b"\xff" * 8
        for what, blob in (
            ("encrypted", one(locked)),
            ("bzip2", one(odd)),
            ("broken", bytes(broken)),
        ):
            with self.subTest(what), self.assertRaises(pack.PackError):
                pack.import_zip(blob, CATALOG)
            self.assertEqual(self.left(), [])

    def test_a_skill_only_the_owners_pack_has_is_taken(self):
        own = pack.owner_dir() / "skills" / "my-skill" / pack.SKILL_FILE
        own.parent.mkdir(parents=True)
        own.write_text("mine")
        with self.assertRaises(pack.PackError) as e:
            pack.import_zip(zipped(a_pack(**{"skills/my-skill/SKILL.md": "theirs"})), CATALOG)
        self.assertIn("my-skill is your own pack's skill", str(e.exception))
