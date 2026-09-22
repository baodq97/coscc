#!/usr/bin/env python3
"""Proof for .cos/0009_branch-and-release-conventions.

Thirteen claims. Eight are static — they run `node`, read files, and ask nothing of the
network. Three ask GitHub about rulesets, releases and pull requests, so they need `gh` and
a login. One runs `npm test`.

    0  every claim held
    1  at least one did not
    2  the environment could not answer — no `git`, `node`, `npm`, `uv` or `gh`

`scripts/verify_0003.py:8-14` explains why exit 2 is kept apart from exit 1, and the split
matters more here than in most of these files: "`gh` cannot reach GitHub" is not "the
ruleset was never enabled", and treating the two the same would let a network outage read
as a unit that shipped nothing.

**Three claims are negative controls and they are the point.** C3 skews each of the four
version declarations in turn and requires the check to go red each time; C1 and C2 include
the rows that must be *rejected*. A grammar check that only ever sees valid input is a
function that returns true.

**The negative control never touches the working tree.** C3 copies `cos.mjs` and the four
version files into a temporary directory and runs the copy. `cos.mjs:9` derives its root
from the script's own location, so a copy two levels down a scratch directory reads that
scratch directory and nothing else.

This file is written before the commands it calls exist, which `plan.md` step 2 requires:
the first run is red, and every red line names a command `cos.mjs` does not yet declare.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.proof_harness import EXIT_BROKEN, EXIT_ENV, EXIT_PASS, say

REPO = Path(__file__).resolve().parent.parent

UNIT = "0009_branch-and-release-conventions"
SLUG = UNIT.split("_", 1)[1]

# `spec.md` R9 and R12 name this repository directly, so it is pinned rather than read off
# `git remote`. A proof that asks whichever remote happens to be configured would pass
# against a fork with a ruleset while this repository had none.
GH_REPO = "baodq97/coscc"

VERSION = "0.1.0"

# `spec.md` R13. Ten types, and the closed set is the point — C7 of the spec records that it
# will block somebody at an inconvenient moment on purpose.
TYPES = ("feat", "fix", "docs", "refactor", "test", "chore", "perf", "build", "ci", "revert")

MJS = ".claude/scripts/cos.mjs"

# `spec.md` R4. Two declared by hand, two generated, and all four drift independently.
VERSION_FILES = ("pyproject.toml", "package.json", "uv.lock", "package-lock.json")

# `spec.md` R10 requires the harness to say that the strongest leg does not travel with a
# copy. Pinning the sentence is brittle and deliberate: the claim is that the warning is
# present, and a claim that accepts any paraphrase accepts its absence too.
RULESET_WARNING = "does not travel with the harness"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args), cwd=cwd or REPO, capture_output=True, text=True, check=False
    )


def cos(*args: str, root: Path | None = None) -> subprocess.CompletedProcess[str]:
    """`cos.mjs` as a person runs it."""
    script = str((root or REPO) / MJS) if root else str(REPO / MJS)
    return run("node", script, *args, cwd=root or REPO)


def gh_json(*args: str):
    """`gh api` or `gh ... --json`, decoded. `None` when the call failed."""
    out = run("gh", *args)
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return None


def read(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def exits(expected: int, *args: str) -> tuple[bool, str]:
    got = cos(*args).returncode
    return got == expected, f"`cos.mjs {' '.join(args)}` exited {got}, wanted {expected}"


# --------------------------------------------------------------------------
# C1 — the branch grammar, eight rows, two of them accepted
# --------------------------------------------------------------------------

# `spec.md` R1, copied row for row. `True` means the name is accepted.
BRANCHES = [
    ("feat/branch-conventions", True),
    ("fix/version-drift", True),
    ("main", False),
    ("feature/foo", False),
    ("feat/Foo", False),
    ("feat/", False),
    ("feat/a--b", False),
    ("feat/foo/bar", False),
]


def claim_1() -> bool:
    wrong = []
    for name, ok in BRANCHES:
        code = cos("check-branch", name).returncode
        if code != (0 if ok else 1):
            wrong.append(f"{name!r} exited {code}, wanted {0 if ok else 1}")
    return say(
        not wrong,
        f"the branch grammar answers all {len(BRANCHES)} rows of spec R1",
        "; ".join(wrong),
    )


# --------------------------------------------------------------------------
# C2 — the tag grammar
# --------------------------------------------------------------------------

TAGS = [
    ("v0.1.0", True),
    ("v1.20.3", True),
    ("v0.1.0-rc.1", True),
    ("v0.1.0-rc.12", True),
    ("0.1.0", False),
    ("v0.1", False),
    ("v0.1.0-rc", False),
    ("v0.1.0-rc.0", False),
]


def claim_2() -> bool:
    wrong = []
    for name, ok in TAGS:
        code = cos("check-tag", name).returncode
        if code != (0 if ok else 1):
            wrong.append(f"{name!r} exited {code}, wanted {0 if ok else 1}")
    return say(
        not wrong,
        f"the tag grammar answers all {len(TAGS)} cases of spec R2",
        "; ".join(wrong),
    )


# --------------------------------------------------------------------------
# C3 — version sync, and it goes red for each of the four places in turn
# --------------------------------------------------------------------------


def claim_3() -> bool:
    live = cos("check-version").returncode
    if live != 0:
        return say(False, "the version check is green on this tree", f"exited {live}")

    missed = []
    with tempfile.TemporaryDirectory() as tmp:
        sandbox = Path(tmp)
        (sandbox / ".claude" / "scripts").mkdir(parents=True)
        shutil.copy2(REPO / MJS, sandbox / MJS)
        for name in VERSION_FILES:
            shutil.copy2(REPO / name, sandbox / name)

        clean = cos("check-version", root=sandbox).returncode
        if clean != 0:
            return say(False, "the version check is green on a clean copy", f"exited {clean}")

        for name in VERSION_FILES:
            target = sandbox / name
            good = target.read_text(encoding="utf-8")
            target.write_text(good.replace(VERSION, "9.9.9", 1), encoding="utf-8")
            code = cos("check-version", root=sandbox).returncode
            target.write_text(good, encoding="utf-8")
            if code != 1:
                missed.append(f"{name} skewed and the check exited {code}")

    return say(
        not missed,
        f"the version check goes red for each of the {len(VERSION_FILES)} places on its own",
        "; ".join(missed),
    )


# --------------------------------------------------------------------------
# C4 — `--root` stops at the commands that read git
# --------------------------------------------------------------------------


def claim_4() -> bool:
    problems = []
    for cmd in (("check-branch", "feat/x"), ("check-tag", "v0.1.0"), ("check-version",)):
        code = cos("--root", "/tmp", *cmd).returncode
        if code == 0:
            problems.append(f"`{cmd[0]}` accepted --root")
    code = cos("--root", str(REPO), "unit-branch", UNIT).returncode
    if code != 0:
        problems.append(f"`unit-branch` refused --root (exited {code})")
    return say(
        not problems,
        "--root reaches the .cos/ reader and stops at the three that describe this checkout",
        "; ".join(problems),
    )


# --------------------------------------------------------------------------
# C5 — four declarations, one number
# --------------------------------------------------------------------------


def declared_versions() -> dict[str, str | None]:
    """The five numbers, each read the way its own format means it.

    A single regex over all four files is what the first draft did, and it was wrong twice:
    `uv.lock` holds a `version` line for every package it locks, so a pattern match returns
    whichever dependency sorts first, and a JSON key is `"version":` rather than `version =`.
    Both mistakes read *a* number and call it the project's.
    """
    out: dict[str, str | None] = {}

    try:
        out["pyproject.toml"] = tomllib.loads(read("pyproject.toml"))["project"]["version"]
    except (tomllib.TOMLDecodeError, KeyError):
        out["pyproject.toml"] = None

    try:
        out["package.json"] = json.loads(read("package.json"))["version"]
    except (json.JSONDecodeError, KeyError):
        out["package.json"] = None

    try:
        locked = tomllib.loads(read("uv.lock"))["package"]
        out["uv.lock"] = next(p["version"] for p in locked if p["name"] == "coscc")
    except (tomllib.TOMLDecodeError, KeyError, StopIteration):
        out["uv.lock"] = None

    # Two of them, and they are separate keys that can disagree with each other.
    for key, where in (("package-lock.json", lambda d: d["version"]),
                       ("package-lock.json packages[''].version",
                        lambda d: d["packages"][""]["version"])):
        try:
            out[key] = where(json.loads(read("package-lock.json")))
        except (json.JSONDecodeError, KeyError):
            out[key] = None

    return out


def claim_5() -> bool:
    found = declared_versions()
    off = sorted(f"{k} is {v!r}" for k, v in found.items() if v != VERSION)
    return say(
        not off,
        f"all {len(found)} version declarations read {VERSION}",
        "; ".join(off),
    )


# --------------------------------------------------------------------------
# C6 — the harness carries the convention
# --------------------------------------------------------------------------


def claim_6() -> bool:
    text = read(".claude/harness.md")
    missing = [t for t in TYPES if re.search(rf"`{t}`", text) is None]
    if missing:
        return say(False, "the harness names all ten branch types", ", ".join(missing))
    gaps = []
    for needle, label in (
        ("vX.Y.Z", "the release tag form"),
        ("-rc.N", "the prerelease tag form"),
        ("Type:", "the intent.md Type field"),
        (RULESET_WARNING, "the warning that the ruleset does not travel"),
    ):
        if needle not in text:
            gaps.append(label)
    return say(not gaps, "the harness carries the whole convention", "missing: " + ", ".join(gaps))


# --------------------------------------------------------------------------
# C7 — the first workflows of a public repository
# --------------------------------------------------------------------------

# No YAML parser: `spec.md` criterion 3 forbids adding a dependency, and pyyaml is not
# installed. So this is structural rather than a parse — which means a syntactically broken
# file passes here and fails at step 9 of `plan.md`, where GitHub itself is the parser and
# an empty `gh pr checks` is the symptom. That gap is Risk 1 of the plan, not an oversight.
WORKFLOWS = (".github/workflows/pr.yml", ".github/workflows/release.yml")

SHA_PIN = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S+)", re.MULTILINE)


def claim_7() -> bool:
    problems = []
    for rel in WORKFLOWS:
        text = read(rel)
        if not text:
            problems.append(f"{rel} is missing")
            continue
        if not re.search(r"^permissions:", text, re.MULTILINE):
            problems.append(f"{rel} declares no top-level permissions")
        if "pull_request_target" in text:
            problems.append(f"{rel} uses pull_request_target")
        for ref in SHA_PIN.findall(text):
            if ref.startswith("./"):
                continue
            _, _, pin = ref.partition("@")
            if not re.fullmatch(r"[0-9a-f]{40}", pin):
                problems.append(f"{rel} pins {ref} by something other than a commit SHA")
    return say(
        not problems,
        f"both workflows declare permissions and pin every third-party action by SHA",
        "; ".join(problems),
    )


# --------------------------------------------------------------------------
# C8 — a work unit knows its type, and the branch follows from it
# --------------------------------------------------------------------------


def claim_8() -> bool:
    header = read(f".cos/{UNIT}/intent.md").split("\n\n", 1)[0]
    if "Type: feat" not in header:
        return say(False, f"{UNIT} declares its type", "no `Type: feat.` on the intent header")
    out = cos("unit-branch", UNIT)
    want = f"feat/{SLUG}"
    if out.returncode != 0 or out.stdout.strip() != want:
        return say(
            False,
            "the branch name is derived from the unit, not typed by hand",
            f"got {out.stdout.strip()!r} (exit {out.returncode}), wanted {want!r}",
        )
    return say(True, f"{UNIT} derives {want} from its type")


# --------------------------------------------------------------------------
# C9 — the skill asks for the field, or nobody writes it
# --------------------------------------------------------------------------


def claim_9() -> bool:
    text = read(".claude/skills/write-intent/SKILL.md")
    return say(
        "Type:" in text,
        "write-intent asks for a Type, so the field is written rather than read",
        "the template header still carries only Author and Status",
    )


# --------------------------------------------------------------------------
# C10 — the only leg that blocks anything
# --------------------------------------------------------------------------


def ruleset_on_main() -> dict | None:
    rules = gh_json("api", f"repos/{GH_REPO}/rulesets")
    if not rules:
        return None
    for summary in rules:
        detail = gh_json("api", f"repos/{GH_REPO}/rulesets/{summary['id']}")
        if not detail or detail.get("enforcement") != "active":
            continue
        refs = detail.get("conditions", {}).get("ref_name", {}).get("include", [])
        if not any(r in ("~DEFAULT_BRANCH", "refs/heads/main") for r in refs):
            continue
        if any(r.get("type") == "pull_request" for r in detail.get("rules", [])):
            return detail
    return None


def claim_10() -> bool:
    return say(
        ruleset_on_main() is not None,
        "an active ruleset makes main accept commits only through a pull request",
        "no active ruleset on main carries a pull_request rule",
    )


# --------------------------------------------------------------------------
# C11 — two releases, and the tag sits on main
# --------------------------------------------------------------------------


def claim_11() -> bool:
    releases = gh_json("release", "list", "--repo", GH_REPO, "--json", "tagName,isPrerelease")
    if releases is None:
        return say(False, "GitHub lists the releases", "`gh release list` did not answer")
    by_tag = {r["tagName"]: r["isPrerelease"] for r in releases}
    problems = []
    if len(releases) != 2:
        problems.append(f"{len(releases)} releases, wanted 2: {sorted(by_tag)}")
    if by_tag.get("v0.1.0") is not False:
        problems.append("v0.1.0 is missing or marked prerelease")
    pre = [t for t, p in by_tag.items() if p and re.fullmatch(r"v0\.1\.0-rc\.\d+", t)]
    if not pre:
        problems.append("no v0.1.0-rc.N marked prerelease")
    if "v0.1.0" in by_tag:
        out = run("git", "branch", "--contains", "v0.1.0", "--format=%(refname:short)")
        if "main" not in out.stdout.split():
            problems.append("the v0.1.0 tag is not on main")
    return say(not problems, "two releases: one prerelease, then v0.1.0 on main", "; ".join(problems))


# --------------------------------------------------------------------------
# C12 — the outcome, measured from the gate rather than from a date
# --------------------------------------------------------------------------


def claim_12() -> bool:
    ruleset = ruleset_on_main()
    if ruleset is None:
        return say(False, "every commit on main since the gate arrived through a PR",
                   "there is no ruleset, so there is no gate to measure from")
    since = ruleset.get("created_at")
    if not since:
        return say(False, "every commit on main since the gate arrived through a PR",
                   "the ruleset carries no created_at")
    commits = gh_json("api", f"repos/{GH_REPO}/commits?sha=main&since={since}&per_page=100")
    if commits is None:
        return say(False, "every commit on main since the gate arrived through a PR",
                   "GitHub did not list the commits")
    direct = []
    for c in commits:
        pulls = gh_json("api", f"repos/{GH_REPO}/commits/{c['sha']}/pulls")
        if not pulls or not any(p.get("merged_at") for p in pulls):
            direct.append(c["sha"][:7])
    return say(
        not direct,
        f"all {len(commits)} commits on main since {since} came through a merged PR",
        "straight to main: " + ", ".join(direct),
    )


# --------------------------------------------------------------------------
# C13 — the repository's own floor
# --------------------------------------------------------------------------


def claim_13() -> bool:
    out = run("npm", "test")
    return say(out.returncode == 0, "npm test is green", (out.stdout + out.stderr)[-400:])


def main() -> int:
    for tool in ("git", "node", "npm", "uv", "gh"):
        if not shutil.which(tool):
            print(f"{tool} is not installed; this proof cannot answer.")
            return EXIT_ENV
    if run("gh", "auth", "status").returncode != 0:
        print("gh is not logged in; three of these claims ask GitHub directly.")
        return EXIT_ENV

    results = [
        claim_1(), claim_2(), claim_3(), claim_4(), claim_5(), claim_6(), claim_7(),
        claim_8(), claim_9(), claim_10(), claim_11(), claim_12(), claim_13(),
    ]
    print()
    if all(results):
        print("PASS — the convention is written, checked in three places, and released once.")
        return EXIT_PASS
    print(f"FAIL — {results.count(False)} of {len(results)} claims did not hold.")
    return EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(main())
