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
SKILLS = (REPO / ".claude" / "skills", REPO / "coscc" / "packs" / "coscc-sdlc" / "skills")
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


class TheHarnessIsGenericAndTimeless(unittest.TestCase):
    """A skill runs in any repository the app serves; a rule or doc that names a line, a date or
    what changed reads as a log and goes stale."""

    def test_no_skill_names_this_repository_or_another_skill(self):
        skills = sorted(p for d in SKILLS for p in d.glob("*/SKILL.md"))
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


def _tree(*patterns: str) -> list[str]:
    return sorted(
        p.relative_to(REPO).as_posix() for g in patterns for p in REPO.glob(g) if p.is_file()
    )


def _is_test(path: str) -> bool:
    return re.search(r"\.test\.tsx?$", path) is not None


class TheUiStandardCoversEveryScreen(unittest.TestCase):
    """Only a file that draws a screen, or the words and times on it, makes a unit a UI unit to
    `coscc/units/board.py`; data and test files do not, and a `.ts` file is matched or named in
    the standard's "Not a screen" list."""

    def setUp(self):
        text = UI.read_text(encoding="utf-8")
        self.globs = probe.parse_standard(text)
        self.body = text.split("\n---\n", 1)[1]

    def matched(self, path: str) -> bool:
        return any(probe.glob_match(g, path) for g in self.globs)

    def test_the_globs_are_the_ones_the_loop_reads(self):
        text = UI.read_text(encoding="utf-8")
        self.assertEqual(self.globs, scoped_patterns(text))
        self.assertTrue(self.globs)

    def test_data_and_test_files_are_not_screens(self):
        tests = _tree(
            "ui/src/**/*.test.ts", "ui/src/**/*.test.tsx", "coscc/features/*/ui/**/*.test.*"
        )
        self.assertIn("ui/src/lib/lib.test.ts", tests)
        for path in ["ui/src/lib/boards.ts", "ui/src/lib/stream.ts", *tests]:
            with self.subTest(path=path):
                self.assertFalse(self.matched(path))

    def test_every_screen_file_is_matched(self):
        screens = _tree(
            "ui/src/**/*.tsx",
            "ui/src/styles.css",
            "ui/src/lib/format.ts",
            "ui/index.html",
            "coscc/http/auth.py",
            "coscc/features/*/ui/**/*",
        )
        screens = [p for p in screens if not _is_test(p)]
        self.assertIn("ui/src/screens/UnitPage.tsx", screens)
        self.assertIn("coscc/features/vault/ui/index.tsx", screens)
        self.assertEqual(
            [p for p in screens if not self.matched(p)],
            [],
            "add a glob for the new file to `paths:` in `.claude/rules/ui-standard.md`",
        )

    def test_every_ts_file_is_matched_or_named_as_not_a_screen(self):
        files = [p for p in _tree("ui/src/**/*.ts") if not _is_test(p)]
        self.assertIn("ui/src/lib/format.ts", files)
        outside = [p for p in files if not self.matched(p) and f"`{p}`" not in self.body]
        self.assertEqual(
            outside,
            [],
            "match the new `.ts` in `paths:` if it draws or words a screen, else list it under "
            '"Not a screen" in `.claude/rules/ui-standard.md` with a reason',
        )

    def test_a_listed_file_is_not_also_matched(self):
        for path in re.findall(r"`(ui/src/[\w./-]+\.ts)`", self.body):
            with self.subTest(path=path):
                self.assertFalse(self.matched(path))
