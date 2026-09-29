"""The *Release* panel copies the board's block and decides nothing."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from coscc.state import release as release_state
from coscc.state.release import ReleaseCommit, ReleaseUnit, release_fields

BLOCK = {
    "state": "pr-open",
    "reason": "release pull request #40 is open",
    "last_tag": "v0.1.0",
    "units": [
        {"name": "0001_a", "type": "feat", "pr": 11, "sha": "a" * 40, "subject": "feat: one (#11)"}
    ],
    "unmatched": [{"sha": "c" * 40, "subject": "build(deps): bump (#13)"}],
    "version": "0.2.0",
    "button": "publish",
    "enabled": False,
    "disabled_reason": "required checks are still running: test",
    "pr": 40,
    "workflow": "",
    "workflow_url": "",
    "release_url": "",
}


class TheReleasePanel(unittest.TestCase):
    def test_every_field_is_the_blocks(self):
        got = release_fields(BLOCK)
        self.assertEqual(got["rel_state"], "pr-open")
        self.assertEqual(
            got["rel_units"], [ReleaseUnit(name="0001_a", type="feat", pr="#11", sha="aaaaaaa")]
        )
        self.assertEqual(
            got["rel_unmatched"], [ReleaseCommit(sha="ccccccc", subject="build(deps): bump (#13)")]
        )
        self.assertEqual(
            (got["rel_button"], got["rel_phase"], got["rel_enabled"]),
            ("Merge and tag", "publish", False),
        )
        self.assertEqual(got["rel_disabled_reason"], "required checks are still running: test")
        self.assertEqual((got["rel_version"], got["rel_pr"]), ("0.2.0", "#40"))

    def test_no_block_is_no_panel(self):
        got = release_fields(None)
        self.assertEqual((got["rel_state"], got["rel_button"], got["rel_units"]), ("", "", []))

    def test_no_full_sha_reaches_the_page(self):
        got = release_fields(BLOCK)
        self.assertTrue(all(len(u.sha) == 7 for u in got["rel_units"] + got["rel_unmatched"]))


class ThePress(unittest.TestCase):
    def test_it_runs_in_the_background_and_holds_the_state_only_in_blocks(self):
        # A plain event held the state lock for the whole press.
        tree = ast.parse(Path(release_state.__file__).read_text(encoding="utf-8"))
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "press_release"
        )
        self.assertIn("rx.event(background=True)", [ast.unparse(d) for d in fn.decorator_list])
        loop = next(n for n in ast.walk(fn) if isinstance(n, ast.AsyncFor))
        self.assertFalse([n for n in ast.walk(loop) if isinstance(n, ast.AsyncWith)])


if __name__ == "__main__":
    unittest.main()
