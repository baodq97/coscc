"""Codes beside the words, the workflows a red check is read against, one idea over several units
and repositories, the pull request's title, the metadata `meta` prints and the lane a unit walks.

A deciding command is handed the snapshot the app would build of the files under
`--root` and of the stores named as peers, from `meta`'s own readers.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from coscc.loop import NEEDS_STATE, STAGE_NAMES, stringify
from coscc.loop.model import (
    WAITING_ON,
    above_answers,
    lane_of,
    not_a_work_branch,
    parse_answers,
    parse_idea_ref,
    parse_links,
    parse_questions,
    parse_unit_ref,
    pr_text,
    read_unit,
    title_problem,
)
from coscc.loop.paths import parse_idea, read_ideas, unit_meta
from coscc.loop.probe import make_probe
from coscc.loop.repo_rules import branch_checks
from coscc.loop.rules import gate_answer, next_answer
from tests.loop.conftest import REPO, python
from tests.loop.test_model import (
    check_gate,
    next_action,
    next_step,
    CHAIN,
    FULL_LANE,
    NOT_ANCESTOR,
    SHA,
    PR,
    art,
    branched,
    cli,
    entry_from,
    green_probe,
    hold_block,
    moved_to,
    ok,
    parse_hold,
    passed,
    question_tree,
    review_art,
    round_,
    unit,
)

# --- the suite's glue -------------------------------------------------------------------


def state_for(stores: dict[str, Path], own: str = "") -> dict:
    """The snapshot of the stores `{workspace: <root>/.cos}`; `own` is the one the command reads."""
    units, ideas = {}, {}
    for ws, cos in stores.items():
        if not cos.exists():
            continue
        for d in sorted(cos.iterdir()):
            if d.is_dir() and d.name != "ideas":
                units[f"{ws}/{d.name}"] = entry_from(unit_meta(str(d)))
        ideas[ws] = read_ideas(str(cos)) if (cos / "ideas").exists() else []
    return {
        "workspace": own,
        "workspaces": [w for w in stores if w],
        "units": units,
        "ideas": ideas,
    }


def state_of_roots(root, peers=()) -> dict:
    """The snapshot of the store at `root` and of `peers`, `(workspace, root)` pairs."""
    at = Path(root).resolve()
    own = next((n for n, d in peers if Path(d).resolve() == at), "")
    stores = {own: at / ".cos"}
    for n, d in peers:
        if n != own:
            stores[n] = Path(d).resolve() / ".cos"
    return state_for(stores, own)


def ask(*argv, peers=()):
    """A deciding command on `--root`, handed the snapshot of it and of `peers`."""
    words = [str(a) for a in argv]
    root = words[words.index("--root") + 1]
    return python([*words, "--state", "-"], stdin=json.dumps(state_of_roots(root, peers)))


def read_in(dir_: Path, name: str, root: Path, peers=()) -> dict:
    """`read_unit` of `dir_` under `name`, in the snapshot of the store at `root` and `peers`."""
    state = state_of_roots(root, peers)
    state["units"][f"{state['workspace']}/{name}"] = entry_from(unit_meta(str(dir_)))
    return read_unit(str(dir_), name, state)


def without_lane(o: dict) -> dict:
    return {k: v for k, v in o.items() if k not in ("lane", "enteredFast", "laneMissing")}


def status_without_lane(out: str) -> str:
    data = json.loads(out)
    return stringify({**data, "units": [without_lane(u) for u in data["units"]]}, 2) + "\n"


def make_store(tmp_path: Path, units: dict, ideas: dict | None = None) -> Path:
    """A store root whose `.cos/` holds `units`, `{name: {file: text}}`, and `ideas`."""
    root = tmp_path / f"store{len(list(tmp_path.iterdir()))}"
    (root / ".cos").mkdir(parents=True)
    for name, files in units.items():
        (root / ".cos" / name).mkdir()
        for f, text in files.items():
            (root / ".cos" / name / f).write_text(text)
    if ideas:
        (root / ".cos" / "ideas").mkdir()
    for f, text in (ideas or {}).items():
        (root / ".cos" / "ideas" / f).write_text(text)
    return root


def counted(probe, calls: list) -> SimpleNamespace:
    """`probe`, every call of it, git's and gh's, landing in `calls`."""

    def gh(*a):
        calls.append(" ".join(["gh", *a]))
        return probe.gh(*a)

    def git(*a):
        calls.append(" ".join(a))
        return probe.git(*a)

    return SimpleNamespace(**{**vars(probe), "gh": gh, "git": git})


# --- codes beside the words ---------------------------------------------------------------

MERGE = "9" * 40
MERGED_AT = "2026-09-20T13:33:07Z"


def merged_view(**over) -> dict:
    return {
        "state": "MERGED",
        "headRefOid": SHA,
        "mergeCommit": {"oid": MERGE},
        "mergedAt": MERGED_AT,
        **over,
    }


def merged_probe(view=None, git=None, checks=None, calls=None):
    """A pull request GitHub reports merged as `MERGE`, here and on origin/main unless `git`
    says otherwise."""
    return counted(green_probe(checks, git, view or merged_view()), [] if calls is None else calls)


# What `.github/workflows/pr.yml` holds of its first job, copied so the tests stand where the
# workflows were not.
PR_YML = {
    "path": ".github/workflows/pr.yml",
    "text": "\n".join(
        [
            "name: pr",
            "",
            "on:",
            "  pull_request:",
            "",
            "permissions:",
            "  contents: read",
            "",
            "jobs:",
            "  branch-name:",
            "    runs-on: ubuntu-latest",
            "    steps:",
            "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7",
            "      - name: The source branch follows the grammar in .claude/CLAUDE.md",
            "        # `github.head_ref` is the branch the pull request comes from. The check is",
            "        # the same one that runs locally, from the same file, so CI and `coscc.loop`",
            "        # cannot disagree about what a valid name is.",
            '        run: uv run python -m coscc.loop check-branch "$HEAD_REF"',
            "        env:",
            "          HEAD_REF: ${{ github.head_ref }}",
            "",
        ]
    ),
}
HEAD_97 = "feat/open-questions-wait-for-the-originator-even-when-precedent-answers-them"
REASON_97 = "the slug is 71 characters, over the 60 allowed"
RED_97 = [{"name": "branch-name", "bucket": "fail"}, {"name": "tests", "bucket": "pending"}]


def head_probe(probe, head=HEAD_97, workflows=None, calls=None):
    """`probe`, with `gh pr view --json headRefName` answering `head` (`None`: gh fails) and the
    workflows `workflows`. Every `gh` call lands in `calls`."""
    calls = [] if calls is None else calls
    files = [PR_YML] if workflows is None else workflows

    def gh(*a):
        calls.append(" ".join(["gh", *a]))
        if a[-1] != "headRefName":
            return probe.gh(*a)
        if head is None:
            return {"code": 1, "out": "", "err": "HTTP 502: Bad Gateway"}
        return ok(json.dumps({"headRefName": head}))

    return SimpleNamespace(**{**vars(probe), "gh": gh, "workflows": lambda: files})


def test_next_and_gate_name_ci_a_person_a_merge_made_and_a_closed_unit_by_code():
    def has(answer, *codes):
        for c in codes:
            assert c in answer["reasons"], f"{c} in {answer['reasons']}"

    pending = green_probe([{"name": "tests", "bucket": "pending"}])
    has(next_answer(branched(CHAIN), pending), "ci-pending")
    has(gate_answer(branched(CHAIN), "review", pending), "ci-pending")
    red = head_probe(green_probe([{"name": "tests", "bucket": "fail"}]), head="feat/x")
    back = next_answer(branched(CHAIN), red)
    assert back["stage"] == "impl"
    has(back, "ci-red")
    assert "ci-pending" not in back["reasons"]
    has(
        next_answer(branched(CHAIN), head_probe(green_probe(RED_97))),
        "ci-unfixable",
        "needs-person",
    )
    # The gate opens to record a merge already made, and says so by code alone.
    assert gate_answer(passed(), "ship", merged_probe())["reasons"] == ["recording-ship"]
    has(next_answer(passed(), merged_probe()), "recording-ship")
    assert gate_answer(passed(), "ship", green_probe())["reasons"] == []
    assert next_answer(branched({**CHAIN, "plan.md": art("done")}))["reasons"] == ["finished"]
    has(next_answer(branched({**CHAIN, "spec.md": art("rejected")})), "closed")
    # The words stay where they were: `next_step` and `check_gate` hand out no codes.
    assert "reasons" not in next_step(branched(CHAIN), pending)
    assert "reasons" not in check_gate(branched(CHAIN), "review", pending)


def test_every_closed_gate_names_at_least_one_code():
    u = branched({"intent.md": art("accepted"), "spec.md": art("draft")})
    for stage in STAGE_NAMES:
        g = gate_answer(u, stage, green_probe())
        if not g["ok"]:
            assert g["reasons"], stage
    assert gate_answer(u, "plan")["reasons"] == ["draft"]
    held = {**u, "hold": {"state": "paused", "reason": "x", "by": "b", "date": "d"}}
    assert gate_answer(held, "plan")["reasons"] == ["paused"]
    assert gate_answer(u, "nope")["reasons"] == ["unreadable"]


def test_gate_json_prints_the_lines_it_prints_without_it_its_codes_and_the_same_exit(tmp_path):
    root, _ = question_tree(
        tmp_path, {"intent.md": "# I\nAuthor: t. Type: feat. Status: accepted.\n"}
    )
    words = cli("--root", str(root), "gate", "0001_q", "plan")
    json_out = cli("--root", str(root), "gate", "0001_q", "plan", "--json")
    assert words.code == 1
    assert json_out.code == 1
    said = json.loads(json_out.out)
    assert said["lines"] == words.err.rstrip().split("\n")
    assert said["lines"] == [
        "blocked: plan cannot proceed for 0001_q",
        "  - spec.md does not exist — write it, or a person records the skip (coscc skip)",
    ]
    assert [said["ok"], said["reasons"]] == [False, ["missing"]]
    open_ = json.loads(cli("--root", str(root), "gate", "--json", "0001_q", "spec").out)
    plain = cli("--root", str(root), "gate", "0001_q", "spec").out.strip()
    assert open_ == {"ok": True, "lines": [plain], "reasons": []}
    assert plain == "open: spec may proceed for 0001_q"


# --- the checks a workflow names ----------------------------------------------------------


def test_a_red_read_asks_gh_once_more_for_the_head_and_a_green_read_never_does():
    read = ["gh pr checks 7 --required --json name,bucket"]
    head = ["gh pr view 7 --json headRefName"]
    for checks, more in [
        ([{"name": "tests", "bucket": "pass"}], []),
        ([{"name": "tests", "bucket": "pending"}], []),
        ([], []),
        ([{"name": "tests", "bucket": "fail"}], head),
        (RED_97, head),
    ]:
        calls: list[str] = []
        check_gate(branched(CHAIN), "review", head_probe(green_probe(checks), calls=calls))
        assert calls == [*read, *more]


def test_branch_checks_finds_the_job_whose_run_step_calls_coscc_loop_check_branch():
    def wf(text, path=".github/workflows/x.yml"):
        return {"path": path, "text": text}

    assert branch_checks([PR_YML]) == ["branch-name"]
    # The job's own `name:`, quoted or not, over its key; a `.yaml` file; a `run: |` block.
    named = wf(
        'jobs:\n  names:\n    name: "Branch name" # the check\n    runs-on: x\n    steps:\n'
        '      - run: uv run python -m coscc.loop check-branch "$H"\n'
    )
    assert branch_checks([named]) == ["Branch name"]
    block = wf(
        "jobs:\n  a:\n    name: 'the branch'\n    steps:\n      - name: check\n        run: |\n"
        '          set -e\n          uv run python -m coscc.loop check-branch "$H"\n'
        "  b:\n    steps:\n      - run: npm test\n",
        ".github/workflows/y.yaml",
    )
    assert branch_checks([block]) == ["the branch"]
    assert branch_checks([PR_YML, named, block]) == ["branch-name", "Branch name", "the branch"]
    # A step's `name:` is not the job's, at any indentation.
    step = wf(
        "jobs:\n  b:\n    steps:\n    - name: a step\n"
        "      run: python -m coscc.loop check-branch x\n"
    )
    assert branch_checks([step]) == ["b"]
    for text in [
        # A comment is not a call, inline or inside a block.
        "jobs:\n  tests:\n    steps:\n      - name: not the job\n"
        "        # python -m coscc.loop check-branch\n        run: npm test\n"
        '  lint:\n    steps:\n    - name: x\n      run: |\n        # coscc.loop check-branch "$H"\n'
        "        echo ok\n",
        # A name built from an expression is not known here.
        "jobs:\n  b:\n    name: ${{ matrix.os }} branch\n    steps:\n"
        "      - run: python -m coscc.loop check-branch x\n",
        # The words anywhere but a `run:` step, or no `jobs:` at all.
        "jobs:\n  b:\n    env:\n      X: python -m coscc.loop check-branch\n    steps:\n"
        "      - run: echo\n",
        "name: coscc.loop check-branch\non: push\n",
    ]:
        assert branch_checks([wf(text)]) == [], text
    # The workflow this repository runs, when it has one.
    real = make_probe(str(REPO)).workflows()
    if any(f["path"] == PR_YML["path"] for f in real):
        assert "branch-name" in branch_checks(real)


def test_make_probe_reads_the_workflows_of_the_repository_it_is_given_and_none_when_it_has_none(
    tmp_path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert make_probe(str(repo)).workflows() == []
    d = repo / ".github" / "workflows"
    d.mkdir(parents=True)
    (d / "pr.yml").write_text(PR_YML["text"])
    (d / "b.yaml").write_text("jobs: {}\n")
    (d / "README.md").write_text("not a workflow")
    found = make_probe(str(repo)).workflows()
    assert found == [{"path": ".github/workflows/b.yaml", "text": "jobs: {}\n"}, PR_YML]
    assert branch_checks(found) == ["branch-name"]


def test_check_branch_prints_not_a_work_branchs_line_unchanged():
    out = cli("check-branch", HEAD_97)
    assert out.code == 1
    assert out.err == f'"{HEAD_97}" is not a work branch: {REASON_97}\n'
    assert not_a_work_branch(HEAD_97, REASON_97) == out.err.rstrip()
    assert cli("check-branch", "feat/x").out == "feat/x\n"


# --- one idea, several units, several repositories ----------------------------------------


def intent_for(status, header=""):
    lines = [
        "# Intent: x",
        f"Author: a. Type: feat. Status: {status}.",
        *([header] if header else []),
    ]
    return "\n".join(lines) + "\n"


def idea_with(*lines):
    return (
        "# Idea: one feature\nAuthor: the originator. Status: accepted.\n\n"
        "## In their own words\n\nboth sides\n\n## Units\n\n" + "".join(f"{ln}\n" for ln in lines)
    )


def to_impl(header):
    """A unit whose next stage is `impl`, its links in its header."""
    return {
        "intent.md": intent_for("accepted", header),
        "spec.md": "# Spec\nStatus: skipped.\n",
        "plan.md": "# Plan\nStatus: accepted.\n",
    }


def pair(
    tmp_path,
    ship="draft",
    a_intent=None,
    line="- b/0001_y. Depends on: a/0001_x.",
    header="Idea: ideas/0001_f.md. Repo: b. Depends on: a/0001_x.",
):
    """Workspace `a` holds the unit depended on; `b` holds the idea and the unit that waits."""
    files = {"intent.md": a_intent or intent_for("accepted")}
    if ship:
        files["ship.md"] = f"# Ship\nStatus: {ship}.\n"
    a = make_store(tmp_path, {"0001_x": files})
    b = make_store(
        tmp_path,
        {"0001_y": to_impl(header)},
        {"0001_f.md": idea_with("- a/0001_x.", line)},
    )
    return a, b


def test_new_idea_gives_one_then_two_and_leaves_new_path_numbering_alone(tmp_path):
    root = make_store(tmp_path, {"0007_u": {"intent.md": intent_for("accepted")}})
    first = cli("--root", str(root), "new-idea", "one")
    assert first.code == 0, first.err
    assert first.out == ".cos/ideas/0001_one.md\n"
    assert sorted(p.name for p in (root / ".cos").iterdir()) == ["0007_u"], "creates nothing"
    (root / ".cos" / "ideas").mkdir()
    (root / first.out.strip()).write_text(idea_with())
    assert cli("--root", str(root), "new-idea", "two").out == ".cos/ideas/0002_two.md\n"
    assert cli("--root", str(root), "new-path", "z").out == ".cos/0008_z\n"


@pytest.mark.parametrize("slug", ["Bad_Slug", "a" * 61, ""])
def test_new_idea_refuses_a_bad_slug_with_exit_2(tmp_path, slug):
    root = make_store(tmp_path, {})
    out = cli("--root", str(root), "new-idea", slug)
    assert out.code == 2, slug
    assert out.out == ""


def test_a_store_with_ideas_reports_no_problem_about_it(tmp_path):
    _, b = pair(tmp_path)
    status = json.loads(ask("--root", b, "status", "--json").out)
    assert [u["name"] for u in status["units"]] == ["0001_y"]
    for u in status["units"]:
        assert not [p for p in u["problems"] if "ideas" in p and "Depends on" not in p], u
    assert status["ideas"] == [
        {
            "id": "0001_f",
            "title": "one feature",
            "status": "accepted",
            "problems": [],
            "units": [
                {"ref": "a/0001_x", "dependsOn": []},
                {"ref": "b/0001_y", "dependsOn": ["a/0001_x"]},
            ],
        }
    ]


# The sha256 of `status --json` of the store below, its root blanked, as the loop printed it
# before ideas were read.
BEFORE_IDEAS = "f7851805e41dcd5ee4c4d9e8fb136515e23784eea2dc82e79fda647524e3f3cb"


def test_status_json_without_ideas_is_byte_identical(tmp_path):
    root = make_store(
        tmp_path,
        {
            # The fields in the body, not the header, are not read.
            "0001_plain": {
                "intent.md": f"{intent_for('accepted')}\n## Problem\n\n"
                "Idea: ideas/0001_y.md. Repo: x. Depends on: 0002_other.\n",
                "spec.md": "# Spec\nStatus: skipped.\n",
                "plan.md": "# Plan\nStatus: accepted.\n",
            },
            "0002_other": {"intent.md": f"{intent_for('draft')}\n## Open questions\n\n1. Which?\n"},
            "0003_shipped": {
                f"{s}.md": intent_for("accepted")
                if s == "intent"
                else f"# {s}\nStatus: accepted.\n"
                for s in ["intent", "spec", "plan", "impl", "pr", "review", "ship"]
            },
        },
    )
    out = status_without_lane(ask("--root", root, "status", "--json").out)
    # Two stage hints were renamed, and nothing else: named back, the bytes are the same.
    was = out.replace('"hint": "pr"', '"hint": "write-pr"').replace(
        '"hint": "ship"', '"hint": "write-ship"'
    )
    assert was != out
    blanked = was.replace(str(root / ".cos"), "<root>")
    assert hashlib.sha256(blanked.encode()).hexdigest() == BEFORE_IDEAS
    assert not re.search(r'"ideas":|"idea":|"repo":|"dependsOn":', out)


def test_a_child_unit_carries_idea_repo_and_depends_on_in_status_json(tmp_path):
    a, b = pair(tmp_path)
    [u] = json.loads(ask("--root", b, "status", "--json", peers=[("a", a)]).out)["units"]
    assert u["idea"] == "ideas/0001_f.md"
    assert u["repo"] == "b"
    assert u["dependsOn"] == [
        {"ref": "a/0001_x", "merged": False, "why": "not merged: the app holds no merge of it"}
    ]
    assert u["problems"] == []


def test_a_unit_without_those_headers_has_none_of_the_keys(tmp_path):
    a, _ = pair(tmp_path)
    [u] = json.loads(ask("--root", a, "status", "--json").out)["units"]
    for key in ["idea", "repo", "dependsOn"]:
        assert key not in u, key


def test_the_header_fields_read_to_whitespace_less_the_closing_dot_and_a_list_splits_on_commas():
    text = (
        "# Intent: x\nIdea: a/ideas/0001_f.md. Repo: b. Depends on: a/0001_x, 0002_y. "
        "Status: accepted.\n"
    )
    assert parse_links(text) == {
        "idea": "a/ideas/0001_f.md",
        "repo": "b",
        "dependsOn": ["a/0001_x", "0002_y"],
    }
    body = "# Intent: Idea: no\nStatus: accepted.\n\n## Problem\n\nRepo: body.\n"
    assert parse_links(body) == {"idea": None, "repo": None, "dependsOn": None}
    assert parse_idea_ref("ideas/0001_f.md") == {"ws": None, "file": "0001_f.md"}
    assert parse_idea_ref("proj/ideas/0001_f.md") == {"ws": "proj", "file": "0001_f.md"}
    assert parse_idea_ref("proj/ideas/0001_f") is None
    assert parse_idea_ref("a/b/ideas/0001_f.md") is None
    assert parse_unit_ref("api/0001_x") == {"ws": "api", "name": "0001_x"}
    assert parse_unit_ref("api/1_x") is None


def test_a_line_under_units_that_is_not_the_grammar_is_a_problem_of_that_idea():
    idea = parse_idea(
        idea_with(
            "- a/0001_x.", "- 0002_no-workspace.", "a note", "- b/0003_z. Depends on: nowhere."
        )
    )
    assert idea["units"] == [{"ref": "a/0001_x", "dependsOn": []}]
    assert len(idea["problems"]) == 3
    assert parse_idea("# Idea: x\nStatus: accepted.\n")["problems"] == ["has no ## Units section"]


def test_a_missing_peer_a_missing_idea_file_and_an_unlisted_unit_each_land_in_problems(tmp_path):
    b = make_store(
        tmp_path,
        {
            "0002_p": to_impl("Repo: b. Depends on: c/0001_z."),
            "0003_q": to_impl("Idea: ideas/0009_none.md. Repo: b."),
            "0004_r": to_impl("Idea: ideas/0001_f.md. Repo: b."),
            "0005_s": to_impl("Idea: ideas/0001_f.md."),
        },
        {"0001_f.md": idea_with("- b/0005_s.")},
    )
    units = json.loads(ask("--root", b, "status", "--json").out)["units"]

    def said(name):
        return "\n".join(next(u for u in units if u["name"] == name)["problems"])

    assert re.search(r"Depends on: c/0001_z — the app has no workspace named c", said("0002_p"))
    assert re.search(
        r"Idea: ideas/0009_none\.md — the app knows no /ideas/0009_none\.md", said("0003_q")
    )
    assert re.search(r"does not list b/0004_r under ## Units", said("0004_r"))
    assert re.search(r"declares no Repo:", said("0005_s"))


@pytest.mark.parametrize("ship", ["draft", None])
def test_impl_gate_stays_shut_while_the_dependencys_ship_is_not_accepted(tmp_path, ship):
    a, b = pair(tmp_path, ship=ship)
    out = ask("--root", b, "gate", "0001_y", "impl", peers=[("a", a)])
    assert out.code == 1, str(ship)
    assert re.search(r"waits on a/0001_x: not merged: the app holds no merge of it", out.err)
    # `plan` is not `impl`: the wait closes nothing else.
    assert ask("--root", b, "gate", "0001_y", "plan", peers=[("a", a)]).code == 0


def test_a_dependency_opens_on_the_merged_row_not_on_ship_md(tmp_path):
    def gate(state, b):
        return python(
            ["--root", str(b), "gate", "0001_y", "impl", "--state", "-"], stdin=json.dumps(state)
        )

    def asked_with(ship, merged):
        a, b = pair(tmp_path, ship=ship)
        state = state_of_roots(b, [("a", a)])
        state["units"]["a/0001_x"]["merged"] = merged
        return gate(state, b)

    shut = asked_with("accepted", False)
    assert shut.code == 1, "an accepted ship.md with no merge row opens nothing"
    assert re.search(r"waits on a/0001_x: not merged: the app holds no merge of it", shut.err)
    for ship in ["draft", None]:
        assert asked_with(ship, True).code == 0, f"merged with ship.md {ship}"
    # An entry that carries no `merged` at all is not merged.
    a, b = pair(tmp_path, ship="accepted")
    state = state_of_roots(b, [("a", a)])
    del state["units"]["a/0001_x"]["merged"]
    assert gate(state, b).code == 1


def test_next_says_why_dependency_and_names_the_ref(tmp_path):
    a, b = pair(tmp_path)
    assert json.loads(ask("--root", b, "next", "0001_y", peers=[("a", a)]).out) == {
        "unit": "0001_y",
        "stage": "",
        "action": f"{WAITING_ON}a/0001_x to merge",
        "blocked": True,
        "why": "dependency",
        "reasons": ["dependency", "waiting-on"],
        **FULL_LANE,
    }
    assert f"{WAITING_ON}a/0001_x to merge" == "waiting on a/0001_x to merge"
    [u] = json.loads(ask("--root", b, "status", "--json", peers=[("a", a)]).out)["units"]
    assert u["next"]["why"] == "dependency"
    assert u["next"]["action"] == "waiting on a/0001_x to merge"


def test_impl_gate_opens_once_the_dependencys_ship_is_accepted(tmp_path):
    a, b = pair(tmp_path, ship="accepted")
    out = ask("--root", b, "gate", "0001_y", "impl", peers=[("a", a)])
    assert out.code == 0, out.err
    nxt = json.loads(ask("--root", b, "next", "0001_y", peers=[("a", a)]).out)
    assert nxt["stage"] == "impl"
    assert "why" not in nxt, "why is carried only by a wait"


def test_a_workspace_the_snapshot_does_not_name_shuts_impl_and_says_so(tmp_path):
    _, b = pair(tmp_path, ship="accepted")
    out = ask("--root", b, "gate", "0001_y", "impl")
    assert out.code == 1
    assert re.search(r"waits on a/0001_x: the app has no workspace named a", out.err)


def test_a_depends_on_that_differs_from_the_ideas_line_shuts_impl(tmp_path):
    # The idea lists no dependency; the header declares one.
    a, b = pair(tmp_path, ship="accepted", line="- b/0001_y.")
    out = ask("--root", b, "gate", "0001_y", "impl", peers=[("a", a)])
    assert out.code == 1
    assert re.search(
        r"lists b/0001_y with Depends on: nothing, but intent\.md declares a/0001_x"
        r" — the two must match",
        out.err,
    )
    # The header lacks the field the idea lists.
    a, b = pair(tmp_path, ship="accepted", header="Idea: ideas/0001_f.md. Repo: b.")
    out = ask("--root", b, "gate", "0001_y", "impl", peers=[("a", a)])
    assert out.code == 1
    assert re.search(r"Depends on: a/0001_x, but intent\.md declares none", out.err)
    assert json.loads(ask("--root", b, "next", "0001_y", peers=[("a", a)]).out)["stage"] == ""
    # Both agree, one of them without its `<ws>`: the unit's own.
    a, b = pair(
        tmp_path,
        ship="accepted",
        line="- b/0001_y. Depends on: b/0002_z.",
        header="Idea: ideas/0001_f.md. Repo: b. Depends on: 0002_z.",
    )
    (b / ".cos" / "0002_z").mkdir()
    (b / ".cos" / "0002_z" / "intent.md").write_text(intent_for("accepted"))
    (b / ".cos" / "0002_z" / "ship.md").write_text("# Ship\nStatus: accepted.\n")
    out = ask("--root", b, "gate", "0001_y", "impl", peers=[("a", a)])
    assert out.code == 0, out.err


def test_a_dropped_dependency_shuts_impl_and_says_dropped(tmp_path):
    dropped = f"{intent_for('accepted')}\n## Answers\n{hold_block('Dropped', 'Not needed.')}"
    a, b = pair(tmp_path, ship=None, a_intent=dropped)
    out = ask("--root", b, "gate", "0001_y", "impl", peers=[("a", a)])
    assert out.code == 1
    assert re.search(r"waits on a/0001_x: dropped", out.err)
    a, b = pair(tmp_path, ship=None, a_intent=intent_for("rejected"))
    assert re.search(
        r"waits on a/0001_x: rejected: its intent\.md is rejected",
        ask("--root", b, "gate", "0001_y", "impl", peers=[("a", a)]).err,
    )


def related(tmp_path, intent, change=None):
    """A unit ready for `impl`; the snapshot says it depends, by a relation of the backlog, on a
    second one, whose `impl` is done and whose `intent.md` is `intent`."""
    b = make_store(
        tmp_path,
        {
            "0001_y": to_impl("Repo: b."),
            "0002_x": {"intent.md": intent, "impl.md": "# Impl\nStatus: accepted.\n"},
        },
    )
    state = state_of_roots(b)
    state["units"]["/0001_y"]["links"]["backlog"] = [{"ref": "0002_x", "source": "backlog"}]
    if change:
        change(state)

    def gate(*args):
        return python(
            ["--root", str(b), "gate", "0001_y", "impl", *args, "--state", "-"],
            stdin=json.dumps(state),
        )

    return state, gate


def test_a_backlog_relation_to_a_unit_not_shipped_shuts_impl_and_merged_opens_it(tmp_path):
    state, gate = related(tmp_path, intent_for("accepted"))
    shut = gate()
    assert shut.code == 1
    assert re.search(
        r"waits on 0002_x: not merged: the app holds no merge of it \(backlog relation\)", shut.err
    )
    assert json.loads(gate("--json").out)["reasons"] == ["waiting-on"]
    state["units"]["/0002_x"]["merged"] = True
    assert gate().code == 0


def test_a_backlog_relation_to_a_rejected_or_dropped_unit_holds_nothing(tmp_path):
    for intent in [
        intent_for("rejected"),
        f"{intent_for('accepted')}\n## Answers\n{hold_block('Dropped', 'Not needed.')}",
    ]:
        _, gate = related(tmp_path, intent)
        out = gate("--json")
        assert out.code == 0, out.err
        assert json.loads(out.out)["reasons"] == []


def test_status_carries_the_source_of_a_backlog_dependency_and_a_unit_with_none_carries_none(
    tmp_path,
):
    b = make_store(
        tmp_path,
        {
            "0001_y": to_impl("Repo: b. Depends on: 0003_z."),
            "0002_x": {"intent.md": intent_for("accepted")},
            "0003_z": {"intent.md": intent_for("accepted")},
        },
    )
    state = state_of_roots(b)
    state["units"]["/0001_y"]["links"]["backlog"] = [{"ref": "0002_x", "source": "backlog"}]
    argv = ["--root", str(b)]
    out = python(["status", "--json", *argv, "--state", "-"], stdin=json.dumps(state))
    y, x, *_ = json.loads(out.out)["units"]
    assert [[d["ref"], d.get("source")] for d in y["dependsOn"]] == [
        ["0003_z", None],
        ["0002_x", "backlog"],
    ]
    assert "dependsOn" not in x
    gate = python(["gate", "0001_y", "impl", *argv, "--state", "-"], stdin=json.dumps(state))
    assert re.search(r"waits on 0003_z: .* \(Depends on:\)", gate.err)


def test_an_unreadable_idea_shuts_impl_but_no_other_gate(tmp_path):
    b = make_store(tmp_path, {"0001_y": to_impl("Idea: b/ideas/0001_gone.md. Repo: b.")})
    own = [("b", b)]
    for stage in ["spec", "plan"]:
        assert ask("--root", b, "gate", "0001_y", stage, peers=own).code == 0, stage
    out = ask("--root", b, "gate", "0001_y", "impl", peers=own)
    assert out.code == 1
    assert re.search(
        r"Idea: b/ideas/0001_gone\.md cannot be read: the app knows no b/ideas/0001_gone\.md",
        out.err,
    )
    nxt = json.loads(ask("--root", b, "next", "0001_y", peers=own).out)
    assert nxt["stage"] == ""
    assert re.search(r"^fix the idea link — ", nxt["action"])
    assert "why" not in nxt


# --- the pull request's title -------------------------------------------------------------

PR_MD = "\n".join(
    [
        "# PR: the pr body is taken from pr.md",
        "Intent: intent.md. Impl: impl.md. PR: https://github.com/o/r/pull/7. Author: a. "
        "Status: accepted.",
        "",
        "## Where",
        "",
        "https://github.com/o/r/pull/7, branch fix/x, checks pending.",
        "",
        "## Scope of the diff",
        "",
        "## What a reviewer should look at first",
        "",
    ]
)


def test_a_red_check_that_sends_the_work_back_to_impl_waits_on_the_dependency_too(tmp_path):
    red = green_probe([{"name": "tests", "bucket": "fail"}])

    def files(header, n):
        return {
            **to_impl(header),
            "impl.md": "# Impl\nStatus: accepted.\n",
            "pr.md": PR_MD.replace("# PR: the pr body is taken from pr.md", f"# PR: feat({n}): x"),
        }

    a = make_store(tmp_path, {"0001_x": {"intent.md": intent_for("accepted")}})
    b = make_store(
        tmp_path,
        {
            "0001_y": files("Repo: b. Depends on: a/0001_x.", "0001"),
            "0002_free": files("Repo: b.", "0002"),
        },
    )

    def read(name):
        return read_in(b / ".cos" / name, name, b, [("a", a)])

    free = next_step(read("0002_free"), red)
    assert free["stage"] == "impl", "without a dependency, red goes back to impl"
    assert next_step(read("0001_y"), red) == {
        "blocked": True,
        "action": f"{WAITING_ON}a/0001_x to merge",
        "stage": "",
        "why": "dependency",
    }


TITLE_REFUSED = [
    (
        "wip: impl run 1 stopped at max_turns before committing",
        "0049",
        r"is not <type>\(<NNNN>\): <text>",
    ),
    ("a clean rebase voids a passing review", "0049", r"is not <type>\(<NNNN>\): <text>"),
    ("feat(0049): x", "0049", r'type is "feat", but intent\.md declares Type: fix'),
    ("fix(0049): x", "0048", r"names unit 0049, not 0048"),
    ("fix(0049): wip x", "0049", r'opens with "wip"'),
    ("fix(0049): WIP: x", "0049", r'opens with "wip"'),
    ("fix(0049): tiêu đề", "0049", r'carries "ê", a letter with a diacritic'),
    ("fix(0049): de đi", "0049", r'carries "đ"'),
    ("wibble(0049): x", "0049", r'type "wibble" is not one of feat, fix'),
    (None, "0049", r"no title"),
    ("", "0049", r"no title"),
]


def test_title_problem_refuses_the_titles_the_spec_names_and_passes_the_one_it_names():
    for title, number, why in TITLE_REFUSED:
        assert re.search(why, title_problem(title, "fix", number) or "null"), str(title)
    assert title_problem("fix(0049): a pr title is taken from pr.md", "fix", "0049") is None
    # `wip` is a word, not a prefix: `wipe` is text like any other.
    assert title_problem("fix(0049): wipe the stale manifest", "fix", "0049") is None


def test_an_em_dash_passes_and_a_decomposed_vietnamese_letter_does_not():
    assert title_problem("fix(0049): a title — with an em dash", "fix", "0049") is None
    decomposed = "fix(0049): tie\u0302u"
    assert "ê" not in decomposed, "the ê is a base and a combining mark"
    assert re.search(r'carries "ê"', title_problem(decomposed, "fix", "0049"))


def test_a_unit_with_no_type_is_named_as_the_reason():
    assert re.search(r"intent\.md declares no Type", title_problem("fix(0049): x", None, "0049"))
    u = {**unit({**CHAIN}), "type": None}
    assert re.search(r"intent\.md declares no Type", "\n".join(check_gate(u, "review")["need"]))


def pr_tree(tmp_path: Path, files: dict) -> Path:
    root = tmp_path / "prroot"
    for name, text in files.items():
        (root / ".cos" / name).mkdir(parents=True)
        (root / ".cos" / name / "intent.md").write_text(
            "# Intent: x\nAuthor: a. Type: fix. Status: accepted.\n"
        )
        if text is not None:
            (root / ".cos" / name / "pr.md").write_text(text)
    return root


def test_pr_text_prints_title_problem_beside_the_fields_it_printed_before_and_exits_as_before(
    tmp_path,
):
    good = PR_MD.replace(
        "# PR: the pr body is taken from pr.md", "# PR: fix(0049): a pr title is taken from pr.md"
    )
    root = pr_tree(tmp_path, {"0049_a": good, "0050_b": PR_MD})
    ok49 = cli("--root", str(root), "pr-text", "0049_a")
    assert ok49.code == 0, ok49.err
    assert json.loads(ok49.out) == {
        "unit": "0049_a",
        **pr_text(good),
        "status": "accepted",
        "titleProblem": None,
    }
    assert json.loads(ok49.out)["title"] == "fix(0049): a pr title is taken from pr.md"
    bad = cli("--root", str(root), "pr-text", "0050_b")
    assert bad.code == 0, "a title outside the grammar is still printed, exit 0"
    got = json.loads(bad.out)
    assert got["title"] == "the pr body is taken from pr.md"
    assert re.search(r"is not <type>\(<NNNN>\): <text>", got["titleProblem"])
    assert sorted(got) == ["body", "scope", "status", "title", "titleProblem", "unit", "url"]


WIP = "wip: impl run 1 stopped at max_turns before committing"
AGAIN = "the pr stage writes the # PR: line of pr.md again"


def test_the_review_gate_is_closed_on_a_title_outside_the_grammar_before_gh_is_asked(tmp_path):
    calls: list[str] = []
    u = branched({**CHAIN, "pr.md": {**PR, "title": WIP}})
    g = check_gate(u, "review", counted(green_probe(), calls))
    assert g["ok"] is False
    assert g["need"] == [f"{title_problem(WIP, 'feat', '0001')} — {AGAIN}"]
    assert g["need"][0].startswith(f'the title "{WIP}" is not <type>(<NNNN>): <text>')
    assert calls == []
    # Read off the file, as the board reads it: a pr.md with no # PR: line at all.
    _, read = question_tree(
        tmp_path,
        {
            "intent.md": "# I\nAuthor: t. Type: feat. Status: accepted.\n",
            "spec.md": "Status: accepted.\n",
            "plan.md": "Status: accepted.\n",
            "impl.md": "Status: accepted.\n",
            "pr.md": "PR: https://github.com/o/r/pull/1. Status: accepted.\n",
        },
    )
    assert re.search(
        r"pr\.md has no title", check_gate(read, "review", counted(green_probe(), calls))["need"][0]
    )
    assert calls == []
    # And with a good one the gate reads CI as before.
    assert check_gate(branched(CHAIN), "review", green_probe())["ok"] is True


def test_next_names_no_stage_for_a_unit_whose_pr_md_title_is_outside_the_grammar():
    calls: list[str] = []
    title = "a clean rebase voids a passing review"
    n = next_step(
        branched({**CHAIN, "pr.md": {**PR, "title": title}}), counted(green_probe(), calls)
    )
    assert n["stage"] == ""
    assert n["blocked"] is True
    assert re.search(
        r'the title "a clean rebase voids a passing review" is not <type>\(<NNNN>\): <text>'
        r" — the pr stage writes",
        n["action"],
    )
    assert calls == []


def passed_titled(title):
    return branched(
        {
            **CHAIN,
            "pr.md": {**PR, "title": title},
            "review.md": review_art("accepted", round_(1, "pass")),
        }
    )


def titled_view(**over) -> dict:
    return {"state": "OPEN", "headRefOid": SHA, **over}


def test_the_ship_gate_is_closed_on_a_title_outside_the_grammar_before_gh_is_asked():
    calls: list[str] = []
    g = check_gate(passed_titled("fix(0001): x"), "ship", counted(green_probe(), calls))
    assert g["ok"] is False
    assert g["need"] == [f"{title_problem('fix(0001): x', 'feat', '0001')} — {AGAIN}"]
    assert calls == []


def test_the_ship_gate_is_closed_when_the_open_pull_requests_title_differs_and_names_both():
    differs = green_probe(None, {}, titled_view(title="wip: something else"))
    g = check_gate(passed(), "ship", differs)
    assert g["ok"] is False
    assert len(g["need"]) == 1, "the title is the only reason"
    assert g["need"][0] == (
        '#7 carries the title "wip: something else", not pr.md\'s "feat(0001): x" — start ship '
        "from the board, which puts pr.md onto it first"
    )
    # gh giving no title is not a title that matches.
    untitled = SimpleNamespace(
        gh=lambda *a: (
            ok(json.dumps(titled_view()))
            if a[1] == "view"
            else ok(json.dumps([{"name": "tests", "bucket": "pass"}]))
        ),
        git=lambda *a: ok(),
    )
    none = check_gate(passed(), "ship", untitled)
    assert none["ok"] is False
    assert re.search(
        r'#7 carries no title gh could read, not pr\.md\'s "feat\(0001\): x"', none["need"][0]
    )
    # Only once everything else is open: a finding still open is named, and the title is not.
    open_ = branched(
        {
            **CHAIN,
            "review.md": review_art("accepted", round_(1, "pass", ["- F1 [open] x"])),
        }
    )
    assert "carries the title" not in "\n".join(check_gate(open_, "ship", differs)["need"])


def test_titles_that_differ_only_in_surrounding_whitespace_pass():
    spaced = green_probe(None, {}, titled_view(title="  feat(0001): x \n"))
    assert check_gate(passed(), "ship", spaced) == {"ok": True, "need": [], "head": SHA}
    upper = green_probe(None, {}, titled_view(title="feat(0001): X"))
    assert check_gate(passed(), "ship", upper)["ok"] is False


def test_a_merged_pull_requests_title_is_not_compared():
    probe = merged_probe(view=merged_view(title="wip: not the one pr.md gives"))
    assert check_gate(passed(), "ship", probe) == {
        "ok": True,
        "need": [],
        "merged": {"number": 7, "commit": MERGE, "at": MERGED_AT, "head": SHA},
    }


def test_the_title_is_read_from_the_one_gh_pr_view_the_ship_gate_already_asks():
    view = "gh pr view 7 --json state,headRefOid,mergeCommit,mergedAt,title"
    calls: list[str] = []
    assert check_gate(passed(), "ship", counted(green_probe(), calls))["ok"] is True
    assert [c for c in calls if c.startswith("gh ")] == [view]
    differs: list[str] = []
    other = green_probe(None, {}, titled_view(title="feat(0001): y"))
    check_gate(passed(), "ship", counted(other, differs))
    assert [c for c in differs if c.startswith("gh ")] == [view]


def test_next_offers_ship_when_a_differing_title_is_the_only_thing_closing_its_gate():
    differs = green_probe(None, {}, titled_view(title="feat(0001): y"))
    n = next_step(passed(), differs)
    assert n["stage"] == "ship"
    assert n["blocked"] is True
    assert re.search(
        r'^ship — #7 carries the title "feat\(0001\): y", not pr\.md\'s "feat\(0001\): x"',
        n["action"],
    )
    # A draft ship.md a refused merge left, against the last round: the same.
    refused = branched(
        {
            **CHAIN,
            "review.md": review_art("accepted", round_(1, "pass")),
            "ship.md": {
                **art("draft"),
                "ship": {"round": 1, "refused": "Pull request is not mergeable"},
            },
        }
    )
    assert next_step(refused, differs) == {
        "blocked": True,
        "stage": "ship",
        "action": (
            'ship — #7 carries the title "feat(0001): y", not pr.md\'s "feat(0001): x" — start '
            "ship from the board, which puts pr.md onto it first"
        ),
    }
    # Any other reason alongside it and ship is not offered: the title is compared last.
    behind = green_probe(
        None,
        {f"merge-base --is-ancestor refs/remotes/origin/main {SHA}": NOT_ANCESTOR},
        titled_view(title="feat(0001): y"),
    )
    late = next_step(passed(), behind)
    assert late["stage"] == ""
    assert "carries the title" not in late["action"]


def test_no_skill_opens_a_pull_request_with_fill():
    skills = REPO / ".claude" / "skills"
    files = sorted(skills.rglob("*.md"))
    assert len(files) > 5, files
    for f in files:
        assert "--fill" not in f.read_text(), str(f)


def test_next_never_names_write_pr_or_write_ship():
    skills = REPO / ".claude" / "skills"
    for gone in ["write-pr", "write-ship"]:
        assert not (skills / gone).exists(), gone
    base = {
        "intent.md": art("accepted"),
        "spec.md": art("accepted"),
        "plan.md": art("accepted"),
        "impl.md": art("accepted"),
    }
    pr = next_action(unit(base))
    assert [pr["stage"], pr["action"]] == ["pr", "pr"]
    ship = next_action(unit({**base, "pr.md": art("accepted"), "review.md": art("accepted")}))
    assert [ship["stage"], ship["action"]] == ["ship", "ship"]
    rules = (REPO / "coscc" / "loop" / "rules.py").read_text()
    assert not re.search(r"write-(pr|ship)", rules)


# --- metadata read once, by `meta` --------------------------------------------------------

META_STORE = REPO / "tests" / "units" / "testdata" / "meta_store"
META_BEFORE = REPO / "tests" / "units" / "testdata" / "meta_store_before.json"


def meta_of(*args):
    out = cli("--root", str(META_STORE), "meta", *args)
    assert out.code == 0, out.err
    return json.loads(out.out)


def test_meta_prints_every_field_status_reads_from_the_same_parsers():
    units, ideas = meta_of()["units"], meta_of()["ideas"]
    # Every directory, the one misnamed among them; never `ideas/`.
    on_disk = [p.name for p in (META_STORE / ".cos").iterdir() if p.name != "ideas"]
    assert sorted(units) == sorted(on_disk)
    assert units["0016_bad-status"]["artifacts"]["spec.md"]["status"] is None
    assert units["0016_bad-status"]["artifacts"]["spec.md"]["raw"] == "approved"
    assert units["0015_no-status"]["artifacts"]["intent.md"]["raw"] is None
    assert units["0010_full-loop"]["artifacts"]["plan.md"]["status"] == "done"
    assert (
        units["0014_changes-requested"]["artifacts"]["review.md"]["status"] == "changes-requested"
    )
    spec = (META_STORE / ".cos" / "0013_open-question" / "spec.md").read_text()
    assert units["0013_open-question"]["artifacts"]["spec.md"]["questions"] == parse_questions(spec)
    assert units["0013_open-question"]["answers"] == [
        {"artifact": "spec.md", **a} for a in parse_answers(spec)
    ]
    sha = hashlib.sha256(spec.encode()).hexdigest()
    assert units["0013_open-question"]["artifacts"]["spec.md"]["sha256"] == sha
    assert [a["id"] for a in units["0014_changes-requested"]["answers"]] == ["F1"]
    intent = (META_STORE / ".cos" / "0011_paused-then-resumed" / "intent.md").read_text()
    assert [h["state"] for h in units["0011_paused-then-resumed"]["holds"]] == ["paused", "active"]
    assert parse_hold(intent) == {"hold": None, "problems": []}
    assert units["0003_old-unit"]["type"] is None
    assert units["0017_linked"]["type"] == "feat"
    assert units["0017_linked"]["links"] == {
        "idea": "ideas/0001_x.md",
        "repo": "proj",
        "dependsOn": ["0010_full-loop"],
    }
    assert [i["units"] for i in ideas] == [
        [{"ref": "proj/0017_linked", "dependsOn": ["proj/0010_full-loop"]}]
    ]


def test_meta_of_one_unit_reads_only_the_artifacts_named_and_intent_md_brings_its_header():
    only = meta_of("0013_open-question", "spec.md")["units"]["0013_open-question"]
    assert list(only["artifacts"]) == ["spec.md"]
    assert "type" not in only
    assert (
        meta_of("0013_open-question", "intent.md")["units"]["0013_open-question"]["type"] == "feat"
    )
    assert cli("--root", str(META_STORE), "meta", "0013_open-question", "notes.md").code == 2
    assert cli("--root", str(META_STORE), "meta", "../x").code == 2


def snapshot_of(meta: dict) -> dict:
    """The snapshot the app would build from `meta`, for a store whose one workspace is `proj`."""
    return {
        "workspace": "proj",
        "workspaces": ["proj"],
        "units": {
            f"proj/{name}": {
                "artifacts": {
                    f: {
                        "status": a.get("status"),
                        "raw": a.get("raw"),
                        "questions": a.get("questions"),
                    }
                    for f, a in m["artifacts"].items()
                },
                "type": m.get("type"),
                "links": m.get("links") or {"idea": None, "repo": None, "dependsOn": None},
                "holds": [h for h in (m.get("holds") or []) if h.get("by") is not None],
                "answers": m.get("answers"),
                "unknowns": [],
                "merged": (m["artifacts"].get("ship.md") or {}).get("status") == "accepted",
            }
            for name, m in meta["units"].items()
        },
        "ideas": {"proj": meta.get("ideas") or []},
    }


def strip(text: str) -> str:
    lines = text.split("\n")
    cut = next((i for i, ln in enumerate(lines) if ln in ("## Open questions", "## Answers")), -1)
    kept = "\n".join(lines if cut == -1 else lines[:cut])
    return re.sub(r"\b(Status|Type|Idea|Repo|Depends on):\s*[^\s]+(?:,\s*[^\s]+)*\.?", "", kept)


def test_stripping_status_type_idea_depends_on_open_questions_and_answers_lines_changes_nothing(
    tmp_path,
):
    d = tmp_path / "copy"
    shutil.copytree(META_STORE, d)
    state = json.dumps(snapshot_of(meta_of()))

    def run(*args):
        out = python(["--root", str(d), "--state", "-", *args], stdin=state)
        return out.code, out.out.replace(str(d), "<root>"), out.err

    questions = [
        ["status", "--json"],
        ["next", "0013_open-question"],
        ["next", "0017_linked"],
        ["next", "0011_paused-then-resumed"],
        ["gate", "0013_open-question", "plan"],
        ["gate", "0012_dropped", "spec"],
        ["gate", "0017_linked", "spec"],
    ]
    before = [run(*q) for q in questions]
    # Real answers, not seven refusals: status reads, the draft spec shuts `plan`, the drop
    # shuts `spec`.
    assert [b[0] for b in before] == [0, 0, 0, 0, 1, 1, 0]

    def files():
        return cli("--root", str(d), "status", "--json").out.replace(str(d), "<root>")

    unstripped = files()
    cos = d / ".cos"
    for u in cos.iterdir():
        for f in u.iterdir():
            f.write_text(strip(f.read_text()))
    assert "Status:" not in (cos / "0013_open-question" / "spec.md").read_text()
    assert [run(*q) for q in questions] == before
    # Without `--state` the same files now say something else, so what was stripped was read.
    assert files() != unstripped


def test_a_deciding_command_without_state_exits_2_and_says_it_needs_the_app():
    for args in [
        ["status"],
        ["status", "--json"],
        ["gate", "0010_full-loop", "spec"],
        ["next", "0010_full-loop"],
        ["rerun", "0010_full-loop"],
        ["unit-branch", "0010_full-loop"],
        ["pr-text", "0010_full-loop"],
    ]:
        out = python(["--root", str(META_STORE), *args])
        assert out.code == 2, " ".join(args)
        assert NEEDS_STATE in out.err
        assert out.out == ""
    # `meta`, `new-path` and the `check-*` commands read no metadata, and need none.
    assert cli("--root", str(META_STORE), "meta").code == 0


def test_changing_a_status_in_the_snapshot_changes_the_output():
    state = snapshot_of(meta_of())

    def run(s):
        return python(
            ["--root", str(META_STORE), "--state", "-", "next", "0013_open-question"],
            stdin=json.dumps(s),
        )

    before = json.loads(run(state).out)
    spec = state["units"]["proj/0013_open-question"]["artifacts"]["spec.md"]
    spec["status"] = "accepted"
    spec["questions"] = None
    after = json.loads(run(state).out)
    assert after != before
    assert after["stage"] == "plan"


def test_status_json_of_the_fixture_store_is_what_it_was_before_meta_existed():
    out = cli("--root", str(META_STORE), "status", "--json")
    assert out.code == 0, out.err
    now = json.loads(out.out)
    before = json.loads(META_BEFORE.read_text())
    assert {
        **now,
        "root": before["root"],
        "units": [without_lane(u) for u in now["units"]],
    } == before


# --- every fixture unit answers as it did before lanes ------------------------------------


def digest(x) -> str:
    return hashlib.sha256(stringify(x).encode()).hexdigest()[:16]


def answers_of(root: str) -> dict:
    """`next`, `status --json` and `gate` at every stage, for each unit of the store, hashed. The
    three fields a lane adds are dropped first; every other byte stays."""

    def run(*args):
        out = cli("--root", root, *args)
        return {
            "status": out.code,
            "stdout": out.out.replace(root, "<root>"),
            "stderr": out.err,
        }

    status = run("status", "--json")
    data = json.loads(status["stdout"])
    shown = stringify({**data, "units": [without_lane(u) for u in data["units"]]}, 2)
    got = {"status": digest({**status, "stdout": shown})}
    names = sorted(
        p.name for p in (Path(root) / ".cos").iterdir() if p.is_dir() and p.name != "ideas"
    )
    for name in names:
        nxt = run("next", name)
        said = [{**nxt, "stdout": stringify(without_lane(json.loads(nxt["stdout"])))}]
        said += [run("gate", name, stage, "--json") for stage in STAGE_NAMES]
        got[name] = digest(said)
    return got


def test_next_status_json_and_gate_at_every_stage_answer_every_fixture_unit_as_before_lanes():
    assert answers_of(str(META_STORE)) == {
        "status": "22c7b2f418dc630d",
        "0003_old-unit": "9fef610ffae6d63e",
        "0010_full-loop": "b103dea258de2696",
        "0011_paused-then-resumed": "6d7a53343fc70702",
        "0012_dropped": "eddb2787b7ff4218",
        "0013_open-question": "e904c9e2b67edeee",
        "0014_changes-requested": "cf41c58bcf374bf2",
        "0015_no-status": "c5b22eadb152cebe",
        "0016_bad-status": "4bf913f48bd50e56",
        "0017_linked": "3f2e9fb7181b04fe",
        "not_a-unit": "8fcb06c009c258ce",
    }
    assert answers_of(f"{REPO}/") == {
        "status": "7acae9a577d92d16",
        "0001_no-session-management": "9bb372122b1bf9bd",
        "0002_no-workspace-management": "b1b6d1b170a9f610",
        "0003_unproven-page": "022e4485bd8fd4d4",
        "0004_silent-concurrent-loss": "acdf8bfd272af144",
        "0005_hand-driven-invisible-loop": "b45cc856d51e8110",
        "0006_demo-data-and-no-durable-store": "c993c9250f778c88",
        "0007_stale-claims-and-dead-code": "c2990668f97ee979",
        "0008_personal-name-blocks-publishing": "38b54d01234f23d8",
        "0009_branch-and-release-conventions": "b52c01881a10d51e",
        "0010_harness-restates-rules-and-omits-steps": "81f0ea52396808d9",
        "0011_no-install-path-on-a-clean-machine": "4ec160a0d97b4112",
        "0012_installed-copy-runs-no-stage": "500b3104e58b22a3",
        "0013_board-cannot-say-what-happened": "5faddaa084429bc8",
        "0014_product-cannot-start-a-work-unit": "b74827525ff9828d",
    }


# --- the lane a unit walks, read off its files --------------------------------------------

# An intent carrying the three sections a fix enters the fast lane with; `None` leaves one out.
REPRO = "```\npython -m pytest x\n```"
EXPECTED = "Source: coscc/units/guards.py:19-30\nThe table names every code the loop hands out."


def intent_of(
    type="fix",
    status="accepted",
    header=None,
    repro=REPRO,
    expected=EXPECTED,
    actual="It names one fewer.",
):
    parts = [
        "# Intent: x",
        f"Author: t. Type: {type}. Status: {status}.",
        *([header] if header else []),
        "",
        *([] if repro is None else ["## Reproduction", "", repro, ""]),
        *([] if expected is None else ["## Expected", "", expected, ""]),
        *([] if actual is None else ["## Actual", "", actual, ""]),
    ]
    return "\n".join(parts)


def lane_for(parts=None, artifacts=None, impl=None):
    parts = parts or {}
    artifacts = artifacts or {"intent.md": art("accepted")}
    u = {**unit(artifacts), "type": parts.get("type", "fix")}
    return lane_of(u, intent_of(**parts), impl)


FAST = {"lane": "fast", "enteredFast": True, "laneMissing": []}


def test_a_fix_with_its_reproduction_a_cited_expected_result_and_the_actual_one_is_fast():
    assert lane_for() == FAST
    assert lane_for({"expected": "Source: `docs/x.md`\nIt says so."}) == FAST
    assert lane_for({"expected": "Source: README.md\nIt says so."}) == FAST


def test_a_unit_that_is_not_a_fix_is_in_the_full_lane_and_nothing_is_missing_for_it():
    assert lane_for({"type": "feat"}) == FULL_LANE
    assert lane_of({**unit({}), "type": None}) == FULL_LANE


def test_a_fix_missing_one_mark_is_in_the_full_lane_and_lane_missing_names_that_mark():
    def missing(parts, **opts):
        got = lane_for(parts, **opts)
        assert got["lane"] == "full"
        return got["laneMissing"]

    assert missing({"repro": None}) == ["b"]
    assert missing({"repro": "```\n\n```"}) == ["b"]
    assert missing({"repro": "python -m pytest x"}) == ["b"]
    assert missing({"expected": None}) == ["c"]
    assert missing({"expected": "The table names every code."}) == ["c"]
    assert missing({"actual": None}) == ["d"]
    assert missing({"actual": "   "}) == ["d"]
    assert missing({"repro": None, "expected": None, "actual": None}) == ["b", "c", "d"]
    assert lane_of({**unit({}), "type": "fix"})["laneMissing"] == ["b", "c", "d"]


def test_the_expected_result_cites_one_relative_path_outside_cos_and_says_something_besides():
    def c(expected):
        return lane_for({"expected": expected})["laneMissing"]

    assert c("Source: /etc/passwd\nIt says so.") == ["c"]
    assert c("Source: .cos/0001_x/spec.md\nIt says so.") == ["c"]
    assert c("Source: ../other/README.md\nIt says so.") == ["c"]
    assert c("Source: docs/../../x.md\nIt says so.") == ["c"]
    assert c("Source: coscc/units/guards.py:30-19\nIt says so.") == ["c"]
    assert c("Source: coscc/units/guards.py") == ["c"]
    assert c("Source: a.md\nSource: b.md\nIt says so.") == ["c"]
    assert c("Source: the guards file\nIt says so.") == ["c"]


def test_a_spec_a_plan_or_lane_full_in_the_impl_header_keeps_a_fix_in_full_and_it_still_entered():
    left = {"lane": "full", "enteredFast": True, "laneMissing": ["e"]}
    draft = {"intent.md": art("accepted"), "spec.md": art("draft")}
    assert lane_for({}, artifacts=draft) == left
    planned = {"intent.md": art("accepted"), "plan.md": art("accepted")}
    assert lane_for({}, artifacts=planned) == left
    full_header = (
        "# Impl: x\nIntent: intent.md. Lane: full. Status: draft.\n\n## Why full\n\nBigger.\n"
    )
    assert lane_for({}, impl=full_header) == left
    # Only the header decides: the words further down are prose.
    prose = "# Impl: x\nIntent: intent.md. Status: draft.\n\n## What was built\n\nNot Lane: full.\n"
    assert lane_for({}, impl=prose) == FAST


def lane_tree(tmp_path, files):
    """One store holding a unit with `files`, asked through the command line as the app asks."""
    root = make_store(tmp_path, {"0001_x": files})

    def run(*args):
        return ask("--root", root, *args)

    return SimpleNamespace(
        dir=root / ".cos" / "0001_x",
        ask=run,
        next=lambda: json.loads(run("next", "0001_x").out),
        gate=lambda stage: json.loads(run("gate", "0001_x", stage, "--json").out),
    )


def test_a_fix_in_the_fast_lane_goes_from_its_accepted_intent_to_impl(tmp_path):
    t = lane_tree(tmp_path, {"intent.md": intent_of()})
    n = t.next()
    assert [n["stage"], n["lane"], n["enteredFast"], n["laneMissing"]] == ["impl", "fast", True, []]
    assert t.gate("impl") == {
        "ok": True,
        "lines": ["open: impl may proceed for 0001_x"],
        "reasons": [],
    }
    for stage in ["spec", "spike", "plan"]:
        g = t.gate(stage)
        assert g["ok"] is False, stage
        assert g["reasons"][0] == "not-in-lane", stage
        assert f"{stage} is not a stage of the fast lane" in g["lines"][1], stage
    for stage in ["idea", "intent", "pr", "review", "ship"]:
        assert "not-in-lane" not in t.gate(stage)["reasons"], stage
    status = json.loads(t.ask("status", "--json").out)
    u = status["units"][0]
    assert [u["lane"], u["enteredFast"], u["laneMissing"]] == ["fast", True, []]
    assert all("lanes" not in s for s in status["stages"])


def test_a_fix_in_the_fast_lane_still_waits_on_its_intent_being_accepted(tmp_path):
    t = lane_tree(tmp_path, {"intent.md": intent_of(status="draft")})
    n = t.next()
    assert [n["stage"], n["action"], n["lane"]] == ["", "finish and accept intent.md", "fast"]
    assert t.gate("impl")["reasons"] == ["draft"]


def test_gate_impl_in_the_fast_lane_stays_shut_while_stale_held_or_a_dependency_is_not_merged(
    tmp_path,
):
    text = intent_of()
    rerun = (
        "\n## Answers\n\n### Rerun\nRequested by: owner. Date: 2026-09-29. Via: product.\n"
        f"Stage: intent.\nStale: intent.md sha256:{above_answers(text)}\n"
    )
    stale = lane_tree(tmp_path, {"intent.md": text + rerun})
    assert stale.gate("impl")["ok"] is False
    assert "stale" in stale.gate("impl")["reasons"]
    assert stale.next()["stage"] == "intent"

    paused = hold_block("Paused", "chờ một bản khác")
    held = lane_tree(tmp_path, {"intent.md": f"{text}\n## Answers\n{paused}"})
    assert [held.gate("impl")["ok"], held.gate("impl")["reasons"]] == [False, ["paused"]]
    assert held.next()["stage"] == ""

    header = "Idea: ideas/0001_f.md. Repo: b. Depends on: a/0001_x."
    a = make_store(
        tmp_path,
        {"0001_x": {"intent.md": intent_for("accepted"), "ship.md": "# Ship\nStatus: draft.\n"}},
    )
    b = make_store(
        tmp_path,
        {"0001_y": {"intent.md": intent_of(header=header)}},
        {"0001_f.md": idea_with("- a/0001_x.", "- b/0001_y. Depends on: a/0001_x.")},
    )
    peers = [("a", a)]

    def gate():
        return json.loads(ask("--root", b, "gate", "0001_y", "impl", "--json", peers=peers).out)

    def nxt():
        return json.loads(ask("--root", b, "next", "0001_y", peers=peers).out)

    assert [gate()["ok"], gate()["reasons"]] == [False, ["waiting-on"]]
    assert [nxt()["stage"], nxt()["why"], nxt()["lane"]] == ["", "dependency", "fast"]
    (a / ".cos" / "0001_x" / "ship.md").write_text("# Ship\nStatus: accepted.\n")
    assert gate()["ok"] is True
    assert nxt()["stage"] == "impl"


def test_a_fix_whose_intent_cites_no_source_walks_the_full_lane_from_spec(tmp_path):
    t = lane_tree(tmp_path, {"intent.md": intent_of(expected="It should name every code.")})
    n = t.next()
    assert [n["stage"], n["lane"], n["enteredFast"], n["laneMissing"]] == [
        "spec",
        "full",
        False,
        ["c"],
    ]
    assert t.gate("spec")["ok"] is True
    assert t.gate("impl")["reasons"] == ["missing"]


def test_review_and_ship_ask_a_fix_in_the_fast_lane_what_they_ask_one_in_the_full_lane():
    fast_chain = {"intent.md": art("accepted"), "impl.md": art("accepted"), "pr.md": PR}

    def both(extra):
        return [
            branched({**CHAIN, **extra}),
            {**branched({**fast_chain, **extra}), "lane": "fast"},
        ]

    red = green_probe([{"name": "tests", "bucket": "fail"}])
    probes = [
        green_probe(),
        red,
        green_probe([{"name": "tests", "bucket": "pending"}]),
        green_probe([]),
        moved_to(["src/a.py"]),
        None,
    ]
    out = "\n".join(round_(n, "changes-requested", ["- F1 [open] x"]) for n in [1, 2, 3])
    reviews = [
        {},
        {"review.md": review_art("accepted", round_(1, "pass"))},
        {"review.md": review_art("changes-requested", out)},
    ]
    for extra in reviews:
        for probe in probes:
            full, fast = both(extra)
            for stage in ["review", "ship"]:
                assert gate_answer(fast, stage, probe) == gate_answer(full, stage, probe), stage
            assert next_answer(fast, probe) == next_answer(full, probe)
    # What was compared closes and opens: red CI, the rounds used, the head the pass pins.
    _, fast = both({})
    assert "ci-red" in gate_answer(fast, "review", red)["reasons"]
    _, spent = both(reviews[2])
    assert re.search(
        r"needs a person — review used 3 of 3 rounds", next_answer(spent, green_probe())["action"]
    )
    _, done = both(reviews[1])
    assert gate_answer(done, "ship", green_probe())["head"] == SHA
    assert gate_answer(done, "ship", moved_to(["src/a.py"]))["ok"] is False


def test_an_impl_that_takes_a_fix_out_of_the_fast_lane_sends_it_to_spec_and_impl_runs_after_plan(
    tmp_path,
):
    t = lane_tree(
        tmp_path,
        {
            "intent.md": intent_of(),
            "impl.md": "# Impl: x\nIntent: intent.md. Author: Uruz. Lane: full. Status: draft.\n\n"
            "## Why full\n\nThe source says otherwise.\n",
        },
    )
    n = t.next()
    assert [n["stage"], n["lane"], n["enteredFast"], n["laneMissing"]] == [
        "spec",
        "full",
        True,
        ["e"],
    ]
    assert t.gate("spec")["ok"] is True
    (t.dir / "spec.md").write_text("# Spec: x\nIntent: intent.md. Status: accepted.\n")
    assert t.gate("plan")["ok"] is True
    assert t.next()["stage"] == "plan"
    (t.dir / "plan.md").write_text(
        "# Plan: x\nIntent: intent.md. Spec: spec.md. Status: accepted.\n"
    )
    n = t.next()
    assert [n["stage"], n["reasons"], n["lane"]] == ["impl", ["missing"], "full"]
    assert re.search(r"leaving the fast lane", n["action"])
    assert t.gate("impl")["ok"] is True
    # Once impl rewrites its record without the line, a spec already keeps the unit in full.
    (t.dir / "impl.md").write_text("# Impl: x\nIntent: intent.md. Author: Uruz. Status: draft.\n")
    n = t.next()
    assert [n["stage"], n["action"], n["lane"], n["laneMissing"]] == [
        "",
        "finish and accept impl.md",
        "full",
        ["e"],
    ]
