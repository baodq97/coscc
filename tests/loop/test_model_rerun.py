"""The stage a unit is at, the rerun of an accepted stage, the screenshots retake and the ship
that a refused merge leaves, called directly or through the CLI, each held to fixed values.

Ported from the `test(` calls of the loop's former JavaScript suite (`tests/loop/ported.txt` maps
each one here). Helpers and constants come from `test_model`, as they did from one suite's glue.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from coscc.loop import RERUNNABLE
from coscc.loop.model import (
    above_answers,
    parse_answers,
    parse_reruns,
    parse_ship,
    parse_status,
)
from coscc.loop.probe import UI_STANDARD
from coscc.loop.repo_rules import screens_answer, screens_needs
from coscc.loop.rules import check_gate, decide, next_action, next_answer, next_step, stage_at
from tests.loop.conftest import TZ, git
from tests.loop.test_model_links import MERGE, merged_probe
from tests.loop.test_model import (
    CHAIN,
    FULL_LANE,
    SHA,
    answer_block,
    art,
    branched,
    cli,
    files_in,
    green_probe,
    hold_block,
    impl_text,
    ok,
    parse_hold,
    passed,
    read,
    review_art,
    round_,
    unit,
    with_answers,
)

# --- helpers ----------------------------------------------------------------------------------


def tree(tmp_path: Path, files: dict[str, str], name: str = "0001_q") -> tuple[Path, Path]:
    """A `--root` of its own under `tmp_path`, holding the unit `name` with `files`."""
    root = tmp_path / f"t{len(list(tmp_path.iterdir()))}"
    d = root / ".cos" / name
    d.mkdir(parents=True)
    for f, text in files.items():
        (d / f).write_text(text)
    return root, d


def run_in(root: Path, *words: str):
    return cli(*words, "--root", str(root))


def json_of(root: Path, *words: str):
    out = run_in(root, *words)
    assert out.code == 0, out.err
    return json.loads(out.out)


def today() -> str:
    return datetime.now(ZoneInfo(TZ)).strftime("%Y-%m-%d")


# --- the stage a unit is at ---------------------------------------------------------------------


def test_stage_at_is_the_stage_next_names_else_the_last_artifact_else_the_first_stage():
    def at(artifacts):
        u = unit(artifacts)
        return stage_at(u, next_action(u))

    assert at({"intent.md": art("accepted"), "spec.md": art("accepted")}) == "plan"
    assert (
        at({"intent.md": art("accepted"), "spec.md": art("accepted"), "plan.md": art("draft")})
        == "plan"
    )
    requested = {
        **CHAIN,
        "review.md": review_art(
            "changes-requested", round_(1, "changes-requested", ["- F1 [open] x"])
        ),
    }
    assert next_action(unit(requested))["stage"] == ""
    assert at(requested) == "review"
    assert at({"idea.md": art("accepted")}) == "intent"
    assert stage_at(unit({}), {"stage": ""}) == "intent"


def test_status_json_carries_at_and_next_why_for_every_unit(tmp_path):
    root = tmp_path / "root"
    for name, files in {
        "0001_open": {"intent.md": "# X\nType: feat. Status: accepted.\n"},
        "0002_draft": {"intent.md": "# X\nType: fix. Status: draft.\n"},
        "0003_done": {
            "intent.md": "# X\nType: fix. Status: accepted.\n",
            "plan.md": "# X\nStatus: done.\n",
        },
    }.items():
        d = root / ".cos" / name
        d.mkdir(parents=True)
        for f, text in files.items():
            (d / f).write_text(text)
    status = json_of(root, "status", "--json")
    assert [[u["name"], u["at"], u["next"]["why"]] for u in status["units"]] == [
        ["0001_open", "spec", "missing"],
        ["0002_draft", "intent", "draft"],
        ["0003_done", "plan", "finished"],
    ]
    assert [s["name"] for s in status["stages"]] == [
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


def test_the_line_next_prints_carries_no_why(tmp_path):
    root, _ = tree(tmp_path, {"intent.md": "# X\nType: feat. Status: accepted.\n"}, "0001_open")
    line = json_of(root, "next", "0001_open")
    assert line["stage"] == "spec"
    assert "why" not in line
    assert "at" not in line


# --- an accepted stage run again from the board -------------------------------------------------

# A unit with every artifact up to a passed review, like the board's one awaiting its ship.
RERUN_FILES = {
    "intent.md": "# Intent: x\nType: feat. Status: accepted.\n\n## Open questions\n\n1. Một?\n",
    "spec.md": "# Spec: x\nIntent: intent.md. Status: accepted.\n\nR1.\n",
    "plan.md": "# Plan: x\nStatus: accepted.\n\n1. build it\n",
    "impl.md": "# Impl: x\nStatus: accepted.\n\nbuilt\n",
    "pr.md": "# PR: feat(0003): x\nPR: https://github.com/o/r/pull/7. Status: accepted.\n\nbody\n",
    "review.md": f"# Review: x\nStatus: accepted.\n\n{round_(1, 'pass')}",
}


class RerunTree:
    """A `--root` holding one unit, and what the board does with it."""

    def __init__(self, tmp_path: Path, files=None, name: str = "0003_awaiting-ship"):
        self.name = name
        self.root, self.dir = tree(tmp_path, RERUN_FILES if files is None else files, name)

    def cli(self, *args: str):
        return run_in(self.root, *args)

    def read(self):
        return read(self.dir, self.name)

    def write(self, file: str, text: str) -> None:
        (self.dir / file).write_text(text)

    def append(self, file: str, tail: str) -> None:
        """What the app does with the block: append it under `## Answers`."""
        was = (self.dir / file).read_text()
        opened = "" if "\n## Answers" in was else "\n## Answers\n"
        self.write(file, was + opened + "\n" + tail)

    def offers(self) -> list:
        return json.loads(self.cli("rerun", self.name).out)["offers"]

    def rerun(self, stage: str) -> dict:
        out = self.cli("rerun", self.name, stage)
        assert out.code == 0, out.err
        answer = json.loads(out.out)
        self.append("intent.md", answer["block"])
        return answer


def test_the_hash_above_answers_does_not_move_when_an_answer_is_appended():
    text = "# Spec: x\nStatus: accepted.\n\nR1.\n"
    digest = above_answers(text)
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    # The two ways a section is opened: the runner's `with_answers`, and the app's append.
    answered = (
        f"{text}\n## Answers\n\n### Câu 1\nAnswered by: A. Date: 2026-09-26. Via: product.\n\nx\n"
    )
    assert above_answers(answered) == digest
    assert above_answers(f"{text.rstrip()}\n\n## Answers\n") == digest
    assert above_answers(text.replace("R1.", "R1, rewritten.")) != digest


def test_a_rerun_block_ends_the_answer_before_it_and_is_never_an_answer():
    block = (
        "### Rerun\nRequested by: owner. Date: 2026-09-26. Via: product.\nStage: pr.\n"
        f"Stale: pr.md sha256:{'c' * 64}\n"
    )
    text = with_answers(answer_block(1, "A", "Có."), f"\n{block}", answer_block(2, "B", "Không."))
    assert [[a["n"], a["text"]] for a in parse_answers(text)] == [[1, "Có."], [2, "Không."]]
    assert parse_hold(text)["hold"] is None
    assert parse_reruns(text)["reruns"] == [
        {"stage": "pr", "by": "owner", "date": "2026-09-26", "stale": {"pr.md": "c" * 64}}
    ]


def test_a_malformed_rerun_block_is_ignored_and_reported(tmp_path):
    t = RerunTree(tmp_path)
    t.append(
        "intent.md",
        f"### Rerun\nStage: pr.\nStale: pr.md sha256:{above_answers(RERUN_FILES['pr.md'])}\n",
    )
    u = t.read()
    assert "stale" not in u["artifacts"]["pr.md"]
    assert re.search(r"intent\.md: rerun block 1 has no well-formed", "\n".join(u["problems"]))


def test_a_unit_up_to_a_passed_review_is_offered_intent_spec_plan_and_pr(tmp_path):
    t = RerunTree(tmp_path)
    out = t.cli("rerun", t.name)
    assert out.code == 0, out.err
    answer = json.loads(out.out)
    assert [o["stage"] for o in answer["offers"]] == ["intent", "spec", "plan", "pr"]
    assert answer["why"] == ""
    later = {o["stage"]: o["later"] for o in answer["offers"]}
    assert later["pr"] == ["review", "ship"]
    assert later["plan"] == ["impl", "pr", "review", "ship"]
    assert later["intent"] == ["spec", "plan", "impl", "pr", "review", "ship"]
    assert RERUNNABLE == ["intent", "spec", "spike", "plan", "pr"]


def test_the_block_names_the_stage_owner_and_a_hash_per_artifact_and_no_approval(tmp_path):
    t = RerunTree(tmp_path)
    out = t.cli("rerun", t.name, "pr")
    assert out.code == 0, out.err
    answer = json.loads(out.out)
    assert answer["stage"] == "pr"
    assert answer["later"] == ["review", "ship"]
    lines = answer["block"].rstrip().split("\n")
    assert lines[0] == "### Rerun"
    assert lines[1] == f"Requested by: owner. Date: {today()}. Via: product."
    assert lines[2] == "Stage: pr."
    # `ship.md` does not exist, so it has no line.
    assert lines[3:] == [
        f"Stale: pr.md sha256:{above_answers(RERUN_FILES['pr.md'])}",
        f"Stale: review.md sha256:{above_answers(RERUN_FILES['review.md'])}",
    ]
    assert not re.search(r"approved|accepted|Decided by", answer["block"], re.I)


def test_rerunning_pr_closes_ship_until_pr_and_then_review_are_written_again(tmp_path):
    t = RerunTree(tmp_path)
    probe = green_probe()

    def stale_needs(u):
        return [n for n in check_gate(u, "ship", probe)["need"] if "stale" in n]

    assert stale_needs(t.read()) == []
    t.rerun("pr")
    u = t.read()
    assert u["artifacts"]["pr.md"]["stale"] == {
        "stage": "pr",
        "date": u["artifacts"]["review.md"]["stale"]["date"],
    }
    # The stage run again comes first: a rerun that never ran is offered again.
    assert next_step(u, probe)["stage"] == "pr"
    assert re.search(
        r"^pr\.md is stale — pr was rerun on .*: pr again$", next_step(u, probe)["action"]
    )
    # An answer appended to pr.md does not make it fresh.
    t.append("pr.md", answer_block(1, "A", "x"))
    assert t.read()["artifacts"]["pr.md"].get("stale")

    t.write("pr.md", RERUN_FILES["pr.md"].replace("body", "a new body"))
    u = t.read()
    assert "stale" not in u["artifacts"]["pr.md"]
    assert u["artifacts"]["review.md"].get("stale")
    assert next_step(u, probe)["stage"] == "review"
    ship = check_gate(u, "ship", probe)
    assert ship["ok"] is False
    assert re.search(
        r"review\.md is stale: pr was rerun on .* — run review again first", "\n".join(ship["need"])
    )
    # Red CI sends a stale review back to impl, as a missing one would be.
    red = green_probe([{"name": "tests", "bucket": "fail"}])
    assert next_step(u, red)["stage"] == "impl"
    # With no repository, `next` offers nothing and says why, the review first.
    assert next_step(u)["action"].startswith("review.md is stale")
    assert next_step(u)["stage"] == ""

    t.write("review.md", f"{RERUN_FILES['review.md']}{round_(2, 'pass')}")
    assert "stale" not in t.read()["artifacts"]["review.md"]
    assert stale_needs(t.read()) == []


def test_a_stale_artifact_before_the_target_closes_its_gate_and_names_the_stage(tmp_path):
    t = RerunTree(tmp_path)
    t.rerun("plan")
    u = t.read()
    for f in ["plan.md", "impl.md", "pr.md", "review.md"]:
        assert u["artifacts"][f].get("stale"), f
    assert "stale" not in u["artifacts"]["spec.md"]
    assert check_gate(u, "plan")["ok"] is True
    impl = check_gate(u, "impl")
    assert impl["ok"] is False
    date = u["artifacts"]["plan.md"]["stale"]["date"]
    assert impl["need"] == [f"plan.md is stale: plan was rerun on {date} — run plan again first"]
    assert next_action(u)["stage"] == "plan"
    # Already stale: not offered again, the run button offers it.
    assert [o["stage"] for o in t.offers()] == ["intent", "spec"]
    assert t.cli("rerun", t.name, "plan").code == 1


def test_a_changes_requested_review_and_a_spike_the_spec_no_longer_needs_are_never_stale(tmp_path):
    review = "# Review: x\nStatus: changes-requested.\n\n" + round_(
        1, "changes-requested", ["- F1 [open] x"]
    )
    spike = "# Spike: x\nStatus: accepted.\n\n## U1\n\nVerdict: holds.\n"
    t = RerunTree(tmp_path, {**RERUN_FILES, "review.md": review, "spike.md": spike})
    t.append(
        "intent.md",
        "\n".join(
            [
                "### Rerun",
                "Requested by: owner. Date: 2026-09-26. Via: product.",
                "Stage: spec.",
                f"Stale: spike.md sha256:{above_answers(spike)}",
                f"Stale: review.md sha256:{above_answers(review)}",
                "",
            ]
        ),
    )
    u = t.read()
    assert "stale" not in u["artifacts"]["review.md"]
    assert "stale" not in u["artifacts"]["spike.md"]


def test_nothing_is_offered_on_a_finished_held_or_closed_unit(tmp_path):
    done = RerunTree(tmp_path, {**RERUN_FILES, "plan.md": "# Plan: x\nStatus: done.\n"})
    assert json.loads(done.cli("rerun", done.name).out) == {
        "unit": done.name,
        "offers": [],
        "why": "the unit is finished: plan.md is done",
    }
    refused = done.cli("rerun", done.name, "pr")
    assert refused.code == 1
    assert re.search(r"pr cannot be run again for .*: the unit is finished", refused.err)

    held = RerunTree(tmp_path)
    held.append("intent.md", hold_block("Paused", "chờ"))
    assert held.offers() == []

    closed = RerunTree(tmp_path, {**RERUN_FILES, "impl.md": "# Impl: x\nStatus: rejected.\n"})
    assert re.search(
        r"impl\.md is rejected", json.loads(closed.cli("rerun", closed.name).out)["why"]
    )


def test_only_an_accepted_artifact_is_offered_and_spike_only_when_the_spec_needs_one(tmp_path):
    fresh = RerunTree(tmp_path, {"intent.md": RERUN_FILES["intent.md"]}, "0001_fresh-intent")
    assert fresh.offers() == [
        {"stage": "intent", "later": ["spec", "plan", "impl", "pr", "review", "ship"]}
    ]
    draft = RerunTree(tmp_path, {**RERUN_FILES, "plan.md": "# Plan: x\nStatus: draft.\n"})
    assert [o["stage"] for o in draft.offers()] == ["intent", "spec"]
    # A skipped spec counts as settled; its later list has no spike.
    skipped = RerunTree(tmp_path, {**RERUN_FILES, "spec.md": "# Spec: x\nStatus: skipped.\n"})
    assert skipped.offers()[1] == {
        "stage": "spec",
        "later": ["plan", "impl", "pr", "review", "ship"],
    }

    spec = "# Spec: x\nStatus: accepted.\n\n## Concerns\n\n- [unmeasured] U1 nhanh không?\n"
    spike = "# Spike: x\nStatus: accepted.\n\n## U1\n\nVerdict: holds.\n\n```\n$ x\n1\n```\n"
    plan = "# Plan: x\nStatus: accepted.\n\n1. build it (spike.md ## U1)\n"
    measured = RerunTree(
        tmp_path, {**RERUN_FILES, "spec.md": spec, "spike.md": spike, "plan.md": plan}
    )
    offers = measured.offers()
    assert [o["stage"] for o in offers] == ["intent", "spec", "spike", "plan", "pr"]
    assert offers[1]["later"] == ["spike", "plan", "impl", "pr", "review", "ship"]


def test_rerun_refuses_a_stage_it_does_not_offer_an_unknown_one_and_repo(tmp_path):
    t = RerunTree(tmp_path)
    review = t.cli("rerun", t.name, "review")
    assert review.code == 1
    assert re.search(
        r"review cannot be run again from the board — only intent, spec, spike, plan, pr",
        review.err,
    )
    assert t.cli("rerun", t.name, "spike").code == 1
    assert t.cli("rerun", t.name, "nonsense").code == 2
    assert t.cli("rerun", "0009_nope").code == 2
    assert t.cli("rerun", "../x").code == 2
    assert t.cli("rerun").code == 2
    repo = t.cli("rerun", t.name, "--repo", str(t.root))
    assert repo.code == 2
    assert "--repo applies only to `gate` and `next`" in repo.err


# --- a draft whose questions are all answered names its stage as `rerun` ------------------------

DRAFT_INTENT = (
    "# Intent: x\nType: feat. Status: draft.\n\n## Open questions\n\n1. Một?\n2. Hai?\n\n"
    "## Answers\n"
)


class Answered:
    """A unit under `--root`, read, and what `next` prints of it."""

    def __init__(self, tmp_path: Path, files: dict[str, str]):
        self.root, d = tree(tmp_path, files)
        self.u = read(d, "0001_q")

    def next(self) -> dict:
        return json.loads(run_in(self.root, "next", "0001_q").out)


def test_a_draft_intent_with_every_question_answered_is_rerun_intent_and_nothing_else(tmp_path):
    a = Answered(
        tmp_path,
        {"intent.md": DRAFT_INTENT + answer_block(1, "A", "x") + answer_block(2, "A", "y")},
    )
    assert a.next() == {
        "unit": "0001_q",
        "stage": "",
        "action": "finish and accept intent.md",
        "blocked": True,
        "rerun": "intent",
        "reasons": ["draft"],
        **FULL_LANE,
    }


def test_one_question_left_unanswered_is_no_rerun(tmp_path):
    a = Answered(tmp_path, {"intent.md": DRAFT_INTENT + answer_block(1, "A", "x")})
    assert "rerun" not in a.next()
    assert next_step(a.u).get("rerun") is None


def test_a_held_unit_is_no_rerun_even_with_every_question_answered(tmp_path):
    a = Answered(
        tmp_path,
        {
            "intent.md": DRAFT_INTENT
            + answer_block(1, "A", "x")
            + answer_block(2, "A", "y")
            + hold_block("Paused", "chờ")
        },
    )
    n = a.next()
    assert n["hold"]["state"] == "paused"
    assert "rerun" not in n


def test_a_draft_with_no_questions_is_no_rerun(tmp_path):
    a = Answered(tmp_path, {"intent.md": "# I\nType: feat. Status: draft.\n"})
    assert "rerun" not in a.next()


def test_a_spec_draft_answered_in_full_is_rerun_spec(tmp_path):
    a = Answered(
        tmp_path,
        {
            "intent.md": "# I\nType: feat. Status: accepted.\n",
            "spec.md": "# S\nStatus: draft.\n\n## Open questions\n\n1. Một?\n\n## Answers\n"
            + answer_block(1, "A", "x"),
        },
    )
    assert next_step(a.u)["rerun"] == "spec"


def test_status_json_and_next_action_never_carry_rerun(tmp_path):
    a = Answered(
        tmp_path,
        {"intent.md": DRAFT_INTENT + answer_block(1, "A", "x") + answer_block(2, "A", "y")},
    )
    assert "rerun" not in next_action(a.u)
    # Not the root's own path, which may hold the word.
    assert "rerun" not in run_in(a.root, "status", "--json").out.replace(str(a.root), "")


# --- a draft impl.md asks a person, and runs again on the answer --------------------------------

# Every stage before `impl` accepted, so `decide` reaches `impl.md`.
BEFORE_IMPL = {
    "intent.md": "# I\nType: feat. Status: accepted.\n",
    "spec.md": "# S\nStatus: accepted.\n",
    "plan.md": "# P\nStatus: accepted.\n",
}
DRAFT_IMPL = "# Impl\nStatus: draft.\n\n## Open questions\n\n1. Chạy lệnh X rồi đưa kết quả?\n"


def impl_tree(tmp_path: Path, impl: str, extra: dict | None = None) -> Answered:
    return Answered(tmp_path, {**BEFORE_IMPL, "impl.md": impl, **(extra or {})})


def test_a_draft_impl_with_an_open_question_is_listed_and_counted(tmp_path):
    a = impl_tree(tmp_path, DRAFT_IMPL)
    u = json_of(a.root, "status", "--json")["units"][0]
    assert [
        {"artifact": q["artifact"], "n": q["n"], "answered": q["answered"]} for q in u["questions"]
    ] == [{"artifact": "impl.md", "n": 1, "answered": False}]
    assert u["counted"] == "impl.md"
    assert u["open"] == 1


def test_a_draft_impl_answered_in_full_is_rerun_impl(tmp_path):
    a = impl_tree(tmp_path, f"{DRAFT_IMPL}\n## Answers\n{answer_block(1, 'A', 'x')}")
    assert a.next() == {
        "unit": "0001_q",
        "stage": "",
        "action": "finish and accept impl.md",
        "blocked": True,
        "rerun": "impl",
        "reasons": ["draft"],
        **FULL_LANE,
    }


def test_a_draft_impl_with_one_question_unanswered_is_no_rerun(tmp_path):
    two = DRAFT_IMPL + "2. Đăng nhập rồi báo lại?\n"
    a = impl_tree(tmp_path, f"{two}\n## Answers\n{answer_block(1, 'A', 'x')}")
    assert "rerun" not in a.next()
    assert next_step(a.u).get("rerun") is None


def test_a_draft_impl_with_no_questions_is_no_rerun(tmp_path):
    a = impl_tree(tmp_path, "# Impl\nStatus: draft.\n\n## What is still open\n\nx\n")
    assert "rerun" not in a.next()


def test_a_paused_or_dropped_unit_with_an_answered_draft_impl_is_no_rerun(tmp_path):
    impl = f"{DRAFT_IMPL}\n## Answers\n{answer_block(1, 'A', 'x')}"
    for head, state in [("Paused", "paused"), ("Dropped", "dropped")]:
        intent = f"{BEFORE_IMPL['intent.md']}\n## Answers\n{hold_block(head, 'chờ')}"
        n = impl_tree(tmp_path, impl, {"intent.md": intent}).next()
        assert n["hold"]["state"] == state
        assert "rerun" not in n


def test_plan_draft_keeps_the_impl_gate_closed(tmp_path):
    a = impl_tree(
        tmp_path,
        f"{DRAFT_IMPL}\n## Answers\n{answer_block(1, 'A', 'x')}",
        {"plan.md": "# P\nStatus: draft.\n"},
    )
    out = run_in(a.root, "gate", "0001_q", "impl")
    assert out.code == 1
    assert 'plan.md is "draft"' in out.out + out.err


def test_status_json_names_the_stages_an_answered_draft_runs_again(tmp_path):
    a = impl_tree(tmp_path, DRAFT_IMPL)
    data = json_of(a.root, "status", "--json")
    assert data["afterAnswers"] == ["intent", "spec", "spike", "plan", "impl"]


# --- a rebase no longer leaves review with stale screenshots ------------------------------------

OLD = "c" * 40
MANIFEST = {
    "head": OLD,
    "dirty": False,
    "addresses": ["/board"],
    "hits": [{"address": "/board", "size": "1440x900", "kind": "path", "snippet": "/tmp/x"}],
    "shots": [],
}
UI = {"path": UI_STANDARD, "globs": ["coscc/screens.py"]}


def answer_probe(files=None, manifest=MANIFEST, ancestor=1, ui=UI, git_says=None):
    """A probe for `screens_answer`: the branch changes `files`, `.screens/manifest.json` reads
    `manifest`, and the manifest's head is no longer an ancestor of `HEAD` unless `ancestor`."""
    files = ["coscc/screens.py"] if files is None else files
    git_says = git_says or {}
    calls: list[str] = []

    def run(*args):
        key = " ".join(args)
        calls.append(key)
        if key in git_says:
            return git_says[key]
        if key == "diff --name-only origin/main...HEAD":
            return ok("\n".join(files))
        if key == f"merge-base --is-ancestor {OLD} HEAD":
            return {"code": ancestor, "out": "", "err": ""}
        return ok()

    return SimpleNamespace(calls=calls, ui=lambda: ui, manifest=lambda: manifest, git=run)


def without(d: dict, key: str) -> dict:
    return {k: v for k, v in d.items() if k != key}


def test_a_ui_unit_whose_clean_manifest_names_a_rewritten_head_is_taken_again():
    assert screens_answer(unit({}), answer_probe()) == {
        "unit": "0001_x",
        "ui": ["coscc/screens.py"],
        "manifest": {
            "head": OLD,
            "dirty": False,
            "addresses": ["/board"],
            "hits": MANIFEST["hits"],
        },
        "rewritten": True,
        "retake": True,
        "why": "",
    }
    # A commit gone from the object store is rewritten too: git exits 128, not 1.
    assert screens_answer(unit({}), answer_probe(ancestor=128))["retake"] is True


def test_each_condition_broken_once_is_no_retake_and_says_which():
    def said(**opts):
        a = screens_answer(unit({}), answer_probe(**opts))
        assert a["retake"] is False
        return a

    assert re.search(
        r"changes no file the UI standard counts as a screen",
        said(files=["coscc/runner.py"])["why"],
    )
    assert re.search(r"no readable \.screens/manifest\.json", said(manifest=None)["why"])
    assert said(manifest=None)["manifest"] is None
    assert re.search(r"no readable", said(manifest=[1, 2])["why"])
    assert re.search(r"lists no addresses", said(manifest={**MANIFEST, "addresses": []})["why"])
    assert re.search(r"lists no addresses", said(manifest=without(MANIFEST, "addresses"))["why"])
    assert re.search(r"uncommitted changes", said(manifest={**MANIFEST, "dirty": True})["why"])
    assert re.search(r"uncommitted changes", said(manifest=without(MANIFEST, "dirty"))["why"])
    kept = said(ancestor=0)
    assert kept["rewritten"] is False
    assert f"head {OLD} is still an ancestor of HEAD" in kept["why"]
    # A head that is no commit name is not handed to git at all.
    odd = answer_probe(manifest={**MANIFEST, "head": "--output=/tmp/x"})
    a = screens_answer(unit({}), odd)
    assert a["retake"] is False
    assert "names no commit" in a["why"]
    assert not any(c.startswith("merge-base") for c in odd.calls)


def test_no_standard_or_one_with_no_globs_is_ui_empty_and_asks_git_no_diff():
    for ui in [None, {"path": UI_STANDARD, "globs": []}]:
        probe = answer_probe(ui=ui)
        a = screens_answer(unit({}), probe)
        assert [a["ui"], a["retake"]] == [[], False]
        assert not any(c.startswith("diff") for c in probe.calls)


def test_git_that_cannot_diff_is_no_retake_and_why_is_its_error_and_main_alone_is_enough():
    fail = {"code": 128, "out": "", "err": "fatal: bad revision"}
    a = screens_answer(
        unit({}),
        answer_probe(
            git_says={
                "diff --name-only origin/main...HEAD": fail,
                "diff --name-only main...HEAD": fail,
            }
        ),
    )
    assert [a["ui"], a["retake"]] == [[], False]
    assert re.search(
        r"cannot tell whether 0001_x changes a screen: .*fatal: bad revision", a["why"]
    )
    local = screens_answer(
        unit({}),
        answer_probe(
            git_says={
                "diff --name-only origin/main...HEAD": fail,
                "diff --name-only main...HEAD": ok("coscc/screens.py\n"),
            }
        ),
    )
    assert local["retake"] is True


def ui_probe(files, extra=None, ui=UI):
    """A probe whose repository has a standard, and whose branch diff to the trunk is `files`."""
    diff = {f"diff --name-only origin/main...{SHA}": ok("\n".join(files)), **(extra or {})}
    probe = green_probe(None, diff)
    probe.ui = lambda: ui
    return probe


def test_screens_answer_and_the_ship_gate_leave_out_the_same_files():
    # The unit's own `.cos/` files are left out of both, even when a glob takes them.
    globs = {"path": UI_STANDARD, "globs": ["**/*.py"]}
    own = answer_probe(files=[".cos/0001_x/x.py"], ui=globs)
    assert screens_answer(unit({}), own)["ui"] == []
    g = check_gate(passed(), "ship", ui_probe([".cos/0001_x/x.py"], {}, globs))
    assert g == check_gate(passed(), "ship", green_probe())
    assert callable(screens_needs)


def test_screens_on_a_real_repository(tmp_path):
    store, _ = tree(tmp_path, {"intent.md": "# I\nType: fix. Status: accepted.\n"}, "0001_x")
    repo = tmp_path / "repo"
    (repo / ".claude" / "rules").mkdir(parents=True)
    (repo / "coscc").mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / UI_STANDARD).write_text('---\npaths:\n  - "coscc/screens.py"\n---\n')
    (repo / ".gitignore").write_text(".screens/\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "first")
    git(repo, "switch", "-q", "-c", "fix/x")
    (repo / "coscc" / "screens.py").write_text("# a screen\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "a screen")
    taken = git(repo, "rev-parse", "HEAD")
    git(repo, "commit", "-q", "--amend", "-m", "a screen, rewritten")
    (repo / ".screens").mkdir()
    manifest = repo / ".screens" / "manifest.json"
    manifest.write_text(json.dumps({**MANIFEST, "head": taken}))

    out = run_in(store, "screens", "0001_x", "--repo", str(repo))
    assert out.code == 0, out.err
    a = json.loads(out.out)
    assert [a["ui"], a["manifest"]["head"], a["rewritten"], a["retake"], a["why"]] == [
        ["coscc/screens.py"],
        taken,
        True,
        True,
        "",
    ]
    # Taken again at HEAD: nothing to do.
    manifest.write_text(json.dumps({**MANIFEST, "head": git(repo, "rev-parse", "HEAD")}))
    again = run_in(store, "screens", "0001_x", "--repo", str(repo))
    assert json.loads(again.out)["retake"] is False
    # Misuse is exit 2.
    assert run_in(store, "screens").code == 2
    assert run_in(store, "screens", "0002_none", "--repo", str(repo)).code == 2
    assert run_in(store, "screens", "../x", "--repo", str(repo)).code == 2
    bare = run_in(store, "screens", "0001_x")
    assert bare.code == 2
    assert "pass --repo" in bare.err
    # It writes nothing into the store.
    assert sorted(p.name for p in (store / ".cos" / "0001_x").iterdir()) == ["intent.md"]


# --- a passed unit behind main, and the ship.md a refused merge leaves --------------------------

BEHIND = "the head branch is not up to date with the base branch"
SHIP_OLD = (
    "# Ship: x\nReview: review.md. Author: A. Status: draft.\n\n"
    "## What went out\n\nNothing merged.\n"
)
REB = "d" * 40
TRUNK = "refs/remotes/origin/main"
NOT_ANCESTOR = {"code": 1, "out": "", "err": ""}


def ship_draft(n, refused=BEHIND):
    line = "" if refused is None else f"Refused: {refused}\n"
    return (
        f"# Ship: x\nReview: review.md. Round: {n}. Author: A. Status: draft.\n\n"
        f"## What went out\n\nNothing merged.\n{line}\n## What is still open\n\n"
        "Refused: not this one\n"
    )


def ship_art(text):
    """What `read_unit` attaches, without a directory."""
    ship = parse_ship(text)
    present = ship["round"] is not None or ship["refused"] is not None
    return {**art(parse_status(text)), **({"ship": ship} if present else {})}


def behind_by(k, git_says=None):
    return green_probe(
        None,
        {
            f"merge-base --is-ancestor {TRUNK} {SHA}": NOT_ANCESTOR,
            f"rev-list --count {SHA}..{TRUNK}": ok(f"{k}\n"),
            **(git_says or {}),
        },
    )


def accepted_pass(extra=None):
    return branched(
        {**CHAIN, "review.md": review_art("accepted", round_(1, "pass")), **(extra or {})}
    )


def test_parse_ship_reads_round_from_the_header_and_the_first_refused_line_of_what_went_out():
    assert parse_ship(ship_draft(2)) == {"round": 2, "refused": BEHIND}
    assert parse_ship(ship_draft(3, None)) == {"round": 3, "refused": None}
    assert parse_ship(SHIP_OLD) == {"round": None, "refused": None}
    # Indented is not the line.
    indented = (
        "# S\nReview: review.md. Round: 1. Status: draft.\n\n## What went out\n\n  Refused: no\n"
    )
    assert parse_ship(indented)["refused"] is None


def test_read_unit_attaches_ship_only_to_a_ship_md_that_says_something(tmp_path):
    d = files_in(tmp_path, {"ship.md": ship_draft(1)}, "0001_x")
    assert read(d, "0001_x")["artifacts"]["ship.md"]["ship"] == {"round": 1, "refused": BEHIND}
    (d / "ship.md").write_text(SHIP_OLD)
    assert "ship" not in read(d, "0001_x")["artifacts"]["ship.md"]


def test_a_pull_request_behind_origin_main_closes_ship_says_by_how_much_and_names_no_full_sha():
    u = accepted_pass()
    g = check_gate(u, "ship", behind_by(3))
    assert g["ok"] is False
    assert "#7 is 3 commit(s) behind origin/main — integrate, then review again" in g["need"][0]
    assert not re.search(r"[0-9a-f]{40}", "\n".join(g["need"]))
    # A count git cannot give still closes the gate, with git's words.
    unknown = behind_by(
        0, {f"rev-list --count {SHA}..{TRUNK}": {"code": 128, "out": "", "err": "bad revision"}}
    )
    assert re.search(
        r"unknown number of commits \(git said: bad revision\) behind origin/main",
        check_gate(u, "ship", unknown)["need"][0],
    )


def test_no_origin_main_here_or_origin_main_an_ancestor_of_the_head_opens_ship_as_before():
    u = accepted_pass()
    no_trunk = behind_by(3, {f"rev-parse --verify --quiet {TRUNK}": NOT_ANCESTOR})
    assert check_gate(u, "ship", no_trunk) == {"ok": True, "need": [], "head": SHA}
    assert check_gate(u, "ship", green_probe()) == {"ok": True, "need": [], "head": SHA}


def test_a_head_already_rebased_goes_to_review_not_to_the_behind_reason():
    u = accepted_pass()
    rebased = behind_by(2, {f"merge-base --is-ancestor {SHA} refs/heads/feat/x": NOT_ANCESTOR})
    assert next_step(u, rebased)["stage"] == "review"
    behind = next_step(u, behind_by(2))
    assert behind["stage"] == ""
    assert "2 commit(s) behind origin/main" in behind["action"]


def test_a_draft_ship_md_from_an_older_round_is_a_missing_one():
    text = f"{round_(1, 'pass')}\n{round_(2, 'pass').replace(SHA, REB)}"
    u = branched(
        {
            **CHAIN,
            "review.md": review_art("accepted", text),
            "ship.md": ship_art(ship_draft(1)),
        }
    )

    def at(git_says):
        return green_probe(None, git_says, {"state": "OPEN", "headRefOid": REB})

    assert next_step(u, at({})) == {
        "blocked": True,
        "action": f"ship — merge with --match-head-commit {REB}",
        "stage": "ship",
    }
    moved = at({f"diff --name-only {REB}..{REB}": ok("src/a.py")})
    assert next_step(u, moved)["stage"] == "review"
    behind = at(
        {
            f"merge-base --is-ancestor {TRUNK} {REB}": NOT_ANCESTOR,
            f"rev-list --count {REB}..{TRUNK}": ok("1"),
        }
    )
    n = next_step(u, behind)
    assert n["stage"] == ""
    assert "1 commit(s) behind origin/main" in n["action"]


def test_a_draft_ship_md_from_the_last_round_asks_the_gate_and_an_open_one_stops_on_refused():
    def u(refused=BEHIND):
        return accepted_pass({"ship.md": ship_art(ship_draft(1, refused))})

    # Behind: the reason the autopilot integrates on.
    behind = next_step(u(), behind_by(4))
    assert behind["stage"] == ""
    assert "4 commit(s) behind origin/main — integrate, then review again" in behind["action"]
    # Rebased since: another round.
    rebased = behind_by(0, {f"merge-base --is-ancestor {SHA} refs/heads/feat/x": NOT_ANCESTOR})
    assert next_step(u(), rebased)["stage"] == "review"
    # Open: refused for something the gate cannot see, and the stop says what.
    open_ = next_step(u("you do not have permission to merge"), green_probe())
    assert open_ == {
        "blocked": True,
        "action": "ship was refused: you do not have permission to merge — "
        "finish and accept ship.md",
        "stage": "",
    }
    # No `Refused:` line: a merge asked for and not yet recorded, which is no refusal.
    assert "ship was refused" not in next_step(u(None), green_probe())["action"]


def test_a_draft_ship_md_with_a_round_and_no_refused_line_is_merging_not_refused():
    u = accepted_pass({"ship.md": ship_art(ship_draft(1, None))})
    assert decide(u)["why"] == "ship-merging"
    assert decide(u)["action"] == "ship is merging #7 — wait"
    # The gate cannot yet see the merge commit: a wait, and its words are not raised as a refusal.
    unfetched = merged_probe(git={f"cat-file -e {MERGE}^{{commit}}": NOT_ANCESTOR})
    wait = next_answer(u, unfetched)
    assert wait["stage"] == ""
    assert wait["reasons"] == ["ship-merging"]
    assert wait["action"] == "ship is merging #7 — wait"
    assert "not in this repository" not in wait["action"]
    # Nor does an open pull request's CI reach the reasons.
    assert next_answer(u, green_probe([{"name": "tests", "bucket": "pending"}]))["reasons"] == [
        "ship-merging"
    ]
    # Once the merge commit is here, the ship that records it, as after a refusal.
    done = next_answer(u, merged_probe())
    assert done["stage"] == "ship"
    assert done["reasons"] == ["ship-merging", "recording-ship"]
    assert "do not merge" in done["action"]
    # A `Refused:` line is still a refusal.
    assert decide(accepted_pass({"ship.md": ship_art(ship_draft(1))}))["why"] == "ship-refused"


def test_a_draft_ship_md_with_no_round_stops_as_it_always_did():
    u = accepted_pass({"ship.md": ship_art(SHIP_OLD)})
    stop = {"blocked": True, "action": "finish and accept ship.md", "stage": ""}
    assert next_step(u, behind_by(4)) == stop
    assert next_action(u) == stop


def test_a_pass_then_a_rebase_with_no_ship_md_is_review_not_a_draft_stop():
    after_rebase = green_probe(
        None,
        {
            f"merge-base --is-ancestor {SHA} refs/heads/feat/x": NOT_ANCESTOR,
            f"merge-base --is-ancestor {SHA} refs/remotes/origin/feat/x": NOT_ANCESTOR,
            f"merge-base --is-ancestor {SHA} {REB}": NOT_ANCESTOR,
        },
        {"state": "OPEN", "headRefOid": REB},
    )
    n = next_step(accepted_pass(), after_rebase)
    assert n["stage"] == "review"
    assert "finish and accept" not in n["action"]


def test_status_offers_no_acceptance_of_a_ship_md_naming_its_round_only(tmp_path):
    review = f"# Review: x\nPR: pr.md. Author: t. Status: accepted.\n\n{round_(1, 'pass')}"

    def status(ship):
        root, _ = tree(
            tmp_path,
            {
                "intent.md": "# I\nAuthor: t. Type: fix. Status: accepted.\n",
                "spec.md": "Status: accepted.\n",
                "plan.md": "Status: accepted.\n",
                "impl.md": impl_text(""),
                "pr.md": "PR: https://github.com/o/r/pull/7. Status: accepted.\n",
                "review.md": review,
                "ship.md": ship,
            },
        )
        return json_of(root, "status", "--json")["units"][0]

    refused = status(ship_draft(1))
    assert refused["next"] == {
        "blocked": True,
        "action": "ship after review round 1 did not merge — the next step says what runs now",
        "stage": "",
        "why": "ship-refused",
    }
    # Still in the window, at `ship`: the board reads the integration and the autopilot fetches.
    assert refused["at"] == "ship"
    assert refused["betweenPrAndShip"] is True
    # A ship.md from before the round was named is any draft, byte for byte.
    assert status(SHIP_OLD)["next"] == {
        "blocked": True,
        "action": "finish and accept ship.md",
        "stage": "",
        "why": "draft",
    }
    u = accepted_pass({"ship.md": ship_art(ship_draft(1))})
    assert "accept" not in next_action(u)["action"]
    # With no repository, `next` says it needs one, as the gate does.
    assert next_step(u)["stage"] == ""
    assert "--repo" in next_step(u)["action"]


# --- an accepted spec or plan a decision or main came after ------------------------------------

ACCEPTED_TO_PLAN = {
    "intent.md": "# I\nType: feat. Status: accepted.\n",
    "spec.md": "# S\nIntent: intent.md. Status: accepted.\n\nWe change `coscc/x.py`.\n",
    "plan.md": "# P\nIntent: intent.md. Status: accepted.\n\n## Files that change\n- coscc/x.py\n",
}
DECIDED = {"decisions": [{"id": "C2", "authority": "person"}], "main": None}
MOVED = {"decisions": [], "main": {"from_sha": "a" * 40, "main_sha": "b" * 40, "paths": ["x"]}}


def outdated_unit(tmp_path: Path, outdated: dict) -> dict:
    _, d = tree(tmp_path, ACCEPTED_TO_PLAN)
    return {**read(d, "0001_q"), "outdated": outdated}


def test_an_outdated_spec_runs_again_before_an_outdated_plan_and_says_why(tmp_path):
    u = outdated_unit(tmp_path, {"plan": MOVED, "spec": DECIDED})
    got = next_answer(u)
    assert (got["stage"], got["rerun"], got["reasons"]) == ("", "spec", ["outdated-decision"])
    assert "spec.md is outdated — 1 new decision(s)" in got["action"]
    assert "rerun" not in next_action(u)


def test_main_alone_is_outdated_main_and_a_decision_with_it_is_outdated_decision(tmp_path):
    got = next_answer(outdated_unit(tmp_path, {"plan": MOVED}))
    assert (got["rerun"], got["reasons"]) == ("plan", ["outdated-main"])
    assert "main changed 1 path(s) it cites" in got["action"]
    both = next_answer(
        outdated_unit(tmp_path, {"plan": {**MOVED, "decisions": DECIDED["decisions"]}})
    )
    assert both["reasons"] == ["outdated-decision"]


def test_nothing_new_or_a_stage_no_rewrite_reaches_is_not_outdated(tmp_path):
    for outdated in (
        {"plan": {"decisions": [], "main": {"paths": []}}},
        {"intent": DECIDED},
        {"impl": DECIDED},
    ):
        got = next_answer(outdated_unit(tmp_path, outdated))
        assert "rerun" not in got and not {"outdated-decision", "outdated-main"} & set(
            got["reasons"]
        ), outdated


def test_a_plan_a_spec_rewrite_made_stale_runs_as_stale_not_a_second_time_as_outdated(tmp_path):
    root, d = tree(tmp_path, ACCEPTED_TO_PLAN)
    digest = above_answers((d / "plan.md").read_text())
    (d / "intent.md").write_text(
        ACCEPTED_TO_PLAN["intent.md"]
        + "\n## Answers\n\n### Rerun\nRequested by: app. Date: 2026-10-04. Via: product.\n"
        + f"Stage: spec.\nDecisions: C2 (person).\nStale: plan.md sha256:{digest}\n"
    )
    got = next_answer({**read(d, "0001_q"), "outdated": {"plan": DECIDED}})
    assert (got["stage"], got.get("rerun"), got["reasons"]) == ("plan", None, ["stale"])
