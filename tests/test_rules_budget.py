"""What every session carries of this repository's rules stays small.

`.claude/CLAUDE.md` reaches every session in a checkout, and each rule under `.claude/rules/` at
least as a line of contents (`coscc/agent/instructions.py`). A unit that meets one moves detail down
into `.claude/docs/`, which nothing loads, and points to it — or changes the ceiling here and says
why. Red here is the point: the files grew quietly until now."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CLAUDE = REPO / ".claude" / "CLAUDE.md"
RULES = REPO / ".claude" / "rules"
DOCS = REPO / ".claude" / "docs"
SKILLS = REPO / ".claude" / "skills"
APP = RULES / "coscc-app.md"
UI = RULES / "ui-standard.md"
WRITING = RULES / "harness-writing.md"

# Bytes, as `wc -c` counts them. Chosen, not measured.
CLAUDE_MAX = 3_000
APP_MAX = 3_000
AREA_MAX = 1_500
WRITING_MAX = 600
DOC_MAX = 8_000

APP_PATHS = ["coscc/**", "coscc/**/*", "scripts/*.py"]

# Files nearly every unit passes through. A rule scoped to one of them would be read by nearly every
# step, which is what tier 2 already is. The spec's list, not measured.
HOT = {
    "coscc/http/app.py",
    "coscc/runner/step.py",
    "coscc/http/routes.py",
}

DOC_REF = re.compile(r"\.claude/docs/[\w./-]+\.md")


def _areas() -> list[Path]:
    return sorted(p for p in RULES.glob("*.md") if p not in (APP, UI, WRITING))


class TheCeilings(unittest.TestCase):
    def test_claude_md(self):
        self.assertLessEqual(len(CLAUDE.read_bytes()), CLAUDE_MAX)

    def test_coscc_app_md(self):
        self.assertLessEqual(len(APP.read_bytes()), APP_MAX)

    def test_harness_writing_md(self):
        self.assertLessEqual(len(WRITING.read_bytes()), WRITING_MAX)

    def test_each_doc(self):
        self.assertTrue(list(DOCS.glob("*.md")))
        for path in sorted(DOCS.glob("*.md")):
            with self.subTest(doc=path.name):
                self.assertLessEqual(
                    len(path.read_bytes()),
                    DOC_MAX,
                    f"{path.name} is over {DOC_MAX} B: cut what a model knows or the code shows, "
                    "or split by kind of task",
                )

    def test_each_area_rule(self):
        self.assertTrue(_areas())
        for path in _areas():
            with self.subTest(rule=path.name):
                self.assertLessEqual(len(path.read_bytes()), AREA_MAX)


SKILL_LEAKS = re.compile(r"coscc/|\.claude/|scripts/|write-[a-z]+")
CITATION = re.compile(r"[\w./-]+\.(?:py|mjs|md|json|toml|sh|js):\d+")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
LOG_WORDS = re.compile(r"no longer|used to|moved here", re.IGNORECASE)


if __name__ == "__main__":
    unittest.main()
