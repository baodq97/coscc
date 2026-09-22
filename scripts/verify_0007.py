#!/usr/bin/env python3
"""Proof for .cos/0007_stale-claims-and-dead-code.

Eleven claims, all static. No port, no browser, no session, no quota — which is the whole
point: this unit is about what the repository says about itself, and none of that needs a
server to read. The one slow claim is C9, which runs `npm test` inside itself to count the
lines it prints; budget one `npm test` plus a few seconds.

    0  every claim held
    1  at least one did not
    2  the environment could not answer — no `npm`, no `uv`, or `cos.mjs status --json`
       would not run

C1 is the reason this file never spells the wrong figure out. The claim is that no file in
the tree still states the `0004` measurement backwards; if the checker wrote that string as
a literal, the checker itself would be the last file failing it. So the needle is composed
from the two numbers at import time and the literal never exists on disk.

`scripts/verify_0003.py:8-14` explains why exit 2 is kept apart from exit 1. The same split
holds here: "npm is not installed" is not "the repository lies about itself".
"""

from __future__ import annotations

import ast
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cos_baodo.data import Data
from scripts.proof_harness import EXIT_BROKEN, EXIT_ENV, EXIT_PASS, say

REPO = Path(__file__).resolve().parent.parent

UNIT = ".cos/0007_stale-claims-and-dead-code"

# The `0004` measurement, from `.cos/0004_silent-concurrent-loss/plan.md:115`: four
# processes adding five workspaces each to one working folder left 8 of 20. Change it
# there, not here.
KEPT, TOTAL = 8, 20

# Directories that hold nothing this repository wrote.
SKIP_DIRS = {".git", ".venv", ".web", "node_modules", "__pycache__", ".playwright-cli"}

# Files that are generated or binary, so a substring in them is not a claim.
SKIP_NAMES = {"uv.lock", "package-lock.json"}

TEXT_SUFFIXES = {".py", ".mjs", ".js", ".md", ".toml", ".json", ".txt", ".cfg", ".yml", ".yaml"}

# Each row: the file that carries the citation, the path it cites, the line it cites, and a
# string that must be on that line for the citation to still hold up what it holds up.
CITATIONS = (
    ("rxconfig.py", "cos_baodo/config.py", 66, "127.0.0.1"),
    ("cos_baodo/journal_test.py", ".cos/0004_silent-concurrent-loss/plan.md", 115, "8 trên"),
    ("scripts/verify_0005.py", "scripts/proof_harness.py", 36, "EXIT_PASS"),
)


def read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def line_of(rel: str, number: int) -> str:
    lines = read(rel).splitlines()
    return lines[number - 1] if 0 < number <= len(lines) else ""


def tree_files() -> list[Path]:
    """Every text file this repository wrote, as paths relative to the root."""
    out = []
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name in SKIP_NAMES:
                continue
            p = Path(dirpath) / name
            if p.suffix in TEXT_SUFFIXES or name.startswith("."):
                out.append(p)
    return out


def files_containing(needle: str) -> list[str]:
    hits = []
    for p in tree_files():
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if needle in text:
            hits.append(str(p.relative_to(REPO)))
    return sorted(hits)


# --------------------------------------------------------------------------
# C1 — the 0004 measurement reads one way only
# --------------------------------------------------------------------------


def claim_1() -> bool:
    # The backwards reading is the one that takes the survivors for the losses.
    needle = f"mất {KEPT}/{TOTAL}"
    source = line_of(".cos/0004_silent-concurrent-loss/plan.md", 115)
    ok = say(
        f"{KEPT} trên" in source,
        "the original measurement is still where everything cites it",
        f".cos/0004_silent-concurrent-loss/plan.md:115 reads {source.strip()!r}",
    )
    backwards = files_containing(needle)
    return ok & say(
        not backwards,
        "no file states that measurement backwards",
        f"{needle!r} in {', '.join(backwards)}",
    )


# --------------------------------------------------------------------------
# C2 — three named citations still land on what they cite
# --------------------------------------------------------------------------


def claim_2() -> bool:
    ok = True
    for citer, cited, number, anchor in CITATIONS:
        reference = f"{cited}:{number}"
        if reference not in read(citer):
            ok &= say(False, f"{citer} cites {reference}", "the citation is not there")
            continue
        target = line_of(cited, number)
        ok &= say(
            anchor in target,
            f"{citer} cites {reference}",
            f"that line reads {target.strip()!r}, which does not carry {anchor!r}",
        )
    return ok


# --------------------------------------------------------------------------
# C3, C4 — the documentation describes this repository
# --------------------------------------------------------------------------


def stage_files() -> list[str]:
    out = subprocess.run(
        ["node", ".claude/scripts/cos.mjs", "status", "--json"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    return [s["file"] for s in json.loads(out.stdout)["stages"]]


def claim_3(stages: list[str]) -> bool:
    readme = read("README.md")
    missing = [f for f in stages if f not in readme]
    return say(
        not missing,
        f"README.md describes the loop as {len(stages)} stages",
        f"it never names {', '.join(missing)}",
    )


def claim_4() -> bool:
    return say(
        "eight units" not in read(".claude/scripts/cos.test.mjs"),
        "no test name carries a count of the units on disk",
    )


# --------------------------------------------------------------------------
# C5 — no width constant nobody reads
# --------------------------------------------------------------------------


def claim_5() -> bool:
    hits = [f for f in files_containing("BREAKPOINTS") if f.startswith("cos_baodo/")]
    return say(not hits, "no unread width constant in cos_baodo/", ", ".join(hits))


# --------------------------------------------------------------------------
# C6 — no module is imported only by its own test
# --------------------------------------------------------------------------


def imports_of(path: Path) -> set[str]:
    """Every `cos_baodo` submodule this file imports, in any of the three spellings."""
    found: set[str] = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return found
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in ("cos_baodo", "") and node.level <= 1:
                # `from cos_baodo import studio` and `from . import studio` — the submodule
                # is a name, not the module path. cos_baodo/screens.py:27 is this shape, and
                # a checker that only reads node.module calls three live modules dead.
                found.update(a.name for a in node.names)
            if module.startswith("cos_baodo."):
                found.add(module.split(".")[1])
            elif node.level == 1 and module:
                found.add(module.split(".")[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "cos_baodo" and len(parts) > 1:
                    found.add(parts[1])
    return found


def claim_6() -> bool:
    package = REPO / "cos_baodo"
    modules = [
        p.stem
        for p in sorted(package.glob("*.py"))
        if p.stem != "__init__" and not p.stem.endswith("_test")
    ]
    readers = [
        p
        for p in list(package.glob("*.py")) + list((REPO / "scripts").glob("*.py"))
        if p.name != "__init__.py"
    ] + [REPO / "rxconfig.py", REPO / "pyproject.toml"]

    orphans = []
    for module in modules:
        others = []
        for p in readers:
            if p.name in (f"{module}.py", f"{module}_test.py"):
                continue
            if module in imports_of(p):
                others.append(p)
                continue
            # An entry point is reached by name, not by import: `cos-baodo =
            # "cos_baodo.run:main"` in pyproject.toml, and `"cos_baodo.cos_baodo:app"` at
            # cos_baodo/run.py:61. A checker that only reads imports calls both dead, and
            # deleting either stops the app rather than removing dead code.
            try:
                if f"cos_baodo.{module}" in p.read_text(encoding="utf-8"):
                    others.append(p)
            except (UnicodeDecodeError, OSError):
                pass
        if not others:
            orphans.append(module)
    return say(
        not orphans,
        "no module is imported only by its own test",
        ", ".join(f"cos_baodo/{m}.py" for m in orphans),
    )


# --------------------------------------------------------------------------
# C7 — the data root has no object store
# --------------------------------------------------------------------------


def claim_7() -> bool:
    with tempfile.TemporaryDirectory() as d:
        data = Data(d)
        ok = say(
            not hasattr(data, "objects_dir"),
            "Data carries no objects_dir",
            "the attribute is still there",
        )
        data.ensure_dir()
        left = sorted(os.listdir(d))
        # This half already held before the layer was removed: `ensure_dir` only ever made
        # the root, and the one `mkdir` of an objects directory lived in `Objects.put`,
        # which nothing called. Kept because it is the claim a reader cares about.
        return ok & say("objects" not in left, "the data root grows no objects/", ", ".join(left))


# --------------------------------------------------------------------------
# C8 — no orphaned npm dependency
# --------------------------------------------------------------------------


def claim_8() -> bool:
    declared = json.loads(read("package.json")).get("dependencies", {})
    scripts = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted((REPO / ".claude" / "scripts").glob("*.mjs"))
    )
    orphans = [name for name in declared if name not in scripts]
    return say(not orphans, "every declared npm dependency is imported", ", ".join(orphans))


# --------------------------------------------------------------------------
# C9 — npm test is green and silent
# --------------------------------------------------------------------------


def claim_9() -> bool:
    out = subprocess.run(
        ["npm", "test"], cwd=REPO, capture_output=True, text=True, stdin=subprocess.DEVNULL
    )
    noise = [ln for ln in (out.stdout + out.stderr).splitlines() if "Warning" in ln]
    ok = say(out.returncode == 0, "npm test is green", f"it exited {out.returncode}")
    return ok & say(not noise, "npm test prints no warning", f"{len(noise)} lines, first: {noise[0].strip() if noise else ''}")


# --------------------------------------------------------------------------
# C10 — .gitignore says each thing once
# --------------------------------------------------------------------------


def claim_10() -> bool:
    entries = [
        ln.strip() for ln in read(".gitignore").splitlines() if ln.strip() and not ln.startswith("#")
    ]
    redundant = []
    for i, a in enumerate(entries):
        for j, b in enumerate(entries):
            if i == j:
                continue
            # Same thing spelled twice (`.web` and `.web/`), or one entry already covered by
            # another's glob (`*.pyc` inside `*.py[cod]`). Not `.env` and `.env.*` — that
            # pattern does not match the bare name, so both are load bearing.
            if a.rstrip("/") == b.rstrip("/") and i > j:
                redundant.append(a)
            elif a != b and fnmatch.fnmatch(a, b):
                redundant.append(a)
    return say(not redundant, ".gitignore names each thing once", ", ".join(sorted(set(redundant))))


# --------------------------------------------------------------------------
# C11 — the versions are the current ones
# --------------------------------------------------------------------------


def claim_11() -> bool:
    pinned = read(".python-version").strip()
    ok = say(pinned == "3.14", "the interpreter is pinned to 3.14", f"it says {pinned!r}")

    out = subprocess.run(
        ["uv", "pip", "list", "--outdated"], cwd=REPO, capture_output=True, text=True
    )
    rows = [ln.split()[0] for ln in out.stdout.splitlines()[2:] if ln.strip()]
    if not rows:
        return ok & say(True, "nothing is out of date")

    # `spec.md` R13 allows what is held back, as long as impl.md names it and the constraint
    # holding it. A floor this repository does not own is not a claim this repository made.
    impl = REPO / UNIT / "impl.md"
    if not impl.exists():
        return ok & say(False, "every held-back package is accounted for", f"{UNIT}/impl.md is not written yet, and {len(rows)} packages are out of date")
    text = impl.read_text(encoding="utf-8")
    unexplained = [name for name in rows if name not in text]
    return ok & say(
        not unexplained,
        f"all {len(rows)} held-back packages are named in impl.md",
        ", ".join(unexplained),
    )


def run() -> int:
    for tool in ("npm", "uv", "node"):
        if not shutil.which(tool):
            print(f"{tool} is not installed; this proof cannot answer.")
            return EXIT_ENV
    try:
        stages = stage_files()
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as e:
        print(f"cos.mjs status --json would not run: {e}")
        return EXIT_ENV

    results = [
        claim_1(),
        claim_2(),
        claim_3(stages),
        claim_4(),
        claim_5(),
        claim_6(),
        claim_7(),
        claim_8(),
        claim_9(),
        claim_10(),
        claim_11(),
    ]
    print()
    if all(results):
        print("PASS — the repository describes itself accurately and stands on current versions.")
        return EXIT_PASS
    print(f"FAIL — {results.count(False)} of {len(results)} claims did not hold.")
    return EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(run())
