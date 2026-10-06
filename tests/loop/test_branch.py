"""`check-branch`, `check-tag`, `check-version`, `unit-branch` and `pr-text` answer alike."""

from __future__ import annotations


import pytest

from tests.loop.conftest import entry, env, expect, git, git_repo, header, python

UNIT = "0151_the-loop-is-decided"


# --- check-branch ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "feat/the-loop",
        "fix/x",
        "revert/" + "a" * 60,
        "main",
        "wip/x",
        "Feat/x",
        "feat/",
        "feat/a/b",
        "feat/" + "a" * 61,
        "feat/A",
        "feat/a--b",
        "feat/a_b",
        "",
        "feat/x\n",
    ],
)
def test_check_branch_with_a_name(name, tmp_path):
    r = expect(["check-branch", name], cwd=tmp_path)
    assert r.code in (0, 1)


def test_check_branch_with_no_name_reads_the_checkout_of_the_cwd(tmp_path):
    repo = git_repo(tmp_path / "repo")
    git(repo, "checkout", "-q", "-b", "feat/x")
    (repo / "sub").mkdir()
    r = python(["check-branch"], environ=env(), cwd=repo / "sub")
    assert (r.code, r.out, r.err) == (0, "feat/x\n", "")


def test_check_branch_outside_a_checkout_and_with_no_name_is_refused(tmp_path):
    r = python(["check-branch"], environ=env(), cwd=tmp_path)
    assert (r.code, r.err) == (2, "not a git checkout, and no branch name was given\n")


# --- check-tag ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tag",
    [
        "v1.2.3",
        "v1.2.3-rc.1",
        "v1.2.3-rc.01",
        "v01.2.3",
        "1.2.3",
        "v1.2",
        "v1.2.3-rc",
        "v1.2.3-beta.1",
        "v1.2.3\n",
        "v١.٢.٣",
        "garbage",
        " v1.2.3",
    ],
)
def test_check_tag(tag, tmp_path):
    r = expect(["check-tag", tag], cwd=tmp_path)
    assert r.code in (0, 1)


def test_check_tag_says_release_or_prerelease(tmp_path):
    assert expect(["check-tag", "v1.2.3"], cwd=tmp_path).out == "release\n"
    assert expect(["check-tag", "v1.2.3-rc.4"], cwd=tmp_path).out == "prerelease\n"


# --- check-version --------------------------------------------------------------------------


def test_check_version_outside_a_checkout_reads_the_cwd(tmp_path):
    r = python(["check-version"], environ=env(), cwd=tmp_path)
    assert (r.code, r.out) == (1, "")
    assert r.err.startswith("the version is not in step: pyproject.toml declares no version\n")


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
    r = expect(argv, cwd=tmp_path)
    assert r.code == 2


# --- unit-branch ----------------------------------------------------------------------------


def test_unit_branch_of_no_such_unit(store):
    r = expect(store.argv("unit-branch", "0001_x"))
    assert r.code == 2
    assert "No such work unit" in r.err


def test_unit_branch_of_a_unit_the_app_does_not_know(store):
    store.unit("0001_x", {"intent.md": header("x", "accepted")})
    r = expect(store.argv("unit-branch", "0001_x"))
    assert r.code == 1
    assert "handed back no type" in r.err


def test_unit_branch_of_a_name_that_is_no_unit(store):
    store.unit("x", {"intent.md": header("x", "accepted")}, entry())
    assert expect(store.argv("unit-branch", "x")).code == 1
    store.unit("0002_Bad", {"intent.md": header("x", "accepted")}, entry())
    assert expect(store.argv("unit-branch", "0002_Bad")).code == 1


def test_unit_branch_with_no_type(store):
    store.unit("0001_x", {"intent.md": header("x", "accepted")}, entry(type=None))
    r = expect(store.argv("unit-branch", "0001_x"))
    assert r.code == 1
    assert "handed back no type" in r.err


def test_unit_branch_with_a_bad_type(store):
    store.unit("0001_x", {"intent.md": header("x", "accepted")}, entry(type="wip"))
    r = expect(store.argv("unit-branch", "0001_x"))
    assert r.code == 1
    assert "is not one of" in r.err


def test_unit_branch_cuts_a_long_slug(store):
    name = "0001_" + "-".join(["word"] * 20)
    store.unit(name, {"intent.md": header("x", "accepted")}, entry(type="refactor"))
    r = expect(store.argv("unit-branch", name))
    assert r.code == 0
    assert r.out.startswith("refactor/")
    assert len(r.out.strip()) <= len("refactor/") + 60


@pytest.mark.parametrize("type_", ["feat", "fix"])
def test_unit_branch_is_type_and_slug(store, type_):
    store.unit(UNIT, {"intent.md": header("x", "accepted")}, entry(type=type_))
    r = expect(store.argv("unit-branch", UNIT))
    assert r.out == f"{type_}/the-loop-is-decided\n"


# --- pr-text --------------------------------------------------------------------------------


def _pr(store, body, *, type_="feat", unit=UNIT, intent=True, status="draft"):
    files = {"pr.md": body}
    if intent:
        files["intent.md"] = header("x", "accepted")
    store.unit(unit, files, entry({"pr.md": status}, type=type_))
    return unit


@pytest.mark.parametrize("name", ["x", "../0001_x", "0001_x\n"])
def test_pr_text_with_a_bad_name(store, name):
    r = expect(store.argv("pr-text", name))
    assert r.code == 2
    assert "Invalid unit name" in r.err


def test_pr_text_of_no_unit(store):
    r = expect(store.argv("pr-text", "0001_x"))
    assert r.code == 1
    assert "No such work unit" in r.err


def test_pr_text_of_a_unit_with_no_pr(store):
    store.unit("0001_x", {"intent.md": header("x", "accepted")}, entry())
    r = expect(store.argv("pr-text", "0001_x"))
    assert r.code == 1
    assert "has no pr.md" in r.err


def test_pr_text_of_a_good_title(store):
    _pr(
        store,
        "# PR: feat(0151): port the loop\nAuthor: t. Status: draft.\n\nBody here.\n\nPR: "
        "https://github.com/o/r/pull/12\n",
    )
    r = expect(store.argv("pr-text", UNIT))
    assert r.code == 0
    assert '"titleProblem":null' in r.out
    assert '"number"' not in r.out


@pytest.mark.parametrize(
    "title_line",
    [
        "# PR: feat(0151): port the loop",
        "# PR:",
        "# PR: feat(0151): wip port",
        "# PR: feat(0151): WIP: port",
        "# PR: feat(0151): wipe the slate",
        "# PR: feat(0151): café",
        "# PR: fix(0151): port the loop",
        "# PR: feat(0152): port the loop",
        "# PR: wip(0151): port the loop",
        "# PR: feat(151): port the loop",
        "# PR: port the loop",
        "# PR: feat(0151): ",
    ],
)
def test_pr_text_titles(store, title_line):
    _pr(store, f"{title_line}\nAuthor: t. Status: draft.\n\nBody.\n")
    assert expect(store.argv("pr-text", UNIT)).code == 0


def test_pr_text_with_no_title_line(store):
    _pr(store, "Author: t. Status: draft.\n\nBody only.\n")
    r = expect(store.argv("pr-text", UNIT))
    assert "has no title" in r.out


@pytest.mark.parametrize(
    ("type_", "intent"), [("feat", True), ("fix", True), (None, True), ("wip", True)]
)
def test_pr_text_against_the_units_type(store, type_, intent):
    _pr(
        store,
        "# PR: feat(0151): port the loop\nAuthor: t. Status: draft.\n\nBody.\n",
        type_=type_,
        intent=intent,
    )
    assert expect(store.argv("pr-text", UNIT)).code == 0


@pytest.mark.parametrize(
    "block",
    [
        "## Scope of the diff\n\n3 files, +10/-2\n\n- `a.py`\n- `b/c.py`\n- `d.md`\n",
        "## Scope of the diff\n3 files, +10/-2\n- `a.py`\n",
        "## Scope of the diff   \n\n1 files, +1/-0   \n\n- `a.py`  \n\n## Next\n\nMore.\n",
        "## Scope of the diff\n\n3 files, +10/-2\n\n- `a.py`\n- `a.py`\n",
        "## Scope of the diff\n\nmany files\n\n- `a.py`\n",
        "## Scope of the diff\n\n",
    ],
)
def test_pr_text_with_a_scope_block(store, block):
    _pr(store, f"# PR: feat(0151): port the loop\nAuthor: t. Status: draft.\n\nBody.\n\n{block}")
    assert expect(store.argv("pr-text", UNIT)).code == 0


def test_pr_text_scope_is_read(store):
    _pr(
        store,
        "# PR: feat(0151): port\nAuthor: t. Status: draft.\n\n"
        "## Scope of the diff\n\n2 files, +3/-4\n\n- `a.py`\n- `b.py`\n",
    )
    r = expect(store.argv("pr-text", UNIT))
    assert '"scope":{"files":2,"additions":3,"deletions":4,"paths":["a.py","b.py"]}' in r.out


@pytest.mark.parametrize(
    "body",
    [
        "# PR: feat(0151): port the loop\r\nAuthor: t. Status: draft.\r\n\r\nBody.\r\n",
        "# PR: feat(0151): port the loop\r\nAuthor: t. Status: draft.\r\n\r\n"
        "## Scope of the diff\r\n\r\n2 files, +3/-4\r\n\r\n- `a.py`\r\n- `b.py`\r\n",
        "# PR: feat(0151): port the loop\r\nStatus: draft.\r\n\r\nPR: "
        "https://github.com/o/r/pull/3\r\n",
    ],
)
def test_pr_text_with_crlf_lines(store, body):
    _pr(store, body)
    assert expect(store.argv("pr-text", UNIT)).code == 0


def test_pr_text_with_a_pr_url(store):
    _pr(
        store,
        "# PR: feat(0151): port\nStatus: draft.\n\nPR: https://github.com/o/r/pull/42\n",
    )
    r = expect(store.argv("pr-text", UNIT))
    assert '"url":"https://github.com/o/r/pull/42"' in r.out
