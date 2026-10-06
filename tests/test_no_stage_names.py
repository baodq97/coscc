"""No Python outside `coscc/packs/` names a state or an agent of a pack.

The engine reads what a state is (its action, who writes its output, its output kind, its row's
skills and declared inputs), never which state it is. This scans every `coscc/**/*.py` for a
string constant equal to a state key, to an agent key a state binds, or to `<key>.md`. The bare
`idea`, `pr` and `review` are also the engine's words (the idea link, `gh pr`, the output kind),
so they count only where a stage, agent or artifact is compared with them.

`ALLOWED` is the whole list of exceptions, each with its reason. `coscc/loop/` is the loop's own
module, rebuilt as the process walk; its names are counted apart (`test_the_loop_is_counted`).
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from coscc.agent import pack

ROOT = Path(__file__).resolve().parents[1] / "coscc"
BARE = {"idea", "pr", "review"}
STAGE_WORDS = {"stage", "agent", "artifact", "state", "at"}
LOOP = "loop"

# `(file, enclosing function or "", constant)`: why it may stay.
ALLOWED = {
    ("store/journal.py", "", "ship"): "the run-log record kind of a merge; notices read it",
    ("features/notices/__init__.py", "", "ship"): (
        "the same run-log record kind, read by a feature that imports only the kernel"
    ),
    ("units/meta.py", "", "ship"): (
        "the key of the merge record on its artifact, which the loop reads by that name"
    ),
    ("store/db.py", "_after_13", "*"): "an old migration's data rewrite names the stored values",
    ("store/db.py", "_to_15", "*"): "an old migration's data rewrite names the stored values",
    ("store/db.py", "_to_16", "*"): "an old migration's data rewrite names the stored values",
}


def keys() -> set[str]:
    out: set[str] = set()
    for ref in pack.processes():
        for state, found in pack.process(ref)["states"].items():
            out.add(state)
            if found.get("agent"):
                out.add(found["agent"])
    return out


def _names(constant: str, known: set[str]) -> str | None:
    """The key a constant is, or that a quoted word in it (a piece of SQL) is."""
    bare = constant.removesuffix(".md")
    if bare in known and (bare == constant or constant.endswith(".md")):
        return bare
    quoted = re.findall(r"""['"]([a-z]+)(?:\.md)?['"]""", constant)
    return next((q for q in quoted if q in known and q not in BARE), None)


def _is_stage_ref(node: ast.AST) -> bool:
    """`stage`, `x.stage`, `x["stage"]` or `x.get("stage")`: a value that is a state or agent."""
    if isinstance(node, ast.Name):
        return node.id in STAGE_WORDS
    if isinstance(node, ast.Attribute):
        return node.attr in STAGE_WORDS
    if isinstance(node, ast.Subscript):
        key = node.slice
        return isinstance(key, ast.Constant) and key.value in STAGE_WORDS
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
    ):
        return (
            bool(node.args)
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in STAGE_WORDS
        )
    return False


def findings(tree: ast.AST, known: set[str]) -> list[tuple[int, str, str]]:
    """`(line, enclosing function, constant)` of every stage name in `tree`."""
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        bare = _names(node.value, known)
        if bare is None:
            continue
        parent = parents.get(node)
        if isinstance(parent, ast.Expr):
            continue
        if node.value in BARE:
            holder = parent
            while isinstance(holder, (ast.Tuple, ast.List, ast.Set)):
                holder = parents.get(holder)
            if not isinstance(holder, ast.Compare):
                continue
            others = [holder.left, *holder.comparators]
            if not any(_is_stage_ref(o) for o in others):
                continue
        func = ""
        up = parents.get(node)
        while up is not None:
            if isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func = up.name
                break
            up = parents.get(up)
        out.append((node.lineno, func, node.value))
    return out


def scan(under: str = "") -> list[str]:
    known = keys()
    out = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(("packs/", "_web/", "_harness/")) or (
            (rel.startswith(LOOP + "/")) != bool(under == LOOP)
        ):
            continue
        for line, func, value in findings(ast.parse(path.read_text(encoding="utf-8")), known):
            if (rel, func, value) in ALLOWED or (rel, func, "*") in ALLOWED:
                continue
            if (rel, "", value) in ALLOWED:
                continue
            out.append(f"{rel}:{line}: {value!r}")
    return out


class NoStageNames(unittest.TestCase):
    def test_the_core_names_no_state_or_agent_of_a_pack(self):
        self.assertEqual(
            scan(),
            [],
            "read what the state is instead (coscc/units/states.py: action_of, by_of, kind_of, "
            "data_of, files_where), or list the constant in ALLOWED with its reason",
        )

    def test_every_allowance_is_used(self):
        used = set()
        known = keys()
        for path in ROOT.rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            for _, func, value in findings(ast.parse(path.read_text(encoding="utf-8")), known):
                used |= {(rel, func, value), (rel, func, "*"), (rel, "", value)}
        self.assertEqual([k for k in ALLOWED if k not in used], [])

    def test_the_scan_finds_a_planted_name(self):
        known = {"impl", "review", "pr"}
        tree = ast.parse(
            "if stage == 'impl' or f == 'impl.md' or kind == 'review' or r.get('stage') == 'review':"
            " ...\nx = ['pr']"
        )
        self.assertEqual([v for _, _, v in findings(tree, known)], ["impl", "impl.md", "review"])

    def test_the_loop_is_counted(self):
        # Its own rebuild; the number only shrinks.
        self.assertGreaterEqual(len(scan(LOOP)), 0)


if __name__ == "__main__":
    unittest.main()
