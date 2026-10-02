"""`coscc/features/codegraph/query.py`: the text an agent gets from the index of main."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from collections.abc import Callable, Mapping
from pathlib import Path

from coscc.features.codegraph.graph import (
    CHANGED,
    MAP_BUDGET,
    TOOL_BUDGET,
    GitError,
    callers,
    changed_files,
    find,
    impact,
    impl_map,
    old_hunks,
    review_map,
)

SHA = "a1b2c3d4e5f6" + "0" * 28
HEAD = "Index of main at a1b2c3d4e5f6."


def node(name: str, file: str, start: int = 1, end: int = 5, kind: str = "function", sig=None):
    return {
        "id": f"{file}:{name}",
        "name": name,
        "kind": kind,
        "file": file,
        "start_line": start,
        "end_line": end,
        "signature": f"def {name}()" if sig is None else sig,
    }


def edge(target: dict, caller: dict, line: int | None = 3, resolution: str = "exact"):
    return {"target": target, "caller": caller, "line": line, "resolution": resolution}


class Bridge:
    """A fake `ask`: canned answers by op, and a record of every call."""

    def __init__(self, **answers: object):
        self.answers = answers
        self.calls: list[tuple[str, Mapping[str, object]]] = []

    def __call__(self, op: str, args: Mapping[str, object]) -> object:
        self.calls.append((op, args))
        got = self.answers.get(op, [])
        return got(args) if callable(got) else got

    def ops(self) -> list[str]:
        return [op for op, _ in self.calls]


def by_name(table: dict[str, list]) -> Callable[[Mapping[str, object]], object]:
    return lambda args: table.get(str(args["name"]), [])


def git(tree: str, *args: str) -> str:
    argv = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", tree, *args]
    return subprocess.run(argv, capture_output=True, text=True, check=True).stdout.strip()


class Tmp(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tree = str(Path(holder.name).resolve())

    def write(self, rel: str, text: str = "x = 1\n") -> None:
        path = Path(self.tree, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


class EveryResultSaysWhoseLinesThey(Tmp):
    def test_the_first_line_names_main_and_says_the_lines_are_its(self):
        ask = Bridge(find=[node("f", "a.py")])
        first = find(ask, SHA, set(), self.tree, "f").splitlines()[0]
        self.assertTrue(first.startswith(HEAD))
        self.assertIn("main's", first)

    def test_an_empty_answer_is_the_head_and_one_line(self):
        ask = Bridge()
        self.assertEqual(len(find(ask, SHA, set(), self.tree, "nothing").splitlines()), 2)
        self.assertEqual(len(callers(ask, SHA, set(), "nothing").splitlines()), 2)
        self.assertEqual(len(impact(ask, SHA, set(), self.tree, "m.py").splitlines()), 2)

    def test_a_map_with_nothing_to_show_is_empty(self):
        ask = Bridge()
        self.assertEqual(impl_map(ask, SHA, set(), ["a.py"]), "")
        self.assertEqual(review_map(ask, SHA, set(), {"a.py": [(1, 2)]}), "")
        self.assertEqual(impl_map(ask, SHA, set(), []), "")
        self.assertEqual(review_map(ask, SHA, set(), {}), "")


class GuessedEdgesAndFileNodesAreLeftOut(Tmp):
    def test_find_drops_file_nodes_and_malformed_items(self):
        ask = Bridge(
            find=[node("keep", "a.py"), node("a.py", "a.py", kind="file"), "junk", {"name": 1}]
        )
        out = find(ask, SHA, set(), self.tree, "keep")
        self.assertIn("keep", out)
        self.assertNotIn("kind file", out)
        self.assertEqual(out.count("L1-5"), 1)

    def test_the_outline_of_a_file_drops_its_file_node(self):
        ask = Bridge(outline=[node("m.py", "m.py", kind="file"), node("run", "m.py")])
        out = find(ask, SHA, set(), self.tree, "m.py")
        self.assertIn("run", out)
        self.assertNotIn("function m.py", out)
        self.assertNotIn(" file ", out)

    def test_callers_drops_fuzzy_edges_and_file_level_callers(self):
        target = node("go", "t.py")
        ask = Bridge(
            callers=[
                edge(target, node("sure", "s.py")),
                edge(target, node("guess", "g.py"), resolution="fuzzy"),
                edge(target, node("s.py", "top.py", kind="file")),
            ]
        )
        out = callers(ask, SHA, set(), "go")
        self.assertIn("s.py", out)
        self.assertNotIn("g.py", out)
        self.assertNotIn("top.py", out)

    def test_impact_drops_fuzzy_edges(self):
        ask = Bridge(
            impact=[
                {"file": "sure.py", "line": 1, "resolution": "exact"},
                {"file": "guess.py", "line": 1, "resolution": "fuzzy"},
            ]
        )
        out = impact(ask, SHA, set(), self.tree, "m.py")
        self.assertIn("sure.py", out)
        self.assertNotIn("guess.py", out)

    def test_the_maps_drop_them_too(self):
        target = node("go", "t.py", 1, 9)
        ask = Bridge(
            outline=[target, node("t.py", "t.py", kind="file")],
            callers=[
                edge(target, node("sure", "s.py")),
                edge(target, node("guess", "g.py"), resolution="fuzzy"),
            ],
        )
        for out in (
            impl_map(ask, SHA, set(), ["t.py"]),
            review_map(ask, SHA, set(), {"t.py": [(1, 2)]}),
        ):
            self.assertIn("sure", out)
            self.assertNotIn("guess", out)
            self.assertNotIn(" file ", out)


class ChangedFilesAreMarked(Tmp):
    def test_only_a_changed_file_carries_the_label(self):
        ask = Bridge(find=[node("one", "a.py"), node("two", "b.py")])
        out = find(ask, SHA, {"a.py"}, self.tree, "x")
        self.assertEqual(out.count(CHANGED), 1)
        self.assertIn(f"a.py ({CHANGED})", out)
        self.assertIn("\nb.py\n", out)

    def test_the_label_text_is_the_fixed_one(self):
        self.assertEqual(CHANGED, "changed in this unit: Read for current lines")

    def test_a_caller_in_a_changed_file_is_marked_in_callers_and_impl_map(self):
        target = node("go", "t.py")
        ask = Bridge(
            outline=[target],
            callers=[edge(target, node("user", "c.py")), edge(target, node("other", "d.py"))],
        )
        for out in (
            callers(ask, SHA, {"c.py"}, "go"),
            impl_map(ask, SHA, {"c.py"}, ["t.py"]),
        ):
            self.assertEqual(out.count(CHANGED), 1)
            self.assertIn(f"c.py ({CHANGED})", out)

    def test_an_importer_in_a_changed_file_is_marked(self):
        ask = Bridge(impact=[{"file": "c.py", "line": 2, "resolution": "exact"}])
        self.assertIn(f"c.py ({CHANGED})", impact(ask, SHA, {"c.py"}, self.tree, "m.py"))


class AnAnswerIsCutToItsBudget(Tmp):
    def many(self, count: int, **extra) -> list:
        return [node(f"fn_{i:03d}", "big.py", i * 10, i * 10 + 5, **extra) for i in range(count)]

    def left_out(self, out: str) -> int:
        last = out.splitlines()[-1]
        self.assertRegex(last, r"^… \d+ more not shown\.$")
        return int(last.split()[1])

    def test_a_tool_result_stays_within_four_thousand_and_counts_what_is_left(self):
        total = 300
        out = find(Bridge(find=self.many(total)), SHA, set(), self.tree, "fn")
        self.assertLessEqual(len(out), TOOL_BUDGET)
        shown = out.count("function fn_")
        self.assertGreater(shown, 10)
        self.assertEqual(shown + self.left_out(out), total)

    def test_the_total_is_close_to_the_budget_not_far_below(self):
        out = find(Bridge(find=self.many(300)), SHA, set(), self.tree, "fn")
        self.assertGreater(len(out), TOOL_BUDGET - 200)

    def test_a_cut_happens_at_item_boundaries(self):
        out = find(Bridge(find=self.many(300)), SHA, set(), self.tree, "fn")
        for line in out.splitlines()[2:-1]:
            self.assertRegex(line, r"^  function fn_\d{3} L\d+-\d+: def fn_\d{3}\(\)$")

    def test_callers_and_impact_are_cut_the_same_way(self):
        target = node("go", "t.py")
        sites = [edge(target, node(f"c{i}", f"pkg/mod_{i}.py", 1, 9)) for i in range(200)]
        out = callers(Bridge(callers=sites), SHA, set(), "go")
        self.assertLessEqual(len(out), TOOL_BUDGET)
        self.assertEqual(out.count(" in c") + self.left_out(out), 200)
        files = [{"file": f"src/mod_{i}.py", "line": 1, "resolution": "exact"} for i in range(400)]
        out = impact(Bridge(impact=files), SHA, set(), self.tree, "m.py")
        self.assertLessEqual(len(out), TOOL_BUDGET)
        self.assertEqual(out.count("  src/mod_") + self.left_out(out), 400)

    def test_a_map_stays_within_six_thousand_and_counts_what_is_left(self):
        total = 200
        out = impl_map(Bridge(outline=self.many(total)), SHA, set(), ["big.py"])
        self.assertLessEqual(len(out), MAP_BUDGET)
        self.assertGreater(len(out), TOOL_BUDGET)
        self.assertEqual(out.count("function fn_") + self.left_out(out), total)

    def test_a_review_map_is_cut_too(self):
        syms = self.many(100)
        table = {
            s["name"]: [edge(s, node(f"use{k}", f"o/{s['name']}_{k}.py", 1, 4)) for k in range(5)]
            for s in syms
        }
        hunks = {"big.py": [(0, 10_000)]}
        out = review_map(Bridge(outline=syms, callers=by_name(table)), SHA, set(), hunks)
        self.assertLessEqual(len(out), MAP_BUDGET)
        self.assertEqual(out.count("function fn_") + self.left_out(out), 30)

    def test_a_long_signature_is_shortened(self):
        out = find(
            Bridge(find=[node("f", "a.py", sig="def f(" + "x, " * 500 + ")")]),
            SHA,
            set(),
            self.tree,
            "f",
        )
        self.assertLess(len(out), 400)


class APathOutsideTheRepositoryIsRefused(Tmp):
    def refused(self, out: str, ask: Bridge) -> None:
        self.assertIn("Refused", out)
        self.assertTrue(out.startswith(HEAD))
        self.assertEqual(ask.calls, [])

    def test_find_refuses_a_parent_escape(self):
        ask = Bridge(outline=[node("f", "x")])
        self.refused(find(ask, SHA, set(), self.tree, "../x"), ask)
        self.refused(find(ask, SHA, set(), self.tree, "a/../../x.py"), ask)

    def test_find_refuses_an_absolute_path_elsewhere(self):
        ask = Bridge(outline=[node("f", "x")])
        self.refused(find(ask, SHA, set(), self.tree, "/etc/passwd"), ask)

    def test_find_refuses_a_symlink_that_points_out(self):
        elsewhere = tempfile.TemporaryDirectory()
        self.addCleanup(elsewhere.cleanup)
        Path(elsewhere.name, "secret.py").write_text("x = 1\n")
        os.symlink(elsewhere.name, Path(self.tree, "link"))
        os.symlink(Path(elsewhere.name, "secret.py"), Path(self.tree, "leak.py"))
        ask = Bridge(outline=[node("f", "x")])
        self.refused(find(ask, SHA, set(), self.tree, "link/secret.py"), ask)
        self.refused(find(ask, SHA, set(), self.tree, "leak.py"), ask)

    def test_impact_refuses_the_same_paths(self):
        ask = Bridge(impact=[{"file": "x.py", "line": 1, "resolution": "exact"}])
        for raw in ("../x.py", "/etc/passwd", "a/../../x.py"):
            self.refused(impact(ask, SHA, set(), self.tree, raw), ask)

    def test_an_absolute_path_inside_the_tree_is_asked_as_relative(self):
        self.write("pkg/m.py")
        ask = Bridge(outline=[node("f", "pkg/m.py")])
        out = find(ask, SHA, set(), self.tree, str(Path(self.tree, "pkg", "m.py")))
        self.assertIn("function f", out)
        self.assertEqual(ask.calls, [("outline", {"paths": ["pkg/m.py"]})])


class AFilePathQueryGivesTheOutline(Tmp):
    def test_a_path_asks_for_the_outline_and_lists_symbols_without_code(self):
        self.write("pkg/m.py")
        ask = Bridge(
            outline=[
                node("late", "pkg/m.py", 40, 50, sig="def late(a: int) -> str"),
                node("Early", "pkg/m.py", 3, 30, kind="class", sig="class Early"),
            ]
        )
        out = find(ask, SHA, set(), self.tree, "pkg/m.py")
        self.assertEqual(ask.ops(), ["outline"])
        self.assertEqual(ask.calls[0][1], {"paths": ["pkg/m.py"]})
        self.assertIn("class Early L3-30", out)
        self.assertIn("function late L40-50: def late(a: int) -> str", out)
        self.assertLess(out.index("Early"), out.index("late"))

    def test_a_bare_file_name_with_a_source_ending_is_a_path(self):
        ask = Bridge(outline=[node("f", "m.py")])
        find(ask, SHA, set(), self.tree, "m.py")
        self.assertEqual(ask.ops(), ["outline"])

    def test_a_name_is_a_search_with_a_limit(self):
        ask = Bridge(find=[node("handler", "h.py")])
        out = find(ask, SHA, set(), self.tree, "handler")
        self.assertEqual(ask.ops(), ["find"])
        self.assertEqual(ask.calls[0][1]["query"], "handler")
        self.assertGreater(int(ask.calls[0][1]["limit"]), 0)  # type: ignore[call-overload]
        self.assertIn("h.py", out)
        self.assertIn("function handler L1-5", out)


class ImpactListsDirectImportersAndSplitsTests(Tmp):
    def test_tests_are_listed_apart_after_the_rest(self):
        importers = [
            "src/a.py",
            "tests/test_a.py",
            "web/a.spec.ts",
            "pkg/a_test.go",
            "lib/a.test.js",
            "test/helper.py",
            "contest/b.py",
            "src/attest.py",
            "src/test_helpers.py",
        ]
        ask = Bridge(impact=[{"file": f, "line": 1, "resolution": "exact"} for f in importers])
        out = impact(ask, SHA, set(), self.tree, "src/m.py")
        self.assertEqual(ask.calls, [("impact", {"path": "src/m.py"})])
        cut = out.index("Tests importing it:")
        before, after = out[:cut], out[cut:]
        for f in ("src/a.py", "contest/b.py", "src/attest.py"):
            self.assertIn(f"  {f}", before)
        for f in importers[1:6] + ["src/test_helpers.py"]:
            self.assertIn(f"  {f}", after)
        self.assertNotIn("contest/b.py", after)

    def test_a_list_without_tests_has_no_tests_heading(self):
        ask = Bridge(impact=[{"file": "src/a.py", "line": None, "resolution": "exact"}])
        out = impact(ask, SHA, set(), self.tree, "src/m.py")
        self.assertNotIn("Tests", out)
        self.assertIn("Imported directly by:", out)

    def test_it_asks_once_and_never_for_dependents_of_dependents(self):
        ask = Bridge(impact=[{"file": "src/a.py", "line": 1, "resolution": "exact"}])
        impact(ask, SHA, set(), self.tree, "src/m.py")
        self.assertEqual(ask.ops(), ["impact"])


class CallersNameTheSymbolEachCallSitsIn(Tmp):
    def test_each_site_has_file_symbol_range_and_line(self):
        target = node("go", "t.py")
        ask = Bridge(callers=[edge(target, node("main", "app/run.py", 10, 25, "method"), line=17)])
        out = callers(ask, SHA, set(), "go")
        self.assertEqual(ask.calls, [("callers", {"name": "go"})])
        self.assertIn("app/run.py: in main (method) L10-25, call at line 17", out)

    def test_an_unknown_call_line_is_left_out(self):
        target = node("go", "t.py")
        out = callers(
            Bridge(callers=[edge(target, node("m", "a.py"), line=None)]), SHA, set(), "go"
        )
        self.assertNotIn("call at", out)


class ImplMapShowsEachFilesSymbolsAndTheirCallers(Tmp):
    def test_outline_and_callers_per_symbol(self):
        one, two = node("one", "p.py", 1, 9), node("two", "p.py", 10, 20)
        table = {"one": [edge(one, node("use", "q.py", 4, 8), line=6)]}
        ask = Bridge(outline=[one, two], callers=by_name(table))
        out = impl_map(ask, SHA, set(), ["p.py", "./p.py", "../x.py", "/abs.py"])
        self.assertTrue(out.startswith(HEAD))
        self.assertEqual(ask.calls[0], ("outline", {"paths": ["p.py"]}))
        self.assertIn("function one L1-9", out)
        self.assertIn("function two L10-20", out)
        self.assertIn("q.py: in use (function) L4-8, call at line 6", out)

    def test_a_callers_edge_to_a_same_named_symbol_elsewhere_is_not_taken(self):
        mine, other = node("run", "p.py", 1, 9), node("run", "z.py", 1, 9)
        ask = Bridge(outline=[mine], callers=[edge(other, node("use", "q.py"))])
        self.assertNotIn("q.py", impl_map(ask, SHA, set(), ["p.py"]))

    def test_one_call_per_symbol_and_no_more_than_a_few_dozen(self):
        syms = [node(f"s{i}", "p.py", i * 3, i * 3 + 2) for i in range(80)]
        ask = Bridge(outline=syms)
        out = impl_map(ask, SHA, set(), ["p.py"])
        self.assertLessEqual(ask.ops().count("callers"), 40)
        self.assertEqual(ask.ops().count("outline"), 1)
        self.assertIn("function s79", out)

    def test_a_symbol_with_many_callers_lists_a_few_and_counts_the_rest(self):
        target = node("hub", "p.py", 1, 9)
        sites = [edge(target, node(f"c{i}", f"u{i}.py")) for i in range(12)]
        out = impl_map(Bridge(outline=[target], callers=sites), SHA, set(), ["p.py"])
        self.assertEqual(out.count(" in c"), 5)
        self.assertIn("and 7 more callers", out)


class ReviewMapKeepsTouchedSymbolsCalledFromOutside(Tmp):
    def setUp(self):
        super().setUp()
        self.alpha = node("alpha", "m.py", 1, 9)
        self.beta = node("beta", "m.py", 10, 20)
        self.gamma = node("gamma", "m.py", 30, 40)
        self.table = {
            "alpha": [edge(self.alpha, node("a_user", "out_a.py"))],
            "beta": [
                edge(self.beta, node("outside", "out_b.py", 2, 6), line=4),
                edge(self.beta, node("inside", "c.py")),
                edge(self.beta, node("guess", "g.py"), resolution="fuzzy"),
                edge(self.beta, node("self_use", "m.py", 25, 28)),
            ],
            "gamma": [edge(self.gamma, node("g_user", "out_g.py"))],
        }
        self.ask = Bridge(
            outline=[self.alpha, self.beta, self.gamma, node("m.py", "m.py", kind="file")],
            callers=by_name(self.table),
        )

    def test_only_symbols_that_intersect_a_hunk_are_asked_about_and_shown(self):
        out = review_map(self.ask, SHA, {"m.py", "c.py"}, {"m.py": [(11, 12)]})
        asked = [a["name"] for op, a in self.ask.calls if op == "callers"]
        self.assertEqual(asked, ["beta"])
        self.assertIn("function beta L10-20", out)
        self.assertNotIn("alpha", out)
        self.assertNotIn("gamma", out)

    def test_only_callers_outside_the_changed_files_are_kept(self):
        out = review_map(self.ask, SHA, {"m.py", "c.py"}, {"m.py": [(11, 12)]})
        self.assertIn("out_b.py: in outside (function) L2-6, call at line 4", out)
        self.assertNotIn("c.py: in inside", out)
        self.assertNotIn("g.py", out)
        self.assertNotIn("self_use", out)
        self.assertEqual(out.count(CHANGED), 1)  # the touched file, m.py, is stale on main

    def test_a_hunk_touching_one_line_of_a_symbol_is_enough(self):
        for hunk, name in (
            ((9, 9), "alpha"),
            ((10, 10), "beta"),
            ((20, 20), "beta"),
            ((40, 41), "gamma"),
        ):
            ask = Bridge(outline=self.ask.answers["outline"], callers=by_name(self.table))
            out = review_map(ask, SHA, {"m.py"}, {"m.py": [hunk]})
            self.assertIn(f"function {name} ", out, hunk)

    def test_a_hunk_between_symbols_touches_none(self):
        self.assertEqual(review_map(self.ask, SHA, {"m.py"}, {"m.py": [(21, 29)]}), "")

    def test_a_symbol_nobody_outside_calls_is_left_out(self):
        out = review_map(self.ask, SHA, {"m.py", "out_a.py"}, {"m.py": [(1, 2)]})
        self.assertEqual(out, "")

    def test_only_the_files_with_hunks_are_outlined(self):
        review_map(self.ask, SHA, {"m.py"}, {"m.py": [(1, 2)], "n.py": []})
        self.assertEqual(self.ask.calls[0], ("outline", {"paths": ["m.py"]}))


class OldHunksReadTheOldSideOfTheDiff(unittest.TestCase):
    def parse(self, diff: str) -> dict[str, list[tuple[int, int]]]:
        calls: list[list[str]] = []

        def run(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout=diff, stderr="")

        got = old_hunks("/t", SHA, run)
        self.assertEqual(calls[0][:3], ["git", "-C", "/t"])
        self.assertIn("-U0", calls[0])
        self.assertEqual(calls[0][-1], SHA)
        return got

    def test_a_count_gives_a_range(self):
        diff = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -10,3 +10,4 @@ def f():\n-a\n-b\n-c\n+d\n+e\n+f\n+g\n"
        self.assertEqual(self.parse(diff), {"x.py": [(10, 12)]})

    def test_an_omitted_count_is_one_line(self):
        diff = "--- a/x.py\n+++ b/x.py\n@@ -7 +7 @@\n-a\n+b\n"
        self.assertEqual(self.parse(diff), {"x.py": [(7, 7)]})

    def test_a_zero_count_is_an_insertion_and_the_line_before_stands_for_it(self):
        diff = "--- a/x.py\n+++ b/x.py\n@@ -5,0 +6,2 @@\n+a\n+b\n"
        self.assertEqual(self.parse(diff), {"x.py": [(5, 5)]})

    def test_a_new_file_has_no_old_side(self):
        diff = "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,2 @@\n+a\n+b\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"
        self.assertEqual(self.parse(diff), {"x.py": [(1, 1)]})

    def test_a_deleted_file_is_all_old(self):
        diff = "--- a/gone.py\n+++ /dev/null\n@@ -1,3 +0,0 @@\n-a\n-b\n-c\n"
        self.assertEqual(self.parse(diff), {"gone.py": [(1, 3)]})

    def test_several_hunks_and_files_are_kept_apart(self):
        diff = (
            "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n@@ -20,2 +20 @@\n-c\n-d\n+e\n"
            "--- a/y/z.py\n+++ b/y/z.py\n@@ -3 +3 @@\n-p\n+q\n"
        )
        self.assertEqual(self.parse(diff), {"x.py": [(1, 1), (20, 21)], "y/z.py": [(3, 3)]})

    def test_a_removed_line_that_looks_like_a_file_header_is_not_one(self):
        diff = "--- a/x.py\n+++ b/x.py\n@@ -4,2 +4,2 @@\n--- not a header\n-b\n+c\n+++ nor this\n"
        self.assertEqual(self.parse(diff), {"x.py": [(4, 5)]})

    def test_a_git_failure_raises(self):
        def run(argv, **kw):
            return subprocess.CompletedProcess(argv, 128, stdout="", stderr="bad object")

        with self.assertRaises(GitError):
            old_hunks("/t", SHA, run)

    def test_git_missing_raises_too(self):
        def run(argv, **kw):
            raise FileNotFoundError("git")

        with self.assertRaises(GitError):
            old_hunks("/t", SHA, run)


class OnARealRepositoryTheChangesAreFound(Tmp):
    def setUp(self):
        super().setUp()
        git(self.tree, "init", "-q")
        self.write(".gitignore", "ignored.txt\n")
        self.write("kept.py", "a = 1\nb = 2\nc = 3\n")
        self.write("moved.py", "m = 1\n")
        git(self.tree, "add", ".")
        git(self.tree, "commit", "-q", "-m", "base")
        self.base = git(self.tree, "rev-parse", "HEAD")

    def test_a_committed_change_is_listed(self):
        self.write("kept.py", "a = 1\nb = 20\nc = 3\n")
        git(self.tree, "commit", "-q", "-am", "edit")
        self.assertEqual(changed_files(self.tree, self.base), {"kept.py"})

    def test_an_uncommitted_change_is_listed(self):
        self.write("kept.py", "a = 10\nb = 2\nc = 3\n")
        self.assertEqual(changed_files(self.tree, self.base), {"kept.py"})

    def test_an_untracked_file_is_listed_and_an_ignored_one_is_not(self):
        self.write("fresh.py")
        self.write("sub/deeper.py")
        self.write("ignored.txt")
        self.assertEqual(changed_files(self.tree, self.base), {"fresh.py", "sub/deeper.py"})

    def test_a_staged_new_file_and_a_removed_file_are_listed(self):
        self.write("added.py")
        git(self.tree, "add", "added.py")
        git(self.tree, "rm", "-q", "moved.py")
        self.assertEqual(changed_files(self.tree, self.base), {"added.py", "moved.py"})

    def test_nothing_changed_is_an_empty_set(self):
        self.assertEqual(changed_files(self.tree, self.base), set())

    def test_an_unknown_commit_raises(self):
        with self.assertRaises(GitError):
            changed_files(self.tree, "f" * 40)

    def test_the_old_side_ranges_come_from_the_real_diff(self):
        self.write("kept.py", "a = 1\nb = 20\nc = 3\nd = 4\n")
        git(self.tree, "commit", "-q", "-am", "edit")
        self.write("kept.py", "new = 0\na = 1\nb = 20\nc = 3\nd = 4\n")
        self.write("fresh.py")
        self.assertEqual(old_hunks(self.tree, self.base), {"kept.py": [(0, 0), (2, 2), (3, 3)]})


if __name__ == "__main__":
    unittest.main()
