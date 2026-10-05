"""What every session carries of this repository's rules stays small.

`.claude/CLAUDE.md` reaches every session in a checkout, and each rule under `.claude/rules/` at
least as a line of contents (`coscc/agent/instructions.py`). A unit that meets one moves detail down
into `.claude/docs/`, which nothing loads, and points to it — or changes the ceiling here and says
why. Red here is the point: the files grew quietly until now."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from coscc.agent.instructions import scoped_patterns
from coscc.loop import probe

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
    "coscc/service/__init__.py",
    "coscc/runner/step.py",
    "coscc/api.py",
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


class TheScopes(unittest.TestCase):
    def test_coscc_app_keeps_its_scope(self):
        self.assertEqual(scoped_patterns(APP.read_text(encoding="utf-8")), APP_PATHS)

    def test_harness_writing_covers_the_harness(self):
        self.assertEqual(scoped_patterns(WRITING.read_text(encoding="utf-8")), [".claude/**"])

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


class TheUiStandardCoversEveryScreen(unittest.TestCase):
    """A studio file, or a feature's script or page, that the UI standard's globs do not match is
    code it is not loaded for, and a unit that changes only that file is not a UI unit to
    `coscc/units/board.py`."""

    def test_every_screen_file_is_named(self):
        globs = scoped_patterns(UI.read_text(encoding="utf-8")) or []
        screens = [
            p.relative_to(REPO).as_posix()
            for pattern in (
                "ui/src/**/*.tsx",
                "ui/src/**/*.ts",
                "ui/src/*.css",
                "coscc/features/*.js",
                "coscc/features/*/page.py",
            )
            for p in sorted(REPO.glob(pattern))
        ]
        self.assertIn("ui/src/screens/UnitPage.tsx", screens)
        self.assertEqual(
            [m for m in screens if not any(probe.glob_match(g, m) for g in globs)],
            [],
            "add a glob for the new file to `paths:` in `.claude/rules/ui-standard.md`",
        )


class EveryDocIsPointedTo(unittest.TestCase):
    """Tier 4 is reached only by a pointer, so a pointer must lead somewhere and every document must
    have one."""

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


SKILL_LEAKS = re.compile(r"coscc/|\.claude/|scripts/|write-[a-z]+")
CITATION = re.compile(r"[\w./-]+\.(?:py|mjs|md|json|toml|sh|js):\d+")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
LOG_WORDS = re.compile(r"no longer|used to|moved here", re.IGNORECASE)


class TheHarnessIsGenericAndTimeless(unittest.TestCase):
    """A skill runs in any repository the app serves; a rule or doc that names a line, a date or
    what changed reads as a log and goes stale."""

    def test_no_skill_names_this_repository_or_another_skill(self):
        skills = sorted(SKILLS.glob("*/SKILL.md"))
        self.assertTrue(skills)
        for path in skills:
            text = path.read_text(encoding="utf-8")
            own = f"write-{path.parent.name.removeprefix('write-')}"
            for m in SKILL_LEAKS.finditer(text):
                if m.group(0) == own:
                    continue
                with self.subTest(skill=path.parent.name, found=m.group(0)):
                    self.fail(
                        f"{path.parent.name} names `{m.group(0)}`: say it in generic words "
                        "(ask the loop for the gate, a security-sensitive file) or drop it"
                    )

    def test_no_rule_or_doc_reads_as_a_log(self):
        for path in [*sorted(RULES.glob("*.md")), *sorted(DOCS.glob("*.md"))]:
            text = path.read_text(encoding="utf-8")
            for pattern, fix in (
                (CITATION, "name the symbol, not a line: a line moves"),
                (DATE, "drop the date: the rule states an invariant"),
                (LOG_WORDS, "state what holds, not what changed"),
            ):
                for m in pattern.finditer(text):
                    with self.subTest(file=path.name, found=m.group(0)):
                        self.fail(f"{path.name} has `{m.group(0)}`: {fix}")


if __name__ == "__main__":
    unittest.main()
