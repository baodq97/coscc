from __future__ import annotations

import unittest

from coscc.agent import agents, pack
from coscc.agent.agents import agent_for


class TheTableIsTheOneTheSpecChose(unittest.TestCase):
    def test_an_unknown_stage_has_no_agent(self):
        self.assertIsNone(agent_for("deploy"))
        self.assertIsNone(agent_for(""))

    def test_a_shipped_row_is_every_field_by_default(self):
        row = agent_for("review")
        self.assertEqual((row["name"], row["glyph"]), ("Tiwaz", "ᛏ"))
        self.assertEqual(set(row["source"].values()), {agents.DEFAULT})

    def test_the_page_lists_the_stage_agents_and_gebo_not_the_helpers(self):
        self.assertTrue(agents.shown("impl"))
        self.assertTrue(agents.shown("integrate"))
        self.assertFalse(agents.shown("scout"))


class OverridesComeFirst(unittest.TestCase):
    def test_an_owner_field_wins_and_the_rest_stay_the_builtins(self):
        pack.write("review", "name", "Judge")
        row = agent_for("review")
        self.assertEqual(row["name"], "Judge")
        self.assertEqual(row["glyph"], "ᛏ")
        self.assertEqual(row["source"]["name"], agents.OVERRIDE)
        self.assertEqual(row["source"]["glyph"], agents.DEFAULT)
        self.assertEqual(agent_for("plan")["name"], "Raidho")

    def test_putting_none_back_restores_the_builtin(self):
        pack.write("review", "name", "Judge")
        pack.write("review", "name", None)
        self.assertEqual(agent_for("review")["source"]["name"], agents.DEFAULT)

    def test_every_rule_of_the_identity_is_packs_at_save(self):
        ok = {"name": "Tiwaz-2", "glyph": "ᛏᛏ", "description": "x" * 200}
        for field, value in ok.items():
            pack.write("review", field, value)
            pack.write("review", field, None)
        wrong = [
            ("name", ""),
            ("name", "2abc"),
            ("name", "a" * 25),
            ("name", "Tïwaz"),
            ("name", "a b"),
            ("name", "Raidho"),
            ("glyph", "abc"),
            ("glyph", "a b"),
            ("glyph", " "),
            ("description", "x" * 201),
            ("description", "a\nb"),
            ("name", 3),
            ("colour", "red"),
        ]
        for field, value in wrong:
            with self.assertRaises(ValueError, msg=(field, value)):
                pack.write("review", field, value)
        self.assertEqual(pack.owner_fields("review"), ({}, ""))


if __name__ == "__main__":
    unittest.main()
