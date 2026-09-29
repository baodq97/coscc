"""Comments and docstrings say why the code is so, never which unit or requirement made it.

An id (`0088`, `R3`, `spec.md C7`) or a history note in code is read again on every turn of
every session that opens the file, and new code copies the comments around it. Code files
carry none; test files carry no more than `TESTS_MAX` until they are cleaned too.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# A unit number (`0088`, `0088_slug`), a spec/plan/spike/review id (R3, C7, U2, F4), with or
# without the artifact in front, or a review round. `0700`-style file modes do not match.
ID = re.compile(
    r"`0[0-3]\d\d\b|\b0[0-3]\d\d_[a-z]|\b0[0-3]\d\d\s+[RCUF]\d"
    r"|(?<![-\w])[RCUF]\d{1,2}\b|\breview round \d"
)

# Ids left in test files. Lower it whenever tests are cleaned; never raise it.
TESTS_MAX = 2_236


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


def ids(path: Path) -> list[str]:
    return [
        f"{path.relative_to(REPO)}:{line}: {m.group(0)}"
        for line, text in _texts(path.read_text(encoding="utf-8"))
        for m in ID.finditer(text)
    ]


def _files(tests: bool) -> list[Path]:
    return [
        p
        for p in sorted((REPO / "coscc").rglob("*.py"))
        if p.name.endswith("_test.py") == tests and not {"_web", "_harness"} & set(p.parts)
    ]


class CommentsCarryNoIds(unittest.TestCase):
    def test_the_pattern(self):
        for text in ("`0088` R3", "see 0088_some-slug", "spec.md C7", "(R12)", "review round 2"):
            self.assertTrue(ID.search(text), text)
        for text in ("mode `0700`", "`git diff -U0`", "HTTP 404", "F<k>", "a round"):
            self.assertFalse(ID.search(text), text)

    def test_code_files_carry_none(self):
        found = [hit for path in _files(tests=False) for hit in ids(path)]
        self.assertFalse(found, "say why in the present tense, without the id:\n" + "\n".join(found))

    def test_test_files_carry_no_more_than_before(self):
        count = sum(len(ids(path)) for path in _files(tests=True))
        self.assertLessEqual(count, TESTS_MAX, "a test added an id to a comment or docstring")


if __name__ == "__main__":
    unittest.main()
