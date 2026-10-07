"""Modules talk through three declared channels, so a feature is added or removed without reaching in.

The channels are calls (public names, typed), events (`coscc/bus.py`) and data (one module owns
each table and runs its SQL). Each rule below is a ratchet over `coscc/`: today's findings are
listed in a literal, a new finding fails, and a listed finding that is gone fails too, so the
lists only shrink. `DICT_ANY` is keyed by `module:qualname`: a function that moves across modules is
an explicit replacement of its entry, and one that moves inside its module keeps it. `tests/` is not checked. Each check takes parsed trees, so a test can feed it
a planted case.
"""

from __future__ import annotations

import ast
import re
import unittest
from collections import Counter

from coscc import features
from tests.test_layers import ROOT, _files

DATA = "coscc.store.db"

PRIVATE_IMPORTS: set[tuple[str, str, str]] = {
    ("coscc/runner/step.py", "coscc.runner.attempt", "_head_of"),
    ("coscc/runner/step.py", "coscc.runner.attempt", "_tree_state"),
    ("coscc/runner/step.py", "coscc.runner.attempt", "_write_artifact"),
    ("coscc/runner/step.py", "coscc.runner.prompt", "_read"),
    ("coscc/runner/step.py", "coscc.runner.reply", "_Stopped"),
    ("coscc/runner/step.py", "coscc.runner.reply", "_after_tool"),
    ("coscc/runner/step.py", "coscc.runner.reply", "_joined"),
    ("coscc/runner/step.py", "coscc.runner.reply", "_title"),
    ("coscc/runner/step.py", "coscc.runner.reply", "_with_reply"),
    ("coscc/runner/step.py", "coscc.runner.review", "_round_number"),
    ("coscc/runner/step.py", "coscc.runner.review", "_rounds"),
    ("coscc/units/backlog.py", "coscc.units.hold", "_line_problem"),
    ("coscc/units/ideas.py", "coscc.units", "_cos"),
}

OWNERS: dict[str, str] = {
    "auth": "coscc.store.db",
    "auth_sessions": "coscc.store.db",
    "impl_claims": "coscc.units.meta",
    "migrations": "coscc.store.db",
    "outputs": "coscc.units.meta",
    "prefs": "coscc.store.db",
    "pull_requests": "coscc.github.prmachine",
    "attempts": "coscc.runner.queue",
    "attempt_moves": "coscc.runner.queue",
    "review_findings": "coscc.units.meta",
    "review_rounds": "coscc.units.meta",
    "runs": "coscc.store.journal",
    "step_events": "coscc.store.db",
    "step_runs": "coscc.store.db",
    "transitions": "coscc.units.meta",
    "unit_answers": "coscc.units.meta",
    "unit_decisions": "coscc.units.meta",
    "unit_holds": "coscc.units.meta",
    "unit_links": "coscc.units.meta",
    "unit_meta": "coscc.units.meta",
    "unit_questions": "coscc.units.meta",
    "unit_unknowns": "coscc.units.meta",
    "workspaces": "coscc.store.workspaces",
    "proposals": "coscc.units.proposals",
    "trigger_due": "coscc.runner.triggers",
}

FOREIGN_SQL: set[tuple[str, str]] = {
    ("coscc.github.prmachine", "review_rounds"),
    ("coscc.github.prmachine", "transitions"),
    ("coscc.run", "unit_meta"),
    # `_from_12`, the one step from 0.14: records, links, unknowns, a stored `done`, answers, the
    # scan feature's proposals, every `exhausted` end, a merge's record kind.
    ("coscc.store.db", "outputs"),
    ("coscc.store.db", "proposals"),
    ("coscc.store.db", "unit_meta"),
    ("coscc.store.db", "unit_links"),
    ("coscc.store.db", "transitions"),
    ("coscc.store.db", "unit_unknowns"),
    ("coscc.store.db", "unit_answers"),
    ("coscc.store.db", "runs"),
    ("coscc.store.db", "attempt_moves"),
    ("coscc.run", "workspaces"),
    ("coscc.github.integration", "transitions"),
    ("coscc.units.history", "transitions"),
    ("coscc.units.turnstats", "runs"),
    ("coscc.units.turnstats", "step_events"),
    ("coscc.units.turnstats", "step_runs"),
    ("coscc.units.turnstats", "transitions"),
}

DICT_ANY: set[str] = {
    "coscc.agent.agents:address",
    "coscc.agent.agents:agent_for",
    "coscc.agent.agents:identity_section",
    "coscc.agent.agents:label",
    "coscc.agent.agents:of_record",
    "coscc.agent.agents:settings_json",
    "coscc.agent.agents:table",
    "coscc.agent.helpers:definitions",
    "coscc.agent.policy:part_of",
    "coscc.agent.pack:manifest",
    "coscc.agent.pack:owner_fields",
    "coscc.agent.pack:parse",
    "coscc.agent.pack:row",
    "coscc.agent.pack:rows",
    "coscc.agent.pack:stamp",
    "coscc.agent.sessions:Sessions.send",
    "coscc.agent.sessions:Sessions.stream",
    "coscc.agent.sessions:Sessions.suspend_all",
    "coscc.agent.sessions:history",
    "coscc.agent.sessions:list_for_directory",
    "coscc.agent.transcript:ceilings_left",
    "coscc.agent.transcript:cut",
    "coscc.features.notices:Notices.follow_notices",
    "coscc.features.notices:notice_of",
    "coscc.features.release.rules:classify",
    "coscc.features.release.rules:record",
    "coscc.features.release:Release._release_press.write",
    "coscc.features.release:Release.view",
    "coscc.git.drift:compute",
    "coscc.git.drift:describe",
    "coscc.git.drift:plan_head",
    "coscc.git.fetches:Fetches.fetch",
    "coscc.git.fetches:fetch",
    "coscc.github.integrate:build_prompt",
    "coscc.github.integrate:classify",
    "coscc.github.integrate:describe_for_review",
    "coscc.github.integrate:needs_person_of",
    "coscc.github.integrate:record",
    "coscc.github.integration:Integration._integrate_body.write",
    "coscc.github.integration:Integration.attach_integration",
    "coscc.github.integration:Integration.cleanup",
    "coscc.github.integration:Integration.integrate_gebo",
    "coscc.github.integration:Integration.mechanical",
    "coscc.github.integration:Integration.reconcile_prs",
    "coscc.github.integration:Integration.resume",
    "coscc.github.integration:Integration.resume.write",
    "coscc.github.integration:Integration.shipped",
    "coscc.github.integration:integration_since_review",
    "coscc.github.prmachine:Machine.open_of",
    "coscc.github.prmachine:Machine.view",
    "coscc.github.prmachine:Outcome.as_dict",
    "coscc.github.prmachine:ci_held",
    "coscc.github.prmachine:last_round",
    "coscc.github.prmachine:open_prs",
    "coscc.github.prmachine:state",
    "coscc.github.prmachine:watched",
    "coscc.http.app:Core.board",
    "coscc.kernel:body",
    "coscc.kernel:line",
    "coscc.leif.agents:Agents.agent",
    "coscc.leif.agents:Models.stage_config",
    "coscc.leif.answers:Answers.answer",
    "coscc.leif.answers:Answers.create_unit",
    "coscc.leif.answers:Answers.hold",
    "coscc.leif.answers:Answers.ingest",
    "coscc.leif.answers:Answers.more_rounds",
    "coscc.leif.answers:Answers.post_new_rounds",
    "coscc.leif.answers:Answers.post_review_comment",
    "coscc.leif.answers:Answers.record_outcome",
    "coscc.leif.answers:Answers.sync_pr",
    "coscc.leif.answers:Answers.worktree",
    "coscc.leif.autopilot:Autopilot.cap",
    "coscc.leif.autopilot:Autopilot.guide_block",
    "coscc.leif.autopilot:Autopilot.nudge",
    "coscc.leif.autopilot:Autopilot.run_pass",
    "coscc.leif.autopilot:Autopilot.set_setting",
    "coscc.leif.autopilot:Autopilot.settings",
    "coscc.leif.autopilot:Autopilot.show",
    "coscc.leif.autopilot:autopilot_values",
    "coscc.leif.backlog:Backlog._append_checked.refuse",
    "coscc.leif.backlog:Backlog.propose_estimates",
    "coscc.leif.backlog:Backlog.record_estimate",
    "coscc.leif.backlog:Backlog.record_relation",
    "coscc.leif.backlog:Backlog.record_shortlist",
    "coscc.leif.chat:Chat.history",
    "coscc.leif.chat:Chat.sessions_for",
    "coscc.leif.chat:Chat.stream",
    "coscc.leif.decide:after_own_integration",
    "coscc.leif.decide:answer_completes",
    "coscc.leif.decide:answered_since_start",
    "coscc.leif.decide:measure",
    "coscc.leif.decide:measure.blank",
    "coscc.leif.decide:open_starts",
    "coscc.leif.decide:pick",
    "coscc.leif.decide:reruns_of",
    "coscc.leif.decide:reserved",
    "coscc.leif.decide:since_integration",
    "coscc.leif.decide:spent_on",
    "coscc.leif.decide:spent_today",
    "coscc.leif.decide:stop_for",
    "coscc.leif.decide:unopened_of",
    "coscc.leif.guide:held",
    "coscc.leif.guide:needs_you",
    "coscc.leif.guide:notes",
    "coscc.leif.guide:running",
    "coscc.leif.spend:_anomalies.row",
    "coscc.leif.spend:model",
    "coscc.runlog.events:Recorder.subscribe",
    "coscc.runner.attempt:describe_attempt",
    "coscc.runner.attempt:snapshot",
    "coscc.runner.prompt:compose_prompt",
    "coscc.runner.resume:Resume.resume_after_update",
    "coscc.runner.resume:Resume.settle_after_suspend",
    "coscc.runner.resume:Resume.suspend_sessions",
    "coscc.runner.resume:check",
    "coscc.runner.resume:moved_on",
    "coscc.runner.resume:resume_message",
    "coscc.runner.review:finding_line",
    "coscc.runner.review:render_round",
    "coscc.runner.run:system_prompt",
    "coscc.runner.step:Runner.run",
    "coscc.runner.steps:Steps.drive",
    "coscc.runner.steps:Steps.rerun_offers",
    "coscc.runner.steps:Steps.resume_step",
    "coscc.runner.steps:Steps.running_steps",
    "coscc.runner.steps:Steps.set_mode",
    "coscc.runner.steps:Steps.stop_running",
    "coscc.runner.steps:Steps.stop_step",
    "coscc.runner.watch:Watch.events_page",
    "coscc.store.db:Data.prefs",
    "coscc.store.db:Data.step_event",
    "coscc.store.db:Data.step_events_add",
    "coscc.store.db:Data.step_events_page",
    "coscc.store.db:Data.step_run",
    "coscc.store.db:Data.step_runs_open",
    "coscc.store.journal:Journal.append",
    "coscc.store.journal:Journal.append_checked",
    "coscc.store.journal:Journal.append_with",
    "coscc.store.journal:Journal.attempted",
    "coscc.store.journal:Journal.failed_attempts",
    "coscc.store.journal:Journal.finished",
    "coscc.store.journal:Journal.notice_rows",
    "coscc.store.journal:Journal.open_starts",
    "coscc.store.journal:Journal.records",
    "coscc.store.journal:Journal.resumed",
    "coscc.store.journal:Journal.set_mode",
    "coscc.store.journal:Journal.started",
    "coscc.store.journal:Journal.suspended",
    "coscc.store.journal:Journal.timeline",
    "coscc.store.journal:Journal.timelines",
    "coscc.store.journal:Journal.unresumed",
    "coscc.store.journal:add_cost",
    "coscc.store.journal:is_step",
    "coscc.store.journal:last_runs",
    "coscc.store.journal:timelines_of",
    "coscc.store.journal:totals_of",
    "coscc.store.journal:zero_cost",
    "coscc.units.backlog:build_prompt",
    "coscc.units.backlog:check_relation",
    "coscc.units.backlog:check_shortlist",
    "coscc.units.backlog:computed_order",
    "coscc.units.backlog:effort_from",
    "coscc.units.backlog:estimates_of",
    "coscc.units.backlog:fold",
    "coscc.units.backlog:in_backlog",
    "coscc.units.backlog:measured",
    "coscc.units.backlog:parse_proposal",
    "coscc.units.backlog:relations_of",
    "coscc.units.backlog:shortlist_of",
    "coscc.units.backlog:stamp",
    "coscc.units.backlog:undetermined",
    "coscc.units.board:attention_reason",
    "coscc.units.board:gate",
    "coscc.units.board:next_step",
    "coscc.units.board:open_questions",
    "coscc.units.board:read",
    "coscc.units.board:rerun",
    "coscc.units.board:screens",
    "coscc.units.board:shown_state",
    "coscc.units.board:unit_state",
    "coscc.units.history:History.record",
    "coscc.units.history:History.record_in",
    "coscc.units.history:History.record_many",
    "coscc.units.history:History.transitions",
    "coscc.units.hold:record",
    "coscc.units.hold:refusal",
    "coscc.units.ideas:Ideas.create_idea",
    "coscc.units.ideas:create_idea",
    "coscc.units.meta:UnitMeta.snapshot",
    "coscc.units.meta:UnitMeta.snapshot.artifact",
    "coscc.units.meta:UnitMeta.snapshot.entry",
    "coscc.units.more_rounds:refusal",
    "coscc.units.planmap:for_step",
    "coscc.units.planmap:select",
    "coscc.units.read:Board.get",
    "coscc.units.read:Board.read",
    "coscc.units.read:Board.running",
    "coscc.units.read:Board.running_here",
    "coscc.units.retake:describe_for_review",
    "coscc.units.retake:judge",
    "coscc.units.retake:read_manifest",
    "coscc.units.retake:record",
    "coscc.units.retake:take",
    "coscc.units.submit:Channel.handle",
    "coscc.units.submit:Channel.inputs",
    "coscc.units.submit:Collector.handle",
    "coscc.units.submit:Collector.object",
    "coscc.units.submit:refusal",
    "coscc.units.turnstats:pairs",
    "coscc.units.workspaces:Workspaces.add",
    "coscc.units.workspaces:Workspaces.meta_of",
    "coscc.units.workspaces:Workspaces.pull",
    "coscc.units.workspaces:Workspaces.remove",
    "coscc.units.workspaces:Workspaces.set_label",
    "coscc.units.workspaces:Workspaces.snapshot",
    "coscc.units.worktrees:describe_base",
    "coscc.units.worktrees:describe_failure",
    "coscc.units.worktrees:ensure",
    "coscc.units.worktrees:prepare",
    "coscc.units.worktrees:read_prepare",
    "coscc.units.worktrees:refresh_base",
    "coscc.units.worktrees:remove_if_finished",
    "coscc.units:branch_name",
    "coscc.units:create",
    "coscc.update.updater:Updater.apply",
    "coscc.update.updater:Updater.build_local",
    "coscc.update.updater:Updater.cancel",
    "coscc.update.updater:Updater.me",
    "coscc.update.updater:Updater.status",
    "coscc.update.updater:Updater.waited",
    "coscc.update.updater:update_words",
    "coscc.update:fetch_into",
    "coscc.update:identity",
    "coscc.update:read_json",
    "coscc.update:verified_wheel",
    "coscc.update:write_json",
}

CREATE = re.compile(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", re.IGNORECASE)
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


def feature_owners(trees: dict[str, ast.AST]) -> dict[str, str]:
    """Each table a module under `coscc/features/` creates, owned by that module."""
    return {
        name: _dotted(path)
        for path, tree in trees.items()
        if path.startswith("coscc/features/")
        for s in _sql_strings(tree)
        for name in CREATE.findall(s)
    }


def table_names(trees: dict[str, ast.AST]) -> set[str]:
    """Tables created in `coscc/store/db.py` and in the modules under `coscc/features/`."""
    found = set(feature_owners(trees))
    for s in _sql_strings(trees["coscc/store/db.py"]):
        found.update(CREATE.findall(s))
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


def dict_any_keys(trees: dict[str, ast.AST]) -> set[str]:
    """`module:qualname` of each public function or method with `dict[str, Any]` in a parameter
    or the return type; the qualname includes the enclosing classes and functions."""
    found: set[str] = set()

    def visit(node: ast.AST, module: str, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, module, (*scope, child.name))
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                if not child.name.startswith("_") and _takes_dict_any(child):
                    found.add(f"{module}:{'.'.join((*scope, child.name))}")
                visit(child, module, (*scope, child.name))
            else:
                visit(child, module, scope)

    for path, tree in trees.items():
        visit(tree, _dotted(path), ())
    return found


def _takes_dict_any(f: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    args = f.args
    notes = [a.annotation for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]]
    notes += [args.vararg and args.vararg.annotation, args.kwarg and args.kwarg.annotation]
    notes.append(f.returns)
    return any(a is not None and "dict[str, Any]" in ast.unparse(a) for a in notes)


def dict_any_problems(found: set[str], listed: set[str]) -> list[str]:
    out = [
        f"{key} takes or returns dict[str, Any]: type it: a dataclass, a TypedDict or a Literal."
        for key in sorted(found - listed)
    ]
    out += [
        f"DICT_ANY lists {key}, which is gone: delete that entry." for key in sorted(listed - found)
    ]
    return out


def _parse(**sources: str) -> dict[str, ast.AST]:
    return {f"coscc/{name}.py": ast.parse(src) for name, src in sources.items()}


class NoPrivateNameCrossesAModule(unittest.TestCase):
    """A name with one leading underscore is imported only by its own module."""

    def test_no_new_private_import_and_no_stale_entry(self):
        self.assertEqual(private_import_problems(_trees(), PRIVATE_IMPORTS), [])

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
        owners = OWNERS | feature_owners(trees)
        self.assertEqual(table_problems(trees, table_names(trees), owners, FOREIGN_SQL), [])

    def test_a_feature_table_is_owned_by_its_module_and_foreign_sql_names_the_owner(self):
        trees = {
            "coscc/store/db.py": ast.parse("pass\n"),
            "coscc/features/x.py": ast.parse(
                'FEATURE = Feature(tables=("CREATE TABLE IF NOT EXISTS t (a INTEGER)",))\n'
            ),
            "coscc/other.py": ast.parse('q = "SELECT a FROM t"\n'),
        }
        owners = feature_owners(trees)
        self.assertEqual(owners, {"t": "coscc.features.x"})
        self.assertEqual(table_names(trees), {"t"})
        self.assertEqual(
            table_problems(trees, {"t"}, owners, set()),
            [
                "coscc/other.py runs SQL on `t`, which coscc/features/x.py owns. Add a function "
                "to coscc/features/x.py that does it, and call that."
            ],
        )

    def test_a_listed_use_that_is_gone_says_to_delete_it(self):
        (msg,) = table_problems(_parse(a="pass\n"), {"t"}, {"t": "coscc.b"}, {("coscc.a", "t")})
        self.assertIn("Delete that entry.", msg)


class CallsAreTyped(unittest.TestCase):
    """Public functions do not take or return a bare `dict[str, Any]`."""

    def test_no_new_dict_any_and_no_stale_entry(self):
        self.assertEqual(dict_any_problems(dict_any_keys(_trees()), DICT_ANY), [])

    def test_a_planted_public_function_counts_but_a_private_one_does_not(self):
        src = (
            "def a(x: dict[str, Any]): ...\n"
            "async def b() -> list[dict[str, Any]]: ...\n"
            "def _c(x: dict[str, Any]): ...\n"
            "def d(x: dict[str, int]): ...\n"
        )
        self.assertEqual(dict_any_keys(_parse(m=src)), {"coscc.m:a", "coscc.m:b"})

    def test_a_new_method_of_the_same_name_fails_and_a_stale_entry_says_to_delete_it(self):
        src = (
            "class A:\n    def read(self) -> dict[str, Any]: ...\n"
            "class B:\n    def read(self) -> dict[str, Any]: ...\n"
        )
        found = dict_any_keys(_parse(m=src))
        self.assertEqual(found, {"coscc.m:A.read", "coscc.m:B.read"})
        (msg,) = dict_any_problems(found, {"coscc.m:A.read"})
        self.assertEqual(
            msg,
            "coscc.m:B.read takes or returns dict[str, Any]: type it: a dataclass, a TypedDict "
            "or a Literal.",
        )
        (msg,) = dict_any_problems(set(), {"coscc.m:A.read"})
        self.assertIn("delete that entry", msg)


if __name__ == "__main__":
    unittest.main()


# A feature's name the core may still write, with why. Anything else is the core knowing a
# feature: what it needs comes from the feature's own `Feature` (its sessions, tables, parts).
CORE_MAY_NAME: dict[tuple[str, str], str] = {
    ("*", "scratch"): "the kernel makes each unit's scratch directories and a spike's; the "
    "feature only tells the agent about them",
    ("coscc/agent/policy.py", "vault"): "the vault's store is a protected path even with the "
    "vault off",
    ("coscc/agent/policy.py", "release"): "`gh release`, which no session may run, not the feature",
    ("coscc/update/updater.py", "release"): "the update channel of that name, not the feature",
    ("coscc/update/__init__.py", "release"): "the update channel of that name, not the feature",
    ("coscc/loop/branch.py", "release"): "the kind of tag `check-tag` prints, not the feature",
}


def _docstrings(tree: ast.AST) -> set[int]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                out.add(id(first.value))
    return out


def feature_names_in_core(trees: dict[str, ast.AST], names: tuple[str, ...]) -> list[str]:
    """Each string in core code, docstrings aside, that is a feature's name or one of its event
    subjects (`<name>.<state>`). `coscc/features/` and the vault's own package are not core."""
    out = []
    for path, tree in sorted(trees.items()):
        if path.startswith(("coscc/features/", "coscc/vault/")):
            continue
        docs = _docstrings(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if id(node) in docs:
                continue
            for name in names:
                if node.value != name and not node.value.startswith(name + "."):
                    continue
                if ("*", name) in CORE_MAY_NAME or (path, name) in CORE_MAY_NAME:
                    continue
                out.append(
                    f"{path}:{node.lineno} names the feature {name}: let the feature declare it "
                    "on its `Feature`, or list it in CORE_MAY_NAME with why."
                )
    return out


class CoreNamesNoFeature(unittest.TestCase):
    def test_no_core_module_names_a_feature(self):
        names = tuple(f.name for f in features.FEATURES)
        self.assertEqual(feature_names_in_core(_trees(), names), [])

    def test_a_planted_name_or_event_counts_but_prose_does_not(self):
        src = (
            '"""The scan feature."""\n'
            'X = "scan"\n'
            'Y = "scan.ended"\n'
            'Z = "a scan of this workspace"\n'
            'W = "scratch"\n'
        )
        found = feature_names_in_core({"coscc/leif/m.py": ast.parse(src)}, ("scan", "scratch"))
        self.assertEqual(
            [f.split(" names")[0] for f in found], ["coscc/leif/m.py:2", "coscc/leif/m.py:3"]
        )
        self.assertEqual(
            feature_names_in_core({"coscc/features/scan.py": ast.parse(src)}, ("scan",)), []
        )

    def test_every_allowed_name_is_still_written(self):
        written = {
            (path, node.value)
            for path, tree in _trees().items()
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        stale = [
            key
            for key in CORE_MAY_NAME
            if not any(v == key[1] and key[0] in ("*", p) for p, v in written)
        ]
        self.assertEqual(stale, [])
