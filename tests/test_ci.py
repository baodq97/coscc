"""CI runs the command list `npm run ci` runs, and no other.

`pr.yml` keeps the set-up; every command it runs is an `npm run` call into `package.json`, and
impl runs `npm run ci` itself. A command on one side and not the other is red here."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = json.loads((REPO / "package.json").read_text())["scripts"]
WORKFLOW = (REPO / ".github" / "workflows" / "pr.yml").read_text()


def expand(command: str) -> list[str]:
    """The leaf commands of `command`, `npm run <script>` replaced by that script, in order."""
    out: list[str] = []
    for part in (p.strip() for p in command.split("&&")):
        m = re.fullmatch(r"npm run (\S+)", part)
        out += expand(SCRIPTS[m.group(1)]) if m else [part]
    return out


def workflow_commands(text: str = WORKFLOW) -> list[str]:
    runs = re.findall(r"^\s*(?:- )?run: (.+)$", text, re.M)
    return [leaf for run in runs for leaf in expand(run)]


class CiRunsTheListImplRuns(unittest.TestCase):
    def test_every_command_of_the_workflow_is_in_the_list_and_back(self):
        theirs, ours = workflow_commands(), expand("npm run ci")
        self.assertEqual(sorted(theirs), sorted(ours))

    def test_the_list_holds_what_the_workflow_ran_before(self):
        ours = expand("npm run ci")
        for need in (
            "uv sync --frozen",
            "uv run python -m coscc.loop check-version",
            "npm test",
            "npm --prefix ui ci --no-audit --no-fund",
            "npm --prefix ui run check",
            "npm --prefix ui test",
            "npm --prefix ui run build",
            "uv run python -m coscc.loop check-branch $HEAD_REF",
        ):
            self.assertIn(need, ours)

    def test_a_command_dropped_from_the_workflow_is_red(self):
        cut = WORKFLOW.replace("      - run: npm run ci:tests\n", "")
        self.assertNotEqual(sorted(workflow_commands(cut)), sorted(expand("npm run ci")))

    def test_a_command_dropped_from_the_list_is_red(self):
        theirs = workflow_commands()
        ours = [c for c in expand("npm run ci") if c != "npm --prefix ui run build"]
        self.assertNotEqual(sorted(theirs), sorted(ours))


if __name__ == "__main__":
    unittest.main()
