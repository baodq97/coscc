"""`coscc/agents.py`: the default agent names, as `0051 spec.md ## Answers, câu 2` chose them."""

from __future__ import annotations

import unittest

from coscc.agents import agent_for


class TheTableIsTheOneTheSpecChose(unittest.TestCase):
    def test_all_nine_pairs(self):
        expected = {
            "idea": ("ᛜ", "Ingwaz"),
            "intent": ("ᚾ", "Nauthiz"),
            "spec": ("ᚲ", "Kenaz"),
            "plan": ("ᚱ", "Raidho"),
            "impl": ("ᚢ", "Uruz"),
            "pr": ("ᚨ", "Ansuz"),
            "review": ("ᛏ", "Tiwaz"),
            "ship": ("ᛟ", "Othala"),
            "integrate": ("ᚷ", "Gebo"),
        }
        for stage, (glyph, name) in expected.items():
            self.assertEqual(agent_for(stage), {"glyph": glyph, "name": name}, stage)

    def test_implement_is_impl(self):
        self.assertEqual(agent_for("implement"), agent_for("impl"))
        self.assertEqual(agent_for("implement"), {"glyph": "ᚢ", "name": "Uruz"})

    def test_an_unknown_stage_has_no_agent(self):
        self.assertIsNone(agent_for("deploy"))
        self.assertIsNone(agent_for(""))


if __name__ == "__main__":
    unittest.main()
