"""Every `test(` call of the loop's former JavaScript suite has a Python test, or a reason.

`ported.txt` holds one line per call, in the order of that suite at `4858236`:
`<name> → tests/loop/<file>.py::<function>` or `<name> → dropped: <reason>`. A target's
`[param]` is stripped; its file must exist and define the function.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PORTED = Path(__file__).resolve().parent / "ported.txt"
CALLS = 371
LINE = re.compile(
    r"^(?P<name>.+?) → (?:(?P<file>tests/loop/\w+\.py)::(?P<fn>\w+)(?:\[.*\])?|dropped: .+)$"
)


def _rows() -> list[str]:
    return PORTED.read_text().splitlines()


def test_one_line_per_test_call():
    assert len(_rows()) == CALLS


def test_every_line_names_a_target_or_a_reason():
    bad = [row for row in _rows() if not LINE.match(row)]
    assert bad == [], f"{len(bad)} lines neither ported nor dropped, the first: {bad[:3]}"


def test_every_target_exists():
    defined: dict[str, set[str]] = {}
    missing = []
    for row in _rows():
        m = LINE.match(row)
        if not m or not m["file"]:
            continue
        if m["file"] not in defined:
            path = REPO / m["file"]
            tree = ast.parse(path.read_text()) if path.exists() else ast.Module(body=[])
            defined[m["file"]] = {
                n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
        if m["fn"] not in defined[m["file"]]:
            missing.append(f"{m['file']}::{m['fn']}")
    assert missing == []
