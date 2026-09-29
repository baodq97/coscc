"""Each package imports only the packages below it, so a change is read downwards.

`LAYERS` runs from the top down. A module imports its own package and anything on a lower
line, never a line above or its own line's neighbour. An import inside a function counts: it
hides a cycle, it does not remove one. `tests/` is not checked; a test may reach anything.
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
    ("auth", "build", "frontend", "ui"),
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


class PackagesSitInLayers(unittest.TestCase):
    def test_every_package_has_a_layer(self):
        self.assertEqual(sorted({_top(p) for p in _files()} - set(LAYER)), [])

    def test_no_import_goes_up_or_sideways(self):
        self.assertEqual(violations(), [])


if __name__ == "__main__":
    unittest.main()
