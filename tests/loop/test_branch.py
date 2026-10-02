"""`check-branch`, `check-tag`, `check-version`, `unit-branch` and `pr-text` answer alike (R3)."""

from __future__ import annotations

import json
import subprocess

import pytest

from coscc.loop import js, nullish, stringify
from coscc.loop.branch import declared_versions, version_problem
from tests.loop.conftest import COS_MJS, entry, env, git_repo, header, same

UNIT = "0151_the-loop-is-decided"


# --- check-branch ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "feat/the-loop",
        "fix/x",
        "refactor/a1-b2-c3",
        "revert/" + "a" * 60,
        "main",
        "nothing",
        "wip/x",
        "Feat/x",
        "feat/",
        "feat/a/b",
        "feat/" + "a" * 61,
        "feat/A",
        "feat/a--b",
        "feat/-a",
        "feat/a-",
        "feat/a_b",
        "feat/ả",
        "/x",
        "",
        "feat/x\n",
    ],
)
def test_check_branch_with_a_name(name, tmp_path):
    r = same(["check-branch", name], cwd=tmp_path)
    assert r.code in (0, 1)


def test_check_branch_with_no_name_reads_this_checkout(tmp_path):
    r = same(["check-branch"], cwd=tmp_path)
    assert r.code in (0, 1)
    assert r.out.strip() or r.err.strip()


def test_check_branch_names_the_problem(tmp_path):
    r = same(["check-branch", "feat/a/b"], cwd=tmp_path)
    assert r.code == 1
    assert "second slash" in r.err


def test_check_branch_works_from_anywhere(tmp_path):
    assert same(["check-branch"], cwd=tmp_path).out == same(["check-branch"]).out


def test_check_branch_beside_other_words(tmp_path):
    assert same(["feat/x", "check-branch"], cwd=tmp_path).code == 2
    assert same(["check-branch", "feat/x", "fix/y"], cwd=tmp_path).out == "feat/x\n"


# --- check-tag ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tag",
    [
        "v1.2.3",
        "v0.0.0",
        "v10.20.30",
        "v1.2.3-rc.1",
        "v1.2.3-rc.12",
        "v1.2.3-rc.0",
        "v1.2.3-rc.01",
        "v01.2.3",
        "v1.02.3",
        "v1.2.03",
        "v00.0.0",
        "1.2.3",
        "v1.2",
        "v1.2.3.4",
        "v1.2.3-rc",
        "v1.2.3-beta.1",
        "v1.2.3-rc.1-x",
        "v1.2.3\n",
        "v١.٢.٣",
        "garbage",
        "V1.2.3",
        " v1.2.3",
    ],
)
def test_check_tag(tag, tmp_path):
    r = same(["check-tag", tag], cwd=tmp_path)
    assert r.code in (0, 1)


def test_check_tag_says_release_or_prerelease(tmp_path):
    assert same(["check-tag", "v1.2.3"], cwd=tmp_path).out == "release\n"
    assert same(["check-tag", "v1.2.3-rc.4"], cwd=tmp_path).out == "prerelease\n"


def test_check_tag_with_no_tag_is_misuse(tmp_path):
    r = same(["check-tag"], cwd=tmp_path)
    assert r.code == 2
    assert "usage" in r.err
    assert same(["check-tag", ""], cwd=tmp_path).code == 2


# --- check-version --------------------------------------------------------------------------


def test_check_version_reads_this_checkout(tmp_path):
    r = same(["check-version"], cwd=tmp_path)
    assert r.code in (0, 1)
    assert r.out.strip() or r.err.strip()


def test_check_version_is_the_same_from_a_repository_elsewhere(tmp_path):
    other = git_repo(tmp_path / "elsewhere")
    assert same(["check-version"], cwd=other).out == same(["check-version"]).out


@pytest.mark.parametrize("words", [["check-branch", "--root"], ["check-version", "--root", ""]])
def test_a_misused_root_is_refused_alike(words, tmp_path):
    assert same(words, cwd=tmp_path).code == 2


@pytest.mark.parametrize(
    "argv",
    [
        ["--root", ".", "check-branch", "feat/x"],
        ["check-branch", "feat/x", "--root", "."],
        ["--root", ".", "check-tag", "v1.2.3"],
        ["--root", ".", "check-version"],
        ["--root", "/nonexistent", "check-version"],
    ],
)
def test_root_is_refused_for_the_checks_in_every_form(argv, tmp_path):
    r = same(argv, cwd=tmp_path)
    assert r.code == 2


# --- unit-branch ----------------------------------------------------------------------------


def test_unit_branch_with_no_name_is_misuse(store):
    r = same(store.argv("unit-branch"))
    assert r.code == 2
    assert "usage" in r.err


def test_unit_branch_of_no_such_unit(store):
    r = same(store.argv("unit-branch", "0001_x"))
    assert r.code == 2
    assert "No such work unit" in r.err


def test_unit_branch_of_a_unit_the_app_does_not_know(store):
    store.unit("0001_x", {"intent.md": header("x", "accepted")})
    r = same(store.argv("unit-branch", "0001_x"))
    assert r.code == 1
    assert "declares no Type" in r.err


def test_unit_branch_of_a_name_that_is_no_unit(store):
    store.unit("x", {"intent.md": header("x", "accepted")}, entry())
    assert same(store.argv("unit-branch", "x")).code == 1
    store.unit("0002_Bad", {"intent.md": header("x", "accepted")}, entry())
    assert same(store.argv("unit-branch", "0002_Bad")).code == 1


def test_unit_branch_with_no_type(store):
    store.unit("0001_x", {"intent.md": header("x", "accepted")}, entry(type=None))
    r = same(store.argv("unit-branch", "0001_x"))
    assert r.code == 1
    assert "declares no Type" in r.err


def test_unit_branch_with_a_bad_type(store):
    store.unit("0001_x", {"intent.md": header("x", "accepted")}, entry(type="wip"))
    r = same(store.argv("unit-branch", "0001_x"))
    assert r.code == 1
    assert "is not one of" in r.err


def test_unit_branch_with_a_type_that_is_no_string(store):
    store.unit("0001_x", {"intent.md": header("x", "accepted")}, entry(type=3))
    assert same(store.argv("unit-branch", "0001_x")).code == 1


def test_unit_branch_cuts_a_long_slug(store):
    name = "0001_" + "-".join(["word"] * 20)
    store.unit(name, {"intent.md": header("x", "accepted")}, entry(type="refactor"))
    r = same(store.argv("unit-branch", name))
    assert r.code == 0
    assert r.out.startswith("refactor/")
    assert len(r.out.strip()) <= len("refactor/") + 60


@pytest.mark.parametrize("type_", ["feat", "fix", "docs", "refactor", "revert"])
def test_unit_branch_is_type_and_slug(store, type_):
    store.unit(UNIT, {"intent.md": header("x", "accepted")}, entry(type=type_))
    r = same(store.argv("unit-branch", UNIT))
    assert r.out == f"{type_}/the-loop-is-decided\n"


def test_unit_branch_in_a_workspace_the_state_names(store):
    store.unit(UNIT, {"intent.md": header("x", "accepted")}, entry(type="fix"))
    r = same(store.argv("unit-branch", UNIT))
    assert r.code == 0


def test_unit_branch_with_a_path_in_the_name(store):
    store.unit(UNIT, {"intent.md": header("x", "accepted")}, entry())
    assert same(store.argv("unit-branch", f"nowhere/../{UNIT}")).code in (0, 1, 2)
    assert same(store.argv("unit-branch", "../store/.cos/" + UNIT)).code in (0, 1, 2)


# --- pr-text --------------------------------------------------------------------------------


def _pr(store, body, *, type_="feat", unit=UNIT, intent=True, status="draft"):
    files = {"pr.md": body}
    if intent:
        files["intent.md"] = header("x", "accepted")
    store.unit(unit, files, entry({"pr.md": status}, type=type_))
    return unit


def test_pr_text_with_no_name_is_misuse(store):
    r = same(store.argv("pr-text"))
    assert r.code == 2
    assert "usage" in r.err


@pytest.mark.parametrize(
    "name", ["x", "0001", "0001_X", "../0001_x", "0001_x/", "00001_x", "0001_x\n"]
)
def test_pr_text_with_a_bad_name(store, name):
    r = same(store.argv("pr-text", name))
    assert r.code == 2
    assert "Invalid unit name" in r.err


def test_pr_text_of_no_unit(store):
    r = same(store.argv("pr-text", "0001_x"))
    assert r.code == 1
    assert "No such work unit" in r.err


def test_pr_text_of_a_unit_with_no_pr(store):
    store.unit("0001_x", {"intent.md": header("x", "accepted")}, entry())
    r = same(store.argv("pr-text", "0001_x"))
    assert r.code == 1
    assert "has no pr.md" in r.err


def test_pr_text_of_a_good_title(store):
    _pr(
        store,
        "# PR: feat(0151): port the loop\nAuthor: t. Status: draft.\n\nBody here.\n\nPR: "
        "https://github.com/o/r/pull/12\n",
    )
    r = same(store.argv("pr-text", UNIT))
    assert r.code == 0
    assert '"titleProblem":null' in r.out
    assert '"number"' not in r.out


@pytest.mark.parametrize(
    "title_line",
    [
        "# PR: feat(0151): port the loop",
        "# PR:",
        "# PR:   ",
        "# PR: feat(0151): wip port",
        "# PR: feat(0151): WIP: port",
        "# PR: feat(0151): wipe the slate",
        "# PR: feat(0151): wip-port",
        "# PR: feat(0151): đổi vòng lặp",
        "# PR: feat(0151): café",
        "# PR: feat(0151): café",
        "# PR: fix(0151): port the loop",
        "# PR: feat(0152): port the loop",
        "# PR: wip(0151): port the loop",
        "# PR: feat(151): port the loop",
        "# PR: port the loop",
        "# PR: Feat(0151): port the loop",
        "# PR: feat(0151):  two spaces",
        "# PR: feat(0151): ",
        "#  PR: feat(0151): port",
        "## PR: feat(0151): port",
        "# PR:feat(0151): port the loop",
        "# PR: feat(0151): port the loop   ",
        "# PR: feat(0151): port the loop",
    ],
)
def test_pr_text_titles(store, title_line):
    _pr(store, f"{title_line}\nAuthor: t. Status: draft.\n\nBody.\n")
    assert same(store.argv("pr-text", UNIT)).code == 0


def test_pr_text_with_no_title_line(store):
    _pr(store, "Author: t. Status: draft.\n\nBody only.\n")
    r = same(store.argv("pr-text", UNIT))
    assert "has no title" in r.out


def test_pr_text_with_a_title_that_is_not_the_first_line(store):
    _pr(store, "Preface\n\n# PR: feat(0151): port the loop\nAuthor: t. Status: draft.\n\nBody.\n")
    assert same(store.argv("pr-text", UNIT)).code == 0


def test_pr_text_with_two_title_lines(store):
    _pr(store, "# PR: feat(0151): one\n# PR: feat(0151): two\nStatus: draft.\n\nBody.\n")
    assert same(store.argv("pr-text", UNIT)).code == 0


@pytest.mark.parametrize(
    ("type_", "intent"),
    [("feat", True), ("fix", True), (None, True), ("wip", True), (3, True), ("feat", False)],
)
def test_pr_text_against_the_units_type(store, type_, intent):
    _pr(
        store,
        "# PR: feat(0151): port the loop\nAuthor: t. Status: draft.\n\nBody.\n",
        type_=type_,
        intent=intent,
    )
    assert same(store.argv("pr-text", UNIT)).code == 0


def test_pr_text_of_a_unit_the_app_does_not_know(store):
    store.unit(
        UNIT,
        {
            "pr.md": "# PR: feat(0151): port the loop\nAuthor: t. Status: draft.\n\nB.\n",
            "intent.md": header("x", "accepted"),
        },
    )
    r = same(store.argv("pr-text", UNIT))
    assert r.code == 0
    assert '"status":null' in r.out


@pytest.mark.parametrize("status", ["draft", "accepted", "nonsense", None])
def test_pr_text_carries_the_status_the_app_read(store, status):
    _pr(
        store,
        "# PR: feat(0151): port the loop\nAuthor: t. Status: draft.\n\nB.\n",
        status=status,
    )
    assert same(store.argv("pr-text", UNIT)).code == 0


@pytest.mark.parametrize(
    "block",
    [
        "## Scope of the diff\n\n3 files, +10/-2\n\n- `a.py`\n- `b/c.py`\n- `d.md`\n",
        "## Scope of the diff\n3 files, +10/-2\n- `a.py`\n",
        "## Scope of the diff   \n\n1 files, +1/-0   \n\n- `a.py`  \n\n## Next\n\nMore.\n",
        "## Scope of the diff\n\n3 files, +10/-2\n\n- `a.py`\n- `a.py`\n",
        "## Scope of the diff\n\nmany files\n\n- `a.py`\n",
        "## Scope of the diff\n\n",
        "## Scope of the diff\n",
        "## Scope of the diff\n\n0 files, +0/-0\n",
        "## Scope of the diff\n\n3 files, +10/-2\n\n- `a.py`\n\n- `b.py`\n",
        "## Scope of the diff\n\n3 files, +10/-2\n\n\n- `a.py`\n- b.py\n- `c.py`\n",
        "## Scope of the diff\n\n1 files, +1/-1\n## Other\n- `a.py`\n",
        "## Scope of the diff\n\n1 files, +1/-1\n\n- `a.py`\n\n## Scope of the diff\n\n2 files, +1/-1\n",
    ],
)
def test_pr_text_with_a_scope_block(store, block):
    _pr(store, f"# PR: feat(0151): port the loop\nAuthor: t. Status: draft.\n\nBody.\n\n{block}")
    assert same(store.argv("pr-text", UNIT)).code == 0


def test_pr_text_scope_is_read(store):
    _pr(
        store,
        "# PR: feat(0151): port\nAuthor: t. Status: draft.\n\n"
        "## Scope of the diff\n\n2 files, +3/-4\n\n- `a.py`\n- `b.py`\n",
    )
    r = same(store.argv("pr-text", UNIT))
    assert '"scope":{"files":2,"additions":3,"deletions":4,"paths":["a.py","b.py"]}' in r.out


@pytest.mark.parametrize(
    "body",
    [
        "# PR: feat(0151): port the loop\r\nAuthor: t. Status: draft.\r\n\r\nBody.\r\n",
        "# PR: feat(0151): port the loop\r\nAuthor: t. Status: draft.\r\n\r\n"
        "## Scope of the diff\r\n\r\n2 files, +3/-4\r\n\r\n- `a.py`\r\n- `b.py`\r\n",
        "# PR: feat(0151): port the loop\r\nStatus: draft.\r\n\r\nPR: "
        "https://github.com/o/r/pull/3\r\n",
        "# PR: feat(0151): port the loop\rAuthor: t. Status: draft.\r\rBody.\r",
        "\r\n\r\n# PR: feat(0151): port the loop\r\n",
        "﻿# PR: feat(0151): port the loop\nStatus: draft.\n",
    ],
)
def test_pr_text_with_crlf_lines(store, body):
    _pr(store, body)
    assert same(store.argv("pr-text", UNIT)).code == 0


def test_pr_text_with_a_vietnamese_body(store):
    _pr(
        store,
        "# PR: feat(0151): port the loop\nAuthor: t. Status: draft.\n\n"
        "Chuyển vòng lặp sang Python, đầu ra giống từng byte. Ưu điểm: ổn định.\n\n"
        '- Đã kiểm tra "ngoặc kép" và \\ gạch chéo\n- tab\there\n',
    )
    r = same(store.argv("pr-text", UNIT))
    assert "Chuyển vòng lặp" in r.out


def test_pr_text_with_json_hostile_text(store):
    _pr(
        store,
        "# PR: feat(0151): port the loop\nStatus: draft.\n\n"
        'quote " slash \\ ctrl \x01 \x1f del \x7f nbsp   sep   emoji \U0001f600\n',
    )
    assert same(store.argv("pr-text", UNIT)).code == 0


def test_pr_text_with_a_pr_url(store):
    _pr(
        store,
        "# PR: feat(0151): port\nStatus: draft.\n\nPR: https://github.com/o/r/pull/42\n",
    )
    r = same(store.argv("pr-text", UNIT))
    assert '"url":"https://github.com/o/r/pull/42"' in r.out


def test_pr_text_with_no_status_line(store):
    _pr(store, "# PR: feat(0151): port\n\nBody.\n")
    assert same(store.argv("pr-text", UNIT)).code == 0


def test_pr_text_of_an_empty_file(store):
    _pr(store, "")
    assert same(store.argv("pr-text", UNIT)).code == 0


# --- the version files, read by hand --------------------------------------------------------

PROJECT = '[project]\nname = "app"\nversion = "1.2.3"\n'
LOCK = '[[package]]\nname = "app"\nversion = "1.2.3"\n\n[[package]]\nname = "x"\nversion = "9"\n'
PACKAGE_LOCK = '{"version":"1.2.3","packages":{"":{"version":"1.2.3"}}}'
FILES = [
    {
        "pyproject.toml": PROJECT,
        "package.json": '{"version":"1.2.3"}',
        "uv.lock": LOCK,
        "package-lock.json": PACKAGE_LOCK,
    },
    {
        "pyproject.toml": PROJECT.replace("\n", "\r\n"),
        "package.json": '{"version":1}',
        "uv.lock": LOCK.replace("\n", "\r\n"),
        "package-lock.json": '{"version":[1,null],"packages":{"":{"version":{"a":1}}}}',
    },
    {
        "pyproject.toml": '[project]\rname = "app"\rversion = "2"\r[tool]\rversion = "3"',
        "package.json": "NaN",
        "uv.lock": '[[package]]  \u2028name = "app"\u2028version = "2"',
        "package-lock.json": '{"version":true,"packages":""}',
    },
    {
        "pyproject.toml": '  [project]  \nname\u00a0=\u00a0"app"\nversion="1.0"\n   [x]\nversion="5"',
        "package.json": '\ufeff{"version":"1.0"}',
        "uv.lock": '[[package]]\n  name = "app"\n[[package]]\nname="app"\nversion = "1.0"',
        "package-lock.json": '{"version":1.0,"packages":{"":{"version":-0}}}',
    },
    {"pyproject.toml": "", "package.json": "", "uv.lock": "", "package-lock.json": ""},
    {
        "pyproject.toml": '[project]\nname = "app"\nversion = ""\n',
        "package.json": '"str"',
        "uv.lock": '[[package]]\nname = "app"',
        "package-lock.json": "[1]",
    },
    {
        "pyproject.toml": '[project]\nname = "a\u00e9"\nversion = "1"\n',
        "package.json": '{"version":null}',
        "uv.lock": '[[package]]\nname = "a\u00e9"\nversion = "1"\n',
        "package-lock.json": '{"version":1.5,"packages":{"":{"version":2}}}',
    },
    {
        "pyproject.toml": '[project]\nname = "app"\nversion = "1"',
        "package.json": '{"version":"1"} x',
        "uv.lock": '[[package]]\nname = "app"\nversion = "1"\n[[package]] x\nname = "app"',
        "package-lock.json": '{"packages":{"":null}}',
    },
]


@pytest.mark.parametrize("files", FILES)
def test_the_version_files_are_read_alike(files):
    script = (
        f"import {{declaredVersions, versionProblem}} from {json.dumps(str(COS_MJS))}\n"
        "const c = JSON.parse(process.argv[1])\n"
        "const f = declaredVersions((r) => c[r] ?? '')\n"
        "console.log(JSON.stringify([f, versionProblem(f),"
        " Object.entries(f).map(([p, v]) => `${p}: ${v ?? '(unreadable)'}`)]))\n"
    )
    r = subprocess.run(
        ["node", "--input-type=module", "-e", script, json.dumps(files)],
        capture_output=True,
        text=True,
        env=env(),
        check=True,
    )
    found = declared_versions(lambda rel: files.get(rel, ""))
    lines = [f"{p}: {js(nullish(v, '(unreadable)'))}" for p, v in found.items()]
    assert [json.loads(stringify(found)), version_problem(found), lines] == json.loads(r.stdout)
