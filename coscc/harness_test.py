"""Tests for where the rules are found, and for the check that would have caught v0.2.2.

The wheel tests build real zip files rather than mocking `zipfile`, because what is being
tested is an agreement about **path strings inside an archive** — and a mock agrees with
whatever it was told.
"""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from coscc import frontend, harness
from coscc.harness import MissingRules

REPO = Path(__file__).resolve().parent.parent


def _wheel(path: Path, names) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, "x")
    return path


RUNNABLE = (
    f"coscc/_web/{frontend._LAYOUT.as_posix()}/index.html",
    f"coscc/_web/{frontend.MARKER.as_posix()}",
    "coscc/_harness/scripts/cos.mjs",
    "coscc/_harness/skills/write-spec/SKILL.md",
    "coscc/__init__.py",
)


class TheCheckoutFindsItsOwnRules(unittest.TestCase):
    def test_this_checkout_resolves_cos_mjs_and_every_skill(self):
        # Running from a checkout, `root()` is `.claude/`. If this fails, the fallback is
        # broken and every developer's Board is broken with it.
        self.assertTrue(harness.script().is_file(), harness.script())
        found = sorted(p.parent.name for p in harness.skills_dir().glob(f"*/{harness.SKILL_FILE}"))
        self.assertIn("write-spec", found)

    def test_the_resolved_script_is_the_one_the_repo_commits(self):
        self.assertEqual(harness.script(), REPO / ".claude" / "scripts" / "cos.mjs")


class RulesThatCannotBeFoundStopTheStep(unittest.TestCase):
    def test_read_skill_raises_rather_than_returning_empty(self):
        # The whole of `spec.md` R4. An empty string here is what let a step run, spend
        # quota, and leave a record identical to a step that had its rules.
        with self.assertRaises(MissingRules):
            harness.read_skill("write-no-such-stage", "no-such-stage")

    def test_the_refusal_names_every_path_it_looked_at(self):
        with self.assertRaises(MissingRules) as caught:
            harness.read_skill("write-nope", "nope")
        message = str(caught.exception)
        self.assertIn("write-nope", message)
        self.assertIn("nope", message)
        self.assertIn(str(harness.skills_dir()), message)

    def test_the_first_name_that_exists_wins(self):
        self.assertIn("# Write a spec", harness.read_skill("write-spec", "spec"))


class ThePackagedCopyWins(unittest.TestCase):
    def test_root_prefers_the_package_when_cos_mjs_is_there(self):
        with tempfile.TemporaryDirectory() as tmp:
            packaged = Path(tmp) / "_harness"
            (packaged / "scripts").mkdir(parents=True)
            (packaged / "scripts" / "cos.mjs").write_text("// packaged", encoding="utf-8")
            original = harness.PACKAGE_HARNESS
            harness.PACKAGE_HARNESS = packaged
            try:
                self.assertTrue(harness.is_packaged())
                self.assertEqual(harness.root(), packaged)
                self.assertEqual(harness.script().read_text(encoding="utf-8"), "// packaged")
            finally:
                harness.PACKAGE_HARNESS = original

    def test_a_package_without_cos_mjs_falls_back_rather_than_half_resolving(self):
        # A packaged tree that exists but is empty must not win. Half a harness resolving
        # is worse than none: it would find no skills and blame the checkout.
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "_harness"
            empty.mkdir()
            original = harness.PACKAGE_HARNESS
            harness.PACKAGE_HARNESS = empty
            try:
                self.assertFalse(harness.is_packaged())
                self.assertEqual(harness.root(), harness.CHECKOUT_HARNESS)
            finally:
                harness.PACKAGE_HARNESS = original


class AWheelIsChecked(unittest.TestCase):
    def test_a_complete_wheel_has_nothing_wrong_with_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            wheel = _wheel(Path(tmp) / "ok.whl", RUNNABLE)
            self.assertEqual(harness.wheel_complaints(wheel), [])

    def test_the_v0_2_2_shape_is_caught(self):
        # Exactly what shipped: frontend present, harness absent. Three releases passed
        # every check there was, and this is the check there was not.
        with tempfile.TemporaryDirectory() as tmp:
            names = [n for n in RUNNABLE if "_harness" not in n]
            wheel = _wheel(Path(tmp) / "v022.whl", names)
            complaints = harness.wheel_complaints(wheel)
            self.assertEqual(len(complaints), 2, complaints)
            self.assertTrue(any("cos.mjs" in c for c in complaints), complaints)
            self.assertTrue(any("SKILL.md" in c for c in complaints), complaints)

    def test_a_wheel_with_no_frontend_is_caught_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            names = [n for n in RUNNABLE if "_web" not in n]
            wheel = _wheel(Path(tmp) / "noweb.whl", names)
            complaints = harness.wheel_complaints(wheel)
            self.assertEqual(len(complaints), 2, complaints)

    def test_skills_are_counted_not_named(self):
        # `spec.md` C4: nine is today's number. A tenth skill must not need this file edited.
        with tempfile.TemporaryDirectory() as tmp:
            names = list(RUNNABLE) + ["coscc/_harness/skills/write-tenth/SKILL.md"]
            wheel = _wheel(Path(tmp) / "ten.whl", names)
            self.assertEqual(harness.wheel_complaints(wheel), [])

    def test_a_wheel_carrying_settings_is_refused(self):
        # `plan.md` Risk 6: the copy step takes two named directories, never `.claude/`
        # whole, and a wheel is published.
        with tempfile.TemporaryDirectory() as tmp:
            names = list(RUNNABLE) + ["coscc/_harness/settings.local.json"]
            wheel = _wheel(Path(tmp) / "leak.whl", names)
            complaints = harness.wheel_complaints(wheel)
            self.assertTrue(any("settings" in c for c in complaints), complaints)

    def test_something_that_is_not_a_wheel_is_one_complaint_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            junk = Path(tmp) / "not.whl"
            junk.write_text("not a zip", encoding="utf-8")
            complaints = harness.wheel_complaints(junk)
            self.assertEqual(len(complaints), 1)
            self.assertIn("could not be read as a wheel", complaints[0])


if __name__ == "__main__":
    unittest.main()
