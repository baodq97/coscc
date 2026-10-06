"""Comments, docstrings, names, strings and the markdown say why something is so, never which unit
or requirement made it.

An id or a history note is read again on every turn of every session that opens the file, and
new code copies the text around it. No file under `coscc/` or `tests/` carries one, in a comment,
a docstring, a name or any string constant (the SQL schema comments are strings), and neither
does a markdown file under `.claude/`, `coscc/features/` or `coscc/packs/`. Every string constant is read, not
only SQL comment lines: outside the schema it raised no false match. Strings under `tests/` are
fixture data (a unit directory name), so only `coscc/` strings are read."""

from __future__ import annotations

import ast
import io
import re
import tempfile
import tokenize
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# A unit number, a spec/plan/spike/review id, with or without the artifact in front, or a review
# round. `0700`-style file modes do not match.
ID = re.compile(
    r"`0[0-3]\d\d\b|\b0[0-3]\d\d_[a-z]|\b0[0-3]\d\d\s+[RCUF]\d"
    r"|(?<![-\w])[RCUF]\d{1,2}\b|\breview round \d"
)

# The same ids in a function or class name: `test_r4_…`, `test_0096_…`, `TheCaseOf0096`.
NAME = re.compile(
    r"(?:^|_)(?:0[0-3]\d\d|[rcuf][1-9]\d?[a-z]?)(?=_|$)|0[0-3]\d\d|(?<=[a-z])[RCUF][1-9]\d?(?=[A-Z]|$)"
)

FIX = "say why, not which unit: drop the id, or the sentence if it only names the unit"

# Fixture data for the parsers keeps ids on purpose.
# Ids that are a format, not a unit: the finding id `F<k>` and the spike id `U<n>` that the
# skills and the UI rule define.
SKIPPED = ("worktrees", "testdata")
ALLOWED = {
    ".claude/rules/ui-standard.md": re.compile(r"F\d"),
    "coscc/packs/coscc-sdlc/skills/write-review/SKILL.md": re.compile(r"F\d"),
    "coscc/packs/coscc-sdlc/skills/write-spike/SKILL.md": re.compile(r"U\d"),
    "coscc/packs/coscc-sdlc/skills/write-spec/SKILL.md": re.compile(r"U\d"),
}


def _texts(source: str):
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            yield token.start[0], token.string
    for node in ast.walk(ast.parse(source)):
        body = getattr(node, "body", None)
        if (
            isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            yield body[0].lineno, body[0].value.value


def _strings(source: str):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for offset, line in enumerate(node.value.splitlines()):
                yield node.lineno + offset, line


def _names(source: str):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.lineno, node.name


def ids(path: Path, where: str | None = None) -> list[str]:
    source = path.read_text(encoding="utf-8")
    where = where or str(path.relative_to(REPO))
    found = {
        (line, m.group(0))
        for line, text in [
            *_texts(source),
            *(_strings(source) if where.startswith("coscc/") else []),
        ]
        for m in ID.finditer(text)
    }
    return [f"{where}:{line}: {hit}" for line, hit in sorted(found)] + [
        f"{where}:{line}: {name}" for line, name in _names(source) if NAME.search(name)
    ]


def md_ids(path: Path, where: str | None = None) -> list[str]:
    where = where or str(path.relative_to(REPO))
    allowed = ALLOWED.get(where)
    return [
        f"{where}:{n}: {m.group(0)}"
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for m in ID.finditer(line)
        if not (allowed and allowed.search(m.group(0)))
    ]


def _markdown() -> list[Path]:
    files = [
        *(REPO / ".claude").rglob("*.md"),
        *(REPO / "coscc" / "features").glob("*/README.md"),
        *(REPO / "coscc" / "packs").rglob("*.md"),
    ]
    return sorted(p for p in files if not set(SKIPPED) & set(p.relative_to(REPO).parts))


def _files() -> list[Path]:
    return [
        p
        for top in ("coscc", "tests")
        for p in sorted((REPO / top).rglob("*.py"))
        if "_web" not in p.parts
    ]


class NoFileCarriesAnId(unittest.TestCase):
    def test_every_file_carries_none(self):
        found = [hit for path in _files() for hit in ids(path)]
        self.assertFalse(found, FIX + ":\n" + "\n".join(found))

    def test_no_markdown_carries_one(self):
        found = [hit for path in _markdown() for hit in md_ids(path)]
        self.assertFalse(found, FIX + ":\n" + "\n".join(found))

    def test_a_planted_string_fails_with_the_fix(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "planted.py"
            path.write_text('SQL = """-- `0013` R1: the record\nCREATE TABLE t (a)"""\n')
            found = ids(path, "coscc/planted.py")
        self.assertCountEqual(["coscc/planted.py:1: `0013", "coscc/planted.py:1: R1"], found)
        self.assertIn("which unit", FIX)

    def test_the_named_exceptions_hold_only_their_own_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.md"
            path.write_text("the finding F2, but also `0068`\n")
            found = md_ids(path, ".claude/rules/ui-standard.md")
        self.assertEqual([".claude/rules/ui-standard.md:1: `0068"], found)


if __name__ == "__main__":
    unittest.main()
