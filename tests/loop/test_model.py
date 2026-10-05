"""The loop's parsers, gates and branch grammar, called directly, each held to fixed values.

A unit read from files gets the snapshot the app would build of them, from
`meta`'s own readers.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from coscc.loop import BRANCH_TYPES, STAGE_NAMES
from coscc.loop.branch import (
    VERSION_SOURCE,
    is_prerelease,
    tag_problem,
    version_problem,
)
from coscc.loop.model import (
    branch_for,
    branch_problem,
    parse_answers,
    parse_questions,
    parse_skip_reason,
    parse_status,
    parse_type,
    read_unit,
)
from coscc.loop.paths import next_number, read_ideas, unit_meta
from coscc.loop import stringify
from coscc.loop.model import (
    parse_deadline,
    parse_needs_person,
    parse_outcome,
    parse_spike,
    parse_unmeasured,
    unit_outcome,
)
from coscc.loop import SPIKE_ROUNDS
from coscc.loop.model import fold_holds, hold_blocks, non_blocking
from coscc.loop.rules import between_pr_and_ship, decide, gate_answer, next_answer
from coscc.loop.run import ask
from coscc.loop.model import parse_pr, parse_review, review_rounds
from coscc.loop import REVIEW_ROUNDS
from tests.loop.conftest import python

# --- the suite's glue -------------------------------------------------------------------


def next_action(unit, limit=REVIEW_ROUNDS):
    return {k: v for k, v in decide(unit, limit).items() if k not in ("why", "rerun", "continue")}


def check_gate(unit, stage, probe=None, limit=REVIEW_ROUNDS):
    return {k: v for k, v in gate_answer(unit, stage, probe, limit).items() if k != "reasons"}


def next_step(unit, probe=None, limit=REVIEW_ROUNDS):
    return {k: v for k, v in next_answer(unit, probe, limit).items() if k != "reasons"}


def entry_from(m: dict) -> dict:
    """The snapshot entry the app builds of `meta`'s reading; a skip stands for a person's."""
    arts = {
        f: {
            "status": a.get("status"),
            "raw": a.get("raw"),
            "questions": a.get("questions"),
            **({"authority": "person"} if a.get("status") == "skipped" else {}),
        }
        for f, a in m["artifacts"].items()
    }
    return {
        "artifacts": arts,
        "type": m.get("type"),
        "links": m.get("links") or {"idea": None, "repo": None, "dependsOn": None},
        "holds": [h for h in (m.get("holds") or []) if h.get("by") is not None],
        "answers": m.get("answers"),
        "unknowns": [],
        "merged": (m["artifacts"].get("ship.md") or {}).get("status") == "accepted",
    }


def state_of_root(root: Path) -> dict:
    """The snapshot of `<root>/.cos/`, as the app would hand `--state`."""
    cos = root / ".cos"
    units = {}
    if cos.exists():
        for d in sorted(cos.iterdir()):
            if d.is_dir() and d.name != "ideas":
                units[f"/{d.name}"] = entry_from(unit_meta(str(d)))
    ideas = read_ideas(str(cos)) if (cos / "ideas").exists() else []
    return {"workspace": "", "workspaces": [], "units": units, "ideas": {"": ideas}}


def read(dir_: Path, name: str) -> dict:
    """`read_unit` of `dir_` under `name`, with the snapshot of its files."""
    state = {"workspace": "", "workspaces": [], "units": {}, "ideas": {"": []}}
    state["units"][f"/{name}"] = entry_from(unit_meta(str(dir_)))
    return read_unit(str(dir_), name, state)


def unit(artifacts: dict) -> dict:
    return {"name": "0001_x", "type": "feat", "artifacts": artifacts, "problems": []}


def art(status) -> dict:
    return {"status": status, "skipReason": None}


SHA = "a" * 40


def ok(out: str = "") -> dict:
    return {"code": 0, "out": out, "err": ""}


def green_probe(checks=None, git=None, view=None) -> SimpleNamespace:
    """A probe that answers as git and gh would: `checks` for `gh pr checks`, `view` for
    `gh pr view` (the reviewed head when `None`), `git` by its argument string."""
    checks = [{"name": "tests", "bucket": "pass"}] if checks is None else checks
    view = view or {"state": "OPEN", "headRefOid": SHA}
    view = view if "title" in view else {**view, "title": "feat(0001): x"}
    git = git or {}
    return SimpleNamespace(
        gh=lambda *a: ok(json.dumps(view)) if a[1] == "view" else ok(json.dumps(checks)),
        git=lambda *a: git.get(" ".join(a), ok()),
    )


def files_in(tmp_path: Path, files: dict[str, str], name: str = "u") -> Path:
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    for f, text in files.items():
        (d / f).write_text(text)
    return d


def cli(*argv: str, stdin: str | None = None):
    """`python -m coscc.loop argv`; a deciding command gets the snapshot of its `--root`."""
    words = list(argv)
    deciding = {"status", "gate", "next", "rerun", "unit-branch", "pr-text", "screens"}
    if "--root" in words and deciding & set(words) and "--state" not in words:
        root = Path(words[words.index("--root") + 1])
        return python([*words, "--state", "-"], stdin=json.dumps(state_of_root(root)))
    return python(words, stdin=stdin)


# --- the status line, the gates and the next action -------------------------------------


def test_parse_status_takes_the_first_status_line():
    assert parse_status("Author: X. Status: draft.") == "draft"
    assert parse_status("Intent: intent.md. Spec: skipped (tiny). Status: accepted.") == "accepted"
    assert parse_status("Status: Accepted.") == "accepted"


def test_a_later_status_mention_cannot_shadow_the_real_one():
    assert parse_status("Status: draft.\n\n## Notes\nStatus: accepted is what we want.") == "draft"


def test_parse_status_returns_none_rather_than_guessing():
    assert parse_status("# Intent: x\nNo status here.") is None


def test_parse_skip_reason_reads_the_plan_header():
    assert parse_skip_reason("Spec: skipped (one file, no schema change).") == (
        "one file, no schema change"
    )
    assert parse_skip_reason("Spec: spec.md.") is None


def test_spec_gate_needs_an_accepted_intent():
    assert check_gate(unit({"intent.md": art("accepted")}), "spec")["ok"] is True
    assert check_gate(unit({"intent.md": art("draft")}), "spec")["ok"] is False
    assert check_gate(unit({}), "spec")["ok"] is False


def test_plan_gate_accepts_a_skipped_spec_but_not_a_missing_one():
    base = {"intent.md": art("accepted")}
    assert check_gate(unit({**base, "spec.md": art("skipped")}), "plan")["ok"] is True
    assert check_gate(unit({**base, "spec.md": art("accepted")}), "plan")["ok"] is True
    assert check_gate(unit({**base, "spec.md": art("draft")}), "plan")["ok"] is False
    assert check_gate(unit(base), "plan")["ok"] is False


FULL = {"intent.md": art("accepted"), "spec.md": art("accepted"), "plan.md": art("accepted")}


def test_implement_gate_needs_the_whole_chain_accepted():
    assert check_gate(unit(FULL), "implement")["ok"] is True
    assert check_gate(unit({**FULL, "plan.md": art("draft")}), "implement")["ok"] is False
    assert check_gate(unit({**FULL, "intent.md": art("draft")}), "implement")["ok"] is False


def test_an_unknown_stage_is_refused():
    assert check_gate(unit({"intent.md": art("accepted")}), "deploy")["ok"] is False


def test_next_action_names_one_action_per_state():
    assert "accept intent" in next_action(unit({"intent.md": art("draft")}))["action"]
    assert "write-spec" in next_action(unit({"intent.md": art("accepted")}))["action"]
    skipped = {"intent.md": art("accepted"), "spec.md": art("skipped")}
    assert "write-plan" in next_action(unit(skipped))["action"]
    assert "implementation starts" in next_action(unit(FULL))["action"]


def test_a_rejected_artifact_closes_the_unit():
    r = next_action(unit({"intent.md": art("rejected")}))
    assert r["blocked"] is False
    assert "closed" in r["action"]


def test_a_done_plan_is_finished():
    assert next_action(unit({**FULL, "plan.md": art("done")}))["action"] == "finished"


def test_next_number_counts_from_the_highest():
    assert next_number([]) == "0001"
    assert next_number([1, 2]) == "0003"
    assert next_number([7]) == "0008"
    assert next_number([3, 1]) == "0004"


def test_an_unreadable_artifact_is_not_a_missing_one():
    broken = unit({"intent.md": {"status": None, "skipReason": None}})
    assert "exists but carries no Status line" in check_gate(broken, "spec")["need"][0]
    assert re.search(r"fix intent\.md", next_action(broken)["action"])
    absent = unit({})
    assert "does not exist" in check_gate(absent, "spec")["need"][0]
    assert "write-intent" in next_action(absent)["action"]


def test_every_stage_name_opens_a_gate_and_a_tenth_does_not():
    assert STAGE_NAMES == [
        "idea",
        "intent",
        "spec",
        "spike",
        "plan",
        "impl",
        "pr",
        "review",
        "ship",
    ]
    for name in STAGE_NAMES:
        assert "unknown stage" not in " ".join(check_gate(unit({}), name)["need"]), name
    assert "unknown stage" in check_gate(unit({}), "deploy")["need"][0]


def test_idea_gates_nothing():
    assert check_gate(unit({}), "idea")["ok"] is True
    assert check_gate(unit({}), "intent")["ok"] is True
    assert "write-intent" in next_action(unit({}))["action"]


def test_a_later_stage_needs_the_earlier_ones_behind_it():
    assert check_gate(unit(FULL), "pr")["ok"] is False
    assert re.search(r"impl\.md does not exist", check_gate(unit(FULL), "pr")["need"][0])
    with_impl = {**FULL, "impl.md": art("accepted")}
    assert check_gate(unit(with_impl), "pr")["ok"] is True
    pr = {
        **art("accepted"),
        "pr": {"url": "https://github.com/o/r/pull/7", "number": 7},
        "title": "feat(0001): x",
    }
    assert check_gate(unit({**with_impl, "pr.md": pr}), "review", green_probe())["ok"] is True
    assert check_gate(unit({**FULL, "pr.md": pr}), "review", green_probe())["ok"] is False


def test_a_done_artifact_is_not_in_the_way():
    assert check_gate(unit({**FULL, "plan.md": art("done")}), "impl")["ok"] is True


def test_next_action_walks_past_an_accepted_plan():
    assert next_action(unit({**FULL, "impl.md": art("accepted")}))["action"] == "pr"
    with_pr = {**FULL, "impl.md": art("accepted"), "pr.md": art("accepted")}
    assert "write-review" in next_action(unit(with_pr))["action"]
    shipped = {**with_pr, "review.md": art("accepted"), "ship.md": art("accepted")}
    assert next_action(unit(shipped))["action"] == "finished"


def test_a_late_rejection_closes_the_unit():
    r = next_action(unit({**FULL, "impl.md": art("rejected")}))
    assert r["blocked"] is False
    assert "closed — impl rejected" in r["action"]


# --- the Type header --------------------------------------------------------------------


def with_intent(tmp_path: Path, header: str) -> dict:
    d = files_in(tmp_path, {"intent.md": f"# Intent: x\n{header}\n"}, name=str(len(header)))
    return read(d, "0011_typed")


def test_an_intent_with_no_type_is_a_problem(tmp_path):
    u = with_intent(tmp_path, "Author: Bao Do. Status: accepted.")
    assert len(u["problems"]) == 1
    assert re.search(r"intent\.md declares no Type", u["problems"][0])
    assert "type" not in u


def test_a_type_outside_the_ten_is_another_problem(tmp_path):
    u = with_intent(tmp_path, "Author: Bao Do. Type: nonsense. Status: accepted.")
    assert len(u["problems"]) == 1
    assert 'has type "nonsense", not one of' in u["problems"][0]
    for t in BRANCH_TYPES:
        assert t in u["problems"][0]


# --- pre-intent ---------------------------------------------------------------------------


def test_a_unit_holding_only_a_valid_idea_is_pre_intent(tmp_path):
    u = read(files_in(tmp_path, {"idea.md": "# Idea: x\nStatus: accepted.\n"}), "0015_fresh")
    assert u["phase"] == "pre-intent"
    assert u["problems"] == []
    assert next_action(u) == {
        "blocked": True,
        "action": "write-intent — the unit has no intent.md",
        "stage": "intent",
    }
    assert check_gate(u, "spec")["ok"] is False


def test_an_idea_with_a_later_artifact_and_no_intent_is_a_problem(tmp_path):
    files = {"idea.md": "Status: accepted.\n", "spec.md": "Status: draft.\n"}
    u = read(files_in(tmp_path, files), "0015_x")
    assert u["phase"] == "started"
    assert "no intent.md — every unit opens with one" in u["problems"]


def test_an_idea_with_no_status_line_is_not_pre_intent(tmp_path):
    u = read(files_in(tmp_path, {"idea.md": "# Idea: x\n"}), "0015_x")
    assert u["phase"] == "started"
    assert "no intent.md — every unit opens with one" in u["problems"]
    assert "idea.md carries no Status line" in u["problems"]


def test_a_unit_with_an_intent_is_started(tmp_path):
    u = read(files_in(tmp_path, {"intent.md": "Type: fix. Status: draft.\n"}), "0015_x")
    assert u["phase"] == "started"


def test_a_type_problem_closes_no_gate():
    u = {**unit({"intent.md": art("accepted")}), "problems": ["intent.md declares no Type"]}
    assert check_gate(u, "spec") == {"ok": True, "need": []}


# --- the branch grammar -------------------------------------------------------------------

BRANCH_ROWS = [
    ("feat/branch-conventions", None),
    ("fix/version-drift", None),
    ("main", "trunk"),
    ("feature/foo", "not one of"),
    ("feat/Foo", "lowercase"),
    ("feat/", "empty"),
    ("feat/a--b", "single hyphens"),
    ("feat/foo/bar", "second slash"),
]


@pytest.mark.parametrize(("name", "want"), BRANCH_ROWS)
def test_branch_problem(name, want):
    got = branch_problem(name)
    if want is None:
        assert got is None
    else:
        assert want in (got or "")


def test_the_type_set_is_the_ten_of_conventional_commits():
    assert BRANCH_TYPES == [
        "feat", "fix", "docs", "refactor", "test", "chore", "perf", "build", "ci", "revert",
    ]  # fmt: skip
    for t in BRANCH_TYPES:
        assert branch_problem(f"{t}/a-slug") is None
    assert '"feature" is not one of' in branch_problem("feature/x")


def test_a_slug_over_sixty_characters_is_refused():
    assert branch_problem("feat/" + "a" * 60) is None
    assert "61 characters, over the 60" in branch_problem("feat/" + "a" * 61)


TAG_ROWS = [
    ("v0.1.0", None),
    ("v1.20.3", None),
    ("v0.1.0-rc.1", None),
    ("v0.1.0-rc.12", None),
    ("0.1.0", "expected vX.Y.Z"),
    ("v0.1", "expected vX.Y.Z"),
    ("v0.1.0-rc", "expected vX.Y.Z"),
    ("v0.1.0-rc.0", "starts at 1"),
]


@pytest.mark.parametrize(("name", "want"), TAG_ROWS)
def test_tag_problem(name, want):
    got = tag_problem(name)
    if want is None:
        assert got is None
    else:
        assert want in (got or "")


def test_prerelease_is_decided_by_the_tag_grammar():
    assert is_prerelease("v0.1.0-rc.1") is True
    assert is_prerelease("v0.1.0") is False
    assert is_prerelease("release-rc.1") is False


def test_pyproject_is_the_version_source():
    assert VERSION_SOURCE == "pyproject.toml"
    assert version_problem({"pyproject.toml": "0.1.0", "package.json": "0.1.0"}) is None
    off = version_problem({"pyproject.toml": "0.1.0", "package.json": "0.0.1"})
    assert "pyproject.toml says 0.1.0, but package.json is 0.0.1" in off


def test_a_version_place_that_could_not_be_read_is_named():
    assert "uv.lock is unreadable" in version_problem({"pyproject.toml": "0.1.0", "uv.lock": None})
    assert "declares no version" in version_problem({"pyproject.toml": None})


def test_parse_type_reads_the_header_field():
    assert parse_type("Author: X. Type: feat. Status: accepted.") == "feat"
    assert parse_type("Author: X. Type: Feat. Status: accepted.") == "feat"
    assert parse_type("Author: X. Status: accepted.") is None


def unit_branch(name, header):
    """What `unit-branch` derives: the unit's name and the `Type:` of its header."""
    return branch_for(name, parse_type(header))


def test_the_branch_name_is_derived_from_the_unit_and_its_type():
    header = "Author: X. Type: feat. Status: accepted."
    got = unit_branch("0009_branch-and-release-conventions", header)
    assert got == {"branch": "feat/branch-and-release-conventions"}
    assert branch_problem(got["branch"]) is None


def test_a_derived_name_no_grammar_accepts_cannot_be_produced():
    assert '"feature" is not one of' in unit_branch("0009_x", "Type: feature.")["error"]
    assert "declares no Type:" in unit_branch("0009_x", "Author: X.")["error"]
    assert "does not match NNNN_<slug>" in unit_branch("9_x", "Type: feat.")["error"]
    assert "does not match NNNN_<slug>" in unit_branch(None, "Type: feat.")["error"]


def slug_of(n: int) -> str:
    """A slug of exactly `n` characters, made of short words the way a real one is."""
    s = ("abcd-" * -(-n // 5))[:n]
    return s[:-1] + "e" if s.endswith("-") else s


OLD_0044 = "0044_open-questions-wait-for-the-originator-even-when-precedent-answers-them"


def test_a_long_slug_is_cut_at_its_last_hyphen():
    assert unit_branch(OLD_0044, "Type: feat.")["branch"] == (
        "feat/open-questions-wait-for-the-originator-even-when-precedent"
    )
    assert len(slug_of(60)) == 60
    assert unit_branch(f"0001_{slug_of(60)}", "Type: feat.")["branch"] == f"feat/{slug_of(60)}"
    assert unit_branch("0001_" + "a" * 71, "Type: fix.")["branch"] == "fix/" + "a" * 60
    assert unit_branch("0001_" + "a" * 60 + "-b", "Type: fix.")["branch"] == "fix/" + "a" * 60


# --- the --root boundary, through the command line --------------------------------------


# --- 0016, 0109: the numbered questions under Open questions, and their answers -------------

QUESTIONS = "\n".join(
    [
        "# Intent: q",
        "Author: t. Type: feat. Status: accepted.",
        "",
        "## Problem",
        "",
        "1. Not a question: this list is under Problem.",
        "",
        "## Open questions",
        "",
        "1. **First?** Asked here",
        "   and continued on this line.",
        "2. Second?",
        "3. Third?",
        "",
    ]
)


def answer_block(n, by, text):
    return f"\n### Câu {n}\nAnswered by: {by}. Date: 2026-09-23. Via: product.\n\n{text}\n"


def with_answers(*blocks):
    return f"{QUESTIONS}\n## Answers\n{''.join(blocks)}"


def question_tree(tmp_path: Path, files: dict[str, str]) -> tuple[Path, dict]:
    root = tmp_path / "qroot"
    d = root / ".cos" / "0001_q"
    d.mkdir(parents=True)
    for f, text in files.items():
        (d / f).write_text(text)
    return root, read(d, "0001_q")


def ns(qs):
    return [q["n"] for q in qs]


def test_questions_are_the_numbered_items_under_open_questions():
    qs = parse_questions(QUESTIONS)
    assert ns(qs) == [1, 2, 3]
    assert "continued on this line" in qs[0]["text"]


def test_a_bullet_is_never_a_question():
    bullets = "## Open questions\n\nKhông còn câu hỏi mở.\n\n- **One?** first\n- Two?\n"
    assert parse_questions(bullets) == []
    mixed = "## Open questions\n\n1. Numbered?\n- a sub-point of it\n2. Also numbered?\n"
    assert ns(parse_questions(mixed)) == [1, 2]
    assert "sub-point" in parse_questions(mixed)[0]["text"]


def test_a_numbered_item_is_a_question_only_when_it_asks_one():
    text = "## Open questions\n\n1. Ai quyết định?\n7. Người khởi xướng vẫn nên đọc lại file này.\n"
    assert ns(parse_questions(text)) == [1]


def test_a_note_keeps_no_answer_and_a_heading_with_none_is_counted(tmp_path):
    intent = "\n".join(
        [
            "# Intent: q",
            "Author: t. Type: fix. Status: accepted.",
            "",
            "## Open questions",
            "",
            "1. A?",
            "2. Ghi chú.",
            "",
            "## Answers",
            answer_block(1, "A", "có"),
            answer_block(2, "A", "đã đọc"),
        ]
    )
    spec = (
        "# Spec\nIntent: intent.md. Author: t. Status: accepted.\n\n"
        "## Open questions\n\nKhông còn câu hỏi mở.\n"
    )
    _, u = question_tree(tmp_path, {"intent.md": intent, "spec.md": spec})
    qs = u["artifacts"]["intent.md"]["questions"]
    assert [[q["n"], q["answered"]] for q in qs] == [[1, True]]
    assert len(parse_answers(intent)) == 2
    assert u["counted"] == "spec.md"
    assert u["open"] == 0


def test_the_notes_of_a_hold_and_a_rerun_are_not_questions():
    s0107 = "\n".join(
        [
            "## Open questions",
            "",
            "Không còn câu hỏi mở. Câu 1 đến câu 5 của intent.md ## Answers đã được trả lời, "
            "và spec này dùng chúng như sau:",
            "",
            "- Câu 1 là hạn 2026-10-02. Spec không đổi hạn này.",
            "- Câu 2 cho phép R4.",
            "- Câu 3 cho phép R5.",
            "- Câu 4 là cách đo, và thành phép thử ngoài spec.",
            "- Câu 5 là lý do có Out of scope về nén tất định.",
            "",
            "Cả năm câu do Leif (CoS) trả lời thay người khởi xướng.",
            "",
        ]
    )
    assert parse_questions(s0107) == []
    s0054 = "\n".join(
        [
            "## Open questions",
            "",
            "1. Hạn thật cho outcome: đã trả lời, xem `intent.md ## Answers, câu 1`.",
            "6. Nút tách riêng và dòng xác nhận là yêu cầu hay gợi ý: đã trả lời, "
            "xem `intent.md ## Answers, câu 6`.",
            "7. Các câu 1–6 do Leif (CoS) trả lời thay người khởi xướng. "
            "Người khởi xướng vẫn nên đọc lại file này.",
            "",
        ]
    )
    assert 7 not in ns(parse_questions(s0054))


def test_three_questions_and_no_answers_is_three_open(tmp_path):
    _, u = question_tree(tmp_path, {"intent.md": QUESTIONS})
    assert u["open"] == 3
    assert len(u["questions"]) == 3
    assert u["problems"] == []


def test_one_answer_leaves_two_open_and_says_who_gave_it(tmp_path):
    text = with_answers(answer_block(2, "Phong Pham", "Tách ra."))
    _, u = question_tree(tmp_path, {"intent.md": text})
    assert u["open"] == 2
    q2 = next(q for q in u["artifacts"]["intent.md"]["questions"] if q["n"] == 2)
    assert q2["answered"] is True
    assert q2["answer"]["by"] == "Phong Pham"
    assert q2["answer"]["via"] == "product"
    assert q2["answer"]["text"] == "Tách ra."


def test_of_two_blocks_for_one_number_the_last_is_in_force(tmp_path):
    text = with_answers(answer_block(1, "A", "cũ"), answer_block(1, "B. C", "mới"))
    assert len(parse_answers(text)) == 2
    _, u = question_tree(tmp_path, {"intent.md": text})
    q1 = next(q for q in u["artifacts"]["intent.md"]["questions"] if q["n"] == 1)
    assert q1["answer"]["text"] == "mới"
    assert q1["answer"]["by"] == "B. C"
    assert u["open"] == 2


def test_a_block_with_no_header_line_is_not_an_answer():
    assert parse_answers(f"{QUESTIONS}\n## Answers\n\n### Câu 1\nno header here\n") == []


def test_answers_are_never_read_as_questions():
    text = with_answers(answer_block(3, "A", "1. looks like a question"))
    assert ns(parse_questions(text)) == [1, 2, 3]
    assert parse_status(text) == "accepted"
    assert parse_status(QUESTIONS) == parse_status(text)


def test_only_the_latest_artifact_with_questions_is_counted(tmp_path):
    spec = (
        "# Spec\nIntent: intent.md. Author: t. Status: accepted.\n\n"
        "## Open questions\n\n1. Only one?\n"
    )
    _, u = question_tree(tmp_path, {"intent.md": QUESTIONS, "spec.md": spec})
    assert u["open"] == 1
    assert u["counted"] == "spec.md"
    assert [q["artifact"] for q in u["questions"]] == ["intent.md"] * 3 + ["spec.md"]
    assert [q["counted"] for q in u["questions"]] == [False, False, False, True]


def test_an_open_question_closes_no_gate(tmp_path):
    _, u = question_tree(tmp_path, {"intent.md": QUESTIONS})
    assert u["open"] == 3
    assert check_gate(u, "spec")["ok"] is True


def test_status_json_over_64_kib_reaches_a_pipe_whole(tmp_path):
    """Read asynchronously, the way the board reads it."""
    long = f"{QUESTIONS}4. {'x' * 200 * 1024}?\n"
    root, _ = question_tree(tmp_path, {"intent.md": long})
    stdin = json.dumps(state_of_root(root))
    got = asyncio.run(ask(["--root", str(root), "--state", "-", "status", "--json"], stdin=stdin))
    assert len(got.out) > 200 * 1024
    assert json.loads(got.out)["units"][0]["open"] == 4


# --- 0015: review before merge ------------------------------------------------------------

FIX = "b" * 40
PR = {
    **art("accepted"),
    "pr": {"url": "https://github.com/o/r/pull/7", "number": 7},
    "title": "feat(0001): x",
}
CHAIN = {**FULL, "impl.md": art("accepted"), "pr.md": PR}
NOT_ANCESTOR = {"code": 1, "out": "", "err": ""}


def review_art(status, text):
    return {**art(status), "review": parse_review(text)}


def round_(n, verdict, findings=()):
    return (
        f"## Round {n}\n\nReviewed: {SHA}. Verdict: {verdict}.\n\n### Findings\n\n"
        f"{chr(10).join(findings)}\n\n### What was not reviewed\n\nnothing\n"
    )


def branched(artifacts):
    return {**unit(artifacts), "name": "0001_x", "branch": "feat/x"}


def test_parse_status_reads_a_hyphenated_status_whole():
    assert parse_status("Author: X. Status: changes-requested.") == "changes-requested"
    assert parse_status("Status: accepted-.") == "accepted"


def test_parse_pr_reads_the_pull_request_or_none():
    assert parse_pr("# PR\nPR: https://github.com/o/r/pull/42. Status: accepted.") == {
        "url": "https://github.com/o/r/pull/42",
        "number": 42,
    }
    assert parse_pr("# PR\nStatus: accepted.") is None


def test_parse_review_reads_rounds_verdicts_and_findings():
    one = round_(1, "changes-requested", ["- F1 [open] a", "- F2 [open] b"])
    two = round_(2, "pass", [f"- F1 [fixed {FIX}] a", "- F2 [maybe] b"])
    rounds = parse_review(
        f"# Review\nStatus: accepted.\n\n{one}\n{two}\n## Answers\n\n## Round 3\n"
    )["rounds"]
    assert [[r["n"], r["verdict"], r["reviewed"]] for r in rounds] == [
        [1, "changes-requested", SHA],
        [2, "pass", SHA],
    ]
    assert [[f["id"], f["label"], f["fixedBy"]] for f in rounds[1]["findings"]] == [
        ["F1", "fixed", FIX],
        ["F2", "unreadable", None],
    ]
    assert parse_review("# Review written before rounds\nStatus: accepted.\n")["rounds"] == []


def test_the_review_gate_is_closed_while_pr_md_names_no_pull_request():
    g = check_gate(unit({**CHAIN, "pr.md": art("accepted")}), "review", green_probe())
    assert g["ok"] is False
    assert "pr.md names no pull request" in g["need"][0]


def test_changes_requested_and_rejected_lead_to_different_places():
    cr = review_art("changes-requested", round_(1, "changes-requested", ["- F1 [open] x"]))
    asked = next_action(unit({**CHAIN, "review.md": cr}))
    assert asked["blocked"] is True
    assert re.search(
        r"fix the open findings of review round 1 .* \(1 of 3 rounds used\)", asked["action"]
    )
    rejected = next_action(unit({**CHAIN, "review.md": art("rejected")}))
    assert rejected["blocked"] is False
    assert "closed — review rejected" in rejected["action"]


def test_changes_requested_closes_ship_but_not_review():
    cr = review_art("changes-requested", round_(1, "changes-requested", ["- F1 [open] x"]))
    u = unit({**CHAIN, "review.md": cr})
    assert check_gate(u, "review", green_probe())["ok"] is True
    assert 'review.md is "changes-requested"' in check_gate(u, "ship", green_probe())["need"][0]


def test_ci_decides_whether_review_may_begin():
    u = unit(CHAIN)
    red = check_gate(
        u,
        "review",
        green_probe(
            [{"name": "tests", "bucket": "fail"}, {"name": "branch-name", "bucket": "pass"}]
        ),
    )
    assert red["ok"] is False
    assert "CI is red on #7: tests — back to impl" in red["need"][0]
    pending = green_probe([{"name": "tests", "bucket": "pending"}])
    assert "has not finished" in check_gate(u, "review", pending)["need"][0]
    assert "no required checks" in check_gate(u, "review", green_probe([]))["need"][0]
    broken = SimpleNamespace(
        gh=lambda *a: {"code": 1, "out": "", "err": "HTTP 401"}, git=lambda *a: ok()
    )
    assert "HTTP 401" in check_gate(u, "review", broken)["need"][0]
    assert "no repository given" in check_gate(u, "review")["need"][0]
    skipping = green_probe([{"name": "a", "bucket": "pass"}, {"name": "b", "bucket": "skipping"}])
    assert check_gate(u, "review", skipping)["ok"] is True


def test_the_round_limit_stops_the_loop_for_a_person():
    text = "\n".join(round_(n, "changes-requested", ["- F1 [open] x"]) for n in (1, 2, 3))
    u = unit({**CHAIN, "review.md": review_art("changes-requested", text)})
    assert REVIEW_ROUNDS == 3
    assert (
        "needs a person — review used 3 of 3 rounds"
        in check_gate(u, "review", green_probe())["need"][0]
    )
    assert "needs a person" in next_action(u)["action"]
    assert check_gate(u, "review", green_probe(), 4)["ok"] is True
    assert "3 of 4 rounds used" in next_action(u, 4)["action"]


def test_review_rounds_reads_the_variable_and_refuses_what_is_no_positive_integer():
    assert review_rounds({}) == 3
    assert review_rounds({"COS_REVIEW_ROUNDS": "5"}) == 5
    for bad in ["0", "-1", "two", "1.5"]:
        with pytest.raises(Exception, match="COS_REVIEW_ROUNDS"):
            review_rounds({"COS_REVIEW_ROUNDS": bad})


def ship(text, status="accepted"):
    return branched({**CHAIN, "review.md": review_art(status, text)})


def test_an_accepted_review_with_a_finding_open_cannot_ship():
    g = check_gate(ship(round_(1, "pass", ["- F1 [open] the thing"])), "ship", green_probe())
    assert g["ok"] is False
    assert "F1 [open]" in "\n".join(g["need"])


def test_an_earlier_round_survives_and_a_dropped_or_renumbered_one_is_caught():
    r1 = round_(1, "changes-requested", ["- F1 [open] x"])
    good = f"{r1}\n{round_(2, 'pass', [f'- F1 [fixed {FIX}] x'])}"
    assert check_gate(ship(good), "ship", green_probe())["ok"] is True
    dropped = f"{r1}\n{round_(2, 'pass')}"
    assert re.search(
        r"drops findings .*F1", "\n".join(check_gate(ship(dropped), "ship", green_probe())["need"])
    )
    gap = f"{r1}\n{round_(3, 'pass', [f'- F1 [fixed {FIX}] x'])}"
    assert "numbered 1, 3" in "\n".join(check_gate(ship(gap), "ship", green_probe())["need"])


def test_code_after_the_reviewed_commit_closes_ship_and_the_units_files_do_not():
    u = ship(round_(1, "pass"))

    def diff(files):
        said = ok("\n".join(files))
        return green_probe(
            None,
            {
                f"diff --name-only {SHA}..refs/heads/feat/x": said,
                f"diff --name-only {SHA}..refs/remotes/origin/feat/x": said,
            },
        )

    assert check_gate(u, "ship", diff([".cos/0001_x/review.md"]))["ok"] is True
    g = check_gate(u, "ship", diff([".cos/0001_x/review.md", "src/a.py"]))
    assert g["ok"] is False
    assert re.search(r"changed after the reviewed commit .*src/a\.py", g["need"][0])
    rewritten = green_probe(
        None, {f"merge-base --is-ancestor {SHA} refs/heads/feat/x": NOT_ANCESTOR}
    )
    assert "not on refs/heads/feat/x" in check_gate(u, "ship", rewritten)["need"][0]
    assert "no repository given" in check_gate(u, "ship")["need"][0]


def test_ship_reads_the_pull_request_head_and_pins_the_merge_to_it():
    u = ship(round_(1, "pass"))
    head = "c" * 40
    open_ = {"state": "OPEN", "headRefOid": head}
    elsewhere = green_probe(None, {f"diff --name-only {SHA}..{head}": ok("src/a.py")}, open_)
    g = check_gate(u, "ship", elsewhere)
    assert g["ok"] is False
    assert re.search(
        rf"the head of #7 \({head}\) changed after the reviewed commit .*src/a\.py", g["need"][0]
    )
    own = green_probe(None, {f"diff --name-only {SHA}..{head}": ok(".cos/0001_x/review.md")}, open_)
    good = check_gate(u, "ship", own)
    assert good["ok"] is True
    assert good["head"] == head
    missing = green_probe(None, {f"cat-file -e {head}^{{commit}}": NOT_ANCESTOR}, open_)
    assert (
        "not in this repository — someone pushed from elsewhere: fetch"
        in (check_gate(u, "ship", missing)["need"][0])
    )
    closed = green_probe(None, {}, {"state": "CLOSED", "headRefOid": SHA})
    assert "#7 is CLOSED, not open" in check_gate(u, "ship", closed)["need"][0]
    offline = SimpleNamespace(
        gh=lambda *a: {"code": 1, "out": "", "err": "error connecting to api.github.com"},
        git=lambda *a: ok(),
    )
    assert (
        "cannot read the head of #7: error connecting" in check_gate(u, "ship", offline)["need"][0]
    )


def test_a_rebase_after_the_pass_closes_ship_and_another_pass_opens_it():
    reb = "d" * 40
    rebased = {"state": "OPEN", "headRefOid": reb}
    after = green_probe(
        None,
        {
            f"merge-base --is-ancestor {SHA} refs/heads/feat/x": NOT_ANCESTOR,
            f"merge-base --is-ancestor {SHA} refs/remotes/origin/feat/x": NOT_ANCESTOR,
            f"merge-base --is-ancestor {SHA} {reb}": NOT_ANCESTOR,
        },
        rebased,
    )
    r1 = round_(1, "changes-requested", ["- F1 [open] x"])
    r2 = round_(2, "pass", [f"- F1 [fixed {FIX}] x"])
    g = check_gate(ship(f"{r1}\n{r2}"), "ship", after)
    assert g["ok"] is False
    assert (
        "rewritten after the pass (a rebase does this): review its new head in another round"
        in g["need"][0]
    )
    r3 = round_(3, "pass", [f"- F1 [fixed {FIX}] x"]).replace(SHA, reb, 1)
    opened = check_gate(ship(f"{r1}\n{r2}\n{r3}"), "ship", green_probe(None, {}, rebased))
    assert opened["ok"] is True
    assert opened["head"] == reb
    r3cr = round_(3, "changes-requested", [f"- F1 [fixed {FIX}] x", "- F2 [open] y"])
    u = unit({**CHAIN, "review.md": review_art("changes-requested", f"{r1}\n{r2}\n{r3cr}")})
    assert "2 of 3 rounds used" in next_action(u)["action"]
    assert check_gate(u, "review", green_probe())["ok"] is True


def test_a_review_md_with_no_rounds_cannot_ship():
    u = ship("# Review\nStatus: accepted.\n")
    assert "no ## Round" in check_gate(u, "ship", green_probe())["need"][0]


# --- 0024: the stage a run button offers ---------------------------------------------------

HEAD2 = "e" * 40
FULL_LANE = {"lane": "full", "enteredFast": False, "laneMissing": []}


def asked(text=None):
    text = text or round_(1, "changes-requested", ["- F1 [open] x"])
    return branched({**CHAIN, "review.md": review_art("changes-requested", text)})


def moved_to(files, checks=None):
    diff = {f"diff --name-only {SHA}..{HEAD2}": ok("\n".join(files))}
    return green_probe(checks, diff, {"state": "OPEN", "headRefOid": HEAD2})


def passed(findings=()):
    return branched({**CHAIN, "review.md": review_art("accepted", round_(1, "pass", findings))})


def test_next_action_stage_names_the_missing_stage_or_nothing():
    assert next_action(unit({"intent.md": art("accepted")}))["stage"] == "spec"
    assert next_action(unit(CHAIN))["stage"] == "review"
    assert next_action(unit({"intent.md": art("draft")}))["stage"] == ""
    assert next_action(unit({"intent.md": art("rejected")}))["stage"] == ""
    assert next_action(unit({"intent.md": art(None)}))["stage"] == ""
    cr = review_art("changes-requested", round_(1, "changes-requested", ["- F1 [open] x"]))
    assert next_action(unit({**CHAIN, "review.md": cr}))["stage"] == ""


def test_a_missing_stage_that_needs_no_git_is_offered_as_it_was():
    no_pr = {k: v for k, v in CHAIN.items() if k != "pr.md"}
    for artifacts, stage in [
        ({"intent.md": art("accepted")}, "spec"),
        ({"intent.md": art("accepted"), "spec.md": art("skipped")}, "plan"),
        (FULL, "impl"),
        (no_pr, "pr"),
    ]:
        u = unit(artifacts)
        assert next_step(u)["stage"] == stage
        assert next_step(u, green_probe())["stage"] == stage
        assert next_step(u)["action"] == next_action(u)["action"]


def test_changes_asked_and_nothing_new_on_the_pull_request_is_impl():
    n = next_step(asked(), green_probe())
    assert n["stage"] == "impl"
    assert re.search(
        r"fix the open findings of review round 1 .*nothing outside \.cos/0001_x/ has reached #7",
        n["action"],
    )
    assert next_step(asked(), moved_to([".cos/0001_x/review.md"]))["stage"] == "impl"


def test_changes_asked_and_a_fix_on_a_green_head_is_review():
    assert next_step(asked(), moved_to(["src/a.py"]))["stage"] == "review"
    rebased = green_probe(
        None,
        {f"merge-base --is-ancestor {SHA} {HEAD2}": NOT_ANCESTOR},
        {"state": "OPEN", "headRefOid": HEAD2},
    )
    assert next_step(asked(), rebased)["stage"] == "review"


def test_a_fix_on_the_head_while_ci_runs_or_failed():
    pending = next_step(asked(), moved_to(["src/a.py"], [{"name": "tests", "bucket": "pending"}]))
    assert pending["stage"] == ""
    assert "CI has not finished on #7" in pending["action"]
    red = next_step(asked(), moved_to(["src/a.py"], [{"name": "tests", "bucket": "fail"}]))
    assert red["stage"] == "impl"
    assert "CI is red on #7" in red["action"]
    view = ok(json.dumps({"state": "OPEN", "headRefOid": HEAD2, "title": "feat(0001): x"}))
    unreadable = SimpleNamespace(
        gh=lambda *a: view if a[1] == "view" else {"code": 1, "out": "", "err": "HTTP 401"},
        git=lambda *a: ok("src/a.py") if a[0] == "diff" else ok(),
    )
    assert next_step(asked(), unreadable)["stage"] == ""


def test_an_open_pull_request_with_no_review_goes_by_ci():
    u = branched(CHAIN)
    assert next_step(u, green_probe())["stage"] == "review"
    assert next_step(u, green_probe([{"name": "tests", "bucket": "fail"}]))["stage"] == "impl"
    assert next_step(u, green_probe([{"name": "tests", "bucket": "pending"}]))["stage"] == ""
    assert next_step(u, green_probe([]))["stage"] == ""


def test_the_round_limit_used_up_offers_nothing():
    text = "\n".join(round_(n, "changes-requested", ["- F1 [open] x"]) for n in (1, 2, 3))
    n = next_step(asked(text), moved_to(["src/a.py"]))
    assert n["stage"] == ""
    assert "needs a person — review used 3 of 3 rounds" in n["action"]
    assert next_step(asked(text), moved_to(["src/a.py"]), 4)["stage"] == "review"


def test_a_pass_with_the_ship_gate_open_is_ship_pinned():
    n = next_step(passed(), green_probe())
    assert n["stage"] == "ship"
    assert n["action"].startswith(f"ship — merge with --match-head-commit {SHA}")


def test_a_done_plan_offers_nothing_whatever_the_later_files_say():
    cr = review_art("changes-requested", round_(1, "changes-requested", ["- F1 [open] x"]))
    u = unit({**CHAIN, "plan.md": art("done"), "review.md": cr})
    assert next_step(u, green_probe()) == {"blocked": False, "action": "finished", "stage": ""}


def test_a_branch_moved_after_the_pass_is_review_again():
    n = next_step(passed(), moved_to(["src/a.py"]))
    assert n["stage"] == "review"
    assert "changed after the reviewed commit" in n["action"]
    red = moved_to(["src/a.py"], [{"name": "tests", "bucket": "fail"}])
    assert next_step(passed(), red)["stage"] == "impl"
    assert next_step(passed(["- F1 [open] x"]), green_probe())["stage"] == ""


def test_with_no_repository_the_git_cases_offer_nothing_and_say_repo():
    for u in [asked(), branched(CHAIN), passed()]:
        n = next_step(u)
        assert n["stage"] == ""
        assert "--repo" in n["action"]


def test_next_step_never_offers_a_stage_whose_gate_is_closed():
    probes = [
        green_probe(),
        moved_to(["src/a.py"]),
        moved_to(["src/a.py"], [{"name": "t", "bucket": "fail"}]),
    ]
    units = [asked(), branched(CHAIN), passed(), unit({"intent.md": art("accepted")})]
    for probe in probes:
        for u in units:
            stage = next_step(u, probe)["stage"]
            if stage:
                assert check_gate(u, stage, probe)["ok"] is True, stage


# --- 0028: a finding impl cannot fix waits for a person -------------------------------------

NEEDS = (
    "## Needs a person\n\n- F2: the grant holds no budget for --paid\n- F3: the grant holds no gh\n"
)
REVIEW_HEAD = "# Review: x\nPR: pr.md. Author: t. Status: changes-requested.\n\n"
ROUND1 = round_(1, "changes-requested", ["- F1 [open] a", "- F2 [open] b", "- F3 [open] c"])
ROUND2 = round_(2, "changes-requested", [f"- F1 [fixed {FIX}] a", "- F2 [open] b", "- F3 [open] c"])


def impl_text(needs=NEEDS):
    return (
        "# Impl: x\nIntent: intent.md. Plan: plan.md. Author: t. Status: accepted.\n\n"
        f"## What was built\n\nx\n\n{needs}"
    )


def f_block(id_, text="ran it"):
    return f"\n### {id_}\nAnswered by: Bao. Date: 2026-09-24. Via: product.\n\n{text}\n"


def round3(f3="needs-person", extra=()):
    return round_(
        3,
        "needs-person",
        [f"- F1 [fixed {FIX}] a", "- F2 [needs-person] b", f"- F3 [{f3}] c", *extra],
    )


def tree_after_round_two(tmp_path, review, impl=None):
    """A unit right after its second review round, as files on disk."""
    _, u = question_tree(
        tmp_path / str(len(list(tmp_path.iterdir()))),
        {
            "intent.md": "# I\nAuthor: t. Type: fix. Status: accepted.\n",
            "spec.md": "# S\nStatus: accepted.\n",
            "plan.md": "# P\nStatus: accepted.\n",
            "impl.md": impl_text() if impl is None else impl,
            "pr.md": "# PR: fix(0001): x\nPR: https://github.com/o/r/pull/7. Status: accepted.\n",
            "review.md": review,
        },
    )
    return u


R12 = f"{REVIEW_HEAD}{ROUND1}\n{ROUND2}"


def test_needs_a_person_reads_well_formed_lines_and_stops_at_answers():
    body = (
        "## Needs a person\n\n- F2: no gh in this grant\n- F3 no colon\n-F4: no space\n"
        "- F5:   \n* F6: a star\n- F7: real money\n"
    )
    text = f"{impl_text(body)}\n## Answers\n\n## Needs a person\n\n- F9: under answers\n"
    assert parse_needs_person(text) == [
        {"id": "F2", "reason": "no gh in this grant"},
        {"id": "F7", "reason": "real money"},
    ]
    assert parse_needs_person(impl_text("")) == []
    tail = "# Impl\nStatus: accepted.\n\n## Answers\n\n## Needs a person\n\n- F1: x\n"
    assert parse_needs_person(tail) == []


def test_parse_answers_reads_numbered_and_finding_blocks_side_by_side(tmp_path):
    text = (
        f"{with_answers(answer_block(2, 'Bao', 'two'))}{f_block('F2', 'first')}"
        f"{f_block('F2', 'second')}\n### F3\nno header\n"
    )
    assert [[a["n"], a["id"], a["text"]] for a in parse_answers(text)] == [
        [2, None, "two"],
        [None, "F2", "first"],
        [None, "F2", "second"],
    ]
    root, _ = question_tree(tmp_path / "a", {})
    d = root / ".cos" / "0001_q"
    (d / "review.md").write_text(
        f"{REVIEW_HEAD}{ROUND1}\n## Answers\n{f_block('F2', 'first')}{f_block('F2', 'second')}"
        "\n### F3\nno header\n"
    )
    assert read(d, "0001_q")["artifacts"]["review.md"]["personAnswers"] == ["F2"]
    intent = f"# I\nAuthor: t. Type: fix. Status: accepted.\n\n{with_answers(f_block('F1'))}"
    _, u = question_tree(tmp_path / "b", {"intent.md": intent})
    assert all(not q["answered"] for q in u["artifacts"]["intent.md"]["questions"])


def test_the_person_labels_and_the_needs_person_verdict_are_read():
    findings = [
        "- F1 [needs-person] a",
        "- F2 [Claim-Rejected] b",
        "- F3 [answered] c",
        "- F4 [waiting] d",
    ]
    r = parse_review(round_(1, "needs-person", findings))["rounds"][0]
    assert r["verdict"] == "needs-person"
    assert [f["label"] for f in r["findings"]] == [
        "needs-person",
        "claim-rejected",
        "answered",
        "unreadable",
    ]


def test_a_needs_person_round_is_not_counted_toward_the_limit():
    three = "\n".join(round_(n, "changes-requested", ["- F1 [open] x"]) for n in (1, 2, 3))
    text = f"{three}\n{round_(4, 'needs-person', ['- F1 [needs-person] x'])}"
    assert "needs a person — F1: impl.md gives no reason" in next_action(asked(text), 4)["action"]
    assert "3 of 4 rounds used" in next_action(asked(three), 4)["action"]
    assert "needs a person — review used 3 of 3" in next_action(asked(text), 3)["action"]
    one = next_action(asked(round_(1, "needs-person", ["- F1 [needs-person] x"])), 1)
    assert "rounds and findings are still open" not in one["action"]
    assert one["waiting"] == ["F1"]


def test_every_open_finding_claimed_and_unconfirmed_is_review(tmp_path):
    n = next_step(tree_after_round_two(tmp_path, R12), green_probe())
    assert n["stage"] == "review"
    assert "claimed in impl.md ## Needs a person — review confirms or rejects each" in n["action"]
    assert "waiting" not in n


def test_both_claims_confirmed_names_a_person(tmp_path):
    u = tree_after_round_two(tmp_path, f"{R12}\n{round3()}")
    n = next_step(u, green_probe())
    assert n["stage"] == ""
    assert n["blocked"] is True
    assert n["action"].startswith(
        "needs a person — F2: the grant holds no budget for --paid; F3: the grant holds no gh "
        "— answer each on the Questions tab"
    )
    assert n["waiting"] == ["F2", "F3"]
    assert next_step(u)["waiting"] == ["F2", "F3"]
    assert next_action(u)["waiting"] == ["F2", "F3"]
    assert [[p["id"], p["answered"]] for p in u["personFindings"]] == [["F2", False], ["F3", False]]


def test_one_answered_one_not_still_waits_for_the_other(tmp_path):
    u = tree_after_round_two(tmp_path, f"{R12}\n{round3()}\n## Answers\n{f_block('F2')}")
    n = next_step(u, green_probe())
    assert n["stage"] == ""
    assert n["action"].startswith("needs a person — F3: the grant holds no gh")
    assert n["waiting"] == ["F3"]


def test_both_answered_review_reads_the_answers(tmp_path):
    u = tree_after_round_two(
        tmp_path, f"{R12}\n{round3()}\n## Answers\n{f_block('F2')}{f_block('F3')}"
    )
    n = next_step(u, green_probe())
    assert n["stage"] == "review"
    assert "a person answered F2, F3 in review.md" in n["action"]
    assert "waiting" not in n
    assert check_gate(u, "review", green_probe())["ok"] is True


def test_an_open_finding_impl_did_not_claim_is_impl(tmp_path):
    r2 = round_(
        2,
        "changes-requested",
        [f"- F1 [fixed {FIX}] a", "- F2 [open] b", "- F3 [open] c", "- F4 [open] d"],
    )
    u = tree_after_round_two(tmp_path, f"{REVIEW_HEAD}{ROUND1}\n{r2}")
    assert next_step(u, green_probe())["stage"] == "impl"


def test_a_rejected_claim_is_impl(tmp_path):
    u = tree_after_round_two(tmp_path, f"{R12}\n{round3('claim-rejected')}")
    assert next_step(u, green_probe())["stage"] == "impl"
    assert u["personFindings"] == []
    r3cr = round_(
        3,
        "changes-requested",
        [f"- F1 [fixed {FIX}] a", "- F2 [needs-person] b", "- F3 [claim-rejected] c"],
    )
    assert (
        next_step(tree_after_round_two(tmp_path, f"{R12}\n{r3cr}"), green_probe(), 4)["stage"]
        == "impl"
    )


def test_answered_but_kept_open_is_impl_not_review_again(tmp_path):
    r4 = round_(
        4, "changes-requested", [f"- F1 [fixed {FIX}] a", "- F2 [answered] b", "- F3 [open] c"]
    )
    review = f"{R12}\n{round3()}\n{r4}\n## Answers\n{f_block('F2')}{f_block('F3')}"
    n = next_step(tree_after_round_two(tmp_path, review), green_probe(), 4)
    assert n["stage"] == "impl"
    assert "claimed in impl.md ## Needs a person" not in n["action"]
    r4b = round_(
        4, "changes-requested", [f"- F1 [fixed {FIX}] a", "- F2 [needs-person] b", "- F3 [open] c"]
    )
    r3cr = round_(
        3,
        "changes-requested",
        [f"- F1 [fixed {FIX}] a", "- F2 [open] b", "- F3 [claim-rejected] c"],
    )
    u = tree_after_round_two(tmp_path, f"{R12}\n{r3cr}\n{r4b}")
    assert next_step(u, green_probe(), 5)["stage"] == "impl"


def test_a_needs_person_verdict_written_wrong_falls_back(tmp_path):
    a = tree_after_round_two(tmp_path, f"{R12}\n{round3('needs-person', ['- F4 [open] d'])}")
    assert next_step(a, green_probe())["stage"] == "impl"
    assert "waiting" not in next_step(a, green_probe())
    b = tree_after_round_two(tmp_path, f"{R12}\n{round3('answered')}")
    assert next_step(b, green_probe())["stage"] == "impl"
    assert b["personFindings"] == []


def test_an_impl_with_no_needs_a_person_section_changes_nothing(tmp_path):
    n = next_step(tree_after_round_two(tmp_path, R12, impl_text("")), green_probe())
    assert n["stage"] == "impl"
    assert "nothing outside .cos/0001_q/ has reached #7" in n["action"]


def test_ship_closes_an_answered_finding_only_with_its_answer():
    pass_ = round_(1, "pass", [f"- F1 [fixed {FIX}] a", "- F2 [answered] b"])
    with_block = {**review_art("accepted", pass_), "personAnswers": ["F2"]}
    g = check_gate(branched({**CHAIN, "review.md": with_block}), "ship", green_probe())
    assert g["ok"] is True, g["need"]
    bare = check_gate(ship(pass_), "ship", green_probe())
    assert bare["ok"] is False
    assert "F2 [answered, no answer in review.md]" in "\n".join(bare["need"])
    for label in ["needs-person", "claim-rejected"]:
        art_ = {
            **review_art("accepted", round_(1, "pass", [f"- F2 [{label}] b"])),
            "personAnswers": ["F2"],
        }
        shut = check_gate(branched({**CHAIN, "review.md": art_}), "ship", green_probe())
        assert shut["ok"] is False
        assert f"F2 [{label}]" in "\n".join(shut["need"])


def test_next_prints_waiting_only_when_a_person_is_awaited(tmp_path):
    root, _ = question_tree(
        tmp_path,
        {
            "intent.md": "# I\nAuthor: t. Type: fix. Status: accepted.\n",
            "spec.md": "Status: accepted.\n",
            "plan.md": "Status: accepted.\n",
            "impl.md": impl_text(),
            "pr.md": "PR: https://github.com/o/r/pull/7. Status: accepted.\n",
            "review.md": f"{R12}\n{round3()}\n## Answers\n{f_block('F2')}",
        },
    )
    out = json.loads(cli("--root", str(root), "next", "0001_q").out)
    assert out["stage"] == ""
    assert out["waiting"] == ["F3"]
    status = json.loads(cli("--root", str(root), "status", "--json").out)["units"][0]
    assert status["next"]["waiting"] == ["F3"]
    assert status["personFindings"] == [
        {"id": "F2", "reason": "the grant holds no budget for --paid", "answered": True},
        {"id": "F3", "reason": "the grant holds no gh", "answered": False},
    ]


# --- 0035: between pr and ship --------------------------------------------------------------


def test_between_pr_and_ship_only_on_an_accepted_pr_naming_a_pull_request():
    def between(artifacts):
        return between_pr_and_ship(unit(artifacts))

    no_pr = {**FULL, "impl.md": art("accepted")}
    assert between(no_pr) is False
    assert between({**no_pr, "pr.md": {**art("draft"), "pr": PR["pr"]}}) is False
    assert between({**no_pr, "pr.md": {**art("accepted"), "pr": None}}) is False
    cr = review_art("changes-requested", round_(1, "changes-requested", ["- F1 [open] x"]))
    assert between({**CHAIN, "review.md": cr}) is True
    assert between({**CHAIN, "plan.md": art("done")}) is False
    assert between({**CHAIN, "review.md": art("rejected")}) is False


# --- 0033: the plan's Impl: label -----------------------------------------------------------


def test_the_plans_impl_label_opens_and_closes_no_gate_and_moves_no_next(tmp_path):
    def ask_(label):
        root = tmp_path / str(label)
        d = root / ".cos" / "0001_same"
        d.mkdir(parents=True)
        (d / "intent.md").write_text("# X\nType: feat. Status: accepted.\n")
        (d / "spec.md").write_text("# X\nStatus: accepted.\n")
        said = "" if label is None else f" Impl: {label}."
        (d / "plan.md").write_text(f"# X\nStatus: accepted.{said}\n")
        gate = cli("gate", "0001_same", "implement", "--root", str(root))
        nxt = cli("next", "0001_same", "--root", str(root))
        return gate.code, gate.out, json.loads(nxt.out)

    routine = ask_("routine")
    assert routine[0] == 0, routine[1]
    assert ask_("novel") == routine
    assert ask_(None) == routine


# --- 0047: the outcome a unit was measured against ------------------------------------------

OUTCOME_INTENT = "\n".join(
    [
        "# Intent: x",
        "Author: a. Type: feat. Status: accepted.",
        "",
        "## Proposed outcome",
        "",
        "By 2026-10-07, three of three. Compared against 2026-09-01.",
        "",
        "## Open questions",
        "",
        "1. First?",
        "",
    ]
)


def outcome_block(lines, by="Linh", date="2026-10-08"):
    body = "\n".join(lines)
    return f"\n### Outcome\nAnswered by: {by}. Date: {date}. Via: product.\n\n{body}\n"


def test_the_deadline_is_the_first_real_date_under_proposed_outcome():
    assert parse_deadline(OUTCOME_INTENT) == "2026-10-07"
    assert (
        parse_deadline("## Proposed outcome\n\nOn 2026-02-30, then 2026-03-01.\n") == "2026-03-01"
    )
    assert parse_deadline("## Problem\n\n2026-10-07\n\n## Proposed outcome\n\nSoon.\n") is None
    assert parse_deadline("## Problem\n\n2026-10-07\n") is None
    tail = f"## Proposed outcome\n\nSoon.\n\n## Answers\n{outcome_block(['Result: đạt'])}"
    assert parse_deadline(tail) is None


def test_a_valid_outcome_block_needs_a_result_measured_by_and_source_or_reason():
    blocks = [
        outcome_block(["Result: đạt", "Measured by: agent", "Source: npm test, 12 pass"]),
        outcome_block(["Result: trượt", "Measured by: Linh"]),
        outcome_block(["Result: không đo được", "Measured by: agent", "Source: x"]),
        outcome_block(["Result: maybe", "Measured by: agent", "Source: x"]),
        outcome_block(["Result: đạt", "Source: x"]),
        "\n### Outcome\nno header line\n\nResult: đạt\nMeasured by: agent\nSource: x\n",
        outcome_block(
            [
                "Source: board, 2026-10-08",
                "Result: Trượt",
                "a note line inside",
                "Measured by: Linh",
            ]
        ),
        outcome_block(
            [
                "Result: không đo được",
                "Measured by: agent",
                "Reason: no script",
                "",
                "A longer note.",
            ]
        ),
    ]
    text = f"{OUTCOME_INTENT}\n## Answers\n" + "".join(blocks)
    parsed = parse_outcome(text)
    assert parsed["invalid"] == 5
    found = parsed["blocks"]
    assert [b["result"] for b in found] == ["met", "missed", "unmeasurable"]
    assert found[1]["source"] == "board, 2026-10-08"
    assert found[1]["note"] == "a note line inside"
    assert found[2]["reason"] == "no script"
    assert found[2]["note"] == "A longer note."
    o = unit_outcome(text)
    assert o["result"] == "unmeasurable"
    assert o["deadline"] == "2026-10-07"
    assert o["invalid"] == 5
    assert o["by"] == "Linh"
    assert o["measuredBy"] == "agent"


def test_an_outcome_block_ends_the_answer_above_it_and_is_no_answer():
    def f(id_, text):
        return f"\n### {id_}\nAnswered by: Linh. Date: 2026-09-23. Via: product.\n\n{text}\n"

    out = outcome_block(["Result: đạt", "Measured by: agent", "Source: x"])
    head = f"{OUTCOME_INTENT}\n## Answers\n{answer_block(1, 'Linh', 'Yes.')}"
    plain = f"{head}{f('F2', 'Fine.')}{f('F3', 'Also.')}"
    mixed = f"{head}{out}{f('F2', 'Fine.')}{out}{f('F3', 'Also.')}"
    after = parse_answers(mixed)
    assert after == parse_answers(plain)
    assert not any("Result:" in a["text"] for a in after)
    assert [a["id"] if a["id"] is not None else a["n"] for a in after] == [1, "F2", "F3"]
    assert next(a for a in after if a["id"] == "F2")["text"] == "Fine."


def test_an_outcome_block_changes_nothing_but_outcome(tmp_path):
    d = tmp_path / "u"
    d.mkdir()
    for f in ["idea.md", "spec.md", "impl.md", "pr.md", "review.md", "ship.md"]:
        pr = "PR: https://github.com/o/r/pull/7. Status: accepted.\n"
        (d / f).write_text(pr if f == "pr.md" else "Status: accepted.\n")
    (d / "plan.md").write_text("Status: done.\n")
    (d / "intent.md").write_text(OUTCOME_INTENT)

    def view():
        u = read(d, "0047_x")
        gates = [check_gate(u, s, green_probe()) for s in STAGE_NAMES]
        rest = {k: v for k, v in u.items() if k != "outcome"}
        return u["outcome"], stringify({"rest": rest, "next": next_action(u), "gates": gates})

    before = view()
    assert before[0]["result"] is None
    blocks = outcome_block(["Result: đạt", "Measured by: agent", "Source: x"]) + outcome_block(
        ["Result: ?"]
    )
    (d / "intent.md").write_text(f"{OUTCOME_INTENT}\n## Answers\n{blocks}")
    after = view()
    assert after[0]["result"] == "met"
    assert after[0]["invalid"] == 1
    assert after[1] == before[1]


# --- 0039: spike, only when the spec left a question unmeasured ------------------------------


def spec_text(concerns, status="accepted"):
    return (
        f"# Spec: x\nIntent: intent.md. Author: t. Status: {status}.\n\n## Requirements\n\n"
        f"- [unmeasured] U9. prose, not a concern\n\n## Concerns\n\n{concerns}\n"
    )


def test_parse_unmeasured_reads_items_under_concerns_only():
    assert parse_unmeasured(spec_text("- **C1.** nothing unmeasured here.")) == {
        "ids": [],
        "problems": [],
    }
    two = spec_text("- [unmeasured] U1. does it exit?\n* [unmeasured] U2. how long?")
    assert parse_unmeasured(two)["ids"] == ["U1", "U2"]
    assert parse_unmeasured(spec_text("1. [unmeasured] U3 numbered"))["ids"] == ["U3"]
    prose = spec_text("- C1 says [unmeasured] U1 in passing.\n  - [unmeasured] U2 nested")
    assert parse_unmeasured(prose)["ids"] == []
    assert parse_unmeasured("## Requirements\n\n- [unmeasured] U1. x\n")["ids"] == []


def test_an_unmeasured_item_with_no_id_or_twice_is_a_problem_that_closes_plan(tmp_path):
    no_id = parse_unmeasured(spec_text("- [unmeasured] does it exit?"))
    assert no_id["ids"] == []
    assert 'carries no U<n>: "- [unmeasured] does it exit?"' in no_id["problems"][0]
    twice = parse_unmeasured(spec_text("- [unmeasured] U1. a\n- [unmeasured] U1. b"))
    assert twice["problems"] == ["spec.md: U1 is marked [unmeasured] twice"]
    files = {
        "intent.md": "# Intent: x\nAuthor: t. Type: feat. Status: accepted.\n",
        "spec.md": spec_text("- [unmeasured] does it exit?"),
    }
    u = read(files_in(tmp_path, files), "0039_x")
    assert "carries no U<n>" in " ".join(u["problems"])
    assert "carries no U<n>" in " ".join(check_gate(u, "plan")["need"])
    assert next_action(u)["stage"] == ""
    assert next_action(u)["action"].startswith("fix spec.md — an [unmeasured] item carries no U<n>")


def test_parse_spike_reads_verdicts_and_blocks_and_stops_at_answers():
    text = (
        "Round: 2. Status: accepted.\n\n## U1\n\nVerdict: holds.\n\n```\n$ x\ny\n```\n\n"
        "## U2\n\nno verdict here\n\n```\nout\n```\n\n## U3\n\nVerdict: fails.\n\n```\n```\n\n"
        "## Answers\n\n## U4\n\nVerdict: holds.\n\n```\nz\n```\n"
    )
    assert parse_spike(text) == {
        "round": 2,
        "items": {
            "U1": {"verdict": "holds", "hasBlock": True},
            "U2": {"verdict": None, "hasBlock": True},
            "U3": {"verdict": "fails", "hasBlock": False},
        },
    }
    assert parse_spike("## U1\nVerdict: holds.\n")["round"] is None


INTENT_0039 = "# Intent: x\nAuthor: t. Type: feat. Status: accepted.\n"


def spike_text(rnd, items):
    head = f"# Spike: x\nSpec: spec.md. Author: ᛈ Perthro. Round: {rnd}. Status: accepted.\n\n"
    body = "\n".join(f"## {i}\n\nVerdict: {v}.\n\n```\n$ node -e 1\nok\n```\n" for i, v in items)
    return head + body


def spike_unit(tmp_path, files):
    d = files_in(
        tmp_path, {"intent.md": INTENT_0039, **files}, name=f"s{len(list(tmp_path.iterdir()))}"
    )
    return read(d, "0039_x")


def with_entry(d, name="0039_x"):
    """The snapshot of `d`'s files and its entry, for a test to change before `read_unit`."""
    entry = entry_from(unit_meta(str(d)))
    state = {"workspace": "", "workspaces": [], "units": {f"/{name}": entry}, "ideas": {"": []}}
    return state, entry


def test_a_stage_result_decides_the_specs_ids_and_the_spikes_verdicts(tmp_path):
    files = {
        "intent.md": INTENT_0039,
        "spec.md": spec_text("- **C1.** nothing unmeasured here."),
        "spike.md": spike_text(1, [("U1", "holds")]),
    }
    d = files_in(tmp_path, files)

    def read_(spec, spike=None):
        state, entry = with_entry(d)
        entry["artifacts"]["spec.md"]["result"] = {
            "stage": "spec",
            "judgement": "ready",
            "questions": [],
            **spec,
        }
        if spike:
            entry["artifacts"]["spike.md"]["result"] = {
                "stage": "spike",
                "judgement": "ready",
                "questions": [],
                **spike,
            }
        return read_unit(str(d), "0039_x", state)

    asked_ = read_({"unmeasured": ["U1"]}, {"verdicts": [{"id": "U1", "verdict": "fails"}]})
    assert asked_["artifacts"]["spec.md"]["unmeasured"]["ids"] == ["U1"]
    assert check_gate(asked_, "plan")["ok"] is False
    assert "U1: spike.md measured that it does not hold" in "\n".join(
        check_gate(asked_, "plan")["need"]
    )
    held = read_({"unmeasured": ["U1"]}, {"verdicts": [{"id": "U1", "verdict": "holds"}]})
    assert check_gate(held, "plan")["ok"] is True
    assert "unmeasured" not in read_({"unmeasured": []})["artifacts"]["spec.md"]


def test_a_round_and_the_claims_the_app_holds_decide(tmp_path):
    review = (
        "# Review: x\nStatus: changes-requested.\n\n## Round 1\n\nReviewed: abc1234. "
        "Verdict: changes-requested.\n\n### Findings\n\n- F1 [open] a.py:3 — high — old\n\n"
        "## Round 2\n\nReviewed: abc1234. Verdict: pass.\n\n### Findings\n\nNone.\n"
    )
    impl = "# Impl: x\nStatus: accepted.\n\n## Needs a person\n\n- F2: a login\n"
    d = files_in(tmp_path, {"intent.md": INTENT_0039, "review.md": review, "impl.md": impl})
    state, entry = with_entry(d)

    def row(label, severity="medium"):
        return {
            "id": "F2",
            "label": label,
            "fixedIn": None,
            "severity": severity,
            "rule": "S3",
            "path": "b.py",
            "lines": "9",
            "text": "new",
        }

    entry["artifacts"]["review.md"]["rounds"] = [
        {
            "n": 2,
            "reviewed": "f" * 40,
            "verdict": "changes-requested",
            "screens": {},
            "findings": [row("open")],
        }
    ]
    entry["artifacts"]["impl.md"]["result"] = {
        "stage": "impl",
        "judgement": "ready",
        "questions": [],
        "needs_person": [],
    }
    u = read_unit(str(d), "0039_x", state)
    rounds = u["artifacts"]["review.md"]["review"]["rounds"]
    assert [[r["n"], r["reviewed"], r["verdict"]] for r in rounds] == [
        [1, "abc1234", "changes-requested"],
        [2, "f" * 40, "changes-requested"],
    ]
    assert [[f["id"], f["label"], f["severity"]] for f in rounds[1]["findings"]] == [
        ["F2", "open", "medium"]
    ]
    assert rounds[1]["dropped"] == ["F1"]
    assert "Verdict: pass" in rounds[1]["text"]
    assert u["artifacts"]["impl.md"]["needsPerson"] == []
    entry["artifacts"]["impl.md"]["result"]["needs_person"] = ["F2"]
    again = read_unit(str(d), "0039_x", state)
    assert again["artifacts"]["impl.md"]["needsPerson"] == [{"id": "F2", "reason": "a login"}]
    entry["artifacts"]["review.md"]["rounds"][0]["findings"] = [row("open", "low")]
    assert non_blocking(read_unit(str(d), "0039_x", state)) == []


def test_parse_spike_reads_round_from_the_header_line_only(tmp_path):
    text = (
        "# Spike: x\nSpec: spec.md. Author: ᛈ Perthro. Status: accepted.\n\n"
        "## U1\n\nVerdict: fails.\n\n```\n$ cat notes\nRound: 3\n```\n"
    )
    assert parse_spike(text)["round"] is None
    u = spike_unit(tmp_path, {"spec.md": spec_text("- [unmeasured] U1. a"), "spike.md": text})
    assert next_action(u)["stage"] == "spec"


def test_a_unit_with_nothing_unmeasured_walks_as_if_spike_did_not_exist(tmp_path):
    u = spike_unit(
        tmp_path, {"spec.md": spec_text("- **C1.** none"), "plan.md": "Status: accepted.\n"}
    )
    assert "unmeasured" not in u["artifacts"]["spec.md"]
    assert "citesSpike" not in u["artifacts"]["plan.md"]
    assert check_gate(u, "impl")["ok"] is True
    assert "write-impl" in next_action(u)["action"]
    assert check_gate(u, "spike")["need"] == [
        "spike is not required: spec.md has no [unmeasured] item"
    ]


def test_a_skipped_spec_never_needs_a_spike(tmp_path):
    files = {
        "spec.md": spec_text("- [unmeasured] U1. x", "skipped"),
        "spike.md": spike_text(1, [("U1", "fails")]),
        "plan.md": "Status: accepted.\n",
    }
    u = spike_unit(tmp_path, files)
    assert check_gate(u, "impl")["ok"] is True
    assert "write-impl" in next_action(u)["action"]


def test_an_unmeasured_spec_sends_the_unit_to_spike_and_closes_plan(tmp_path):
    spec = spec_text("- [unmeasured] U1. a\n- [unmeasured] U2. b")
    none = spike_unit(tmp_path, {"spec.md": spec})
    assert none["artifacts"]["spec.md"]["unmeasured"]["ids"] == ["U1", "U2"]
    assert next_action(none)["stage"] == "spike"
    assert check_gate(none, "spike")["ok"] is True
    need = "\n".join(check_gate(none, "plan")["need"])
    assert "spike.md does not exist" in need
    assert "U1: spike.md is missing, not accepted" in need
    spike = spike_text(1, [("U1", "holds")]) + "\n## U2\n\nVerdict: holds.\n"
    no_block = spike_unit(tmp_path, {"spec.md": spec, "spike.md": spike})
    need = check_gate(no_block, "plan")["need"]
    assert len(need) == 1
    assert re.match(r"U2: .* no fenced block", need[0])
    assert next_action(no_block)["stage"] == "spike"
    held = spike_unit(
        tmp_path, {"spec.md": spec, "spike.md": spike_text(1, [("U1", "holds"), ("U2", "holds")])}
    )
    assert check_gate(held, "plan")["ok"] is True
    assert next_action(held)["stage"] == "plan"


def test_spec_spike_spec_again_spike_plan(tmp_path):
    first = spike_unit(
        tmp_path,
        {
            "spec.md": spec_text("- [unmeasured] U1. a\n- [unmeasured] U2. b"),
            "spike.md": spike_text(1, [("U1", "holds"), ("U2", "fails")]),
        },
    )
    assert next_action(first)["stage"] == "spec"
    assert "U2 does not hold" in next_action(first)["action"]
    assert "U2: spike.md measured that it does not hold" in " ".join(
        check_gate(first, "plan")["need"]
    )
    assert check_gate(first, "spec")["ok"] is True
    rewritten = spike_unit(
        tmp_path,
        {
            "spec.md": spec_text("- [unmeasured] U1. a\n- [unmeasured] U3. c"),
            "spike.md": spike_text(1, [("U1", "holds"), ("U2", "fails")]),
        },
    )
    assert next_action(rewritten)["stage"] == "spike"
    assert "does not measure U3" in next_action(rewritten)["action"]
    measured = spike_unit(
        tmp_path,
        {
            "spec.md": spec_text("- [unmeasured] U1. a\n- [unmeasured] U3. c"),
            "spike.md": spike_text(2, [("U1", "holds"), ("U3", "holds")]),
        },
    )
    assert next_action(measured)["stage"] == "plan"
    assert check_gate(measured, "plan")["ok"] is True


def test_fails_ahead_of_missing(tmp_path):
    u = spike_unit(
        tmp_path,
        {
            "spec.md": spec_text("- [unmeasured] U1. a\n- [unmeasured] U2. b"),
            "spike.md": spike_text(1, [("U2", "fails")]),
        },
    )
    assert next_action(u)["stage"] == "spec"


def test_a_question_still_failing_at_the_last_spike_round_needs_a_person(tmp_path):
    u = spike_unit(
        tmp_path,
        {
            "spec.md": spec_text("- [unmeasured] U3. c"),
            "spike.md": spike_text(SPIKE_ROUNDS, [("U3", "fails")]),
        },
    )
    n = next_action(u)
    assert n["stage"] == ""
    assert n["blocked"] is True
    assert n["action"] == (
        f"needs a person — spike round {SPIKE_ROUNDS} of {SPIKE_ROUNDS} found U3 does not hold"
    )


def test_a_spec_rewritten_without_its_questions_needs_no_spike(tmp_path):
    u = spike_unit(
        tmp_path,
        {"spec.md": spec_text("- none left"), "spike.md": spike_text(1, [("U1", "fails")])},
    )
    assert next_action(u)["stage"] == "plan"
    assert check_gate(u, "plan")["ok"] is True
    draft = spike_unit(
        tmp_path, {"spec.md": spec_text("- none left"), "spike.md": "# Spike\nStatus: draft.\n"}
    )
    assert next_action(draft)["stage"] == "plan"
    assert check_gate(draft, "plan")["ok"] is True
    assert check_gate(draft, "spike")["ok"] is False


def skip_tree(tmp_path, authority, files=None):
    """`spec.md` skipped, and the snapshot saying whose decision the skip was."""
    files = {"spec.md": "# Spec\nStatus: skipped.\n"} if files is None else files
    d = files_in(
        tmp_path, {"intent.md": INTENT_0039, **files}, name=f"k{len(list(tmp_path.iterdir()))}"
    )
    state, e = with_entry(d)
    spec = {
        "raw": None,
        "questions": None,
        **e["artifacts"].get("spec.md", {}),
        "status": "skipped",
    }
    if authority is not None:
        spec["authority"] = authority
    else:
        spec.pop("authority", None)
    e["artifacts"]["spec.md"] = spec
    return read_unit(str(d), "0039_x", state)


def test_a_skip_no_person_decided_stops_the_unit_for_a_person(tmp_path):
    for authority in ["agent", "code", "unknown", None]:
        u = skip_tree(tmp_path, authority)
        assert u["artifacts"]["spec.md"]["agentSkip"] == {"by": authority}
        n = next_answer(u)
        assert n["stage"] == "", authority
        assert n["blocked"] is True
        assert n["reasons"] == ["agent-cannot-skip"]
        assert re.search(
            r"spec\.md is skipped by .*, not by a person — a person records "
            r"the skip \(coscc skip\), or runs write-spec",
            n["action"],
        )
        gate = gate_answer(u, "plan")
        assert gate["ok"] is False
        assert gate["reasons"] == ["agent-cannot-skip"]
        assert "spec.md is skipped by" in "\n".join(gate["need"])


def test_a_skip_a_person_decided_opens_plan_with_or_without_a_file(tmp_path):
    for authority in ["person"]:
        written = skip_tree(tmp_path, authority)
        assert "agentSkip" not in written["artifacts"]["spec.md"]
        assert next_action(written)["stage"] == "plan"
        assert check_gate(written, "plan")["ok"] is True
        recorded = skip_tree(tmp_path, authority, {})
        assert recorded["artifacts"]["spec.md"] == {"status": "skipped", "skipReason": None}
        assert [p for p in recorded["problems"] if "spec.md" in p] == []
        assert check_gate(recorded, "plan")["ok"] is True
    bare = skip_tree(tmp_path, "agent", {})
    assert "spec.md" not in bare["artifacts"]
    assert "the app records spec.md as skipped, but the file does not exist" in "\n".join(
        bare["problems"]
    )


def test_with_a_spike_required_impl_opens_only_on_a_plan_citing_it(tmp_path):
    files = {
        "spec.md": spec_text("- [unmeasured] U1. a"),
        "spike.md": spike_text(1, [("U1", "holds")]),
    }
    silent = spike_unit(tmp_path, {**files, "plan.md": "Status: accepted.\n\n1. build it\n"})
    assert silent["artifacts"]["plan.md"]["citesSpike"] is False
    assert check_gate(silent, "impl")["need"] == [
        "plan.md does not cite spike.md — every step that rests on a U<n> cites spike.md ## U<n>"
    ]
    cites = spike_unit(
        tmp_path, {**files, "plan.md": "Status: accepted.\n\n1. build it (spike.md ## U1)\n"}
    )
    assert check_gate(cites, "impl")["ok"] is True


# --- 0045: a person pauses or drops a unit -------------------------------------------------

HELD_INTENT = (
    "# Intent: x\nType: feat. Status: accepted.\n\n## Open questions\n\n1. Một?\n2. Hai?\n\n"
    "## Answers\n"
)


def hold_block(head, reason, by="Leif"):
    return f"\n### {head}\nDecided by: {by}. Date: 2026-09-24. Via: product.\n\n{reason}\n"


def parse_hold(text):
    return fold_holds(hold_blocks(text))


def held_tree(tmp_path, tail, extra=None):
    root = tmp_path / f"h{len(list(tmp_path.iterdir()))}"
    d = root / ".cos" / "0001_held"
    d.mkdir(parents=True)
    (d / "intent.md").write_text(HELD_INTENT + tail)
    for f, text in (extra or {}).items():
        (d / f).write_text(text)
    return SimpleNamespace(
        root=root, u=read(d, "0001_held"), cli=lambda *a: cli(*a, "--root", str(root))
    )


def test_a_hold_block_ends_the_answer_before_it():
    text = HELD_INTENT + answer_block(2, "A", "Tách ra.") + hold_block("Paused", "chờ 0034")
    answers = parse_answers(text)
    assert len(answers) == 1
    assert answers[0]["text"] == "Tách ra."
    assert parse_hold(text)["hold"]["reason"] == "chờ 0034"


def test_no_hold_block_is_no_hold_and_the_active_moves(tmp_path):
    u = held_tree(tmp_path, answer_block(1, "A", "x")).u
    assert u["hold"] is None
    assert u["holdMoves"] == ["paused", "dropped"]
    assert u["problems"] == []


def test_hold_blocks_are_read_in_order_and_the_last_valid_one_decides():
    def walk(*heads):
        return parse_hold(
            HELD_INTENT + "".join(hold_block(h, f"r{i}") for i, h in enumerate(heads))
        )

    assert walk("Paused")["hold"]["state"] == "paused"
    assert walk("Paused", "Resumed")["hold"] is None
    assert walk("Dropped")["hold"]["state"] == "dropped"
    assert walk("Paused", "Dropped")["hold"]["state"] == "dropped"
    assert walk("Dropped", "Paused")["hold"]["reason"] == "r1"
    assert walk("Dropped", "Paused", "Resumed") == {"hold": None, "problems": []}


def test_an_invalid_hold_move_is_ignored_and_reported(tmp_path):
    resumed = parse_hold(HELD_INTENT + hold_block("Dropped", "bỏ") + hold_block("Resumed", "lại"))
    assert resumed["hold"]["state"] == "dropped"
    msg = "hold block 2 (### Resumed) is not a valid move from dropped — it is ignored"
    assert resumed["problems"] == [msg]
    twice = parse_hold(HELD_INTENT + hold_block("Paused", "a") + hold_block("Paused", "b"))
    assert twice["hold"]["reason"] == "a"
    assert len(twice["problems"]) == 1
    assert len(parse_hold(HELD_INTENT + hold_block("Resumed", "x"))["problems"]) == 1
    u = held_tree(tmp_path, hold_block("Dropped", "bỏ") + hold_block("Resumed", "lại")).u
    assert u["problems"] == [f"intent.md: {msg}"]


def test_a_hold_block_without_a_decided_by_line_is_not_counted():
    assert parse_hold(f"{HELD_INTENT}\n### Paused\n\nkhông ai ký\n")["hold"] is None
    assert parse_hold(f"{HELD_INTENT}\n### Paused\nDecided by: A\n\nthiếu ngày\n")["hold"] is None
    assert parse_hold(f"{HELD_INTENT}\n### Paused\n")["problems"] == []


def test_next_offers_no_stage_and_asks_no_probe_when_held(tmp_path):
    def boom(*a):
        raise AssertionError("the probe was asked")

    probe = SimpleNamespace(gh=boom, git=boom)
    later = {
        "spec.md": "Status: accepted.\n",
        "plan.md": "Status: accepted.\n",
        "impl.md": "Status: accepted.\n",
        "pr.md": "PR: https://github.com/o/r/pull/7. Status: accepted.\n",
    }
    paused = held_tree(tmp_path, hold_block("Paused", "chờ người"), later)
    n = next_step(paused.u, probe)
    assert n["stage"] == ""
    assert n["blocked"] is True
    assert n["action"] == "paused — chờ người (Leif, 2026-09-24) — resume it from the board"
    dropped = held_tree(tmp_path, hold_block("Dropped", "không đáng"), later)
    assert next_step(dropped.u, probe) == {
        "blocked": False,
        "action": "dropped — không đáng (Leif, 2026-09-24)",
        "stage": "",
    }
    assert between_pr_and_ship(paused.u) is False
    assert between_pr_and_ship(dropped.u) is False
    said = json.loads(paused.cli("next", "0001_held").out)
    assert said["stage"] == ""
    assert said["hold"] == {
        "state": "paused",
        "reason": "chờ người",
        "by": "Leif",
        "date": "2026-09-24",
    }


def test_every_gate_is_closed_on_a_held_unit_and_says_why(tmp_path):
    h = held_tree(tmp_path, hold_block("Paused", "chờ 0034"), {"spec.md": "Status: accepted.\n"})
    for s in STAGE_NAMES:
        g = check_gate(h.u, s, green_probe())
        assert g["ok"] is False, s
        assert g["need"] == ["the unit is paused: chờ 0034 (Leif, 2026-09-24)"], s
        r = h.cli("gate", "0001_held", s)
        assert r.code == 1, s
        assert "the unit is paused: chờ 0034" in r.err
    assert check_gate(h.u, "nope")["need"][0].startswith("unknown stage")


def test_a_done_plan_or_a_rejection_wins_over_a_hold(tmp_path):
    files = {"spec.md": "Status: accepted.\n", "plan.md": "Status: done.\n"}
    done = held_tree(tmp_path, hold_block("Paused", "x"), files).u
    assert done["hold"] is None
    assert done["holdMoves"] == []
    assert (
        "intent.md carries a hold block, but the unit is finished — it is ignored"
        in done["problems"]
    )
    assert next_action(done)["action"] == "finished"
    closed = held_tree(tmp_path, hold_block("Dropped", "x"), {"spec.md": "Status: rejected.\n"}).u
    assert closed["hold"] is None
    assert closed["holdMoves"] == []
    assert (
        "intent.md carries a hold block, but the unit is closed — it is ignored"
        in closed["problems"]
    )


def test_a_unit_with_no_intent_has_nowhere_to_hold(tmp_path):
    u = read(files_in(tmp_path, {"idea.md": "# Idea\nStatus: accepted.\n"}), "0001_x")
    assert u["hold"] is None
    assert u["holdMoves"] == []


# --- 0061: a finding that does not block -----------------------------------------------------


def low(id_, label="open", what="x"):
    return f"- {id_} [{label}] a.py:3 — low — {what}"


def rated(id_, severity, label="open"):
    return f"- {id_} [{label}] a.py:3 — {severity} — x"


def test_severity_is_read_only_between_two_em_dashes_after_the_location():
    findings = [
        "- F1 [open] a.py:3 — Mức thấp — x",
        "- F2 [open] a.py:3 - low - x",
        "- F3 [open] a.py:3 – low – x",
        "- F4 [open] a.py:3 — HIGH — x",
        "- F5 [open] a.py:3 — medium — x",
        f"- F6 [fixed {FIX}] a.py:3 — low — x",
        "- F7 [open] no location — low",
        "- F8 [open] a.py:3 x — low — y",
    ]
    r = parse_review(round_(1, "changes-requested", findings))["rounds"][0]
    assert [[f["id"], f["severity"]] for f in r["findings"]] == [
        ["F1", None],
        ["F2", None],
        ["F3", None],
        ["F4", "high"],
        ["F5", "medium"],
        ["F6", "low"],
        ["F7", None],
        ["F8", None],
    ]


def test_an_open_low_does_not_block_unless_rated_higher_before():
    one = asked(round_(1, "pass", [low("F1"), rated("F2", "medium"), "- F3 [open] a.py:3 x"]))
    assert non_blocking(one) == [{"id": "F1", "text": "a.py:3 — low — x"}]
    r1 = round_(1, "changes-requested", [rated("F1", "high")])
    assert non_blocking(asked(f"{r1}\n{round_(2, 'pass', [low('F1')])}")) == []
    prose = round_(1, "changes-requested", ["- F1 [open] a.py:3 — Mức thấp — x"])
    got = non_blocking(asked(f"{prose}\n{round_(2, 'pass', [low('F1')])}"))
    assert [f["id"] for f in got] == ["F1"]
    for label in ["needs-person", "claim-rejected", "answered", "maybe"]:
        assert non_blocking(asked(round_(1, "needs-person", [low("F1", label)]))) == [], label
    assert non_blocking(unit(CHAIN)) == []


def test_ship_lets_an_open_low_through_and_nothing_else():
    def gate(findings):
        return check_gate(ship(round_(1, "pass", findings)), "ship", green_probe())

    assert gate([low("F1")])["ok"] is True
    for findings in ([rated("F1", "medium")], ["- F1 [open] a.py:3 x"]):
        g = gate(findings)
        assert g["ok"] is False
        assert "F1 [open]" in "\n".join(g["need"])


def test_a_severity_lowered_between_rounds_closes_ship():
    text = (
        f"{round_(1, 'changes-requested', [rated('F1', 'high')])}\n{round_(2, 'pass', [low('F1')])}"
    )
    g = check_gate(ship(text), "ship", green_probe())
    assert g["ok"] is False
    assert (
        "F1 is low in review round 2, but review round 1 rated it high — lowering a severity is "
        "not a fix: fix it on the branch, or keep it open"
    ) in g["need"]


def test_next_step_never_offers_a_closed_gate_in_the_person_states(tmp_path):
    reviews = [
        R12,
        f"{R12}\n{round3()}",
        f"{R12}\n{round3()}\n## Answers\n{f_block('F2')}{f_block('F3')}",
    ]
    probes = [
        green_probe(),
        green_probe([{"name": "t", "bucket": "fail"}]),
        green_probe([{"name": "t", "bucket": "pending"}]),
    ]
    for probe in probes:
        for review in reviews:
            u = tree_after_round_two(tmp_path, review)
            stage = next_step(u, probe)["stage"]
            if stage:
                assert check_gate(u, stage, probe)["ok"] is True, stage


def test_new_path_takes_the_highest_and_writes_nothing(tmp_path):
    root = cos_tree(tmp_path, "0003_c")
    host = cos_tree(tmp_path, "0014_n")
    out = cli("--root", str(root), "new-path", "x", "--reserve-from", str(host))
    assert out.out.strip() == ".cos/0015_x"
    assert [p.name for p in (root / ".cos").iterdir()] == ["0003_c"]
    assert [p.name for p in (host / ".cos").iterdir()] == ["0014_n"]


def cos_tree(tmp_path: Path, *names: str) -> Path:
    """A directory with a `.cos/` holding the named units."""
    d = tmp_path / f"tree{len(list(tmp_path.iterdir()))}"
    (d / ".cos").mkdir(parents=True)
    for n in names:
        (d / ".cos" / n).mkdir()
    return d
