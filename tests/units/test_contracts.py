"""Each agent's output declaration: checked at load, the `submit` schemas generated from it."""

from __future__ import annotations

import ast
import copy
import unittest
from pathlib import Path
from typing import Any, get_args
from unittest import mock

import jsonschema

from coscc.agent import pack
from coscc.units import contracts
from coscc.units.contracts import ContractError


def _shipped() -> dict[str, Any]:
    return {"agents": copy.deepcopy(pack.rows())}


def _load(raw: dict[str, Any]) -> dict[str, contracts.Output]:
    return contracts.load(raw["agents"])


def _output(raw: dict[str, Any], agent: str) -> dict[str, Any]:
    return raw["agents"][agent]["output"]


def _refusal(raw: dict[str, Any]) -> str:
    try:
        _load(raw)
    except ContractError as e:
        return str(e)
    raise AssertionError("the load was not refused")


class TheShippedDeclarationsLoad(unittest.TestCase):
    def test_every_agent_row_and_the_estimate_declare_an_output(self):
        declared = contracts.load(pack.rows())
        self.assertEqual(
            {
                k
                for k, r in pack.rows().items()
                if r.get("output", {}).get("kind") in contracts.KINDS
            },
            set(declared),
        )
        self.assertEqual(declared["spec"]["kind"], "artifact")
        self.assertEqual(declared["review"]["kind"], "review")
        self.assertEqual(declared["integrate"]["kind"], "session")


def _row_without(agent, field):
    row = copy.deepcopy(pack.rows()[agent])
    del row["output"]["fields"][field]
    return row


class RemovingAFieldRefusesTheLoad(unittest.TestCase):
    def test_each_field_the_engine_reads_names_its_reader(self):
        declared = contracts.load(pack.rows())
        cases = 0
        for agent, out in declared.items():
            read = {**contracts.READS.get(out["kind"], {}), **contracts.READS.get(agent, {})}
            for field, (reader, _) in read.items():
                raw = _shipped()
                del _output(raw, agent)["fields"][field]
                self.assertEqual(
                    _refusal(raw),
                    f"contract-field-missing: {agent}.{field} (read by {reader})",
                )
                cases += 1
        # The fields read by name: refused when their group's other field is declared (type with
        # fix, needs_person with left_lane, the plan's four) or a process branches on them
        # (unmeasured, left_lane). A spike's `verdicts` stands alone: nothing refuses its absence.
        for agent, out in declared.items():
            if out["kind"] != "artifact":
                continue
            for field, (reader, _) in contracts.FIELD_READS.items():
                if field.rstrip("?") not in {n.rstrip("?") for n in out["fields"]}:
                    continue
                raw = _shipped()
                del _output(raw, agent)["fields"][field]
                if field == "verdicts":
                    contracts.load({**pack.rows(), agent: _row_without(agent, field)})
                    continue
                self.assertEqual(
                    _refusal(raw), f"contract-field-missing: {agent}.{field} (read by {reader})"
                )
                cases += 1
        # judgement and questions for six artifacts, four review fields, one field each for
        # integrate, estimate, the scan's proposals and the grader's criteria, Dagaz's five; then
        # the nine read by name.
        self.assertEqual(cases, 6 * 2 + 4 + 4 + 5 + 9)

    def test_the_intents_type_names_branch_for(self):
        raw = _shipped()
        del raw["agents"]["intent"]["output"]["fields"]["type"]
        self.assertEqual(_refusal(raw), "contract-field-missing: intent.type (read by branch_for)")

    def test_a_field_read_when_present_must_still_be_declared_optional(self):
        raw = _shipped()
        fields = raw["agents"]["intent"]["output"]["fields"]
        fields["fix"] = fields.pop("fix?")
        self.assertEqual(_refusal(raw), "contract-field-missing: intent.fix? (read by fast-lane)")
        raw = _shipped()
        raw["agents"]["impl"]["output"]["fields"]["left_lane?"] = "number"
        self.assertTrue(_refusal(raw).startswith("contract-bad-type: impl.left_lane: "))

    def test_the_plans_fields_name_their_readers(self):
        for field, reader in (
            ("variant", "label_of"),
            ("files", "label_of"),
            ("steps", "render"),
            ("rests_on", "evaluate"),
        ):
            raw = _shipped()
            del raw["agents"]["plan"]["output"]["fields"][field]
            self.assertEqual(
                _refusal(raw), f"contract-field-missing: plan.{field} (read by {reader})"
            )

    def test_the_label_enum_is_the_policys(self):
        from typing import get_args

        from coscc.agent import policy

        self.assertEqual(
            _shipped()["agents"]["plan"]["output"]["fields"]["variant"],
            {"enum": list(get_args(policy.Label))},
        )

    def test_spec_without_unmeasured_names_the_spike_rule(self):
        raw = _shipped()
        del raw["agents"]["spec"]["output"]["fields"]["unmeasured"]
        self.assertEqual(
            _refusal(raw), "contract-field-missing: spec.unmeasured (read by spike-holds)"
        )

    def test_an_optional_field_the_engine_reads_is_missing(self):
        raw = _shipped()
        fields = raw["agents"]["impl"]["output"]["fields"]
        fields["needs_person?"] = fields.pop("needs_person")
        self.assertEqual(
            _refusal(raw), "contract-field-missing: impl.needs_person (read by impl-claim)"
        )

    def test_removing_a_questions_recommendation_names_the_questions_reader(self):
        for agent in ("intent", "spec", "plan"):
            raw = _shipped()
            del raw["agents"][agent]["output"]["fields"]["questions"]["list"]["recommendation"]
            self.assertTrue(
                _refusal(raw).startswith(
                    f"contract-field-missing: {agent}.questions.recommendation (read by "
                ),
                agent,
            )

    def test_a_sub_field_a_reader_names_is_missing(self):
        raw = _shipped()
        del raw["agents"]["spike"]["output"]["fields"]["verdicts"]["list"]["verdict"]
        self.assertEqual(
            _refusal(raw), "contract-field-missing: spike.verdicts.verdict (read by spike-holds)"
        )


class AWrongTypeRefusesTheLoad(unittest.TestCase):
    def test_a_read_field_of_another_type(self):
        raw = _shipped()
        raw["agents"]["spec"]["output"]["fields"]["unmeasured"] = "text"
        self.assertTrue(
            _refusal(raw).startswith("contract-bad-type: spec.unmeasured: "), _refusal(raw)
        )

    def test_an_enum_the_reader_does_not_know(self):
        raw = _shipped()
        raw["agents"]["review"]["output"]["fields"]["verdict"] = {"enum": ["pass", "maybe"]}
        self.assertTrue(_refusal(raw).startswith("contract-bad-type: review.verdict: "))

    def test_a_bad_pattern_a_bad_version_and_a_bad_kind(self):
        raw = _shipped()
        raw["agents"]["plan"]["output"]["fields"]["note"] = "U[0-9"
        self.assertTrue(_refusal(raw).startswith("contract-bad-type: plan.note: "))
        raw = _shipped()
        raw["agents"]["plan"]["output"]["version"] = 0
        self.assertTrue(_refusal(raw).startswith("contract-bad-type: plan.version: "))
        with self.assertRaises(ContractError) as e:
            contracts.check("plan", {"kind": "helper", "version": 1, "fields": {}})
        self.assertTrue(str(e.exception).startswith("contract-bad-type: plan.kind: "))

    def test_an_artifact_does_not_declare_who_sent_it(self):
        raw = _shipped()
        raw["agents"]["plan"]["output"]["fields"]["stage"] = "text"
        self.assertTrue(_refusal(raw).startswith("contract-bad-type: plan.stage: "))


class AnUnknownWordRefusesTheLoad(unittest.TestCase):
    def test_a_lowercase_word_that_is_no_type(self):
        for word in ("string", "int", "texts"):
            raw = _shipped()
            raw["agents"]["plan"]["output"]["fields"]["note"] = word
            self.assertTrue(_refusal(raw).startswith("contract-bad-type: plan.note: "), word)

    def test_a_field_named_list_or_enum_and_an_empty_object(self):
        for bad in ({"list": "text", "x": "text"}, {"enum": ["a"], "y": "text"}, {}):
            raw = _shipped()
            raw["agents"]["plan"]["output"]["fields"]["note"] = bad
            self.assertTrue(_refusal(raw).startswith("contract-bad-type: plan.note"), bad)

    def test_an_enum_of_no_words_and_a_bad_name(self):
        raw = _shipped()
        raw["agents"]["plan"]["output"]["fields"]["note"] = {"enum": []}
        self.assertTrue(_refusal(raw).startswith("contract-bad-type: plan.note: "))
        raw = _shipped()
        raw["agents"]["plan"]["output"]["fields"]["Note"] = "text"
        self.assertTrue(_refusal(raw).startswith("contract-bad-type: plan.Note: "))


class AProposalsChangeIsReadWhenDeclared(unittest.TestCase):
    """A proposal's `change`, `signal`, `measure` and `usd` are read when present: the scan
    declares them, another proposing row may leave them out, and none may declare another type."""

    def item(self, raw):
        return _output(raw, "scan")["fields"]["proposals"]["list"]

    def test_the_scan_declares_all_four(self):
        self.assertLessEqual({"change", "signal", "measure", "usd"}, set(self.item(_shipped())))
        self.assertEqual(contracts.output("scan")["version"], 2)

    def test_a_proposing_row_without_them_loads(self):
        raw = _shipped()
        for field in ("change", "signal", "measure", "usd"):
            del self.item(raw)[field]
        self.assertEqual(_load(raw)["scan"]["kind"], "proposal")
        raw = _shipped()
        item = self.item(raw)
        item["change?"] = item.pop("change")
        _load(raw)

    def test_one_of_another_type_is_refused(self):
        for field, bad in (
            ("change", "text"),
            ("change", {"kind": "text", "path": "text", "text": "text"}),
            ("signal", {"kind": {"enum": ["rerun"]}, "now": "number", "target": "number"}),
            ("usd", "number"),
        ):
            raw = _shipped()
            self.item(raw)[field] = bad
            self.assertTrue(
                _refusal(raw).startswith(f"contract-bad-type: scan.proposals.{field}"), field
            )
        raw = _shipped()
        self.item(raw)["change?"] = "text"
        del self.item(raw)["change"]
        self.assertTrue(_refusal(raw).startswith("contract-bad-type: scan.proposals.change"))

    def test_the_signal_kinds_are_the_interventions(self):
        from coscc.runner import interventions

        self.assertEqual(get_args(contracts.SignalKind), interventions.KINDS)

    def test_the_schema_takes_a_cost_of_two_decimals_at_most(self):
        usd = contracts.schema("scan")["properties"]["proposals"]["items"]["properties"]["usd"]
        for good in ("3", "3.5", "0.75"):
            jsonschema.validate(good, usd)
        for bad in ("3.555", "$3", "-1", 3.5):
            with self.assertRaises(jsonschema.ValidationError, msg=bad):
                jsonschema.validate(bad, usd)


class AnOptionalFieldIsNotRequired(unittest.TestCase):
    def test_a_trailing_question_mark_leaves_the_field_out_of_required(self):
        out = contracts.check(
            "planted", {"kind": "session", "version": 1, "fields": {"a": "text", "b?": "number"}}
        )
        schema = contracts.schema_of("planted", out)
        self.assertEqual(schema["required"], ["a"])
        self.assertEqual(set(schema["properties"]), {"a", "b"})
        jsonschema.validate({"a": "x"}, schema)
        jsonschema.validate({"a": "x", "b": 2}, schema)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({"a": "x", "c": 1}, schema)


# Each declaration's version and the hash of its kind and fields. A change to a declaration
# changes its hash: bump its version, add the migration of the stored records, then pin both.
PINNED = {
    "idea": (2, "ea807636d79d"),
    "intent": (3, "ca57a2f691e3"),
    "spec": (2, "aea0a6c62a73"),
    "spike": (2, "95f4668e18e5"),
    "plan": (4, "f49d1faf3b5f"),
    "impl": (3, "0e0331fd23e0"),
    "review": (2, "86f63317e692"),
    "integrate": (1, "9e29819d42c2"),
    "estimate": (1, "cd5fc053a8e3"),
    "scan": (2, "de0fdf5bc251"),
    "outcome": (1, "5a28b9ca8b0f"),
    "dagaz": (2, "c2164bfa60dd"),
}


class AChangedDeclarationNeedsANewVersion(unittest.TestCase):
    def test_each_declaration_is_pinned_with_its_version(self):
        declared = contracts.load(pack.rows())
        now = {a: (o["version"], contracts.fingerprint(o)) for a, o in declared.items()}
        self.assertEqual(now, PINNED)

    def test_a_changed_field_changes_the_hash(self):
        out = contracts.output("spec")
        changed = copy.deepcopy(out)
        changed["fields"]["questions"] = {"list": {"n": "number"}}
        self.assertNotEqual(contracts.fingerprint(out), contracts.fingerprint(changed))

    def test_a_record_of_another_version_is_refused(self):
        with self.assertRaises(ContractError) as e:
            contracts.check_stored("spec", 1)
        self.assertEqual(str(e.exception), "output-version: spec stored v1, declared v2")
        contracts.check_stored("spec", 2)


class TheBlockNamesEveryDeclaredField(unittest.TestCase):
    def test_the_prompt_teaches_each_field_the_schema_asks_for(self):
        from coscc.runner import prompt

        for agent, out in contracts.load(pack.rows()).items():
            if out["kind"] == "artifact":
                block = prompt.submit_block(agent, f"{agent}.md", writes_own=False)
            elif out["kind"] == "review":
                block = prompt.round_block("review", "review.md")
            else:
                continue
            for field in out["fields"]:
                self.assertIn(f"`{field.rstrip('?')}`", block, f"{agent}.{field}")

    def test_the_intent_block_lists_the_branch_types_the_schema_takes(self):
        from coscc.runner import prompt

        block = prompt.submit_block("intent", "intent.md", writes_own=False)
        listed = ", ".join(f"`{t}`" for t in get_args(contracts.BranchType))
        self.assertIn(listed, block)


class TheBlockIsTheDeclaration(unittest.TestCase):
    """The `submit` block is written from `contracts.output`: a declaration changes, the block
    follows, and `prompt.py` is not touched."""

    def block(self, agent="spec"):
        from coscc.runner import prompt

        return prompt.submit_block(agent, f"{agent}.md", writes_own=False)

    def test_a_field_added_to_a_declaration_is_taught_in_the_declared_order(self):
        out = copy.deepcopy(contracts.output("spec"))
        out["fields"]["extra?"] = "text"
        with mock.patch.object(contracts, "output", return_value=out):
            block = self.block()
        self.assertIn("`extra`", block)
        line = next(x for x in block.splitlines() if x.startswith("- `extra`"))
        self.assertIn("may be left out", line)
        order = [block.index(f"- `{n.rstrip('?')}`") for n in out["fields"]]
        self.assertEqual(order, sorted(order))

    def test_each_type_is_written_from_its_declaration(self):
        from coscc.runner.prompt import _describe

        self.assertEqual(_describe("text"), "text")
        self.assertEqual(_describe("number"), "a whole number")
        self.assertIn("`holds`", _describe({"enum": ["holds", "fails"]}))
        self.assertIn("`fails`", _describe({"enum": ["holds", "fails"]}))
        self.assertTrue(_describe({"list": "U[0-9]+"}).startswith("a list of"))
        self.assertIn("U[0-9]+", _describe({"list": "U[0-9]+"}))
        got = _describe({"list": {"n": "number", "text": "text"}})
        self.assertIn("{n: a whole number, text: text}", got)
        self.assertIn("may be left out", _describe({"a?": "text"}))

    def test_a_field_the_kind_requires_has_the_engines_sentence(self):
        for agent, out in contracts.load(pack.rows()).items():
            if out["kind"] != "artifact":
                continue
            block = self.block(agent)
            self.assertIn("`not-ready`", block, agent)
            self.assertIn("stays at this stage", block, agent)
            self.assertIn("`## Open questions`", block, agent)

    def test_a_review_round_block_says_what_goes_in_each_required_field(self):
        from coscc.runner import prompt

        block = prompt.round_block("review", "review.md")
        for part in ("`verdict`", "`findings`", "`screens`", "`fixed_in`", "`claim-rejected`"):
            self.assertIn(part, block)

    def test_no_block_names_a_status_line(self):
        from coscc.runner import prompt

        for agent, out in contracts.load(pack.rows()).items():
            if out["kind"] == "artifact":
                self.assertNotIn("Status:", self.block(agent), agent)
        self.assertNotIn("Status:", prompt.round_block("review", "review.md"))


class TheSchemasAreGenerated(unittest.TestCase):
    def test_an_artifact_names_its_sender_and_takes_nothing_else(self):
        schema = contracts.schema("spec")
        self.assertEqual(schema["properties"]["stage"], {"type": "string", "enum": ["spec"]})
        self.assertEqual(schema["required"], ["stage", "judgement", "questions", "unmeasured"])
        self.assertIs(schema["additionalProperties"], False)
        obj = {"stage": "spec", "judgement": "ready", "questions": [], "unmeasured": ["U1"]}
        jsonschema.validate(obj, schema)
        for bad in ({**obj, "stage": "plan"}, {**obj, "unmeasured": ["X1"]}, {**obj, "id": 1}):
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(bad, schema)

    def test_reads_keeps_only_what_the_engine_decides_on(self):
        obj = {"stage": "spike", "judgement": "ready", "questions": [], "verdicts": []}
        self.assertEqual(
            contracts.reads("spike", obj), {"judgement": "ready", "questions": [], "verdicts": []}
        )
        self.assertEqual(contracts.reads("plan", {**obj, "verdicts": [1]})["judgement"], "ready")
        self.assertNotIn("verdicts", contracts.reads("plan", obj))


def _schema_literals(source: str) -> list[int]:
    """The lines of each dict literal with a JSON Schema key, or a `type` naming a JSON Schema
    type; an MCP content block's `{"type": "text"}` is no schema."""
    words = {"properties", "items", "required", "additionalProperties", "pattern", "enum"}
    types = {"object", "array", "string", "integer", "number", "boolean"}
    return [
        n.lineno
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Dict)
        for k, v in zip(n.keys, n.values, strict=True)
        if isinstance(k, ast.Constant)
        and (
            k.value in words
            or (k.value == "type" and isinstance(v, ast.Constant) and v.value in types)
        )
    ]


class NoHandWrittenSchema(unittest.TestCase):
    def test_submit_holds_no_json_schema_literal(self):
        from coscc.units import submit

        self.assertEqual(_schema_literals(Path(submit.__file__).read_text(encoding="utf-8")), [])

    def test_the_check_finds_a_planted_schema_and_passes_a_content_block(self):
        self.assertEqual(_schema_literals('S = {"type": "object"}\nT = {"enum": []}'), [1, 2])
        self.assertEqual(_schema_literals('C = {"type": "text", "text": "x"}'), [])


class TheLoopsBranchTypesAreTheDeclaredEnum(unittest.TestCase):
    def test_one_list_of_types(self):
        from coscc.loop import BRANCH_TYPES

        declared = contracts.output("intent")["fields"]["type"]
        self.assertEqual(declared, {"enum": list(get_args(contracts.BranchType))})
        self.assertEqual(BRANCH_TYPES, list(get_args(contracts.BranchType)))

    def test_the_schema_asks_for_a_type_and_takes_a_fix(self):
        schema = contracts.schema("intent")
        self.assertIn("type", schema["required"])
        self.assertNotIn("fix", schema["required"])
        fix = {
            "reproduction": "run x",
            "expected": {"source": "coscc/a.py:1-2", "text": "t"},
            "actual": "boom",
        }
        base = {"stage": "intent", "judgement": "ready", "questions": [], "type": "fix"}
        jsonschema.validate({**base, "fix": fix}, schema)
        bad = {**fix, "expected": {"source": "a b", "text": "t"}}
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({**base, "fix": bad}, schema)


class AnInputDeclarationIsChecked(unittest.TestCase):
    """Each stage's `input` names stages, agents and data sources there are, or the load refuses."""

    def inputs(self, raw: dict[str, Any]) -> dict[str, contracts.Input]:
        return contracts.load_inputs(raw["agents"])

    def test_every_stage_that_composes_a_prompt_declares_one(self):
        loaded = self.inputs(_shipped())
        for stage in ("idea", "intent", "spec", "spike", "plan", "impl", "review"):
            self.assertIn(stage, loaded)
        self.assertEqual(loaded["impl"]["artifacts"], ["intent", "spec?", "plan?"])

    def test_an_unknown_artifact_output_or_source_refuses_it(self):
        for part, value in (
            ("artifacts", ["intent", "notes"]),
            ("outputs", ["pr"]),
            ("data", ["weather"]),
            ("answers", "yes"),
        ):
            raw = copy.deepcopy(_shipped())
            raw["agents"]["review"]["input"][part] = value
            with self.assertRaises(ContractError, msg=part) as caught:
                self.inputs(raw)
            self.assertEqual(caught.exception.code, "contract-bad-type")
            self.assertIn(f"review.input.{part}", str(caught.exception))

    def test_a_missing_key_refuses_it(self):
        raw = copy.deepcopy(_shipped())
        del raw["agents"]["spec"]["input"]["findings"]
        with self.assertRaises(ContractError):
            self.inputs(raw)


class TheCacheIsReadOnce(unittest.TestCase):
    def test_a_cache_replaced_meanwhile_does_not_change_what_this_read_returns(self):
        from unittest import mock

        real = contracts.load_inputs

        def others_write_meanwhile(rows):
            got = real(rows)
            contracts._READ[:] = [(object(), {}, {})]
            return got

        contracts._READ.clear()
        with mock.patch.object(contracts, "load_inputs", others_write_meanwhile):
            outputs, inputs = contracts._read()
        self.assertIn("dagaz", outputs)
        self.assertIn("dagaz", inputs)
        contracts._READ.clear()
