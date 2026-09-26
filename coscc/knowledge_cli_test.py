"""`0090` plan step 8: `coscc knowledge ...`, and that nothing but a terminal reaches it (R9)."""

from __future__ import annotations

import ast
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc import admit, gather, knowledge, knowledge_cli, units
from coscc.admit_test import lock, make_repo
from coscc.journal import Journal

REPO = Path(__file__).resolve().parents[1]
SLOT = "proj-aaaaaaaaaaaa"


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.data = self.root / "data"
        self.env = {"COS_DATA_DIR": str(self.data), "COS_WORKING_DIR": str(self.root)}
        self.said: list[str] = []

    def run_cli(self, *argv: str, env: dict | None = None) -> int:
        with mock.patch.dict("os.environ", self.env if env is None else env, clear=True):
            return knowledge_cli.main(list(argv), self.said.append)

    def a_source(self) -> None:
        d = self.data / "units" / SLOT / ".cos" / "0001_a"
        d.mkdir(parents=True)
        (d / "spike.md").write_text("# Spike\n## U1\nx\n", encoding="utf-8")


class Misuse(Fixture):
    def test_an_unknown_command_or_flag_is_2_with_the_usage(self):
        for argv in ([], ["forget"], ["gather", "--everything"], ["show", "x"], ["baseline", "--workspace"],
                     ["gather", "--all", "--all"], ["check", "--all"]):
            with self.subTest(argv=argv):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(self.run_cli(*argv), 2)
                self.assertIn("usage: coscc knowledge gather", err.getvalue())


class Gather(Fixture):
    def test_without_a_working_folder_nothing_is_spent(self):
        self.a_source()
        with mock.patch.object(gather, "gather", side_effect=AssertionError("ran")):
            self.assertEqual(self.run_cli("gather", "--yes", env={"COS_DATA_DIR": str(self.data)}), 2)
        self.assertIn("COS_WORKING_DIR", self.said[-1])

    def test_without_yes_it_only_says_what_it_would_spend(self):
        self.a_source()
        with mock.patch.object(gather, "gather", side_effect=AssertionError("ran")), \
                mock.patch("coscc.sessions.Sessions", side_effect=AssertionError("a session")):
            self.assertEqual(self.run_cli("gather", "--all"), 0)
        self.assertIn("1 source(s) in 1 batch(es); at most $2.00", self.said[0])
        self.assertIn("nothing was run: add --yes", self.said[-1])

    def test_with_yes_it_gathers_in_the_mode_asked(self):
        self.a_source()
        seen = {}

        async def fake(data_dir, journal, sessions, model, mode, say):
            seen.update(mode=mode, model=model)
            return 0

        with mock.patch.object(gather, "gather", fake):
            self.assertEqual(self.run_cli("gather", "--all", "--yes", "--model", "m1"), 0)
        self.assertEqual(seen, {"mode": "all", "model": "m1"})

    def test_a_store_with_a_block_it_cannot_read_is_refused_even_without_yes(self):
        self.a_source()
        knowledge.save(knowledge.path_of(str(self.data)) / knowledge.STORE, "# Knowledge\n\n## K1\nno scope\n")
        with mock.patch.object(gather, "gather", side_effect=AssertionError("ran")):
            self.assertEqual(self.run_cli("gather", "--all"), 2)
        self.assertIn("K1 has no Scope:", self.said[-1])

    def unfinished(self) -> Path:
        """`0107` R4's state, written by hand: SLOT's source passed, another slot's has not."""
        self.a_source()
        d = self.data / "units" / "other-bbbbbbbbbbbb" / ".cos" / "0001_a"
        d.mkdir(parents=True)
        (d / "spike.md").write_text("# Spike\n## U1\ny\n", encoding="utf-8")
        passed = {s["label"]: s["sha"] for s in gather.sources(str(self.data)) if s["slot"] == SLOT}
        path = knowledge.path_of(str(self.data)) / gather.PROGRESS
        gather.save_progress(path, {"started": "t", "done": passed, "rebuilt_recorded": True})
        return path

    def test_without_yes_an_unfinished_all_says_what_is_left(self):
        path = self.unfinished()
        with mock.patch.object(gather, "gather", side_effect=AssertionError("ran")), \
                mock.patch("coscc.sessions.Sessions", side_effect=AssertionError("a session")):
            self.assertEqual(self.run_cli("gather", "--all"), 0)
        self.assertEqual(self.said[0], f"an unfinished --all ({path}): 1 source(s) passed; 1 batch(es) left, at most $2.00")
        self.assertIn("nothing was run: add --yes", self.said[-1])

    def test_new_is_refused_while_an_all_is_unfinished(self):
        path = self.unfinished()
        for argv in (["gather"], ["gather", "--yes"]):
            with self.subTest(argv=argv):
                with mock.patch.object(gather, "gather", side_effect=AssertionError("ran")), \
                        mock.patch("coscc.sessions.Sessions", side_effect=AssertionError("a session")):
                    self.assertEqual(self.run_cli(*argv), 2)
                self.assertIn(str(path), self.said[-1])

    def test_a_progress_record_that_does_not_read_fails_before_anything_is_spent(self):
        path = self.unfinished()
        path.write_text("{not json", encoding="utf-8")
        with mock.patch.object(gather, "gather", side_effect=AssertionError("ran")):
            self.assertEqual(self.run_cli("gather", "--all", "--yes"), 1)
        self.assertIn(str(path), self.said[-1])

    def test_with_yes_a_resume_with_nothing_left_still_runs_to_finish(self):
        path = self.unfinished()
        every = {s["label"]: s["sha"] for s in gather.sources(str(self.data))}
        gather.save_progress(path, {"started": "t", "done": every, "rebuilt_recorded": True})
        seen = {}

        async def fake(data_dir, journal, sessions, model, mode, say):
            seen.update(mode=mode)
            return 0

        with mock.patch.object(gather, "gather", fake):
            self.assertEqual(self.run_cli("gather", "--all", "--yes"), 0)
        self.assertEqual(seen, {"mode": "all"})


class Show(Fixture):
    def test_no_store_yet(self):
        self.assertEqual(self.run_cli("show"), 0)
        self.assertIn("no store yet", self.said[-1])

    def test_a_store_by_scope_and_by_workspace(self):
        self.a_source()
        text = (
            "# Knowledge\nVersion: 2. Gathered: 2026-09-27T00:00:00Z. Max id: K2.\n\n"
            f"## K1\nScope: tool:x\nSource: {SLOT}/0001_a/spike.md ## U1\nMeasured: 2026-09-25\nA.\n\n"
            f"## K2\nScope: workspace:{SLOT}\nSource: {SLOT}/0001_a/spike.md ## U1\nMeasured: 2026-09-25\nB.\n"
        )
        knowledge.save(knowledge.path_of(str(self.data)) / knowledge.STORE, text)
        self.assertEqual(self.run_cli("show"), 0)
        out = "\n".join(self.said)
        self.assertIn("Version: 2. Gathered: 2026-09-27T00:00:00Z. Max id: K2.", out)
        self.assertIn("  tool:x: 1", out)
        self.assertIn(f"{SLOT}: 2 apply, 2 carried", out)


class Check(Fixture):
    """`0108` R10, on a real repository whose `main` pins `claude-agent-sdk` 0.2.159."""

    def setUp(self):
        super().setUp()
        self.repo = make_repo(self.root / "proj", ("2026-09-20T00:00:00+00:00", {
            ".python-version": "3.14\n", "uv.lock": lock(**{"claude-agent-sdk": "0.2.159"}),
            "coscc/gather.py": "def batches():\n    pass\n"}))
        self.slot = units.slot(self.repo)
        Journal(self.root, self.data).finished(str(self.repo), "0001_a", "spike", "done")
        self.src = f"{self.slot}/0001_a/spike.md ## U1"

    def store(self, *blocks: tuple[str, str]) -> Path:
        """`(scope, ref)` per entry, K1 upward; `ref` only on a `workspace:` entry."""
        text = "# Knowledge\nVersion: 1. Gathered: x. Max id: K9.\n\n" + "\n\n".join(
            f"## K{n}\nScope: {scope}\nSource: {self.src}\n" + (f"Ref: {ref}\n" if ref else "")
            + "Measured: 2026-09-25\nA fact."
            for n, (scope, ref) in enumerate(blocks, 1)) + "\n"
        path = knowledge.path_of(str(self.data)) / knowledge.STORE
        knowledge.save(path, text)
        return path

    def test_every_entry_passing_is_0(self):
        self.store(("tool:claude-agent-sdk 0.2.159", ""), ("tool:python 3.14", ""),
                   (f"workspace:{self.slot}", "coscc/gather.py::batches"))
        self.assertEqual(self.run_cli("check"), 0, self.said)
        out = "\n".join(self.said)
        sha = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "main"], capture_output=True, text=True).stdout
        self.assertIn(f"main of {self.slot}: {sha[:12]} 2026-09-20T00:00:00Z", out)
        self.assertIn("K1 tool:claude-agent-sdk 0.2.159: pass", out)
        self.assertIn(f"K3 workspace:{self.slot}: pass", out)
        self.assertIn("tool: 2/3 = 67% (needs >= 50%)", out)
        self.assertRegex(out, r"checked on \d{4}-\d{2}-\d{2}")

    def test_a_version_main_no_longer_has_is_1(self):
        self.store(("tool:claude-agent-sdk 0.2.158", ""))
        self.assertEqual(self.run_cli("check"), 1)
        self.assertIn(f"K1 tool:claude-agent-sdk 0.2.158: fail: version 0.2.158, main has 0.2.159 in {self.slot}",
                      self.said)

    def test_a_tool_with_no_pin_or_no_version_is_1(self):
        self.store(("tool:gh 2.0", ""), ("tool:python", ""))
        self.assertEqual(self.run_cli("check"), 1)
        self.assertIn(f"K1 tool:gh 2.0: fail: no pin in {self.slot}", self.said)
        self.assertIn("K2 tool:python: fail: cannot be checked", self.said)

    def test_a_missing_ref_is_1(self):
        self.store(("tool:python 3.14", ""), (f"workspace:{self.slot}", "coscc/gather.py::gather"))
        self.assertEqual(self.run_cli("check"), 1)
        self.assertIn(f"K2 workspace:{self.slot}: fail: ref missing: coscc/gather.py::gather", self.said)

    def test_one_tool_entry_in_three_is_1(self):
        ws = (f"workspace:{self.slot}", "coscc/gather.py")
        self.store(("tool:python 3.14", ""), ws, ws)
        self.assertEqual(self.run_cli("check"), 1)
        self.assertIn("tool: 1/3 = 33% (needs >= 50%)", self.said)

    def test_an_empty_store_is_1(self):
        self.store()
        self.assertEqual(self.run_cli("check"), 1)
        self.assertIn("tool: 0/0 = 0% (needs >= 50%)", self.said)

    def test_no_store_is_2(self):
        self.assertEqual(self.run_cli("check"), 2)
        self.assertIn("the store cannot be read", self.said[-1])

    def test_no_git_is_2(self):
        self.store(("tool:python 3.14", ""))
        with mock.patch.object(admit, "_git", side_effect=admit.GitError("no git")):
            self.assertEqual(self.run_cli("check"), 2)

    def test_it_writes_nothing(self):
        path = self.store(("tool:python 3.14", ""), (f"workspace:{self.slot}", "coscc/gather.py::gone"))
        db = self.data / "cos.db"
        before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in (path, db)]
        rows = Journal(self.root, self.data).records()
        self.assertEqual(self.run_cli("check"), 1)
        self.assertEqual([(p.read_bytes(), p.stat().st_mtime_ns) for p in (path, db)], before)
        self.assertEqual(Journal(self.root, self.data).records(), rows)


class BaselineAndMeasure(Fixture):
    def test_measure_with_no_baseline_is_refused(self):
        self.assertEqual(self.run_cli("measure"), 2)
        self.assertIn("no baseline", self.said[-1])

    def test_a_baseline_with_no_database_fails(self):
        self.assertEqual(self.run_cli("baseline"), 1)


class OnlyATerminalReachesIt(unittest.TestCase):
    """R9: no route, no autopilot pass and no service call imports what gathers or measures.
    Read from the source, because a route added later is exactly what this is for."""

    FORBIDDEN = {"gather", "knowledge_cli", "measure", "admit"}

    def imported(self, path: Path) -> set[str]:
        names: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[-1] for a in node.names if a.name.startswith("coscc.")}
            elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("coscc"):
                if node.module == "coscc":
                    names |= {a.name for a in node.names}
                else:
                    names.add(node.module.split(".")[-1])
        return names

    def test_api_autopilot_and_service_import_none_of_it(self):
        # `0095`: `Service` is spread over `service.py` and the `service_*.py` it was split into.
        split = [p.name for p in sorted((REPO / "coscc").glob("service_*.py")) if not p.name.endswith("_test.py")]
        self.assertTrue(split)
        for name in ("api.py", "autopilot.py", "service.py", *split):
            with self.subTest(module=name):
                self.assertFalse(self.imported(REPO / "coscc" / name) & self.FORBIDDEN)

    def test_the_check_would_see_one(self):
        with tempfile.TemporaryDirectory() as d:
            probe = Path(d) / "probe.py"
            probe.write_text("from coscc import gather\nimport coscc.measure\nfrom coscc.knowledge_cli import main\n"
                             "from coscc.admit import check\n")
            self.assertEqual(self.imported(probe) & self.FORBIDDEN, self.FORBIDDEN)


if __name__ == "__main__":
    unittest.main()
