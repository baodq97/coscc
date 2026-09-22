#!/usr/bin/env python3
"""Proof for .cos/0008_personal-name-blocks-publishing.

Fourteen claims. Thirteen are static; the last one asks GitHub whether the repository is
public, so it needs `gh` and a network. No session, no quota, no browser, no port.

    0  every claim held
    1  at least one did not
    2  the environment could not answer — no `git`, `npm`, `uv` or `gh`

Two things about this file are forced by what it is checking, and neither is a style
choice:

**It never spells the old name.** C1 claims that no file outside `.cos/` still carries the
author's name. This file lives in `scripts/`, so if it wrote that string as a literal to go
looking for it, it would be the last file failing its own claim — and the natural "fix"
would be to weaken C1, which is the one thing holding the unit's outcome up.
`scripts/verify_0007.py:14-17` hit exactly this and solved it the same way: the needle is
composed at import time and the literal never exists on disk.

**It imports nothing from the application package.** The old package name contains the
needle, so importing it is not available; the new one does not exist until step 3 of
`plan.md`. Everything therefore goes through `git`, the filesystem and `subprocess`. That
is also what lets this file be written first and run red, which `plan.md` step 1 requires.

`scripts/verify_0003.py:8-14` explains why exit 2 is kept apart from exit 1. The same split
holds here: "gh is not installed" is not "the repository was never published".
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.proof_harness import EXIT_BROKEN, EXIT_ENV, EXIT_PASS, say

REPO = Path(__file__).resolve().parent.parent

UNIT = ".cos/0008_personal-name-blocks-publishing"

# Composed, never written. See the module docstring.
NEEDLE = "bao" + "do"
OLD_PKG = "cos_" + NEEDLE
OLD_DIST = "cos-" + NEEDLE
OLD_STORE_FILE = "." + OLD_DIST + ".json"

NEW = "coscc"

# The commit this unit started from: the parent of its first artifact commit. C11 and C12
# diff against it. `.cos/0008_personal-name-blocks-publishing/plan.md` pins the same point.
BASE = "b923bba"

# `spec.md` R10, measured outside `.cos/` on purpose — a whole-repo count drifts every time
# this unit commits another artifact, which is `spec.md` C9.
ENV_PREFIX_LINES, ENV_PREFIX_FILES = 70, 20
COS_MJS_FILES = 21


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=False
    )


def grep_lines(pattern: str, *pathspec: str) -> list[str]:
    """`git grep -in`, tracked files only. Empty list when nothing matches."""
    out = git("grep", "-in", pattern, "--", *(pathspec or (".",)))
    if out.returncode not in (0, 1):
        raise RuntimeError(out.stderr.strip() or "git grep failed")
    return [ln for ln in out.stdout.splitlines() if ln]


def grep_files(pattern: str, *pathspec: str) -> list[str]:
    out = git("grep", "-li", pattern, "--", *(pathspec or (".",)))
    if out.returncode not in (0, 1):
        raise RuntimeError(out.stderr.strip() or "git grep failed")
    return [ln for ln in out.stdout.splitlines() if ln]


def grep_files_cs(pattern: str, *pathspec: str) -> list[str]:
    """Case-sensitive. `-i` on the environment prefix also matches the old package name,
    which is how a 110-line count became 537 while this unit was being specified."""
    out = git("grep", "-l", pattern, "--", *(pathspec or (".",)))
    if out.returncode not in (0, 1):
        raise RuntimeError(out.stderr.strip() or "git grep failed")
    return [ln for ln in out.stdout.splitlines() if ln]


def grep_lines_cs(pattern: str, *pathspec: str) -> list[str]:
    out = git("grep", "-n", pattern, "--", *(pathspec or (".",)))
    if out.returncode not in (0, 1):
        raise RuntimeError(out.stderr.strip() or "git grep failed")
    return [ln for ln in out.stdout.splitlines() if ln]


OUTSIDE = (".", ":!.cos")


def read(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.is_file() else ""


# --------------------------------------------------------------------------
# C1 — the name is gone everywhere the outcome measures
# --------------------------------------------------------------------------


def claim_1() -> bool:
    hits = grep_lines(NEEDLE, *OUTSIDE)
    detail = f"{len(hits)} line(s), first: {hits[0][:90] if hits else ''}"
    return say(not hits, "no personal name outside .cos/", detail)


# --------------------------------------------------------------------------
# C2 — and nothing else was renamed along with it (spec.md R10)
# --------------------------------------------------------------------------


def claim_2() -> bool:
    prefix_lines = grep_lines_cs("COS_", *OUTSIDE)
    prefix_files = grep_files_cs("COS_", *OUTSIDE)
    mjs_files = grep_files_cs(r"cos\.mjs", *OUTSIDE)

    ok = say(
        len(prefix_lines) == ENV_PREFIX_LINES and len(prefix_files) == ENV_PREFIX_FILES,
        f"the COS_ prefix is untouched ({ENV_PREFIX_LINES} lines / {ENV_PREFIX_FILES} files)",
        f"found {len(prefix_lines)} lines / {len(prefix_files)} files",
    )
    ok &= say(
        len(mjs_files) == COS_MJS_FILES,
        f"cos.mjs is still referenced by {COS_MJS_FILES} files outside .cos/",
        f"found {len(mjs_files)}",
    )
    data = read(f"{NEW}/data.py")
    ok &= say('DEFAULT_DIR = "~/.cos"' in data, "the data root is still ~/.cos")
    ok &= say('DB_FILENAME = "cos.db"' in data, "the database is still cos.db")
    ok &= say(
        (REPO / ".claude" / "skills" / "cos-status").is_dir(),
        "the cos-status skill kept its name",
    )
    return ok


# --------------------------------------------------------------------------
# C3 — the package answers to the new name
# --------------------------------------------------------------------------


def claim_3() -> bool:
    ok = say((REPO / NEW / f"{NEW}.py").is_file(), f"{NEW}/{NEW}.py exists")
    ok &= say(not (REPO / OLD_PKG).exists(), "the old package directory is gone")
    out = subprocess.run(
        ["uv", "run", "python", "-c", f"import {NEW}"],
        cwd=REPO, capture_output=True, text=True, check=False,
    )
    return ok & say(
        out.returncode == 0,
        f"import {NEW} succeeds",
        (out.stderr.strip().splitlines() or [""])[-1],
    )


# --------------------------------------------------------------------------
# C4 — the console scripts carry the new name and point somewhere real
# --------------------------------------------------------------------------


def claim_4() -> bool:
    try:
        meta = tomllib.loads(read("pyproject.toml"))
    except tomllib.TOMLDecodeError as e:
        return say(False, "pyproject.toml parses", str(e))

    project = meta.get("project", {})
    scripts = project.get("scripts", {})
    build = meta.get("tool", {}).get("uv", {}).get("build-backend", {})

    ok = say(project.get("name") == NEW, "the distribution is named coscc", str(project.get("name")))
    ok &= say(build.get("module-name") == NEW, "module-name is coscc", str(build.get("module-name")))

    wanted = {NEW: f"{NEW}.run:main", f"{NEW}-build": f"{NEW}.build:main"}
    for name, target in wanted.items():
        ok &= say(scripts.get(name) == target, f"{name} -> {target}", str(scripts.get(name)))
    ok &= say(
        (REPO / NEW / "run.py").is_file() and (REPO / NEW / "build.py").is_file(),
        "both entry-point modules exist",
    )
    return ok


# --------------------------------------------------------------------------
# C5 — the fingerprint's source list still points at files that exist
# --------------------------------------------------------------------------


def claim_5() -> bool:
    """`coscc/build.py` lists its inputs as string literals, so a missed rename does not
    raise — it makes the fingerprint say "current" about a stale bundle, which
    `build.py:16-19` warns about in as many words."""
    text = read(f"{NEW}/build.py")
    if not text:
        return say(False, "build.py declares _SOURCES", "the file is not there")
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return say(False, "build.py parses", str(e))

    sources: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_SOURCES" for t in node.targets
        ):
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                continue
            sources = [str(v) for v in value]

    if not sources:
        return say(False, "build.py declares _SOURCES", "no literal list found")
    missing = [s for s in sources if not (REPO / s).exists()]
    return say(not missing, f"all {len(sources)} fingerprint sources exist", ", ".join(missing))


# --------------------------------------------------------------------------
# C6 — the legacy store import is gone, not renamed (spec.md R3)
# --------------------------------------------------------------------------


def claim_6() -> bool:
    store = read(f"{NEW}/store.py")
    if not store:
        return say(False, "store.py is readable", "the file is not there")
    gone = ["STORE_FILENAME", "_import_legacy", "_load_legacy", "_needs_import", "legacy_path"]
    left = [n for n in gone if n in store]
    ok = say(not left, "the legacy store import is gone from store.py", ", ".join(left))

    stragglers = grep_files(OLD_STORE_FILE.replace(".", r"\."), *OUTSIDE)
    return ok & say(
        not stragglers,
        "nothing outside .cos/ names the pre-0006 JSON store",
        ", ".join(stragglers),
    )


# --------------------------------------------------------------------------
# C7 — and the journal's legacy import was NOT taken with it (spec.md R5)
# --------------------------------------------------------------------------


def claim_7() -> bool:
    journal = read(f"{NEW}/journal.py")
    data = read(f"{NEW}/data.py")
    ok = say(
        "cos-journal.jsonl" in journal and "_import_legacy" in journal,
        "the journal still imports its own legacy file",
    )
    return ok & say("def import_once" in data, "Data.import_once is still there")


# --------------------------------------------------------------------------
# C8 — the two sentences that described the JSON store are true again
# --------------------------------------------------------------------------


def claim_8() -> bool:
    ok = True
    for rel in (".claude/CLAUDE.md", "docs/studio.md"):
        text = read(rel)
        ok &= say(
            OLD_STORE_FILE not in text,
            f"{rel} no longer claims the JSON store is imported",
        )
    return ok


# --------------------------------------------------------------------------
# C9 — the name is finally explained, and not oversold
# --------------------------------------------------------------------------


def claim_9() -> bool:
    """`intent.md` counted 81 files using the name and none saying what it meant. A rename
    that swapped one unreadable acronym for another would leave that half unfixed —
    `spec.md` C3."""
    readme = read("README.md").lower()
    ok = say("chief of staff" in readme, "README says what CoS stands for")
    return ok & say(
        any(p in readme for p in ("not built", "not yet built", "is not implemented")),
        "README says the Chief-of-Staff function is not built yet",
    )


# --------------------------------------------------------------------------
# C10 — the lookup table exists, and the pointer to it does not undo C1
# --------------------------------------------------------------------------


def claim_10() -> bool:
    renames = read(".cos/RENAMES.md")
    harness = read(".claude/harness.md")
    ok = say(
        OLD_PKG in renames and f"{NEW}/" in renames,
        ".cos/RENAMES.md maps the old package onto the new one",
    )
    ok &= say("RENAMES.md" in harness, "harness.md points at the table")
    return ok & say(
        NEEDLE not in harness.lower(),
        "the pointer does not spell the old name",
        "harness.md is outside .cos/, so writing it there would fail C1",
    )


# --------------------------------------------------------------------------
# C11 — no accepted artifact was rewritten (intent.md constraint 4)
# --------------------------------------------------------------------------


def claim_11() -> bool:
    """This claim exists for one reason, written down at `plan.md` Risk 8: the count of the
    old name inside `.cos/` only ever goes up as this unit writes its own artifacts, and the
    temptation at the end is to tidy it by editing something that was already signed."""
    out = git("diff", "--name-only", f"{BASE}..HEAD", "--", ".cos")
    if out.returncode != 0:
        return say(False, "the base commit is reachable", out.stderr.strip())
    touched = [ln for ln in out.stdout.splitlines() if ln]
    allowed = {".cos/RENAMES.md"}
    stray = [p for p in touched if p not in allowed and not p.startswith(f"{UNIT}/")]
    return say(
        not stray,
        "no artifact outside this unit was rewritten",
        ", ".join(stray),
    )


# --------------------------------------------------------------------------
# C12 — the lockfiles carry a name change and nothing else (spec.md C7)
# --------------------------------------------------------------------------


def claim_12() -> bool:
    """`0007` re-measured every proof on Python 3.14 and reflex 0.9.12. A dependency that
    moved during an unrelated `uv lock` would pull the ground out from under those numbers
    with nothing to announce it."""
    out = git("diff", "-U0", f"{BASE}..HEAD", "--", "uv.lock", "package-lock.json")
    if out.returncode != 0:
        return say(False, "the base commit is reachable", out.stderr.strip())
    changed = [
        ln for ln in out.stdout.splitlines()
        if ln[:1] in "+-" and not ln.startswith(("+++", "---"))
    ]
    unrelated = [ln for ln in changed if OLD_DIST not in ln and NEW not in ln]
    return say(
        not unrelated,
        "the lockfiles changed only where the name is",
        f"{len(unrelated)} other line(s), first: {unrelated[0][:80] if unrelated else ''}",
    )


# --------------------------------------------------------------------------
# C13 — both runtimes still pass
# --------------------------------------------------------------------------


def claim_13() -> bool:
    out = subprocess.run(
        ["npm", "test"], cwd=REPO, capture_output=True, text=True, check=False
    )
    return say(out.returncode == 0, "npm test is green", f"it exited {out.returncode}")


# --------------------------------------------------------------------------
# C14 — and it is actually published (intent.md, the outcome)
# --------------------------------------------------------------------------

SLUG = f"baodq97/{NEW}"


def claim_14() -> bool:
    out = subprocess.run(
        ["gh", "repo", "view", SLUG, "--json", "visibility,isPrivate"],
        cwd=REPO, capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        return say(False, f"{SLUG} exists", (out.stderr.strip().splitlines() or [""])[0])
    try:
        info = json.loads(out.stdout)
    except json.JSONDecodeError as e:
        return say(False, f"{SLUG} reports its visibility", str(e))
    return say(
        info.get("visibility", "").upper() == "PUBLIC" and not info.get("isPrivate", True),
        f"{SLUG} is public",
        json.dumps(info),
    )


def run() -> int:
    for tool in ("git", "npm", "uv", "gh"):
        if not shutil.which(tool):
            print(f"{tool} is not installed; this proof cannot answer.")
            return EXIT_ENV
    try:
        grep_lines(NEEDLE, *OUTSIDE)
    except RuntimeError as e:
        print(f"git grep would not run: {e}")
        return EXIT_ENV

    results = [
        claim_1(),
        claim_2(),
        claim_3(),
        claim_4(),
        claim_5(),
        claim_6(),
        claim_7(),
        claim_8(),
        claim_9(),
        claim_10(),
        claim_11(),
        claim_12(),
        claim_13(),
        claim_14(),
    ]
    print()
    if all(results):
        print("PASS — the repository carries no personal name outside .cos/, and it is public.")
        return EXIT_PASS
    print(f"FAIL — {results.count(False)} of {len(results)} claims did not hold.")
    return EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(run())
