"""`0094` R6, R7, R10, R11: what every session carries of this repository's rules stays small.

`.claude/CLAUDE.md` reaches every session in a checkout, and each rule under
`.claude/rules/` at least as a line of contents (`coscc/instructions.py`). The ceilings are
the spec's own choice, not a measurement (`.cos/0094_*/spec.md` C9). A unit that meets one
moves detail down into `.claude/docs/`, which nothing loads, and points to it — or changes
the ceiling here and says why. Red here is the point (C8): the files grew quietly until now.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from coscc.instructions import scoped_patterns

REPO = Path(__file__).resolve().parents[1]
CLAUDE = REPO / ".claude" / "CLAUDE.md"
RULES = REPO / ".claude" / "rules"
DOCS = REPO / ".claude" / "docs"
APP = RULES / "coscc-app.md"
UI = RULES / "ui-standard.md"

# Bytes, as `wc -c` counts them (R6). Chosen, not measured (spec C9).
CLAUDE_MAX = 8_000
APP_MAX = 12_000
AREA_MAX = 8_000

APP_PATHS = ["coscc/**", "coscc/**/*", "rxconfig.py", "scripts/*.py"]

# R7: files nearly every unit passes through. A rule scoped to one of them would be read by
# nearly every step, which is what tier 2 already is. The spec's list, not measured.
HOT = {"coscc/service.py", "coscc/runner.py", "coscc/state.py", "coscc/screens.py", "coscc/api.py"}

DOC_REF = re.compile(r"\.claude/docs/[\w./-]+\.md")


def _areas() -> list[Path]:
    return sorted(p for p in RULES.glob("*.md") if p not in (APP, UI))


class TheCeilings(unittest.TestCase):
    def test_claude_md(self):
        self.assertLessEqual(len(CLAUDE.read_bytes()), CLAUDE_MAX)

    def test_coscc_app_md(self):
        self.assertLessEqual(len(APP.read_bytes()), APP_MAX)

    def test_each_area_rule(self):
        self.assertTrue(_areas())
        for path in _areas():
            with self.subTest(rule=path.name):
                self.assertLessEqual(len(path.read_bytes()), AREA_MAX)


class TheScopes(unittest.TestCase):
    def test_coscc_app_keeps_its_scope(self):
        self.assertEqual(scoped_patterns(APP.read_text(encoding="utf-8")), APP_PATHS)

    def test_each_area_rule_names_files_and_no_hot_one(self):
        for path in _areas():
            with self.subTest(rule=path.name):
                patterns = scoped_patterns(path.read_text(encoding="utf-8"))
                # No `paths:` would put the whole file into every session's system prompt.
                self.assertTrue(patterns)
                for p in patterns:
                    self.assertFalse(set("*?[") & set(p), p)
                    self.assertNotIn(p, HOT)
                    self.assertTrue((REPO / p).is_file(), p)


class EveryDocIsPointedTo(unittest.TestCase):
    """R10: tier 4 is reached only by a pointer, so a pointer must lead somewhere and every
    document must have one."""

    def _pointers(self) -> set[str]:
        found: set[str] = set()
        for path in [CLAUDE, *sorted(RULES.glob("*.md"))]:
            found |= set(DOC_REF.findall(path.read_text(encoding="utf-8")))
        return found

    def test_every_pointer_names_a_file(self):
        for ref in sorted(self._pointers()):
            with self.subTest(ref=ref):
                self.assertTrue((REPO / ref).is_file())

    def test_every_doc_is_named_somewhere(self):
        pointed = self._pointers()
        docs = sorted(DOCS.glob("*.md"))
        self.assertTrue(docs)
        for doc in docs:
            with self.subTest(doc=doc.name):
                self.assertIn(doc.relative_to(REPO).as_posix(), pointed)


if __name__ == "__main__":
    unittest.main()
