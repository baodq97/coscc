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
