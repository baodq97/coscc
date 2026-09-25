"""Tests for the reader that hands a session its project's instructions (`0088` R5, R7, R13).

The fixture puts a canary in every kind of file the reader meets, the way `spike.md ## U6`
did, so each assertion is about a word being present or absent rather than a shape.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coscc import instructions


def plant(root: Path) -> Path:
    """One directory with a canary in each kind of file, and the file each canary names."""
    (root / ".claude" / "rules" / "deep").mkdir(parents=True)
    (root / "canary.txt").write_text("CANARY-IMPORTED\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text("CANARY-ROOT\nSee @canary.txt\n", encoding="utf-8")
    (root / ".claude" / "CLAUDE.md").write_text("CANARY-DOTCLAUDE — ệ\n", encoding="utf-8")
    (root / "CLAUDE.local.md").write_text("CANARY-LOCAL\n", encoding="utf-8")
    rules = root / ".claude" / "rules"
    (rules / "a-plain.md").write_text("CANARY-PLAIN\n", encoding="utf-8")
    (rules / "b-inline.md").write_text(
        '---\npaths: ["src/**", \'lib/*.py\']\n---\n\nCANARY-INLINE\n', encoding="utf-8"
    )
    (rules / "deep" / "c-block.md").write_text(
        '---\ndescription: x\npaths:\n  - "coscc/**"\n  - scripts/*.py\nother: y\n---\n\nCANARY-BLOCK\n',
        encoding="utf-8",
    )
    (rules / "d-broken.md").write_text(
        '---\npaths: ["never", "closed"\n---\n\nCANARY-BROKEN\n', encoding="utf-8"
    )
    (rules / "e-unclosed.md").write_text(
        '---\npaths:\n  - "x/**"\n\nCANARY-UNCLOSED\n', encoding="utf-8"
    )
    (rules / "f-latin1.md").write_bytes("CANARY-LATIN1 caf\xe9\n".encode("latin-1"))
    return root


class TheReaderTakesOnlyWhatTheProjectHolds(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = plant(Path(self._tmp.name))
        self.got = instructions.read(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_sections_come_in_order(self):
        text = self.got.text
        self.assertTrue(text.startswith("# Project instructions\n\n## CLAUDE.md\n\nCANARY-ROOT"))
        order = [
            "## CLAUDE.md\n",
            "## .claude/CLAUDE.md\n",
            "## .claude/rules/a-plain.md\n",
            "## .claude/rules/d-broken.md\n",
            "## .claude/rules/e-unclosed.md\n",
            "## Scoped rules\n",
        ]
        at = [text.index(heading) for heading in order]
        self.assertEqual(at, sorted(at))
        self.assertEqual(
            self.got.verbatim,
            (
                "CLAUDE.md",
                ".claude/CLAUDE.md",
                ".claude/rules/a-plain.md",
                ".claude/rules/d-broken.md",
                ".claude/rules/e-unclosed.md",
                ".claude/rules/f-latin1.md (unreadable)",
            ),
        )

    def test_a_file_arrives_byte_for_byte(self):
        self.assertIn("## .claude/CLAUDE.md\n\nCANARY-DOTCLAUDE — ệ\n", self.got.text)

    def test_a_scoped_rule_is_a_line_of_contents_and_never_its_body(self):
        self.assertEqual(
            self.got.scoped, (".claude/rules/b-inline.md", ".claude/rules/deep/c-block.md")
        )
        self.assertNotIn("CANARY-INLINE", self.got.text)
        self.assertNotIn("CANARY-BLOCK", self.got.text)
        self.assertIn(
            "- .claude/rules/b-inline.md (paths: src/**, lib/*.py): read this file with Read",
            self.got.text,
        )
        self.assertIn("- .claude/rules/deep/c-block.md (paths: coscc/**, scripts/*.py):", self.got.text)
        self.assertTrue(self.got.text.endswith("one of these patterns."))

    def test_a_front_matter_it_cannot_read_puts_the_rule_in_whole(self):
        self.assertIn("CANARY-BROKEN", self.got.text)
        self.assertIn("CANARY-UNCLOSED", self.got.text)

    def test_the_local_file_is_left_out(self):
        self.assertNotIn("CANARY-LOCAL", self.got.text)
        self.assertNotIn("CLAUDE.local.md", self.got.text)

    def test_an_import_stays_the_words_it_is(self):
        self.assertIn("See @canary.txt\n", self.got.text)
        self.assertNotIn("CANARY-IMPORTED", self.got.text)

    def test_an_unreadable_file_is_named_and_not_included(self):
        self.assertNotIn("CANARY-LATIN1", self.got.text)
        self.assertIn(".claude/rules/f-latin1.md (unreadable)", self.got.record()["verbatim"])

    def test_the_record_is_the_two_lists(self):
        record = self.got.record()
        self.assertEqual(set(record), {"verbatim", "scoped"})
        self.assertEqual(record["scoped"], list(self.got.scoped))

    def test_a_parent_directory_is_not_read(self):
        child = self.root / "sub"
        child.mkdir()
        self.assertEqual(instructions.read(child), instructions.Instructions())


class NothingThereIsNothingSent(unittest.TestCase):
    def test_an_empty_directory_gives_an_empty_text(self):
        with tempfile.TemporaryDirectory() as d:
            got = instructions.read(d)
        self.assertEqual(got.text, "")
        self.assertEqual(got.record(), {"verbatim": [], "scoped": []})

    def test_a_directory_that_does_not_exist_gives_an_empty_text(self):
        self.assertEqual(instructions.read("/p/does/not/exist").text, "")


class ThisCheckout(unittest.TestCase):
    """R7 on the repository itself: its rules are scoped and stay out of the block."""

    def test_every_rule_is_a_contents_line_only(self):
        # `0094` split `coscc-app.md` into one rule per area; every one of them is scoped.
        root = Path(__file__).parents[1]
        got = instructions.read(root)
        rules = tuple(sorted(
            p.relative_to(root).as_posix() for p in (root / ".claude" / "rules").rglob("*.md")
        ))
        self.assertEqual(got.scoped, rules)
        self.assertIn(".claude/rules/coscc-app.md", got.scoped)
        self.assertIn(".claude/rules/ui-standard.md", got.scoped)
        self.assertEqual(got.verbatim, (".claude/CLAUDE.md",))
        self.assertIn("coscc/screens.py", got.text)
        self.assertNotIn("# The UI standard", got.text)
        self.assertNotIn("# The coscc app", got.text)


class FrontMatter(unittest.TestCase):
    def test_the_shapes_it_reads(self):
        read = instructions.scoped_patterns
        self.assertEqual(read('---\npaths: ["a", "b"]\n---\n'), ["a", "b"])
        self.assertEqual(read("---\npaths:\n  - 'a'\n  - b\n---\n"), ["a", "b"])
        self.assertEqual(read("---\npaths: a/**\n---\n"), ["a/**"])

    def test_what_it_does_not_read_is_none(self):
        read = instructions.scoped_patterns
        for text in (
            "no front-matter\npaths: [a]\n",
            "---\ndescription: x\n---\n",
            "---\npaths:\n---\n",
            "---\npaths: []\n---\n",
            "---\npaths: [a\n---\n",
            "---\npaths: [a]\n",
            "",
        ):
            self.assertIsNone(read(text), text)


if __name__ == "__main__":
    unittest.main()
