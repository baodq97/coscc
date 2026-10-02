"""`screens`, and the `review` and `ship` gates that ask git and `gh`, print what the goldens hold.

Every case runs both versions through `expect()` on a real git repository in tmp and a `gh` that
answers from a table. A case also checks the words or codes it means to reach, so that both
versions agreeing on a wrong branch cannot pass for coverage. The gates and `next` go through
`rules.py`; the probe, the pull request and the screens are `repo_rules.py`'s.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from coscc.loop.repo_rules import branch_checks
from tests.loop.conftest import UnitStore, entry, env, fake_gh, git, git_repo, header, expect

UNIT = "0040_widget"
BRANCH = "feat/widget"
TITLE = "feat(0040): add the widget"
PR_URL = "https://github.com/o/r/pull/7"
STANDARD = ".claude/rules/ui-standard.md"
STANDARD_TEXT = (
    "---\npaths:\n"
    '  - "src/ui/**"\n'
    "  - 'web/?.html'\n"
    "  - **/*.tsx\n"
    "  - docs/*.md\n"
    "---\n# UI standard\n"
)
CHECKS = "pr checks 7 --required --json name,bucket"
HEAD_NAME = "pr view 7 --json headRefName"
VIEW = "pr view 7 --json state,headRefOid,mergeCommit,mergedAt,title"
KIND = {
    "intent.md": "Intent",
    "spec.md": "Spec",
    "plan.md": "Plan",
    "impl.md": "Impl",
    "pr.md": "PR",
    "review.md": "Review",
    "ship.md": "Ship",
}
GREEN = "[" + '{"name":"build","bucket":"pass"},{"name":"lint","bucket":"skipping"}' + "]"
NOBODY = "0" * 40


def checks(*pairs: tuple[str, str]) -> str:
    return json.dumps([{"name": n, "bucket": b} for n, b in pairs])


def view(head: str, state: str = "OPEN", title: str | None = TITLE, **over) -> str:
    body = {"state": state, "headRefOid": head, "mergeCommit": None, "mergedAt": None}
    if title is not None:
        body["title"] = title
    body.update(over)
    return json.dumps(body)


def pr_text(title: str | None = TITLE, url: str | None = PR_URL) -> str:
    first = "# PR:\n" if title == "" else f"# PR: {title}\n"
    return (first if title is not None else "# PR\n") + (
        "Author: test. Status: accepted.\n\n" + (f"PR: {url}\n" if url else "")
    )


def finding(n: int, label: str, severity: str, text: str = "the function is wrong") -> str:
    return f"- F{n} [{label}] src/a.py:{n} — {severity} — {text}"


def rnd(n: int, verdict: str, sha: str, *findings: str, screens: str = "") -> str:
    body = f"## Round {n}\nReviewed: {sha}. Verdict: {verdict}.\n\n### Findings\n"
    body += "".join(f"{f}\n" for f in findings)
    if screens:
        body += f"\n### Screens\n{screens}"
    return body + "\n"


def review_text(status: str, *rounds: str, tail: str = "") -> str:
    return header("Tiêu đề", status, "Review") + "\n" + "".join(rounds) + tail


def shots(taken: str, by: str = "agent session s1", standard: str = STANDARD) -> str:
    return (
        f"Taken at: {taken}. Standard: {standard}. Looked at by: {by}, from screenshots.\n"
        "- .screens/home.png — 800×600 — /home — ok\n"
    )


def answered(r) -> dict:
    return json.loads(r.out)


def text_of(gate: dict) -> str:
    return "\n".join(gate["lines"])


class Scene:
    """A repository on `feat/widget` one commit past `main`, a store with the unit, a `gh`."""

    def __init__(self, tmp_path: Path, standard: str | None = None, workflows=None):
        self.tmp = tmp_path
        self.store = UnitStore(tmp_path / "store")
        self.repo = git_repo(tmp_path / "repo")
        self.bin = tmp_path / "bin"
        if standard is not None:
            self.write({STANDARD: standard})
        for name, text in (workflows or {}).items():
            self.write({f".github/workflows/{name}": text})
        if standard is not None or workflows:
            git(self.repo, "add", "-A")
            git(self.repo, "commit", "-q", "-m", "base")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "main")
        git(self.repo, "checkout", "-q", "-b", BRANCH)
        self.reviewed = self.commit({"src/a.py": "a = 1\n"}, "feat: a")
        git(self.repo, "update-ref", f"refs/remotes/origin/{BRANCH}", self.reviewed)

    def write(self, files: dict[str, str]) -> None:
        for path, text in files.items():
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_text(text)

    def commit(self, files: dict[str, str], message: str = "more") -> str:
        self.write(files)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", message)
        return git(self.repo, "rev-parse", "HEAD")

    def rev(self, ref: str) -> str:
        return git(self.repo, "rev-parse", ref)

    def advance_main(self, files: dict[str, str] | None = None) -> str:
        """A commit on `main` and `origin/main`, then back on the unit's branch."""
        git(self.repo, "checkout", "-q", "main")
        sha = self.commit(files or {"other.txt": "o\n"}, "main moves")
        git(self.repo, "update-ref", "refs/remotes/origin/main", sha)
        git(self.repo, "checkout", "-q", BRANCH)
        return sha

    def rebase(self, onto: str, amend: dict[str, str] | None = None) -> str:
        """The reviewed commit cherry-picked onto `onto`, on its own branch; back on the unit's."""
        git(self.repo, "checkout", "-q", "-B", "rebased", onto)
        git(self.repo, "cherry-pick", self.reviewed)
        sha = self.commit(amend, "amend") if amend else self.rev("HEAD")
        git(self.repo, "checkout", "-q", BRANCH)
        return sha

    def unit(
        self,
        review: str | None = None,
        status: str = "accepted",
        pr: str | None = None,
        ship=None,
        rows: list | None = None,
    ):
        arts = {f: "accepted" for f in ("intent.md", "spec.md", "plan.md", "impl.md", "pr.md")}
        files = {f: header("Tiêu đề", "accepted", KIND[f]) for f in arts}
        files["pr.md"] = pr if pr is not None else pr_text()
        if review is not None:
            files["review.md"] = review
            arts["review.md"] = status
        if ship is not None:
            files["ship.md"] = ship
            arts["ship.md"] = "draft"
        known = entry(arts, **({"review_md": {"rounds": rows}} if rows else {}))
        self.store.unit(UNIT, files, known)

    def shim(self, *rules: dict) -> None:
        """A `git` first on `PATH` that answers `rules` — `match` words all in the argv, none of
        `not` — with `code`, `out` and `err`, and runs the real one otherwise."""
        self.bin.mkdir(parents=True, exist_ok=True)
        table = self.bin / "git-rules.json"
        table.write_text(json.dumps(list(rules)))
        script = self.bin / "git"
        script.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            f"rules = json.load(open({str(table)!r}))\n"
            "line = ' '.join(sys.argv[1:])\n"
            "for r in rules:\n"
            "    if all(m in line for m in r['match']) and not any(m in line for m in r.get('not', [])):\n"
            "        sys.stdout.write(r.get('out', '')); sys.stderr.write(r.get('err', ''))\n"
            "        sys.exit(r.get('code', 0))\n"
            f"os.execv({shutil.which('git')!r}, ['git', *sys.argv[1:]])\n"
        )
        script.chmod(0o755)

    def passed(self, *extra: str, sha: str | None = None, screens: str = "") -> str:
        """A review with one passing round that fixed."""
        sha = sha or self.reviewed
        fixed = finding(1, f"fixed {sha[:7]}", "high")
        return review_text("accepted", rnd(1, "pass", sha, fixed, *extra, screens=screens))

    def run(self, *words: str, gh: dict | None = None, repo: bool = True, **extra: str):
        argv = self.store.argv(*words)
        if repo:
            argv += ["--repo", str(self.repo)]
        return expect(argv, environ={**fake_gh(self.bin, gh or {}), **extra})

    def three(self, gh: dict | None = None, **extra: str) -> tuple[dict, dict, str]:
        """`gate ship --json`, `gate review --json`, and `next`, each equal in both versions."""
        gh = {
            CHECKS: (0, GREEN, ""),
            HEAD_NAME: (0, json.dumps({"headRefName": BRANCH}), ""),
            **(gh or {}),
        }
        ship = self.run("gate", UNIT, "ship", "--json", gh=gh, **extra)
        rev = self.run("gate", UNIT, "review", "--json", gh=gh, **extra)
        nxt = self.run("next", UNIT, gh=gh, **extra)
        self.run("gate", UNIT, "ship", gh=gh, **extra)
        return json.loads(ship.out), json.loads(rev.out), nxt.out


@pytest.fixture
def sc(tmp_path: Path) -> Scene:
    return Scene(tmp_path)


def ui_scene(tmp_path: Path) -> Scene:
    return Scene(tmp_path, standard=STANDARD_TEXT)


def open_pr(head: str, **over) -> dict:
    return {VIEW: (0, view(head, **over), "")}


# --- screens ------------------------------------------------------------------------------


def test_screens_is_refused_without_what_it_needs(sc):
    sc.unit()
    assert sc.run("screens").code == 2
    assert sc.run("screens", "nope").code == 2
    assert sc.run("screens", "0999_none").code == 2
    assert sc.run("screens", UNIT, repo=False).code == 2


def test_screens_without_a_standard_changes_no_screen(sc):
    sc.unit()
    r = sc.run("screens", UNIT)
    assert (
        json.loads(r.out)["why"] == "this unit changes no file the UI standard counts as a screen"
    )


def test_screens_with_a_standard_that_has_no_globs(tmp_path):
    s = Scene(tmp_path, standard="---\ntitle: x\n---\nno paths here\n")
    s.unit()
    assert json.loads(s.run("screens", UNIT).out)["ui"] == []


@pytest.mark.parametrize(
    "text",
    [
        "no front matter\n",
        "---\npaths:\n  - src/**\n",
        '---\r\npaths:\r\n  - "src/**"\r\n---\r\n',
        "---\npaths:\n  - 'src/**'\n\n  - web/?.html\nname: x\n  - ignored/**\n---\n",
        "---\npaths:   \n- src/**\n---\n",
        '---\npaths:\n  -   src/**   \n  - "unbalanced\n---\n',
    ],
)
def test_screens_reads_a_standard_as_it_is_written(tmp_path, text):
    s = Scene(tmp_path, standard=text)
    s.commit({"src/x.py": "1\n", "web/a.html": "1\n", "ignored/y": "1\n"}, "ui")
    s.unit()
    same_json = json.loads(s.run("screens", UNIT).out)
    assert isinstance(same_json["ui"], list)


@pytest.mark.parametrize(
    "files",
    [
        {"src/ui/page.py": "1\n"},
        {"src/ui/deep/er/page.py": "1\n", "web/a.html": "1\n", "web/ab.html": "1\n"},
        {"app.tsx": "1\n", "src/sub/app.tsx": "1\n", "docs/x.md": "1\n", "docs/sub/y.md": "1\n"},
        {".cos/0040_widget/note.md": "1\n", "src/ui/ok.py": "1\n"},
        {"src/ui.py": "1\n"},
    ],
)
def test_screens_counts_the_files_the_globs_name(tmp_path, files):
    s = ui_scene(tmp_path)
    s.commit(files, "ui")
    s.unit()
    r = json.loads(s.run("screens", UNIT).out)
    assert r["why"] in (
        "there is no readable .screens/manifest.json in this checkout",
        "this unit changes no file the UI standard counts as a screen",
    )


MANIFESTS = {
    "absent": None,
    "not json": "{nope",
    "empty": "",
    "bom": "\ufeff{}",
    "an array": "[]",
    "a string": '"x"',
    "null": "null",
    "no addresses": '{"head":"abcdef1","dirty":false}',
    "empty addresses": '{"head":"abcdef1","dirty":false,"addresses":[]}',
    "odd addresses": '{"head":"abcdef1","dirty":false,"addresses":["/a",1]}',
    "dirty": '{"head":"abcdef1","dirty":true,"addresses":["/a"]}',
    "dirty missing": '{"head":"abcdef1","addresses":["/a"]}',
    "no head": '{"dirty":false,"addresses":["/a"]}',
    "head not a name": '{"head":"; rm -rf","dirty":false,"addresses":["/a"]}',
    "head number": '{"head":12345678,"dirty":false,"addresses":["/a"]}',
    "numbers": '{"head":"abcdef1","dirty":false,"addresses":["/a"],"hits":[1.0,2.5,1e2,-0,-0.0,1E400],"x":1}',
    "hits not a list": '{"head":"abcdef1","dirty":false,"addresses":["/a"],"hits":{"a":1}}',
    "nan": '{"head":"abcdef1","dirty":false,"addresses":["/a"],"hits":[NaN]}',
}


@pytest.mark.parametrize("name", list(MANIFESTS))
def test_screens_reads_the_manifest(tmp_path, name):
    s = ui_scene(tmp_path)
    s.commit({"src/ui/page.py": "1\n"}, "ui")
    s.unit()
    if MANIFESTS[name] is not None:
        s.write({".screens/manifest.json": MANIFESTS[name]})
    s.run("screens", UNIT)


def manifest(head: str, **over) -> str:
    return json.dumps({"head": head, "dirty": False, "addresses": ["/home"], "hits": [], **over})


def test_screens_head_still_an_ancestor_is_not_retaken(tmp_path):
    s = ui_scene(tmp_path)
    s.commit({"src/ui/page.py": "1\n"}, "ui")
    s.unit()
    s.write({".screens/manifest.json": manifest(s.rev("HEAD~1"))})
    r = json.loads(s.run("screens", UNIT).out)
    assert r["retake"] is False
    assert "is still an ancestor of HEAD" in r["why"]


@pytest.mark.parametrize("gone", [False, True])
def test_screens_head_rewritten_away_is_retaken(tmp_path, gone):
    s = ui_scene(tmp_path)
    s.commit({"src/ui/page.py": "1\n"}, "ui")
    s.unit()
    old = s.rev("HEAD")
    git(s.repo, "commit", "-q", "--amend", "-m", "ui again")
    s.write({".screens/manifest.json": manifest(NOBODY if gone else old)})
    assert json.loads(s.run("screens", UNIT).out)["retake"] is True


def test_screens_when_git_cannot_say(tmp_path):
    s = ui_scene(tmp_path)
    s.unit()
    git(s.repo, "branch", "-m", "main", "trunk")
    git(s.repo, "update-ref", "-d", "refs/remotes/origin/main")
    r = json.loads(s.run("screens", UNIT).out)
    assert r["why"].startswith("cannot tell whether")


def test_screens_without_git_on_the_path(tmp_path):
    s = ui_scene(tmp_path)
    s.unit()
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "node").symlink_to(shutil.which("node") or "node")
    r = s.run("screens", UNIT, PATH=str(bare))
    assert "spawnSync git ENOENT" in json.loads(r.out)["why"]


def test_screens_in_a_repository_that_is_not_there(sc):
    sc.unit()
    expect([*sc.store.argv("screens", UNIT), "--repo", str(sc.tmp / "nowhere")], environ=env())


# --- the review gate: CI ------------------------------------------------------------------

CI_READS = {
    "green": (0, GREEN, "", True, []),
    "green with a failing exit": (1, GREEN, "", True, []),
    "pending": (8, checks(("build", "pending")), "", False, ["ci-pending"]),
    "queued": (8, checks(("build", "pass"), ("lint", "queued")), "", False, ["ci-pending"]),
    "none": (0, "[]", "", False, ["gate-closed"]),
    "an error": (1, "", "HTTP 404: Not Found\n", False, ["gate-closed"]),
    "silent": (4, "", "", False, ["gate-closed"]),
    "not json": (0, "garbage", "", False, ["gate-closed"]),
    "an object": (0, '{"a":1}', "", False, ["gate-closed"]),
    "out but no err": (2, "oops", "", False, ["gate-closed"]),
    "no name": (0, '[{"bucket":"pending"}]', "", False, ["ci-pending"]),
}


@pytest.mark.parametrize("name", list(CI_READS))
def test_review_gate_reads_ci(sc, name):
    code, out, err, ok, reasons = CI_READS[name]
    sc.unit()
    r = answered(sc.run("gate", UNIT, "review", "--json", gh={CHECKS: (code, out, err)}))
    assert (r["ok"], r["reasons"]) == (ok, reasons)
    sc.run("gate", UNIT, "review", gh={CHECKS: (code, out, err)})


def test_review_gate_without_a_repo_or_a_pr(sc):
    sc.unit()
    assert "no repository given" in sc.run("gate", UNIT, "review", repo=False).err
    sc.unit(pr=pr_text(url=None))
    assert "names no pull request" in sc.run("gate", UNIT, "review", "--json").out


@pytest.mark.parametrize(
    "title",
    ["", None, "feat(0040) add", "chore(0040): add", "fix(0041): add", "feat(0040): wip", "feat(0040): WIP: x",
     "feat(0040): wipe the cache", "feat(0040): café", "foo(0040): add", "feat(0040): " + "x" * 20 + "é"],
)  # fmt: skip
def test_review_gate_checks_the_title_before_it_asks_gh(sc, title):
    sc.unit(pr=pr_text(title))
    sc.run("gate", UNIT, "review", "--json")
    sc.run("gate", UNIT, "ship", "--json")


def test_review_gate_when_gh_cannot_start(tmp_path):
    s = Scene(tmp_path)
    s.unit()
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "node").symlink_to(shutil.which("node") or "node")
    r = s.run("gate", UNIT, "review", PATH=str(bare))
    assert "spawnSync gh ENOENT" in r.out + r.err


def test_review_gate_in_a_repository_that_is_not_there(sc):
    sc.unit()
    argv = [*sc.store.argv("gate", UNIT, "review", "--json"), "--repo", str(sc.tmp / "nowhere")]
    r = expect(argv, environ=fake_gh(sc.bin, {CHECKS: (0, GREEN, "")}))
    assert "spawnSync gh ENOENT" in r.out


def three_rounds(sc, verdict="changes-requested") -> str:
    return review_text(
        "changes-requested",
        *(rnd(n, verdict, sc.reviewed, finding(1, "open", "high")) for n in (1, 2, 3)),
    )


@pytest.mark.parametrize("limit", [None, "5", "2"])
def test_review_gate_out_of_rounds(sc, limit):
    sc.unit(three_rounds(sc), status="changes-requested")
    extra = {} if limit is None else {"COS_REVIEW_ROUNDS": limit}
    r = sc.run("gate", UNIT, "review", "--json", gh={CHECKS: (0, GREEN, "")}, **extra)
    assert answered(r)["ok"] is (limit == "5")


def test_review_gate_out_of_rounds_and_granted_more(sc):
    more = "\n## Answers\n### More rounds\nDecided by: Phong. Date: 2026-10-01. Via: board.\nRounds: 2\n"
    sc.unit(three_rounds(sc) + more, status="changes-requested")
    r = sc.run("gate", UNIT, "review", "--json", gh={CHECKS: (0, GREEN, "")})
    assert answered(r)["ok"] is True


def workflow(
    job: str = "branch-name",
    step: str = '      - run: uv run python -m coscc.loop check-branch "$HEAD_REF"',
) -> str:
    return (
        f"name: ci\non: [push]\njobs:\n  {job}:\n    runs-on: ubuntu-latest\n    steps:\n{step}\n"
    )


WORKFLOWS = {
    "plain": {"ci.yml": workflow()},
    "named": {"ci.yml": workflow().replace("    runs-on", "    name: Branch name\n    runs-on")},
    "named with a comment": {
        "ci.yml": workflow().replace("    runs-on", '    name: "Branch name" # why\n    runs-on')
    },
    "named single quoted": {
        "ci.yml": workflow().replace("    runs-on", "    name: 'Branch name'\n    runs-on")
    },
    "named from a matrix": {
        "ci.yml": workflow().replace("    runs-on", "    name: ${{ matrix.x }}\n    runs-on")
    },
    "named by a block": {
        "ci.yml": workflow().replace("    runs-on", "    name: >\n      Branch\n    runs-on")
    },
    "name then a plain comment": {
        "ci.yml": workflow().replace("    runs-on", "    name: Branch name # c\n    runs-on")
    },
    "a block run": {
        "ci.yml": workflow(
            step="      - run: |\n          echo hi\n          uv run python -m coscc.loop check-branch"
        )
    },
    "a folded run": {
        "ci.yml": workflow(step="      - run: >\n          python -m coscc.loop check-branch")
    },
    "a run on the next line": {
        "ci.yml": workflow(step="      - run:\n          python -m coscc.loop check-branch")
    },
    "a run key, no dash": {
        "ci.yml": workflow(
            step="      - name: x\n        run: python -m coscc.loop   check-branch main"
        )
    },
    "a comment only": {
        "ci.yml": workflow(
            step="      # - run: python -m coscc.loop check-branch\n      - run: echo hi"
        )
    },
    "another script": {"ci.yml": workflow(step="      - run: python -m coscc.loop check-tag")},
    "quoted key": {"ci.yml": workflow(job='"branch-name"')},
    "single quoted key": {"ci.yml": workflow(job="'branch-name'")},
    "a key with a comment": {"ci.yml": workflow().replace("branch-name:", "branch-name: # why")},
    "crlf": {"ci.yml": workflow().replace("\n", "\r\n")},
    "no jobs": {"ci.yml": "name: ci\non: [push]\n"},
    "jobs ends at a top key": {
        "ci.yml": "jobs:\n  a:\n    steps:\n      - run: echo\non: x\n" + workflow()
    },
    "two files": {"a.yml": "name: a\n", "b.yaml": workflow(), "c.txt": workflow(job="never")},
    "a second job": {"ci.yml": workflow() + "  other:\n    steps:\n      - run: echo hi\n"},
    "a dedented job": {
        "ci.yml": "jobs:\n    a:\n        runs-on: x\n  b:\n    steps:\n      - run: coscc.loop check-branch\n"
    },
    "a workflow that is a directory": {"dir.yml/x": "1\n"},
    "a job line that is no key": {
        "ci.yml": "jobs:\n  <<: *base\n    runs-on: x\n" + workflow().split("jobs:\n")[1]
    },
}


@pytest.mark.parametrize("name", list(WORKFLOWS))
@pytest.mark.parametrize("head", ["Bad/Name", BRANCH])
def test_review_gate_red_check_that_names_the_branch(tmp_path, name, head):
    s = Scene(tmp_path, workflows=WORKFLOWS[name])
    s.unit()
    gh = {
        CHECKS: (
            1,
            checks(("branch-name", "fail"), ("Branch name", "cancel"), ("build", "fail")),
            "",
        ),
        HEAD_NAME: (0, json.dumps({"headRefName": head}), ""),
    }
    s.run("gate", UNIT, "review", "--json", gh=gh)
    s.run("gate", UNIT, "review", gh=gh)


@pytest.mark.parametrize("head_says", ["fails", "empty", "null", "a number", "an object", "a name"])
def test_review_gate_red_check_whose_branch_cannot_be_read(sc, head_says):
    answer = {
        "fails": (1, "", "no such pr"),
        "empty": (0, "", ""),
        "null": (0, "null", ""),
        "a number": (0, "7", ""),
        "an object": (0, '{"headRefName":""}', ""),
        "a name": (0, '{"headRefName":"fix/ok"}', ""),
    }[head_says]
    sc.unit()
    r = sc.run(
        "gate",
        UNIT,
        "review",
        "--json",
        gh={CHECKS: (1, checks(("build", "fail")), ""), HEAD_NAME: answer},
    )
    assert answered(r)["reasons"] == ["ci-red"]


def test_review_gate_red_check_names_the_branch_and_stops_for_a_person(tmp_path):
    s = Scene(tmp_path, workflows=WORKFLOWS["plain"])
    s.unit()
    gh = {
        CHECKS: (1, checks(("branch-name", "fail")), ""),
        HEAD_NAME: (0, json.dumps({"headRefName": "Bad/Name"}), ""),
    }
    assert answered(s.run("gate", UNIT, "review", "--json", gh=gh))["reasons"] == [
        "ci-unfixable",
        "needs-person",
    ]
    s.three(gh)


# --- the ship gate ------------------------------------------------------------------------


def test_ship_gate_open_on_the_reviewed_head(sc):
    sc.unit(sc.passed())
    ship, rev, nxt = sc.three(open_pr(sc.reviewed))
    assert ship["ok"] is True
    assert sc.reviewed in text_of(ship)
    assert f"--match-head-commit {sc.reviewed}" in nxt


def test_ship_gate_open_with_a_title_gh_spaces_out(sc):
    sc.unit(sc.passed())
    ship, _, _ = sc.three(open_pr(sc.reviewed, title=f"  {TITLE} "))
    assert ship["ok"] is True


@pytest.mark.parametrize("title", ["feat(0040): other", None, 5, ""])
def test_ship_gate_when_the_pull_request_carries_another_title(sc, title):
    sc.unit(sc.passed())
    ship, _, nxt = sc.three(open_pr(sc.reviewed, title=title))
    assert ship["ok"] is False
    assert "start ship from the board" in ship["lines"][1]
    assert '"stage":"ship"' in nxt


def test_ship_gate_head_moved_after_the_pass(sc):
    sc.unit(sc.passed())
    moved = sc.commit({"src/b.py": "b\n"}, "fix")
    ship, rev, nxt = sc.three(open_pr(moved))
    assert "changed after the reviewed commit" in text_of(ship)
    assert "src/b.py" in text_of(ship)
    assert rev["ok"] is True
    assert "ship gate" not in text_of(rev)
    assert "stage" in nxt


def test_ship_gate_head_moved_by_the_units_own_files_only(sc):
    sc.unit(sc.passed())
    moved = sc.commit({f".cos/{UNIT}/impl.md": "x\n"}, "own")
    ship, _, _ = sc.three(open_pr(moved))
    assert ship["ok"] is True


def test_ship_gate_gh_pushed_from_elsewhere(sc):
    sc.unit(sc.passed())
    ship, _, _ = sc.three(open_pr("1" * 40))
    assert "is not in this repository" in ship["lines"][1]


@pytest.mark.parametrize("answer", ["fails", "no head", "closed", "merged"])
def test_ship_gate_pull_request_that_cannot_be_read_or_is_not_open(sc, answer):
    sc.unit(sc.passed())
    gh = {
        "fails": {VIEW: (1, "", "HTTP 502")},
        "no head": {VIEW: (0, "{}", "")},
        "closed": {VIEW: (0, view(sc.reviewed, state="CLOSED"), "")},
        "merged": {VIEW: (0, view(sc.reviewed, state="MERGED"), "")},
    }[answer]
    gh = {k: (c, o, e) for k, (c, o, e) in gh.items()}
    ship, _, _ = sc.three(gh)
    assert ship["ok"] is False


def test_ship_gate_no_branch_here_and_a_reviewed_commit_that_is_gone(sc):
    sc.unit(sc.passed())
    git(sc.repo, "checkout", "-q", "main")
    git(sc.repo, "branch", "-D", BRANCH)
    git(sc.repo, "update-ref", "-d", f"refs/remotes/origin/{BRANCH}")
    ship, _, _ = sc.three(open_pr(sc.reviewed))
    assert "no branch" in ship["lines"][1]
    sc.unit(sc.passed(sha="abcdef1234"))
    git(sc.repo, "branch", BRANCH, "main")
    ship, _, _ = sc.three(open_pr(sc.reviewed))
    assert "is not in this repository" in ship["lines"][1]


def test_ship_gate_a_unit_with_no_branch_or_no_pull_request(sc):
    sc.unit(sc.passed(), pr=pr_text(url=None))
    ship, _, _ = sc.three()
    assert "names no pull request" in ship["lines"][1]
    sc.store.unit(
        UNIT,
        {},
        entry(
            {
                f: "accepted"
                for f in ("intent.md", "spec.md", "plan.md", "impl.md", "pr.md", "review.md")
            },
            type=None,
        ),
    )
    sc.three()


def test_ship_gate_without_a_repo(sc):
    sc.unit(sc.passed())
    sc.run("gate", UNIT, "ship", "--json", repo=False)
    sc.run("next", UNIT, repo=False)


# --- the ship gate: what the pass left open -----------------------------------------------

FINDINGS = {
    "no round": ("accepted", []),
    "changes requested": ("accepted", [("changes-requested", [finding(1, "open", "high")])]),
    "open": ("accepted", [("pass", [finding(1, "open", "high"), finding(2, "open", "medium")])]),
    "unreadable label": ("accepted", [("pass", [finding(1, "maybe", "high")])]),
    "answered, no answer": ("accepted", [("pass", [finding(1, "answered", "high")])]),
    "needs person": (
        "accepted",
        [("pass", [finding(1, "needs-person", "high"), finding(2, "claim-rejected", "high")])],
    ),
    "low and open": ("accepted", [("pass", [finding(1, "open", "low", "a nit")])]),
    "low and open, against the standard": (
        "accepted",
        [("pass", [finding(1, "open", "low", "S2 spacing")])],
    ),
    "lowered": (
        "accepted",
        [
            ("changes-requested", [finding(1, "open", "high")]),
            ("pass", [finding(1, "open", "low")]),
        ],
    ),
    "dropped": (
        "accepted",
        [
            ("changes-requested", [finding(1, "open", "high"), finding(2, "open", "low")]),
            ("pass", [finding(2, "open", "low")]),
        ],
    ),
    "renumbered": (
        "accepted",
        [
            ("changes-requested", [finding(1, "fixed abcdef1", "low")]),
            (3, [finding(1, "fixed abcdef1", "low")]),
        ],
    ),
}


@pytest.mark.parametrize("name", list(FINDINGS))
def test_ship_gate_reads_the_last_round(sc, name):
    status, rounds = FINDINGS[name]
    texts = []
    for i, (verdict, fs) in enumerate(rounds, start=1):
        n, verdict = (verdict, "pass") if isinstance(verdict, int) else (i, verdict)
        texts.append(rnd(n, verdict, sc.reviewed, *fs))
    sc.unit(review_text(status, *texts) if texts else review_text(status))
    ship, _, _ = sc.three(open_pr(sc.reviewed))
    assert ship["ok"] is (name in ("low and open",))
    sc.run("gate", UNIT, "ship", gh=open_pr(sc.reviewed))


def test_ship_gate_with_an_answer_that_closes_a_finding(sc):
    answer = (
        "\n## Answers\n### Answer F1\nAnswered by: Phong. Date: 2026-10-01. Via: board.\nKeep it.\n"
    )
    sc.unit(
        review_text(
            "accepted", rnd(1, "pass", sc.reviewed, finding(1, "answered", "high")), tail=answer
        )
    )
    sc.three(open_pr(sc.reviewed))


# --- the ship gate: a rewritten branch ----------------------------------------------------


def test_ship_gate_behind_origin_main(sc):
    sc.unit(sc.passed())
    sc.advance_main()
    ship, _, nxt = sc.three(open_pr(sc.reviewed))
    assert "1 commit(s) behind origin/main" in ship["lines"][1]


@pytest.mark.parametrize("ci", ["green", "red", "pending", "none", "fails"])
def test_ship_gate_clean_rebase(sc, ci):
    sc.unit(sc.passed())
    main = sc.advance_main()
    head = sc.rebase(main)
    gh = {
        "green": {CHECKS: (0, GREEN, "")},
        "red": {CHECKS: (1, checks(("build", "fail")), "")},
        "pending": {CHECKS: (8, checks(("build", "pending")), "")},
        "none": {CHECKS: (0, "[]", "")},
        "fails": {CHECKS: (1, "", "rate limited")},
    }[ci]
    ship, _, nxt = sc.three({**gh, **open_pr(head)})
    assert (ship["ok"], "rebased" in ship) == (ci == "green", ci == "green")


def test_ship_gate_clean_rebase_of_a_branch_with_a_rewritten_local_ref(sc):
    sc.unit(sc.passed())
    main = sc.advance_main()
    head = sc.rebase(main)
    git(sc.repo, "update-ref", f"refs/heads/{BRANCH}", head)
    ship, _, _ = sc.three(open_pr(head))
    assert ship["ok"] is True


@pytest.mark.parametrize(
    "amend",
    [
        {"src/a.py": "a = 2\n"},
        {"src/new.py": "n\n"},
        {"src/a.py": "a = 1\nb = 2\n", "z.txt": "z\n"},
    ],
)
def test_ship_gate_rebase_that_changes_the_patch(sc, amend):
    sc.unit(sc.passed())
    main = sc.advance_main()
    head = sc.rebase(main, amend)
    ship, rev, nxt = sc.three(open_pr(head))
    assert "its patch differs from the reviewed one in" in text_of(ship)


def test_ship_gate_rebase_that_cannot_be_compared(sc):
    sc.unit(sc.passed())
    main = sc.advance_main()
    head = sc.rebase(main)
    git(sc.repo, "branch", "-m", "main", "trunk")
    git(sc.repo, "update-ref", "-d", "refs/remotes/origin/main")
    ship, _, _ = sc.three(open_pr(head))
    assert "could not be compared" in text_of(ship)


def test_ship_gate_reviewed_commit_with_no_common_ancestor(sc):
    git(sc.repo, "checkout", "-q", "--orphan", "island")
    git(sc.repo, "rm", "-rfq", ".")
    orphan = sc.commit({"island.txt": "i\n"}, "island")
    git(sc.repo, "checkout", "-q", BRANCH)
    sc.unit(sc.passed(sha=orphan))
    ship, _, _ = sc.three(open_pr(sc.reviewed))
    assert "could not be compared" in text_of(ship)


# --- the ship gate: a pull request already merged ----------------------------------------


def merged_view(head: str, main: str, **over) -> dict:
    body = {"state": "MERGED", "mergeCommit": {"oid": main}, "mergedAt": "2026-10-01T10:00:00Z"}
    return open_pr(head, **{**body, **over})


def merged_scene(sc):
    main = sc.advance_main()
    sc.unit(sc.passed())
    return main, merged_view(sc.reviewed, main)


def test_ship_gate_already_merged(sc):
    main, gh = merged_scene(sc)
    ship, rev, nxt = sc.three(gh)
    assert main in text_of(ship)
    assert ship["reasons"] == ["recording-ship"]
    assert "do not merge" in nxt


@pytest.mark.parametrize(
    "over",
    [
        {"mergeCommit": None},
        {"mergeCommit": {"oid": "abc"}},
        {"mergeCommit": {"oid": 5}},
        {"mergeCommit": {}},
        {"mergeCommit": "x"},
        {"mergedAt": None},
        {"mergedAt": ""},
        {"mergedAt": 0},
        {"mergeCommit": {"oid": NOBODY}},
        {"mergeCommit": {"oid": "SIDE"}},
    ],
)
def test_ship_gate_merged_with_something_wrong(sc, over):
    main, gh = merged_scene(sc)
    if over.get("mergeCommit", {}) == {"oid": "SIDE"}:
        over = {"mergeCommit": {"oid": sc.reviewed}}
    ship, _, _ = sc.three(merged_view(sc.reviewed, main, **over))
    assert ship["ok"] is False


def test_ship_gate_merged_commit_is_not_on_origin_main(sc):
    main, _ = merged_scene(sc)
    git(sc.repo, "update-ref", "refs/remotes/origin/main", sc.rev(f"{main}~1"))
    ship, _, _ = sc.three(merged_view(sc.reviewed, main))
    assert "is not on origin/main here" in ship["lines"][1]


def test_ship_gate_merged_with_a_draft_ship_md(sc):
    main, gh = merged_scene(sc)
    sc.unit(
        sc.passed(),
        ship="# Ship: x\nAuthor: test. Status: draft. Round: 1.\n\n## What went out\nRefused: boom\n",
    )
    sc.three(gh)


# --- the ship gate: the screens a unit changes --------------------------------------------


def ui_unit(tmp_path: Path, make_review):
    s = ui_scene(tmp_path)
    s.reviewed = s.commit({"src/ui/page.py": "p\n"}, "ui")
    git(s.repo, "update-ref", f"refs/remotes/origin/{BRANCH}", s.reviewed)
    s.unit(make_review(s))
    return s


SCREEN_CASES = {
    "none": lambda s: s.passed(),
    "header does not parse": lambda s: s.passed(
        screens="Looked at it.\n- .screens/a.png — 8×6 — /x — ok\n"
    ),
    "not an agent": lambda s: s.passed(screens=shots(s.reviewed, by="Phong")),
    "wrong standard": lambda s: s.passed(
        screens=shots(s.reviewed, standard=".claude/rules/other.md")
    ),
    "no shot": lambda s: s.passed(screens=shots(s.reviewed).splitlines()[0] + "\n"),
    "current": lambda s: s.passed(screens=shots(s.reviewed)),
    "taken off the branch": lambda s: s.passed(screens=shots(s.rev("main"))),
    "taken at a stranger": lambda s: s.passed(screens=shots("abcdef1234")),
}


@pytest.mark.parametrize("name", list(SCREEN_CASES))
def test_ship_gate_screens(tmp_path, name):
    s = ui_unit(tmp_path, SCREEN_CASES[name])
    ship, rev, nxt = s.three(open_pr(s.reviewed))
    assert ship["ok"] is (name == "current")
    if name != "current":
        assert "ship gate" in text_of(rev)


def test_ship_gate_screens_taken_before_the_screen_changed_again(tmp_path):
    s = ui_scene(tmp_path)
    first = s.commit({"src/ui/page.py": "p\n"}, "ui")
    s.reviewed = s.commit({"src/ui/other.py": "o\n", "src/ui/page.py": "q\n"}, "ui again")
    git(s.repo, "update-ref", f"refs/remotes/origin/{BRANCH}", s.reviewed)
    s.unit(s.passed(screens=shots(first)))
    ship, _, _ = s.three(open_pr(s.reviewed))
    assert "changed after the screenshots" in text_of(ship)


def test_ship_gate_screens_when_git_cannot_say(tmp_path):
    s = ui_unit(tmp_path, lambda s: s.passed(screens=shots(s.reviewed)))
    git(s.repo, "branch", "-m", "main", "trunk")
    git(s.repo, "update-ref", "-d", "refs/remotes/origin/main")
    ship, _, nxt = s.three(open_pr(s.reviewed))
    assert "cannot tell whether" in text_of(ship)


def test_ship_gate_screens_that_fall_through_to_main(tmp_path):
    s = ui_unit(tmp_path, lambda s: s.passed())
    git(s.repo, "update-ref", "-d", "refs/remotes/origin/main")
    s.three(open_pr(s.reviewed))


def test_ship_gate_a_standard_without_globs_asks_git_nothing(tmp_path):
    s = Scene(tmp_path, standard="---\npaths:\n---\n")
    s.unit(s.passed())
    ship, _, _ = s.three(open_pr(s.reviewed))
    assert ship["ok"] is True


def test_ship_gate_two_passes_on_one_head_stop_for_a_person(tmp_path):
    def two(s):
        return review_text(
            "accepted",
            rnd(1, "pass", s.reviewed, finding(1, f"fixed {s.reviewed[:7]}", "high")),
            rnd(2, "pass", s.reviewed, finding(1, f"fixed {s.reviewed[:7]}", "high")),
        )

    s = ui_unit(tmp_path, two)
    ship, _, nxt = s.three(open_pr(s.reviewed))
    assert "both passed on" in nxt
    assert "needs-person" in nxt


def test_ship_gate_two_passes_on_two_heads_go_to_one_more_round(tmp_path):
    s = ui_scene(tmp_path)
    first = s.commit({"src/ui/page.py": "p\n"}, "ui")
    s.reviewed = s.commit({"src/ui/page.py": "q\n"}, "ui again")
    git(s.repo, "update-ref", f"refs/remotes/origin/{BRANCH}", s.reviewed)
    fixed = finding(1, f"fixed {first[:7]}", "high")
    s.unit(review_text("accepted", rnd(1, "pass", first, fixed), rnd(2, "pass", s.reviewed, fixed)))
    s.three(open_pr(s.reviewed))


def test_ship_gate_a_pass_before_the_last_whose_commit_is_gone(tmp_path):
    s = ui_scene(tmp_path)
    s.reviewed = s.commit({"src/ui/page.py": "p\n"}, "ui")
    git(s.repo, "update-ref", f"refs/remotes/origin/{BRANCH}", s.reviewed)
    fixed = finding(1, f"fixed {s.reviewed[:7]}", "high")
    s.unit(
        review_text(
            "accepted", rnd(1, "pass", "abcdef1234", fixed), rnd(2, "pass", s.reviewed, fixed)
        )
    )
    _, _, nxt = s.three(open_pr(s.reviewed))
    assert "is not in this repository;" in nxt


def test_ship_gate_a_second_pass_on_a_head_the_first_cannot_diff(tmp_path):
    s = ui_scene(tmp_path)
    s.reviewed = s.commit({"src/ui/page.py": "p\n"}, "ui")
    git(s.repo, "update-ref", f"refs/remotes/origin/{BRANCH}", s.reviewed)
    fixed = finding(1, f"fixed {s.reviewed[:7]}", "high")
    git(s.repo, "tag", "t0", "main")
    s.unit(
        review_text(
            "accepted", rnd(1, "pass", s.reviewed, fixed), rnd(2, "pass", s.reviewed, fixed)
        )
    )
    s.three(open_pr(s.reviewed))


# --- next ---------------------------------------------------------------------------------


def cr(sc, sha=None) -> str:
    return review_text(
        "changes-requested",
        rnd(1, "changes-requested", sha or sc.reviewed, finding(1, "open", "high")),
    )


def test_next_after_changes_were_asked_and_nothing_reached_the_branch(sc):
    sc.unit(cr(sc), status="changes-requested")
    r = sc.run("next", UNIT, gh=open_pr(sc.reviewed))
    assert json.loads(r.out)["stage"] == "impl"
    assert "nothing outside" in r.out
    own = sc.commit({f".cos/{UNIT}/impl.md": "x\n"}, "own")
    assert "nothing outside" in sc.run("next", UNIT, gh=open_pr(own)).out


@pytest.mark.parametrize("ci", ["green", "red", "pending", "none"])
def test_next_after_a_fix_reached_the_branch(sc, ci):
    sc.unit(cr(sc), status="changes-requested")
    head = sc.commit({"src/a.py": "a = 2\n"}, "fix")
    gh = {
        "green": {CHECKS: (0, GREEN, "")},
        "red": {
            CHECKS: (1, checks(("build", "fail")), ""),
            HEAD_NAME: (0, '{"headRefName":"feat/widget"}', ""),
        },
        "pending": {CHECKS: (8, checks(("build", "pending")), "")},
        "none": {CHECKS: (0, "[]", "")},
    }[ci]
    r = sc.run("next", UNIT, gh={**gh, **open_pr(head)})
    assert json.loads(r.out)["stage"] == {"green": "review", "red": "impl"}.get(ci, "")
    sc.run("gate", UNIT, "review", "--json", gh={**gh, **open_pr(head)})


@pytest.mark.parametrize("how", ["clean", "differs", "no trunk"])
def test_next_after_the_branch_was_rewritten(sc, how):
    sc.unit(cr(sc), status="changes-requested")
    main = sc.advance_main()
    head = sc.rebase(main, {"src/a.py": "a = 2\n"} if how == "differs" else None)
    if how == "no trunk":
        git(sc.repo, "branch", "-m", "main", "trunk")
        git(sc.repo, "update-ref", "-d", "refs/remotes/origin/main")
    r = sc.run("next", UNIT, gh={**open_pr(head), CHECKS: (0, GREEN, "")})
    assert ("was rebased to" in r.out) is (how == "clean")


@pytest.mark.parametrize(
    "what", ["no pr", "no sha", "pr unreadable", "pr closed", "head gone", "sha gone", "no repo"]
)
def test_next_after_changes_were_asked_and_git_cannot_say(sc, what):
    pr = pr_text(url=None) if what == "no pr" else None
    review = cr(sc, "abcdef1234" if what == "sha gone" else None)
    sc.unit(review, status="changes-requested", pr=pr)
    gh = {
        "pr unreadable": {VIEW: (1, "", "HTTP 500")},
        "pr closed": open_pr(sc.reviewed, state="CLOSED"),
        "head gone": open_pr("2" * 40),
    }.get(what, open_pr(sc.reviewed))
    sc.run("next", UNIT, gh=gh, repo=what != "no repo")


def test_next_after_a_pass_whose_ship_is_refused(sc):
    ship = "# Ship: x\nAuthor: test. Status: draft. Round: 1.\n\n## What went out\nRefused: Pull request is not up to date\n"
    sc.unit(sc.passed(), ship=ship)
    sc.three(open_pr(sc.reviewed))
    main = sc.advance_main()
    head = sc.rebase(main)
    sc.three(open_pr(head))
    sc.unit(sc.passed(), ship=ship.replace("Round: 1", "Round: 0"))
    sc.three(open_pr(sc.reviewed))
    sc.unit(sc.passed(), ship=ship.replace("not up to date", "branch protection"))
    sc.three(open_pr(sc.reviewed))


def test_next_after_a_pass_with_the_review_still_to_come(sc):
    sc.unit(None)
    sc.run("next", UNIT, gh={CHECKS: (0, GREEN, "")})
    sc.run("next", UNIT, gh={CHECKS: (1, checks(("build", "fail")), ""), HEAD_NAME: (1, "", "no")})
    sc.run("next", UNIT, repo=False)


def test_the_environment_is_not_the_testers(sc):
    assert not any(k.startswith("COS_") for k in env())
    assert os.path.isdir(sc.repo)


# --- a git that fails where it can --------------------------------------------------------

DIFF_NAMES = ["diff", "--name-only"]


def test_ship_gate_when_git_cannot_diff_what_moved(sc):
    sc.unit(sc.passed())
    sc.shim(
        {
            "match": [*DIFF_NAMES, f"{sc.reviewed}..{sc.reviewed}"],
            "code": 128,
            "err": "fatal: boom\n",
        }
    )
    ship, _, _ = sc.three(open_pr(sc.reviewed))
    assert "git could not diff" in text_of(ship)


@pytest.mark.parametrize(
    "fails", [{"code": 128, "err": "fatal: no\n"}, {"code": 3}, {"code": 0, "out": "many\n"}]
)
def test_ship_gate_when_git_cannot_count_what_it_is_behind(sc, fails):
    sc.unit(sc.passed())
    sc.advance_main()
    sc.shim({"match": ["rev-list", "--count"], **fails})
    ship, _, _ = sc.three(open_pr(sc.reviewed))
    assert "behind origin/main" in text_of(ship)


@pytest.mark.parametrize(
    "rule",
    [
        {"match": ["merge-base"], "not": ["--is-ancestor"], "out": "not a sha\n"},
        {"match": ["--binary"], "code": 128, "err": "fatal: no diff for you\n"},
        {"match": ["--binary"], "code": 5},
        {"match": ["--binary"], "code": 5, "out": "said on stdout\n"},
    ],
)
def test_ship_gate_when_git_cannot_take_the_patch(sc, rule):
    sc.unit(sc.passed())
    head = sc.rebase(sc.advance_main())
    sc.shim(rule)
    ship, _, _ = sc.three(open_pr(head))
    assert "could not be compared" in text_of(ship) or "behind" in text_of(ship)


def test_ship_gate_when_git_cannot_take_the_patch_of_the_head_alone(sc):
    sc.unit(sc.passed())
    head = sc.rebase(sc.advance_main())
    sc.shim({"match": ["--binary", head], "code": 128, "err": "fatal: only this one\n"})
    ship, _, _ = sc.three(open_pr(head))
    assert "could not be compared" in text_of(ship)


def test_ship_gate_when_git_cannot_diff_what_the_screens_followed(tmp_path):
    s = ui_scene(tmp_path)
    first = s.commit({"src/ui/page.py": "p\n"}, "ui")
    s.reviewed = s.commit({"README.md": "y\n"}, "other")
    git(s.repo, "update-ref", f"refs/remotes/origin/{BRANCH}", s.reviewed)
    s.unit(s.passed(screens=shots(first)))
    s.shim({"match": [*DIFF_NAMES, f"{first}..{s.reviewed}"], "code": 128, "err": "fatal: boom\n"})
    ship, _, _ = s.three(open_pr(s.reviewed))
    assert "git could not diff" in text_of(ship)


def test_ship_gate_two_passes_whose_heads_git_cannot_compare(tmp_path):
    s = ui_scene(tmp_path)
    first = s.commit({"src/ui/page.py": "p\n"}, "ui")
    s.reviewed = s.commit({"src/ui/page.py": "q\n"}, "ui again")
    git(s.repo, "update-ref", f"refs/remotes/origin/{BRANCH}", s.reviewed)
    one, two = (
        finding(1, f"fixed {first[:7]}", "high"),
        finding(1, f"fixed {s.reviewed[:7]}", "high"),
    )
    s.unit(review_text("accepted", rnd(1, "pass", first, one), rnd(2, "pass", s.reviewed, two)))
    s.shim({"match": [*DIFF_NAMES, f"{first}..{s.reviewed}"], "code": 128, "err": "fatal: boom\n"})
    _, _, nxt = s.three(open_pr(s.reviewed))
    assert "not known to be one head" in nxt


def test_ship_gate_a_round_the_app_keeps_without_its_commit(sc):
    row = {"n": 1, "verdict": "pass", "reviewed": "", "findings": [], "screens": {}}
    sc.unit(sc.passed(), rows=[row])
    ship, _, _ = sc.three(open_pr(sc.reviewed))
    assert "names no reviewed commit" in text_of(ship)


# The script the loop was before it moved to Python, spelled so no search for it finds this file.
OLD = "cos" + ".mjs"


@pytest.mark.parametrize(
    ("run", "found"),
    [
        ('uv run python -m coscc.loop check-branch "$HEAD_REF"', ["branch-name"]),
        ("python -m coscc.loop   check-branch", ["branch-name"]),
        (f"node .claude/scripts/{OLD} check-branch", []),
        (f"node {OLD} check-branch", []),
        ("uv run python -m coscc.loop check-tag v1.0.0", []),
        ("uv run python -m coscc.loopy check-branch", []),
    ],
)
def test_branch_checks_knows_the_loop_command_and_not_the_old_script(run, found):
    """The job whose `run:` calls `coscc.loop check-branch` is found; the deleted
    script's no longer is."""
    text = workflow(step=f"      - run: {run}")
    assert branch_checks([{"path": ".github/workflows/ci.yml", "text": text}]) == found
