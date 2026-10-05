"""`coscc/features/parallel.py`: reading `## Parallelization`, and impl's block."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from coscc.features import parallel
from coscc.kernel import Facts

THREE = """# Plan: x
Intent: intent.md. Status: accepted.

## Files that change
- coscc/a.py

## Parallelization
(a) Feature `parallel`
- coscc/features/parallel.py (new)
- `coscc/features/__init__.py`
- tests/features/test_parallel.py (new)

Báo cáo: gồm hai phần.
- Bộ đọc trả `[{name, paths, report}]`.
- `Block` chỉ render ở stage impl.

(b) Luật của write-plan
- .claude/skills/write-plan/SKILL.md
Report: the new rule.

(c) Tests
- tests/x/*.py
Report: green.

## Notes
- not/a/path.py
"""


def _facts(directory: Path, stage: str = "impl") -> Facts:
    return mock.Mock(spec=Facts, stage=stage, directory=directory)


class ThePlanIsReadIntoItsSteps(unittest.TestCase):
    def test_three_steps_each_with_its_own_paths_and_report(self):
        found = parallel.steps(THREE)
        self.assertEqual(
            [s["name"] for s in found],
            ["(a) Feature `parallel`", "(b) Luật của write-plan", "(c) Tests"],
        )
        self.assertEqual(
            [s["paths"] for s in found],
            [
                [
                    "coscc/features/parallel.py",
                    "coscc/features/__init__.py",
                    "tests/features/test_parallel.py",
                ],
                [".claude/skills/write-plan/SKILL.md"],
                ["tests/x/*.py"],
            ],
        )
        self.assertEqual(
            found[0]["report"],
            "Báo cáo: gồm hai phần.\n- Bộ đọc trả `[{name, paths, report}]`.\n"
            "- `Block` chỉ render ở stage impl.",
        )
        self.assertEqual(
            [s["report"] for s in found[1:]], ["Report: the new rule.", "Report: green."]
        )

    def test_one_session_or_no_section_is_no_step(self):
        self.assertEqual(parallel.steps("# Plan\n\n## Parallelization\nnone: one session\n"), [])
        self.assertEqual(parallel.steps("# Plan\n\n## Parallelization\nNone: one session.\n"), [])
        self.assertEqual(parallel.steps("# Plan\n\n## Files that change\n- a.py\n"), [])


if __name__ == "__main__":
    unittest.main()
