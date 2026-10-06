"""The loop decides from the app's snapshot, never from what an artifact's header says.

(a) Every test held to a golden runs again with `CONTRADICT` set, where each header says a status,
a type, a skip reason, a spike citation and an open question no row holds: it stays green.
(b) The functions of `coscc/loop/` that read text (call `read_text`, or take `text` or `*_text`)
are exactly `STILL_READ`, each a repository file, `not an artifact`: a new read, or a listed one
that is gone, fails.
"""

from __future__ import annotations

import ast
import subprocess
import sys

from tests.loop.conftest import CONTRADICT, GOLDEN, REPO, env

REPO_FILE = "not an artifact"

STILL_READ = {
    "__init__.py:split_lines": REPO_FILE,
    "branch.py:toml_string": REPO_FILE,
    "branch.py:toml_table": REPO_FILE,
    "branch.py:locked_version": REPO_FILE,
    "branch.py:json_at": REPO_FILE,
    "branch.py:slurp": REPO_FILE,
    "model.py:against_standard": REPO_FILE,
    "probe.py:parse_standard": REPO_FILE,
    "probe.py:_number": REPO_FILE,
    "probe.py:parse_json": REPO_FILE,
    "probe.py:ui": REPO_FILE,
    "probe.py:manifest": REPO_FILE,
    "probe.py:workflows": REPO_FILE,
    "repo_rules.py:_lines": REPO_FILE,
    "repo_rules.py:_count": REPO_FILE,
    "repo_rules.py:normalize_patch": REPO_FILE,
    "snapshot.py:_json_message": REPO_FILE,
}


def _reads_text(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    params = [a.arg for a in (*fn.args.args, *fn.args.kwonlyargs)]
    if any(p == "text" or p.endswith("_text") for p in params):
        return True
    for call in ast.walk(fn):
        if isinstance(call, ast.Call):
            f = call.func
            name = (
                f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            )
            if name == "read_text":
                return True
    return False


def reading() -> set[str]:
    found = set()
    for path in sorted((REPO / "coscc" / "loop").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _reads_text(node):
                found.add(f"{path.name}:{node.name}")
    return found


def test_the_functions_that_read_text_are_the_listed_ones():
    assert reading() == set(STILL_READ)
    assert set(STILL_READ.values()) == {REPO_FILE}


def test_the_golden_suite_decides_the_same_when_every_header_contradicts_the_rows(tmp_path):
    files = [str(REPO / "tests" / "loop" / f"{g.stem}.py") for g in sorted(GOLDEN.glob("*.json"))]
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", "4", *files],
        capture_output=True,
        text=True,
        cwd=REPO,
        env=env(**{CONTRADICT: "1", "TMPDIR": str(tmp_path)}),
        timeout=600,
        check=False,
    )
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
