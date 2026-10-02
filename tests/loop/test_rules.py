"""`status`, `gate` and `next` print what `cos.mjs` prints, for every shape of unit.

One store holds a unit for each rule that answers without a repository. Every test asks both
versions through `expect()`, which asserts stdout, stderr and the exit code are alike; the
coverage test then reads the codes each unit gets and checks every one `cos.mjs` can hand out
without git or `gh` is among them.
"""

from __future__ import annotations

import json

import pytest

from coscc.loop import STAGE_NAMES
from coscc.loop.model import above_answers, read_unit
from coscc.loop.rules import gate_answer, next_answer
from tests.loop.conftest import UnitStore, entry, env, git_repo, expect

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
UNMEASURED = "## Concerns\n- [unmeasured] U1 tốc độ đọc mười nghìn dòng chưa đo\n"
FENCE = "```\n$ time run\n3.2s\n```\n"
PR = "PR: https://github.com/o/r/pull/7\n"
MORE = (
    "\n## Answers\n### More rounds\nDecided by: Phong. Date: 2026-10-01. Via: board.\nRounds: 2\n"
)
NEEDS = "## Needs a person\n- F1: cần quyết định về giấy phép dữ liệu\n"
FIX_INTENT = (
    "# Intent: lỗi\nAuthor: test. Status: accepted. Type: fix.\n\n"
    "## Reproduction\n```\n$ run\nboom\n```\n\n## Expected\nSource: src/a.py:1-3\n"
    "trả về 3\n\n## Actual\ntrả về 2\n"
)
ASKED = "## Open questions\n1. Ai chịu trách nhiệm cho phần này?\n"
ANSWER = {"artifact": "intent.md", "n": 1, "id": None, "text": "Người dùng.", **PERSON}


def spike_text(status: str, verdict: str | None, rnd: int = 1, fence: bool = True) -> str:
    said = "" if verdict is None else f"Verdict: {verdict}.\n"
    body = f"## U1\n{said}\n{FENCE if fence else ''}"
    return text("spike.md", status, head=f"Round: {rnd}.", body=body)


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
    sp_u = text("spec.md", "accepted", body=UNMEASURED)
    put(s, "0015_spike-missing", accepted("spec.md"), texts={"spec.md": sp_u})
    put(s, "0016_spike-draft", accepted("spec.md", **{"spike.md": "draft"}),
        texts={"spec.md": sp_u, "spike.md": spike_text("draft", "holds")})  # fmt: skip
    put(s, "0017_spike-fails", accepted("spec.md", **{"spike.md": "accepted"}),
        texts={"spec.md": sp_u, "spike.md": spike_text("accepted", "fails")})  # fmt: skip
    put(s, "0018_spike-fails-twice", accepted("spec.md", **{"spike.md": "accepted"}),
        texts={"spec.md": sp_u, "spike.md": spike_text("accepted", "fails", rnd=2)})  # fmt: skip
    put(s, "0019_spike-no-fence", accepted("spec.md", **{"spike.md": "accepted"}),
        texts={"spec.md": sp_u, "spike.md": spike_text("accepted", "holds", fence=False)})  # fmt: skip
    put(s, "0020_spike-no-verdict", accepted("spec.md", **{"spike.md": "accepted"}),
        texts={"spec.md": sp_u, "spike.md": spike_text("accepted", None)})  # fmt: skip
    cite = text("plan.md", "accepted", body="Dựa trên spike.md ## U1.\n")
    put(s, "0021_spike-ok", accepted("plan.md", **{"spike.md": "accepted"}),
        texts={"spec.md": sp_u, "spike.md": spike_text("accepted", "holds"), "plan.md": cite})  # fmt: skip
    put(s, "0022_spike-no-cite", accepted("plan.md", **{"spike.md": "accepted"}),
        texts={"spec.md": sp_u, "spike.md": spike_text("accepted", "holds")})  # fmt: skip
    put(s, "0023_spike-unnamed", accepted("spec.md"),
        texts={"spec.md": text("spec.md", "accepted", body="## Concerns\n- [unmeasured] chưa có mã\n")})  # fmt: skip
    put(s, "0024_spike-skipped-spec", accepted("intent.md", **{"spec.md": "skipped"}),
        texts={"spec.md": text("spec.md", "skipped", body=UNMEASURED)}, spec_md={"authority": "person"})  # fmt: skip
    put(s, "0025_fast-lane", {"intent.md": "accepted"}, texts={"intent.md": FIX_INTENT}, type="fix")
    put(s, "0026_fast-lane-impl", {"intent.md": "accepted", "impl.md": "accepted"},
        texts={"intent.md": FIX_INTENT}, type="fix")  # fmt: skip
    put(s, "0027_fast-lane-left", accepted("plan.md", **{"impl.md": "draft"}),
        texts={"intent.md": FIX_INTENT, "impl.md": text("impl.md", "draft", head="Lane: full.")}, type="fix")  # fmt: skip
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
    impl_claim = {"impl.md": text("impl.md", "accepted", body=NEEDS)}
    put(s, "0035_awaits-person", accepted("pr.md", **{"review.md": "changes-requested"}),
        texts={**pr, **impl_claim, "review.md": review("changes-requested", needs)})  # fmt: skip
    put(s, "0036_awaits-no-reason", accepted("pr.md", **{"review.md": "changes-requested"}),
        texts={**pr, "review.md": review("changes-requested", needs)})  # fmt: skip
    put(s, "0037_person-answered", accepted("pr.md", **{"review.md": "changes-requested"}),
        texts={**pr, **impl_claim, "review.md": review("changes-requested", needs)},
        answers=[{**ANSWER, "artifact": "review.md", "n": None, "id": "F1"}])  # fmt: skip
    put(s, "0038_every-claimed", accepted("pr.md", **{"review.md": "changes-requested"}),
        texts={**pr, **impl_claim, "review.md": review("changes-requested", round_(1, "changes-requested", F1))})  # fmt: skip
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
        links={"idea": None, "repo": None, "dependsOn": ["0006_impl-missing"]})  # fmt: skip
    put(s, "0045_depends-unknown", accepted("plan.md"),
        links={"idea": None, "repo": "ws", "dependsOn": ["0999_ghost", "ma/lformed", "ws/0045_depends-unknown"]})  # fmt: skip
    put(s, "0046_depends-merged", accepted("plan.md"),
        links={"idea": None, "repo": "ws", "dependsOn": ["0047_merged-one", "other/0001_far"]})  # fmt: skip
    put(s, "0047_merged-one", accepted("ship.md", **{"plan.md": "done"}), merged=True)
    put(s, "0048_idea-unreadable", accepted("plan.md"),
        links={"idea": "ideas/0001_big.md", "repo": "ws", "dependsOn": None})  # fmt: skip
    s.ideas["ws"] = [
        {
            "id": "0001_big",
            "problems": ["lists a unit twice"],
            "units": [{"ref": "ws/0049_idea-mismatch", "dependsOn": ["0047_merged-one"]}],
        }  # fmt: skip
    ]
    put(s, "0049_idea-mismatch", accepted("plan.md"),
        links={"idea": "ideas/0001_big.md", "repo": "ws", "dependsOn": None})  # fmt: skip
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
        (17, "spike-fails"), (18, "spike-fails-twice"), (19, "spike-no-fence"),
        (20, "spike-no-verdict"), (21, "spike-ok"), (22, "spike-no-cite"),
        (23, "spike-unnamed"), (24, "spike-skipped-spec"), (25, "fast-lane"),
        (26, "fast-lane-impl"), (27, "fast-lane-left"), (28, "review-missing"),
        (29, "review-draft"), (30, "changes-requested"), (31, "out-of-rounds"),
        (32, "more-rounds"), (33, "unfinished"), (34, "incomplete"), (35, "awaits-person"),
        (36, "awaits-no-reason"), (37, "person-answered"), (38, "every-claimed"),
        (39, "ship-refused"), (40, "ship-draft-old"), (41, "ship-missing"),
        (42, "draft-answered"), (43, "draft-asking"), (44, "waits-on-dependency"),
        (45, "depends-unknown"), (46, "depends-merged"), (47, "merged-one"),
        (48, "idea-unreadable"), (49, "idea-mismatch"), (50, "no-status-line"),
        (51, "odd-status"), (52, "unknowns"), (53, "closed-with-hold"), (54, "bad-hold-move"),
        (55, "resumed"), (56, "hold-no-reason"), (57, "no-type"), (58, "bad-type"),
        (59, "app-only"), (60, "stray"), (61, "review-limit-one"), (62, "impl-draft"),
        (63, "pr-draft"), (64, "pr-rejected"), (65, "plan-draft"), (66, "idea-and-intent"), (67, "pr-bad-title"), (68, "stale-review"), (69, "stale-ship"),
    ]
]  # fmt: skip
UNITS = [*_NAMES, "scratch"]
# Each stage, the alias `implement`, and a name that is none.
STAGES_ASKED = [*STAGE_NAMES, "implement", "bogus"]


# --- status -----------------------------------------------------------------------------


def test_status_table_of_every_shape(world):
    r = expect(world.argv("status"))
    assert r.code == 0
    assert "Next action" in r.out
    assert "Problems (report these" in r.out


def test_status_json_prints_whole_units_and_the_ideas(world):
    r = expect(world.argv("status", "--json"))
    body = json.loads(r.out)
    assert body["ideas"][0]["id"] == "0001_big"
    assert len(body["units"]) == len(UNITS)
    assert any(u.get("moreRounds") for u in body["units"])


def test_status_json_flag_may_stand_anywhere(world):
    expect(["--json", *world.argv("status")])


def test_status_of_an_empty_store(store):
    r = expect(store.argv("status"))
    assert r.out == "No work units yet. `write-intent` opens one.\n"
    expect(store.argv("status", "--json"))


def test_status_of_an_empty_store_lists_the_idea_problems(store):
    (store.cos / "ideas").mkdir()
    store.ideas["ws"] = [{"id": "0001_big", "problems": ["no Units section", "x"], "units": []}]
    r = expect(store.argv("status"))
    assert "  - ideas/0001_big: no Units section" in r.out
    r = expect(store.argv("status", "--json"))
    assert json.loads(r.out)["ideas"][0]["problems"][0] == "no Units section"


def test_status_without_an_ideas_directory_has_no_ideas_key(store):
    put(store, "0001_a", {"intent.md": "draft"})
    store.ideas["ws"] = [{"id": "0001_big", "problems": [], "units": []}]
    r = expect(store.argv("status", "--json"))
    assert "ideas" not in json.loads(r.out)


def test_status_with_an_empty_ideas_directory_has_an_empty_ideas_key(store):
    put(store, "0001_a", {"intent.md": "draft"})
    (store.cos / "ideas").mkdir()
    r = expect(store.argv("status", "--json"))
    assert json.loads(r.out)["ideas"] == []
    expect(store.argv("status"))


def test_status_with_a_unit_missing_from_the_snapshot(store):
    store.unit("0001_unknown-to-app", {"intent.md": text("intent.md", "accepted")}, None)
    expect(store.argv("status"))
    expect(store.argv("status", "--json"))


def test_status_of_a_vietnamese_title_and_hold_reason(store):
    put(store, "0001_tiếng-việt", {"intent.md": "accepted"})
    put(store, "0002_ok", {"intent.md": "accepted"}, holds=[{"state": "paused", **HOLD}])
    expect(store.argv("status"))
    expect(store.argv("status", "--json"))


@pytest.mark.parametrize("limit", ["1", "2", "5"])
def test_status_under_a_review_round_limit(world, limit):
    argv = world.argv("status", "--json")
    expect(argv, environ=env(COS_REVIEW_ROUNDS=limit))
    expect(world.argv("status"), environ=env(COS_REVIEW_ROUNDS=limit))


def test_status_reads_the_snapshot_from_stdin(world):
    path = world.state()
    argv = ["status", "--root", str(world.root), "--state", "-"]
    expect(argv, stdin=path.read_text())


# --- gate -------------------------------------------------------------------------------


@pytest.mark.parametrize("unit", UNITS)
def test_gate_json_of_every_stage(world, unit):
    for stage in STAGES_ASKED:
        r = expect(world.argv("gate", unit, stage, "--json"))
        body = json.loads(r.out)
        assert body["ok"] == (r.code == 0)
        assert body["reasons"] is not None


@pytest.mark.parametrize("unit", UNITS)
def test_gate_text_of_every_stage(world, unit):
    for stage in ["impl", "review", "ship", "implement", "bogus"]:
        r = expect(world.argv("gate", unit, stage))
        first = (r.out or r.err).splitlines()[0]
        assert first.startswith(("open: ", "blocked: "))


@pytest.mark.parametrize("stage", STAGES_ASKED)
def test_gate_text_of_a_unit_with_no_artifact_at_all(store, stage):
    store.unit("0001_empty", {}, entry())
    expect(store.argv("gate", "0001_empty", stage))
    expect(store.argv("gate", "0001_empty", stage, "--json"))


def test_gate_opens_every_stage_of_a_finished_unit(world):
    for stage in ["intent", "spec", "plan", "impl"]:
        assert expect(world.argv("gate", "0010_finished", stage)).code == 0


def test_gate_opens_with_the_gate_flag_first(world):
    expect(["--json", *world.argv("gate", "0006_impl-missing", "impl")])
    expect(world.argv("gate", "--json", "0006_impl-missing", "impl"))


def test_gate_names_the_unit_and_stage_it_was_asked(world):
    r = expect(world.argv("gate", "0006_impl-missing", "implement"))
    assert r.out == "open: implement may proceed for 0006_impl-missing\n"


@pytest.mark.parametrize(
    "words",
    [
        [],
        ["0001_fresh"],
        ["--json"],
        ["0001_fresh", "--json"],
        ["0999_none", "impl"],
        ["x", "bogus"],
    ],
)
def test_gate_misuse_is_refused_alike(world, words):
    r = expect(world.argv("gate", *words))
    assert r.code == 2


# --- next -------------------------------------------------------------------------------


@pytest.mark.parametrize("unit", UNITS)
def test_next_of_every_shape(world, unit):
    r = expect(world.argv("next", unit))
    body = json.loads(r.out)
    assert body["unit"] == unit
    assert body["reasons"]


@pytest.mark.parametrize("limit", ["1", "2", "9"])
@pytest.mark.parametrize(
    "unit",
    [
        "0031_out-of-rounds", "0032_more-rounds", "0033_unfinished", "0034_incomplete",
        "0030_changes-requested", "0061_review-limit-one", "0035_awaits-person",
        "0038_every-claimed", "0018_spike-fails-twice",
    ],
)  # fmt: skip
def test_next_and_gate_under_a_review_round_limit(world, unit, limit):
    environ = env(COS_REVIEW_ROUNDS=limit)
    expect(world.argv("next", unit), environ=environ)
    for stage in ("review", "ship"):
        expect(world.argv("gate", unit, stage, "--json"), environ=environ)


@pytest.mark.parametrize("raw", ["0", "-1", "abc", "2.5", " 4 ", "04"])
def test_a_broken_or_odd_review_limit(world, raw):
    for cmd in ("status", "next"):
        argv = world.argv(cmd, *(["0030_changes-requested"] if cmd == "next" else []))
        expect(argv, environ=env(COS_REVIEW_ROUNDS=raw))


@pytest.mark.parametrize("words", [[], ["0999_none"], ["--json"]])
def test_next_misuse_is_refused_alike(world, words):
    assert expect(world.argv("next", *words)).code == 2


def test_next_names_the_branch_it_cannot_read_without_a_repo(world):
    r = expect(world.argv("next", "0030_changes-requested"))
    assert "no repository given to tell which: pass --repo" in json.loads(r.out)["action"]


def test_next_with_a_repo_that_cannot_answer(world, tmp_path):
    repo = git_repo(tmp_path / "repo")
    for unit in (
        "0030_changes-requested",
        "0028_review-missing",
        "0041_ship-missing",
        "0039_ship-refused",
    ):
        expect([*world.argv("next", unit), "--repo", str(repo)])
        for stage in ("review", "ship"):
            expect([*world.argv("gate", unit, stage, "--json"), "--repo", str(repo)])


def test_next_with_a_repo_that_is_not_one(world, tmp_path):
    expect([*world.argv("next", "0028_review-missing"), "--repo", str(tmp_path / "nowhere")])


# --- every code `cos.mjs` hands out without a repository --------------------------------

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
        ("0048_idea-unreadable", "impl", ["unreadable"]),
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
