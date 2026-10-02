"""The app decides the loop in its own process's language: no file under `coscc/` runs `node`.

The loop is `python -m coscc.loop`; a JavaScript copy of it beside the app is two definitions that
drift. So no `*.py` under `coscc/` names the old script, and `"node"` is handed to a process as an
argument (an element of a list or tuple, or an argument of a call) only by the codegraph feature,
which runs a Node bridge of its own. The impl stage's command list names `node` as a word an agent
may run in a workspace, not one the app runs, so it is not read. Each check takes text, so a test
can feed it a planted case.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "coscc"
# In two halves, so a search of the tree for the name finds no file once it is gone.
SCRIPT = ".".join(("cos", "mjs"))
NODE = {"node", "node.exe"}
# Relative to `coscc/`: may hand `node` to a process.
ALLOWED = ("features/codegraph/", "agent/policy.py")


def problems(rel: str, text: str) -> list[str]:
    """What in `text`, the file `coscc/<rel>`, runs or names the old loop script."""
    found = []
    if SCRIPT in text:
        found.append(f"{rel}: names {SCRIPT}")
    if rel.startswith(ALLOWED):
        return found
    for node in ast.walk(ast.parse(text)):
        held = (
            node.elts
            if isinstance(node, (ast.List, ast.Tuple))
            else [*node.args, *(k.value for k in node.keywords)]
            if isinstance(node, ast.Call)
            else []
        )
        for arg in held:
            if isinstance(arg, ast.Constant) and arg.value in NODE:
                found.append(f"{rel}:{arg.lineno}: hands {arg.value!r} to a process")
    return found


class NothingRunsTheLoopScript(unittest.TestCase):
    def test_no_file_under_coscc_names_or_runs_it(self):
        found = [
            p
            for path in sorted(ROOT.rglob("*.py"))
            for p in problems(path.relative_to(ROOT).as_posix(), path.read_text())
        ]
        self.assertEqual(found, [])

    def test_a_planted_call_is_caught(self):
        self.assertEqual(
            problems("units/x.py", 'run(["node", "a.js"])\n'),
            ["units/x.py:1: hands 'node' to a process"],
        )
        self.assertEqual(
            problems("units/x.py", 'which("node")\n'),
            ["units/x.py:1: hands 'node' to a process"],
        )
        self.assertEqual(
            problems("units/x.py", f'S = "see .claude/scripts/{SCRIPT}"\n'),
            [f"units/x.py: names {SCRIPT}"],
        )

    def test_the_word_alone_and_the_codegraph_bridge_are_not_runs(self):
        self.assertEqual(problems("units/x.py", 'kind = "node"\n'), [])
        self.assertEqual(problems("features/codegraph/graph.py", 'run(["node", "b.js"])\n'), [])


if __name__ == "__main__":
    unittest.main()
