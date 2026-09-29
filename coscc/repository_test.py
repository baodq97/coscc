"""What this repository says about itself, outside any one module: the tracked files and the
harness's own instructions. Each claim was a proof script's once."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True, check=False
    )


class NoPersonalNameIsPublished(unittest.TestCase):
    """The needle is composed here so that this file never carries it."""

    NEEDLE = "bao" + "do"

    def test_no_tracked_file_outside_the_units_carries_the_authors_name(self):
        out = _git("grep", "-il", self.NEEDLE, "--", ".", ":!.cos")
        self.assertIn(out.returncode, (0, 1), out.stderr)
        self.assertEqual(out.stdout.split(), [])


class TheRecordedNamesAreNotTyped(unittest.TestCase):
    """No line of the harness says a recorded name is typed: no field takes a name a person
    types."""

    FIELDS = ("Answered by", "stopped_by", "`by`", "Recorded by")

    def test_no_line_of_the_harness_says_a_recorded_name_is_typed(self):
        lines = (REPO / ".claude" / "CLAUDE.md").read_text(encoding="utf-8").splitlines()
        bad = [
            f"{i}: {line}"
            for i, line in enumerate(lines, 1)
            if "typed" in line and any(f in line for f in self.FIELDS)
        ]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
