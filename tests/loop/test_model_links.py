"""Codes beside the words, the workflows a red check is read against, one idea over several units
and repositories, the pull request's title, the lane a unit walks.

A deciding command is handed the snapshot the app would build: each unit's rows, stated by the
test as `known` entries, `{"<workspace>/<name>": entry}` (the workspace of `--root` is empty).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from coscc.units import guards

from coscc.loop import proc_of, stringify
from coscc.loop import model
from coscc.loop.model import (
    WAITING_ON,
    parse_unit_ref,
    read_unit,
)
from coscc.loop.probe import make_probe
from coscc.loop.repo_rules import branch_checks
from coscc.loop.rules import gate_answer, next_answer
from tests.loop.conftest import pr_row, python, rerun_row
from tests.loop.test_model import (
    NOT_ANCESTOR,
    check_gate,
    next_step,
    CHAIN,
    SHA,
    PR,
    art,
    branched,
    green_probe,
    hold,
    known,
    moved_to,
    ok,
    passed,
    fr,
    review_art,
    round_,
)

STAGE_NAMES = proc_of(None).names

# --- the suite's glue -------------------------------------------------------------------


def state_for(
    stores: dict[str, Path],
    own: str = "",
    links: dict | None = None,
    entries: dict[str, dict] | None = None,
) -> dict:
    """The snapshot of the stores `{workspace: <root>/.cos}`; `own` is the one the command reads.
    `entries` are the units' rows, `{"<workspace>/<name>": known(...)}`; a directory with none has
    no rows. `links` are the rows the press wrote, `{unit: {"idea": ..., "dependsOn": [...]}}`,
    of `own`."""
    units = {}
    for ws, cos in stores.items():
        if not cos.exists():
            continue
        for d in sorted(cos.iterdir()):
            if d.is_dir() and d.name != "ideas":
                units[f"{ws}/{d.name}"] = (entries or {}).get(f"{ws}/{d.name}") or known()
    for name, linked in (links or {}).items():
        units[f"{own}/{name}"]["links"] = {"idea": None, "dependsOn": None, **linked}
    return {"workspace": own, "workspaces": [w for w in stores if w], "units": units}


def state_of_roots(root, peers=(), links=None, units=None) -> dict:
    """The snapshot of the store at `root` and of `peers`, `(workspace, root)` pairs."""
    at = Path(root).resolve()
    own = next((n for n, d in peers if Path(d).resolve() == at), "")
    stores = {own: at / ".cos"}
    for n, d in peers:
        if n != own:
            stores[n] = Path(d).resolve() / ".cos"
    return state_for(stores, own, links, units)


def ask(*argv, peers=(), records=None, links=None, units=None):
    """A deciding command on `--root`, handed the snapshot of it and of `peers`; `units` are the
    units' `known` entries, `records` the submitted records of the own store's units,
    `{unit: {file: {"result": ...}}}`, and `links` the rows of its units."""
    words = [str(a) for a in argv]
    root = words[words.index("--root") + 1]
    state = state_of_roots(root, peers, links, units)
    for name, files in (records or {}).items():
        arts = state["units"][f"{state['workspace']}/{name}"]["artifacts"]
        for f, rec in files.items():
            arts.setdefault(f, {"status": None, "questions": None}).update(rec)
    return python([*words, "--state", "-"], stdin=json.dumps(state))


def read_in(dir_: Path, name: str, root: Path, peers=(), links=None, entry=None) -> dict:
    """`read_unit` of `dir_` under `name`, in the snapshot of the store at `root` and `peers`;
    `entry` is the unit's `known` entry."""
    state = state_of_roots(root, peers, links)
    own = state["workspace"]
    state["units"][f"{own}/{name}"] = {
        **(entry or known()),
        "links": state["units"][f"{own}/{name}"]["links"],
    }
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
    assert next_answer({**branched(CHAIN), "shipped": True})["reasons"] == ["finished"]
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


def to_impl_rows():
    """The rows of `to_impl()`."""
    return known({"intent.md": "accepted", "spec.md": "skipped", "plan.md": "accepted"})


# The rows the press wrote for the unit that waits: its idea, and the unit it depends on.
Y_LINKS = {"0001_y": {"idea": "b/ideas/0001_f.md", "dependsOn": ["a/0001_x"]}}


def pair(tmp_path, ship="draft", a_intent="accepted", a_holds=None):
    """Workspace `a` holds the unit depended on; `b` holds the unit that waits. The third is the
    rows of both."""
    files = {"intent.md": intent_for(a_intent)}
    rows = {"intent.md": a_intent}
    if ship:
        files["ship.md"] = f"# Ship\nStatus: {ship}.\n"
        rows["ship.md"] = ship
    a = make_store(tmp_path, {"0001_x": files})
    b = make_store(tmp_path, {"0001_y": to_impl()})
    return a, b, {"a/0001_x": known(rows, holds=a_holds), "/0001_y": to_impl_rows()}


def test_a_child_unit_carries_idea_and_depends_on_in_status_json(tmp_path):
    a, b, units = pair(tmp_path)
    out = ask("--root", b, "status", "--json", peers=[("a", a)], links=Y_LINKS, units=units).out
    [u] = json.loads(out)["units"]
    assert u["idea"] == "b/ideas/0001_f.md"
    assert "repo" not in u
    assert u["dependsOn"] == [
        {"ref": "a/0001_x", "merged": False, "why": "not merged: the app holds no merge of it"}
    ]
    assert u["problems"] == []


def test_a_unit_without_links_has_none_of_the_keys(tmp_path):
    a, _, units = pair(tmp_path)
    own = {"/0001_x": units["a/0001_x"]}
    [u] = json.loads(ask("--root", a, "status", "--json", units=own).out)["units"]
    for key in ["idea", "repo", "dependsOn"]:
        assert key not in u, key


def test_a_unit_ref_is_a_name_alone_or_a_workspace_and_a_name():
    assert parse_unit_ref("api/0001_x") == {"ws": "api", "name": "0001_x"}
    assert parse_unit_ref("0001_x") == {"ws": None, "name": "0001_x"}
    assert parse_unit_ref("api/1_x") is None


def test_a_dependency_in_a_workspace_the_app_does_not_know_is_a_problem(tmp_path):
    b = make_store(tmp_path, {"0002_p": to_impl()})
    links = {"0002_p": {"dependsOn": ["c/0001_z", "0002_p", "b/0002_p"]}}
    units = {"/0002_p": to_impl_rows()}
    [u] = json.loads(ask("--root", b, "status", "--json", links=links, units=units).out)["units"]
    problems = "\n".join(u["problems"])
    assert re.search(r"c/0001_z — the app has no workspace named c", problems)
    assert re.search(r"0002_p — a unit cannot depend on itself", problems)
    assert re.search(r"b/0002_p — the app has no workspace named b", problems)


@pytest.mark.parametrize("ship", ["draft", None])
def test_impl_gate_stays_shut_while_the_dependencys_ship_is_not_accepted(tmp_path, ship):
    a, b, units = pair(tmp_path, ship=ship)
    out = ask("--root", b, "gate", "0001_y", "impl", peers=[("a", a)], links=Y_LINKS, units=units)
    assert out.code == 1, str(ship)
    assert re.search(r"waits on a/0001_x: not merged: the app holds no merge of it", out.err)
    # `plan` is not `impl`: the wait closes nothing else.
    plan = ask("--root", b, "gate", "0001_y", "plan", peers=[("a", a)], links=Y_LINKS, units=units)
    assert plan.code == 0


def test_a_dependency_opens_on_the_merged_row_not_on_ship_md(tmp_path):
    def gate(state, b):
        return python(
            ["--root", str(b), "gate", "0001_y", "impl", "--state", "-"], stdin=json.dumps(state)
        )

    def asked_with(ship, merged):
        a, b, units = pair(tmp_path, ship=ship)
        state = state_of_roots(b, [("a", a)], Y_LINKS, units)
        state["units"]["a/0001_x"]["merged"] = merged
        return gate(state, b)

    shut = asked_with("accepted", False)
    assert shut.code == 1, "an accepted ship.md with no merge row opens nothing"
    assert re.search(r"waits on a/0001_x: not merged: the app holds no merge of it", shut.err)
    for ship in ["draft", None]:
        assert asked_with(ship, True).code == 0, f"merged with ship.md {ship}"
    # An entry that carries no `merged` at all is not merged.
    a, b, units = pair(tmp_path, ship="accepted")
    state = state_of_roots(b, [("a", a)], Y_LINKS, units)
    del state["units"]["a/0001_x"]["merged"]
    assert gate(state, b).code == 1


def test_impl_gate_opens_once_the_dependencys_ship_is_accepted(tmp_path):
    a, b, units = pair(tmp_path, ship="accepted")
    out = ask("--root", b, "gate", "0001_y", "impl", peers=[("a", a)], links=Y_LINKS, units=units)
    assert out.code == 0, out.err
    peers = [("a", a)]
    nxt = json.loads(
        ask("--root", b, "next", "0001_y", peers=peers, links=Y_LINKS, units=units).out
    )
    assert nxt["stage"] == "impl"
    assert "why" not in nxt, "why is carried only by a wait"


def test_a_workspace_the_snapshot_does_not_name_shuts_impl_and_says_so(tmp_path):
    _, b, units = pair(tmp_path, ship="accepted")
    out = ask("--root", b, "gate", "0001_y", "impl", links=Y_LINKS, units=units)
    assert out.code == 1
    assert re.search(r"waits on a/0001_x: the app has no workspace named a", out.err)


def test_a_dropped_dependency_shuts_impl_and_says_dropped(tmp_path):
    a, b, units = pair(tmp_path, ship=None, a_holds=[hold("dropped", "Not needed.")])
    out = ask("--root", b, "gate", "0001_y", "impl", peers=[("a", a)], links=Y_LINKS, units=units)
    assert out.code == 1
    assert re.search(r"waits on a/0001_x: dropped", out.err)
    a, b, units = pair(tmp_path, ship=None, a_intent="rejected")
    assert re.search(
        r"waits on a/0001_x: rejected: its intent\.md is rejected",
        ask(
            "--root", b, "gate", "0001_y", "impl", peers=[("a", a)], links=Y_LINKS, units=units
        ).err,
    )


def related(tmp_path, intent, change=None, holds=None):
    """A unit ready for `impl`; the snapshot says it depends, by a relation of the backlog, on a
    second one, whose `impl` is done and whose `intent.md` has the status `intent`."""
    b = make_store(
        tmp_path,
        {
            "0001_y": to_impl(),
            "0002_x": {"intent.md": intent_for(intent), "impl.md": "# Impl\nStatus: accepted.\n"},
        },
    )
    units = {
        "/0001_y": to_impl_rows(),
        "/0002_x": known({"intent.md": intent, "impl.md": "accepted"}, holds=holds),
    }
    state = state_of_roots(b, units=units)
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
    state, gate = related(tmp_path, "accepted")
    shut = gate()
    assert shut.code == 1
    assert re.search(
        r"waits on 0002_x: not merged: the app holds no merge of it \(backlog relation\)", shut.err
    )
    assert json.loads(gate("--json").out)["reasons"] == ["waiting-on"]
    state["units"]["/0002_x"]["merged"] = True
    assert gate().code == 0


def test_a_backlog_relation_to_a_rejected_or_dropped_unit_holds_nothing(tmp_path):
    for intent, holds in [("rejected", None), ("accepted", [hold("dropped", "Not needed.")])]:
        _, gate = related(tmp_path, intent, holds=holds)
        out = gate("--json")
        assert out.code == 0, out.err
        assert json.loads(out.out)["reasons"] == []


def test_a_relation_to_an_idea_dropped_before_its_intent_reads_dropped(tmp_path):
    def on(depends, backlog=()):
        b = make_store(
            tmp_path,
            {"0001_y": to_impl(), "0002_x": {"idea.md": "# Idea: x\nStatus: accepted.\n"}},
        )
        units = {"/0001_y": to_impl_rows(), "/0002_x": known({"idea.md": "accepted"})}
        state = state_of_roots(b, units=units)
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
    units = {
        "/0001_y": to_impl_rows(),
        "/0002_x": known({"intent.md": "accepted"}),
        "/0003_z": known({"intent.md": "accepted"}),
    }
    state = state_of_roots(b, units=units)
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


# --- a red check, and the pull request's title ---------------------------------------------


def test_a_red_check_that_sends_the_work_back_to_impl_waits_on_the_dependency_too(tmp_path):
    red = green_probe([{"name": "tests", "bucket": "fail"}])

    def files(n):
        return {
            **to_impl(),
            "impl.md": "# Impl\nStatus: accepted.\n",
            "pr.md": "# PR: x\nStatus: accepted.\n",
        }

    a = make_store(tmp_path, {"0001_x": {"intent.md": intent_for("accepted")}})
    b = make_store(
        tmp_path,
        {
            "0001_y": files("0001"),
            "0002_free": files("0002"),
        },
    )

    rows = {
        **known(
            {
                "intent.md": "accepted",
                "spec.md": "skipped",
                "plan.md": "accepted",
                "impl.md": "accepted",
                "pr.md": "accepted",
            }
        )
    }
    rows["artifacts"]["pr.md"].update(pr_row(7))

    def read(name):
        return read_in(b / ".cos" / name, name, b, [("a", a)], Y_LINKS, rows)

    free = next_step(read("0002_free"), red)
    assert free["stage"] == "impl", "without a dependency, red goes back to impl"
    assert next_step(read("0001_y"), red) == {
        "blocked": True,
        "action": f"{WAITING_ON}a/0001_x to merge",
        "stage": "",
        "why": "dependency",
    }


def titled_view(**over) -> dict:
    return {"state": "OPEN", "headRefOid": SHA, **over}


def test_the_ship_gate_is_closed_when_the_open_pull_requests_title_differs_and_names_both():
    differs = green_probe(None, {}, titled_view(title="wip: something else"))
    g = check_gate(passed(), "ship", differs)
    assert g["ok"] is False
    assert len(g["need"]) == 1, "the title is the only reason"
    assert g["need"][0] == (
        '#7 carries the title "wip: something else", not the unit\'s "feat(0001): x" — start ship '
        "from the board, which puts it onto the pull request first"
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
        r'#7 carries no title gh could read, not the unit\'s "feat\(0001\): x"', none["need"][0]
    )
    # Only once everything else is open: a finding still open is named, and the title is not.
    open_ = branched(
        {
            **CHAIN,
            "review.md": review_art("accepted", [round_(1, "pass", [fr("F1", "open")])]),
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


def missing_marks(fix=FIX, bypassed=False, type="fix"):
    """The `fast-lane` guard's marks that do not hold."""
    marks = guards.fast_lane_marks({"type": type, "fix": fix, "bypassed": bypassed})
    return [k for k, v in marks.items() if not v]


def test_a_fix_with_its_reproduction_a_cited_expected_result_and_the_actual_one_is_fast():
    assert missing_marks() == []
    assert missing_marks(fix_with(source="docs/x.md")) == []
    assert missing_marks(fix_with(source="README.md")) == []
    assert guards.fast_lane({"type": "fix", "fix": FIX}).open is True


def test_a_unit_that_is_not_a_fix_is_not_fast():
    assert missing_marks(type="feat") == ["a"]
    assert guards.fast_lane({"type": "feat", "fix": FIX}).reasons == ("not-in-lane",)


def test_a_fix_missing_one_mark_is_not_fast_and_the_marks_say_which():
    assert missing_marks(fix_with(reproduction="")) == ["b"]
    assert missing_marks(fix_with(reproduction="  ")) == ["b"]
    assert missing_marks(fix_with(text="")) == ["c"]
    assert missing_marks(fix_with(actual="")) == ["d"]
    assert missing_marks(fix_with(actual="   ")) == ["d"]
    assert missing_marks(fix_with(reproduction="", text="", actual="")) == ["b", "c", "d"]
    assert missing_marks(None) == ["b", "c", "d"]


def test_the_expected_result_cites_one_relative_path_outside_cos_and_says_something_besides():
    def c(source):
        return missing_marks(fix_with(source=source))

    assert c("/etc/passwd") == ["c"]
    assert c(".cos/0001_x/spec.md") == ["c"]
    assert c("../other/README.md") == ["c"]
    assert c("docs/../../x.md") == ["c"]
    assert c("coscc/units/guards.py:30-19") == ["c"]
    assert c("coscc/units/guards.py:0-3") == ["c"]
    assert c("the guards file") == ["c"]
    assert c("") == ["c"]
    assert c("coscc/units/guards.py") == []


def test_a_state_the_branch_passes_over_keeps_a_fix_off_it_and_it_still_entered():
    assert missing_marks(bypassed=True) == ["e"]


def lane_tree(tmp_path, files, statuses, fix=FIX, left_lane=None, holds=None, reruns=None):
    """One store holding a unit with `files`, whose rows say `statuses` (a dict the test may
    change), asked through the command line as the app asks, its records saying `fix` (intent)
    and `left_lane` (impl)."""
    root = make_store(tmp_path, {"0001_x": files})
    records = {"intent.md": {"result": {"judgement": "ready", "fix": fix}}}
    if fix is None:
        records = {}
    if reruns:
        records["intent.md"]["record"] = 3
    if left_lane is not None:
        records["impl.md"] = {"result": {"judgement": "ready", "left_lane": left_lane}}

    def run(*args):
        units = {"/0001_x": {**known(statuses, type="fix", holds=holds), "reruns": reruns or []}}
        return ask("--root", root, *args, records={"0001_x": records}, units=units)

    return SimpleNamespace(
        dir=root / ".cos" / "0001_x",
        statuses=statuses,
        ask=run,
        next=lambda: json.loads(run("next", "0001_x").out),
        gate=lambda stage: json.loads(run("gate", "0001_x", stage, "--json").out),
    )


def test_a_fix_in_the_fast_lane_goes_from_its_accepted_intent_to_impl(tmp_path):
    t = lane_tree(tmp_path, {"intent.md": intent_of()}, {"intent.md": "accepted"})
    n = t.next()
    assert [n["stage"], n["process"]] == ["impl", "coscc-sdlc/full"]
    assert t.gate("impl") == {
        "ok": True,
        "lines": ["open: impl may proceed for 0001_x"],
        "reasons": [],
        "via": ["fast-lane"],
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
    assert u["process"] == "coscc-sdlc/full"
    assert all("lanes" not in s for s in status["stages"])


def test_a_fix_in_the_fast_lane_still_waits_on_its_intent_being_accepted(tmp_path):
    t = lane_tree(tmp_path, {"intent.md": intent_of(status="draft")}, {"intent.md": "draft"})
    n = t.next()
    assert [n["stage"], n["action"]] == ["", "finish and accept intent.md"]
    assert t.gate("impl")["reasons"] == ["draft"]


def test_gate_impl_in_the_fast_lane_stays_shut_while_stale_held_or_a_dependency_is_not_merged(
    tmp_path,
):
    text = intent_of()
    accepted = {"intent.md": "accepted"}
    rerun = [rerun_row("intent", date="2026-09-29", intent_md=3)]
    stale = lane_tree(tmp_path, {"intent.md": text}, accepted, reruns=rerun)
    assert stale.gate("impl")["ok"] is False
    assert "stale" in stale.gate("impl")["reasons"]
    assert stale.next()["stage"] == "intent"

    paused = [hold("paused", "chờ một bản khác")]
    held = lane_tree(tmp_path, {"intent.md": text}, accepted, holds=paused)
    assert [held.gate("impl")["ok"], held.gate("impl")["reasons"]] == [False, ["paused"]]
    assert held.next()["stage"] == ""

    a = make_store(
        tmp_path,
        {"0001_x": {"intent.md": intent_for("accepted"), "ship.md": "# Ship\nStatus: draft.\n"}},
    )
    b = make_store(tmp_path, {"0001_y": {"intent.md": intent_of()}})
    peers = [("a", a)]

    records = {"0001_y": {"intent.md": {"result": {"judgement": "ready", "fix": FIX}}}}

    def units(ship):
        return {
            "a/0001_x": known({"intent.md": "accepted", "ship.md": ship}),
            "/0001_y": known({"intent.md": "accepted"}, type="fix"),
        }

    def gate(ship):
        args = ("--root", b, "gate", "0001_y", "impl", "--json")
        kw = {"peers": peers, "records": records, "links": Y_LINKS, "units": units(ship)}
        return json.loads(ask(*args, **kw).out)

    def nxt(ship):
        kw = {"peers": peers, "records": records, "links": Y_LINKS, "units": units(ship)}
        return json.loads(ask("--root", b, "next", "0001_y", **kw).out)

    assert [gate("draft")["ok"], gate("draft")["reasons"]] == [False, ["waiting-on"]]
    assert [nxt("draft")["stage"], nxt("draft")["why"]] == ["", "dependency"]
    assert gate("accepted")["ok"] is True
    assert nxt("accepted")["stage"] == "impl"


def test_a_fix_whose_intent_cites_no_source_walks_the_full_lane_from_spec(tmp_path):
    t = lane_tree(
        tmp_path, {"intent.md": intent_of()}, {"intent.md": "accepted"}, fix=fix_with(text="")
    )
    n = t.next()
    assert n["stage"] == "spec"
    assert "via" not in t.gate("spec")
    assert t.gate("spec")["ok"] is True
    assert t.gate("impl")["reasons"] == ["missing"]


def test_review_and_ship_ask_a_fix_in_the_fast_lane_what_they_ask_one_in_the_full_lane():
    fast_chain = {"intent.md": art("accepted"), "impl.md": art("accepted"), "pr.md": PR}

    def both(extra):
        fast = branched({**fast_chain, **extra})
        model.FACTS[id(fast)] = (fast, {"results": {}, "marks": dict.fromkeys("abcde", True)})
        return [branched({**CHAIN, **extra}), fast]

    red = green_probe([{"name": "tests", "bucket": "fail"}])
    probes = [
        green_probe(),
        red,
        green_probe([{"name": "tests", "bucket": "pending"}]),
        green_probe([]),
        moved_to(["src/a.py"]),
        None,
    ]
    out = [round_(n, "changes-requested", [fr("F1", "open")]) for n in [1, 2, 3]]
    reviews = [
        {},
        {"review.md": review_art("accepted", [round_(1, "pass")])},
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
        {"intent.md": "accepted", "impl.md": "draft"},
        left_lane=LEFT,
    )
    n = t.next()
    assert n["stage"] == "spec"
    assert t.gate("spec")["ok"] is True
    (t.dir / "spec.md").write_text("# Spec: x\nIntent: intent.md. Status: accepted.\n")
    t.statuses["spec.md"] = "accepted"
    assert t.gate("plan")["ok"] is True
    assert t.next()["stage"] == "plan"
    (t.dir / "plan.md").write_text(
        "# Plan: x\nIntent: intent.md. Spec: spec.md. Status: accepted.\n"
    )
    t.statuses["plan.md"] = "accepted"
    n = t.next()
    assert [n["stage"], n["reasons"]] == ["impl", ["missing"]]
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
        dict(t.statuses),
    )
    n = t.next()
    assert [n["stage"], n["action"]] == ["", "finish and accept impl.md"]


def test_next_offers_ship_when_a_differing_title_is_the_only_thing_closing_its_gate():
    differs = green_probe(None, {}, titled_view(title="feat(0001): y"))
    n = next_step(passed(), differs)
    assert n["stage"] == "ship"
    assert n["blocked"] is True
    assert re.search(
        r'^ship — #7 carries the title "feat\(0001\): y", not the unit\'s "feat\(0001\): x"',
        n["action"],
    )
    # A draft ship.md a refused merge left, against the last round: the same.
    refused = branched(
        {
            **CHAIN,
            "review.md": review_art("accepted", [round_(1, "pass")]),
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
            'ship — #7 carries the title "feat(0001): y", not the unit\'s "feat(0001): x" — start '
            "ship from the board, which puts it onto the pull request first"
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
