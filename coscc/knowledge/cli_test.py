"""`0090` plan step 8: `coscc knowledge ...`, and that nothing but a terminal reaches it (R9)."""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc import knowledge, units
from coscc.knowledge import admit, gather, cli as knowledge_cli
from coscc.knowledge.admit_test import failing_fetch, lock, make_repo, no_fetch
from coscc.runlog.journal import Journal

REPO = Path(__file__).resolve().parents[2]
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
                mock.patch("coscc.agent.sessions.Sessions", side_effect=AssertionError("a session")):
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
                mock.patch("coscc.agent.sessions.Sessions", side_effect=AssertionError("a session")):
            self.assertEqual(self.run_cli("gather", "--all"), 0)
        self.assertEqual(self.said[0], f"an unfinished --all ({path}): 1 source(s) passed; 1 batch(es) left, at most $2.00")
        self.assertIn("nothing was run: add --yes", self.said[-1])

    def test_new_is_refused_while_an_all_is_unfinished(self):
        path = self.unfinished()
        for argv in (["gather"], ["gather", "--yes"]):
            with self.subTest(argv=argv):
                with mock.patch.object(gather, "gather", side_effect=AssertionError("ran")), \
                        mock.patch("coscc.agent.sessions.Sessions", side_effect=AssertionError("a session")):
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
    """`0131` R12, R13: `show` took `check` into it, on a real repository whose `origin/main`
    pins `claude-agent-sdk` 0.2.159, fetched first."""

    def setUp(self):
        super().setUp()
        self.repo = make_repo(self.root / "proj", ("2026-09-20T00:00:00+00:00", {
            ".python-version": "3.14\n", "uv.lock": lock(**{"claude-agent-sdk": "0.2.159"}),
            "coscc/knowledge/gather.py": "def batches():\n    pass\n"}))
        self.slot = units.slot(self.repo)
        Journal(self.root, self.data).finished(str(self.repo), "0001_a", "spike", "done")
        self.src = f"{self.slot}/0001_a/spike.md ## U1"
        self.sha = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "origin/main"],
                                  capture_output=True, text=True).stdout.strip()
        fetch = no_fetch()
        fetch.start()
        self.addCleanup(fetch.stop)

    def store(self, *blocks: tuple[str, str]) -> Path:
        """`(scope, ref)` per entry, K1 upward; `ref` only on a `workspace:` entry."""
        text = "# Knowledge\nVersion: 1. Gathered: x. Max id: K9.\n\n" + "\n\n".join(
            f"## K{n}\nScope: {scope}\nSource: {self.src}\n" + (f"Ref: {ref}\n" if ref else "")
            + "Measured: 2026-09-25\nA fact."
            for n, (scope, ref) in enumerate(blocks, 1)) + "\n"
        path = knowledge.path_of(str(self.data)) / knowledge.STORE
        knowledge.save(path, text)
        return path

    def health(self) -> dict:
        return json.loads((knowledge.path_of(str(self.data)) / knowledge.HEALTH).read_text())

    def test_no_store_yet(self):
        self.assertEqual(self.run_cli("show"), 0)
        self.assertIn("no store yet", self.said[-1])

    def test_every_entry_passing_is_0(self):
        self.store(("tool:claude-agent-sdk 0.2.159", ""), ("tool:python 3.14", ""),
                   (f"workspace:{self.slot}", "coscc/knowledge/gather.py::batches"))
        self.assertEqual(self.run_cli("show"), 0, self.said)
        out = "\n".join(self.said)
        self.assertIn("Version: 1. Gathered: x. Max id: K9.", out)
        self.assertIn("  tool:python 3.14: 1", out)
        self.assertIn("K1 tool:claude-agent-sdk 0.2.159: pass", out)
        self.assertIn(f"K3 workspace:{self.slot}: pass", out)
        self.assertIn(f"{self.slot}: 3 apply (0 broken, not counted), 3 pass on origin/main {self.sha[:12]}, 3 carried",
                      out)
        self.assertNotIn("needs >= 50%", out)
        self.assertEqual(self.health()["entries"], {"K1": "", "K2": "", "K3": ""})

    def test_show_exits_1_and_writes_health_when_an_entry_is_broken_on_origin_main(self):
        self.store(("tool:claude-agent-sdk 0.2.158", ""), ("tool:gh 2.0", ""), ("tool:python", ""),
                   (f"workspace:{self.slot}", "coscc/knowledge/gather.py::gather"))
        self.assertEqual(self.run_cli("show"), 1)
        self.assertIn(f"K1 tool:claude-agent-sdk 0.2.158: fail: version 0.2.158, origin/main has 0.2.159 in {self.slot}",
                      self.said)
        self.assertIn(f"K2 tool:gh 2.0: fail: no pin in {self.slot}", self.said)
        self.assertIn("K3 tool:python: fail: cannot be checked", self.said)
        self.assertIn(f"K4 workspace:{self.slot}: fail: ref missing: coscc/knowledge/gather.py::gather", self.said)
        health = self.health()
        self.assertEqual(health["sha"], {self.slot: self.sha})
        self.assertEqual(health["entries"]["K4"], "ref missing: coscc/knowledge/gather.py::gather")
        self.assertRegex(health["at"], r"^\d{4}-\d{2}-\d{2}T")

    def test_show_exits_1_and_writes_no_health_when_the_fetch_fails(self):
        self.store(("tool:python 3.14", ""))
        with failing_fetch():
            self.assertEqual(self.run_cli("show"), 1)
        self.assertIn("could not read from remote repository", self.said[-1])
        self.assertFalse((knowledge.path_of(str(self.data)) / knowledge.HEALTH).exists())

    def test_a_broken_entry_is_never_counted_as_apply(self):
        ws = (f"workspace:{self.slot}", "coscc/knowledge/gather.py")
        self.store(("tool:python 3.14", ""), ws, (f"workspace:{self.slot}", "coscc/gone.py"))
        self.assertEqual(self.run_cli("show"), 1)
        self.assertIn(f"{self.slot}: 2 apply (1 broken, not counted), 2 pass on origin/main {self.sha[:12]}, "
                      "2 carried", "\n".join(self.said))

    def test_no_git_is_2(self):
        self.store(("tool:python 3.14", ""))
        with mock.patch.object(admit, "git_runs", return_value=False):
            self.assertEqual(self.run_cli("show"), 2)

    def test_no_run_log_is_2(self):
        self.store(("tool:python 3.14", ""))
        (self.data / "cos.db").unlink()
        self.assertEqual(self.run_cli("show"), 2)
        self.assertIn("the run log cannot be read", self.said[-1])

    def test_it_writes_nothing_but_the_health_file(self):
        path = self.store(("tool:python 3.14", ""), (f"workspace:{self.slot}", "coscc/knowledge/gather.py::gone"))
        db = self.data / "cos.db"
        before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in (path, db)]
        rows = Journal(self.root, self.data).records()
        self.assertEqual(self.run_cli("show"), 1)
        self.assertEqual([(p.read_bytes(), p.stat().st_mtime_ns) for p in (path, db)], before)
        self.assertEqual(Journal(self.root, self.data).records(), rows)
        self.assertEqual(sorted(os.listdir(path.parent)), [knowledge.HEALTH, knowledge.STORE])

    def test_check_and_baseline_are_no_longer_commands(self):
        for argv in (["check"], ["baseline"]):
            with self.subTest(argv=argv):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(self.run_cli(*argv), 2)
                self.assertIn(knowledge_cli.USAGE, err.getvalue())
        self.assertNotIn("check", knowledge_cli.USAGE)
        self.assertNotIn("baseline", knowledge_cli.USAGE)


def imported(path: Path) -> set[str]:
    """Every module an import in `path` names, dotted in full. `from a import b` gives `a`
    and `a.b`, since `b` may be a module: since `0129` the four live in one package, and
    `from coscc.knowledge import gather` names only the package on its `from` side."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names |= {node.module} | {f"{node.module}.{a.name}" for a in node.names}
    return names



class OnlyATerminalReachesIt(unittest.TestCase):
    """R9: no route and no autopilot pass imports what gathers or measures, and of the service
    only `coscc/service/knowledge.py` does, since `0131` R1 made a gather follow a ship. Read
    from the source, because a route added later is exactly what this is for."""

    FORBIDDEN = {"coscc.knowledge.gather", "coscc.knowledge.cli", "coscc.knowledge.measure", "coscc.knowledge.admit"}
    ALLOWED = "coscc/service/knowledge.py"

    def forbidden(self, path: Path) -> set[str]:
        return {f for f in self.FORBIDDEN for n in imported(path) if n == f or n.startswith(f + ".")}

    def test_only_the_knowledge_service_imports_what_gathers(self):
        # `0095`: `Service` is spread over `service.py` and the `service_*.py` it was split into.
        split = [f"coscc/service/{p.name}" for p in sorted((REPO / "coscc" / "service").glob("*.py"))
                 if not p.name.endswith("_test.py")]
        self.assertIn(self.ALLOWED, split)
        for name in ("coscc/web/api.py", "coscc/units/autopilot.py", *split):
            with self.subTest(module=name):
                if name == self.ALLOWED:
                    self.assertTrue(self.forbidden(REPO / name))
                    self.assertNotIn("coscc.knowledge.cli", self.forbidden(REPO / name))
                else:
                    self.assertFalse(self.forbidden(REPO / name))
        # And one caller of it: the end of a board step, nowhere else.
        callers = [str(p.relative_to(REPO)) for p in sorted((REPO / "coscc").rglob("*.py"))
                   if not p.name.endswith("_test.py") and "self._gather_soon(" in p.read_text(encoding="utf-8")]
        self.assertEqual(callers, ["coscc/service/steps.py"])
        self.assertEqual((REPO / "coscc/service/steps.py").read_text(encoding="utf-8").count("self._gather_soon("), 1)

    def test_the_check_would_see_one(self):
        with tempfile.TemporaryDirectory() as d:
            probe = Path(d) / "probe.py"
            probe.write_text("from coscc.knowledge import gather\nimport coscc.knowledge.measure\n"
                             "from coscc.knowledge.cli import main\nfrom coscc.knowledge import admit as a\n")
            self.assertEqual(self.forbidden(probe), self.FORBIDDEN)


if __name__ == "__main__":
    unittest.main()
