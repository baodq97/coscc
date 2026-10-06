"""`coscc/units/planmap.py`."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.units import planmap

PY = '''"""A module."""

import os


def top(a):
    def inner():
        pass
    return inner


class Box:
    """A box."""

    def __init__(self):
        pass

    async def fill(self):
        def nested():
            pass


async def later():
    pass
'''

JS = """import x from "y";

export function run(a) {
  function inside() {}
}
async function wait() {}
const LIMIT = 3;
export class Board {}
let other = 1;
"""


def plan(*paths: str) -> list[str]:
    """The `files` of a plan's record."""
    return list(paths)


class TheDefinitions(unittest.TestCase):
    def test_python_top_level_and_class_level_with_their_lines(self):
        self.assertEqual(
            planmap.definitions("m.py", PY),
            [
                (6, "def top"),
                (12, "class Box"),
                (15, "def Box.__init__"),
                (18, "async def Box.fill"),
                (23, "async def later"),
            ],
        )

    def test_a_function_inside_a_function_is_not_one(self):
        names = [d for _, d in planmap.definitions("m.py", PY)]
        self.assertNotIn("def inner", names)
        self.assertFalse(any("nested" in n for n in names))


class TheSection(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.tree = self.root / "tree"
        (self.tree / "pkg").mkdir(parents=True)
        (self.tree / "pkg" / "m.py").write_text(PY, encoding="utf-8")
        (self.tree / "pkg" / "b.mjs").write_text(JS, encoding="utf-8")
        (self.tree / "README.md").write_text("# x\n\ny\n", encoding="utf-8")

    def test_each_file_in_the_order_the_record_names_it(self):
        section, record = planmap.select(plan("README.md", "pkg/m.py", "pkg/b.mjs"), self.tree)
        self.assertEqual(section.splitlines()[0], "- `README.md` — 3 lines")
        self.assertIn("- `pkg/m.py` — 24 lines\n  - 6 def top\n", section)
        self.assertLess(section.index("pkg/m.py"), section.index("pkg/b.mjs"))
        self.assertNotIn("not/listed.py", section)
        self.assertEqual(
            record,
            {
                "bytes": len(section.encode()),
                "files": 3,
                "full": 3,
                "short": 0,
                "new": 0,
                "outside": 0,
            },
        )

    def test_a_file_not_yet_there_is_new_and_a_name_that_is_no_path_is_skipped(self):
        section, record = planmap.select(plan("pkg/later.py", "Runner.run"), self.tree)
        self.assertEqual(section, "- `pkg/later.py` — new")
        self.assertEqual((record["files"], record["new"]), (1, 1))

    def test_nothing_outside_the_tree_is_read(self):
        (self.root / "secret.py").write_text("def hidden():\n    pass\n", encoding="utf-8")
        os.symlink(self.root / "secret.py", self.tree / "pkg" / "link.py")
        opened: list[str] = []
        real = Path.read_bytes

        def spy(p):
            opened.append(str(p))
            return real(p)

        with mock.patch.object(Path, "read_bytes", spy):
            section, record = planmap.select(
                plan("../secret.py", str(self.root / "secret.py"), "pkg/link.py", "/etc/passwd"),
                self.tree,
            )
        self.assertEqual(section, "")
        self.assertEqual(record["outside"], 4)
        self.assertEqual(opened, [])

    def test_over_the_cap_the_files_after_get_their_short_line_only(self):
        many = "".join(f"def f{i:05d}():\n    pass\n" for i in range(2000))
        (self.tree / "pkg" / "big.py").write_text(many, encoding="utf-8")
        section, record = planmap.select(plan("pkg/m.py", "pkg/big.py", "pkg/b.mjs"), self.tree)
        self.assertLessEqual(record["bytes"], planmap.CAP_BYTES)
        self.assertIn("  - 6 def top", section)
        self.assertIn("- `pkg/big.py` — 4000 lines", section)
        self.assertNotIn("def f00000", section)
        self.assertIn("- `pkg/b.mjs` — 9 lines", section)
        self.assertNotIn("function run", section)
        self.assertEqual((record["full"], record["short"]), (1, 2))

    def test_a_plan_naming_no_files_is_nothing(self):
        self.assertEqual(planmap.select([], self.tree), ("", planmap._empty()))

    def test_a_path_named_twice_is_listed_once(self):
        section, record = planmap.select(plan("README.md", "pkg/m.py", "README.md"), self.tree)
        self.assertEqual(section.count("README.md"), 1)
        self.assertEqual(record["files"], 2)

    def test_thirty_paths_all_get_their_line(self):
        names = [f"pkg/n{i:02d}.py" for i in range(30)]
        section, record = planmap.select(names, self.tree)
        self.assertEqual((record["files"], record["new"]), (30, 30))
        self.assertTrue(all(f"`{n}`" in section for n in names))

    def test_a_failure_is_an_error_not_a_raise(self):
        with mock.patch.object(planmap, "select", side_effect=OSError("boom")):
            kw = planmap.for_step(["README.md"], self.tree)
        self.assertEqual(kw["plan_map"], "")
        self.assertEqual(kw["plan_map_record"]["bytes"], 0)
        self.assertIn("OSError", kw["plan_map_record"]["error"])

    def test_for_step_is_select_on_the_records_files(self):
        section, record = planmap.select(plan("README.md"), self.tree)
        self.assertEqual(
            planmap.for_step(plan("README.md"), self.tree),
            {"plan_map": section, "plan_map_record": record},
        )


if __name__ == "__main__":
    unittest.main()
