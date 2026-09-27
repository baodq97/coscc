"""`0118` R5, R6: a `path:line` citation under `.claude/` points at what it names.

A citation is a backtick span that is nothing but `path:N` or `path:N-M`. What it names is
the last identifier of the nearest span before it, in the same paragraph, list item or table
row, that is not itself a citation. It is green only when one of the lines it cites defines
that name. Four citations drifted when `0095` split the files and nothing noticed; a red
here is a citation to move, and the message says where it is and what was read.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from typing import NamedTuple

REPO = Path(__file__).resolve().parents[1]

SPAN = re.compile(r"`([^`]+)`")
CITATION = re.compile(r"(?P<path>[\w.-]+(?:/[\w.-]+)*\.[A-Za-z]\w*):(?P<start>\d+)(?:-(?P<end>\d+))?")
IDENT = re.compile(r"[A-Za-z_]\w*")
FENCE = re.compile(r"\s*```")
OPENS = re.compile(r"\s*(?:[-*+] |\d+\. |\|)")
ACCEPTED = "`NAME` (`path:N`) or `NAME`, `path:N`"


class Problem(NamedTuple):
    source: str
    line: int
    citation: str
    name: str
    reason: str


def show(p: Problem) -> str:
    return f"{p.source}:{p.line}: `{p.citation}` — {p.name or 'names nothing'}: {p.reason}. Accepted: {ACCEPTED}"


def _blocks(text: str) -> list[tuple[int, str]]:
    """Each paragraph, list item and table row, with the number of its first line. Headings,
    blank lines and fenced code end one and are not read."""
    blocks: list[tuple[int, list[str]]] = []
    current: list[str] | None = None
    fenced = False
    for number, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            fenced, current = not fenced, None
        elif fenced or not line.strip() or line.lstrip().startswith("#"):
            current = None
        elif current is None or OPENS.match(line):
            current = [line]
            blocks.append((number, current))
        else:
            current.append(line)
    return [(start, "\n".join(lines)) for start, lines in blocks]


def _defines(line: str, name: str) -> bool:
    n = re.escape(name)
    return bool(
        re.match(rf"\s*(?:export\s+)?(?:async\s+)?(?:def|class|function|const|let|var)\s+{n}\b", line)
        or re.match(rf"\s*[\"']?{n}[\"']?\s*(?::|=(?!=))", line)
    )


def check(text: str, source: str, root: Path) -> list[Problem]:
    problems = []
    for start, block in _blocks(text):
        name = ""
        for span in SPAN.finditer(block):
            cited = CITATION.fullmatch(span.group(1))
            if not cited:
                idents = IDENT.findall(span.group(1))
                name = idents[-1] if idents else ""
                continue
            line = start + block.count("\n", 0, span.start())
            first = int(cited["start"])
            last = int(cited["end"] or first)
            path = root / cited["path"]
            if not path.is_file():
                reason = "file does not exist"
            else:
                lines = path.read_text(encoding="utf-8").splitlines()
                if not 1 <= first <= last <= len(lines):
                    reason = f"lines {first}-{last} are past the end ({len(lines)} lines)"
                elif not name:
                    reason = "names nothing"
                elif not any(_defines(lines[i - 1], name) for i in range(first, last + 1)):
                    reason = f"{name} is not defined on lines {first}-{last}"
                else:
                    continue
            problems.append(Problem(source, line, span.group(1), name, reason))
    return problems


def scan(root: Path) -> list[Problem]:
    scripts = root / ".claude" / "scripts"
    problems = []
    for path in sorted((root / ".claude").rglob("*.md")):
        if scripts in path.parents:
            continue
        source = path.relative_to(root).as_posix()
        problems += check(path.read_text(encoding="utf-8"), source, root)
    return problems


class TheCheckerSeesEachWayACitationBreaks(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "mod.py").write_text("import os\n\nLIMIT = 5\n\nprint(LIMIT)\n", encoding="utf-8")

    def _red(self, text: str, reason: str) -> None:
        problems = check(text, "rule.md", self.root)
        self.assertEqual([p.reason for p in problems], [reason])
        self.assertIn(ACCEPTED, show(problems[0]))

    def test_a_citation_on_the_line_that_defines_its_name_is_green(self):
        self.assertEqual(check("Waits `LIMIT` (`pkg/mod.py:3`).\n", "rule.md", self.root), [])
        self.assertEqual(check("Waits `4 * LIMIT`, `pkg/mod.py:2-3`.\n", "rule.md", self.root), [])

    def test_a_citation_whose_line_moved_is_red(self):
        self._red("Waits `LIMIT` (`pkg/mod.py:1`).\n", "LIMIT is not defined on lines 1-1")

    def test_a_citation_that_names_nothing_is_red(self):
        self._red("Since `0014` it waits (`pkg/mod.py:3`).\n", "names nothing")
        # The name does not come from an earlier paragraph.
        self._red("Waits `LIMIT`.\n\nIt waits (`pkg/mod.py:3`).\n", "names nothing")

    def test_a_citation_of_a_missing_file_is_red(self):
        self._red("Waits `LIMIT` (`pkg/gone.py:3`).\n", "file does not exist")

    def test_a_citation_past_the_end_of_the_file_is_red(self):
        self._red("Waits `LIMIT` (`pkg/mod.py:4-9`).\n", "lines 4-9 are past the end (5 lines)")

    def test_a_line_that_only_mentions_the_name_is_red(self):
        self._red("Waits `LIMIT` (`pkg/mod.py:5`).\n", "LIMIT is not defined on lines 5-5")

    def test_a_path_inside_a_longer_span_is_not_a_citation(self):
        text = "A finding reads `- F3 [open] pkg/mod.py:1 — the wait is wrong`, and `127.0.0.1:8790` is an address.\n"
        self.assertEqual(check(text, "rule.md", self.root), [])

    def test_the_line_named_is_the_citation_s_own(self):
        problems = check("# Title\n\n```\n`pkg/mod.py:1`\n```\n- one\n  and `LIMIT`, `pkg/mod.py:1`\n", "rule.md", self.root)
        self.assertEqual([(p.line, p.name) for p in problems], [(7, "LIMIT")])


if __name__ == "__main__":
    unittest.main()
