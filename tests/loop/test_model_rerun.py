"""The stage a unit is at, the rerun of an accepted stage, the screenshots retake and the ship
that a refused merge leaves, called directly or through the CLI, each held to fixed values.

Units are stated by snapshot rows; the glue comes from `test_model_rebase`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from coscc.loop.probe import UI_STANDARD
from coscc.loop.repo_rules import screens_answer, screens_needs
from coscc.loop.rules import decide, next_answer, stage_at
from tests.loop.conftest import git, python, rerun_row
from tests.loop.test_model_rebase import (
    CHAIN,
    NOT_ANCESTOR,
    PR,
    REB,
    SHA,
    TRUNK,
    art,
    branched,
    check_gate,
    green_probe,
    known,
    next_action,
    next_step,
    ok,
    passed,
    read,
    review_art,
    rnd,
    open_f,
    ship_art,
    unit,
    BEHIND,
    answer,
    recorded,
)

FULL_LANE = {"lane": "full", "enteredFast": False, "laneMissing": []}
MERGE = "9" * 40


def hold(state: str, reason: str, by: str = "Leif", date: str = "2026-09-24") -> dict:
    """A hold row: `paused`, `dropped` or `active` (resumed)."""
    return {"state": state, "reason": reason, "by": by, "date": date, "via": "product"}


def merged_probe(view=None, git=None, checks=None, calls=None):
    """A pull request GitHub reports merged as `MERGE`, here and on origin/main unless `git`
    says otherwise."""
    view = view or {
        "state": "MERGED",
        "headRefOid": SHA,
        "mergeCommit": {"oid": MERGE},
        "mergedAt": "2026-09-20T13:33:07Z",
    }
    return recorded(green_probe(checks, git, view), [] if calls is None else calls)


def files_in(tmp_path: Path, files: dict[str, str], name: str = "u") -> Path:
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    for f, text in files.items():
        (d / f).write_text(text)
    return d


def state_of_root(units: dict[str, dict] | None = None) -> dict:
    return {
        "workspace": "",
        "workspaces": [],
        "units": {f"/{n}": e for n, e in (units or {}).items()},
    }


def cli(*argv: str, units: dict[str, dict] | None = None):
    """`python -m coscc.loop argv`; a deciding command gets `units` (`known` entries by name) as
    the snapshot of its `--root`."""
    words = list(argv)
    deciding = {"status", "gate", "next", "rerun", "unit-branch", "screens"}
    if "--root" in words and deciding & set(words) and "--state" not in words:
        return python([*words, "--state", "-"], stdin=json.dumps(state_of_root(units)))
    return python(words)


# --- helpers ----------------------------------------------------------------------------------


def tree(tmp_path: Path, files: dict[str, str], name: str = "0001_q") -> tuple[Path, Path]:
    """A `--root` of its own under `tmp_path`, holding the unit `name` with `files`."""
    root = tmp_path / f"t{len(list(tmp_path.iterdir()))}"
    d = root / ".cos" / name
    d.mkdir(parents=True)
    for f, text in files.items():
        (d / f).write_text(text)
    return root, d


def run_in(root: Path, *words: str, units: dict | None = None):
    return cli(*words, "--root", str(root), units=units)


def json_of(root: Path, *words: str, units: dict | None = None):
    out = run_in(root, *words, units=units)
    assert out.code == 0, out.err
    return json.loads(out.out)


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
        "review.md": review_art("changes-requested", rnd(1, "changes-requested", open_f())),
    }
    assert next_action(unit(requested))["stage"] == ""
    assert at(requested) == "review"
    assert at({"idea.md": art("accepted")}) == "intent"
    assert stage_at(unit({}), {"stage": ""}) == "intent"


# --- an accepted stage run again from the board -------------------------------------------------

# A unit with every artifact up to a passed review, like the board's one awaiting its ship.
RERUN_FILES = dict.fromkeys(
    ["intent.md", "spec.md", "plan.md", "impl.md", "pr.md", "review.md"], "# x\nStatus: accepted.\n"
)
RERUN_STATUSES = dict.fromkeys(RERUN_FILES, "accepted")


class RerunTree:
    """A `--root` holding one unit, and what the board does with it: a rerun is a row, and each
    artifact holds the record it was last written as."""

    def __init__(
        self,
        tmp_path: Path,
        files=None,
        name: str = "0003_awaiting-ship",
        shipped=False,
        holds=(),
        statuses=None,
        rounds=None,
    ):
        self.name = name
        self.statuses = {**RERUN_STATUSES, **(statuses or {})}
        self.shipped = shipped
        self.holds = list(holds)
        self.rounds = [rnd(1, "pass")] if rounds is None else rounds
        self.reruns: list[dict] = []
        self.root, self.dir = tree(tmp_path, RERUN_FILES if files is None else files, name)
        self.records = {p.name: i + 1 for i, p in enumerate(sorted(self.dir.iterdir()))}

    def entry(self) -> dict:
        fields = {f.replace(".md", "_md"): {"record": r} for f, r in self.records.items()}
        fields["pr_md"]["pr"] = PR["pr"]
        fields["review_md"]["rounds"] = self.rounds
        entry = known(
            self.statuses,
            questions={"intent.md": ["Một?"]},
            holds=self.holds,
            type="feat",
            reruns=self.reruns,
            fields=fields,
        )
        entry["shipped"] = self.shipped
        return entry

    def cli(self, *args: str):
        return run_in(self.root, *args, units={self.name: self.entry()})

    def read(self):
        return read(self.dir, self.name, self.entry())

    def rewrite(self, file: str) -> None:
        """The stage ran again and wrote `file` as a new record."""
        self.records[file] = max(self.records.values()) + 1

    def offers(self) -> list:
        return json.loads(self.cli("rerun", self.name).out)["offers"]

    def rerun(self, stage: str) -> dict:
        """What the board does on a press: `rerun <stage>` says which records go stale, and the
        app writes that as a row."""
        out = self.cli("rerun", self.name, stage)
        assert out.code == 0, out.err
        said = json.loads(out.out)
        self.reruns.append(
            rerun_row(
                stage,
                "2026-10-01",
                **{f.replace(".md", "_md"): r for f, r in said["stale"].items()},
            )
        )
        return said


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

    t.rewrite("pr.md")
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

    t.rewrite("review.md")
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
    t = RerunTree(
        tmp_path,
        {**RERUN_FILES, "spike.md": "# Spike: x\nStatus: accepted.\n"},
        statuses={"review.md": "changes-requested", "spike.md": "accepted"},
        rounds=[rnd(1, "changes-requested", open_f())],
    )
    # A person's rerun of spec names both files at the records they hold now.
    t.reruns.append(
        rerun_row("spec", spike_md=t.records["spike.md"], review_md=t.records["review.md"])
    )
    u = t.read()
    assert "stale" not in u["artifacts"]["review.md"]
    assert "stale" not in u["artifacts"]["spike.md"]


def test_a_shipped_unit_stays_finished_after_a_rerun_and_ignores_a_hold(tmp_path):
    t = RerunTree(tmp_path)
    t.rerun("intent")

    def shipped():
        t.shipped = True
        return t.read()

    u = t.read()
    for f in ["intent.md", "spec.md", "plan.md", "impl.md", "pr.md"]:
        assert u["artifacts"][f].get("stale"), f
    assert decide(u)["stage"] == "intent"

    u = shipped()
    assert {k: decide(u)[k] for k in ("why", "stage", "blocked")} == {
        "why": "finished",
        "stage": "",
        "blocked": False,
    }

    t.holds = [hold("paused", "chờ")]
    u = shipped()
    assert u["hold"] is None
    assert u["holdMoves"] == []
    assert (
        "intent.md carries a hold block, but the unit is finished — it is ignored" in u["problems"]
    )
    assert decide(u)["why"] == "finished"


# --- a draft whose questions are all answered names its stage as `rerun` ------------------------

ANSWERS_1_2 = [answer("intent.md", 1, "A", "x"), answer("intent.md", 2, "A", "y")]


def DRAFT_ASKED(answers=(), holds=()):
    """The row of a draft intent asking `Một?` and `Hai?`."""
    return known(
        {"intent.md": "draft"},
        questions={"intent.md": ["Một?", "Hai?"]},
        answers=list(answers),
        holds=list(holds),
    )


DRAFT_INTENT = (
    "# Intent: x\nType: feat. Status: draft.\n\n## Open questions\n\n1. Một?\n2. Hai?\n\n"
    "## Answers\n"
)


class Answered:
    """A unit under `--root`, read, and what `next` prints of it."""

    def __init__(self, tmp_path: Path, files: dict[str, str], entry: dict):
        self.root, d = tree(tmp_path, files)
        self.entry = entry
        self.u = read(d, "0001_q", entry)

    def run(self, *words: str):
        return run_in(self.root, *words, units={"0001_q": self.entry})

    def next(self) -> dict:
        return json.loads(self.run("next", "0001_q").out)


def test_a_draft_intent_with_every_question_answered_is_rerun_intent_and_nothing_else(tmp_path):
    a = Answered(tmp_path, {"intent.md": DRAFT_INTENT}, DRAFT_ASKED(ANSWERS_1_2))
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
    a = Answered(tmp_path, {"intent.md": DRAFT_INTENT}, DRAFT_ASKED(ANSWERS_1_2[:1]))
    assert "rerun" not in a.next()
    assert next_step(a.u).get("rerun") is None


def test_a_held_unit_is_no_rerun_even_with_every_question_answered(tmp_path):
    a = Answered(
        tmp_path, {"intent.md": DRAFT_INTENT}, DRAFT_ASKED(ANSWERS_1_2, [hold("paused", "chờ")])
    )
    n = a.next()
    assert n["hold"]["state"] == "paused"
    assert "rerun" not in n


def test_a_draft_with_no_questions_is_no_rerun(tmp_path):
    a = Answered(
        tmp_path, {"intent.md": "# I\nType: feat. Status: draft.\n"}, known({"intent.md": "draft"})
    )
    assert "rerun" not in a.next()


def test_a_spec_draft_answered_in_full_is_rerun_spec(tmp_path):
    a = Answered(
        tmp_path,
        {
            "intent.md": "# I\nType: feat. Status: accepted.\n",
            "spec.md": "# S\nStatus: draft.\n\n## Open questions\n\n1. Một?\n\n## Answers\n",
        },
        known(
            {"intent.md": "accepted", "spec.md": "draft"},
            questions={"spec.md": ["Một?"]},
            answers=[answer("spec.md", 1, "A", "x")],
        ),
    )
    assert next_step(a.u)["rerun"] == "spec"


# --- a draft impl.md asks a person, and runs again on the answer --------------------------------

# Every stage before `impl` accepted, so `decide` reaches `impl.md`.
BEFORE_IMPL = {
    "intent.md": "# I\nType: feat. Status: accepted.\n",
    "spec.md": "# S\nStatus: accepted.\n",
    "plan.md": "# P\nStatus: accepted.\n",
}
DRAFT_IMPL = "# Impl\nStatus: draft.\n\n## Open questions\n\n1. Chạy lệnh X rồi đưa kết quả?\n"


def impl_tree(tmp_path: Path, answered=0, asked=1, holds=(), plan="accepted") -> Answered:
    """A draft `impl.md` asking `asked` questions, the first `answered` of them answered."""
    questions = ["Chạy lệnh X rồi đưa kết quả?", "Đăng nhập rồi báo lại?"][:asked]
    entry = known(
        {"intent.md": "accepted", "spec.md": "accepted", "plan.md": plan, "impl.md": "draft"},
        questions={"impl.md": questions},
        answers=[answer("impl.md", n + 1, "A", "x") for n in range(answered)],
        holds=list(holds),
    )
    return Answered(tmp_path, {**BEFORE_IMPL, "impl.md": DRAFT_IMPL}, entry)


def test_a_draft_impl_with_an_open_question_is_listed_and_counted(tmp_path):
    a = impl_tree(tmp_path)
    u = json_of(a.root, "status", "--json", units={"0001_q": a.entry})["units"][0]
    assert [
        {"artifact": q["artifact"], "n": q["n"], "answered": q["answered"]} for q in u["questions"]
    ] == [{"artifact": "impl.md", "n": 1, "answered": False}]
    assert u["counted"] == "impl.md"
    assert u["open"] == 1


def test_a_draft_impl_answered_in_full_is_rerun_impl(tmp_path):
    a = impl_tree(tmp_path, answered=1)
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
    a = impl_tree(tmp_path, answered=1, asked=2)
    assert "rerun" not in a.next()
    assert next_step(a.u).get("rerun") is None


def test_a_paused_or_dropped_unit_with_an_answered_draft_impl_is_no_rerun(tmp_path):
    for state in ["paused", "dropped"]:
        n = impl_tree(tmp_path, answered=1, holds=[hold(state, "chờ")]).next()
        assert n["hold"]["state"] == state
        assert "rerun" not in n


def test_plan_draft_keeps_the_impl_gate_closed(tmp_path):
    a = impl_tree(tmp_path, answered=1, plan="draft")
    out = a.run("gate", "0001_q", "impl")
    assert out.code == 1
    assert 'plan.md is "draft"' in out.out + out.err


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
    return branched({**CHAIN, "review.md": review_art("accepted", rnd(1, "pass")), **(extra or {})})


def test_read_unit_attaches_ship_only_to_a_ship_row_that_says_something(tmp_path):
    d = files_in(tmp_path, {"ship.md": "# Ship: x\n"}, "0001_x")
    said = known(
        {"ship.md": "draft"}, fields={"ship_md": {"ship": {"round": 1, "refused": BEHIND}}}
    )
    assert read(d, "0001_x", said)["artifacts"]["ship.md"]["ship"] == {
        "round": 1,
        "refused": BEHIND,
    }
    mute = known(
        {"ship.md": "draft"}, fields={"ship_md": {"ship": {"round": None, "refused": None}}}
    )
    assert "ship" not in read(d, "0001_x", mute)["artifacts"]["ship.md"]


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
    u = branched(
        {
            **CHAIN,
            "review.md": review_art("accepted", rnd(1, "pass"), rnd(2, "pass", sha=REB)),
            "ship.md": ship_art(1),
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
        return accepted_pass({"ship.md": ship_art(1, refused)})

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
    u = accepted_pass({"ship.md": ship_art(1, None)})
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
    assert decide(accepted_pass({"ship.md": ship_art(1)}))["why"] == "ship-refused"


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
    def status(ship):
        files = {
            f: "# x\n"
            for f in ["intent.md", "spec.md", "plan.md", "impl.md", "pr.md", "review.md", "ship.md"]
        }
        root, _ = tree(tmp_path, files)
        fields = {"pr_md": {"pr": PR["pr"]}, "review_md": {"rounds": [rnd(1, "pass")]}}
        if ship:
            fields["ship_md"] = {"ship": ship}
        return json_of(
            root,
            "status",
            "--json",
            units={
                "0001_q": known(
                    dict.fromkeys(files, "accepted") | {"ship.md": "draft"},
                    type="fix",
                    fields=fields,
                )
            },
        )["units"][0]

    refused = status({"round": 1, "refused": BEHIND})
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
    assert status(None)["next"] == {
        "blocked": True,
        "action": "finish and accept ship.md",
        "stage": "",
        "why": "draft",
    }
    u = accepted_pass({"ship.md": ship_art(1)})
    assert "accept" not in next_action(u)["action"]
    # With no repository, `next` says it needs one, as the gate does.
    assert next_step(u)["stage"] == ""
    assert "--repo" in next_step(u)["action"]


def test_nothing_is_offered_on_a_finished_held_or_closed_unit(tmp_path):
    done = RerunTree(tmp_path, shipped=True)
    assert json.loads(done.cli("rerun", done.name).out) == {
        "unit": done.name,
        "offers": [],
        "why": "the unit is finished: it shipped",
    }
    refused = done.cli("rerun", done.name, "pr")
    assert refused.code == 1
    assert re.search(r"pr cannot be run again for .*: the unit is finished", refused.err)

    held = RerunTree(tmp_path, holds=[hold("paused", "chờ")])
    assert held.offers() == []

    closed = RerunTree(
        tmp_path,
        {**RERUN_FILES, "impl.md": "# Impl: x\nStatus: rejected.\n"},
        statuses={"impl.md": "rejected"},
    )
    assert re.search(
        r"impl\.md is rejected", json.loads(closed.cli("rerun", closed.name).out)["why"]
    )


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


def test_a_draft_ship_md_with_no_round_stops_as_it_always_did():
    u = accepted_pass({"ship.md": ship_art()})
    stop = {"blocked": True, "action": "finish and accept ship.md", "stage": ""}
    assert next_step(u, behind_by(4)) == stop
    assert next_action(u) == stop
