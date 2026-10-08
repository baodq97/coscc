"""The loop's gates over a review's rounds and a pull request, held to fixed values.

A sequel of `tests/loop/test_model.py`, whose helpers it reuses: the screens a unit changes, a review
that ran out of turns and a round that drops a finding an earlier one raised. Every round, finding
and screen is a row of the app's snapshot.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from coscc.loop import REVIEW_ROUNDS
from coscc.loop.model import non_blocking, review_from
from coscc.loop.probe import UI_STANDARD, glob_match, make_probe, parse_standard, ui_files
from tests.loop.conftest import REPO, env, pr_row, python
from tests.loop.test_model import (
    CHAIN,
    R12,
    ROUND1,
    SHA,
    asked,
    branched,
    check_gate,
    fr,
    fx,
    green_probe,
    known,
    low,
    next_action,
    next_step,
    ok,
    review_art,
    round3,
    round_,
    state_of_root,
    tree_after_round_two,
)

# --- the open lows and the findings not fixed -------------------------------------------


def ship_gate(*rounds, **kw):
    """The ship gate of a unit whose accepted `review.md` holds `rounds`, on a green probe."""
    u = branched({**CHAIN, "review.md": review_art("accepted", rounds)})
    return check_gate(u, "ship", green_probe(), kw.get("limit", REVIEW_ROUNDS))


def test_a_pass_round_with_one_open_low_among_fixed_findings_opens_ship():
    r = round_(
        1,
        "pass",
        [
            fx("F1", "x", "low"),
            fx("F2", "x", "medium"),
            fx("F3", "x", "low"),
            fr(
                "F4",
                "open",
                "Còn hai docstring thuộc cùng loại với F3 mà `9243b7b` chưa sửa, vì vòng 3 không "
                "nêu tên chúng.",
                "low",
                path="coscc/screens.py",
                lines="646",
            ),
        ],
    )
    u = branched({**CHAIN, "review.md": review_art("accepted", [r])})
    g = check_gate(u, "ship", green_probe())
    assert g["ok"] is True, g["need"]
    assert g["need"] == []
    assert [f["id"] for f in non_blocking(u)] == ["F4"]


def test_three_counted_rounds_of_unrated_findings_then_a_pass_of_lows_opens_ship():
    unrated = [
        fr("F1", "open", "biên regex", "", lines="3"),
        fr("F2", "open", "y", "", lines="9"),
    ]
    counted = [round_(n, "changes-requested", unrated) for n in (1, 2, 3)]
    g = ship_gate(*counted, round_(4, "pass", [low("F1"), low("F2")]), limit=REVIEW_ROUNDS)
    assert g["ok"] is True, g["need"]


def test_a_needs_person_round_beside_an_open_low_is_still_a_wait_for_a_person(tmp_path):
    u = tree_after_round_two(tmp_path, [*R12, round3("needs-person", [low("F4")])])
    n = next_action(u)
    assert n["waiting"] == ["F2", "F3"]
    # Each claim with what its finding says in the last round.
    assert re.search(r"^needs a person — F2: a.py:3 — high — b; F3: a.py:3 — high — c", n["action"])
    assert [p["id"] for p in u["personFindings"]] == ["F2", "F3"]
    assert [f["id"] for f in u["nonBlocking"]] == ["F4"]


def test_every_blocking_finding_claimed_and_one_low_unclaimed_is_review_not_impl(tmp_path):
    claimed = [fx("F1", "a"), fr("F2", "open", "b"), fr("F3", "open", "c"), low("F4")]
    r2 = round_(2, "changes-requested", claimed)
    u = tree_after_round_two(tmp_path, [ROUND1, r2])
    assert next_step(u, green_probe())["stage"] == "review"
    only_low = round_(1, "changes-requested", [low("F1")])
    u = tree_after_round_two(tmp_path, [only_low], claims=("F1",))
    assert next_step(u, green_probe())["stage"] == "impl"


def test_a_pass_that_leaves_lows_open_costs_no_round():
    first = round_(1, "pass", [low("F1")])
    cr = [
        round_(n, "changes-requested", [low("F1"), fr("F2", "open", "y", path="b.py", lines="1")])
        for n in (2, 3, 4)
    ]
    spent = next_action(asked(first, *cr))["action"]
    assert "needs a person — review used 3 of 3 rounds" in spent
    assert "(2 of 3 rounds used)" in next_action(asked(first, *cr[:2]))["action"]


# --- a UI unit ships only with screenshots a review looked at ------------------------------


def test_parse_standard_reads_the_globs_under_paths_in_the_front_matter_and_nothing_else():
    text = (
        "---\npaths:\n  - \"coscc/screens.py\"\n  - 'coscc/**'\n  - a/*/b.py\nother: x\n"
        '  - "not/this.py"\n---\n\npaths:\n  - "nor/this.py"\n'
    )
    assert parse_standard(text) == ["coscc/screens.py", "coscc/**", "a/*/b.py"]
    assert parse_standard('# No front-matter\npaths:\n  - "x.py"\n') == []
    assert parse_standard("---\npaths:\n---\n") == []
    assert parse_standard('---\npaths:\n  - "x.py"\n') == []


def test_glob_match_double_star_crosses_directories_and_star_and_question_mark_do_not():
    rows = [
        ("coscc/screens.py", "coscc/screens.py", True),
        ("coscc/screens.py", "coscc/screens_py", False),
        ("coscc/screens.py", "x/coscc/screens.py", False),
        ("coscc/**", "coscc/a/b/c.py", True),
        ("coscc/**", "coscc2/a.py", False),
        ("a/*/b.py", "a/x/b.py", True),
        ("a/*/b.py", "a/x/y/b.py", False),
        ("**/x.py", "x.py", True),
        ("**/x.py", "a/b/x.py", True),
        ("**/x.py", "a/bx.py", False),
        ("coscc/?i.py", "coscc/ui.py", True),
        ("coscc/?i.py", "coscc//i.py", False),
    ]
    for glob, path, want in rows:
        assert glob_match(glob, path) is want, (glob, path)
    paths = ["coscc/ui.py", "coscc/runner.py", "README.md"]
    assert ui_files(paths, ["coscc/ui.py", "*.md"]) == ["coscc/ui.py", "README.md"]


def tracked() -> list[str]:
    r = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True, env=env()
    )
    return [p for p in r.stdout.split("\n") if p]


def test_every_glob_of_this_checkouts_standard_names_a_file_git_tracks(tmp_path):
    globs = parse_standard((REPO / UI_STANDARD).read_text())
    assert len(globs) > 0
    files = tracked()
    for g in globs:
        assert any(glob_match(g, p) for p in files), f"{g} matches no tracked file"
    assert make_probe(str(REPO)).ui() == {"path": UI_STANDARD, "globs": globs}
    assert make_probe(str(tmp_path)).ui() is None


SHOT = {
    "path": ".screens/board-1440x900.png",
    "size": "1440x900",
    "address": "/board",
    "result": "no violation",
}


def screens(taken=SHA, by="agent session s1", standard=UI_STANDARD, shots=None):
    """A round's screens as the snapshot carries them."""
    shots = [SHOT] if shots is None else shots
    return {"taken": taken, "standard": standard, "by": by, "shots": shots}


def test_an_open_low_against_the_standard_blocks_and_one_that_is_not_still_does_not():
    findings = [
        fr(
            "F1",
            "open",
            "shows a full sha",
            "low",
            path="coscc/screens.py",
            lines="10",
            rule="S3",
        ),
        low("F2"),
        fr("F3", "open", "Sx is not a rule id", "low"),
    ]
    assert [f["id"] for f in non_blocking(asked(round_(1, "pass", findings)))] == ["F2", "F3"]
    rounds = [round_(1, "pass", findings[:1], screens=screens())]
    u = branched({**CHAIN, "review.md": review_art("accepted", rounds)})
    g = check_gate(u, "ship", ui_probe(["coscc/screens.py"]))
    assert g["ok"] is False
    assert "F1 [open]" in "\n".join(g["need"])


UI = {"path": UI_STANDARD, "globs": ["coscc/screens.py"]}


def ui_probe(files, extra=None, ui=UI):
    """A probe whose repository has a standard listing one file, and whose branch diff against
    the trunk is `files`; `extra` overrides any other git answer."""
    diff = {f"diff --name-only origin/main...{SHA}": ok("\n".join(files)), **(extra or {})}
    probe = green_probe(None, diff)
    probe.ui = lambda: ui
    return probe


def passed_with(shots_of=None):
    rounds = [round_(1, "pass", screens=shots_of)]
    return branched({**CHAIN, "review.md": review_art("accepted", rounds)})


def test_a_ui_unit_whose_pass_has_no_screens_cannot_ship_and_next_offers_review():
    u = passed_with()
    g = check_gate(u, "ship", ui_probe(["coscc/screens.py"]))
    assert g["ok"] is False
    assert len(g["need"]) == 1
    assert re.search(
        r"review round 1 passed, but it has no ### Screens .* this unit changes "
        r"coscc/screens\.py, which \.claude/rules/ui-standard\.md counts as screens",
        g["need"][0],
    )
    n = next_step(u, ui_probe(["coscc/screens.py"]))
    assert n["stage"] == "review"
    assert "no ### Screens" in n["action"]


def real_ui():
    return {"path": UI_STANDARD, "globs": parse_standard((REPO / UI_STANDARD).read_text())}


@pytest.mark.parametrize(
    "path", ["ui/src/screens/UnitPage.tsx", "coscc/features/vault/ui/index.tsx"]
)
def test_a_changed_screen_of_the_real_standard_blocks_ship_for_want_of_screens(path):
    g = check_gate(passed_with(), "ship", ui_probe([path], {}, real_ui()))
    assert g["ok"] is False
    assert "has no ### Screens" in g["need"][0]


@pytest.mark.parametrize(
    "path",
    ["coscc/features/vault/__init__.py", "coscc/features/notices.py", "coscc/units/read.py"],
)
def test_a_file_that_draws_no_page_reads_exactly_as_with_no_standard(path):
    u = passed_with()
    assert check_gate(u, "ship", ui_probe([path], {}, real_ui())) == check_gate(
        u, "ship", green_probe()
    )


def test_each_condition_broken_once_closes_ship_and_says_which():
    ui = ["coscc/screens.py"]

    def closed(shots_of, extra=None):
        g = check_gate(passed_with(shots_of), "ship", ui_probe(ui, extra))
        assert g["ok"] is False
        assert next_step(passed_with(shots_of), ui_probe(ui, extra))["stage"] == "review"
        return "\n".join(g["need"])

    assert "does not say it was an agent" in closed(screens(by="Bao"))
    assert "names the standard STANDARD.md" in closed(screens(standard="STANDARD.md"))
    taken = "e" * 40
    not_ancestor = {f"merge-base --is-ancestor {taken} {SHA}": {"code": 1, "out": "", "err": ""}}
    assert f"taken at {taken}, which is not an ancestor of the reviewed commit {SHA}" in closed(
        screens(taken=taken), not_ancestor
    )
    moved = {f"diff --name-only {taken}..{SHA}": ok("coscc/runner.py\ncoscc/screens.py\n")}
    assert (
        f"coscc/screens.py changed after the screenshots of review round 1 were taken at {taken}"
        in closed(screens(taken=taken), moved)
    )


def test_valid_screens_on_a_ui_unit_open_ship_pinned_to_the_head():
    taken = "e" * 40
    probe = ui_probe(
        ["coscc/screens.py"], {f"diff --name-only {taken}..{SHA}": ok("coscc/runner.py\n")}
    )
    g = check_gate(passed_with(screens(taken=taken)), "ship", probe)
    assert g == {"ok": True, "need": [], "head": SHA}


def test_neither_origin_main_nor_main_readable_closes_ship_and_no_round_is_offered():
    fail = {"code": 128, "out": "", "err": "fatal: bad revision"}
    probe = ui_probe(
        [], {f"diff --name-only origin/main...{SHA}": fail, f"diff --name-only main...{SHA}": fail}
    )
    g = check_gate(passed_with(), "ship", probe)
    assert g["ok"] is False
    assert re.search(
        r"cannot tell whether 0001_x changes a screen: .*the gate does not fetch", g["need"][0]
    )
    assert next_step(passed_with(), probe)["stage"] == ""
    local = ui_probe(
        [],
        {
            f"diff --name-only origin/main...{SHA}": fail,
            f"diff --name-only main...{SHA}": ok("coscc/screens.py\n"),
        },
    )
    assert "no ### Screens" in check_gate(passed_with(), "ship", local)["need"][0]


# --- a review that ran out of turns -------------------------------------------------------


def incomplete_round(n):
    return round_(n, "incomplete", [fr("F1", "open", "x")])


def review_of_rounds(status, rounds):
    return branched({**CHAIN, "review.md": review_art(status, rounds)})


def cr(n):
    return round_(n, "changes-requested", [fr("F1", "open", "x")])


def test_draft_over_an_incomplete_round_offers_review_on_green_and_impl_on_red():
    u = review_of_rounds("draft", [cr(1), incomplete_round(2)])
    green = next_step(u, green_probe())
    assert green["stage"] == "review"
    assert re.search(
        r"review round 2 is incomplete — write-review again; CI is green: write-review",
        green["action"],
    )
    assert next_step(u, green_probe([{"name": "tests", "bucket": "fail"}]))["stage"] == "impl"
    assert next_step(u, green_probe([{"name": "tests", "bucket": "pending"}]))["stage"] == ""
    bare = next_step(u)
    assert bare["stage"] == ""
    assert "pass --repo" in bare["action"]
    assert "finish and accept" not in bare["action"]
    assert check_gate(u, "review", green_probe())["ok"] is True
    ship = check_gate(u, "ship", green_probe())
    assert ship["ok"] is False
    assert 'review.md is "draft", not accepted' in "\n".join(ship["need"])
    accepted = review_of_rounds("accepted", [incomplete_round(1)])
    need = "\n".join(check_gate(accepted, "ship", green_probe())["need"])
    assert 'verdict "incomplete", not pass' in need


UNREAD = round_(1, None, [fr("F1", "open", "x")], reviewed="somewhere")


def used(rounds, limit=3, status="changes-requested"):
    return next_step(review_of_rounds(status, rounds), None, limit)["action"]


def test_an_incomplete_round_never_changes_how_many_rounds_are_used():
    assert "(2 of 3 rounds used)" in used([cr(1), cr(2)])
    assert "(2 of 3 rounds used)" in used([cr(1), incomplete_round(2), cr(3)])
    assert "needs a person" in used([cr(1), cr(2)], 2)
    assert "needs a person" in used([cr(1), incomplete_round(2), cr(3)], 2)
    two = review_of_rounds("draft", [cr(1), incomplete_round(2)])
    assert next_step(two, green_probe(), 2)["stage"] == "review"
    spent = review_of_rounds("draft", [cr(1), cr(2), incomplete_round(3)])
    assert "needs a person" in next_step(spent, green_probe(), 2)["action"]
    assert check_gate(spent, "review", green_probe(), 2)["ok"] is False
    assert "needs a person" in used([UNREAD], 1)
    assert "needs a person" in used([UNREAD, incomplete_round(2)], 1, "draft")
    lone = review_of_rounds("draft", [incomplete_round(1)])
    assert next_step(lone, green_probe(), 1)["stage"] == "review"


def test_a_draft_whose_last_round_is_not_incomplete_is_still_finished_by_hand():
    for verdict in ["changes-requested", "pass"]:
        u = review_of_rounds("draft", [round_(1, verdict, [fr("F1", "open", "x")])])
        n = next_step(u, green_probe())
        assert n["stage"] == ""
        assert "finish and accept review.md" in n["action"]
    assert "finish and accept review.md" in next_step(review_of_rounds("draft", []))["action"]
    other = review_of_rounds("changes-requested", [cr(1), incomplete_round(2)])
    assert "is incomplete" not in next_step(other, green_probe())["action"]


# --- a round that drops a finding an earlier one raised -----------------------------------

TWO = [
    fr("F1", "open", "x", path="a.py", lines="1"),
    fr("F2", "open", "y", path="b.py", lines="2"),
]


def dropped_of(*rounds):
    return [[r["n"], r["dropped"], r["unfinished"]] for r in review_from(list(rounds))["rounds"]]


def test_a_round_that_omits_an_earlier_id_is_unfinished_only_under_changes_requested():
    assert dropped_of(round_(1, "changes-requested", TWO)) == [[1, [], False]]
    carried = round_(2, "changes-requested", [fx("F1", "x"), TWO[1]])
    assert dropped_of(round_(1, "changes-requested", TWO), carried)[-1] == [2, [], False]
    assert dropped_of(round_(1, "changes-requested", TWO), cr(2))[-1] == [2, ["F2"], True]
    three = [*TWO, fr("F3", "open", "z", "low", path="c.py")]
    got = dropped_of(
        round_(1, "changes-requested", TWO), round_(2, "changes-requested", three), cr(3)
    )
    assert got[-1] == [3, ["F2", "F3"], True]
    got = dropped_of(incomplete_round(1), round_(2, "changes-requested", [fr("F2", "open", "y")]))
    assert got[-1] == [2, ["F1"], True]
    got = dropped_of(UNREAD, round_(2, "changes-requested", [fr("F2", "open", "y")]))
    assert got[-1] == [2, ["F1"], True]
    for verdict in ["pass", "needs-person"]:
        got = dropped_of(round_(1, "changes-requested", TWO), round_(2, verdict, [fx("F1", "x")]))
        assert got[-1] == [2, ["F2"], False]


def test_a_changes_requested_round_that_carries_every_earlier_id_counts_exactly_one_more():
    first = round_(1, "changes-requested", TWO)
    assert "(1 of 3 rounds used)" in used([first])
    assert "(2 of 3 rounds used)" in used([first, round_(2, "changes-requested", TWO)])
    assert "(2 of 3 rounds used)" in used([first, round_(2, "changes-requested", TWO), cr(3)])
    assert "(2 of 3 rounds used)" in used([first, cr(2), round_(3, "changes-requested", TWO)])


def test_an_unfinished_last_round_sends_next_to_review_with_its_ids_and_leaves_the_gate_open():
    u = review_of_rounds("changes-requested", [round_(1, "changes-requested", TWO), cr(2)])
    green = next_step(u, green_probe())
    assert green["stage"] == "review"
    assert green["action"] == (
        "review round 2 left out findings an earlier round raised — write-review again "
        "(1 of 3 rounds used); CI is green: write-review"
    )
    assert green["dropped"] == ["F2"]
    assert next_action(u)["dropped"] == ["F2"]
    red = next_step(u, green_probe([{"name": "tests", "bucket": "fail"}]))
    assert [red["stage"], red["dropped"]] == ["impl", ["F2"]]
    assert next_step(u, green_probe([{"name": "tests", "bucket": "pending"}]))["stage"] == ""
    bare = next_step(u)
    assert bare["stage"] == ""
    assert re.search(r"left out findings an earlier round raised.*pass --repo", bare["action"])
    assert bare["dropped"] == ["F2"]
    assert check_gate(u, "review", green_probe())["ok"] is True
    once = review_of_rounds("changes-requested", [round_(1, "changes-requested", TWO)])
    assert "dropped" not in next_step(once)
    draft = review_of_rounds("draft", [cr(1), incomplete_round(2)])
    assert "dropped" not in next_step(draft, green_probe())


def test_an_unfinished_round_at_the_limit_still_stops_at_needs_a_person():
    first = round_(1, "changes-requested", TWO)
    u = review_of_rounds("changes-requested", [first, cr(2)])
    assert next_step(u, green_probe(), 1)["action"].startswith(
        "needs a person — review used 1 of 1 rounds"
    )
    assert check_gate(u, "review", green_probe(), 1)["ok"] is False
    spent = review_of_rounds(
        "changes-requested", [first, round_(2, "changes-requested", TWO), cr(3)]
    )
    assert next_step(spent, green_probe(), 2)["action"].startswith(
        "needs a person — review used 2 of 2 rounds"
    )
    assert next_step(spent, green_probe(), 3)["stage"] == "review"


def test_an_incomplete_first_round_then_an_unfinished_second_uses_no_round():
    rounds = [incomplete_round(1), round_(2, "changes-requested", [fr("F2", "open", "y")])]
    u = review_of_rounds("changes-requested", rounds)
    n = next_step(u, green_probe(), 1)
    assert n["stage"] == "review"
    assert re.search(r"left out findings .*\(0 of 1 rounds used\)", n["action"])
    assert n["dropped"] == ["F1"]
    floor = review_of_rounds(
        "changes-requested", [UNREAD, round_(2, "changes-requested", [fr("F2", "open", "y")])]
    )
    assert next_step(floor, green_probe(), 1)["action"].startswith(
        "needs a person — review used 1 of 1 rounds"
    )


class Tree17:
    """A unit whose review holds `rounds`, asked through the command the way the board asks it."""

    name = "0017_units-share-one-working-tree"

    def __init__(self, tmp_path: Path, rounds: list[dict]):
        self.root = tmp_path / f"t{len(list(tmp_path.iterdir()))}"
        self.dir = self.root / ".cos" / self.name
        self.dir.mkdir(parents=True)
        self.entry = known(
            {
                **dict.fromkeys(
                    ("intent.md", "spec.md", "plan.md", "impl.md", "pr.md"), "accepted"
                ),
                "review.md": "changes-requested",
            },
            questions={"intent.md": ["Một?"]},
            records={"pr.md": pr_row(7), "review.md": {"rounds": rounds}},
        )
        for f in ("intent.md", "spec.md", "plan.md", "impl.md", "pr.md", "review.md"):
            (self.dir / f).write_text(f"# {f}\n")

    def run(self, limit, *args):
        argv = [*args, "--root", str(self.root), "--state", "-"]
        state = json.dumps(state_of_root(self.root, {self.name: self.entry}))
        return python(argv, stdin=state, environ=env(COS_REVIEW_ROUNDS=str(limit)))

    def used(self, limit):
        out = self.run(limit, "next", self.name)
        assert out.code == 0, out.err
        m = re.search(r"(\d+) of (\d+) rounds", json.loads(out.out)["action"])
        assert m, out.out
        assert int(m[2]) == limit
        return int(m[1])


def test_a_round_that_drops_findings_adds_no_round_to_the_count(tmp_path):
    ids = ["F1", "F2", "F3", "F4", "F5"]
    first = round_(1, "changes-requested", [fr(i, "open", "x") for i in ids])
    thin = round_(2, "changes-requested", [fr("F1", "open", "x")])
    both = Tree17(tmp_path, [first, thin])
    one = Tree17(tmp_path, [first])
    again = round_(2, "changes-requested", [fr(i, "open", "x") for i in ids])
    full = Tree17(tmp_path, [first, again])
    for limit in (1, 2, 3):
        assert both.used(limit) == one.used(limit), limit
        assert full.used(limit) == one.used(limit) + 1, limit
    status = json.loads(both.run(3, "status", "--json").out)
    rounds = status["units"][0]["artifacts"]["review.md"]["review"]["rounds"]
    assert [[r["n"], r["dropped"], r["unfinished"]] for r in rounds] == [
        [1, [], False],
        [2, ["F2", "F3", "F4", "F5"], True],
    ]
    assert status["units"][0]["next"]["dropped"] == ["F2", "F3", "F4", "F5"]
    nxt = json.loads(both.run(3, "next", both.name).out)
    assert nxt["dropped"] == ["F2", "F3", "F4", "F5"]
    assert not re.search(r"F\d", nxt["action"])
    assert "dropped" not in json.loads(one.run(3, "next", one.name).out)
