"""`coscc/agent/agents.py`: the agent table of `0036 spec.md` R1, its overrides, and every
string built from a row."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from coscc.agent import agents
from coscc.agent.agents import agent_for


def _pref(fields: dict) -> str:
    return json.dumps(fields)


class TheTableIsTheOneTheSpecChose(unittest.TestCase):
    def test_the_default_table_holds_the_names_the_intent_chose(self):
        expected = {
            "idea": ("ᛜ", "Ingwaz"),
            "intent": ("ᚾ", "Nauthiz"),
            "spec": ("ᚲ", "Kenaz"),
            "spike": ("ᛈ", "Perthro"),
            "plan": ("ᚱ", "Raidho"),
            "impl": ("ᚢ", "Uruz"),
            "pr": ("ᚨ", "Ansuz"),
            "review": ("ᛏ", "Tiwaz"),
            "ship": ("ᛟ", "Othala"),
            "integrate": ("ᚷ", "Gebo"),
            "precedent": ("ᛃ", "Jera"),
        }
        defaults, problems = agents.load_defaults()
        self.assertEqual(problems, [])
        self.assertEqual(set(defaults), set(expected))
        for key, (glyph, name) in expected.items():
            row = agent_for(key)
            self.assertEqual((row["glyph"], row["name"]), (glyph, name), key)
            self.assertEqual(row["source"], {f: agents.DEFAULT for f in agents.FIELDS})
        self.assertEqual(agent_for("review")["meaning"], "Tyr: justice and judgement")
        self.assertEqual(agent_for("review")["role"], "Judges the change and never edits code.")
        # Spec C5, and R1 for the two rows `0051` added.
        for key in ("integrate", "spike", "precedent"):
            self.assertEqual((agent_for(key)["meaning"], agent_for(key)["role"]), ("", ""), key)

    def test_implement_is_impl(self):
        self.assertEqual(agent_for("implement"), agent_for("impl"))
        self.assertEqual(agent_for("implement")["key"], "impl")

    def test_an_unknown_stage_has_no_agent(self):
        self.assertIsNone(agent_for("deploy"))
        self.assertIsNone(agent_for(""))

    def test_a_broken_default_file_is_a_problem_not_a_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "agents.json"
            bad.write_text("{", encoding="utf-8")
            self.assertEqual(agents.load_defaults(bad)[0], {})
            self.assertIn("not JSON", agents.load_defaults(bad)[1][0])
            bad.write_text(json.dumps({"agents": {
                "a": {"glyph": "x", "name": "Has space"},
                "b": {"glyph": "y", "name": "Fine", "role": "two\nlines"},
            }}), encoding="utf-8")
            rows, problems = agents.load_defaults(bad)
            self.assertEqual(list(rows), ["b"])
            self.assertEqual(rows["b"]["role"], "")
            self.assertEqual(len(problems), 3, problems)
            self.assertEqual(agents.load_defaults(Path(tmp) / "none.json")[0], {})


class OverridesComeFirst(unittest.TestCase):
    def test_an_override_wins_field_by_field_and_a_broken_one_falls_back(self):
        overrides, problems = agents.overrides_from({
            "agent:review": _pref({"name": "Judge", "glyph": "a b"}),
            "agent:plan": "not json",
            "agent:spec": _pref("Kenaz"),
            "model:review": _pref("ignored, another prefix"),
        })
        self.assertEqual(overrides, {"review": {"name": "Judge"}})
        self.assertEqual(len(problems), 3, problems)
        row = agent_for("review", overrides)
        self.assertEqual(row["name"], "Judge")
        self.assertEqual(row["glyph"], "ᛏ")
        self.assertEqual(row["source"]["name"], agents.OVERRIDE)
        self.assertEqual(row["source"]["glyph"], agents.DEFAULT)
        self.assertEqual(agent_for("plan", overrides)["name"], "Raidho")

    def test_every_rule_of_r2_but_the_duplicate(self):
        ok = {"name": "Tiwaz-2", "glyph": "ᛏᛏ", "meaning": "x" * 60, "role": "y" * 200}
        for field, value in ok.items():
            self.assertEqual(agents.check_field(field, value), "", field)
        wrong = [
            ("name", ""), ("name", "2abc"), ("name", "a" * 25), ("name", "Tïwaz"), ("name", "a b"),
            ("glyph", ""), ("glyph", "abc"), ("glyph", "a b"), ("glyph", " "),
            ("meaning", "x" * 61), ("meaning", "a\nb"), ("role", "y" * 201), ("role", "a\rb"),
            ("name", 3), ("colour", "red"),
        ]
        for field, value in wrong:
            self.assertNotEqual(agents.check_field(field, value), "", (field, value))

    def test_the_table_lists_every_row_and_an_override_for_no_agent(self):
        found = agents.table({"deploy": {"name": "Nobody"}})
        self.assertEqual([r["key"] for r in found["rows"]][:3], ["idea", "intent", "spec"])
        self.assertEqual(len(found["rows"]), 11)
        self.assertEqual(found["problems"], ["agent:deploy: no agent called 'deploy', ignored"])


class StringsBuiltFromARow(unittest.TestCase):
    def test_the_label_address_and_attribution_have_one_form(self):
        row = agent_for("impl")
        self.assertEqual(agents.label(row), "Uruz (agent, impl)")
        self.assertEqual(agents.address(row), "uruz@agents.coscc.invalid")
        # Exactly the JSON `spike.md ## U4` measured.
        self.assertEqual(json.loads(agents.settings_json(row)), {"attribution": {
            "commit": "Co-authored-by: Uruz (agent, impl) <uruz@agents.coscc.invalid>",
            "pr": "Uruz (agent, impl)",
        }})
        self.assertEqual(list(json.loads(agents.settings_json(row))), ["attribution"])

    def test_the_identity_section_drops_empty_fields(self):
        review = agents.identity_section(agent_for("review"))
        self.assertTrue(review.startswith("# Who you are\n\n"))
        self.assertIn(
            "You are ᛏ Tiwaz (Tyr: justice and judgement), the agent of the review stage.\n"
            "Your role: Judges the change and never edits code.", review,
        )
        self.assertIn("write exactly `Tiwaz (agent, review)`", review)
        self.assertIn("not a person, and it grants nothing", review)
        gebo = agents.identity_section(agent_for("integrate"))
        self.assertIn("You are ᚷ Gebo, the agent of the integrate stage.", gebo)
        self.assertNotIn("()", gebo)
        self.assertNotIn("Your role", gebo)

    def test_an_old_record_without_agent_is_named_from_its_stage(self):
        self.assertEqual(agents.of_record({"stage": "plan", "agent": "Wayfarer"}), "Wayfarer")
        self.assertEqual(agents.of_record({"stage": "plan"}), "Raidho")
        self.assertEqual(agents.of_record({"stage": "plan"}, {"plan": {"name": "Road"}}), "Road")
        self.assertEqual(agents.of_record({"stage": "rebase"}), "")
        self.assertEqual(agents.of_record({}), "")

    def test_author_of_reads_the_last_author_above_answers(self):
        read = agents.author_of
        self.assertEqual(read("# Idea: x\nAuthor: Ingwaz (agent, idea). Status: accepted.\n"), "Ingwaz (agent, idea)")
        self.assertEqual(
            read("# Plan\nIntent: intent.md. Spec: spec.md. Author: Raidho (agent, plan). "
                 "Status: accepted. Impl: novel.\n"),
            "Raidho (agent, plan)",
        )
        self.assertEqual(read("Spec: spec.md. Author: ᛈ Perthro. Round: 3. Status: accepted."), "ᛈ Perthro")
        rounds = (
            "# Review\n\n## Round 1\nPR: pr.md. Author: Tiwaz (agent, review). Concluded by: one. Status: x.\n"
            "\n## Round 2\nPR: pr.md. Author: Judge (agent, review). Concluded by: two. Status: pass.\n"
        )
        self.assertEqual(read(rounds), "Judge (agent, review)")
        answered = "Author: Kenaz (agent, spec). Status: accepted.\n\n## Answers\n\n### Câu 1\nAuthor: Someone. x\n"
        self.assertEqual(read(answered), "Kenaz (agent, spec)")
        self.assertEqual(read("# Impl\nno header here\n"), "")
        self.assertEqual(read("Author: Uruz (agent, impl)"), "Uruz (agent, impl)")


if __name__ == "__main__":
    unittest.main()
