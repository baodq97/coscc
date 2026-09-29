"""Tests for an idea several units share.

`cos.mjs new-idea` is run for real, for the reason `tests/units/test_units.py:1-7` gives."""

from __future__ import annotations

import tempfile
import unittest

from coscc import units
from coscc.units import CannotCreate, ideas

WS = "/tmp/a-workspace"


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = self._tmp.name


class CreatingAnIdea(Fixture):
    def test_create_idea_writes_with_an_empty_units_section(self):
        made = ideas.create_idea(WS, "one-feature", "  backend adds, frontend calls  ", self.data)
        self.assertEqual(made["id"], "0001_one-feature")
        text = ideas.read_text(made["path"])
        self.assertEqual(
            text,
            "# Idea: one feature\nAuthor: the originator. Status: accepted.\n\n"
            "## In their own words\n\nbackend adds, frontend calls\n\n## Units\n\n",
        )
        self.assertEqual(ideas.read_units(text), [])
        self.assertEqual(ideas.brief_of(text), "backend adds, frontend calls")
        self.assertEqual(ideas.create_idea(WS, "two", "b", self.data)["id"], "0002_two")

    def test_an_idea_leaves_the_unit_numbering_alone(self):
        ideas.create_idea(WS, "one", "b", self.data)
        self.assertEqual(units.create(WS, "first", "", self.data)["unit"], "0001_first")

    def test_a_bad_slug_surfaces_cos_mjs_own_words(self):
        with self.assertRaises(CannotCreate) as caught:
            ideas.create_idea(WS, "Bad_Slug", "b", self.data)
        self.assertIn("Invalid slug", str(caught.exception))

    def test_a_brief_with_headings_of_its_own_is_kept_whole(self):
        """A brief pasted from a markdown file ran only to its first `## `."""
        brief = (
            "Backend adds an endpoint.\n\n## Why\n\nThe frontend needs it.\n\n## Not this\n\nAuth."
        )
        path = ideas.create_idea(WS, "one", brief, self.data)["path"]
        ideas.append_unit(path, "api", "0001_backend")
        text = ideas.read_text(path)
        self.assertEqual(ideas.brief_of(text), brief)
        self.assertEqual(ideas.read_units(text), [{"ref": "api/0001_backend", "depends_on": []}])

    def test_a_brief_holding_the_units_heading_is_refused_before_a_number_is_taken(self):
        with self.assertRaises(CannotCreate) as caught:
            ideas.create_idea(WS, "one", "words\n## Units\n- api/0001_x.", self.data)
        self.assertIn("## Units", str(caught.exception))
        self.assertFalse((units.cos_dir(WS, self.data) / "ideas").exists())

    def test_an_empty_brief_is_refused_before_a_number_is_taken(self):
        with self.assertRaises(CannotCreate):
            ideas.create_idea(WS, "one", "   ", self.data)
        self.assertFalse((units.cos_dir(WS, self.data) / "ideas").exists())


class AppendingAUnit(Fixture):
    def test_append_unit_only_appends(self):
        path = ideas.create_idea(WS, "one", "words", self.data)["path"]
        before = ideas.read_text(path)
        ideas.append_unit(path, "api", "0001_backend")
        ideas.append_unit(path, "proj", "0006_frontend", "api/0001_backend")
        after = ideas.read_text(path)
        self.assertTrue(after.startswith(before), after)
        self.assertEqual(
            after[len(before) :],
            "- api/0001_backend.\n- proj/0006_frontend. Depends on: api/0001_backend.\n",
        )
        self.assertEqual(
            ideas.read_units(after),
            [
                {"ref": "api/0001_backend", "depends_on": []},
                {"ref": "proj/0006_frontend", "depends_on": ["api/0001_backend"]},
            ],
        )

    def test_a_file_without_a_final_newline_gets_one_before_the_line(self):
        path = ideas.create_idea(WS, "one", "words", self.data)["path"]
        with open(path, "a", encoding="utf-8") as f:
            f.write("- api/0001_a.")
        ideas.append_unit(path, "api", "0002_b")
        self.assertTrue(ideas.read_text(path).endswith("- api/0001_a.\n- api/0002_b.\n"))


class References(unittest.TestCase):
    def test_the_grammar(self):
        self.assertEqual(ideas.parse_idea_ref("proj/ideas/0001_one.md"), ("proj", "0001_one"))
        self.assertIsNone(ideas.parse_idea_ref("ideas/0001_one.md"))
        self.assertIsNone(ideas.parse_idea_ref("../ideas/0001_one.md"))
        self.assertIsNone(ideas.parse_idea_ref("proj/ideas/0001_one"))
        self.assertEqual(ideas.parse_unit_ref("api/0001_x"), ("api", "0001_x"))
        self.assertIsNone(ideas.parse_unit_ref("0001_x"))
        self.assertIsNone(ideas.parse_unit_ref("a/b/0001_x"))
        with self.assertRaises(CannotCreate):
            ideas.idea_path(WS, "../x")


if __name__ == "__main__":
    unittest.main()
