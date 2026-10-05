"""Tests for the reader that hands a session its project's instructions."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coscc.agent import instructions


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
        "---\npaths: [\"src/**\", 'lib/*.py']\n---\n\nCANARY-INLINE\n", encoding="utf-8"
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
        self.assertIn(
            "- .claude/rules/deep/c-block.md (paths: coscc/**, scripts/*.py):", self.got.text
        )
        self.assertTrue(self.got.text.endswith("one of these patterns."))

    def test_a_front_matter_it_cannot_read_puts_the_rule_in_whole(self):
        self.assertIn("CANARY-BROKEN", self.got.text)
        self.assertIn("CANARY-UNCLOSED", self.got.text)

    def test_the_local_file_is_left_out(self):
        self.assertNotIn("CANARY-LOCAL", self.got.text)
        self.assertNotIn("CLAUDE.local.md", self.got.text)

    def test_an_unreadable_file_is_named_and_not_included(self):
        self.assertNotIn("CANARY-LATIN1", self.got.text)
        self.assertIn(".claude/rules/f-latin1.md (unreadable)", self.got.record()["verbatim"])

    def test_a_parent_directory_is_not_read(self):
        child = self.root / "sub"
        child.mkdir()
        self.assertEqual(instructions.read(child), instructions.Instructions())


if __name__ == "__main__":
    unittest.main()
