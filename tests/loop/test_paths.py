"""`new-path` and `new-idea` print what the goldens hold ("Ghi đường dẫn")."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.loop.conftest import UnitStore, expect


def run(store: UnitStore, *words: str):
    return expect([*words, "--root", str(store.root)])


# --- new-path -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "words",
    [
        ["new-path"],
        ["new-path", ""],
        ["new-path", "Bad"],
        ["new-path", "has_underscore"],
        ["new-path", "double--hyphen"],
        ["new-path", "-lead"],
    ],
)
def test_new_path_refuses_a_slug_alike(store: UnitStore, words):
    r = run(store, *words)
    assert r.code == 2
    assert r.err


def test_new_path_on_an_empty_store(store: UnitStore):
    r = run(store, "new-path", "first")
    assert (r.code, r.out) == (0, ".cos/0001_first\n")


def test_new_path_allows_a_slug_of_sixty(store: UnitStore):
    assert run(store, "new-path", "a" * 60).out == f".cos/0001_{'a' * 60}\n"


def test_new_path_takes_the_highest_number_over_gaps(store: UnitStore):
    for name in ["0001_a", "0004_b", "0002_c", "notes", "12_short", "0009x_y", "0003_Bad"]:
        store.unit(name, {})
    (store.cos / "ideas").mkdir()
    (store.cos / "ideas" / "0099_idea.md").write_text("x")
    (store.cos / "0050_a_file").write_text("x")
    r = run(store, "new-path", "next")
    assert r.out == ".cos/0005_next\n"


def test_new_path_reserves_from_one_dir(store: UnitStore, tmp_path: Path):
    store.unit("0002_a", {})
    other = UnitStore(tmp_path / "other")
    other.unit("0006_b", {})
    r = run(store, "new-path", "x", "--reserve-from", str(other.root))
    assert r.out == ".cos/0007_x\n"


def test_new_path_reserves_from_two_dirs_and_one_without_cos(store: UnitStore, tmp_path: Path):
    a = UnitStore(tmp_path / "a")
    a.unit("0003_a", {})
    b = UnitStore(tmp_path / "b")
    b.unit("0011_b", {})
    b.unit("ideas", {})
    bare = tmp_path / "bare"
    bare.mkdir()
    words = ["new-path", "x", "--reserve-from", str(a.root), "--reserve-from", str(bare)]
    assert run(store, *words).out == ".cos/0004_x\n"
    words = ["--reserve-from", str(b.root), *words, "--reserve-from", str(b.root)]
    assert run(store, *words).out == ".cos/0012_x\n"


def test_new_path_ignores_a_symlink_to_a_dir(store: UnitStore, tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    (store.cos / "0030_link").symlink_to(target, target_is_directory=True)
    store.unit("0002_a", {})
    assert run(store, "new-path", "x").out == ".cos/0003_x\n"


# --- new-idea -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "words",
    [
        ["new-idea"],
        ["new-idea", ""],
        ["new-idea", "Bad"],
    ],
)
def test_new_idea_refuses_a_slug_alike(store: UnitStore, words):
    assert run(store, *words).code == 2


def test_new_idea_with_no_ideas_yet(store: UnitStore):
    assert run(store, "new-idea", "first").out == ".cos/ideas/0001_first.md\n"


def test_new_idea_after_existing_ones(store: UnitStore):
    ideas = store.cos / "ideas"
    ideas.mkdir()
    for name in ["0001_a.md", "0005_b.md", "0003_c.md"]:
        (ideas / name).write_text("x")
    assert run(store, "new-idea", "next").out == ".cos/ideas/0006_next.md\n"


def test_new_idea_ignores_stray_files(store: UnitStore):
    ideas = store.cos / "ideas"
    ideas.mkdir()
    for name in ["0002_a.md", "0090_b.txt", "0091.md", "notes.md", "0092_Bad.md", ".gitkeep"]:
        (ideas / name).write_text("x")
    (ideas / "0095_dir.md").mkdir()
    r = run(store, "new-idea", "next")
    assert r.out == ".cos/ideas/0096_next.md\n"
