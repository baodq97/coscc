"""Every misuse `cos.mjs` refuses with exit 2 is refused alike by `python -m coscc.loop`."""

from __future__ import annotations

import pytest

from tests.loop.conftest import env, same

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
    assert same(argv, cwd=tmp_path).code == 2


@pytest.mark.parametrize("cmd", STATE_READERS)
def test_a_deciding_command_without_a_snapshot_is_refused_alike(cmd, tmp_path):
    # No `COS_WORKING_DIR`, so no database stands in for `--state`.
    r = same([cmd, "0001_x", "--root", str(tmp_path)], cwd=tmp_path)
    assert r.code == 2
    assert "needs the coscc app" in r.err


@pytest.mark.parametrize("raw", ["0", "abc", "-1", "1.5", " "])
def test_a_broken_review_limit_is_refused_alike(raw, tmp_path):
    r = same(["status", "--root", str(tmp_path)], environ=env(COS_REVIEW_ROUNDS=raw))
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
    r = same(["status", "--root", str(tmp_path), "--state", str(path)])
    assert r.code == 2


def test_a_snapshot_on_stdin_is_read_and_a_flag_it_does_not_apply_to_refused(tmp_path):
    r = same(
        ["new-path", "x", "--root", str(tmp_path), "--state", "-"],
        stdin='{"workspace":"w","units":{}}',
    )
    assert r.code == 2
    assert "--state applies only to" in r.err
