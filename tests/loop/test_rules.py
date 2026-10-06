"""`status`, `gate` and `next` print what the goldens hold, for every shape of unit.

One store holds a unit for each rule that answers without a repository. Every test asks both
versions through `expect()`, which asserts stdout, stderr and the exit code are alike; the
coverage test then reads the codes each unit gets and checks every one the loop can hand out
without git or `gh` is among them.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import pytest

from coscc.loop import STAGE_NAMES
from coscc.loop.model import above_answers, read_unit
from coscc.loop.rules import gate_answer, next_answer
from tests.loop.conftest import UnitStore, entry, env, expect, git_repo, python

KIND = {
    "idea.md": "Idea",
    "intent.md": "Intent",
    "spec.md": "Spec",
    "spike.md": "Spike",
    "plan.md": "Plan",
    "impl.md": "Impl",
    "pr.md": "PR",
    "review.md": "Review",
    "ship.md": "Ship",
}
CHAIN = ["intent.md", "spec.md", "plan.md", "impl.md", "pr.md", "review.md", "ship.md"]
PERSON = {"by": "Phong", "date": "2026-09-30", "via": "board"}
HOLD = {"reason": "chờ khách hàng trả lời", **PERSON}


def text(file: str, status: str | None, head: str = "", body: str = "") -> str:
    """A file as the skills write it: a title line, a header line, then `body`."""
    said = "" if status is None else f"Status: {status}. "
    return f"# {KIND[file]}: Tiêu đề thử nghiệm\nAuthor: test. {said}{head}\n{body}"


def accepted(upto: str = "ship.md", **over: str | None) -> dict[str, str | None]:
    """`{file: status}` for the chain up to and including `upto`, `over` replacing statuses."""
    arts: dict[str, str | None] = {f: "accepted" for f in CHAIN[: CHAIN.index(upto) + 1]}
    arts.update(over)
    return arts


def pr_text(name: str, status: str | None) -> str:
    """A `pr.md` with a title the pr stage would accept, naming the pull request."""
    return f"# PR: feat({name[:4]}): add the thing\nAuthor: test. Status: {status}.\n\n{PR}"


def put(
    s: UnitStore,
    name: str,
    arts: dict[str, str | None],
    texts: dict[str, str] | None = None,
    extra_files: dict[str, str] | None = None,
    **kw,
) -> None:
    """Writes each artifact and records it in the snapshot; `texts` replaces a file's text."""
    texts = texts or {}
    files = {
        f: texts.get(f, pr_text(name, st) if f == "pr.md" else text(f, st))
        for f, st in arts.items()
    }
    files.update(extra_files or {})
    s.unit(name, files, entry(arts, **kw))


def round_(n: int, verdict: str, *findings: str, sha: str = "aaaaaaa") -> str:
    lines = "\n".join(findings)
    return f"## Round {n}\nReviewed: {sha}. Verdict: {verdict}.\n\n### Findings\n{lines}\n\n"


def review(status: str, *rounds: str, tail: str = "") -> str:
    return text("review.md", status, body="\n".join(rounds) + tail)


F1 = "- F1 [open] src/a.py:3 — high — hàm trả sai kết quả khi đầu vào rỗng"
F2 = "- F2 [open] src/b.py:9 — medium — thiếu kiểm tra"
PR = "PR: https://github.com/o/r/pull/7\n"
MORE = (
    "\n## Answers\n### More rounds\nDecided by: Phong. Date: 2026-10-01. Via: board.\nRounds: 2\n"
)
FIX_INTENT = "# Intent: lỗi\nAuthor: test. Status: accepted. Type: fix.\n"
# What intent's record hands over for a fix to enter the fast lane, and impl's to leave it.
FIX = {
    "reproduction": "$ run\nboom",
    "expected": {"source": "src/a.py:1-3", "text": "trả về 3"},
    "actual": "trả về 2",
}
FIX_RECORD = {"result": {"judgement": "ready", "fix": FIX}}
LEFT_RECORD = {"result": {"judgement": "ready", "left_lane": "nguồn nói khác"}}
ASKED = "## Open questions\n1. Ai chịu trách nhiệm cho phần này?\n"
ANSWER = {"artifact": "intent.md", "n": 1, "id": None, "text": "Người dùng.", **PERSON}


def spike_md(verdict: str | None, rnd: int = 1) -> dict:
    return {
        "round": rnd,
        "result": {"verdicts": [{"id": "U1", "verdict": verdict}] if verdict else []},
    }


SPEC_U = {"result": {"unmeasured": ["U1"]}}


def row(n: int, verdict: str, *findings: tuple[str, str]) -> dict:
    return {
        "n": n,
        "verdict": verdict,
        "reviewed": "aaaaaaa",
        "screens": {},
        "findings": [
            {"id": i, "label": label, "severity": "high", "path": "src/a.py", "lines": "3",
             "text": "cần quyết định"}
            for i, label in findings
        ],
    }  # fmt: skip


def rerun_block(stage: str, **stale: str) -> str:
    lines = "".join(f"Stale: {f} sha256:{d}\n" for f, d in stale.items())
    return (
        "\n## Answers\n### Rerun\nRequested by: Phong. Date: 2026-09-29. Via: board.\n"
        f"Stage: {stage}.\n{lines}"
    )


def build(s: UnitStore) -> None:  # noqa: PLR0915 - one list of units, each a rule
    put(s, "0001_fresh", {"intent.md": "draft"})
    s.unit(
        "0002_pre-intent",
        {"idea.md": text("idea.md", "accepted")},
        entry({"idea.md": "accepted"}, type=None),
    )
    s.unit(
        "0003_no-intent", {"spec.md": text("spec.md", "accepted")}, entry({"spec.md": "accepted"})
    )
    put(s, "0004_spec-missing", accepted("intent.md"))
    put(s, "0005_plan-missing", accepted("spec.md"))
    put(s, "0006_impl-missing", accepted("plan.md"))
    put(s, "0007_paused", accepted("plan.md"), holds=[{"state": "paused", **HOLD}])
    put(s, "0008_dropped", accepted("plan.md"), holds=[{"state": "dropped", **HOLD}])
    put(s, "0009_rejected", accepted("intent.md", **{"spec.md": "rejected"}))
    put(s, "0010_finished", accepted("plan.md", **{"plan.md": "done"}))
    put(
        s,
        "0011_agent-skip",
        accepted("intent.md", **{"spec.md": "skipped"}),
        spec_md={"authority": "agent", "result": None},
    )
    put(
        s,
        "0012_agent-skip-named",
        accepted("intent.md", **{"spec.md": "skipped"}),
        spec_md={"authority": "claude"},
    )
    put(
        s,
        "0013_person-skip",
        accepted("intent.md", **{"spec.md": "skipped"}),
        spec_md={"authority": "person"},
    )
    it = text("intent.md", "accepted")
    sp = text("spec.md", "accepted")
    put(
        s,
        "0014_stale",
        accepted("plan.md"),
        texts={"intent.md": it + rerun_block("intent", **{"spec.md": above_answers(sp)})},
    )
    put(s, "0015_spike-missing", accepted("spec.md"), spec_md=SPEC_U)
    put(s, "0016_spike-draft", accepted("spec.md", **{"spike.md": "draft"}),
        spec_md=SPEC_U, spike_md=spike_md("holds"))  # fmt: skip
    put(s, "0017_spike-fails", accepted("spec.md", **{"spike.md": "accepted"}),
        spec_md=SPEC_U, spike_md=spike_md("fails"))  # fmt: skip
    put(s, "0018_spike-fails-twice", accepted("spec.md", **{"spike.md": "accepted"}),
        spec_md=SPEC_U, spike_md=spike_md("fails", 2))  # fmt: skip
    put(s, "0020_spike-no-verdict", accepted("spec.md", **{"spike.md": "accepted"}),
        spec_md=SPEC_U, spike_md=spike_md(None))  # fmt: skip
    cite = text("plan.md", "accepted", body="Dựa trên spike.md ## U1.\n")
    put(s, "0021_spike-ok", accepted("plan.md", **{"spike.md": "accepted"}),
        texts={"plan.md": cite}, spec_md=SPEC_U, spike_md=spike_md("holds"))  # fmt: skip
    put(s, "0022_spike-no-cite", accepted("plan.md", **{"spike.md": "accepted"}),
        spec_md=SPEC_U, spike_md=spike_md("holds"))  # fmt: skip
    put(s, "0024_spike-skipped-spec", accepted("intent.md", **{"spec.md": "skipped"}),
        texts={"spec.md": text("spec.md", "skipped")}, spec_md={"authority": "person", **SPEC_U})  # fmt: skip
    put(s, "0025_fast-lane", {"intent.md": "accepted"}, texts={"intent.md": FIX_INTENT},
        type="fix", intent_md=FIX_RECORD)  # fmt: skip
    put(s, "0026_fast-lane-impl", {"intent.md": "accepted", "impl.md": "accepted"},
        texts={"intent.md": FIX_INTENT}, type="fix", intent_md=FIX_RECORD)  # fmt: skip
    put(s, "0027_fast-lane-left", accepted("plan.md", **{"impl.md": "draft"}),
        texts={"intent.md": FIX_INTENT}, type="fix", intent_md=FIX_RECORD, impl_md=LEFT_RECORD)  # fmt: skip
    put(s, "0028_review-missing", accepted("pr.md"))
    put(s, "0029_review-draft", accepted("pr.md", **{"review.md": "draft"}))
    pr: dict[str, str] = {}
    put(s, "0030_changes-requested", accepted("pr.md", **{"review.md": "changes-requested"}),
        texts={**pr, "review.md": review("changes-requested", round_(1, "changes-requested", F1))})  # fmt: skip
    three = [round_(n, "changes-requested", F1, sha=f"{n}{n}{n}{n}{n}{n}{n}") for n in (1, 2, 3)]
    put(s, "0031_out-of-rounds", accepted("pr.md", **{"review.md": "changes-requested"}),
        texts={**pr, "review.md": review("changes-requested", *three)})  # fmt: skip
    put(s, "0032_more-rounds", accepted("pr.md", **{"review.md": "changes-requested"}),
        texts={**pr, "review.md": review("changes-requested", *three, tail=MORE)})  # fmt: skip
    put(s, "0033_unfinished", accepted("pr.md", **{"review.md": "changes-requested"}),
        texts={**pr, "review.md": review("changes-requested", round_(1, "changes-requested", F1, F2),
                                         round_(2, "changes-requested", F2, sha="bbbbbbb"))})  # fmt: skip
    put(s, "0034_incomplete", accepted("pr.md", **{"review.md": "draft"}),
        texts={**pr, "review.md": review("draft", round_(1, "incomplete"))})  # fmt: skip
    needs = round_(1, "needs-person", "- F1 [needs-person] src/a.py:3 — high — cần quyết định")
    claim = {"impl_md": {"result": {"needs_person": ["F1"]}}}
    asked = {"review_md": {"rounds": [row(1, "needs-person", ("F1", "needs-person"))]}}
    open_one = {"review_md": {"rounds": [row(1, "changes-requested", ("F1", "open"))]}}
    put(s, "0035_awaits-person", accepted("pr.md", **{"review.md": "changes-requested"}),
        texts={**pr, "review.md": review("changes-requested", needs)}, **claim, **asked)  # fmt: skip
    put(s, "0036_awaits-unclaimed", accepted("pr.md", **{"review.md": "changes-requested"}),
        texts={**pr, "review.md": review("changes-requested", needs)}, **asked)  # fmt: skip
    put(s, "0037_person-answered", accepted("pr.md", **{"review.md": "changes-requested"}),
        texts={**pr, "review.md": review("changes-requested", needs)}, **claim, **asked,
        answers=[{**ANSWER, "artifact": "review.md", "n": None, "id": "F1"}])  # fmt: skip
    put(s, "0038_every-claimed", accepted("pr.md", **{"review.md": "changes-requested"}),
        texts={**pr, "review.md": review("changes-requested", round_(1, "changes-requested", F1))},
        **claim, **open_one)  # fmt: skip
    put(s, "0039_ship-refused", accepted("pr.md", **{"review.md": "accepted", "ship.md": "draft"}),
        texts={**pr, "review.md": review("accepted", round_(1, "pass")),
               "ship.md": text("ship.md", "draft", head="Round: 1.", body="## What went out\nRefused: not up to date\n")})  # fmt: skip
    put(s, "0040_ship-draft-old", accepted("pr.md", **{"review.md": "accepted", "ship.md": "draft"}),
        texts={**pr, "review.md": review("accepted", round_(1, "pass"))})  # fmt: skip
    put(s, "0041_ship-missing", accepted("review.md"),
        texts={**pr, "review.md": review("accepted", round_(1, "pass"))})  # fmt: skip
    put(s, "0042_draft-answered", {"intent.md": "draft"},
        texts={"intent.md": text("intent.md", "draft", body=ASKED)},
        intent_md={"questions": [{"n": 1, "text": "Ai chịu trách nhiệm cho phần này?"}]}, answers=[ANSWER])  # fmt: skip
    put(s, "0043_draft-asking", {"intent.md": "draft"},
        texts={"intent.md": text("intent.md", "draft", body=ASKED)},
        intent_md={"questions": [{"n": 1, "text": "Ai chịu trách nhiệm cho phần này?"}]})  # fmt: skip
    put(s, "0044_waits-on-dependency", accepted("plan.md"),
        links={"idea": None, "dependsOn": ["0006_impl-missing"]})  # fmt: skip
    put(s, "0045_depends-unknown", accepted("plan.md"),
        links={"idea": None, "dependsOn": ["0999_ghost", "ma/lformed", "ws/0045_depends-unknown"]})  # fmt: skip
    put(s, "0046_depends-merged", accepted("plan.md"),
        links={"idea": None, "dependsOn": ["0047_merged-one", "other/0001_far"]})  # fmt: skip
    put(s, "0047_merged-one", accepted("ship.md", **{"plan.md": "done"}), merged=True)
    put(s, "0050_no-status-line", {"intent.md": "accepted", "spec.md": None})
    put(s, "0051_odd-status", {"intent.md": "accepted", "spec.md": "wip", "plan.md": "constructor"})
    put(s, "0052_unknowns", accepted("spec.md"),
        unknowns=[{"field": "ingest", "reason": "spec.md không đọc được"}])  # fmt: skip
    put(s, "0053_closed-with-hold", accepted("intent.md", **{"spec.md": "rejected"}),
        holds=[{"state": "paused", **HOLD}])  # fmt: skip
    put(s, "0054_bad-hold-move", accepted("plan.md"),
        holds=[{"state": "active", **HOLD}, {"state": "paused", **HOLD}, {"state": "paused", **HOLD}])  # fmt: skip
    put(s, "0055_resumed", accepted("plan.md"),
        holds=[{"state": "paused", **HOLD}, {"state": "active", **HOLD}])  # fmt: skip
    put(s, "0056_hold-no-reason", accepted("plan.md"),
        holds=[{"state": "dropped", "reason": None, "by": "Phong", "date": "2026-10-01", "via": "board"}])  # fmt: skip
    put(s, "0057_no-type", accepted("plan.md"), type=None)
    put(s, "0058_bad-type", accepted("plan.md"), type="feature")
    put(s, "0059_app-only", {"intent.md": "accepted"}, texts={})
    s.units["ws/0059_app-only"]["artifacts"]["spec.md"] = {"status": "accepted"}
    put(s, "0060_stray", accepted("spec.md"), extra_files={"notes.txt": "x\n"})
    s.unit("scratch", {"intent.md": text("intent.md", "accepted")}, None)
    put(s, "0061_review-limit-one", accepted("pr.md", **{"review.md": "changes-requested"}),
        texts={**pr, "review.md": review("changes-requested", round_(1, "changes-requested", F1))})  # fmt: skip
    put(
        s,
        "0067_pr-bad-title",
        accepted("pr.md"),
        texts={"pr.md": text("pr.md", "accepted", body=PR)},
    )
    rv = review("accepted", round_(1, "pass"))
    sh = text("ship.md", "accepted")
    put(s, "0068_stale-review", accepted("review.md"),
        texts={"intent.md": it + rerun_block("pr", **{"review.md": above_answers(rv)}), "review.md": rv})  # fmt: skip
    put(s, "0069_stale-ship", accepted("ship.md"),
        texts={"intent.md": it + rerun_block("pr", **{"ship.md": above_answers(sh)}), "review.md": rv, "ship.md": sh})  # fmt: skip
    put(s, "0062_impl-draft", accepted("plan.md", **{"impl.md": "draft"}))
    put(s, "0063_pr-draft", accepted("impl.md", **{"pr.md": "draft"}))
    put(s, "0064_pr-rejected", accepted("impl.md", **{"pr.md": "rejected"}))
    put(s, "0065_plan-draft", accepted("spec.md", **{"plan.md": "draft"}))
    put(
        s,
        "0066_idea-and-intent",
        accepted("intent.md"),
        extra_files={"idea.md": text("idea.md", "draft")},
    )
    s.units["ws/0066_idea-and-intent"]["artifacts"]["idea.md"] = {"status": "draft"}


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> UnitStore:
    s = UnitStore(tmp_path_factory.mktemp("rules") / "store")
    s.workspaces = ["ws", "other"]
    build(s)
    s.units["other/0001_far"] = entry({"intent.md": "accepted"}, merged=True)
    (s.cos / "ideas").mkdir()
    return s


_NAMES = [
    f"{i:04d}_{slug}"
    for i, slug in [
        (1, "fresh"), (2, "pre-intent"), (3, "no-intent"), (4, "spec-missing"),
        (5, "plan-missing"), (6, "impl-missing"), (7, "paused"), (8, "dropped"),
        (9, "rejected"), (10, "finished"), (11, "agent-skip"), (12, "agent-skip-named"),
        (13, "person-skip"), (14, "stale"), (15, "spike-missing"), (16, "spike-draft"),
        (17, "spike-fails"), (18, "spike-fails-twice"),
        (20, "spike-no-verdict"), (21, "spike-ok"), (22, "spike-no-cite"),
        (24, "spike-skipped-spec"), (25, "fast-lane"),
        (26, "fast-lane-impl"), (27, "fast-lane-left"), (28, "review-missing"),
        (29, "review-draft"), (30, "changes-requested"), (31, "out-of-rounds"),
        (32, "more-rounds"), (33, "unfinished"), (34, "incomplete"), (35, "awaits-person"),
        (36, "awaits-unclaimed"), (37, "person-answered"), (38, "every-claimed"),
        (39, "ship-refused"), (40, "ship-draft-old"), (41, "ship-missing"),
        (42, "draft-answered"), (43, "draft-asking"), (44, "waits-on-dependency"),
        (45, "depends-unknown"), (46, "depends-merged"), (47, "merged-one"),
        (50, "no-status-line"),
        (51, "odd-status"), (52, "unknowns"), (53, "closed-with-hold"), (54, "bad-hold-move"),
        (55, "resumed"), (56, "hold-no-reason"), (57, "no-type"), (58, "bad-type"),
        (59, "app-only"), (60, "stray"), (61, "review-limit-one"), (62, "impl-draft"),
        (63, "pr-draft"), (64, "pr-rejected"), (65, "plan-draft"), (66, "idea-and-intent"), (67, "pr-bad-title"), (68, "stale-review"), (69, "stale-ship"),
    ]
]  # fmt: skip
UNITS = [*_NAMES, "scratch"]
# The units that share a branch with another are left out of the sweeps over every unit.
_ALIKE = {12, 13, 19, 20, 23, 29, 36, 40, 53, 56, 59, 60, 62, 64, 65, 66}
SWEPT = [u for u in UNITS if not (u[:4].isdigit() and int(u[:4]) in _ALIKE)]
# Each stage, the alias `implement`, and a name that is none.
STAGES_ASKED = [*STAGE_NAMES, "implement", "bogus"]


# --- status -----------------------------------------------------------------------------


def test_status_table_of_every_shape(world):
    r = expect(world.argv("status"))
    assert r.code == 0
    assert "Next action" in r.out
    assert "Problems (report these" in r.out


def test_status_json_prints_whole_units(world):
    r = expect(world.argv("status", "--json"))
    body = json.loads(r.out)
    assert "ideas" not in body
    assert len(body["units"]) == len(UNITS)
    assert any(u.get("moreRounds") for u in body["units"])


def test_status_of_an_empty_store(store):
    r = expect(store.argv("status"))
    assert r.out == "No work units yet. `write-intent` opens one.\n"
    expect(store.argv("status", "--json"))


def test_status_with_a_unit_missing_from_the_snapshot(store):
    store.unit("0001_unknown-to-app", {"intent.md": text("intent.md", "accepted")}, None)
    expect(store.argv("status"))
    expect(store.argv("status", "--json"))


def test_status_reads_the_snapshot_from_stdin(world):
    path = world.state()
    argv = ["status", "--root", str(world.root), "--state", "-"]
    expect(argv, stdin=path.read_text())


# --- gate -------------------------------------------------------------------------------


@pytest.mark.parametrize("unit", SWEPT)
def test_gate_json_of_every_stage(world, unit):
    for stage in STAGES_ASKED:
        r = expect(world.argv("gate", unit, stage, "--json"))
        body = json.loads(r.out)
        assert body["ok"] == (r.code == 0)
        assert body["reasons"] is not None


def test_gate_opens_every_stage_of_a_finished_unit(world):
    for stage in ["intent", "spec", "plan", "impl"]:
        assert expect(world.argv("gate", "0010_finished", stage)).code == 0


# --- next -------------------------------------------------------------------------------


@pytest.mark.parametrize("unit", SWEPT)
def test_next_of_every_shape(world, unit):
    r = expect(world.argv("next", unit))
    body = json.loads(r.out)
    assert body["unit"] == unit
    assert body["reasons"]


@pytest.mark.parametrize("limit", ["1", "2"])
@pytest.mark.parametrize(
    "unit",
    [
        "0031_out-of-rounds", "0032_more-rounds", "0033_unfinished", "0034_incomplete",
        "0030_changes-requested", "0035_awaits-person",
    ],
)  # fmt: skip
def test_next_and_gate_under_a_review_round_limit(world, unit, limit):
    environ = env(COS_REVIEW_ROUNDS=limit)
    expect(world.argv("next", unit), environ=environ)
    for stage in ("review", "ship"):
        expect(world.argv("gate", unit, stage, "--json"), environ=environ)


def test_next_with_a_repo_that_cannot_answer(world, tmp_path):
    repo = git_repo(tmp_path / "repo")
    # What a logged-in `gh` says of a repository with no remote, whoever runs the tests.
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text("#!/bin/sh\necho 'no git remotes found' >&2\nexit 1\n")
    gh.chmod(0o755)
    environ = env(PATH=f"{gh.parent}{os.pathsep}{os.environ['PATH']}")
    for unit in (
        "0030_changes-requested",
        "0028_review-missing",
        "0041_ship-missing",
        "0039_ship-refused",
    ):
        expect([*world.argv("next", unit), "--repo", str(repo)], environ=environ)
        for stage in ("review", "ship"):
            argv = [*world.argv("gate", unit, stage, "--json"), "--repo", str(repo)]
            expect(argv, environ=environ)


def test_next_with_a_repo_that_is_not_one(world, tmp_path):
    expect([*world.argv("next", "0028_review-missing"), "--repo", str(tmp_path / "nowhere")])


# --- every code the loop hands out without a repository --------------------------------

# `next`'s `why`, and a gate's codes, that files alone settle. `ci-*`, `recording-ship` and the
# ones that read git are the probe's, in `test_repo_rules.py`.
WITHOUT_A_REPO = {
    "dependency", "unreadable", "finished", "paused", "dropped", "needs-person", "spike-fails",
    "spike-missing", "missing", "rejected", "stale", "review-incomplete", "ship-refused",
    "draft", "awaits-person", "person-answered", "changes-requested", "waiting-on", "closed",
    "gate-closed", "not-in-lane", "agent-cannot-skip",
}  # fmt: skip


def test_the_units_hand_out_every_code_files_alone_can(world):
    state = json.loads(world.state().read_text())
    seen: set[str] = set()
    for name in UNITS:
        unit = read_unit(str(world.cos / name), name, state)
        seen.update(next_answer(unit)["reasons"])
        for stage in STAGES_ASKED:
            seen.update(gate_answer(unit, stage)["reasons"])
    assert WITHOUT_A_REPO <= seen, sorted(WITHOUT_A_REPO - seen)
    assert seen <= set(__import__("coscc.units.guards", fromlist=["REASONS"]).REASONS)


# --- reasons in `--json` match the words ------------------------------------------------


@pytest.mark.parametrize(
    ("unit", "stage", "codes"),
    [
        ("0007_paused", "impl", ["paused"]),
        ("0008_dropped", "impl", ["dropped"]),
        ("0009_rejected", "plan", ["rejected", "closed"]),
        ("0025_fast-lane", "spec", ["not-in-lane"]),
        ("0011_agent-skip", "plan", ["agent-cannot-skip"]),
        ("0014_stale", "plan", ["stale"]),
        ("0044_waits-on-dependency", "impl", ["waiting-on"]),
        ("0028_review-missing", "review", ["gate-closed"]),
        ("0017_spike-fails", "plan", ["spike-fails"]),
        ("0015_spike-missing", "plan", ["missing", "spike-missing"]),
        ("0004_spec-missing", "ship", ["missing", "missing", "missing"]),
        ("0999_nothing", "bogus", ["unreadable"]),
    ],
)  # fmt: skip
def test_gate_json_codes_are_the_ones_the_words_explain(world, unit, stage, codes):
    if unit == "0999_nothing":
        unit = "0001_fresh"
    r = expect(world.argv("gate", unit, stage, "--json"))
    got = json.loads(r.out)["reasons"]
    assert got == list(dict.fromkeys(codes))


# --- an impl left in draft ----------------------------------------------------------------

ASKED_ENTRY = {"questions": [{"n": 1, "text": "Ai chịu trách nhiệm cho phần này?"}]}


class AnImplLeftInDraftIsToBeContinued(unittest.TestCase):
    """`next` adds `continue: "impl"` to the `draft` answer only when impl itself has more to write."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.store = UnitStore(Path(self._dir.name) / "store")

    def _next(self, name: str) -> dict:
        state = json.loads(self.store.state().read_text())
        unit = read_unit(str(self.store.cos / name), name, state)
        return next_answer(unit)

    def test_a_draft_impl_with_no_question_and_no_claim_is_continued(self):
        put(self.store, "0001_a", accepted("plan.md", **{"impl.md": "draft"}))
        answer = self._next("0001_a")
        self.assertEqual((answer["stage"], answer["reasons"]), ("", ["draft"]))
        self.assertEqual(answer["continue"], "impl")
        self.assertNotIn("rerun", answer)

    def test_a_draft_impl_whose_result_claims_nothing_is_continued(self):
        put(
            self.store,
            "0001_a",
            accepted("plan.md", **{"impl.md": "draft"}),
            impl_md={"result": {"needs_person": []}},
        )
        self.assertEqual(self._next("0001_a")["continue"], "impl")

    def test_next_prints_continue_as_the_name_of_the_stage(self):
        put(self.store, "0001_a", accepted("plan.md", **{"impl.md": "draft"}))
        body = json.loads(python(self.store.argv("next", "0001_a")).out)
        self.assertEqual(
            (body["stage"], body["continue"], body["reasons"]), ("", "impl", ["draft"])
        )

    def test_an_open_question_of_impl_is_not_continued(self):
        put(
            self.store,
            "0001_a",
            accepted("plan.md", **{"impl.md": "draft"}),
            texts={"impl.md": text("impl.md", "draft", body=ASKED)},
            impl_md=ASKED_ENTRY,
        )
        self.assertNotIn("continue", self._next("0001_a"))

    def test_impl_questions_all_answered_are_a_rerun_and_not_a_continue(self):
        answer = {**ANSWER, "artifact": "impl.md"}
        put(
            self.store,
            "0001_a",
            accepted("plan.md", **{"impl.md": "draft"}),
            texts={"impl.md": text("impl.md", "draft", body=ASKED)},
            impl_md=ASKED_ENTRY,
            answers=[answer],
        )
        got = self._next("0001_a")
        self.assertEqual(got["rerun"], "impl")
        self.assertNotIn("continue", got)

    def test_an_impl_that_hands_a_finding_to_a_person_is_not_continued(self):
        put(
            self.store,
            "0001_a",
            accepted("plan.md", **{"impl.md": "draft"}),
            impl_md={"result": {"needs_person": ["F1"]}},
        )
        self.assertNotIn("continue", self._next("0001_a"))

    def test_a_draft_plan_is_never_continued(self):
        put(self.store, "0001_a", accepted("spec.md", **{"plan.md": "draft"}))
        got = self._next("0001_a")
        self.assertEqual(got["reasons"], ["draft"])
        self.assertNotIn("continue", got)

    def test_an_impl_not_yet_run_or_accepted_is_not_continued(self):
        put(self.store, "0001_a", accepted("plan.md"))
        put(self.store, "0002_b", accepted("impl.md"))
        for name in ("0001_a", "0002_b"):
            self.assertNotIn("continue", self._next(name), name)

    def test_the_status_listing_does_not_carry_continue(self):
        put(self.store, "0001_a", accepted("plan.md", **{"impl.md": "draft"}))
        units = json.loads(python(self.store.argv("status", "--json")).out)["units"]
        self.assertNotIn("continue", units[0]["next"])


# --- a ship that asked for a merge ----------------------------------------------------------


class AShipThatAskedForAMergeIsMergingNotRefused(unittest.TestCase):
    """`ship.md` draft with `Round:` and no `Refused:` line: ship asked GitHub to merge and has
    not written the outcome yet."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.store = UnitStore(Path(self._dir.name) / "store")

    def _put(self, name: str, went_out: str) -> None:
        put(self.store, name, accepted("pr.md", **{"review.md": "accepted", "ship.md": "draft"}),
            texts={"review.md": review("accepted", round_(1, "pass")),
                   "ship.md": text("ship.md", "draft", head="Round: 1.", body=f"## What went out\n{went_out}")})  # fmt: skip

    def test_status_and_next_say_merging_and_never_ship_refused(self):
        self._put("0001_a", "Merge requested.\n")
        [row] = json.loads(python(self.store.argv("status", "--json")).out)["units"]
        self.assertEqual(
            row["next"],
            {
                "blocked": True,
                "action": "ship is merging #7 — wait",
                "stage": "",
                "why": "ship-merging",
            },
        )
        self.assertEqual(row["at"], "ship")
        self.assertTrue(row["betweenPrAndShip"])
        body = json.loads(python(self.store.argv("next", "0001_a")).out)
        self.assertEqual(
            (body["stage"], body["action"], body["reasons"]),
            ("", "ship is merging #7 — wait", ["ship-merging"]),
        )

    def test_a_refused_line_is_still_ship_refused(self):
        self._put("0001_a", "Refused: not up to date\n")
        state = json.loads(self.store.state().read_text())
        unit = read_unit(str(self.store.cos / "0001_a"), "0001_a", state)
        self.assertEqual(next_answer(unit)["reasons"], ["ship-refused"])
