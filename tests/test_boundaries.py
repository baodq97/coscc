"""Modules talk through three declared channels, so a feature is added or removed without reaching in.

The channels are calls (public names, typed), events (`coscc/bus.py`) and data (one module owns
each table and runs its SQL). Each rule below is a ratchet over `coscc/`: today's findings are
listed in a literal, a new finding fails, and a listed finding that is gone fails too, so the
lists only shrink. `tests/` is not checked. Each check takes parsed trees, so a test can feed it
a planted case.
"""

from __future__ import annotations

import ast
import re
import unittest
from collections import Counter

from tests.test_layers import ROOT, _files

DATA = "coscc.data"

PRIVATE_IMPORTS: set[tuple[str, str, str]] = {
    ("coscc/runner/attempt.py", "coscc.runner.reply", "_unfence"),
    ("coscc/runner/prompt.py", "coscc.runner.review", "_ROUND_RE"),
    ("coscc/runner/prompt.py", "coscc.runner.review", "_header_status"),
    ("coscc/runner/prompt.py", "coscc.runner.review", "_round_meta"),
    ("coscc/runner/prompt.py", "coscc.runner.review", "_round_number"),
    ("coscc/runner/prompt.py", "coscc.runner.review", "_rounds"),
    ("coscc/runner/step.py", "coscc.runner.attempt", "_head_of"),
    ("coscc/runner/step.py", "coscc.runner.attempt", "_tree_state"),
    ("coscc/runner/step.py", "coscc.runner.attempt", "_write_artifact"),
    ("coscc/runner/step.py", "coscc.runner.prompt", "_read"),
    ("coscc/runner/step.py", "coscc.runner.reply", "_Stopped"),
    ("coscc/runner/step.py", "coscc.runner.reply", "_after_tool"),
    ("coscc/runner/step.py", "coscc.runner.reply", "_hit_ceiling"),
    ("coscc/runner/step.py", "coscc.runner.reply", "_joined"),
    ("coscc/runner/step.py", "coscc.runner.reply", "_title"),
    ("coscc/runner/step.py", "coscc.runner.reply", "_with_reply"),
    ("coscc/runner/step.py", "coscc.runner.review", "_round_number"),
    ("coscc/runner/step.py", "coscc.runner.review", "_rounds"),
    ("coscc/screens/__init__.py", "coscc.screens.backlog", "_backlog_screen"),
    ("coscc/screens/__init__.py", "coscc.screens.board", "_RECONNECT_JS"),
    ("coscc/screens/__init__.py", "coscc.screens.board", "_board"),
    ("coscc/screens/__init__.py", "coscc.screens.chrome", "_banners"),
    ("coscc/screens/__init__.py", "coscc.screens.chrome", "_sidebar"),
    ("coscc/screens/__init__.py", "coscc.screens.chrome", "_status_bar"),
    ("coscc/screens/__init__.py", "coscc.screens.chrome", "_topbar"),
    ("coscc/screens/__init__.py", "coscc.screens.dialogs", "_WATCH_JS"),
    ("coscc/screens/__init__.py", "coscc.screens.dialogs", "_command_dialog"),
    ("coscc/screens/__init__.py", "coscc.screens.dialogs", "_mobile_dialog"),
    ("coscc/screens/__init__.py", "coscc.screens.dialogs", "_remove_dialog"),
    ("coscc/screens/__init__.py", "coscc.screens.dialogs", "_watch_dialog"),
    ("coscc/screens/__init__.py", "coscc.screens.dialogs", "_workspace_dialog"),
    ("coscc/screens/__init__.py", "coscc.screens.idea", "_idea_screen"),
    ("coscc/screens/__init__.py", "coscc.screens.overview", "_overview"),
    ("coscc/screens/__init__.py", "coscc.screens.overview", "_workspaces_screen"),
    ("coscc/screens/__init__.py", "coscc.screens.sessions", "_activity"),
    ("coscc/screens/__init__.py", "coscc.screens.sessions", "_cost"),
    ("coscc/screens/__init__.py", "coscc.screens.sessions", "_sessions"),
    ("coscc/screens/__init__.py", "coscc.screens.settings", "_settings"),
    ("coscc/screens/__init__.py", "coscc.screens.unit", "_detail_dialog"),
    ("coscc/screens/backlog.py", "coscc.screens.common", "_MONO"),
    ("coscc/screens/board.py", "coscc.screens.common", "_MONO"),
    ("coscc/screens/board.py", "coscc.screens.common", "_RUNIC"),
    ("coscc/screens/board.py", "coscc.screens.common", "_details"),
    ("coscc/screens/board.py", "coscc.screens.overview", "_empty_board"),
    ("coscc/screens/dialogs.py", "coscc.screens.chrome", "_nav"),
    ("coscc/screens/dialogs.py", "coscc.screens.chrome", "_workspace_select"),
    ("coscc/screens/dialogs.py", "coscc.screens.common", "_MONO"),
    ("coscc/screens/overview.py", "coscc.screens.chrome", "_event_row"),
    ("coscc/screens/overview.py", "coscc.screens.chrome", "_metrics"),
    ("coscc/screens/overview.py", "coscc.screens.common", "_details"),
    ("coscc/screens/sessions.py", "coscc.screens.board", "_update_warning"),
    ("coscc/screens/sessions.py", "coscc.screens.chrome", "_event_row"),
    ("coscc/screens/sessions.py", "coscc.screens.chrome", "_metrics"),
    ("coscc/screens/sessions.py", "coscc.screens.common", "_MONO"),
    ("coscc/screens/sessions.py", "coscc.screens.common", "_details"),
    ("coscc/screens/sessions.py", "coscc.screens.common", "_mono"),
    ("coscc/screens/sessions.py", "coscc.screens.common", "_table"),
    ("coscc/screens/settings.py", "coscc.screens.board", "_update_panel"),
    ("coscc/screens/settings.py", "coscc.screens.common", "_MONO"),
    ("coscc/screens/settings.py", "coscc.screens.common", "_RUNIC"),
    ("coscc/screens/settings.py", "coscc.screens.common", "_details"),
    ("coscc/screens/settings.py", "coscc.screens.common", "_table"),
    ("coscc/screens/unit.py", "coscc.screens.board", "_update_warning"),
    ("coscc/screens/unit.py", "coscc.screens.chrome", "_banners"),
    ("coscc/screens/unit.py", "coscc.screens.common", "_details"),
    ("coscc/screens/unit.py", "coscc.screens.sessions", "_unit_cost"),
    ("coscc/screens/unit.py", "coscc.screens.settings", "_settings_row"),
    ("coscc/service/board.py", "coscc.service.common", "_younger_than"),
    ("coscc/service/steps.py", "coscc.service.common", "_younger_than"),
    ("coscc/state/__init__.py", "coscc.state.views", "_POLLING"),
    ("coscc/state/__init__.py", "coscc.state.views", "_activities"),
    ("coscc/state/__init__.py", "coscc.state.views", "_anomaly_rows"),
    ("coscc/state/__init__.py", "coscc.state.views", "_asking"),
    ("coscc/state/__init__.py", "coscc.state.views", "_card"),
    ("coscc/state/__init__.py", "coscc.state.views", "_cell_label"),
    ("coscc/state/__init__.py", "coscc.state.views", "_ci_line"),
    ("coscc/state/__init__.py", "coscc.state.views", "_current_stage"),
    ("coscc/state/__init__.py", "coscc.state.views", "_hold_detail"),
    ("coscc/state/__init__.py", "coscc.state.views", "_hold_fields"),
    ("coscc/state/__init__.py", "coscc.state.views", "_initials"),
    ("coscc/state/__init__.py", "coscc.state.views", "_integration_fields"),
    ("coscc/state/__init__.py", "coscc.state.views", "_moves"),
    ("coscc/state/__init__.py", "coscc.state.views", "_number"),
    ("coscc/state/__init__.py", "coscc.state.views", "_outcome_fields"),
    ("coscc/state/__init__.py", "coscc.state.views", "_questions"),
    ("coscc/state/__init__.py", "coscc.state.views", "_relations_text"),
    ("coscc/state/__init__.py", "coscc.state.views", "_rounds"),
    ("coscc/state/__init__.py", "coscc.state.views", "_run_dropped"),
    ("coscc/state/__init__.py", "coscc.state.views", "_run_target"),
    ("coscc/state/__init__.py", "coscc.state.views", "_run_waiting"),
    ("coscc/state/__init__.py", "coscc.state.views", "_shown"),
    ("coscc/state/__init__.py", "coscc.state.views", "_spend_rows"),
    ("coscc/state/__init__.py", "coscc.state.views", "_tab_gone"),
    ("coscc/state/__init__.py", "coscc.state.views", "_title_of"),
    ("coscc/state/__init__.py", "coscc.state.views", "_token_row"),
    ("coscc/state/__init__.py", "coscc.state.views", "_tokens"),
    ("coscc/state/__init__.py", "coscc.state.views", "_unknown"),
    ("coscc/state/__init__.py", "coscc.state.views", "_usd"),
    ("coscc/state/__init__.py", "coscc.state.views", "_waste_rows"),
    ("coscc/state/answers.py", "coscc.state.views", "_key_label"),
    ("coscc/state/update.py", "coscc.state.views", "_channel_line"),
    ("coscc/state/update.py", "coscc.state.views", "_job_line"),
    ("coscc/state/watch.py", "coscc.state.views", "_watch_events"),
    ("coscc/state/watch.py", "coscc.state.views", "_watch_note"),
    ("coscc/units/backlog.py", "coscc.units.hold", "_line_problem"),
    ("coscc/units/ideas.py", "coscc.units", "_cos"),
}

OWNERS: dict[str, str] = {
    "auth": "coscc.data",
    "auth_sessions": "coscc.data",
    "decisions": "coscc.data",
    "idea_meta": "coscc.units.meta",
    "impl_claims": "coscc.units.meta",
    "migrations": "coscc.data",
    "outputs": "coscc.units.history",
    "prefs": "coscc.data",
    "pull_requests": "coscc.github.prmachine",
    "review_findings": "coscc.units.meta",
    "review_rounds": "coscc.units.meta",
    "runs": "coscc.runlog.journal",
    "stage_results": "coscc.units.meta",
    "step_events": "coscc.data",
    "step_runs": "coscc.data",
    "transitions": "coscc.units.meta",
    "unit_answers": "coscc.units.meta",
    "unit_holds": "coscc.units.meta",
    "unit_links": "coscc.units.meta",
    "unit_meta": "coscc.units.meta",
    "unit_questions": "coscc.units.meta",
    "unit_seen": "coscc.units.meta",
    "unit_unknowns": "coscc.units.meta",
    "workspaces": "coscc.service.store",
}

FOREIGN_SQL: set[tuple[str, str]] = {
    ("coscc.github.prmachine", "review_rounds"),
    ("coscc.github.prmachine", "transitions"),
    ("coscc.run", "unit_meta"),
    ("coscc.run", "workspaces"),
    ("coscc.service.steps", "transitions"),
    ("coscc.units.history", "transitions"),
    ("coscc.units.prose_import", "review_rounds"),
    ("coscc.units.turnstats", "runs"),
    ("coscc.units.turnstats", "step_events"),
    ("coscc.units.turnstats", "step_runs"),
    ("coscc.units.turnstats", "transitions"),
}

DICT_ANY_CEILING = 301

SQL_USE = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+(\w+)", re.IGNORECASE)
# A string is a statement only when a line of it opens with an upper-case DML keyword, so
# prose such as "open one from Workspaces." names no table.
STATEMENT = re.compile(r"^\s*(?:SELECT|INSERT|UPDATE|DELETE|WITH|REPLACE)\b", re.MULTILINE)


def _trees() -> dict[str, ast.AST]:
    """Every module of `coscc/` by its path from the repository root, parsed."""
    return {str(p.relative_to(ROOT.parent)): ast.parse(p.read_text()) for p in _files()}


def _dotted(path: str) -> str:
    return path.removesuffix(".py").removesuffix("/__init__").replace("/", ".")


def private_imports(trees: dict[str, ast.AST]) -> set[tuple[str, str, str]]:
    """`(importing file, source module, name)` of each `from coscc.x import _name` outside x."""
    found = set()
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ImportFrom) and node.module and not node.level):
                continue
            if not node.module.startswith("coscc") or node.module == _dotted(path):
                continue
            for a in node.names:
                if a.name.startswith("_") and not a.name.startswith("__"):
                    found.add((path, node.module, a.name))
    return found


def private_import_problems(
    trees: dict[str, ast.AST], frozen: set[tuple[str, str, str]]
) -> list[str]:
    now = private_imports(trees)
    out = []
    for path, module, name in sorted(now - frozen):
        src = module.replace(".", "/") + ".py"
        out.append(
            f"{path} imports `{name}` from {module}: a leading underscore means only {src} uses it. "
            f"Drop the underscore in {src}, or keep the name in the one module that uses it."
        )
    for path, module, name in sorted(frozen - now):
        out.append(
            f"PRIVATE_IMPORTS lists `{name}` imported by {path} from {module}, which is gone. "
            f"Delete that entry."
        )
    return out


def _sql_strings(tree: ast.AST):
    """Each string constant in the tree, an f-string as the join of its literal parts."""
    fstring_parts = {
        id(v)
        for n in ast.walk(tree)
        if isinstance(n, ast.JoinedStr)
        for v in n.values
        if isinstance(v, ast.Constant)
    }
    for n in ast.walk(tree):
        if isinstance(n, ast.JoinedStr):
            yield "".join(v.value for v in n.values if isinstance(v, ast.Constant))
        elif (
            isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in fstring_parts
        ):
            yield n.value


def table_names(tree: ast.AST) -> set[str]:
    found = set()
    for s in _sql_strings(tree):
        found.update(re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", s, re.IGNORECASE))
    return found


def table_uses(trees: dict[str, ast.AST], tables: set[str]) -> Counter[tuple[str, str]]:
    """Statements per `(module, table)` that read or write the table; DDL does not count."""
    uses: Counter[tuple[str, str]] = Counter()
    for path, tree in trees.items():
        for s in _sql_strings(tree):
            if not STATEMENT.search(s):
                continue
            for t in SQL_USE.findall(s):
                if t.lower() in tables:
                    uses[(_dotted(path), t.lower())] += 1
    return uses


def table_problems(
    trees: dict[str, ast.AST],
    tables: set[str],
    owners: dict[str, str],
    frozen: set[tuple[str, str]],
) -> list[str]:
    out = []
    for t in sorted(tables - set(owners)):
        out.append(
            f"Table `{t}` has no owner. Add it to OWNERS with the one module that runs its SQL."
        )
    for t in sorted(set(owners) - tables):
        out.append(f"OWNERS lists `{t}`, which is no table. Delete that entry.")
    now = {(m, t) for (m, t) in table_uses(trees, tables) if owners.get(t) not in (None, m)}
    for m, t in sorted(now - frozen):
        owner = owners[t]
        out.append(
            f"{m.replace('.', '/')}.py runs SQL on `{t}`, which {owner.replace('.', '/')}.py owns. "
            f"Add a function to {owner.replace('.', '/')}.py that does it, and call that."
        )
    for m, t in sorted(frozen - now):
        out.append(f"FOREIGN_SQL lists {m} on `{t}`, which is gone. Delete that entry.")
    return out


def dict_any_count(trees: dict[str, ast.AST]) -> int:
    """Public functions and methods with `dict[str, Any]` in a parameter or the return type."""
    n = 0
    for tree in trees.values():
        for f in ast.walk(tree):
            if not isinstance(f, ast.FunctionDef | ast.AsyncFunctionDef) or f.name.startswith("_"):
                continue
            args = f.args
            notes = [a.annotation for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]]
            notes += [args.vararg and args.vararg.annotation, args.kwarg and args.kwarg.annotation]
            notes.append(f.returns)
            if any(a is not None and "dict[str, Any]" in ast.unparse(a) for a in notes):
                n += 1
    return n


def dict_any_problem(count: int, ceiling: int) -> str | None:
    if count > ceiling:
        return (
            f"{count} public functions take or return dict[str, Any], above the ceiling {ceiling}: "
            f"type the new one with a dataclass, a TypedDict or a Literal."
        )
    if count < ceiling:
        return f"lower DICT_ANY_CEILING to {count}"
    return None


def _parse(**sources: str) -> dict[str, ast.AST]:
    return {f"coscc/{name}.py": ast.parse(src) for name, src in sources.items()}


class NoPrivateNameCrossesAModule(unittest.TestCase):
    """A name with one leading underscore is imported only by its own module."""

    def test_no_new_private_import_and_no_stale_entry(self):
        self.assertEqual(private_import_problems(_trees(), PRIVATE_IMPORTS), [])

    def test_a_planted_private_import_says_the_fix(self):
        trees = _parse(a="from coscc.b import _x, __y__, z\n", b="from coscc.b import _own\n")
        self.assertEqual(
            private_import_problems(trees, set()),
            [
                "coscc/a.py imports `_x` from coscc.b: a leading underscore means only coscc/b.py "
                "uses it. Drop the underscore in coscc/b.py, or keep the name in the one module "
                "that uses it."
            ],
        )

    def test_a_type_checking_import_counts(self):
        trees = _parse(a="if TYPE_CHECKING:\n    from coscc.b import _x\n")
        self.assertEqual(private_imports(trees), {("coscc/a.py", "coscc.b", "_x")})

    def test_a_listed_import_that_is_gone_says_to_delete_it(self):
        (msg,) = private_import_problems(_parse(a="pass\n"), {("coscc/a.py", "coscc.b", "_x")})
        self.assertIn("Delete that entry.", msg)


class EveryTableHasOneOwner(unittest.TestCase):
    """Only a table's owner module runs SQL (FROM, JOIN, INTO, UPDATE) on it."""

    def test_every_table_has_an_owner_and_no_new_foreign_sql(self):
        trees = _trees()
        tables = table_names(trees["coscc/data.py"])
        self.assertEqual(table_problems(trees, tables, OWNERS, FOREIGN_SQL), [])

    def test_a_planted_foreign_statement_says_the_fix(self):
        trees = _parse(
            a='q = "SELECT * FROM t WHERE x = 1"\np = "Add one, or open one from t."\n',
            b='q = f"UPDATE t SET {col} = 1"\nr = "CREATE INDEX i ON t(x)"\n',
        )
        self.assertEqual(
            table_problems(trees, {"t"}, {"t": "coscc.b"}, set()),
            [
                "coscc/a.py runs SQL on `t`, which coscc/b.py owns. Add a function to "
                "coscc/b.py that does it, and call that."
            ],
        )

    def test_a_table_without_an_owner_says_the_fix(self):
        (msg,) = table_problems(_parse(a="pass\n"), {"t"}, {}, set())
        self.assertIn("Add it to OWNERS", msg)

    def test_a_listed_use_that_is_gone_says_to_delete_it(self):
        (msg,) = table_problems(_parse(a="pass\n"), {"t"}, {"t": "coscc.b"}, {("coscc.a", "t")})
        self.assertIn("Delete that entry.", msg)

    def test_ddl_does_not_count_as_a_use(self):
        trees = _parse(a='q = "ALTER TABLE t ADD COLUMN c; DROP TABLE t; CREATE INDEX i ON t(c)"\n')
        self.assertEqual(table_uses(trees, {"t"}), Counter())


class CallsAreTyped(unittest.TestCase):
    """Public functions do not take or return a bare `dict[str, Any]`."""

    def test_the_count_matches_the_ceiling(self):
        self.assertIsNone(dict_any_problem(dict_any_count(_trees()), DICT_ANY_CEILING))

    def test_a_planted_public_function_counts_but_a_private_one_does_not(self):
        src = (
            "def a(x: dict[str, Any]): ...\n"
            "async def b() -> list[dict[str, Any]]: ...\n"
            "def _c(x: dict[str, Any]): ...\n"
            "def d(x: dict[str, int]): ...\n"
        )
        self.assertEqual(dict_any_count(_parse(a=src)), 2)

    def test_above_and_below_the_ceiling_say_the_fix(self):
        self.assertEqual(
            dict_any_problem(3, 2),
            "3 public functions take or return dict[str, Any], above the ceiling 2: type the new "
            "one with a dataclass, a TypedDict or a Literal.",
        )
        self.assertEqual(dict_any_problem(1, 2), "lower DICT_ANY_CEILING to 1")


if __name__ == "__main__":
    unittest.main()
