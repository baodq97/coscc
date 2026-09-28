"""Tests for `AgentsMixin` in `coscc/service/agents.py` (`0036` R2)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coscc.config import Config
from coscc.data import Data
from coscc.service import Service
from coscc.service.common import Invalid
from coscc.agent.sessions import Sessions


class AnOverrideIsCheckedSavedAndLogged(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        config = Config(workspaces=(), working_dir=str(root / "work"), data_dir=str(root / "data"))
        (root / "work").mkdir()
        self.service = Service(config, Sessions(config))
        self.data = Data(config.data_dir)

    def _settings(self):
        return [(r["name"], r["old"], r["new"]) for r in self.service._journal().records("", kind="setting")]

    def _rows(self, table):
        return {r["key"]: r for r in table["rows"]}

    def test_a_valid_override_is_saved_and_logged_with_old_and_new(self):
        table = self.service.set_agent("review", {"name": "Judge"})
        row = self._rows(table)["review"]
        self.assertEqual((row["name"], row["source"]["name"], row["overridden"]), ("Judge", "override", True))
        self.assertEqual((row["glyph"], row["source"]["glyph"]), ("ᛏ", "default"))
        self.service.set_agent("review", {"role": "Reads it all."})
        self.assertEqual(self.service._agent("review")["name"], "Judge")
        self.assertEqual(self.service._agent("review")["role"], "Reads it all.")
        self.assertEqual(self._settings(), [
            ("agent:review", None, {"name": "Judge"}),
            ("agent:review", {"name": "Judge"}, {"name": "Judge", "role": "Reads it all."}),
        ])

    def test_a_wrong_field_is_refused_and_nothing_is_written(self):
        wrong = [
            ("review", {"name": "Two words"}), ("review", {"name": "x" * 25}),
            ("review", {"name": "Ümlaut"}), ("review", {"glyph": "abc"}), ("review", {"glyph": "a b"}),
            ("review", {"meaning": "m" * 61}), ("review", {"meaning": "a\nb"}),
            ("review", {"role": "r" * 201}), ("review", {"role": "a\nb"}),
            ("review", {"colour": "red"}), ("review", {"colour": ""}), ("review", {"name": 3}),
            # Another row's name, whatever its case.
            ("review", {"name": "uruz"}), ("review", {"name": "GEBO"}),
            ("deploy", {"name": "Nobody"}), ("", {"name": "Nobody"}), (None, {}),
        ]
        for key, fields in wrong:
            with self.assertRaises(Invalid, msg=(key, fields)):
                self.service.set_agent(key, fields)
        self.assertEqual(self.data.pref_rows("agent:"), {})
        self.assertEqual(self._settings(), [])

    def test_a_name_taken_by_an_override_is_refused_too(self):
        self.service.set_agent("plan", {"name": "Road"})
        with self.assertRaises(Invalid):
            self.service.set_agent("spec", {"name": "road"})
        # A row may keep its own name.
        self.service.set_agent("plan", {"name": "ROAD"})
        self.assertEqual(self.service._agent("plan")["name"], "ROAD")

    def test_the_key_alone_removes_the_rows_override(self):
        self.service.set_agent("impl", {"name": "Builder", "glyph": "ᛒ"})
        self.service.set_agent("impl", {"glyph": ""})
        self.assertEqual(self.service._agent("impl")["glyph"], "ᚢ")
        self.assertEqual(self.service._agent("impl")["name"], "Builder")
        table = self.service.set_agent("impl")
        self.assertFalse(self._rows(table)["impl"]["overridden"])
        self.assertEqual(self.service._agent("impl")["name"], "Uruz")
        self.assertEqual(self.data.pref_rows("agent:"), {})
        self.assertEqual(self._settings()[-1], ("agent:impl", {"name": "Builder"}, None))

    def test_a_broken_stored_override_falls_back_and_is_named(self):
        self.data.set_pref("agent:spec", {"name": "has space", "glyph": "ᚲᚲ"})
        self.data.set_pref("agent:deploy", {"name": "Nobody"})
        table = self.service.agent_table()
        row = self._rows(table)["spec"]
        self.assertEqual((row["name"], row["glyph"]), ("Kenaz", "ᚲᚲ"))
        self.assertEqual(len(table["problems"]), 2, table["problems"])


if __name__ == "__main__":
    unittest.main()
