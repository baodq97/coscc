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


if __name__ == "__main__":
    unittest.main()
