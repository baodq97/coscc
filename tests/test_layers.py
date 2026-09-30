"""Each package imports only the packages below it, so a change is read downwards.

`LAYERS` runs from the top down. A module imports its own package and anything on a lower
line, never a line above or its own line's neighbour. An import inside a function counts: it
hides a cycle, it does not remove one. Inside a package no module reaches another that reaches
it back; an `if TYPE_CHECKING:` import is not read. `tests/` is not checked; a test may reach
anything.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "coscc"

LAYERS = (
    ("coscc", "run"),
    ("screens",),
    ("state",),
    ("api",),
    ("service",),
    ("github", "update"),
    ("runner",),
    ("units",),
    ("git", "runlog"),
    ("agent",),
    ("auth", "build", "bus", "frontend", "ui"),
    ("data",),
    ("config",),
)
LAYER = {name: i for i, line in enumerate(LAYERS) for name in line}


def _files() -> list[Path]:
    return [
        p
        for p in sorted(ROOT.rglob("*.py"))
        if p != ROOT / "__init__.py"
        and not {"_web", "_harness", "__pycache__"} & set(p.relative_to(ROOT).parts)
    ]


def _top(path: Path) -> str:
    return path.relative_to(ROOT).parts[0].removesuffix(".py")


def _imported(tree: ast.AST):
    """`(line, top name)` of every `coscc` module the tree imports, at any depth."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = (
                [node.module] if node.module != "coscc" else [f"coscc.{a.name}" for a in node.names]
            )
        else:
            continue
        for name in names:
            parts = name.split(".")
            if parts[0] == "coscc" and len(parts) > 1:
                yield node.lineno, parts[1]


def violations() -> list[str]:
    out = []
    for path in _files():
        own = _top(path)
        for line, top in _imported(ast.parse(path.read_text())):
            if top != own and LAYER.get(top, -1) <= LAYER[own]:
                out.append(f"{path.relative_to(ROOT.parent)}:{line} {own} imports {top}")
    return out


def _module(path: Path) -> str:
    parts = list(path.relative_to(ROOT.parent).with_suffix("").parts)
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _runtime_imports(tree: ast.AST, modules: set[str]):
    """Every `coscc` module the tree imports outside `if TYPE_CHECKING:`, at any depth."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.If) and ast.unparse(node.test).endswith("TYPE_CHECKING"):
            continue
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for a in node.names:
                sub = f"{node.module}.{a.name}"
                yield sub if sub in modules else node.module
        else:
            yield from _runtime_imports(node, modules)


def import_cycles() -> list[list[str]]:
    """Strongly connected sets of modules of one package that import one another."""
    trees = {_module(p): ast.parse(p.read_text()) for p in _files()}
    inits = {_module(p) for p in _files() if p.name == "__init__.py"}

    def package(module: str) -> str:
        return module if module in inits else module.rsplit(".", 1)[0]

    edges = {
        m: sorted(
            {
                i
                for i in _runtime_imports(tree, set(trees))
                if i in trees and i != m and package(i) == package(m)
            }
        )
        for m, tree in trees.items()
    }
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    found: list[list[str]] = []

    def visit(m: str) -> None:
        index[m] = low[m] = len(index)
        stack.append(m)
        for n in edges[m]:
            if n not in index:
                visit(n)
                low[m] = min(low[m], low[n])
            elif n in stack:
                low[m] = min(low[m], index[n])
        if low[m] == index[m]:
            group = []
            while not group or group[-1] != m:
                group.append(stack.pop())
            if len(group) > 1:
                found.append(sorted(group))

    for m in edges:
        if m not in index:
            visit(m)
    return found


class PackagesSitInLayers(unittest.TestCase):
    def test_every_package_has_a_layer(self):
        self.assertEqual(sorted({_top(p) for p in _files()} - set(LAYER)), [])

    def test_no_import_goes_up_or_sideways(self):
        self.assertEqual(violations(), [])

    def test_no_module_and_a_module_of_its_package_import_each_other(self):
        self.assertEqual(import_cycles(), [])


if __name__ == "__main__":
    unittest.main()
