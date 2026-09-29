"""Comments, docstrings and names say why the code is so, never which unit or requirement made it.

An id or a history note is read again on every turn of every session that opens the file, and
new code copies the comments around it. No file under `coscc/` or `tests/` carries one."""

from __future__ import annotations

import ast
import io
import re
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


def _names(source: str):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.lineno, node.name


def ids(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    where = path.relative_to(REPO)
    return [
        f"{where}:{line}: {m.group(0)}" for line, text in _texts(source) for m in ID.finditer(text)
    ] + [f"{where}:{line}: {name}" for line, name in _names(source) if NAME.search(name)]


def _files() -> list[Path]:
    return [
        p
        for top in ("coscc", "tests")
        for p in sorted((REPO / top).rglob("*.py"))
        if not {"_web", "_harness"} & set(p.parts)
    ]


class NoFileCarriesAnId(unittest.TestCase):
    def test_the_pattern(self):
        for text in ("`0088` R3", "see 0088_some-slug", "spec.md C7", "(R12)", "review round 2"):
            self.assertTrue(ID.search(text), text)
        for text in ("mode `0700`", "`git diff -U0`", "HTTP 404", "F<k>", "a round"):
            self.assertFalse(ID.search(text), text)
        for name in ("test_r4_the_gate", "test_0096_rebase", "TheCaseOf0096", "TheFiveStepsOfR6"):
            self.assertTrue(NAME.search(name), name)
        for name in ("test_utf8_is_read", "test_sha256", "Round", "test_http_404", "diff_u0"):
            self.assertFalse(NAME.search(name), name)

    def test_every_file_carries_none(self):
        found = [hit for path in _files() for hit in ids(path)]
        self.assertFalse(
            found, "say why in the present tense, without the id:\n" + "\n".join(found)
        )


if __name__ == "__main__":
    unittest.main()
