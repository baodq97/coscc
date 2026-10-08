"""The loop's gates, readers and branch grammar, called directly, each held to fixed values.

A unit read from files gets the snapshot the app would build of its rows, stated by each test
with `known`: the files' headers decide nothing.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from coscc.loop import BRANCH_TYPES, proc_of
from coscc.loop.branch import (
    VERSION_SOURCE,
    is_prerelease,
    tag_problem,
    version_problem,
)
from coscc.loop.model import (
    branch_for,
    branch_problem,
    read_unit,
)
from coscc.loop.paths import next_number
from coscc.loop import SPIKE_ROUNDS
from coscc.loop.model import fold_holds, non_blocking, rounds_used, screens_spared
from coscc.loop.model import standard_findings
from coscc.loop.rules import between_pr_and_ship, decide, gate_answer, next_answer
from coscc.loop.run import ask
from coscc.loop.model import review_from, review_rounds
from coscc.loop import REVIEW_ROUNDS
from tests.loop.conftest import finding_row, pr_row, python, round_row

STAGE_NAMES = proc_of(None).names

# --- the suite's glue -------------------------------------------------------------------


def next_action(unit, limit=REVIEW_ROUNDS):
    return {k: v for k, v in decide(unit, limit).items() if k not in ("why", "rerun", "continue")}


def check_gate(unit, stage, probe=None, limit=REVIEW_ROUNDS):
    return {k: v for k, v in gate_answer(unit, stage, probe, limit).items() if k != "reasons"}


def next_step(unit, probe=None, limit=REVIEW_ROUNDS):
    return {k: v for k, v in next_answer(unit, probe, limit).items() if k != "reasons"}


_BASE = {"judgement": "ready", "questions": []}


def spec_record(*ids: str) -> dict:
    """The spec's submitted record, naming `ids` unmeasured."""
    return {"spec.md": {"result": {**_BASE, "unmeasured": list(ids)}}}


def spike_record(rnd: int | None = 1, **verdicts: str) -> dict:
    """The spike's submitted record, a verdict per id, with the round the app counted."""
    found = [{"id": i, "verdict": v} for i, v in verdicts.items()]
    return {"spike.md": {"round": rnd, "result": {**_BASE, "verdicts": found}}}


def claim_record(*ids: str) -> dict:
    """impl's submitted record, claiming `ids` only a person can close."""
    return {"impl.md": {"result": {**_BASE, "needs_person": list(ids)}}}


def plan_record(*rests_on: str) -> dict:
    """The plan's submitted record, resting on the spike items `rests_on`."""
    return {"plan.md": {"result": {**_BASE, "rests_on": list(rests_on)}}}


def answer(file: str, ref: int | str, by: str, text: str, date: str = "2026-09-23") -> dict:
    """A person's answer row: to question `ref` (a number) or finding `ref` (`F<n>`)."""
    number = isinstance(ref, int)
    return {
        "artifact": file,
        "n": ref if number else None,
        "id": None if number else ref,
        "by": by,
        "date": date,
        "via": "product",
        "text": text,
    }


def hold(state: str, reason: str, by: str = "Leif", date: str = "2026-09-24") -> dict:
    """A hold row: `paused`, `dropped` or `active` (resumed)."""
    return {"state": state, "reason": reason, "by": by, "date": date, "via": "product"}


def known(
    statuses: dict[str, str] | None = None,
    *,
    type: str | None = "feat",
    questions: dict[str, list[str]] | None = None,
    answers: list[dict] | None = None,
    holds: list[dict] | None = None,
    records: dict | None = None,
    links: dict | None = None,
) -> dict:
    """The snapshot entry the app builds of a unit's rows: each file's status, `{file: status}`
    (a skip is a person's); the open questions each record handed back, `{file: [text]}`
    numbered from 1; answer and hold rows; the submitted records, `{file: {"result", ...}}`."""
    arts: dict[str, dict] = {
        f: {
            "status": st,
            "questions": [
                {"n": i, "text": t} for i, t in enumerate((questions or {}).get(f, []), 1)
            ],
            **({"authority": "person"} if st == "skipped" else {}),
        }
        for f, st in (statuses or {}).items()
    }
    for f, qs in (questions or {}).items():
        arts.setdefault(f, {"status": None})["questions"] = [
            {"n": i, "text": t} for i, t in enumerate(qs, 1)
        ]
    for f, rec in (records or {}).items():
        arts.setdefault(f, {"status": None, "questions": None}).update(rec)
    return {
        "artifacts": arts,
        "type": type,
        "links": links or {"idea": None, "dependsOn": None},
        "holds": holds or [],
        "answers": answers or [],
        "unknowns": [],
        "merged": (statuses or {}).get("ship.md") == "accepted",
    }


def state_of_root(root: Path, units: dict[str, dict] | None = None) -> dict:
    """The snapshot the app would hand `--state` for `<root>`: `units`, a `known` entry by name."""
    return {
        "workspace": "",
        "workspaces": [],
        "units": {f"/{n}": e for n, e in (units or {}).items()},
    }


def read(dir_: Path, name: str, entry: dict | None = None) -> dict:
    """`read_unit` of `dir_` under `name`, with `entry` (a `known`) as the app's snapshot."""
    return read_unit(str(dir_), name, state_of_root(dir_, {name: entry or known()}))


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


def cli(*argv: str, stdin: str | None = None, units: dict[str, dict] | None = None):
    """`python -m coscc.loop argv`; a deciding command gets `units` (`known` entries by name) as
    the snapshot of its `--root`."""
    words = list(argv)
    deciding = {"status", "gate", "next", "rerun", "unit-branch", "screens"}
    if "--root" in words and deciding & set(words) and "--state" not in words:
        root = Path(words[words.index("--root") + 1])
        return python([*words, "--state", "-"], stdin=json.dumps(state_of_root(root, units)))
    return python(words, stdin=stdin)


# --- the status line, the gates and the next action -------------------------------------


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
    assert check_gate(unit(FULL), "impl")["ok"] is True
    assert check_gate(unit({**FULL, "plan.md": art("draft")}), "impl")["ok"] is False
    assert check_gate(unit({**FULL, "intent.md": art("draft")}), "impl")["ok"] is False


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


def test_a_shipped_unit_is_finished():
    assert next_action({**unit(FULL), "shipped": True})["action"] == "finished"


def test_next_number_counts_from_the_highest():
    assert next_number([]) == "0001"
    assert next_number([1, 2]) == "0003"
    assert next_number([7]) == "0008"
    assert next_number([3, 1]) == "0004"


def test_an_unreadable_artifact_is_not_a_missing_one():
    broken = unit({"intent.md": {"status": None, "skipReason": None}})
    assert (
        "has no status: no record was handed back for it" in check_gate(broken, "spec")["need"][0]
    )
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


def with_intent(tmp_path: Path, type_: str | None) -> dict:
    d = files_in(tmp_path, {"intent.md": "# Intent: x\n"}, name=str(type_))
    return read(d, "0011_typed", known({"intent.md": "accepted"}, type=type_))


def test_an_intent_with_no_type_is_a_problem(tmp_path):
    u = with_intent(tmp_path, None)
    assert u["problems"] == ["the intent has handed back no type"]
    assert "type" not in u


def test_a_type_outside_the_ten_is_another_problem(tmp_path):
    u = with_intent(tmp_path, "nonsense")
    assert len(u["problems"]) == 1
    assert 'has type "nonsense", not one of' in u["problems"][0]
    for t in BRANCH_TYPES:
        assert t in u["problems"][0]


# --- pre-intent ---------------------------------------------------------------------------


def test_a_unit_holding_only_a_valid_idea_is_pre_intent(tmp_path):
    d = files_in(tmp_path, {"idea.md": "# Idea: x\n"})
    u = read(d, "0015_fresh", known({"idea.md": "accepted"}))
    assert u["phase"] == "pre-intent"
    assert u["problems"] == []
    assert next_action(u) == {
        "blocked": True,
        "action": "write-intent — the unit has no intent.md",
        "stage": "intent",
    }
    assert check_gate(u, "spec")["ok"] is False


def test_an_idea_with_a_later_artifact_and_no_intent_is_a_problem(tmp_path):
    files = {"idea.md": "# Idea\n", "spec.md": "# Spec\n"}
    u = read(
        files_in(tmp_path, files), "0015_x", known({"idea.md": "accepted", "spec.md": "draft"})
    )
    assert u["phase"] == "started"
    assert "no intent.md — every unit opens with one" in u["problems"]


def test_an_idea_with_no_status_line_is_not_pre_intent(tmp_path):
    u = read(files_in(tmp_path, {"idea.md": "# Idea: x\n"}), "0015_x")
    assert u["phase"] == "started"
    assert "no intent.md — every unit opens with one" in u["problems"]
    assert "idea.md has no status: no record was handed back for it" in u["problems"]


def test_a_unit_with_an_intent_is_started(tmp_path):
    d = files_in(tmp_path, {"intent.md": "# Intent\n"})
    u = read(d, "0015_x", known({"intent.md": "draft"}, type="fix"))
    assert u["phase"] == "started"


def test_a_type_problem_closes_no_gate():
    u = {**unit({"intent.md": art("accepted")}), "problems": ["the intent has handed back no type"]}
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


def unit_branch(name, type_):
    """What `unit-branch` derives: the unit's name and the type its intent handed back."""
    return branch_for(name, type_)


def test_the_branch_name_is_derived_from_the_unit_and_its_type():
    got = unit_branch("0009_branch-and-release-conventions", "feat")
    assert got == {"branch": "feat/branch-and-release-conventions"}
    assert branch_problem(got["branch"]) is None


def test_a_derived_name_no_grammar_accepts_cannot_be_produced():
    assert '"feature" is not one of' in unit_branch("0009_x", "feature")["error"]
    assert "the intent has handed back no type" in unit_branch("0009_x", None)["error"]
    assert "does not match NNNN_<slug>" in unit_branch("9_x", "feat")["error"]
    assert "does not match NNNN_<slug>" in unit_branch(None, "feat")["error"]


def slug_of(n: int) -> str:
    """A slug of exactly `n` characters, made of short words the way a real one is."""
    s = ("abcd-" * -(-n // 5))[:n]
    return s[:-1] + "e" if s.endswith("-") else s


OLD_0044 = "0044_open-questions-wait-for-the-originator-even-when-precedent-answers-them"


def test_a_long_slug_is_cut_at_its_last_hyphen():
    assert unit_branch(OLD_0044, "feat")["branch"] == (
        "feat/open-questions-wait-for-the-originator-even-when-precedent"
    )
    assert len(slug_of(60)) == 60
    assert unit_branch(f"0001_{slug_of(60)}", "feat")["branch"] == f"feat/{slug_of(60)}"
    assert unit_branch("0001_" + "a" * 71, "fix")["branch"] == "fix/" + "a" * 60
    assert unit_branch("0001_" + "a" * 60 + "-b", "fix")["branch"] == "fix/" + "a" * 60


# --- the --root boundary, through the command line --------------------------------------


# --- 0016, 0109: the numbered questions under Open questions, and their answers -------------

Q3 = ["**First?** Asked here and continued on this line.", "Second?", "Third?"]
INTENT_Q = "# Intent: q\n"


def question_tree(
    tmp_path: Path, files: dict[str, str], entry: dict | None = None
) -> tuple[Path, dict]:
    root = tmp_path / "qroot"
    d = root / ".cos" / "0001_q"
    d.mkdir(parents=True)
    for f, text in files.items():
        (d / f).write_text(text)
    return root, read(d, "0001_q", entry)


def ns(qs):
    return [q["n"] for q in qs]


def test_a_note_keeps_no_answer_and_a_heading_with_none_is_counted(tmp_path):
    entry = known(
        {"intent.md": "accepted", "spec.md": "accepted"},
        type="fix",
        questions={"intent.md": ["A?"]},
        answers=[answer("intent.md", 1, "A", "có"), answer("intent.md", 2, "A", "đã đọc")],
    )
    _, u = question_tree(tmp_path, {"intent.md": INTENT_Q, "spec.md": "# Spec\n"}, entry)
    qs = u["artifacts"]["intent.md"]["questions"]
    assert [[q["n"], q["answered"]] for q in qs] == [[1, True]]
    assert u["counted"] == "spec.md"
    assert u["open"] == 0


def test_three_questions_and_no_answers_is_three_open(tmp_path):
    entry = known({"intent.md": "accepted"}, questions={"intent.md": Q3})
    _, u = question_tree(tmp_path, {"intent.md": INTENT_Q}, entry)
    assert u["open"] == 3
    assert len(u["questions"]) == 3
    assert u["problems"] == []


def test_one_answer_leaves_two_open_and_says_who_gave_it(tmp_path):
    entry = known(
        {"intent.md": "accepted"},
        questions={"intent.md": Q3},
        answers=[answer("intent.md", 2, "Phong Pham", "Tách ra.")],
    )
    _, u = question_tree(tmp_path, {"intent.md": INTENT_Q}, entry)
    assert u["open"] == 2
    q2 = next(q for q in u["artifacts"]["intent.md"]["questions"] if q["n"] == 2)
    assert q2["answered"] is True
    assert q2["answer"]["by"] == "Phong Pham"
    assert q2["answer"]["via"] == "product"
    assert q2["answer"]["text"] == "Tách ra."


def test_of_two_answers_for_one_number_the_last_is_in_force(tmp_path):
    entry = known(
        {"intent.md": "accepted"},
        questions={"intent.md": Q3},
        answers=[answer("intent.md", 1, "A", "cũ"), answer("intent.md", 1, "B. C", "mới")],
    )
    _, u = question_tree(tmp_path, {"intent.md": INTENT_Q}, entry)
    q1 = next(q for q in u["artifacts"]["intent.md"]["questions"] if q["n"] == 1)
    assert q1["answer"]["text"] == "mới"
    assert q1["answer"]["by"] == "B. C"
    assert u["open"] == 2


def test_only_the_latest_artifact_with_questions_is_counted(tmp_path):
    entry = known(
        {"intent.md": "accepted", "spec.md": "accepted"},
        questions={"intent.md": Q3, "spec.md": ["Only one?"]},
    )
    _, u = question_tree(tmp_path, {"intent.md": INTENT_Q, "spec.md": "# Spec\n"}, entry)
    assert u["open"] == 1
    assert u["counted"] == "spec.md"
    assert [q["artifact"] for q in u["questions"]] == ["intent.md"] * 3 + ["spec.md"]
    assert [q["counted"] for q in u["questions"]] == [False, False, False, True]


def test_an_open_question_closes_no_gate(tmp_path):
    entry = known({"intent.md": "accepted"}, questions={"intent.md": Q3})
    _, u = question_tree(tmp_path, {"intent.md": INTENT_Q}, entry)
    assert u["open"] == 3
    assert check_gate(u, "spec")["ok"] is True


def test_status_json_over_64_kib_reaches_a_pipe_whole(tmp_path):
    """Read asynchronously, the way the board reads it."""
    entry = known({"intent.md": "accepted"}, questions={"intent.md": [*Q3, f"{'x' * 200 * 1024}?"]})
    root, _ = question_tree(tmp_path, {"intent.md": INTENT_Q}, entry)
    stdin = json.dumps(state_of_root(root, {"0001_q": entry}))
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


def review_art(status, rounds):
    """A review.md the app holds `rounds` (`round_` rows) of."""
    return {**art(status), "review": review_from(list(rounds))}


def round_(n, verdict, findings=(), reviewed=SHA, screens=None):
    return round_row(n, verdict, reviewed, *findings, screens=screens)


def fr(id_, label="open", text="x", severity="high", **more):
    """A finding row; `path`, `lines`, `rule`, `fixed_in` in `more`."""
    return finding_row(
        id_,
        label,
        severity,
        text,
        more.get("path", "a.py"),
        more.get("lines", "3"),
        more.get("rule", ""),
        more.get("fixed_in"),
    )


def fx(id_, text="x", severity="high"):
    """A finding fixed at `FIX`."""
    return fr(id_, "fixed", text, severity, fixed_in=FIX)


def branched(artifacts):
    return {**unit(artifacts), "name": "0001_x", "branch": "feat/x"}


def test_the_review_gate_is_closed_while_no_pull_request_is_recorded():
    g = check_gate(unit({**CHAIN, "pr.md": art("accepted")}), "review", green_probe())
    assert g["ok"] is False
    assert "no pull request is recorded" in g["need"][0]


def test_changes_requested_and_rejected_lead_to_different_places():
    cr = review_art("changes-requested", [round_(1, "changes-requested", [fr("F1", "open", "x")])])
    asked = next_action(unit({**CHAIN, "review.md": cr}))
    assert asked["blocked"] is True
    assert re.search(
        r"fix the open findings of review round 1 .* \(1 of 3 rounds used\)", asked["action"]
    )
    rejected = next_action(unit({**CHAIN, "review.md": art("rejected")}))
    assert rejected["blocked"] is False
    assert "closed — review rejected" in rejected["action"]


def test_changes_requested_closes_ship_but_not_review():
    cr = review_art("changes-requested", [round_(1, "changes-requested", [fr("F1", "open", "x")])])
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
    rounds = [round_(n, "changes-requested", [fr("F1", "open", "x")]) for n in (1, 2, 3)]
    u = unit({**CHAIN, "review.md": review_art("changes-requested", rounds)})
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


def ship(*rounds, status="accepted"):
    return branched({**CHAIN, "review.md": review_art(status, rounds)})


def test_an_accepted_review_with_a_finding_open_cannot_ship():
    g = check_gate(ship(round_(1, "pass", [fr("F1", "open", "the thing")])), "ship", green_probe())
    assert g["ok"] is False
    assert "F1 [open]" in "\n".join(g["need"])


def test_an_earlier_round_survives_and_a_dropped_or_renumbered_one_is_caught():
    r1 = round_(1, "changes-requested", [fr("F1", "open", "x")])
    good = [r1, round_(2, "pass", [fx("F1")])]
    assert check_gate(ship(*good), "ship", green_probe())["ok"] is True
    dropped = [r1, round_(2, "pass")]
    assert re.search(
        r"drops findings .*F1",
        "\n".join(check_gate(ship(*dropped), "ship", green_probe())["need"]),
    )
    gap = [r1, round_(3, "pass", [fx("F1")])]
    assert "numbered 1, 3" in "\n".join(check_gate(ship(*gap), "ship", green_probe())["need"])


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
    r1 = round_(1, "changes-requested", [fr("F1", "open", "x")])
    r2 = round_(2, "pass", [fx("F1", "x")])
    g = check_gate(ship(r1, r2), "ship", after)
    assert g["ok"] is False
    assert (
        "rewritten after the pass (a rebase does this): review its new head in another round"
        in g["need"][0]
    )
    r3 = round_(3, "pass", [fx("F1", "x")], reviewed=reb)
    opened = check_gate(ship(r1, r2, r3), "ship", green_probe(None, {}, rebased))
    assert opened["ok"] is True
    assert opened["head"] == reb
    r3cr = round_(3, "changes-requested", [fx("F1", "x"), fr("F2", "open", "y")])
    u = unit({**CHAIN, "review.md": review_art("changes-requested", [r1, r2, r3cr])})
    assert "2 of 3 rounds used" in next_action(u)["action"]
    assert check_gate(u, "review", green_probe())["ok"] is True


def test_a_review_md_with_no_rounds_cannot_ship():
    u = ship()
    assert "no ## Round" in check_gate(u, "ship", green_probe())["need"][0]


# --- 0024: the stage a run button offers ---------------------------------------------------

HEAD2 = "e" * 40


def asked(*rounds):
    rounds = rounds or [round_(1, "changes-requested", [fr("F1", "open", "x")])]
    return branched({**CHAIN, "review.md": review_art("changes-requested", rounds)})


def moved_to(files, checks=None):
    diff = {f"diff --name-only {SHA}..{HEAD2}": ok("\n".join(files))}
    return green_probe(checks, diff, {"state": "OPEN", "headRefOid": HEAD2})


def passed(findings=()):
    return branched({**CHAIN, "review.md": review_art("accepted", [round_(1, "pass", findings)])})


def test_next_action_stage_names_the_missing_stage_or_nothing():
    assert next_action(unit({"intent.md": art("accepted")}))["stage"] == "spec"
    assert next_action(unit(CHAIN))["stage"] == "review"
    assert next_action(unit({"intent.md": art("draft")}))["stage"] == ""
    assert next_action(unit({"intent.md": art("rejected")}))["stage"] == ""
    assert next_action(unit({"intent.md": art(None)}))["stage"] == ""
    cr = review_art("changes-requested", [round_(1, "changes-requested", [fr("F1", "open", "x")])])
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
    rounds = [round_(n, "changes-requested", [fr("F1", "open", "x")]) for n in (1, 2, 3)]
    n = next_step(asked(*rounds), moved_to(["src/a.py"]))
    assert n["stage"] == ""
    assert "needs a person — review used 3 of 3 rounds" in n["action"]
    assert next_step(asked(*rounds), moved_to(["src/a.py"]), 4)["stage"] == "review"


def test_a_pass_with_the_ship_gate_open_is_ship_pinned():
    n = next_step(passed(), green_probe())
    assert n["stage"] == "ship"
    assert n["action"].startswith(f"ship — merge with --match-head-commit {SHA}")


def test_a_shipped_unit_offers_nothing_whatever_the_later_files_say():
    cr = review_art("changes-requested", [round_(1, "changes-requested", [fr("F1", "open", "x")])])
    u = {**unit({**CHAIN, "review.md": cr}), "shipped": True}
    assert next_step(u, green_probe()) == {"blocked": False, "action": "finished", "stage": ""}


def test_a_branch_moved_after_the_pass_is_review_again():
    n = next_step(passed(), moved_to(["src/a.py"]))
    assert n["stage"] == "review"
    assert "changed after the reviewed commit" in n["action"]
    red = moved_to(["src/a.py"], [{"name": "tests", "bucket": "fail"}])
    assert next_step(passed(), red)["stage"] == "impl"
    assert next_step(passed([fr("F1", "open", "x")]), green_probe())["stage"] == ""


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

ROUND1 = round_(
    1, "changes-requested", [fr("F1", "open", "a"), fr("F2", "open", "b"), fr("F3", "open", "c")]
)
ROUND2 = round_(
    2, "changes-requested", [fx("F1", "a"), fr("F2", "open", "b"), fr("F3", "open", "c")]
)


def impl_text(needs=""):
    return (
        "# Impl: x\nIntent: intent.md. Plan: plan.md. Author: t. Status: accepted.\n\n"
        f"## What was built\n\nx\n\n{needs}"
    )


def f_ans(*ids):
    """A person's answer rows to the findings `ids`."""
    return [answer("review.md", i, "Bao", "ran it", "2026-09-24") for i in ids]


def round3(f3="needs-person", extra=()):
    return round_(
        3,
        "needs-person",
        [fx("F1", "a"), fr("F2", "needs-person", "b"), fr("F3", f3, "c"), *extra],
    )


def round_two_entry(rounds, claims=("F2", "F3"), answers=()):
    """The rows of a unit right after its second review round: `rounds`, impl's record claiming
    `claims` only a person can close, and a person's `answers` on the review."""
    return known(
        {
            "intent.md": "accepted",
            "spec.md": "accepted",
            "plan.md": "accepted",
            "impl.md": "accepted",
            "pr.md": "accepted",
            "review.md": "changes-requested",
        },
        type="fix",
        records={
            **claim_record(*claims),
            "pr.md": pr_row(7),
            "review.md": {"rounds": list(rounds)},
        },
        answers=list(answers),
    )


ROUND_TWO_FILES = {
    "intent.md": "# I\n",
    "spec.md": "# S\n",
    "plan.md": "# P\n",
    "impl.md": impl_text(),
    "pr.md": "# PR\n",
    "review.md": "# Review\n",
}


def tree_after_round_two(tmp_path, rounds, claims=("F2", "F3"), answers=()):
    """A unit right after its second review round, as files on disk."""
    _, u = question_tree(
        tmp_path / str(len(list(tmp_path.iterdir()))),
        ROUND_TWO_FILES,
        round_two_entry(rounds, claims, answers),
    )
    return u


R12 = [ROUND1, ROUND2]


def test_a_needs_person_round_is_not_counted_toward_the_limit():
    three = [round_(n, "changes-requested", [fr("F1", "open", "x")]) for n in (1, 2, 3)]
    four = [*three, round_(4, "needs-person", [fr("F1", "needs-person", "x")])]
    assert "needs a person — F1: a.py:3 — high — x" in next_action(asked(*four), 4)["action"]
    assert "3 of 4 rounds used" in next_action(asked(*three), 4)["action"]
    assert "needs a person — review used 3 of 3" in next_action(asked(*four), 3)["action"]
    one = next_action(asked(round_(1, "needs-person", [fr("F1", "needs-person", "x")])), 1)
    assert "rounds and findings are still open" not in one["action"]
    assert one["waiting"] == ["F1"]


def test_every_open_finding_claimed_and_unconfirmed_is_review(tmp_path):
    n = next_step(tree_after_round_two(tmp_path, R12), green_probe())
    assert n["stage"] == "review"
    assert "claimed as needing a person — review confirms or rejects each" in n["action"]
    assert "waiting" not in n


def test_both_claims_confirmed_names_a_person(tmp_path):
    u = tree_after_round_two(tmp_path, [*R12, round3()])
    n = next_step(u, green_probe())
    assert n["stage"] == ""
    assert n["blocked"] is True
    assert n["action"].startswith(
        "needs a person — F2: a.py:3 — high — b; F3: a.py:3 — high — c — answer each on the Questions tab"
    )
    assert n["waiting"] == ["F2", "F3"]
    assert next_step(u)["waiting"] == ["F2", "F3"]
    assert next_action(u)["waiting"] == ["F2", "F3"]
    assert [[p["id"], p["answered"]] for p in u["personFindings"]] == [["F2", False], ["F3", False]]


def test_one_answered_one_not_still_waits_for_the_other(tmp_path):
    u = tree_after_round_two(tmp_path, [*R12, round3()], answers=f_ans("F2"))
    n = next_step(u, green_probe())
    assert n["stage"] == ""
    assert n["action"].startswith("needs a person — F3: a.py:3 — high — c")
    assert n["waiting"] == ["F3"]


def test_both_answered_review_reads_the_answers(tmp_path):
    u = tree_after_round_two(tmp_path, [*R12, round3()], answers=f_ans("F2", "F3"))
    n = next_step(u, green_probe())
    assert n["stage"] == "review"
    assert "a person answered F2, F3 in review.md" in n["action"]
    assert "waiting" not in n
    assert check_gate(u, "review", green_probe())["ok"] is True


def test_an_open_finding_impl_did_not_claim_is_impl(tmp_path):
    r2 = round_(
        2,
        "changes-requested",
        [fx("F1", "a"), fr("F2", "open", "b"), fr("F3", "open", "c"), fr("F4", "open", "d")],
    )
    u = tree_after_round_two(tmp_path, [ROUND1, r2])
    assert next_step(u, green_probe())["stage"] == "impl"


def test_a_rejected_claim_is_impl(tmp_path):
    u = tree_after_round_two(tmp_path, [*R12, round3("claim-rejected")])
    assert next_step(u, green_probe())["stage"] == "impl"
    assert u["personFindings"] == []
    r3cr = round_(
        3,
        "changes-requested",
        [fx("F1", "a"), fr("F2", "needs-person", "b"), fr("F3", "claim-rejected", "c")],
    )
    assert (
        next_step(tree_after_round_two(tmp_path, [*R12, r3cr]), green_probe(), 4)["stage"] == "impl"
    )


def test_answered_but_kept_open_is_impl_not_review_again(tmp_path):
    r4 = round_(
        4, "changes-requested", [fx("F1", "a"), fr("F2", "answered", "b"), fr("F3", "open", "c")]
    )
    review = [*R12, round3(), r4]
    u = tree_after_round_two(tmp_path, review, answers=f_ans("F2", "F3"))
    n = next_step(u, green_probe(), 4)
    assert n["stage"] == "impl"
    assert "claimed in impl.md ## Needs a person" not in n["action"]
    r4b = round_(
        4,
        "changes-requested",
        [fx("F1", "a"), fr("F2", "needs-person", "b"), fr("F3", "open", "c")],
    )
    r3cr = round_(
        3,
        "changes-requested",
        [fx("F1", "a"), fr("F2", "open", "b"), fr("F3", "claim-rejected", "c")],
    )
    u = tree_after_round_two(tmp_path, [*R12, r3cr, r4b])
    assert next_step(u, green_probe(), 5)["stage"] == "impl"


def test_a_needs_person_verdict_with_an_open_finding_left_falls_back(tmp_path):
    a = tree_after_round_two(tmp_path, [*R12, round3("needs-person", [fr("F4", "open", "d")])])
    assert next_step(a, green_probe())["stage"] == "impl"
    assert "waiting" not in next_step(a, green_probe())
    b = tree_after_round_two(tmp_path, [*R12, round3("answered")])
    assert next_step(b, green_probe())["stage"] == "impl"
    assert b["personFindings"] == []


def test_an_impl_that_claims_nothing_changes_nothing(tmp_path):
    n = next_step(tree_after_round_two(tmp_path, R12, claims=()), green_probe())
    assert n["stage"] == "impl"
    assert "nothing outside .cos/0001_q/ has reached #7" in n["action"]


def test_ship_closes_an_answered_finding_only_with_its_answer():
    pass_ = round_(1, "pass", [fx("F1", "a"), fr("F2", "answered", "b")])
    with_block = {**review_art("accepted", [pass_]), "personAnswers": ["F2"]}
    g = check_gate(branched({**CHAIN, "review.md": with_block}), "ship", green_probe())
    assert g["ok"] is True, g["need"]
    bare = check_gate(ship(pass_), "ship", green_probe())
    assert bare["ok"] is False
    assert "F2 [answered, no answer in review.md]" in "\n".join(bare["need"])
    for label in ["needs-person", "claim-rejected"]:
        art_ = {
            **review_art("accepted", [round_(1, "pass", [fr("F2", label, "b")])]),
            "personAnswers": ["F2"],
        }
        shut = check_gate(branched({**CHAIN, "review.md": art_}), "ship", green_probe())
        assert shut["ok"] is False
        assert f"F2 [{label}]" in "\n".join(shut["need"])


def test_next_prints_waiting_only_when_a_person_is_awaited(tmp_path):
    entry = round_two_entry([*R12, round3()], claims=(), answers=f_ans("F2"))
    root, _ = question_tree(tmp_path, ROUND_TWO_FILES, entry)
    units = {"0001_q": entry}
    out = json.loads(cli("--root", str(root), "next", "0001_q", units=units).out)
    assert out["stage"] == ""
    assert out["waiting"] == ["F3"]
    status = json.loads(cli("--root", str(root), "status", "--json", units=units).out)["units"][0]
    assert status["next"]["waiting"] == ["F3"]
    assert status["personFindings"] == [
        {"id": "F2", "reason": "a.py:3 — high — b", "answered": True},
        {"id": "F3", "reason": "a.py:3 — high — c", "answered": False},
    ]


# --- 0035: between pr and ship --------------------------------------------------------------


def test_between_pr_and_ship_only_on_an_accepted_pr_naming_a_pull_request():
    def between(artifacts):
        return between_pr_and_ship(unit(artifacts))

    no_pr = {**FULL, "impl.md": art("accepted")}
    assert between(no_pr) is False
    assert between({**no_pr, "pr.md": {**art("draft"), "pr": PR["pr"]}}) is False
    assert between({**no_pr, "pr.md": {**art("accepted"), "pr": None}}) is False
    cr = review_art("changes-requested", [round_(1, "changes-requested", [fr("F1", "open", "x")])])
    assert between({**CHAIN, "review.md": cr}) is True
    assert between_pr_and_ship({**unit(CHAIN), "shipped": True}) is False
    assert between({**CHAIN, "review.md": art("rejected")}) is False


# --- 0033: the plan's Impl: label -----------------------------------------------------------


def test_the_plans_impl_label_opens_and_closes_no_gate_and_moves_no_next(tmp_path):
    def ask_(label):
        root = tmp_path / str(label)
        d = root / ".cos" / "0001_same"
        d.mkdir(parents=True)
        (d / "intent.md").write_text("# X\n")
        (d / "spec.md").write_text("# X\n")
        said = "" if label is None else f" Impl: {label}."
        (d / "plan.md").write_text(f"# X\nStatus: accepted.{said}\n")
        rows = {"0001_same": known(dict.fromkeys(("intent.md", "spec.md", "plan.md"), "accepted"))}
        gate = cli("gate", "0001_same", "impl", "--root", str(root), units=rows)
        nxt = cli("next", "0001_same", "--root", str(root), units=rows)
        return gate.code, gate.out, json.loads(nxt.out)

    routine = ask_("routine")
    assert routine[0] == 0, routine[1]
    assert ask_("novel") == routine
    assert ask_(None) == routine


# --- 0039: spike, only when the spec left a question unmeasured ------------------------------


def spec_text(concerns, status="accepted"):
    return (
        f"# Spec: x\nIntent: intent.md. Author: t. Status: {status}.\n\n## Requirements\n\n"
        f"- [unmeasured] U9. prose, not a concern\n\n## Concerns\n\n{concerns}\n"
    )


INTENT_0039 = "# Intent: x\n"


def spike_text(rnd, items):
    head = f"# Spike: x\nSpec: spec.md. Author: ᛈ Perthro. Round: {rnd}. Status: accepted.\n\n"
    body = "\n".join(f"## {i}\n\nVerdict: {v}.\n\n```\n$ node -e 1\nok\n```\n" for i, v in items)
    return head + body


def spike_unit(tmp_path, files, records=None, statuses=None):
    """A unit of `files`, each accepted but for `statuses`, its record rows `records`."""
    d = files_in(
        tmp_path, {"intent.md": INTENT_0039, **files}, name=f"s{len(list(tmp_path.iterdir()))}"
    )
    rows = dict.fromkeys(["intent.md", *files], "accepted") | (statuses or {})
    return read(d, "0039_x", known(rows, records=records))


def with_entry(statuses, name="0039_x"):
    """A snapshot of a unit whose files are `statuses` (all accepted), and its entry, for a test
    to change before `read_unit`."""
    entry = known(dict.fromkeys(statuses, "accepted"))
    return state_of_root(Path(), {name: entry}), entry


def test_a_stage_result_decides_the_specs_ids_and_the_spikes_verdicts(tmp_path):
    files = {
        "intent.md": INTENT_0039,
        "spec.md": spec_text("- **C1.** nothing unmeasured here."),
        "spike.md": spike_text(1, [("U1", "holds")]),
    }
    d = files_in(tmp_path, files)

    def read_(spec, spike=None):
        state, entry = with_entry(files)
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


def test_the_loop_reads_no_prose_for_spec_spike_and_impl_decisions(tmp_path):
    files = {
        "intent.md": INTENT_0039,
        "spec.md": spec_text("- [unmeasured] U1. a"),
        "spike.md": spike_text(1, [("U1", "fails")]),
        "impl.md": "# Impl: x\nStatus: accepted.\n\n## Needs a person\n\n- F2: a login\n",
    }
    d = files_in(tmp_path, files)
    state, entry = with_entry(files)
    u = read_unit(str(d), "0039_x", state)
    assert "unmeasured" not in u["artifacts"]["spec.md"]
    assert u["artifacts"]["spike.md"]["verdicts"] == {"round": None, "items": {}}
    assert u["artifacts"]["impl.md"]["needsPerson"] == []
    entry["artifacts"]["spike.md"]["round"] = 2
    again = read_unit(str(d), "0039_x", state)
    assert again["artifacts"]["spike.md"]["verdicts"]["round"] == 2


def test_a_round_and_the_claims_the_app_holds_decide(tmp_path):
    impl = "# Impl: x\nStatus: accepted.\n\n## Needs a person\n\n- F2: a login\n"
    files = {"intent.md": INTENT_0039, "review.md": "# Review\n", "impl.md": impl}
    d = files_in(tmp_path, files)
    state, entry = with_entry(files)
    entry["artifacts"]["review.md"]["status"] = "changes-requested"

    def row(label, severity="medium"):
        return fr("F2", label, "new", severity, path="b.py", lines="9", rule="S3")

    entry["artifacts"]["review.md"]["rounds"] = [
        round_(1, "changes-requested", [fr("F1", "open", "old", lines="3")], reviewed="abc1234"),
        round_(2, "changes-requested", [row("open")], reviewed="f" * 40),
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
    assert rounds[1]["text"] == "Verdict: changes-requested.\n- F2 [open] b.py:9 — medium — S3 new"
    assert u["artifacts"]["impl.md"]["needsPerson"] == []
    entry["artifacts"]["impl.md"]["result"]["needs_person"] = ["F2"]
    again = read_unit(str(d), "0039_x", state)
    assert again["artifacts"]["impl.md"]["needsPerson"] == [{"id": "F2"}]
    entry["artifacts"]["review.md"]["rounds"][1]["findings"] = [row("open", "low")]
    assert non_blocking(read_unit(str(d), "0039_x", state)) == []


def test_a_unit_with_nothing_unmeasured_walks_as_if_spike_did_not_exist(tmp_path):
    u = spike_unit(tmp_path, {"spec.md": spec_text("- **C1.** none"), "plan.md": "# Plan\n"})
    assert "unmeasured" not in u["artifacts"]["spec.md"]
    assert "restsOn" not in u["artifacts"]["plan.md"]
    assert check_gate(u, "impl")["ok"] is True
    assert "write-impl" in next_action(u)["action"]
    assert check_gate(u, "spike")["need"] == [
        "spike is not required: spec.md has no [unmeasured] item"
    ]


SPEC = spec_text("- [unmeasured] U1. a")
SPIKE = spike_text(1, [("U1", "holds")])


def test_a_skipped_spec_never_needs_a_spike(tmp_path):
    files = {
        "spec.md": spec_text("- [unmeasured] U1. x", "skipped"),
        "spike.md": spike_text(1, [("U1", "fails")]),
        "plan.md": "# Plan\n",
    }
    u = spike_unit(
        tmp_path,
        files,
        {**spec_record("U1"), **spike_record(U1="fails")},
        {"spec.md": "skipped"},
    )
    assert check_gate(u, "impl")["ok"] is True
    assert "write-impl" in next_action(u)["action"]


def test_an_unmeasured_spec_sends_the_unit_to_spike_and_closes_plan(tmp_path):
    asked = spec_record("U1", "U2")
    none = spike_unit(tmp_path, {"spec.md": SPEC}, asked)
    assert none["artifacts"]["spec.md"]["unmeasured"]["ids"] == ["U1", "U2"]
    assert next_action(none)["stage"] == "spike"
    assert check_gate(none, "spike")["ok"] is True
    need = "\n".join(check_gate(none, "plan")["need"])
    assert "spike.md does not exist" in need
    assert "U1: spike.md is missing, not accepted" in need
    no_verdict = spike_unit(
        tmp_path, {"spec.md": SPEC, "spike.md": SPIKE}, {**asked, **spike_record(U1="holds")}
    )
    need = check_gate(no_verdict, "plan")["need"]
    assert len(need) == 1
    assert re.match(r"U2: .* does not measure U2", need[0])
    assert next_action(no_verdict)["stage"] == "spike"
    held = spike_unit(
        tmp_path,
        {"spec.md": SPEC, "spike.md": SPIKE},
        {**asked, **spike_record(U1="holds", U2="holds")},
    )
    assert check_gate(held, "plan")["ok"] is True
    assert next_action(held)["stage"] == "plan"


def test_spec_spike_spec_again_spike_plan(tmp_path):
    files = {"spec.md": SPEC, "spike.md": SPIKE}
    first = spike_unit(
        tmp_path, files, {**spec_record("U1", "U2"), **spike_record(U1="holds", U2="fails")}
    )
    assert next_action(first)["stage"] == "spec"
    assert "U2 does not hold" in next_action(first)["action"]
    assert "U2: spike.md measured that it does not hold" in " ".join(
        check_gate(first, "plan")["need"]
    )
    assert check_gate(first, "spec")["ok"] is True
    rewritten = spike_unit(
        tmp_path, files, {**spec_record("U1", "U3"), **spike_record(U1="holds", U2="fails")}
    )
    assert next_action(rewritten)["stage"] == "spike"
    assert "does not measure U3" in next_action(rewritten)["action"]
    measured = spike_unit(
        tmp_path, files, {**spec_record("U1", "U3"), **spike_record(2, U1="holds", U3="holds")}
    )
    assert next_action(measured)["stage"] == "plan"
    assert check_gate(measured, "plan")["ok"] is True


def test_fails_ahead_of_missing(tmp_path):
    u = spike_unit(
        tmp_path,
        {"spec.md": SPEC, "spike.md": SPIKE},
        {**spec_record("U1", "U2"), **spike_record(U2="fails")},
    )
    assert next_action(u)["stage"] == "spec"


def test_a_question_still_failing_at_the_last_spike_round_needs_a_person(tmp_path):
    u = spike_unit(
        tmp_path,
        {"spec.md": SPEC, "spike.md": SPIKE},
        {**spec_record("U3"), **spike_record(SPIKE_ROUNDS, U3="fails")},
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
        {**spec_record(), **spike_record(U1="fails")},
    )
    assert next_action(u)["stage"] == "plan"
    assert check_gate(u, "plan")["ok"] is True
    draft = spike_unit(
        tmp_path,
        {"spec.md": spec_text("- none left"), "spike.md": "# Spike\n"},
        statuses={"spike.md": "draft"},
    )
    assert next_action(draft)["stage"] == "plan"
    assert check_gate(draft, "plan")["ok"] is True
    assert check_gate(draft, "spike")["ok"] is False


def skip_tree(tmp_path, authority, files=None):
    """`spec.md` skipped, and the snapshot saying whose decision the skip was."""
    files = {"spec.md": "# Spec\n"} if files is None else files
    d = files_in(
        tmp_path, {"intent.md": INTENT_0039, **files}, name=f"k{len(list(tmp_path.iterdir()))}"
    )
    entry = known({"intent.md": "accepted", "spec.md": "skipped"})
    if authority is None:
        entry["artifacts"]["spec.md"].pop("authority")
    else:
        entry["artifacts"]["spec.md"]["authority"] = authority
    return read(d, "0039_x", entry)


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
    reasoned = known({"intent.md": "accepted", "spec.md": "skipped"})
    reasoned["artifacts"]["spec.md"]["reason"] = "one file, no schema"
    d = files_in(tmp_path, {"intent.md": INTENT_0039}, name="reasoned")
    assert read(d, "0039_x", reasoned)["artifacts"]["spec.md"]["skipReason"] == (
        "one file, no schema"
    )
    bare = skip_tree(tmp_path, "agent", {})
    assert "spec.md" not in bare["artifacts"]
    assert "the app records spec.md as skipped, but the file does not exist" in "\n".join(
        bare["problems"]
    )


def test_with_a_spike_required_impl_opens_only_on_a_plan_resting_on_it(tmp_path):
    files = {"spec.md": SPEC, "spike.md": SPIKE}
    records = {**spec_record("U1"), **spike_record(U1="holds")}
    silent = spike_unit(tmp_path, {**files, "plan.md": "# Plan\n"}, records)
    assert silent["artifacts"]["plan.md"]["restsOn"] == []
    assert check_gate(silent, "impl")["need"] == [
        "plan.md rests on no U<n> — its record's rests_on names the spike items its steps rest on"
    ]
    cites = spike_unit(tmp_path, {**files, "plan.md": "# Plan\n"}, {**records, **plan_record("U1")})
    assert cites["artifacts"]["plan.md"]["restsOn"] == ["U1"]
    assert check_gate(cites, "impl")["ok"] is True


# --- 0045: a person pauses or drops a unit -------------------------------------------------


def held_tree(tmp_path, holds=(), extra=None, answers=None, shipped=False):
    """A unit whose intent asks two questions; `extra` is `{file: status}` of its later files."""
    root = tmp_path / f"h{len(list(tmp_path.iterdir()))}"
    d = root / ".cos" / "0001_held"
    d.mkdir(parents=True)
    files = {"intent.md": "# Intent: x\n", **dict.fromkeys(extra or {}, "# x\n")}
    if "pr.md" in files:
        files["pr.md"] = "PR: https://github.com/o/r/pull/7.\n"
    for f, text in files.items():
        (d / f).write_text(text)
    entry = known(
        {"intent.md": "accepted", **(extra or {})},
        questions={"intent.md": ["Một?", "Hai?"]},
        answers=answers,
        holds=list(holds),
    )
    entry["shipped"] = shipped
    return SimpleNamespace(
        root=root,
        u=read(d, "0001_held", entry),
        cli=lambda *a: cli(*a, "--root", str(root), units={"0001_held": entry}),
    )


LATER = dict.fromkeys(["spec.md", "plan.md", "impl.md", "pr.md"], "accepted")


def test_no_hold_row_is_no_hold_and_the_active_moves(tmp_path):
    u = held_tree(tmp_path, answers=[answer("intent.md", 1, "A", "x")]).u
    assert u["hold"] is None
    assert u["holdMoves"] == ["paused", "dropped"]
    assert u["problems"] == []


def test_an_invalid_hold_move_is_ignored_and_reported(tmp_path):
    resumed = fold_holds([hold("dropped", "bỏ"), hold("active", "lại")])
    assert resumed["hold"]["state"] == "dropped"
    msg = "hold block 2 (### Resumed) is not a valid move from dropped — it is ignored"
    assert resumed["problems"] == [msg]
    twice = fold_holds([hold("paused", "a"), hold("paused", "b")])
    assert twice["hold"]["reason"] == "a"
    assert len(twice["problems"]) == 1
    assert len(fold_holds([hold("active", "x")])["problems"]) == 1
    u = held_tree(tmp_path, [hold("dropped", "bỏ"), hold("active", "lại")]).u
    assert u["problems"] == [f"intent.md: {msg}"]


def test_next_offers_no_stage_and_asks_no_probe_when_held(tmp_path):
    def boom(*a):
        raise AssertionError("the probe was asked")

    probe = SimpleNamespace(gh=boom, git=boom)
    paused = held_tree(tmp_path, [hold("paused", "chờ người")], LATER)
    n = next_step(paused.u, probe)
    assert n["stage"] == ""
    assert n["blocked"] is True
    assert n["action"] == "paused — chờ người (Leif, 2026-09-24) — resume it from the board"
    dropped = held_tree(tmp_path, [hold("dropped", "không đáng")], LATER)
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
    h = held_tree(tmp_path, [hold("paused", "chờ 0034")], {"spec.md": "accepted"})
    for s in STAGE_NAMES:
        g = check_gate(h.u, s, green_probe())
        assert g["ok"] is False, s
        assert g["need"] == ["the unit is paused: chờ 0034 (Leif, 2026-09-24)"], s
        r = h.cli("gate", "0001_held", s)
        assert r.code == 1, s
        assert "the unit is paused: chờ 0034" in r.err
    assert check_gate(h.u, "nope")["need"][0].startswith("unknown stage")


def test_a_shipped_unit_or_a_rejection_wins_over_a_hold(tmp_path):
    files = {"spec.md": "accepted", "plan.md": "accepted"}
    done = held_tree(tmp_path, [hold("paused", "x")], files, shipped=True).u
    assert done["hold"] is None
    assert done["holdMoves"] == []
    assert (
        "intent.md carries a hold block, but the unit is finished — it is ignored"
        in done["problems"]
    )
    assert next_action(done)["action"] == "finished"
    closed = held_tree(tmp_path, [hold("dropped", "x")], {"spec.md": "rejected"}).u
    assert closed["hold"] is None
    assert closed["holdMoves"] == []
    assert (
        "intent.md carries a hold block, but the unit is closed — it is ignored"
        in closed["problems"]
    )


def test_an_idea_with_no_intent_can_be_held(tmp_path):
    d = files_in(tmp_path, {"idea.md": "# Idea: x\n"})
    u = read(d, "0001_x", known({"idea.md": "accepted"}))
    assert u["hold"] is None
    assert u["holdMoves"] == ["paused", "dropped"]

    def held(*moves):
        rows = [hold(s, r, date="2026-10-05") for s, r in moves]
        return read(d, "0001_x", known({"idea.md": "accepted"}, holds=rows))

    dropped = held(("dropped", "gộp vào 0055"))
    assert dropped["hold"]["state"] == "dropped"
    assert dropped["hold"]["reason"] == "gộp vào 0055"
    assert dropped["holdMoves"] == ["paused"]
    assert not any("intent.md" in p for p in dropped["problems"])
    assert next_step(dropped)["stage"] == ""
    for s in STAGE_NAMES:
        g = gate_answer(dropped, s)
        assert g["ok"] is False, s
        assert g["reasons"] == ["dropped"], s
    bad = held(("dropped", "a"), ("active", "b"))
    assert bad["problems"] == [
        "hold block 2 (### Resumed) is not a valid move from dropped — it is ignored"
    ]
    back = held(("dropped", "a"), ("paused", "b"), ("active", "c"))
    assert back["hold"] is None
    assert next_action(back)["action"].startswith("write-intent")
    assert next_step(back)["stage"] == "intent"


# --- 0061: a finding that does not block -----------------------------------------------------


def low(id_, label="open", what="x"):
    return fr(id_, label, what, "low")


def rated(id_, severity, label="open"):
    return fr(id_, label, "x", severity)


def test_an_open_low_does_not_block_unless_rated_higher_before():
    one = asked(round_(1, "pass", [low("F1"), rated("F2", "medium"), rated("F3", "")]))
    assert non_blocking(one) == [{"id": "F1", "text": "a.py:3 — low — x"}]
    r1 = round_(1, "changes-requested", [rated("F1", "high")])
    assert non_blocking(asked(r1, round_(2, "pass", [low("F1")]))) == []
    unrated = round_(1, "changes-requested", [rated("F1", "")])
    got = non_blocking(asked(unrated, round_(2, "pass", [low("F1")])))
    assert [f["id"] for f in got] == ["F1"]
    for label in ["needs-person", "claim-rejected", "answered", "unreadable"]:
        assert non_blocking(asked(round_(1, "needs-person", [low("F1", label)]))) == [], label
    assert non_blocking(unit(CHAIN)) == []


def test_ship_lets_an_open_low_through_and_nothing_else():
    def gate(findings):
        return check_gate(ship(round_(1, "pass", findings)), "ship", green_probe())

    assert gate([low("F1")])["ok"] is True
    for findings in ([rated("F1", "medium")], [rated("F1", "")]):
        g = gate(findings)
        assert g["ok"] is False
        assert "F1 [open]" in "\n".join(g["need"])


def test_a_severity_lowered_between_rounds_closes_ship():
    rounds = [
        round_(1, "changes-requested", [rated("F1", "high")]),
        round_(2, "pass", [low("F1")]),
    ]
    g = check_gate(ship(*rounds), "ship", green_probe())
    assert g["ok"] is False
    assert (
        "F1 is low in review round 2, but review round 1 rated it high — lowering a severity is "
        "not a fix: fix it on the branch, or keep it open"
    ) in g["need"]


SHOTS = {
    "taken": SHA,
    "standard": ".claude/rules/ui-standard.md",
    "by": "agent session s1",
    "shots": [{"path": ".screens/a.png", "size": "1x1", "address": "/", "result": "ok"}],
}


def standard_finding(id_, path="ui/a.tsx", severity="high", label="open"):
    return fr(id_, label, "x", severity, path=path, lines="3", rule="S3")


def with_diff(*files):
    """A probe whose branch diff against the trunk names `files`."""
    return green_probe(None, {f"diff --name-only origin/main...{SHA}": ok("\n".join(files))})


def test_a_finding_against_the_standard_is_read_for_where_it_points_and_what_it_says():
    u = asked(round_(1, "pass", [standard_finding("F1"), fr("F2", rule="S3", path="")]))
    assert standard_findings(u) == [
        {
            "id": "F1",
            "criterion": "S3",
            "path": "ui/a.tsx",
            "lines": "3",
            "text": "x",
            "round": 1,
        },
        {"id": "F2", "criterion": "S3", "path": None, "lines": "", "text": "x", "round": 1},
    ]


def test_an_s_finding_on_a_file_the_patch_changes_blocks_even_when_low():
    low_one = standard_finding("F1", severity="low")
    u = ship(round_(1, "pass", [low_one], screens=SHOTS))
    for files in (["ui/a.tsx"], ["ui/other.tsx"]):
        g = check_gate(u, "ship", with_diff(*files))
        assert g["ok"] is (files == ["ui/other.tsx"])
        assert [p["why"] for p in u["screenPasses"]] == ([] if g["need"] else ["screen-untouched"])


def test_severity_rule_lets_through_what_the_screens_rule_does_and_the_round_still_counts():
    r1 = round_(1, "changes-requested", [standard_finding("F1")], screens=SHOTS)
    u = asked(r1)
    assert non_blocking(u) == []
    assert screens_spared(u) is False
    u["screenPasses"] = [{"id": "F1", "why": "screen-untouched"}]
    assert [f["id"] for f in non_blocking(u)] == ["F1"]
    assert screens_spared(u) is True
    # A round that asked for changes counts, let through or not: it bounds the rounds.
    assert rounds_used(u) == 1
    # One blocking finding beside it, and a fix is still owed.
    mixed = asked(round_(1, "changes-requested", [standard_finding("F1"), fr("F2")]))
    mixed["screenPasses"] = [{"id": "F1", "why": "screen-untouched"}]
    assert screens_spared(mixed) is False
    assert [f["id"] for f in non_blocking(mixed)] == ["F1"]


def test_a_gate_with_a_probe_sets_what_the_severity_rule_reads():
    u = asked(round_(1, "changes-requested", [standard_finding("F1")], screens=SHOTS))
    check_gate(u, "review", with_diff("ui/other.tsx"))
    assert [p["id"] for p in u["screenPasses"]] == ["F1"]
    assert screens_spared(u) is True
    check_gate(u, "review", with_diff("ui/a.tsx"))
    assert u["screenPasses"] == []
    assert screens_spared(u) is False


def test_changes_asked_only_for_what_the_screens_rule_lets_through_is_review_not_impl():
    u = asked(round_(1, "changes-requested", [standard_finding("F1")], screens=SHOTS))
    n = next_answer(u, with_diff("ui/other.tsx"))
    assert n["stage"] == "review"
    assert (
        "every open finding of review round 1 is one the screens rule lets through (F1)"
        in (n["action"])
    )
    assert n["reasons"] == ["changes-requested"]
    # On a file the patch changes it blocks, and a fix is owed first.
    assert next_step(u, with_diff("ui/a.tsx"))["stage"] == "impl"


def test_a_review_that_keeps_asking_for_what_the_screens_rule_lets_through_stops_at_the_limit():
    finding = standard_finding("F1")
    rounds = [round_(n, "changes-requested", [finding], screens=SHOTS) for n in (1, 2, 3)]
    n = next_answer(asked(*rounds), with_diff("ui/other.tsx"))
    assert n["stage"] == ""
    assert n["reasons"] == ["needs-person"]
    assert "review used 3 of 3 rounds" in n["action"]


def test_next_step_never_offers_a_closed_gate_in_the_person_states(tmp_path):
    reviews = [
        (R12, ()),
        ([*R12, round3()], ()),
        ([*R12, round3()], f_ans("F2", "F3")),
    ]
    probes = [
        green_probe(),
        green_probe([{"name": "t", "bucket": "fail"}]),
        green_probe([{"name": "t", "bucket": "pending"}]),
    ]
    for probe in probes:
        for review, answers in reviews:
            u = tree_after_round_two(tmp_path, review, answers=answers)
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
