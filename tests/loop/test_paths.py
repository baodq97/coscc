"""`new-path`, `new-idea` and `meta` print what the goldens hold (, "Ghi đường dẫn")."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.loop.conftest import UnitStore, header, expect

QUESTIONS = """## Open questions
1. Which database should hold the units?
2. A line that asks nothing
3. Does it need a migration? And a rollback?
   Because the old rows stay.
"""

ANSWERS = """## Answers
### Câu 1
Answered by: Phong. Date: 2026-09-01. Via: product.
Dùng SQLite, vì nó có sẵn.

### F1
Answered by: Bao Do. Date: 2026-09-02. Via: cli.
Fixed in the next round.

### Paused
Decided by: Phong. Date: 2026-09-03. Via: product.
Waiting on the vendor.

### Resumed
Decided by: Phong. Date: 2026-09-04. Via: product.
The vendor answered.

### Dropped
Not a block we can read.
"""


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


# --- meta ---------------------------------------------------------------------------------

INTENT = (
    header("Dùng SQLite", "accepted", extra="Type: Feat. Idea: ws/ideas/0001_x.md. Repo: coscc.\n")
    + "Depends on: ws/0001_a, other/0002_b.\n"
    + "\n## Why\nVì tiếng Việt có dấu: đạt, trượt.\n\n"
    + QUESTIONS
    + "\n"
    + ANSWERS
)


def test_meta_of_an_empty_store(store: UnitStore):
    r = run(store, "meta")
    assert json.loads(r.out) == {"units": {}, "ideas": None}


def _whole_store(store: UnitStore) -> None:
    store.unit(
        "0001_first",
        {
            "intent.md": INTENT,
            "spec.md": header("Spec", "Accepted", "Spec") + QUESTIONS,
            "plan.md": header("Plan", "weird-word", "Plan"),
            "ship.md": "no status here\n",
            "notes.md": "not an artifact\n",
        },
    )
    store.unit("0002_second", {"impl.md": header("Impl", "done", "Impl")})
    store.unit("0003_empty", {})
    store.unit("odd name", {"idea.md": header("Odd", "draft", "Idea")})
    store.unit("Zed", {"review.md": header("R", "changes-requested", "Review")})
    store.unit("123", {})
    store.unit("20", {})
    ideas = store.cos / "ideas"
    ideas.mkdir()
    (ideas / "0001_good.md").write_text(
        "# Idea: Tiếng Việt  \nAuthor: x. Status: accepted.\n\n## Units\n"
        "- ws/0001_a\n- ws/0002_b. Depends on: ws/0001_a, ws/0003_c.\n"
        "- ws/0004_d. Depends on: ws/0001_a.\n\n- not a unit\n- ws/0005_e. Depends on: nope\n"
        "- other/0006_f.\n"
    )
    (ideas / "0002_nostatus.md").write_text("# Idea: No status\n\nBody only\n")
    (ideas / "0003_crlf.md").write_text(
        "# Idea: Crlf\r\nStatus: draft.\r\n## Units\r\n- ws/0001_a\r\n"
    )
    (ideas / "0004_bad.md").write_text("# Idea:\n## Units\n- \n")
    (ideas / "broken.md").write_text("# Idea: Broken\n")
    (ideas / "0005_adir.md").mkdir()
    (ideas / "B_upper.md").write_text("x")
    (ideas / "a-hyphen.md").write_text("x")
    (ideas / "a_under.md").write_text("x")
    (ideas / "a.dot").write_text("x")
    (ideas / "0010-slug.md").write_text("x")


def test_meta_of_a_whole_store_with_units_and_ideas(store: UnitStore):
    _whole_store(store)
    r = run(store, "meta")
    data = json.loads(r.out)
    assert list(data) == ["units", "ideas"]
    assert "ideas" not in data["units"]
    assert data["ideas"] is not None


def test_meta_of_one_unit(store: UnitStore):
    _whole_store(store)
    r = run(store, "meta", "0001_first")
    assert list(json.loads(r.out)["units"]) == ["0001_first"]


@pytest.mark.parametrize("unit", ["0003_empty", "Zed"])
def test_meta_of_each_kind_of_unit(store: UnitStore, unit):
    _whole_store(store)
    assert run(store, "meta", unit).code == 0


def test_meta_of_one_unit_with_named_artifacts(store: UnitStore):
    _whole_store(store)
    r = run(store, "meta", "0001_first", "spec.md", "intent.md")
    arts = json.loads(r.out)["units"]["0001_first"]["artifacts"]
    assert list(arts) == ["intent.md", "spec.md"]
    r = run(store, "meta", "0001_first", "plan.md", "plan.md")
    assert list(json.loads(r.out)["units"]["0001_first"]["artifacts"]) == ["plan.md"]
    # A named artifact the unit lacks is left out, and no intent means no type.
    r = run(store, "meta", "0001_first", "pr.md")
    assert json.loads(r.out)["units"]["0001_first"] == {"artifacts": {}, "answers": []}


def test_meta_reads_the_status_a_stage_may_carry(store: UnitStore):
    _whole_store(store)
    units = json.loads(run(store, "meta").out)["units"]
    first = units["0001_first"]["artifacts"]
    assert first["spec.md"]["status"] == "accepted"
    assert first["plan.md"]["status"] is None
    assert first["plan.md"]["raw"] == "weird-word"
    assert first["ship.md"]["raw"] is None


def test_meta_reads_questions_answers_holds_and_links(store: UnitStore):
    _whole_store(store)
    unit = json.loads(run(store, "meta", "0001_first", "intent.md").out)["units"]["0001_first"]
    assert [q["n"] for q in unit["artifacts"]["intent.md"]["questions"]] == [1, 3]
    assert unit["links"]["dependsOn"] == ["ws/0001_a", "other/0002_b"]
    assert unit["type"] == "feat"
    assert [h["state"] for h in unit["holds"]] == ["paused", "active", "dropped"]
    assert unit["answers"]


@pytest.mark.parametrize(
    "words",
    [
        ["meta", "0009_missing"],
        ["meta", ".."],
        ["meta", "."],
        ["meta", "ideas"],
        ["meta", "a/b"],
        ["meta", "../x"],
    ],
)
def test_meta_refuses_misuse_alike(store: UnitStore, words):
    _whole_store(store)
    r = run(store, *words)
    assert r.code == 2
    assert r.err


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Status: draft\n",
        "# Intent: x\r\nAuthor: a. Status: Accepted.\r\n\r\n## Open questions\r\n1. Why?\r\n",
        "# Intent: x\nStatus: accepted.\n## Answers\n### Câu 1\nAnswered by: A. Date: 2026-01-01. "
        "Via: cli.\nOK\n### Câu 2\nbroken\n",
        "# Intent: x Status: accepted.\n",
    ],
)
def test_meta_reads_odd_text_alike(store: UnitStore, text):
    store.unit("0001_odd", {"intent.md": text, "spec.md": text})
    assert run(store, "meta").code == 0
    assert run(store, "meta", "0001_odd", "intent.md").code == 0


def test_meta_reads_a_file_with_a_bad_byte_alike(store: UnitStore):
    d = store.unit("0001_bytes", {})
    (d / "intent.md").write_bytes(b"# Intent: \xff\xfe bad\nStatus: draft.\n\xc3\n")
    assert run(store, "meta").code == 0


@pytest.mark.parametrize(
    "idea",
    [
        "",
        "# Idea: x\nStatus: draft.\n## Units\n- ws/0001_a. Depends on: ws/0002_b\n",
        "# Idea: x\nStatus: draft.\n## Units\n- ws/0001_a.\n- ws/0002_b. Depends on: ws/0001_a.\n",
        "# Idea:x\nStatus: draft.\n## Units\n   - ws/0001_a   \n",
        "# Idea:   spaced  \nStatus: draft.\n## Units\n- ws/0001_a \n",
    ],
)
def test_meta_reads_an_idea_alike(store: UnitStore, idea):
    ideas = store.cos / "ideas"
    ideas.mkdir()
    (ideas / "0001_x.md").write_text(idea)
    assert run(store, "meta").code == 0
