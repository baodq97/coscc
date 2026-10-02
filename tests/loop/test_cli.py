"""Every misuse `python -m coscc.loop` refuses with exit 2, as its goldens hold."""

from __future__ import annotations

import pytest

from tests.loop.conftest import at_version, env, expect, git, git_repo, python

STATE_READERS = ["status", "gate", "next", "rerun", "unit-branch", "pr-text", "screens"]


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["nope"],
        ["--root"],
        ["status", "--root", ""],
        ["status", "--peer", "x"],
        ["gate", "--repo"],
        ["status", "--state"],
        ["new-path", "x", "--reserve-from"],
        ["check-branch", "--root", "."],
        ["check-tag", "v1.0.0", "--root", "."],
        ["check-version", "--root", "."],
        ["new-idea", "x", "--reserve-from", "."],
        ["meta", "--repo", "."],
        ["new-path", "x", "--repo", "."],
    ],
)
def test_a_misuse_is_refused_alike(argv, tmp_path):
    assert expect(argv, cwd=tmp_path).code == 2


@pytest.mark.parametrize("cmd", STATE_READERS)
def test_a_deciding_command_without_a_snapshot_is_refused_alike(cmd, tmp_path):
    # No `COS_WORKING_DIR`, so no database stands in for `--state`.
    r = expect([cmd, "0001_x", "--root", str(tmp_path)], cwd=tmp_path)
    assert r.code == 2
    assert "needs the coscc app" in r.err


@pytest.mark.parametrize("raw", ["0", "abc", "-1", "1.5", " "])
def test_a_broken_review_limit_is_refused_alike(raw, tmp_path):
    r = expect(["status", "--root", str(tmp_path)], environ=env(COS_REVIEW_ROUNDS=raw))
    assert r.code == 2


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("missing.json", None),
        ("empty.json", ""),
        ("blank.json", "  \n"),
        ("token.json", "not json"),
        ("longtoken.json", '{"workspace": "ws", "units": nope, "ideas": {}}'),
        ("trailing.json", '{"workspace": "ws", "units": {}}\n x'),
        ("cut.json", '{"workspace": '),
        ("array.json", "[]"),
        ("number.json", "1"),
        ("noworkspace.json", '{"units": {}}'),
        ("nounits.json", '{"workspace": "ws"}'),
        ("nullunits.json", '{"workspace": "ws", "units": null}'),
    ],
)
def test_a_state_that_is_no_snapshot_is_refused_alike(name, text, tmp_path):
    path = tmp_path / name
    if text is not None:
        path.write_text(text)
    r = expect(["status", "--root", str(tmp_path), "--state", str(path)])
    assert r.code == 2


def test_a_snapshot_on_stdin_is_read_and_a_flag_it_does_not_apply_to_refused(tmp_path):
    r = expect(
        ["new-path", "x", "--root", str(tmp_path), "--state", "-"],
        stdin='{"workspace":"w","units":{}}',
    )
    assert r.code == 2
    assert "--state applies only to" in r.err


def test_check_version_reads_the_checkout_of_the_cwd_not_the_installed_package(tmp_path):
    # R8: a checkout outside this tree, at 9.9.9; the package's own checkout is not at 9.9.9.
    repo = git_repo(tmp_path / "elsewhere")
    for name, text in at_version("9.9.9").items():
        (repo / name).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "v")
    (repo / "deep" / "er").mkdir(parents=True)
    r = python(["check-version"], environ=env(), cwd=repo / "deep" / "er")
    assert (r.code, r.out, r.err) == (0, "9.9.9\n", "")


def test_without_root_the_cos_of_the_checkout_of_the_cwd_is_read(tmp_path):
    repo = git_repo(tmp_path / "elsewhere")
    (repo / ".cos" / "0041_taken").mkdir(parents=True)
    (repo / "sub").mkdir()
    r = python(["new-path", "next"], environ=env(), cwd=repo / "sub")
    assert (r.code, r.out) == (0, ".cos/0042_next\n")
