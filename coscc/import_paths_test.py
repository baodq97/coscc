"""`0095` R3: every name code or a test reached in the four largest modules still resolves.

`coscc/service/__init__.py`, `state.py`, `screens.py` and `runner.py` were split into modules of their
own. The names below are the ones something in this repository imported from those four,
patched on them, or cited under `.claude/`, collected on `5d161a2`, before the split. Each
of the four imports them back, so `from coscc.<module> import <name>` still works; a name
that was dropped by the split fails here rather than in whichever caller used it.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import unittest
from pathlib import Path

NAMES = {
    "service": (
        "CI_REFRESH", "COLLAPSED_STATES", "CONSEQUENCE", "Invalid", "NotUpdatable",
        "OUTCOME_RESULTS", "RETAKE_REFUSED", "Runner", "STAGE_FILES", "STATE_COLOR",
        "STATE_LABEL", "Service", "StaleCutList", "Updating", "_now", "attention_reason",
        "date", "describe_base", "events", "integration_since_review", "outcome_label",
        "reason_beside", "shown_state", "step_cwd", "unit_state",
    ),
    "state": (
        "API", "Activity", "AnomalyRow", "AutopilotStop", "BacklogRow", "COST_NOTE", "Card",
        "Cell", "Event", "GONE_AFTER", "GrantRow", "Invalid", "Knob", "MESSAGE_CUT", "Message",
        "ModelRow", "NAVIGATION", "NO_RUN_NOTE", "Question", "READ_ONLY_NOTE", "RUNNING_POLL",
        "Round", "Run", "SERVICE", "STATUS_COLOR", "SpendRow", "StudioState", "TokenRow",
        "Unit", "UsageRow", "WATCH_WINDOW", "WasteRow", "WatchEvent", "Workspace", "_ASKING",
        "_POLLING", "_activities", "_asking", "_card", "_cell_label", "_hold_detail",
        "_hold_fields", "_outcome_fields", "_questions", "_relations_text", "_run_target",
        "_run_waiting", "_shown", "_tab_gone", "_tokens", "_usd", "_watch_note",
        "backlog_view", "cost_note", "events_mod", "rx",
    ),
    "screens": (
        "_activity", "_autopilot_settings", "_autopilot_strip", "_backlog_screen", "_banners",
        "_board", "_detail_dialog", "_empty_board", "_integration_panel", "_outcome_panel",
        "_overview", "_questions_tab", "_sessions", "_settings", "_unit_card",
        "_workspaces_screen", "index",
    ),
    "runner": (
        "ATTEMPT_EXCERPT", "CEILING_MARKERS", "CLAUDE_CODE_PRESET", "CLOSING_TIMEOUT",
        "COMMANDS_ADVICE", "COMMANDS_HEADING", "Denials", "KNOWLEDGE_ADVICE",
        "PLAN_MAP_ADVICE", "PLAN_MAP_HEADING", "PRIOR_FINDINGS_ADVICE",
        "PRIOR_FINDINGS_HEADING", "RunError", "Runner", "SESSIONS_PER_STEP", "STATUS_RE",
        "_POINTING", "_jera_answers", "_joined", "_rounds", "_tree_state", "_unfence",
        "_write_artifact", "answers_section", "build_prompt", "check_reply",
        "closing_round_problem", "compose_prompt", "describe_attempt", "from_title",
        "merge_review", "open_findings", "opening_problem", "opening_reason",
        "permission_gate", "sessions_mod", "skill_for", "snapshot", "strip_answers",
        "with_answers",
    ),
}


class EveryNameAModuleWasReachedByStillResolves(unittest.TestCase):
    def test_each_name_is_still_on_its_module(self):
        for module, names in NAMES.items():
            mod = importlib.import_module(f"coscc.{module}")
            for name in names:
                with self.subTest(module=module, name=name):
                    self.assertTrue(hasattr(mod, name), f"coscc.{module}.{name} no longer resolves")


# `0129`: the package is split into subpackages, and the root keeps only entry points and
# the two modules nearly every package reads.
REPO = Path(__file__).resolve().parents[1]
PACKAGE = REPO / "coscc"
ENTRY_POINTS = ("coscc.run", "coscc.build", "coscc.coscc")
FOUNDATION = {"config.py": "coscc.config", "data.py": "coscc.data"}


def _imports(source: str) -> list[str]:
    """Every module an import in `source` names, nested ones included: `from a import b`
    gives `a` and `a.b`, since `b` may be a module."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append(node.module)
            found += [f"{node.module}.{a.name}" for a in node.names if a.name != "*"]
    return found


def _within(name: str, modules) -> bool:
    return any(name == m or name.startswith(m + ".") for m in modules)


def _built(path: Path) -> bool:
    parts = path.relative_to(PACKAGE).parts
    return len(parts) > 1 and parts[0].startswith("_")


def _sources() -> dict[str, str]:
    """Every module of the package but tests and what a build writes (`_web/`, `_harness/`),
    by its path under `coscc/`."""
    return {
        p.relative_to(PACKAGE).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(PACKAGE.rglob("*.py"))
        if not p.name.endswith("_test.py") and not _built(p)
    }


def root_problems(sources: dict[str, str]) -> list[str]:
    """R4: no module in a subpackage imports an entry point, the foundation imports nothing of
    the package but itself, and `coscc/__init__.py` imports nothing."""
    problems = []
    for path, source in sources.items():
        names = _imports(source)
        if "/" in path:
            problems += [f"{path} imports {n}" for n in names if _within(n, ENTRY_POINTS)]
        elif path in FOUNDATION:
            problems += [
                f"{path} imports {n}" for n in names
                if _within(n, ("coscc",)) and n != "coscc" and not _within(n, FOUNDATION.values())
            ]
        elif path == "__init__.py" and names:
            problems.append(f"{path} imports {', '.join(names)}")
    return problems


def _bound(body: list[ast.stmt]) -> list[tuple[str, ast.stmt]]:
    """The names a module's top level binds, with the statement that binds each."""
    found: list[tuple[str, ast.stmt]] = []
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.append((node.name, node))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            found += [((a.asname or a.name).split(".")[0], node) for a in node.names]
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                found += [(n.id, node) for n in ast.walk(t) if isinstance(n, ast.Name)]
        elif isinstance(node, (ast.If, ast.Try, ast.With)):
            for inner in ("body", "orelse", "finalbody", "handlers"):
                for part in getattr(node, inner, []):
                    found += _bound(part.body if isinstance(part, ast.ExceptHandler) else [part])
    return found


def shadowing(package: str, init: str, submodules: set[str]) -> list[str]:
    """R5: names `__init__` binds that are also a submodule of its package. Importing that
    submodule later puts the module in the name's place, and nothing says so. The one binding
    allowed is the submodule itself, `from <package> import <it>`, since that is the same
    object the import would put there."""
    problems = []
    for name, node in _bound(ast.parse(init).body):
        if name not in submodules:
            continue
        itself = isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == package and any(
            a.name == name and a.asname in (None, name) for a in node.names
        )
        if not itself:
            problems.append(f"{package}.{name}")
    return problems


def unresolved(source: str) -> list[str]:
    """R8: each `coscc` module an import names, and each name a `from coscc... import` takes,
    that does not exist."""
    problems = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            wanted = [(a.name, None) for a in node.names if _within(a.name, ("coscc",))]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and _within(node.module or "", ("coscc",)):
            wanted = [(node.module, a.name) for a in node.names]
        else:
            continue
        for module, name in wanted:
            try:
                mod = importlib.import_module(module)
            except ImportError:
                problems.append(module)
                continue
            if name is None or name == "*" or hasattr(mod, name):
                continue
            if not hasattr(mod, "__path__") or importlib.util.find_spec(f"{module}.{name}") is None:
                problems.append(f"{module}.{name}")
    return problems


class NoImportCycleRunsThroughTheRoot(unittest.TestCase):
    def test_the_package(self):
        self.assertEqual(root_problems(_sources()), [])

    def test_the_check_would_see_one(self):
        sources = {
            "__init__.py": "",
            "config.py": "import os\n",
            "data.py": "from coscc import config\n",
            "web/auth.py": "def f():\n    from coscc.run import REPO\n",
        }
        self.assertEqual(root_problems(sources), ["web/auth.py imports coscc.run", "web/auth.py imports coscc.run.REPO"])
        self.assertEqual(root_problems({"config.py": "from coscc.agent import models\n"}),
                         ["config.py imports coscc.agent", "config.py imports coscc.agent.models"])
        self.assertEqual(root_problems({"__init__.py": "import os\n"}), ["__init__.py imports os"])


class NoPackageInitShadowsItsOwnSubmodule(unittest.TestCase):
    def test_every_init(self):
        inits = sorted(p for p in PACKAGE.rglob("__init__.py") if not _built(p))
        self.assertTrue(inits)
        for init in inits:
            source = init.read_text(encoding="utf-8")
            if not source.strip():
                continue
            here = init.parent
            submodules = {p.stem for p in here.glob("*.py") if p.name != "__init__.py"}
            submodules |= {d.name for d in here.iterdir() if (d / "__init__.py").is_file()}
            package = ".".join(here.relative_to(REPO).parts)
            with self.subTest(package=package):
                self.assertEqual(shadowing(package, source, submodules), [])

    def test_the_check_would_see_one(self):
        init = "from coscc.units import board\nfrom coscc.units import hold as hold_mod\nhistory = 1\ndef retake(): pass\n"
        found = shadowing("coscc.service", "from coscc.service import board\n" + init, {"board", "history", "retake", "hold"})
        self.assertEqual(found, ["coscc.service.board", "coscc.service.history", "coscc.service.retake"])


class EveryImportOutsideThePackageResolves(unittest.TestCase):
    def test_rxconfig_and_the_scripts(self):
        for path in [REPO / "rxconfig.py", *sorted((REPO / "scripts").glob("*.py"))]:
            with self.subTest(path=path.name):
                self.assertEqual(unresolved(path.read_text(encoding="utf-8")), [])

    def test_the_check_would_see_one(self):
        source = "from coscc.config import Config, NO_SUCH\nimport coscc.no_such\n\ndef f():\n    from coscc import no_module\n"
        self.assertEqual(unresolved(source), ["coscc.config.NO_SUCH", "coscc.no_such", "coscc.no_module"])


if __name__ == "__main__":
    unittest.main()
