"""Each package imports only the packages below it, so a change is read downwards.

`LAYERS` runs from the top down. A module imports its own package and anything on a lower
line, never a line above or its own line's neighbour. An import inside a function counts: it
hides a cycle, it does not remove one. Inside a package no module reaches another that reaches
it back; an `if TYPE_CHECKING:` import is not read. `tests/` is not checked; a test may reach
anything.

A feature (a folder `coscc/features/<name>/` whose `__init__.py` ends in one `FEATURE`) is a plug-in, so three more rules:
it imports only its own `coscc.features.<name>` and `coscc.kernel`, plus the `KERNEL_GAPS` the kernel does not give yet;
only `coscc/http/app.py` imports `coscc.features`, as
`from coscc import features`; and it is at most 3 files of at most 800 lines, its `ui/` (the
studio's TSX) aside. Each check takes text or a listing, so a test can feed it a planted case.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "coscc"

LAYERS = (
    ("run", "loop"),
    ("http",),
    ("features",),
    ("leif",),
    ("vault",),
    ("github", "update"),
    ("runner",),
    ("kernel",),
    ("units",),
    ("git", "runlog"),
    ("agent",),
    ("bus",),
    ("store",),
    ("config",),
)
# The helper that runs `python -m coscc.loop` in a child process. It imports only `agent` and
# `git`, so a package below the loop may use it; the rest of `coscc.loop` decides on every package.
LOOP_CHILD = "coscc.loop.run"
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
    """`(line, top name, module)` of every `coscc` module the tree imports, at any depth. Of
    `from coscc.loop import x` the module is `coscc.loop.x`: the one child helper is told apart."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = (
                [f"{node.module}.{a.name}" for a in node.names]
                if node.module in ("coscc", "coscc.loop")
                else [node.module]
            )
        else:
            continue
        for name in names:
            parts = name.split(".")
            if parts[0] == "coscc" and len(parts) > 1:
                yield node.lineno, parts[1], name


def violations() -> list[str]:
    out = []
    for path in _files():
        own = _top(path)
        for line, top, name in _imported(ast.parse(path.read_text())):
            if name == LOOP_CHILD:
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


FEATURE_MAY_IMPORT = {"coscc.kernel"}
# Core modules a feature still imports because the kernel does not give it yet. The list only
# shrinks: an entry no feature imports any more fails `test_every_kernel_gap_is_still_used`.
KERNEL_GAPS = {
    "coscc.units.turnstats": "codegraph's turn statistics; codegraph already has its 3 files",
    "coscc.vault": "the vault's store, rules and runner; a feature has at most 3 files",
}
FEATURE_FILES = 3
FEATURE_LINES = 800
FEATURE_READERS = {"http/app.py"}


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
        own = path.split("/")[1].split(".")[0] if is_feature else ""
        for line, name, plain in _coscc_imports(tree):
            at = f"coscc/{path}:{line} imports {name}"
            if is_feature:
                if name in FEATURE_MAY_IMPORT or name in KERNEL_GAPS:
                    continue
                if name == f"coscc.features.{own}" or name.startswith(f"coscc.features.{own}."):
                    continue
                if name.startswith("coscc.features"):
                    fix = "features talk through `coscc/bus.py`, never by import"
                else:
                    fix = "a feature imports only coscc.kernel: add what it needs there"
                out.append(f"{at}: {fix}.")
            elif name.startswith("coscc.features") and not path.startswith("features/"):
                if path not in FEATURE_READERS:
                    out.append(
                        f"{at}: only coscc/http/app.py knows the list of "
                        "features. Get what you need through `Ctx` in coscc/kernel.py."
                    )
                elif name != "coscc.features" or not plain:
                    out.append(f"{at}: import the list as `from coscc import features`.")
    return out


def feature_size_problems(lines: dict[str, int]) -> list[str]:
    """`lines` maps each path under `coscc/features/` but the registry `__init__.py` to its line
    count; a package's own `__init__.py` is one of its files."""
    entries: dict[str, set[str]] = {}
    out = []
    for path, n in sorted(lines.items()):
        entries.setdefault(path.split("/")[0].split(".")[0], set()).add(path)
        if n > FEATURE_LINES:
            out.append(
                f"coscc/features/{path} has {n} lines, above {FEATURE_LINES}: cut it, or move "
                "part of it into a second feature."
            )
    for name, found in sorted(entries.items()):
        if len(found) > FEATURE_FILES:
            out.append(
                f"feature {name} is {len(found)} files, above {FEATURE_FILES}: a feature is "
                f"`{name}/__init__.py`, `{name}/README.md` and at most one more file. "
                "Fold the extra file in."
            )
    return out


def _sources() -> dict[str, str]:
    return {p.relative_to(ROOT).as_posix(): p.read_text() for p in _files()}


def _feature_lines() -> dict[str, int]:
    """Its `ui/` is the studio's layer, built apart, so it is not one of a feature's files."""
    return {
        p.relative_to(ROOT / "features").as_posix(): len(p.read_text().splitlines())
        for p in sorted((ROOT / "features").rglob("*"))
        if p.is_file()
        and p != ROOT / "features" / "__init__.py"
        and "__pycache__" not in p.parts
        and p.relative_to(ROOT / "features").parts[1:2] != ("ui",)
    }


class PackagesSitInLayers(unittest.TestCase):
    def test_every_package_has_a_layer(self):
        self.assertEqual(sorted({_top(p) for p in _files()} - set(LAYER)), [])

    def test_no_import_goes_up_or_sideways(self):
        self.assertEqual(violations(), [])

    def test_the_loop_child_helper_reaches_only_what_is_below_units(self):
        tree = ast.parse((ROOT / "loop" / "run.py").read_text())
        above = sorted(
            {top for _, top, _ in _imported(tree) if LAYER.get(top, -1) <= LAYER["units"]}
        )
        self.assertEqual(above, [])

    def test_the_loop_child_loads_neither_the_database_nor_the_helper_that_started_it(self):
        # `coscc.loop` reaches `coscc.units.guards`, which runs `coscc/units/__init__.py`: had
        # that imported `coscc.loop.run`, the package would come back to itself half-loaded.
        heavy = ("coscc.store.db", "coscc.loop.run", "asyncio")
        child = (
            "import importlib, pkgutil, sys, coscc.loop\n"
            "for m in pkgutil.iter_modules(coscc.loop.__path__):\n"
            "    if m.name != 'run': importlib.import_module('coscc.loop.' + m.name)\n"
            f"print([m for m in {heavy!r} if m in sys.modules])"
        )
        done = subprocess.run(
            [sys.executable, "-P", "-c", child],
            cwd=ROOT.parent,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual((done.returncode, done.stdout.strip(), done.stderr), (0, "[]", ""))

    def test_no_module_and_a_module_of_its_package_import_each_other(self):
        self.assertEqual(import_cycles(), [])


class FeaturesAreAddedAndRemovedWithoutReachingIn(unittest.TestCase):
    def test_the_features_keep_the_three_rules(self):
        self.assertEqual(feature_import_problems(_sources()), [])
        self.assertEqual(feature_size_problems(_feature_lines()), [])

    def test_a_feature_that_imports_the_app_is_told_to_use_ctx(self):
        for src in (
            "from coscc.runner.steps import Steps\n",
            "from coscc.http import app\n",
            "from coscc.http.plugin import ctx_of\n",
        ):
            (msg,) = feature_import_problems({"features/a.py": src})
            self.assertIn("imports only coscc.kernel: add what it needs there", msg)

    def test_a_feature_that_imports_a_feature_is_told_to_use_the_bus(self):
        for src in ("from coscc.features import b\n", "from coscc.features.b import x\n"):
            (msg,) = feature_import_problems({"features/a.py": src})
            self.assertIn("features talk through `coscc/bus.py`, never by import", msg)

    def test_a_feature_may_import_its_own_modules_and_no_other_feature(self):
        own = "from coscc.features.a import x\nfrom coscc.features.a.y import z\n"
        self.assertEqual(feature_import_problems({"features/a/x.py": own}), [])
        self.assertEqual(feature_import_problems({"features/a.py": own}), [])
        (msg,) = feature_import_problems({"features/a/x.py": "from coscc.features.b import x\n"})
        self.assertIn("features talk through `coscc/bus.py`, never by import", msg)
        (msg,) = feature_import_problems({"features/a/x.py": "from coscc.features.ab import x\n"})
        self.assertIn("never by import", msg)

    def test_a_feature_may_import_the_kernel_and_its_gaps(self):
        src = "from coscc.kernel import Ctx\nfrom coscc.vault import Store\n"
        self.assertEqual(feature_import_problems({"features/a.py": src}), [])
        (msg,) = feature_import_problems({"features/a.py": "from coscc.git import gitops\n"})
        self.assertIn("imports only coscc.kernel", msg)

    def test_every_kernel_gap_is_still_used(self):
        used = {
            name
            for path, text in _sources().items()
            if path.startswith("features/")
            for _, name, _ in _coscc_imports(ast.parse(text))
        }
        self.assertEqual(sorted(set(KERNEL_GAPS) - used), [])

    def test_only_the_api_imports_the_list_and_only_one_way(self):
        ok = "from coscc import features\n"
        self.assertEqual(feature_import_problems({"http/app.py": ok}), [])
        (msg,) = feature_import_problems({"leif/autopilot.py": ok})
        self.assertIn("only coscc/http/app.py knows the list", msg)
        (msg,) = feature_import_problems({"http/app.py": "from coscc.features import FEATURES\n"})
        self.assertIn("import the list as `from coscc import features`", msg)

    def test_a_feature_of_four_files_or_a_long_one_says_the_fix(self):
        (msg,) = feature_size_problems({"a.py": 1, "a.md": 1, "a.txt": 1, "b.py": 1, "a/x.py": 1})
        self.assertIn("feature a is 4 files, above 3", msg)
        (msg,) = feature_size_problems({"a.md": 1, "a/x.py": 1, "a/y.py": 1, "a/z.py": 1})
        self.assertIn("feature a is 4 files, above 3", msg)
        self.assertIn("Fold the extra file in", msg)
        self.assertEqual(feature_size_problems({"a.md": 1, "a/x.py": 1, "a/y.py": 1}), [])
        (msg,) = feature_size_problems({"a.py": 801})
        self.assertIn("801 lines, above 800", msg)
        self.assertEqual(feature_size_problems({"a.py": 800, "a.md": 800, "b.py": 5}), [])


if __name__ == "__main__":
    unittest.main()
