"""Codes beside the words, the workflows a red check is read against, one idea over several units
and repositories, the pull request's title, the metadata `meta` prints and the lane a unit walks.

A deciding command is handed the snapshot the app would build of the files under
`--root` and of the stores named as peers, from `meta`'s own readers.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from coscc.loop import STAGE_NAMES, stringify
from coscc.loop.model import (
    WAITING_ON,
    above_answers,
    lane_of,
    parse_unit_ref,
    read_unit,
    title_problem,
)
from coscc.loop.paths import unit_meta
from coscc.loop.probe import make_probe
from coscc.loop.repo_rules import branch_checks
from coscc.loop.rules import gate_answer, next_answer
from tests.loop.conftest import python
from tests.loop.test_model import (
    NOT_ANCESTOR,
    check_gate,
    next_step,
    CHAIN,
    FULL_LANE,
    SHA,
    PR,
    art,
    branched,
    entry_from,
    green_probe,
    hold_block,
    moved_to,
    ok,
    passed,
    question_tree,
    review_art,
    round_,
    unit,
)

# --- the suite's glue -------------------------------------------------------------------


def state_for(stores: dict[str, Path], own: str = "", links: dict | None = None) -> dict:
    """The snapshot of the stores `{workspace: <root>/.cos}`; `own` is the one the command reads.
    `links` are the rows the press wrote, `{unit: {"idea": ..., "dependsOn": [...]}}`, of `own`."""
    units = {}
    for ws, cos in stores.items():
        if not cos.exists():
            continue
        for d in sorted(cos.iterdir()):
            if d.is_dir() and d.name != "ideas":
                units[f"{ws}/{d.name}"] = entry_from(unit_meta(str(d)))
    for name, linked in (links or {}).items():
        units[f"{own}/{name}"]["links"] = {"idea": None, "dependsOn": None, **linked}
    return {"workspace": own, "workspaces": [w for w in stores if w], "units": units}


def state_of_roots(root, peers=(), links=None) -> dict:
    """The snapshot of the store at `root` and of `peers`, `(workspace, root)` pairs."""
    at = Path(root).resolve()
    own = next((n for n, d in peers if Path(d).resolve() == at), "")
    stores = {own: at / ".cos"}
    for n, d in peers:
        if n != own:
            stores[n] = Path(d).resolve() / ".cos"
    return state_for(stores, own, links)


def ask(*argv, peers=(), records=None, links=None):
    """A deciding command on `--root`, handed the snapshot of it and of `peers`; `records` are
    the submitted records of the own store's units, `{unit: {file: {"result": ...}}}`, and `links`
    the rows of its units."""
    words = [str(a) for a in argv]
    root = words[words.index("--root") + 1]
    state = state_of_roots(root, peers, links)
    for name, files in (records or {}).items():
        arts = state["units"][f"{state['workspace']}/{name}"]["artifacts"]
        for f, rec in files.items():
            arts.setdefault(f, {"status": None, "raw": None, "questions": None}).update(rec)
    return python([*words, "--state", "-"], stdin=json.dumps(state))


def read_in(dir_: Path, name: str, root: Path, peers=(), links=None) -> dict:
    """`read_unit` of `dir_` under `name`, in the snapshot of the store at `root` and `peers`."""
    state = state_of_roots(root, peers, links)
    entry = state["units"][f"{state['workspace']}/{name}"] = entry_from(unit_meta(str(dir_)))
    entry["links"] = {"idea": None, "dependsOn": None, **(links or {}).get(name, {})}
    return read_unit(str(dir_), name, state)


def make_store(tmp_path: Path, units: dict) -> Path:
    """A store root whose `.cos/` holds `units`, `{name: {file: text}}`."""
    root = tmp_path / f"store{len(list(tmp_path.iterdir()))}"
    (root / ".cos").mkdir(parents=True)
    for name, files in units.items():
        (root / ".cos" / name).mkdir()
        for f, text in files.items():
            (root / ".cos" / name / f).write_text(text)
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


# --- the checks a workflow names ----------------------------------------------------------


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


# --- one idea, several units, several repositories ----------------------------------------


def intent_for(status):
    return f"# Intent: x\nAuthor: a. Type: feat. Status: {status}.\n"


def to_impl():
    """A unit whose next stage is `impl`."""
    return {
        "intent.md": intent_for("accepted"),
        "spec.md": "# Spec\nStatus: skipped.\n",
        "plan.md": "# Plan\nStatus: accepted.\n",
    }


# The rows the press wrote for `b/0001_y`: it depends on `a/0001_x`, in the idea `0001_f`.
Y_LINKS = {"0001_y": {"idea": "b/ideas/0001_f.md", "dependsOn": ["a/0001_x"]}}


def pair(tmp_path, ship="draft", a_intent=None):
    """Workspace `a` holds the unit depended on; `b` holds the unit that waits."""
    files = {"intent.md": a_intent or intent_for("accepted")}
    if ship:
        files["ship.md"] = f"# Ship\nStatus: {ship}.\n"
    a = make_store(tmp_path, {"0001_x": files})
    b = make_store(tmp_path, {"0001_y": to_impl()})
    return a, b


def test_a_child_unit_carries_idea_and_depends_on_in_status_json(tmp_path):
    a, b = pair(tmp_path)
    out = ask("--root", b, "status", "--json", peers=[("a", a)], links=Y_LINKS).out
    [u] = json.loads(out)["units"]
    assert u["idea"] == "b/ideas/0001_f.md"
    assert "repo" not in u
    assert u["dependsOn"] == [
        {"ref": "a/0001_x", "merged": False, "why": "not merged: the app holds no merge of it"}
    ]
    assert u["problems"] == []


def test_a_unit_without_links_has_none_of_the_keys(tmp_path):
    a, _ = pair(tmp_path)
    [u] = json.loads(ask("--root", a, "status", "--json").out)["units"]
    for key in ["idea", "repo", "dependsOn"]:
        assert key not in u, key


def test_a_unit_ref_is_a_name_alone_or_a_workspace_and_a_name():
    assert parse_unit_ref("api/0001_x") == {"ws": "api", "name": "0001_x"}
    assert parse_unit_ref("0001_x") == {"ws": None, "name": "0001_x"}
    assert parse_unit_ref("api/1_x") is None


def test_a_dependency_in_a_workspace_the_app_does_not_know_is_a_problem(tmp_path):
    b = make_store(tmp_path, {"0002_p": to_impl()})
    links = {"0002_p": {"dependsOn": ["c/0001_z", "0002_p", "b/0002_p"]}}
    [u] = json.loads(ask("--root", b, "status", "--json", links=links).out)["units"]
    problems = "\n".join(u["problems"])
    assert re.search(r"c/0001_z — the app has no workspace named c", problems)
    assert re.search(r"0002_p — a unit cannot depend on itself", problems)
    assert re.search(r"b/0002_p — the app has no workspace named b", problems)


@pytest.mark.parametrize("ship", ["draft", None])
def test_impl_gate_stays_shut_while_the_dependencys_ship_is_not_accepted(tmp_path, ship):
    a, b = pair(tmp_path, ship=ship)
    out = ask("--root", b, "gate", "0001_y", "impl", peers=[("a", a)], links=Y_LINKS)
    assert out.code == 1, str(ship)
    assert re.search(r"waits on a/0001_x: not merged: the app holds no merge of it", out.err)
    # `plan` is not `impl`: the wait closes nothing else.
    assert ask("--root", b, "gate", "0001_y", "plan", peers=[("a", a)], links=Y_LINKS).code == 0


def test_a_dependency_opens_on_the_merged_row_not_on_ship_md(tmp_path):
    def gate(state, b):
        return python(
            ["--root", str(b), "gate", "0001_y", "impl", "--state", "-"], stdin=json.dumps(state)
        )

    def asked_with(ship, merged):
        a, b = pair(tmp_path, ship=ship)
        state = state_of_roots(b, [("a", a)], Y_LINKS)
        state["units"]["a/0001_x"]["merged"] = merged
        return gate(state, b)

    shut = asked_with("accepted", False)
    assert shut.code == 1, "an accepted ship.md with no merge row opens nothing"
    assert re.search(r"waits on a/0001_x: not merged: the app holds no merge of it", shut.err)
    for ship in ["draft", None]:
        assert asked_with(ship, True).code == 0, f"merged with ship.md {ship}"
    # An entry that carries no `merged` at all is not merged.
    a, b = pair(tmp_path, ship="accepted")
    state = state_of_roots(b, [("a", a)], Y_LINKS)
    del state["units"]["a/0001_x"]["merged"]
    assert gate(state, b).code == 1


def test_impl_gate_opens_once_the_dependencys_ship_is_accepted(tmp_path):
    a, b = pair(tmp_path, ship="accepted")
    out = ask("--root", b, "gate", "0001_y", "impl", peers=[("a", a)], links=Y_LINKS)
    assert out.code == 0, out.err
    nxt = json.loads(ask("--root", b, "next", "0001_y", peers=[("a", a)], links=Y_LINKS).out)
    assert nxt["stage"] == "impl"
    assert "why" not in nxt, "why is carried only by a wait"


def test_a_workspace_the_snapshot_does_not_name_shuts_impl_and_says_so(tmp_path):
    _, b = pair(tmp_path, ship="accepted")
    out = ask("--root", b, "gate", "0001_y", "impl", links=Y_LINKS)
    assert out.code == 1
    assert re.search(r"waits on a/0001_x: the app has no workspace named a", out.err)


def test_a_dropped_dependency_shuts_impl_and_says_dropped(tmp_path):
    dropped = f"{intent_for('accepted')}\n## Answers\n{hold_block('Dropped', 'Not needed.')}"
    a, b = pair(tmp_path, ship=None, a_intent=dropped)
    out = ask("--root", b, "gate", "0001_y", "impl", peers=[("a", a)], links=Y_LINKS)
    assert out.code == 1
    assert re.search(r"waits on a/0001_x: dropped", out.err)
    a, b = pair(tmp_path, ship=None, a_intent=intent_for("rejected"))
    assert re.search(
        r"waits on a/0001_x: rejected: its intent\.md is rejected",
        ask("--root", b, "gate", "0001_y", "impl", peers=[("a", a)], links=Y_LINKS).err,
    )


def related(tmp_path, intent, change=None):
    """A unit ready for `impl`; the snapshot says it depends, by a relation of the backlog, on a
    second one, whose `impl` is done and whose `intent.md` is `intent`."""
    b = make_store(
        tmp_path,
        {
            "0001_y": to_impl(),
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


def test_a_relation_to_an_idea_dropped_before_its_intent_reads_dropped(tmp_path):
    def on(depends, backlog=()):
        b = make_store(
            tmp_path,
            {"0001_y": to_impl(), "0002_x": {"idea.md": "# Idea: x\nStatus: accepted.\n"}},
        )
        state = state_of_roots(b)
        state["units"]["/0001_y"]["links"]["dependsOn"] = list(depends) or None
        state["units"]["/0002_x"]["holds"] = [
            {"state": "dropped", "reason": "gộp vào 0055", "by": "Leif", "date": "2026-10-05"}
        ]
        state["units"]["/0001_y"]["links"]["backlog"] = list(backlog)

        def run(*argv):
            return python([*argv, "--root", str(b), "--state", "-"], stdin=json.dumps(state))

        return run

    run = on(["0002_x"])
    y = json.loads(run("status", "--json").out)["units"][0]
    assert [[d["ref"], d["why"]] for d in y["dependsOn"]] == [["0002_x", "dropped"]]
    run = on([], [{"ref": "0002_x", "source": "backlog"}])
    out = run("gate", "0001_y", "impl", "--json")
    assert out.code == 0, out.err
    assert json.loads(out.out)["reasons"] == []
    assert "dependsOn" not in json.loads(run("status", "--json").out)["units"][0]


def test_status_carries_the_source_of_a_backlog_dependency_and_a_unit_with_none_carries_none(
    tmp_path,
):
    b = make_store(
        tmp_path,
        {
            "0001_y": to_impl(),
            "0002_x": {"intent.md": intent_for("accepted")},
            "0003_z": {"intent.md": intent_for("accepted")},
        },
    )
    state = state_of_roots(b)
    state["units"]["/0001_y"]["links"].update(
        dependsOn=["0003_z"], backlog=[{"ref": "0002_x", "source": "backlog"}]
    )
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

    def files(n):
        return {
            **to_impl(),
            "impl.md": "# Impl\nStatus: accepted.\n",
            "pr.md": PR_MD.replace("# PR: the pr body is taken from pr.md", f"# PR: feat({n}): x"),
        }

    a = make_store(tmp_path, {"0001_x": {"intent.md": intent_for("accepted")}})
    b = make_store(
        tmp_path,
        {
            "0001_y": files("0001"),
            "0002_free": files("0002"),
        },
    )

    def read(name):
        return read_in(b / ".cos" / name, name, b, [("a", a)], Y_LINKS)

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


def titled_view(**over) -> dict:
    return {"state": "OPEN", "headRefOid": SHA, **over}


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


def test_a_merged_pull_requests_title_is_not_compared():
    probe = merged_probe(view=merged_view(title="wip: not the one pr.md gives"))
    assert check_gate(passed(), "ship", probe) == {
        "ok": True,
        "need": [],
        "merged": {"number": 7, "commit": MERGE, "at": MERGED_AT, "head": SHA},
    }


# --- metadata read once, by `meta` --------------------------------------------------------


def strip(text: str) -> str:
    lines = text.split("\n")
    cut = next((i for i, ln in enumerate(lines) if ln in ("## Open questions", "## Answers")), -1)
    kept = "\n".join(lines if cut == -1 else lines[:cut])
    return re.sub(r"\b(Status|Type|Idea|Repo|Depends on):\s*[^\s]+(?:,\s*[^\s]+)*\.?", "", kept)


# --- every fixture unit answers as it did before lanes ------------------------------------


def digest(x) -> str:
    return hashlib.sha256(stringify(x).encode()).hexdigest()[:16]


# --- the lane a unit walks, read off its records -----------------------------------------

# What intent's `fix` carries for a fix to enter the fast lane.
FIX = {
    "reproduction": "python -m pytest x",
    "expected": {
        "source": "coscc/units/guards.py:19-30",
        "text": "The table names every code the loop hands out.",
    },
    "actual": "It names one fewer.",
}
LEFT = "The source says otherwise."


def fix_with(**over):
    """`FIX` with `reproduction`, `actual`, `source` or `text` replaced; `None` drops the fix."""
    if any(v is None for v in over.values()) and set(over) == {"fix"}:
        return None
    expected = {
        "source": over.get("source", FIX["expected"]["source"]),
        "text": over.get("text", FIX["expected"]["text"]),
    }
    return {
        "reproduction": over.get("reproduction", FIX["reproduction"]),
        "expected": expected,
        "actual": over.get("actual", FIX["actual"]),
    }


def intent_of(type="fix", status="accepted"):
    return f"# Intent: x\nAuthor: t. Type: {type}. Status: {status}.\n"


def lane_for(fix=FIX, artifacts=None, left_lane=None, type="fix"):
    artifacts = artifacts or {"intent.md": art("accepted")}
    return lane_of({**unit(artifacts), "type": type}, fix, left_lane)


FAST = {"lane": "fast", "enteredFast": True, "laneMissing": []}


def test_a_fix_with_its_reproduction_a_cited_expected_result_and_the_actual_one_is_fast():
    assert lane_for() == FAST
    assert lane_for(fix_with(source="docs/x.md")) == FAST
    assert lane_for(fix_with(source="README.md")) == FAST


def test_a_unit_that_is_not_a_fix_is_in_the_full_lane_and_nothing_is_missing_for_it():
    assert lane_for(type="feat") == FULL_LANE
    assert lane_of({**unit({}), "type": None}) == FULL_LANE


def test_a_fix_missing_one_mark_is_in_the_full_lane_and_lane_missing_names_that_mark():
    def missing(fix):
        got = lane_for(fix)
        assert got["lane"] == "full"
        return got["laneMissing"]

    assert missing(fix_with(reproduction="")) == ["b"]
    assert missing(fix_with(reproduction="  ")) == ["b"]
    assert missing(fix_with(text="")) == ["c"]
    assert missing(fix_with(actual="")) == ["d"]
    assert missing(fix_with(actual="   ")) == ["d"]
    assert missing(fix_with(reproduction="", text="", actual="")) == ["b", "c", "d"]
    assert missing(None) == ["b", "c", "d"]
    assert lane_of({**unit({}), "type": "fix"})["laneMissing"] == ["b", "c", "d"]


def test_the_expected_result_cites_one_relative_path_outside_cos_and_says_something_besides():
    def c(source):
        return lane_for(fix_with(source=source))["laneMissing"]

    assert c("/etc/passwd") == ["c"]
    assert c(".cos/0001_x/spec.md") == ["c"]
    assert c("../other/README.md") == ["c"]
    assert c("docs/../../x.md") == ["c"]
    assert c("coscc/units/guards.py:30-19") == ["c"]
    assert c("coscc/units/guards.py:0-3") == ["c"]
    assert c("the guards file") == ["c"]
    assert c("") == ["c"]
    assert c("coscc/units/guards.py") == []


def test_a_spec_a_plan_or_a_left_lane_in_the_impl_record_keeps_a_fix_in_full_and_it_still_entered():
    left = {"lane": "full", "enteredFast": True, "laneMissing": ["e"]}
    draft = {"intent.md": art("accepted"), "spec.md": art("draft")}
    assert lane_for(artifacts=draft) == left
    planned = {"intent.md": art("accepted"), "plan.md": art("accepted")}
    assert lane_for(artifacts=planned) == left
    assert lane_for(left_lane=LEFT) == left
    assert lane_for(left_lane="") == FAST
    assert lane_for(left_lane=None) == FAST


def lane_tree(tmp_path, files, fix=FIX, left_lane=None):
    """One store holding a unit with `files`, asked through the command line as the app asks, its
    records saying `fix` (intent) and `left_lane` (impl)."""
    root = make_store(tmp_path, {"0001_x": files})
    records = {"intent.md": {"result": {"judgement": "ready", "fix": fix}}}
    if fix is None:
        records = {}
    if left_lane is not None:
        records["impl.md"] = {"result": {"judgement": "ready", "left_lane": left_lane}}

    def run(*args):
        return ask("--root", root, *args, records={"0001_x": records})

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
        "lane": "fast",
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

    a = make_store(
        tmp_path,
        {"0001_x": {"intent.md": intent_for("accepted"), "ship.md": "# Ship\nStatus: draft.\n"}},
    )
    b = make_store(tmp_path, {"0001_y": {"intent.md": intent_of()}})
    peers = [("a", a)]

    records = {"0001_y": {"intent.md": {"result": {"judgement": "ready", "fix": FIX}}}}

    def gate():
        args = ("--root", b, "gate", "0001_y", "impl", "--json")
        return json.loads(ask(*args, peers=peers, records=records, links=Y_LINKS).out)

    def nxt():
        return json.loads(
            ask("--root", b, "next", "0001_y", peers=peers, records=records, links=Y_LINKS).out
        )

    assert [gate()["ok"], gate()["reasons"]] == [False, ["waiting-on"]]
    assert [nxt()["stage"], nxt()["why"], nxt()["lane"]] == ["", "dependency", "fast"]
    (a / ".cos" / "0001_x" / "ship.md").write_text("# Ship\nStatus: accepted.\n")
    assert gate()["ok"] is True
    assert nxt()["stage"] == "impl"


def test_a_fix_whose_intent_cites_no_source_walks_the_full_lane_from_spec(tmp_path):
    t = lane_tree(tmp_path, {"intent.md": intent_of()}, fix=fix_with(text=""))
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
            "impl.md": "# Impl: x\nIntent: intent.md. Author: Uruz. Status: draft.\n",
        },
        left_lane=LEFT,
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
    # Once impl hands back its record without `left_lane`, a spec already keeps the unit in full.
    t = lane_tree(
        tmp_path,
        {
            "intent.md": intent_of(),
            "impl.md": (t.dir / "impl.md").read_text(),
            "spec.md": (t.dir / "spec.md").read_text(),
            "plan.md": (t.dir / "plan.md").read_text(),
        },
    )
    n = t.next()
    assert [n["stage"], n["action"], n["lane"], n["laneMissing"]] == [
        "",
        "finish and accept impl.md",
        "full",
        ["e"],
    ]


def test_the_ship_gate_is_closed_on_a_title_outside_the_grammar_before_gh_is_asked():
    calls: list[str] = []
    g = check_gate(passed_titled("fix(0001): x"), "ship", counted(green_probe(), calls))
    assert g["ok"] is False
    assert g["need"] == [f"{title_problem('fix(0001): x', 'feat', '0001')} — {AGAIN}"]
    assert calls == []


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


def passed_titled(title):
    return branched(
        {
            **CHAIN,
            "pr.md": {**PR, "title": title},
            "review.md": review_art("accepted", round_(1, "pass")),
        }
    )
