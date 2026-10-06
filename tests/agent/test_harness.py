"""Tests for the check that would have caught v0.2.2: a wheel that installs and runs nothing.

The wheel tests build real zip files rather than mocking `zipfile`, because what is being
tested is an agreement about **path strings inside an archive** — and a mock agrees with
whatever it was told.
"""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from coscc.agent import harness

REPO = Path(__file__).resolve().parents[2]


STAMP = "coscc/_build.json"
GOOD_STAMP = '{"commit": "' + "0123456789abcdef" * 2 + '01234567"}'


def _wheel(path: Path, names, stamp: str = GOOD_STAMP) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, stamp if name == STAMP else "x")
    return path


PACK = "coscc/packs/coscc-sdlc"
RUNNABLE = (
    f"{PACK}/.claude-plugin/plugin.json",
    f"{PACK}/agents/spec.md",
    f"{PACK}/skills/write-spec/SKILL.md",
    "coscc/_studio/index.html",
    # Committed rather than generated, unlike the studio, and checked anyway: an installed copy
    # cannot tell how a missing file came to be missing.
    "coscc/units/states.json",
    "coscc/units/lanes.json",
    # The commit the board shows is read from here.
    STAMP,
    "coscc/__init__.py",
)


class AWheelIsChecked(unittest.TestCase):
    def test_a_complete_wheel_has_nothing_wrong_with_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            wheel = _wheel(Path(tmp) / "ok.whl", RUNNABLE)
            self.assertEqual(harness.wheel_complaints(wheel), [])

    def test_the_v0_2_2_shape_is_caught(self):
        # Exactly what shipped: page present, skills absent. Three releases passed
        # every check there was, and this is the check there was not.
        with tempfile.TemporaryDirectory() as tmp:
            names = [n for n in RUNNABLE if "/skills/" not in n]
            wheel = _wheel(Path(tmp) / "v022.whl", names)
            complaints = harness.wheel_complaints(wheel)
            self.assertEqual(len(complaints), 1, complaints)
            self.assertIn("SKILL.md", complaints[0])

    def test_a_wheel_without_the_agents_rows_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            for gone in ("/agents/", "plugin.json"):
                with self.subTest(gone=gone):
                    names = [n for n in RUNNABLE if gone not in n]
                    complaints = harness.wheel_complaints(_wheel(Path(tmp) / "w.whl", names))
                    self.assertEqual(len(complaints), 1, complaints)
                    self.assertIn("no agent would run", complaints[0])

    def test_a_wheel_without_the_studio_is_caught(self):
        # The page would answer 503 on an install that is otherwise whole.
        with tempfile.TemporaryDirectory() as tmp:
            names = [n for n in RUNNABLE if "_studio" not in n]
            wheel = _wheel(Path(tmp) / "nostudio.whl", names)
            complaints = harness.wheel_complaints(wheel)
            self.assertEqual(len(complaints), 1, complaints)
            self.assertIn("_studio/index.html", complaints[0])

    def test_a_wheel_without_the_state_set_is_caught(self):
        # `coscc/units/states.py` has nothing to validate a transition against, so the log can
        # neither be read nor written. The wheel installs and the page renders.
        with tempfile.TemporaryDirectory() as tmp:
            names = [n for n in RUNNABLE if not n.endswith("states.json")]
            wheel = _wheel(Path(tmp) / "nostates.whl", names)
            complaints = harness.wheel_complaints(wheel)
            self.assertEqual(len(complaints), 1, complaints)
            self.assertIn("states.json", complaints[0])

    def test_skills_are_counted_not_named(self):
        # Nine is today's number. A tenth skill must not need this file edited.
        with tempfile.TemporaryDirectory() as tmp:
            names = list(RUNNABLE) + [f"{PACK}/skills/write-tenth/SKILL.md"]
            wheel = _wheel(Path(tmp) / "ten.whl", names)
            self.assertEqual(harness.wheel_complaints(wheel), [])

    def test_a_wheel_without_a_build_stamp_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            names = [n for n in RUNNABLE if n != STAMP]
            complaints = harness.wheel_complaints(_wheel(Path(tmp) / "w.whl", names))
            self.assertEqual(len(complaints), 1, complaints)
            self.assertIn("_build.json", complaints[0])

    def test_a_build_stamp_without_a_full_commit_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            for bad in (
                '{"commit": "abc1234"}',
                '{"commit": null}',
                "not json",
                "[]",
                '{"commit": "' + "G" * 40 + '"}',
            ):
                with self.subTest(stamp=bad):
                    wheel = _wheel(Path(tmp) / "w.whl", RUNNABLE, stamp=bad)
                    complaints = harness.wheel_complaints(wheel)
                    self.assertEqual(len(complaints), 1, complaints)
                    self.assertIn("40-hex", complaints[0])

    def test_something_that_is_not_a_wheel_is_one_complaint_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            junk = Path(tmp) / "not.whl"
            junk.write_text("not a zip", encoding="utf-8")
            complaints = harness.wheel_complaints(junk)
            self.assertEqual(len(complaints), 1)
            self.assertIn("could not be read as a wheel", complaints[0])


if __name__ == "__main__":
    unittest.main()
