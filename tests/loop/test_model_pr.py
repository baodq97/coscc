"""The loop's reading of `pr.md` and `review.md`, and the ship gate over them, held to fixed values.

Ported from the `test(` calls of the loop's former JavaScript suite (`tests/loop/ported.txt` maps
each one here), and a sequel of `tests/loop/test_model.py`, whose helpers it reuses: the title and
body of a pull request, the scope it states, the screens a unit changes, a review that ran out of
turns and a round that drops a finding an earlier one raised.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

from coscc.loop import REVIEW_ROUNDS
from coscc.loop.model import (
    non_blocking,
    parse_review,
    pr_scope,
    pr_text,
    title_problem,
)
from coscc.loop.probe import UI_STANDARD, glob_match, make_probe, parse_standard, ui_files
from coscc.loop.repo_rules import screens_problems
from coscc.loop.rules import check_gate, next_action, next_step
from tests.loop.conftest import REPO, env, python
from tests.loop.test_model import (
    CHAIN,
    FIX,
    SHA,
    asked,
    branched,
    cli,
    green_probe,
    impl_text,
    low,
    ok,
    rated,
    review_art,
    round3,
    round_,
    state_of_root,
    tree_after_round_two,
    REVIEW_HEAD,
    ROUND1,
    ROUND2,
)

# --- the open lows and the findings not fixed -------------------------------------------


def ship_gate(text, **kw):
    """The ship gate of a unit whose accepted `review.md` holds `text`, on a green probe."""
    u = branched({**CHAIN, "review.md": review_art("accepted", text)})
    return check_gate(u, "ship", green_probe(), kw.get("limit", REVIEW_ROUNDS))


LOWERED = (
    "F1 is low in review round 2, but review round 1 rated it high — lowering a severity is not "
    "a fix: fix it on the branch, or keep it open"
)


def test_a_pass_round_with_one_open_low_among_fixed_findings_opens_ship():
    text = round_(
        1,
        "pass",
        [
            f"- F1 [fixed {FIX}] a.py:3 — low — x",
            f"- F2 [fixed {FIX}] a.py:3 — medium — x",
            f"- F3 [fixed {FIX}] a.py:3 — low — x",
            "- F4 [open] coscc/screens.py:646 — low — Còn hai docstring thuộc cùng loại với F3 mà "
            "`9243b7b` chưa sửa, vì vòng 3 không nêu tên chúng.",
        ],
    )
    u = branched({**CHAIN, "review.md": review_art("accepted", text)})
    g = check_gate(u, "ship", green_probe())
    assert g["ok"] is True, g["need"]
    assert g["need"] == []
    assert [f["id"] for f in non_blocking(u)] == ["F4"]


def test_a_lowered_finding_is_named_once_not_among_the_findings_not_fixed():
    high = round_(1, "changes-requested", [rated("F1", "high")])
    a = ship_gate(f"{high}\n{round_(2, 'pass', [low('F1')])}")
    assert a["ok"] is False
    assert LOWERED in a["need"], a["need"]
    assert not any("still has findings not fixed" in line for line in a["need"]), a["need"]
    first = round_(1, "changes-requested", [rated("F1", "high"), rated("F2", "medium")])
    second = round_(2, "pass", [low("F1"), rated("F2", "medium")])
    b = ship_gate(f"{first}\n{second}")
    assert b["ok"] is False
    assert LOWERED in b["need"], b["need"]
    assert [line for line in b["need"] if "still has findings not fixed" in line] == [
        "review round 2 still has findings not fixed: F2 [open]"
    ]


def test_three_counted_rounds_of_prose_severities_then_a_pass_of_lows_opens_ship():
    prose = ["- F1 [open] a.py:3 — Mức thấp — biên regex", "- F2 [open] a.py:9 — Mức thấp — y"]
    counted = [round_(n, "changes-requested", prose) for n in (1, 2, 3)]
    text = "\n".join(counted) + "\n" + round_(4, "pass", [low("F1"), low("F2")])
    g = ship_gate(text, limit=REVIEW_ROUNDS)
    assert g["ok"] is True, g["need"]


def test_a_needs_person_round_beside_an_open_low_is_still_a_wait_for_a_person(tmp_path):
    review = f"{REVIEW_HEAD}{ROUND1}\n{ROUND2}\n{round3('needs-person', [low('F4')])}"
    u = tree_after_round_two(tmp_path, review)
    n = next_action(u)
    assert n["waiting"] == ["F2", "F3"]
    assert re.search(
        r"^needs a person — F2: the grant holds no budget for --paid; F3: the grant holds no gh",
        n["action"],
    )
    assert [p["id"] for p in u["personFindings"]] == ["F2", "F3"]
    assert [f["id"] for f in u["nonBlocking"]] == ["F4"]


def test_every_blocking_finding_claimed_and_one_low_unclaimed_is_review_not_impl(tmp_path):
    claimed = [f"- F1 [fixed {FIX}] a", "- F2 [open] b", "- F3 [open] c", low("F4")]
    r2 = round_(2, "changes-requested", claimed)
    u = tree_after_round_two(tmp_path, f"{REVIEW_HEAD}{ROUND1}\n{r2}")
    assert next_step(u, green_probe())["stage"] == "review"
    only_low = round_(1, "changes-requested", [low("F1")])
    impl = impl_text("## Needs a person\n\n- F1: the grant holds no gh\n")
    u = tree_after_round_two(tmp_path, f"{REVIEW_HEAD}{only_low}", impl)
    assert next_step(u, green_probe())["stage"] == "impl"


def test_a_pass_that_leaves_lows_open_costs_no_round():
    first = round_(1, "pass", [low("F1")])
    cr = [round_(n, "changes-requested", [low("F1"), "- F2 [open] b.py:1 y"]) for n in (2, 3, 4)]
    spent = next_action(asked("\n".join([first, *cr])))["action"]
    assert "needs a person — review used 3 of 3 rounds" in spent
    assert "(2 of 3 rounds used)" in next_action(asked("\n".join([first, *cr[:2]])))["action"]


# --- the title and body pr.md puts on its pull request ------------------------------------

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
PR_BODY = (
    "## Where\n\nhttps://github.com/o/r/pull/7, branch fix/x, checks pending.\n\n"
    "## Scope of the diff\n\n## What a reviewer should look at first\n"
)
TITLE = "the pr body is taken from pr.md"


def test_the_template_gives_a_body_without_the_header_or_the_title_line():
    t = pr_text(PR_MD)
    assert t["title"] == TITLE
    assert t["url"] == "https://github.com/o/r/pull/7"
    assert "Status:" not in t["body"]
    assert "PR: https://" not in t["body"]
    assert "# PR:" not in t["body"]
    assert t["body"].startswith("## Where\n")
    assert t["body"] == PR_BODY


def test_a_later_status_in_the_body_stays_in_the_body():
    text = f"{PR_MD}Status: pending is what gh said.\n"
    assert pr_text(text)["body"].endswith("Status: pending is what gh said.\n")


def test_no_title_line_or_an_empty_one_is_a_null_title():
    without = "\n".join(PR_MD.split("\n")[1:])
    assert pr_text(without)["title"] is None
    assert pr_text(without)["body"].startswith("## Where")
    empty = pr_text(PR_MD.replace(f"# PR: {TITLE}", "# PR:   "))
    assert empty["title"] is None
    assert empty["body"] == PR_BODY


def test_the_same_input_gives_the_same_output():
    assert pr_text(PR_MD) == pr_text(PR_MD)


def test_a_file_with_crlf_keeps_crlf_on_the_lines_it_keeps():
    t = pr_text(PR_MD.replace("\n", "\r\n"))
    assert t["title"] == TITLE
    assert t["body"].startswith("## Where\r\n")
    assert t["body"] == PR_BODY.replace("\n", "\r\n")


def test_a_header_on_the_title_line_itself_drops_only_that_one_line():
    assert pr_text("# PR: x Status: accepted.\n\nbody\n")["body"] == "body\n"


def pr_tree(tmp_path: Path, files: dict[str, str | None]) -> Path:
    """A `--root` with one unit per key, each with an `intent.md` and `pr.md` when text is given."""
    root = tmp_path / f"pr{len(list(tmp_path.iterdir()))}"
    for name, text in files.items():
        d = root / ".cos" / name
        d.mkdir(parents=True)
        (d / "intent.md").write_text("# Intent: x\nAuthor: a. Type: fix. Status: accepted.\n")
        if text is not None:
            (d / "pr.md").write_text(text)
    return root


def test_pr_text_prints_what_the_reader_reads_with_the_unit_and_its_status(tmp_path):
    root = pr_tree(tmp_path, {"0001_a": PR_MD})
    out = cli("--root", str(root), "pr-text", "0001_a")
    assert out.code == 0, out.err
    assert json.loads(out.out) == {
        "unit": "0001_a",
        "title": TITLE,
        "body": PR_BODY,
        "url": "https://github.com/o/r/pull/7",
        "scope": None,
        "status": "accepted",
        "titleProblem": f'the title "{TITLE}" is not <type>(<NNNN>): <text>',
    }


def test_pr_text_of_no_unit_or_no_pr_md_is_1_and_of_a_missing_or_malformed_name_is_2(tmp_path):
    root = str(pr_tree(tmp_path, {"0001_a": None}))
    none = cli("--root", root, "pr-text", "0002_b")
    assert none.code == 1
    assert "No such work unit" in none.err
    nofile = cli("--root", root, "pr-text", "0001_a")
    assert nofile.code == 1
    assert "0001_a has no pr.md" in nofile.err
    assert cli("--root", root, "pr-text").code == 2
    assert cli("--root", root, "pr-text", "../0001_a").code == 2


def test_repo_and_reserve_from_are_refused_by_pr_text(tmp_path):
    root = str(pr_tree(tmp_path, {"0001_a": PR_MD}))
    repo = cli("--root", root, "pr-text", "0001_a", "--repo", root)
    assert repo.code == 2
    assert "--repo applies only to" in repo.err
    assert cli("--root", root, "--reserve-from", root, "pr-text", "0001_a").code == 2


def listing(root: Path) -> list[tuple[str, int]]:
    """Every path under `<root>/.cos`, sorted, with its modification time."""
    base = root / ".cos"
    return sorted((str(p.relative_to(base)), p.stat().st_mtime_ns) for p in base.rglob("*"))


def test_pr_text_writes_nothing_and_leaves_status_as_it_was(tmp_path):
    root = pr_tree(tmp_path, {"0001_a": PR_MD, "0002_b": None})
    before = [cli("--root", str(root), "status", "--json").out, listing(root)]
    assert cli("--root", str(root), "pr-text", "0001_a").code == 0
    assert [cli("--root", str(root), "status", "--json").out, listing(root)] == before


# --- the scope pr.md states, read beside its title and body -------------------------------

SCOPED = PR_MD.replace(
    "## Scope of the diff\n\n",
    "## Scope of the diff\n\n3 files, +120/-7\n- `a.py`\n- `docs/b c.md`\n- `.claude/x.mjs`\n\n"
    "Counted by gh pr view.\n\n",
)
SCOPED_PATHS = ["a.py", "docs/b c.md", ".claude/x.mjs"]
SCOPED_BODY = PR_BODY.replace(
    "## Scope of the diff\n\n",
    "## Scope of the diff\n\n3 files, +120/-7\n- `a.py`\n- `docs/b c.md`\n- `.claude/x.mjs`\n\n"
    "Counted by gh pr view.\n\n",
)


def test_a_scope_written_to_its_grammar_is_read_as_counts_and_paths():
    assert pr_text(SCOPED)["scope"] == {
        "files": 3,
        "additions": 120,
        "deletions": 7,
        "paths": SCOPED_PATHS,
    }
    assert pr_scope("## Scope of the diff\n1 files, +0/-0\n") == {
        "files": 1,
        "additions": 0,
        "deletions": 0,
        "paths": [],
    }


def test_no_scope_heading_is_a_null_scope():
    assert pr_scope("") is None
    assert pr_scope("# PR: x\nStatus: accepted.\n\n## Where\n\n3 files, +1/-1\n") is None
    assert pr_scope("### Scope of the diff\n\n3 files, +1/-1\n") is None
    assert pr_text(PR_MD)["scope"] is None
    assert pr_scope("x\n## Scope of the diff") is None


def test_the_old_git_diff_stat_line_is_a_null_scope():
    old = PR_MD.replace(
        "## Scope of the diff\n\n",
        "## Scope of the diff\n\n`git diff --stat main...HEAD`: **27 file, +2413 −84**.\n\n",
    )
    assert pr_text(old)["scope"] is None
    for line in [
        "1 file, +1/-1",
        "**3 files, +1/-1**",
        "3 files, +1/−1",
        "3 files, +1/-1 from main",
    ]:
        assert pr_scope(f"## Scope of the diff\n\n{line}\n") is None, line


def test_a_path_listed_twice_is_a_null_scope():
    text = "## Scope of the diff\n\n2 files, +1/-1\n- `a.py`\n- `b.py`\n- `a.py`\n"
    assert pr_scope(text) is None


def test_prose_after_the_list_is_not_a_path_and_the_body_is_kept_byte_for_byte():
    text = SCOPED.replace("Counted by gh pr view.", "Counted by gh pr view.\n- `z.py`")
    assert pr_scope(text)["paths"] == SCOPED_PATHS
    nxt = "## Scope of the diff\n\n1 files, +1/-1\n- `a.py`\n## Next\n- `b.py`\n"
    assert pr_scope(nxt)["paths"] == ["a.py"]
    assert pr_text(SCOPED)["body"] == SCOPED_BODY


def test_a_file_with_crlf_reads_the_same_scope():
    crlf = SCOPED.replace("\n", "\r\n")
    assert pr_text(crlf)["scope"] == pr_text(SCOPED)["scope"]
    assert pr_text(crlf)["body"] == SCOPED_BODY.replace("\n", "\r\n")


def test_pr_text_prints_scope_beside_title_body_url_and_status(tmp_path):
    root = pr_tree(tmp_path, {"0001_a": SCOPED})
    out = cli("--root", str(root), "pr-text", "0001_a")
    assert out.code == 0, out.err
    got = json.loads(out.out)
    assert got == {
        "unit": "0001_a",
        "title": TITLE,
        "body": SCOPED_BODY,
        "url": "https://github.com/o/r/pull/7",
        "scope": {"files": 3, "additions": 120, "deletions": 7, "paths": SCOPED_PATHS},
        "status": "accepted",
        "titleProblem": f'the title "{TITLE}" is not <type>(<NNNN>): <text>',
    }
    assert sorted(got) == ["body", "scope", "status", "title", "titleProblem", "unit", "url"]
    assert title_problem("fix(0001): x", "fix", "0001") is None


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


def test_no_other_tracked_file_holds_a_copy_of_the_list():
    globs = parse_standard((REPO / UI_STANDARD).read_text())
    copies = []
    for p in tracked():
        if p == UI_STANDARD or p.startswith(".cos/"):
            continue
        try:
            text = (REPO / p).read_text()
        except OSError, UnicodeDecodeError:
            continue
        if all(g in text for g in globs):
            copies.append(p)
    assert copies == []


SHOT = "- .screens/board-1440x900.png — 1440×900 — /board — no violation"


def screens(
    taken=SHA, by="an agent session (write-review)", standard=UI_STANDARD, shots=None, head=None
):
    shots = [SHOT] if shots is None else shots
    line = head or (
        f"Taken at: {taken}. Standard: `{standard}`. Looked at by: {by}, from screenshots."
    )
    return f"\n### Screens\n\n{line}\n\n" + "\n".join(shots) + "\n"


def test_parse_review_reads_screens_and_a_round_without_one_reads_none():
    shots = [
        SHOT,
        "- `.screens/board-390x844.png` — 390x844 — `/unit?ws=proj&id=0002_open-question"
        "&tab=questions` — S3: a /tmp path",
        "prose between lines",
    ]
    text = (
        f"{round_(1, 'changes-requested', ['- F1 [open] x'])}\n"
        f"{round_(2, 'pass', [f'- F1 [fixed {FIX}] x'])}{screens(shots=shots)}"
    )
    one, two = parse_review(text)["rounds"]
    assert one["screens"] is None
    assert two["screens"] == {
        "taken": SHA,
        "standard": UI_STANDARD,
        "by": "an agent session (write-review)",
        "header": f"Taken at: {SHA}. Standard: `{UI_STANDARD}`. Looked at by: an agent session "
        "(write-review), from screenshots.",
        "shots": [
            {
                "path": ".screens/board-1440x900.png",
                "size": "1440x900",
                "address": "/board",
                "result": "no violation",
            },
            {
                "path": ".screens/board-390x844.png",
                "size": "390x844",
                "address": "/unit?ws=proj&id=0002_open-question&tab=questions",
                "result": "S3: a /tmp path",
            },
        ],
    }
    assert [f["id"] for f in two["findings"]] == ["F1"]
    assert "### Screens" in two["text"]
    bad = parse_review(f"{round_(1, 'pass')}{screens(head='Looked at it.')}")["rounds"][0][
        "screens"
    ]
    assert [bad["taken"], bad["standard"], bad["by"], bad["header"]] == [
        None,
        None,
        None,
        "Looked at it.",
    ]


def test_screens_problems_names_each_thing_wrong_with_the_words():
    def problems(s):
        read = parse_review(f"{round_(1, 'pass')}{s}")["rounds"][0]["screens"]
        return "\n".join(screens_problems(read, UI_STANDARD))

    assert problems(screens()) == ""
    assert "no ### Screens" in "\n".join(screens_problems(None, UI_STANDARD))
    assert "first line of its ### Screens is not" in problems(
        screens(head="Taken at: abc. Looked.")
    )
    assert '"Looked at by: Bao", which does not say it was an agent' in problems(screens(by="Bao"))
    other = problems(screens(standard=".claude/rules/other.md"))
    assert "names the standard .claude/rules/other.md, not" in other
    assert "lists no screenshot" in problems(screens(shots=["- a.jpg — 1×1 — / — x"]))


def test_an_open_low_against_the_standard_blocks_and_one_that_is_not_still_does_not():
    findings = [
        "- F1 [open] coscc/screens.py:10 — low — S3 shows a full sha",
        low("F2"),
        "- F3 [open] a.py:3 — low — Sx is not a rule id",
    ]
    assert [f["id"] for f in non_blocking(asked(round_(1, "pass", findings)))] == ["F2", "F3"]
    g = ship_gate(round_(1, "pass", findings[:1]))
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


def passed_with(tail=""):
    text = f"{round_(1, 'pass')}{tail}"
    return branched({**CHAIN, "review.md": review_art("accepted", text)})


def test_a_unit_that_changes_no_screen_reads_exactly_as_with_no_standard():
    u = passed_with()
    before = check_gate(u, "ship", green_probe())
    for files in (["coscc/runner.py"], [".cos/0001_x/review.md"], []):
        assert check_gate(u, "ship", ui_probe(files)) == before
        assert next_step(u, ui_probe(files)) == next_step(u, green_probe())
    own = {"path": UI_STANDARD, "globs": ["**/*.py"]}
    assert check_gate(u, "ship", ui_probe([".cos/0001_x/x.py"], {}, own)) == before
    no_globs = {"path": UI_STANDARD, "globs": []}
    assert check_gate(u, "ship", ui_probe(["coscc/screens.py"], {}, no_globs)) == before


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


def test_each_condition_broken_once_closes_ship_and_says_which():
    ui = ["coscc/screens.py"]

    def closed(tail, extra=None):
        g = check_gate(passed_with(tail), "ship", ui_probe(ui, extra))
        assert g["ok"] is False
        assert next_step(passed_with(tail), ui_probe(ui, extra))["stage"] == "review"
        return "\n".join(g["need"])

    assert "does not say it was an agent" in closed(screens(by="Bao"))
    assert "names the standard STANDARD.md" in closed(screens(standard="STANDARD.md"))
    assert "lists no screenshot" in closed(screens(shots=[]))
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


def counting(probe):
    """`probe` with its `git` noting each argument string it is asked, in `calls`."""
    calls = []
    inner = probe.git

    def git(*args):
        calls.append(" ".join(args))
        return inner(*args)

    probe.git = git
    return SimpleNamespace(calls=calls, probe=probe)


def test_no_standard_asks_git_nothing_more_and_a_standard_with_no_screen_one_diff_more():
    u = passed_with()
    plain = counting(green_probe())
    check_gate(u, "ship", plain.probe)
    empty = counting(ui_probe(["coscc/screens.py"], {}, None))
    check_gate(u, "ship", empty.probe)
    assert empty.calls == plain.calls
    some = counting(ui_probe(["coscc/runner.py"]))
    check_gate(u, "ship", some.probe)
    assert some.calls == [*plain.calls, f"diff --name-only origin/main...{SHA}"]
    stuck = counting(ui_probe(["coscc/screens.py"]))
    still_open = round_(1, "pass", ["- F1 [open] x"])
    check_gate(
        branched({**CHAIN, "review.md": review_art("accepted", still_open)}), "ship", stuck.probe
    )
    assert stuck.calls == []


# --- a review that ran out of turns -------------------------------------------------------


def incomplete_round(n):
    return (
        f"## Round {n}\n\nReviewed: {SHA}. Verdict: incomplete.\n\n### Reviewed so far\n\n- a.py\n\n"
        "### Findings\n\n- F1 [open] a.py:3 — high — x\n\n### What was not reviewed\n\n- b.py\n"
    )


def review_of_rounds(status, rounds):
    text = f"# Review: x\nStatus: {status}.\n\n" + "\n".join(rounds)
    return branched({**CHAIN, "review.md": review_art(status, text)})


def cr(n):
    return round_(n, "changes-requested", ["- F1 [open] x"])


def test_an_incomplete_round_is_read_as_one_with_its_verdict():
    rounds = parse_review(f"# Review\nStatus: draft.\n\n{incomplete_round(1)}")["rounds"]
    assert [[r["n"], r["verdict"], r["reviewed"]] for r in rounds] == [[1, "incomplete", SHA]]
    assert [[f["id"], f["label"]] for f in rounds[0]["findings"]] == [["F1", "open"]]


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


UNREAD = "## Round 1\n\nReviewed: somewhere. Verdict: maybe.\n\n### Findings\n\n- F1 [open] x\n"


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
        u = review_of_rounds("draft", [round_(1, verdict, ["- F1 [open] x"])])
        n = next_step(u, green_probe())
        assert n["stage"] == ""
        assert "finish and accept review.md" in n["action"]
    assert "finish and accept review.md" in next_step(review_of_rounds("draft", []))["action"]
    other = review_of_rounds("changes-requested", [cr(1), incomplete_round(2)])
    assert "is incomplete" not in next_step(other, green_probe())["action"]


# --- a round that drops a finding an earlier one raised -----------------------------------

FIXTURE = (Path(__file__).parent / "testdata" / "0017-review-rounds-1-2.md").read_bytes().decode()
ROUND1_FIXTURE = FIXTURE[: FIXTURE.index("\n## Round 2\n") + 1]
TWO = ["- F1 [open] a.py:1 — high — x", "- F2 [open] b.py:2 — high — y"]


def under_changes(text):
    return text.replace("Status: accepted.", "Status: changes-requested.")


def dropped_of(*rounds):
    text = "# Review: x\nStatus: changes-requested.\n\n" + "\n".join(rounds)
    return [[r["n"], r["dropped"], r["unfinished"]] for r in parse_review(text)["rounds"]]


def test_a_round_that_omits_an_earlier_id_is_unfinished_only_under_changes_requested():
    assert dropped_of(round_(1, "changes-requested", TWO)) == [[1, [], False]]
    carried = round_(2, "changes-requested", [f"- F1 [fixed {FIX}] a.py:1 — high — x", TWO[1]])
    assert dropped_of(round_(1, "changes-requested", TWO), carried)[-1] == [2, [], False]
    assert dropped_of(round_(1, "changes-requested", TWO), cr(2))[-1] == [2, ["F2"], True]
    three = [*TWO, "- F3 [open] c.py:3 — low — z"]
    got = dropped_of(
        round_(1, "changes-requested", TWO), round_(2, "changes-requested", three), cr(3)
    )
    assert got[-1] == [3, ["F2", "F3"], True]
    got = dropped_of(incomplete_round(1), round_(2, "changes-requested", ["- F2 [open] y"]))
    assert got[-1] == [2, ["F1"], True]
    got = dropped_of(UNREAD, round_(2, "changes-requested", ["- F2 [open] y"]))
    assert got[-1] == [2, ["F1"], True]
    for verdict in ["pass", "needs-person"]:
        got = dropped_of(
            round_(1, "changes-requested", TWO), round_(2, verdict, [f"- F1 [fixed {FIX}] x"])
        )
        assert got[-1] == [2, ["F2"], False]
    prose = (
        f"## Round 2\n\nReviewed: {SHA}. Verdict: changes-requested.\n\nF2 is still open.\n\n"
        "### Findings\n\n- F1 [open] x\n\n### What was not reviewed\n\n- F2 [open] y\n"
    )
    assert dropped_of(round_(1, "changes-requested", TWO), prose)[-1] == [2, ["F2"], True]
    rounds = parse_review(FIXTURE)["rounds"]
    assert [[r["n"], r["verdict"], r["dropped"], r["unfinished"]] for r in rounds] == [
        [1, "changes-requested", [], False],
        [2, "changes-requested", ["F2", "F3", "F4", "F5"], True],
    ]


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
    rounds = [incomplete_round(1), round_(2, "changes-requested", ["- F2 [open] y"])]
    u = review_of_rounds("changes-requested", rounds)
    n = next_step(u, green_probe(), 1)
    assert n["stage"] == "review"
    assert re.search(r"left out findings .*\(0 of 1 rounds used\)", n["action"])
    assert n["dropped"] == ["F1"]
    floor = review_of_rounds(
        "changes-requested", [UNREAD, round_(2, "changes-requested", ["- F2 [open] y"])]
    )
    assert next_step(floor, green_probe(), 1)["action"].startswith(
        "needs a person — review used 1 of 1 rounds"
    )


class Tree17:
    """A unit whose `review.md` is `text`, asked through the command the way the board asks it."""

    name = "0017_units-share-one-working-tree"

    def __init__(self, tmp_path: Path, text: str):
        self.root = tmp_path / f"t{len(list(tmp_path.iterdir()))}"
        self.dir = self.root / ".cos" / self.name
        self.dir.mkdir(parents=True)
        files = {
            "intent.md": "# Intent: x\nType: feat. Status: accepted.\n\n## Open questions\n\n"
            "1. Một?\n",
            "spec.md": "# Spec: x\nIntent: intent.md. Status: accepted.\n\nR1.\n",
            "plan.md": "# Plan: x\nStatus: accepted.\n\n1. build it\n",
            "impl.md": "# Impl: x\nStatus: accepted.\n\nbuilt\n",
            "pr.md": "# PR: feat(0003): x\nPR: https://github.com/o/r/pull/7. Status: accepted.\n\n"
            "body\n",
            "review.md": text,
        }
        for f, body in files.items():
            (self.dir / f).write_text(body)

    def run(self, limit, *args):
        argv = [*args, "--root", str(self.root), "--state", "-"]
        state = json.dumps(state_of_root(self.root))
        return python(argv, stdin=state, environ=env(COS_REVIEW_ROUNDS=str(limit)))

    def used(self, limit):
        out = self.run(limit, "next", self.name)
        assert out.code == 0, out.err
        m = re.search(r"(\d+) of (\d+) rounds", json.loads(out.out)["action"])
        assert m, out.out
        assert int(m[2]) == limit
        return int(m[1])


def test_the_dropped_round_of_the_old_review_adds_no_round_to_the_count(tmp_path):
    both = Tree17(tmp_path, under_changes(FIXTURE))
    one = Tree17(tmp_path, under_changes(ROUND1_FIXTURE))
    ids = [f["id"] for f in parse_review(ROUND1_FIXTURE)["rounds"][0]["findings"]]
    assert ids == ["F1", "F2", "F3", "F4", "F5"]
    again = round_(2, "changes-requested", [f"- {i} [open] x" for i in ids])
    full = Tree17(tmp_path, under_changes(ROUND1_FIXTURE) + "\n" + again)
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


def test_reading_the_old_rounds_leaves_review_md_byte_for_byte(tmp_path):
    t = Tree17(tmp_path, under_changes(FIXTURE))
    file = t.dir / "review.md"
    before = file.read_bytes()
    for args in (["status", "--json"], ["next", t.name], ["gate", t.name, "review"]):
        out = t.run(3, *args)
        assert out.code != 2, out.err
    assert file.read_bytes() == before


def test_the_ship_gate_names_the_same_dropped_ids_in_the_same_words():
    first = round_(1, "changes-requested", [*TWO, "- F3 [open] c"])
    text = (
        f"# Review: x\nStatus: accepted.\n\n{first}\n{round_(2, 'pass', [f'- F1 [fixed {FIX}] x'])}"
    )
    last = parse_review(text)["rounds"][-1]
    assert last["dropped"] == ["F2", "F3"]
    need = ship_gate(text)["need"]
    assert (
        "review round 2 drops findings an earlier round raised: F2, F3 — carry each one "
        "forward, fixed or open"
    ) in need, need
