"""Each package imports only the packages below it, so a change is read downwards.

`LAYERS` runs from the top down. A module imports its own package and anything on a lower
line, never a line above or its own line's neighbour (a feature reaches `plugin`, its neighbour, by design). An import inside a function counts: it
hides a cycle, it does not remove one. Inside a package no module reaches another that reaches
it back; an `if TYPE_CHECKING:` import is not read. `tests/` is not checked; a test may reach
anything.

A feature (`coscc/features/<name>.py`, ending in one `PLUGIN`) is a plug-in, so three more rules:
it imports only `coscc.plugin`, `coscc.bus`, `coscc.service.common` and packages below `service`;
only `coscc/api.py` and `coscc/screens/__init__.py` import `coscc.features`, as
`from coscc import features`; and it is at most 3 files of at most 800 lines. Each check takes
text or a listing, so a test can feed it a planted case.
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
    ("features", "plugin"),
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
            if (own, top) == ("features", "plugin"):
                continue
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


FEATURE_MAY_IMPORT = {"coscc.plugin", "coscc.bus", "coscc.service.common"}
FEATURE_FILES = 3
FEATURE_LINES = 800
FEATURE_READERS = {"api.py", "screens/__init__.py"}


def _coscc_imports(tree: ast.AST):
    """`(line, dotted module, plain)` of every `coscc` import at any depth; `from coscc import x`
    is `coscc.x`, and `plain` is true for that form."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found = [(a.name, False) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found = (
                [(node.module, False)]
                if node.module != "coscc"
                else [(f"coscc.{a.name}", True) for a in node.names]
            )
        else:
            continue
        for name, plain in found:
            if name.split(".")[0] == "coscc" and "." in name:
                yield node.lineno, name, plain


def feature_import_problems(sources: dict[str, str]) -> list[str]:
    """`sources` maps a path under `coscc/` (`features/notices.py`) to its text."""
    out = []
    for path, text in sorted(sources.items()):
        tree = ast.parse(text)
        is_feature = path.startswith("features/") and path != "features/__init__.py"
        for line, name, plain in _coscc_imports(tree):
            at = f"coscc/{path}:{line} imports {name}"
            if is_feature:
                if (
                    name in FEATURE_MAY_IMPORT
                    or LAYER.get(name.split(".")[1], -1) > LAYER["service"]
                ):
                    continue
                if name.startswith("coscc.features"):
                    fix = "features talk through `coscc/bus.py`, never by import"
                else:
                    fix = (
                        "a feature gets the running app only through `Ctx` in coscc/plugin.py: "
                        "add what it needs there"
                    )
                out.append(f"{at}: {fix}.")
            elif name.startswith("coscc.features") and not path.startswith("features/"):
                if path not in FEATURE_READERS:
                    out.append(
                        f"{at}: only coscc/api.py and coscc/screens/__init__.py know the list of "
                        "features. Get what you need through `Ctx` in coscc/plugin.py."
                    )
                elif name != "coscc.features" or not plain:
                    out.append(f"{at}: import the list as `from coscc import features`.")
    return out


def feature_size_problems(lines: dict[str, int]) -> list[str]:
    """`lines` maps each path under `coscc/features/` but `__init__.py` to its line count."""
    entries: dict[str, set[str]] = {}
    out = []
    for path, n in sorted(lines.items()):
        head = path.split("/")[0]
        entries.setdefault(head.split(".")[0], set()).add(head)
        if n > FEATURE_LINES:
            out.append(
                f"coscc/features/{path} has {n} lines, above {FEATURE_LINES}: cut it, or move "
                "part of it into a second feature."
            )
    for name, found in sorted(entries.items()):
        if len(found) > FEATURE_FILES:
            out.append(
                f"feature {name} is {len(found)} files, above {FEATURE_FILES}: a feature is "
                f"{name}.py, {name}.md and at most one more. Fold the extra file in."
            )
    return out


def _sources() -> dict[str, str]:
    return {p.relative_to(ROOT).as_posix(): p.read_text() for p in _files()}


def _feature_lines() -> dict[str, int]:
    return {
        p.relative_to(ROOT / "features").as_posix(): len(p.read_text().splitlines())
        for p in sorted((ROOT / "features").rglob("*"))
        if p.is_file() and p.name != "__init__.py" and "__pycache__" not in p.parts
    }


class PackagesSitInLayers(unittest.TestCase):
    def test_every_package_has_a_layer(self):
        self.assertEqual(sorted({_top(p) for p in _files()} - set(LAYER)), [])

    def test_no_import_goes_up_or_sideways(self):
        self.assertEqual(violations(), [])

    def test_no_module_and_a_module_of_its_package_import_each_other(self):
        self.assertEqual(import_cycles(), [])


class FeaturesAreAddedAndRemovedWithoutReachingIn(unittest.TestCase):
    def test_the_features_keep_the_three_rules(self):
        self.assertEqual(feature_import_problems(_sources()), [])
        self.assertEqual(feature_size_problems(_feature_lines()), [])

    def test_a_feature_that_imports_the_app_is_told_to_use_ctx(self):
        for src in ("from coscc.service.steps import Steps\n", "from coscc import state\n"):
            (msg,) = feature_import_problems({"features/a.py": src})
            self.assertIn("only through `Ctx` in coscc/plugin.py: add what it needs there", msg)

    def test_a_feature_that_imports_a_feature_is_told_to_use_the_bus(self):
        for src in ("from coscc.features import b\n", "from coscc.features.b import x\n"):
            (msg,) = feature_import_problems({"features/a.py": src})
            self.assertIn("features talk through `coscc/bus.py`, never by import", msg)

    def test_a_feature_may_import_the_door_the_bus_and_lower_packages(self):
        src = "from coscc.plugin import Ctx\nfrom coscc.bus import Bus\nfrom coscc.data import Data\nfrom coscc.service.common import Invalid\n"
        self.assertEqual(feature_import_problems({"features/a.py": src}), [])

    def test_only_the_api_and_the_shell_import_the_list_and_only_one_way(self):
        ok = "from coscc import features\n"
        self.assertEqual(feature_import_problems({"api.py": ok, "screens/__init__.py": ok}), [])
        (msg,) = feature_import_problems({"service/steps.py": ok})
        self.assertIn("only coscc/api.py and coscc/screens/__init__.py", msg)
        (msg,) = feature_import_problems({"api.py": "from coscc.features import FEATURES\n"})
        self.assertIn("import the list as `from coscc import features`", msg)

    def test_a_feature_of_four_files_or_a_long_one_says_the_fix(self):
        (msg,) = feature_size_problems(
            {"a.py": 1, "a.md": 1, "a/x.py": 1, "a/y.txt": 1, "a.txt": 1}
        )
        self.assertIn("feature a is 4 files, above 3", msg)
        (msg,) = feature_size_problems({"a.py": 801})
        self.assertIn("801 lines, above 800", msg)
        self.assertEqual(feature_size_problems({"a.py": 800, "a.md": 800, "b.py": 5}), [])


if __name__ == "__main__":
    unittest.main()
