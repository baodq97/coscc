"""`0090` plan step 8: `coscc knowledge ...`, and that nothing but a terminal reaches it (R9)."""

from __future__ import annotations

import ast
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc import gather, knowledge, knowledge_cli

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
                     ["gather", "--all", "--all"]):
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


class BaselineAndMeasure(Fixture):
    def test_measure_with_no_baseline_is_refused(self):
        self.assertEqual(self.run_cli("measure"), 2)
        self.assertIn("no baseline", self.said[-1])

    def test_a_baseline_with_no_database_fails(self):
        self.assertEqual(self.run_cli("baseline"), 1)


class OnlyATerminalReachesIt(unittest.TestCase):
    """R9: no route, no autopilot pass and no service call imports what gathers or measures.
    Read from the source, because a route added later is exactly what this is for."""

    FORBIDDEN = {"gather", "knowledge_cli", "measure"}

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
        for name in ("api.py", "autopilot.py", "service.py"):
            with self.subTest(module=name):
                self.assertFalse(self.imported(REPO / "coscc" / name) & self.FORBIDDEN)

    def test_the_check_would_see_one(self):
        with tempfile.TemporaryDirectory() as d:
            probe = Path(d) / "probe.py"
            probe.write_text("from coscc import gather\nimport coscc.measure\nfrom coscc.knowledge_cli import main\n")
            self.assertEqual(self.imported(probe) & self.FORBIDDEN, self.FORBIDDEN)


if __name__ == "__main__":
    unittest.main()
