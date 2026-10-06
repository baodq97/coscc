"""`coscc/features/parallel`: impl's block, from the plan record's steps."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.features import parallel
from coscc.kernel import Facts, Grant, Hooks, Plan


def _plan(*steps: tuple[str, list[str], str]) -> Plan:
    return {
        "variant": "routine",
        "files": [p for _, paths, _ in steps for p in paths],
        "steps": [{"title": t, "paths": p, "report": r} for t, p, r in steps],
        "rests_on": [],
    }


def _facts(plan: Plan | None, directory: Path | None = None) -> Facts:
    return mock.Mock(spec=Facts, plan=plan, directory=directory or Path("/nowhere"))


TWO = _plan(
    ("Feature `parallel`", ["coscc/features/parallel.py", "tests/x.py"], "Báo cáo: gồm hai phần."),
    ("Luật của write-plan", [".claude/skills/write-plan/SKILL.md"], "Report: the new rule."),
)


class TheBlockIsTheRecordsSteps(unittest.TestCase):
    def test_two_steps_each_with_their_title_paths_and_report(self):
        block = parallel.render(_facts(TWO))
        self.assertTrue(block.startswith("# The plan's parallel steps"), block)
        self.assertIn("The plan names 2 steps", block)
        self.assertIn(
            "(a) Feature `parallel`\n- coscc/features/parallel.py\n- tests/x.py\n"
            "Báo cáo: gồm hai phần.",
            block,
        )
        self.assertIn(
            "(b) Luật của write-plan\n- .claude/skills/write-plan/SKILL.md\nReport: the new rule.",
            block,
        )

    def test_one_step_no_step_or_no_record_is_no_block(self):
        self.assertEqual(parallel.render(_facts(_plan(("one", ["a.py"], "r")))), "")
        self.assertEqual(parallel.render(_facts(_plan())), "")
        self.assertEqual(parallel.render(_facts(None)), "")

    def test_a_run_whose_grant_holds_no_agent_gets_no_block(self):
        hooks = Hooks(parts=(("parallel", parallel.FEATURE.agent(mock.Mock())),))
        for tools, shown in ((("Read",), False), (("Read", "Agent"), True)):
            with self.subTest(tools=tools):
                facts = mock.Mock(spec=Facts, workspace="/w", grant=Grant(tools=tools))
                self.assertEqual(bool(hooks.blocks_for(facts)), shown)

    def test_a_section_in_plan_md_that_the_record_does_not_hold_is_not_read(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "plan.md").write_text(
                "# Plan\n\n## Parallelization\n(a) x\n- a.py\nReport: r\n\n(b) y\n- b.py\nReport: r\n"
            )
            self.assertEqual(parallel.render(_facts(_plan(), directory=Path(d))), "")


if __name__ == "__main__":
    unittest.main()
