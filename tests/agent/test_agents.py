from __future__ import annotations

import ast
import json
import re
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
            "review": ("ᛏ", "Tiwaz"),
            "integrate": ("ᚷ", "Gebo"),
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
        for key in ("integrate", "spike"):
            self.assertEqual((agent_for(key)["meaning"], agent_for(key)["role"]), ("", ""), key)
        # Ansuz and Othala went with the `pr` and `ship` sessions.
        for key in ("pr", "ship"):
            self.assertIsNone(agent_for(key), key)

    def test_an_unknown_stage_has_no_agent(self):
        self.assertIsNone(agent_for("deploy"))
        self.assertIsNone(agent_for(""))

    def test_a_broken_default_file_is_a_problem_not_a_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "agents.json"
            bad.write_text("{", encoding="utf-8")
            self.assertEqual(agents.load_defaults(bad)[0], {})
            self.assertIn("not JSON", agents.load_defaults(bad)[1][0])
            bad.write_text(
                json.dumps(
                    {
                        "agents": {
                            "a": {"glyph": "x", "name": "Has space"},
                            "b": {"glyph": "y", "name": "Fine", "role": "two\nlines"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            rows, problems = agents.load_defaults(bad)
            self.assertEqual(list(rows), ["b"])
            self.assertEqual(rows["b"]["role"], "")
            self.assertEqual(len(problems), 3, problems)
            self.assertEqual(agents.load_defaults(Path(tmp) / "none.json")[0], {})


class OverridesComeFirst(unittest.TestCase):
    def test_an_override_wins_field_by_field_and_a_broken_one_falls_back(self):
        overrides, problems = agents.overrides_from(
            {
                "agent:review": _pref({"name": "Judge", "glyph": "a b"}),
                "agent:plan": "not json",
                "agent:spec": _pref("Kenaz"),
                "model:review": _pref("ignored, another prefix"),
            }
        )
        self.assertEqual(overrides, {"review": {"name": "Judge"}})
        self.assertEqual(len(problems), 3, problems)
        row = agent_for("review", overrides)
        self.assertEqual(row["name"], "Judge")
        self.assertEqual(row["glyph"], "ᛏ")
        self.assertEqual(row["source"]["name"], agents.OVERRIDE)
        self.assertEqual(row["source"]["glyph"], agents.DEFAULT)
        self.assertEqual(agent_for("plan", overrides)["name"], "Raidho")

    def test_every_rule_of_but_the_duplicate(self):
        ok = {"name": "Tiwaz-2", "glyph": "ᛏᛏ", "meaning": "x" * 60, "role": "y" * 200}
        for field, value in ok.items():
            self.assertEqual(agents.check_field(field, value), "", field)
        wrong = [
            ("name", ""),
            ("name", "2abc"),
            ("name", "a" * 25),
            ("name", "Tïwaz"),
            ("name", "a b"),
            ("glyph", ""),
            ("glyph", "abc"),
            ("glyph", "a b"),
            ("glyph", " "),
            ("meaning", "x" * 61),
            ("meaning", "a\nb"),
            ("role", "y" * 201),
            ("role", "a\rb"),
            ("name", 3),
            ("colour", "red"),
        ]
        for field, value in wrong:
            self.assertNotEqual(agents.check_field(field, value), "", (field, value))

    def test_the_table_lists_every_row_and_an_override_for_no_agent(self):
        found = agents.table({"deploy": {"name": "Nobody"}})
        self.assertEqual([r["key"] for r in found["rows"]][:3], ["idea", "intent", "spec"])
        self.assertEqual(len(found["rows"]), 8)
        self.assertEqual(found["problems"], ["agent:deploy: no agent called 'deploy', ignored"])


class StringsBuiltFromARow(unittest.TestCase):
    def test_the_label_address_and_attribution_have_one_form(self):
        row = agent_for("impl")
        self.assertEqual(agents.label(row), "Uruz (agent, impl)")
        self.assertEqual(agents.address(row), "uruz@agents.coscc.invalid")
        self.assertEqual(
            json.loads(agents.settings_json(row)),
            {
                "attribution": {
                    "commit": "Co-authored-by: Uruz (agent, impl) <uruz@agents.coscc.invalid>",
                    "pr": "Uruz (agent, impl)",
                }
            },
        )
        self.assertEqual(list(json.loads(agents.settings_json(row))), ["attribution"])

    def test_the_identity_section_drops_empty_fields(self):
        review = agents.identity_section(agent_for("review"))
        self.assertTrue(review.startswith("# Who you are\n\n"))
        self.assertIn(
            "You are ᛏ Tiwaz (Tyr: justice and judgement), the agent of the review stage.\n"
            "Your role: Judges the change and never edits code.",
            review,
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
        self.assertEqual(
            read("# Idea: x\nAuthor: Ingwaz (agent, idea). Status: accepted.\n"),
            "Ingwaz (agent, idea)",
        )
        self.assertEqual(
            read(
                "# Plan\nIntent: intent.md. Spec: spec.md. Author: Raidho (agent, plan). "
                "Status: accepted. Impl: novel.\n"
            ),
            "Raidho (agent, plan)",
        )
        self.assertEqual(
            read("Spec: spec.md. Author: ᛈ Perthro. Round: 3. Status: accepted."), "ᛈ Perthro"
        )
        rounds = (
            "# Review\n\n## Round 1\nPR: pr.md. Author: Tiwaz (agent, review). Concluded by: one. Status: x.\n"
            "\n## Round 2\nPR: pr.md. Author: Judge (agent, review). Concluded by: two. Status: pass.\n"
        )
        self.assertEqual(read(rounds), "Judge (agent, review)")
        answered = "Author: Kenaz (agent, spec). Status: accepted.\n\n## Answers\n\n### Câu 1\nAuthor: Someone. x\n"
        self.assertEqual(read(answered), "Kenaz (agent, spec)")
        self.assertEqual(read("# Impl\nno header here\n"), "")
        self.assertEqual(read("Author: Uruz (agent, impl)"), "Uruz (agent, impl)")


class NoNameIsWrittenAnywhereElse(unittest.TestCase):
    """The eight names and Gebo live in `agents.json` alone. Comments and docstrings are not where
    the app writes a name, so only other string constants are read."""

    NAMES = re.compile(r"\b(Ingwaz|Nauthiz|Kenaz|Raidho|Uruz|Ansuz|Tiwaz|Othala|Gebo)\b")

    @staticmethod
    def _docstrings(tree: ast.AST) -> set[int]:
        out: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = node.body
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                ):
                    out.add(id(body[0].value))
        return out

    def test_no_agent_name_is_written_outside_the_default_file(self):
        root = Path(agents.__file__).resolve().parents[1]
        found = []
        for path in sorted(root.rglob("*.py")):
            if path.name.endswith("_test.py") or "_harness" in path.parts or "_web" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            skip = self._docstrings(tree)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and id(node) not in skip
                ):
                    if self.NAMES.search(node.value):
                        found.append(
                            f"{path.relative_to(root.parent)}:{node.lineno}: {node.value[:60]!r}"
                        )
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
