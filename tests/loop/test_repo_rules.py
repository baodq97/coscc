"""`screens`, and the `review` and `ship` gates that ask git and `gh`, print what the goldens hold.

Every case runs both versions through `expect()` on a real git repository in tmp and a `gh` that
answers from a table. A case also checks the words or codes it means to reach, so that both
versions agreeing on a wrong branch cannot pass for coverage. The gates and `next` go through
`rules.py`; the probe, the pull request and the screens are `repo_rules.py`'s.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from coscc.loop.model import review_from
from coscc.loop.repo_rules import branch_checks, branch_files, make_probe, screen_passes, ui_changed
from tests.loop.conftest import (
    UnitStore,
    entry,
    env,
    expect,
    fake_gh,
    finding_row,
    git,
    git_repo,
    header,
    pr_row,
    python,
    round_row,
)

UNIT = "0040_widget"
BRANCH = "feat/widget"
TITLE = "feat(0040): widget"
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


def finding(
    n: int,
    label: str,
    severity: str,
    text: str = "the function is wrong",
    fixed_in: str | None = None,
) -> dict:
    return finding_row(f"F{n}", label, severity, text, lines=str(n), fixed_in=fixed_in)


def fixed(n: int, sha: str, severity: str = "high") -> dict:
    return finding(n, "fixed", severity, fixed_in=sha[:7])


rnd = round_row


def shots(taken: str, by: str = "agent session s1", standard: str = STANDARD) -> dict:
    return {
        "taken": taken,
        "standard": standard,
        "by": by,
        "shots": [
            {"path": ".screens/home.png", "size": "800x600", "address": "/home", "result": "ok"}
        ],
    }


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
        rounds: list | None = None,
        status: str = "accepted",
        pr: bool = True,
        ship: dict | None = None,
        granted: int = 0,
    ):
        """The unit's entry: `rounds` are the review's rows (none: no review.md), `pr` whether
        the pull request is recorded, `ship` what ship.md's row says."""
        arts = {f: "accepted" for f in ("intent.md", "spec.md", "plan.md", "impl.md", "pr.md")}
        fields = {"pr_md": pr_row(7)} if pr else {}
        if rounds is not None:
            arts["review.md"] = status
            fields["review_md"] = {"rounds": rounds}
        if ship is not None:
            arts["ship.md"] = "draft"
            fields["ship_md"] = {"merge": ship}
        files = {f: header("Tiêu đề", "accepted", KIND[f]) for f in arts}
        self.store.unit(UNIT, files, entry(arts, rounds_granted=granted, **fields))

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

    def passed(self, *extra: dict, sha: str | None = None, screens: dict | None = None) -> list:
        """A review with one passing round that fixed."""
        sha = sha or self.reviewed
        return [rnd(1, "pass", sha, fixed(1, sha), *extra, screens=screens)]

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
    "empty addresses": '{"head":"abcdef1","dirty":false,"addresses":[]}',
    "dirty": '{"head":"abcdef1","dirty":true,"addresses":["/a"]}',
    "no head": '{"dirty":false,"addresses":["/a"]}',
    "head not a name": '{"head":"; rm -rf","dirty":false,"addresses":["/a"]}',
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


def test_screens_in_a_repository_that_is_not_there(sc):
    sc.unit()
    expect([*sc.store.argv("screens", UNIT), "--repo", str(sc.tmp / "nowhere")], environ=env())


# --- the review gate: CI ------------------------------------------------------------------

CI_READS = {
    "green": (0, GREEN, "", True, []),
    "green with a failing exit": (1, GREEN, "", True, []),
    "pending": (8, checks(("build", "pending")), "", False, ["ci-pending"]),
    "none": (0, "[]", "", False, ["gate-closed"]),
    "an error": (1, "", "HTTP 404: Not Found\n", False, ["gate-closed"]),
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
    sc.unit(pr=False)
    assert "no pull request is recorded" in sc.run("gate", UNIT, "review", "--json").out


def test_review_gate_in_a_repository_that_is_not_there(sc):
    sc.unit()
    argv = [*sc.store.argv("gate", UNIT, "review", "--json"), "--repo", str(sc.tmp / "nowhere")]
    r = expect(argv, environ=fake_gh(sc.bin, {CHECKS: (0, GREEN, "")}))
    assert "spawnSync gh ENOENT" in r.out


def three_rounds(sc, verdict="changes-requested") -> list:
    return [rnd(n, verdict, sc.reviewed, finding(1, "open", "high")) for n in (1, 2, 3)]


@pytest.mark.parametrize("limit", [None, "5", "2"])
def test_review_gate_out_of_rounds(sc, limit):
    sc.unit(three_rounds(sc), status="changes-requested")
    extra = {} if limit is None else {"COS_REVIEW_ROUNDS": limit}
    r = sc.run("gate", UNIT, "review", "--json", gh={CHECKS: (0, GREEN, "")}, **extra)
    assert answered(r)["ok"] is (limit == "5")


def test_review_gate_out_of_rounds_and_granted_more(sc):
    sc.unit(three_rounds(sc), status="changes-requested", granted=2)
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
    "a block run": {
        "ci.yml": workflow(
            step="      - run: |\n          echo hi\n          uv run python -m coscc.loop check-branch"
        )
    },
    "a comment only": {
        "ci.yml": workflow(
            step="      # - run: python -m coscc.loop check-branch\n      - run: echo hi"
        )
    },
    "another script": {"ci.yml": workflow(step="      - run: python -m coscc.loop check-tag")},
    "crlf": {"ci.yml": workflow().replace("\n", "\r\n")},
    "no jobs": {"ci.yml": "name: ci\non: [push]\n"},
    "two files": {"a.yml": "name: a\n", "b.yaml": workflow(), "c.txt": workflow(job="never")},
    "a second job": {"ci.yml": workflow() + "  other:\n    steps:\n      - run: echo hi\n"},
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


@pytest.mark.parametrize("head_says", ["fails", "empty", "a name"])
def test_review_gate_red_check_whose_branch_cannot_be_read(sc, head_says):
    answer = {
        "fails": (1, "", "no such pr"),
        "empty": (0, "", ""),
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
    sc.unit(sc.passed(), pr=False)
    ship, _, _ = sc.three()
    assert "no pull request is recorded" in ship["lines"][1]
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
    "answered, no answer": ("accepted", [("pass", [finding(1, "answered", "high")])]),
    "needs person": (
        "accepted",
        [("pass", [finding(1, "needs-person", "high"), finding(2, "claim-rejected", "high")])],
    ),
    "low and open": ("accepted", [("pass", [finding(1, "open", "low", "a nit")])]),
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
            ("changes-requested", [finding(1, "fixed", "low", fixed_in="abcdef1")]),
            (3, [finding(1, "fixed", "low", fixed_in="abcdef1")]),
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
    sc.unit(texts, status=status)
    ship, _, _ = sc.three(open_pr(sc.reviewed))
    assert ship["ok"] is (name in ("low and open",))
    sc.run("gate", UNIT, "ship", gh=open_pr(sc.reviewed))


def test_ship_gate_with_an_answer_that_closes_a_finding(sc):
    sc.unit([rnd(1, "pass", sc.reviewed, finding(1, "answered", "high"))])
    known = sc.store.units[f"ws/{UNIT}"]
    known["answers"] = [
        {
            "artifact": "review.md",
            "n": None,
            "id": "F1",
            "text": "Keep it.",
            "by": "Phong",
            "date": "2026-10-01",
            "via": "board",
        }
    ]
    sc.three(open_pr(sc.reviewed))


# --- the ship gate: a rewritten branch ----------------------------------------------------


def test_ship_gate_behind_origin_main(sc):
    sc.unit(sc.passed())
    sc.advance_main()
    ship, _, nxt = sc.three(open_pr(sc.reviewed))
    assert "1 commit(s) behind origin/main" in ship["lines"][1]


@pytest.mark.parametrize("ci", ["green", "red", "fails"])
def test_ship_gate_clean_rebase(sc, ci):
    sc.unit(sc.passed())
    main = sc.advance_main()
    head = sc.rebase(main)
    gh = {
        "green": {CHECKS: (0, GREEN, "")},
        "red": {CHECKS: (1, checks(("build", "fail")), "")},
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
    sc.unit(sc.passed(), ship={"round": 1, "refused": "boom"})
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
    "not an agent": lambda s: s.passed(screens=shots(s.reviewed, by="Phong")),
    "wrong standard": lambda s: s.passed(
        screens=shots(s.reviewed, standard=".claude/rules/other.md")
    ),
    "no shot": lambda s: s.passed(screens={**shots(s.reviewed), "shots": []}),
    "current": lambda s: s.passed(screens=shots(s.reviewed)),
    "taken off the branch": lambda s: s.passed(screens=shots(s.rev("main"))),
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


def test_ship_gate_two_passes_on_one_head_stop_for_a_person(tmp_path):
    def two(s):
        return [rnd(n, "pass", s.reviewed, fixed(1, s.reviewed)) for n in (1, 2)]

    s = ui_unit(tmp_path, two)
    ship, _, nxt = s.three(open_pr(s.reviewed))
    assert "both passed on" in nxt
    assert "needs-person" in nxt


def test_ship_gate_two_passes_on_two_heads_go_to_one_more_round(tmp_path):
    s = ui_scene(tmp_path)
    first = s.commit({"src/ui/page.py": "p\n"}, "ui")
    s.reviewed = s.commit({"src/ui/page.py": "q\n"}, "ui again")
    git(s.repo, "update-ref", f"refs/remotes/origin/{BRANCH}", s.reviewed)
    fix = fixed(1, first)
    s.unit([rnd(1, "pass", first, fix), rnd(2, "pass", s.reviewed, fix)])
    s.three(open_pr(s.reviewed))


def test_ship_gate_a_pass_before_the_last_whose_commit_is_gone(tmp_path):
    s = ui_scene(tmp_path)
    s.reviewed = s.commit({"src/ui/page.py": "p\n"}, "ui")
    git(s.repo, "update-ref", f"refs/remotes/origin/{BRANCH}", s.reviewed)
    fix = fixed(1, s.reviewed)
    s.unit([rnd(1, "pass", "abcdef1234", fix), rnd(2, "pass", s.reviewed, fix)])
    _, _, nxt = s.three(open_pr(s.reviewed))
    assert "is not in this repository;" in nxt


# --- next ---------------------------------------------------------------------------------


def cr(sc, sha=None) -> list:
    return [rnd(1, "changes-requested", sha or sc.reviewed, finding(1, "open", "high"))]


def test_next_after_changes_were_asked_and_nothing_reached_the_branch(sc):
    sc.unit(cr(sc), status="changes-requested")
    r = sc.run("next", UNIT, gh=open_pr(sc.reviewed))
    assert json.loads(r.out)["stage"] == "impl"
    assert "nothing outside" in r.out
    own = sc.commit({f".cos/{UNIT}/impl.md": "x\n"}, "own")
    assert "nothing outside" in sc.run("next", UNIT, gh=open_pr(own)).out


@pytest.mark.parametrize("ci", ["green", "red", "pending"])
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


@pytest.mark.parametrize("what", ["no pr", "pr unreadable", "head gone", "no repo"])
def test_next_after_changes_were_asked_and_git_cannot_say(sc, what):
    review = cr(sc, "abcdef1234" if what == "sha gone" else None)
    sc.unit(review, status="changes-requested", pr=what != "no pr")
    gh = {
        "pr unreadable": {VIEW: (1, "", "HTTP 500")},
        "pr closed": open_pr(sc.reviewed, state="CLOSED"),
        "head gone": open_pr("2" * 40),
    }.get(what, open_pr(sc.reviewed))
    sc.run("next", UNIT, gh=gh, repo=what != "no repo")


def test_next_after_a_pass_whose_ship_is_refused(sc):
    ship = {"round": 1, "refused": "Pull request is not up to date"}
    sc.unit(sc.passed(), ship=ship)
    sc.three(open_pr(sc.reviewed))
    main = sc.advance_main()
    head = sc.rebase(main)
    sc.three(open_pr(head))
    sc.unit(sc.passed(), ship={**ship, "round": 0})
    sc.three(open_pr(sc.reviewed))
    sc.unit(sc.passed(), ship={**ship, "refused": "branch protection"})
    sc.three(open_pr(sc.reviewed))


def test_next_after_a_pass_with_the_review_still_to_come(sc):
    sc.unit(None)
    sc.run("next", UNIT, gh={CHECKS: (0, GREEN, "")})
    sc.run("next", UNIT, gh={CHECKS: (1, checks(("build", "fail")), ""), HEAD_NAME: (1, "", "no")})
    sc.run("next", UNIT, repo=False)


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


@pytest.mark.parametrize("fails", [{"code": 128, "err": "fatal: no\n"}])
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
    ],
)
def test_ship_gate_when_git_cannot_take_the_patch(sc, rule):
    sc.unit(sc.passed())
    head = sc.rebase(sc.advance_main())
    sc.shim(rule)
    ship, _, _ = sc.three(open_pr(head))
    assert "could not be compared" in text_of(ship) or "behind" in text_of(ship)


def test_ship_gate_two_passes_whose_heads_git_cannot_compare(tmp_path):
    s = ui_scene(tmp_path)
    first = s.commit({"src/ui/page.py": "p\n"}, "ui")
    s.reviewed = s.commit({"src/ui/page.py": "q\n"}, "ui again")
    git(s.repo, "update-ref", f"refs/remotes/origin/{BRANCH}", s.reviewed)
    s.unit(
        [rnd(1, "pass", first, fixed(1, first)), rnd(2, "pass", s.reviewed, fixed(1, s.reviewed))]
    )
    s.shim({"match": [*DIFF_NAMES, f"{first}..{s.reviewed}"], "code": 128, "err": "fatal: boom\n"})
    _, _, nxt = s.three(open_pr(s.reviewed))
    assert "not known to be one head" in nxt


def test_ship_gate_a_round_the_app_keeps_without_its_commit(sc):
    sc.unit([rnd(1, "pass", "")])
    ship, _, _ = sc.three(open_pr(sc.reviewed))
    assert "names no reviewed commit" in text_of(ship)


@pytest.mark.parametrize(
    ("run", "found"),
    [
        ('uv run python -m coscc.loop check-branch "$HEAD_REF"', ["branch-name"]),
        ("python -m coscc.loop   check-branch", ["branch-name"]),
        ("uv run python -m coscc.loop check-tag v1.0.0", []),
        ("uv run python -m coscc.loopy check-branch", []),
    ],
)
def test_branch_checks_knows_the_loop_command_and_not_the_old_script(run, found):
    """The job whose `run:` calls `coscc.loop check-branch` is found; the deleted
    script's no longer is."""
    text = workflow(step=f"      - run: {run}")
    assert branch_checks([{"path": ".github/workflows/ci.yml", "text": text}]) == found


def test_review_gate_when_gh_cannot_start(tmp_path):
    s = Scene(tmp_path)
    s.unit()
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "node").symlink_to(shutil.which("node") or "node")
    r = s.run("gate", UNIT, "review", PATH=str(bare))
    assert "spawnSync gh ENOENT" in r.out + r.err


def test_ship_gate_a_second_pass_on_a_head_the_first_cannot_diff(tmp_path):
    s = ui_scene(tmp_path)
    s.reviewed = s.commit({"src/ui/page.py": "p\n"}, "ui")
    git(s.repo, "update-ref", f"refs/remotes/origin/{BRANCH}", s.reviewed)
    git(s.repo, "tag", "t0", "main")
    s.unit([rnd(n, "pass", s.reviewed, fixed(1, s.reviewed)) for n in (1, 2)])
    s.three(open_pr(s.reviewed))


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


# --- an S<n> finding that does not block ---------------------------------------------------

# The narrowed standard: what a patch of the lib files alone must not count as a screen.
NARROW_STANDARD = (
    "---\npaths:\n"
    '  - "ui/src/**/*.tsx"\n'
    '  - "ui/src/styles.css"\n'
    '  - "ui/index.html"\n'
    '  - "coscc/http/auth.py"\n'
    '  - "coscc/features/*/ui/**/*.tsx"\n'
    '  - "ui/src/lib/format.ts"\n'
    "---\n# UI standard\n"
)
X = "ui/src/x.tsx"


def standard_row(
    n: int, label: str = "open", path: str = X, severity: str = "high", fixed_in=None
) -> dict:
    return finding_row(
        f"F{n}",
        label,
        severity,
        "shows the sha",
        path=path,
        lines="7",
        rule="S2",
        fixed_in=fixed_in,
    )


def reviewing(*rounds: dict) -> dict:
    return {"name": UNIT, "artifacts": {"review.md": {"review": review_from(list(rounds))}}}


def passes(s: Scene, *rounds: dict) -> list:
    return screen_passes(reviewing(*rounds), make_probe(str(s.repo)))


def asks(s: Scene, *words: str, gh: dict | None = None) -> dict:
    """What the command prints as JSON, from the real repository and a `gh` that answers `gh`."""
    argv = s.store.argv(*words, "--repo", str(s.repo))
    return json.loads(python(argv, environ=fake_gh(s.bin, gh or {})).out)


def test_ui_changed_counts_no_screen_in_a_patch_of_lib_files_the_standard_leaves_out(tmp_path):
    s = Scene(tmp_path, standard=NARROW_STANDARD)
    head = s.commit(
        {
            "ui/src/lib/boards.ts": "b\n",
            "ui/src/lib/stream.ts": "s\n",
            "ui/src/lib/lib.test.ts": "t\n",
        },
        "lib",
    )
    probe = make_probe(str(s.repo))
    assert ui_changed({"name": UNIT}, probe, head)["changed"] == []
    head = s.commit({"ui/src/lib/format.ts": "f\n"}, "format")
    assert ui_changed({"name": UNIT}, probe, head)["changed"] == ["ui/src/lib/format.ts"]


def test_screen_passes_a_finding_on_a_file_the_patch_leaves_alone(sc):
    r = rnd(1, "changes-requested", sc.reviewed, standard_row(1), screens=shots(sc.reviewed))
    assert passes(sc, r) == [
        {
            "id": "F1",
            "criterion": "S2",
            "path": X,
            "lines": "7",
            "text": "shows the sha",
            "why": "screen-untouched",
        }
    ]


def test_screen_passes_nothing_that_is_high_or_low_on_a_file_the_patch_changes(sc):
    for severity in ("high", "low"):
        f = standard_row(1, path="src/a.py", severity=severity)
        assert passes(sc, rnd(1, "pass", sc.reviewed, f, screens=shots(sc.reviewed))) == []


def test_screen_passes_a_finding_raised_in_a_round_without_screens(sc):
    r = rnd(1, "changes-requested", sc.reviewed, standard_row(1, path="src/a.py"))
    assert [p["why"] for p in passes(sc, r)] == ["screen-late"]


def test_screen_passes_a_finding_the_earlier_screens_already_showed(sc):
    first = sc.commit({X: "1\n"}, "x")
    second = sc.commit({"README.md": "2\n"}, "readme")
    r1 = rnd(1, "changes-requested", first, fixed(9, first), screens=shots(first))
    r2 = rnd(2, "changes-requested", second, standard_row(1), screens=shots(second))
    assert [p["why"] for p in passes(sc, r1, r2)] == ["screen-late"]
    # The file moved after those screens: this round's are news.
    third = sc.commit({X: "3\n"}, "x again")
    r3 = rnd(2, "changes-requested", third, standard_row(1), screens=shots(third))
    assert passes(sc, r1, r3) == []


def test_screen_passes_nothing_for_a_fixed_or_answered_finding(sc):
    for label in ("fixed", "answered"):
        f = standard_row(1, label, path="ui/src/other.tsx", fixed_in="abcdef1")
        assert passes(sc, rnd(1, "pass", sc.reviewed, f, screens=shots(sc.reviewed))) == []


@pytest.mark.parametrize("path", [".screens/home-800x600.png", ""])
def test_screen_passes_nothing_for_a_finding_that_points_at_no_source_file(sc, path):
    f = standard_row(1, path=path)
    assert passes(sc, rnd(1, "changes-requested", sc.reviewed, f)) == []


def test_screen_passes_nothing_for_a_finding_that_blocked_until_it_is_fixed(sc):
    first = sc.commit({X: "1\n"}, "x")
    git(sc.repo, "rm", "-q", X)
    second = sc.commit({"README.md": "2\n"}, "x gone")
    assert X not in branch_files({"name": UNIT}, make_probe(str(sc.repo)), second)["files"]
    r1 = rnd(1, "changes-requested", first, standard_row(1), screens=shots(first))
    r2 = rnd(2, "changes-requested", second, standard_row(1), screens=shots(second))
    assert passes(sc, r1, r2) == []


def test_screen_passes_nothing_for_a_finding_whose_severity_was_lowered(sc):
    sc.reviewed = sc.commit({X: "1\n"}, "x")
    git(sc.repo, "update-ref", f"refs/remotes/origin/{BRANCH}", sc.reviewed)
    high = rnd(1, "changes-requested", sc.reviewed, standard_row(1), screens=shots(sc.reviewed))
    low = rnd(2, "pass", sc.reviewed, standard_row(1, severity="low"), screens=shots(sc.reviewed))
    assert passes(sc, high, low) == []
    sc.unit([high, low])
    gate = asks(sc, "gate", UNIT, "ship", "--json", gh=open_pr(sc.reviewed))
    assert gate["ok"] is False
    assert "F1 [open]" in text_of(gate)
    assert gate["passed"] == []


def test_ship_gate_opens_when_only_findings_the_screens_rule_lets_through_remain(sc):
    sc.unit([rnd(1, "pass", sc.reviewed, standard_row(1), screens=shots(sc.reviewed))])
    gate = asks(sc, "gate", UNIT, "ship", "--json", gh=open_pr(sc.reviewed))
    assert gate["ok"] is True
    assert gate["passed"] == [
        {
            "id": "F1",
            "criterion": "S2",
            "path": X,
            "lines": "7",
            "text": "shows the sha",
            "why": "screen-untouched",
        }
    ]
    changed = standard_row(1, path="src/a.py")
    sc.unit([rnd(1, "pass", sc.reviewed, changed, screens=shots(sc.reviewed))])
    gate = asks(sc, "gate", UNIT, "ship", "--json", gh=open_pr(sc.reviewed))
    assert gate["ok"] is False
    assert gate["passed"] == []


def test_screen_passes_nothing_without_a_candidate_or_when_git_cannot_say(sc):
    assert passes(sc, rnd(1, "pass", sc.reviewed, fixed(1, sc.reviewed))) == []
    r = rnd(1, "changes-requested", sc.reviewed, standard_row(1), screens=shots(sc.reviewed))
    git(sc.repo, "update-ref", "-d", "refs/remotes/origin/main")
    git(sc.repo, "branch", "-m", "main", "trunk")
    assert passes(sc, r) == []
