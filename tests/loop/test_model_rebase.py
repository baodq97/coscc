"""The ship gate and `next` across a rebase, a merge made elsewhere, a round more and a red check.

A probe is a namespace answering `git` and `gh` as the real ones would; the cases
on a real repository run `git` itself and fake only `gh`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from coscc.loop.model import (
    branch_problem,
    parse_more_rounds,
    parse_review,
    parse_ship,
    review_limit,
)
from coscc.loop.probe import UI_STANDARD, Probe
from coscc.loop.repo_rules import screens_problems
from coscc.loop.rules import more_rounds, open_lines
from tests.loop.conftest import git
from tests.loop.test_model import (
    ROUND1,
    ROUND2,
    moved_to,
    round3,
    check_gate,
    next_action,
    next_step,
    CHAIN,
    HEAD2,
    NOT_ANCESTOR,
    PR,
    REVIEW_HEAD,
    SHA,
    art,
    asked,
    branched,
    green_probe,
    impl_text,
    ok,
    answer,
    claim_record,
    known,
    read,
    rated,
    review_art,
    round_,
)

NO = NOT_ANCESTOR
REB = "d" * 40
TRUNK = "refs/remotes/origin/main"

# --- the glue: ship.md as the reader attaches it, probes, trees --------------------------------

SHIP_OLD = "# Ship: x\nReview: review.md. Author: A. Status: draft.\n\n## What went out\n\nNothing merged.\n"


def f_block(id_, text="ran it"):
    return f"\n### {id_}\nAnswered by: Bao. Date: 2026-09-24. Via: product.\n\n{text}\n"


def ship_draft(n, refused="the head branch is not up to date with the base branch"):
    said = "" if refused is None else f"Refused: {refused}\n"
    return (
        f"# Ship: x\nReview: review.md. Round: {n}. Author: A. Status: draft.\n\n"
        f"## What went out\n\nNothing merged.\n{said}\n## What is still open\n\nRefused: not this one\n"
    )


def ship_art(text):
    ship = parse_ship(text)
    has = ship["round"] is not None or ship["refused"] is not None
    return {**art("draft"), **({"ship": ship} if has else {})}


def passed_once():
    return branched({**CHAIN, "review.md": review_art("accepted", round_(1, "pass"))})


def with_attrs(probe, **attrs):
    """A copy of `probe` with `attrs` set: the answers it gives, or the calls it also makes."""
    return SimpleNamespace(**{**vars(probe), **attrs})


def recorded(probe, calls):
    """`probe` with every call of its `git` and `gh` appended to `calls` as a command line."""
    git_, gh = probe.git, probe.gh

    def say_git(*a):
        calls.append(" ".join(a))
        return git_(*a)

    def say_gh(*a):
        calls.append(" ".join(["gh", *a]))
        return gh(*a)

    return with_attrs(probe, git=say_git, gh=say_gh)


def no_trunk(probe):
    """`probe` where `git rev-parse --verify --quiet <ref>/main` finds nothing."""
    git_ = probe.git

    def git2(*a):
        if a[0] == "rev-parse" and len(a) > 3 and a[3].endswith("/main"):
            return NO
        return git_(*a)

    return with_attrs(probe, git=git2)


def titled(view):
    return view if "title" in view else {**view, "title": "feat(0001): x"}


# --- a clean rebase does not void a passing review ---------------------------------------------


def hunk(at, context="line 28", blob="1111111..2222222"):
    return (
        f"diff --git a/a.txt b/a.txt\nindex {blob} 100644\n--- a/a.txt\n+++ b/a.txt\n"
        f"@@ -{at},7 +{at},7 @@ def f():\n line 27\n {context}\n line 29\n-line 30\n"
        "+line 30, changed\n line 31\n line 32\n line 33\n"
    )


PATCH_R = hunk(27)


def rebased_probe(patch=None, base=None, checks=None, calls=None, head=REB):
    """A pass on `SHA`, then a rebase to `head`: the reviewed commit is on none of the three refs.
    `patch(commit)` is what `git diff` prints for the unit at that commit, `base(commit)` what
    `git merge-base` prints; every call lands in `calls`."""
    patch = patch or (lambda c: PATCH_R if c == SHA else hunk(28, "line 28", "3333333..4444444"))
    base = base or (lambda c: ok(f"{'f' * 40}\n"))
    checks = [{"name": "tests", "bucket": "pass"}] if checks is None else checks
    calls = [] if calls is None else calls

    def gh(*a):
        calls.append(" ".join(["gh", *a]))
        if a[1] == "view":
            return ok(json.dumps(titled({"state": "OPEN", "headRefOid": head})))
        return ok(json.dumps(checks))

    def git_(*a):
        calls.append(" ".join(a))
        if a[0] == "merge-base" and a[1] == "--is-ancestor":
            return NO if a[2] == SHA and a[3] != SHA else ok()
        if a[0] == "merge-base":
            return base(a[1])
        if a[0] == "diff" and a[1] == "--no-color":
            return ok(patch(a[-4]))
        return ok()

    return SimpleNamespace(gh=gh, git=git_)


def merge_bases(calls):
    return [c for c in calls if c.startswith("merge-base ") and "--is-ancestor" not in c]


def test_a_rewritten_ref_whose_patch_is_unchanged_opens_ship_once_ci_is_green():
    u = passed_once()
    assert check_gate(u, "ship", rebased_probe()) == {
        "ok": True,
        "need": [],
        "head": REB,
        "rebased": {"reviewed": SHA, "head": REB},
    }
    # The reviewed patch is taken once, for all three refs: four merge-bases, not six.
    calls = []
    check_gate(u, "ship", rebased_probe(calls=calls))
    assert len(merge_bases(calls)) == 4
    assert len([c for c in calls if c.startswith("gh pr checks")]) == 1
    n = next_step(u, rebased_probe())
    assert n["stage"] == "ship"
    assert n["action"].endswith(f"--match-head-commit {REB}")


def test_a_branch_ref_rewritten_clean_while_the_pull_request_head_is_the_reviewed_commit():
    u = passed_once()
    calls = []
    # The gate stays as a head that did not move leaves it: open on that head, CI not asked.
    assert check_gate(u, "ship", rebased_probe(head=SHA, calls=calls)) == {
        "ok": True,
        "need": [],
        "head": SHA,
    }
    assert [c for c in calls if c.startswith("gh pr checks")] == []
    failing = rebased_probe(head=SHA, checks=[{"name": "tests", "bucket": "fail"}])
    assert next_step(u, failing)["stage"] == "ship"
    # A merge refused as not up to date is not cured by a rewrite the pull request never saw.
    refused = branched(
        {
            **CHAIN,
            "review.md": review_art("accepted", round_(1, "pass")),
            "ship.md": ship_art(ship_draft(1)),
        }
    )
    assert next_step(refused, rebased_probe(head=SHA)) == {
        "blocked": True,
        "action": (
            "ship was refused: the head branch is not up to date with the base branch — "
            "finish and accept ship.md"
        ),
        "stage": "",
    }


def test_red_pending_none_or_unreadable_ci_on_a_clean_rebase_closes_ship_with_the_ci_reason():
    u = passed_once()
    unreadable = rebased_probe()
    gh = unreadable.gh
    unreadable.gh = lambda *a: (
        {"code": 1, "out": "", "err": "HTTP 401"} if a[1] == "checks" else gh(*a)
    )
    for probe, said, stage in [
        (
            rebased_probe(checks=[{"name": "tests", "bucket": "fail"}]),
            r"^CI is red on #7: tests — back to impl",
            "impl",
        ),
        (
            rebased_probe(checks=[{"name": "tests", "bucket": "pending"}]),
            r"^CI has not finished on #7: tests",
            "",
        ),
        (rebased_probe(checks=[]), r"^#7 reports no required checks", ""),
        (unreadable, r"^cannot read the required checks of #7: HTTP 401$", ""),
    ]:
        g = check_gate(u, "ship", probe)
        assert g["ok"] is False
        assert len(g["need"]) == 1
        assert re.search(said, g["need"][0])
        assert "rewritten" not in g["need"][0]
        # Neither a round nor the screens: `next` sends it back to impl on red, and waits otherwise.
        n = next_step(u, probe)
        assert n["stage"] == stage
        assert re.search(said, n["action"])


def test_a_patch_that_differs_names_its_files_and_one_that_cannot_be_compared_quotes_git():
    u = passed_once()
    other = "diff --git a/b.txt b/b.txt\n--- a/b.txt\n+++ b/b.txt\n@@ -1 +1 @@\n-x\n+y\n"
    extra = (
        "diff --git a/c.txt b/c.txt\nnew file mode 100644\n--- /dev/null\n+++ b/c.txt\n"
        "@@ -0,0 +1 @@\n+z\n"
    )

    def patch(c):
        if c == SHA:
            return f"{PATCH_R}{other}"
        return f"{hunk(28, 'line 28, changed by main')}{other}{extra}"

    g = check_gate(u, "ship", rebased_probe(patch=patch))
    assert g["ok"] is False
    assert re.search(
        r"^the reviewed commit a{40} is not on refs/heads/feat/x — the branch was rewritten "
        r"after the pass",
        g["need"][0],
    )
    assert re.search(r" — its patch differs from the reviewed one in a\.txt, c\.txt$", g["need"][0])
    assert next_step(u, rebased_probe(patch=patch))["stage"] == "review"

    # Quoted paths are named by their path.
    def quoted(c):
        if c == SHA:
            return PATCH_R
        return f'{PATCH_R}diff --git "a/sp ace\\t.txt" "b/sp ace\\t.txt"\n+x\n'

    assert re.search(
        r"differs from the reviewed one in sp ace\\t\.txt$",
        check_gate(u, "ship", rebased_probe(patch=quoted))["need"][0],
    )
    lost = check_gate(u, "ship", no_trunk(rebased_probe()))
    assert re.search(
        r"could not be compared with the reviewed one: there is no origin/main and no main here",
        lost["need"][0],
    )
    assert next_step(u, no_trunk(rebased_probe()))["stage"] == "review"


def test_next_offers_ship_on_a_green_clean_rebase_impl_on_red_and_nothing_while_ci_runs():
    u = passed_once()
    assert next_step(u, rebased_probe())["stage"] == "ship"
    assert (
        next_step(u, rebased_probe(checks=[{"name": "tests", "bucket": "fail"}]))["stage"] == "impl"
    )
    for checks in [[{"name": "tests", "bucket": "pending"}], []]:
        n = next_step(u, rebased_probe(checks=checks))
        assert n["stage"] == ""
        assert n["stage"] != "review"


def test_a_ship_refused_as_not_up_to_date_and_then_rebased_clean_is_offered_ship_again():
    def u(refused=None):
        kwargs = {} if refused is None else {"refused": refused}
        return branched(
            {
                **CHAIN,
                "review.md": review_art("accepted", round_(1, "pass")),
                "ship.md": ship_art(ship_draft(1, **kwargs)),
            }
        )

    assert next_step(u(), rebased_probe()) == {
        "blocked": True,
        "action": f"ship — merge with --match-head-commit {REB}",
        "stage": "ship",
    }
    red = rebased_probe(checks=[{"name": "tests", "bucket": "fail"}])
    assert next_step(u(), red)["stage"] == "impl"
    pending = rebased_probe(checks=[{"name": "tests", "bucket": "pending"}])
    assert next_step(u(), pending)["stage"] == ""
    # Refused for something else: a clean rebase does not cure it.
    assert next_step(u("you do not have permission to merge"), rebased_probe()) == {
        "blocked": True,
        "action": "ship was refused: you do not have permission to merge — finish and accept ship.md",
        "stage": "",
    }
    # Not rebased at all: the refusal stands.
    action = next_step(u(), green_probe())["action"]
    assert action.startswith("ship was refused: the head branch is not up to date")


# --- the same cases on a real repository, where only `gh` is faked ----------------------------

LINE30 = "line 30, changed by the unit"


def swap(old, new):
    def go(lines):
        lines[lines.index(old)] = new

    return go


def far_from_the_hunk(lines):
    lines.insert(5, "inserted by main")


class RebaseRepo:
    """A repository where `a.txt` has 60 lines and the unit changes line 30 on `feat/x`; the
    unit's own files are under `.cos/`."""

    def __init__(self, path: Path, binary: bool = False):
        self.path = path
        self.binary = binary
        path.mkdir(parents=True)
        self.sh("init", "-q", "-b", "main")
        self.file = path / "a.txt"
        self.file.write_text("".join(f"line {i + 1}\n" for i in range(60)))
        if binary:
            (path / "b.bin").write_bytes(bytes([0, 1, 2, 0, 255]))
        self.commit("-m", "first")
        self.sh("switch", "-q", "-c", "feat/x")
        self.the_unit()
        self.R = self.commit("-m", "the unit")

    def sh(self, *args):
        return git(self.path, "-c", "commit.gpgsign=false", *args)

    def edit(self, fn):
        lines = self.file.read_text().split("\n")[:-1]
        fn(lines)
        self.file.write_text("\n".join(lines) + "\n")

    def commit(self, *flags):
        self.sh("add", "-A")
        self.sh("commit", "-q", *flags)
        return self.sh("rev-parse", "HEAD")

    def the_unit(self, binary=(0, 1, 2, 0, 254, 9)):
        """The unit's change, on whatever is checked out."""
        self.edit(swap("line 30", LINE30))
        if self.binary:
            (self.path / "b.bin").write_bytes(bytes(binary))
        (self.path / ".cos" / "0001_x").mkdir(parents=True, exist_ok=True)
        (self.path / ".cos" / "0001_x" / "review.md").write_text("round 1\n")

    def on_main(self, fn):
        """`main` moves, and `origin/main` with it, as a fetch would."""
        self.sh("switch", "-q", "main")
        self.edit(fn)
        self.commit("-m", "main moves")
        self.sh("update-ref", "refs/remotes/origin/main", "main")
        self.sh("switch", "-q", "feat/x")

    def rebase(self):
        self.sh("rebase", "-q", "main")
        return self.sh("rev-parse", "HEAD")

    def probe(self, head, checks=None):
        checks = [{"name": "tests", "bucket": "pass"}] if checks is None else checks
        real = Probe(str(self.path))

        def gh(*a):
            if a[1] == "view":
                return ok(json.dumps(titled({"state": "OPEN", "headRefOid": head})))
            return ok(json.dumps(checks))

        return SimpleNamespace(git=real.git, gh=gh, ui=real.ui, workflows=real.workflows)


def passed_at(commit):
    text = round_(1, "pass").replace(SHA, commit, 1)
    return branched({**CHAIN, "review.md": review_art("accepted", text)})


def rebase_repo(tmp_path, binary=False):
    return RebaseRepo(tmp_path / "repo", binary)


def test_main_inserts_a_line_far_from_the_hunk_a_clean_rebase_ship_opens_on_green(tmp_path):
    r = rebase_repo(tmp_path)
    r.on_main(far_from_the_hunk)
    head = r.rebase()
    assert head != r.R
    u = passed_at(r.R)
    assert check_gate(u, "ship", r.probe(head)) == {
        "ok": True,
        "need": [],
        "head": head,
        "rebased": {"reviewed": r.R, "head": head},
    }
    n = next_step(u, r.probe(head))
    assert n["stage"] == "ship"
    assert n["action"].endswith(f"--match-head-commit {head}")


def test_main_changes_a_line_of_the_context_not_clean_next_offers_review(tmp_path):
    r = rebase_repo(tmp_path)
    r.on_main(swap("line 28", "line 28, changed by main"))
    head = r.rebase()
    u = passed_at(r.R)
    g = check_gate(u, "ship", r.probe(head))
    assert g["ok"] is False
    assert re.search(r"its patch differs from the reviewed one in a\.txt$", g["need"][0])
    assert next_step(u, r.probe(head))["stage"] == "review"


def test_a_conflict_resolved_on_the_adjacent_line_is_not_clean(tmp_path):
    r = rebase_repo(tmp_path)
    r.on_main(swap("line 31", "line 31, changed by main"))
    # What resolving the conflict ends on: the new main, and the unit's line 30 applied again.
    r.sh("switch", "-q", "-C", "feat/x", "main")
    r.the_unit()
    head = r.commit("-m", "the unit, resolved")
    g = check_gate(passed_at(r.R), "ship", r.probe(head))
    assert g["ok"] is False
    assert re.search(r"its patch differs from the reviewed one in a\.txt", "\n".join(g["need"]))


def test_a_binary_file_that_comes_out_different_with_identical_text_is_not_clean(tmp_path):
    r = rebase_repo(tmp_path, binary=True)
    r.on_main(far_from_the_hunk)
    clean = r.rebase()
    assert check_gate(passed_at(r.R), "ship", r.probe(clean))["ok"] is True
    (r.path / "b.bin").write_bytes(bytes([0, 1, 2, 0, 254, 8]))
    head = r.commit("--amend", "--no-edit")
    g = check_gate(passed_at(r.R), "ship", r.probe(head))
    assert g["ok"] is False
    assert re.search(r"its patch differs from the reviewed one in b\.bin$", g["need"][0])


def test_a_head_that_differs_only_under_the_units_own_files_is_clean(tmp_path):
    r = rebase_repo(tmp_path)
    r.on_main(far_from_the_hunk)
    r.rebase()
    (r.path / ".cos" / "0001_x" / "review.md").write_text("round 1\nround 2\n")
    (r.path / ".cos" / "0001_x" / "ship.md").write_text("draft\n")
    head = r.commit("-m", "the unit records its round")
    assert check_gate(passed_at(r.R), "ship", r.probe(head))["ok"] is True
    # Another unit's files are the unit's patch like any other.
    (r.path / ".cos" / "0002_y").mkdir()
    (r.path / ".cos" / "0002_y" / "intent.md").write_text("x\n")
    other = r.commit("-m", "another unit")
    g = check_gate(passed_at(r.R), "ship", r.probe(other))
    assert re.search(r"differs from the reviewed one in \.cos/0002_y/intent\.md$", g["need"][0])


# --- a rebase alone answers no finding ---------------------------------------------------------

TWO_ASKED = "\n".join(
    round_(n, "changes-requested", [rated("F1", "low"), rated("F2", "medium")]) for n in (1, 2)
)


def test_changes_asked_main_moved_and_the_branch_rebased_on_a_real_repository_is_impl(tmp_path):
    r = rebase_repo(tmp_path)
    r.on_main(far_from_the_hunk)
    head = r.rebase()
    assert head != r.R
    u = asked(TWO_ASKED.replace(SHA, r.R))
    assert next_step(u, r.probe(head))["stage"] == "impl"
    # The review gate is as it was: `next` chooses, the gate does not close.
    assert check_gate(u, "review", r.probe(head))["ok"] is True


def test_a_rebase_with_a_fix_commit_on_top_goes_to_review_on_green_impl_on_red(tmp_path):
    r = rebase_repo(tmp_path)
    r.on_main(far_from_the_hunk)
    r.rebase()
    r.file.write_text(r.file.read_text().replace("line 40\n", "line 40, fixed by the unit\n"))
    head = r.commit("-m", "the fix")
    u = asked(TWO_ASKED.replace(SHA, r.R))
    n = next_step(u, r.probe(head))
    assert n["stage"] == "review"
    assert re.search(r"differs from the reviewed one in a\.txt", n["action"])
    assert next_step(u, r.probe(head, [{"name": "tests", "bucket": "fail"}]))["stage"] == "impl"
    assert next_step(u, r.probe(head, [{"name": "tests", "bucket": "pending"}]))["stage"] == ""


def test_a_rebase_whose_patch_differs_in_one_file_names_it_and_goes_to_review():
    def patch(c):
        return PATCH_R if c == SHA else hunk(28, "line 28, changed by main")

    n = next_step(asked(TWO_ASKED), rebased_probe(patch=patch))
    assert n["stage"] == "review"
    assert re.search(
        r"was rewritten past aaaaaaa — its patch differs from the reviewed one in a\.txt",
        n["action"],
    )


# --- a passing review the ship gate cannot read loops ------------------------------------------

SHOT = "- .screens/board-1440x900.png — 1440×900 — /board — no violation"
FULL_PAGE = "- .screens/board-1440x900.png — 1440×900 (full page) — /board — no violation"
UI = {"path": UI_STANDARD, "globs": ["coscc/screens.py"]}
STALE_AT = "7" * 40


def screens(taken=SHA, by="an agent session (write-review)", shots=(SHOT,), head=None):
    first = head or (
        f"Taken at: {taken}. Standard: `{UI_STANDARD}`. Looked at by: {by}, from screenshots."
    )
    return f"\n### Screens\n\n{first}\n\n{chr(10).join(shots)}\n"


def ui_probe(files, extra=None):
    """A probe whose repository has a standard listing one file, and whose branch diff against the
    trunk is `files`."""
    probe = green_probe(
        None, {f"diff --name-only origin/main...{SHA}": ok("\n".join(files)), **(extra or {})}
    )
    return with_attrs(probe, ui=lambda: UI)


OFF_BRANCH = screens(taken=STALE_AT)


def stuck_probe(git_=None, head=SHA):
    """A UI unit whose pull request's head is `head`, with the screenshots of a round on `SHA` or
    `REB` taken at a commit that is an ancestor of neither. `git_` overrides any other answer."""
    probe = green_probe(
        None,
        {
            f"diff --name-only origin/main...{head}": ok("coscc/screens.py"),
            f"merge-base --is-ancestor {STALE_AT} {SHA}": NO,
            f"merge-base --is-ancestor {STALE_AT} {REB}": NO,
            **(git_ or {}),
        },
        {"state": "OPEN", "headRefOid": head},
    )
    return with_attrs(probe, ui=lambda: UI)


def passes(*on):
    return "\n".join(
        f"{round_(i + 1, 'pass').replace(SHA, sha, 1)}{OFF_BRANCH}" for i, sha in enumerate(on)
    )


def passed_on(*on):
    return branched({**CHAIN, "review.md": review_art("accepted", passes(*on))})


def five_full_page_passes():
    return "\n".join(f"{round_(n, 'pass')}{screens(shots=[FULL_PAGE])}" for n in (1, 2, 3, 4, 5))


def test_five_passing_rounds_with_a_full_page_screenshot_open_ship():
    text = five_full_page_passes()
    u = branched({**CHAIN, "review.md": review_art("accepted", text)})
    probe = ui_probe(["coscc/screens.py"])
    for r in parse_review(text)["rounds"]:
        assert screens_problems(r["screens"], UI_STANDARD) == []
    g = check_gate(u, "ship", probe)
    assert not any("lists no screenshot" in n for n in g["need"])
    assert g == {"ok": True, "need": [], "head": SHA}
    assert next_step(u, probe)["stage"] == "ship"


def test_one_pass_the_ship_gate_stays_closed_on_sends_next_to_review_and_the_gate_names_why():
    u = passed_on(SHA)
    assert next_step(u, stuck_probe())["stage"] == "review"
    g = check_gate(u, "review", stuck_probe())
    assert g["ok"] is True
    assert [g["retry"]["n"], g["retry"]["reviewed"]] == [1, SHA]
    assert "not an ancestor" in "; ".join(g["retry"]["need"])
    assert re.search(
        r"^review round 1 passed on aaaaaaa and the ship gate is still closed: "
        r".*not an ancestor.* — this round is the one retry",
        open_lines("review", "0001_x", retry=g["retry"])[1],
    )


def test_the_review_gate_adds_nothing_when_the_stop_has_come_or_ship_would_open():
    assert check_gate(passed_on(SHA, SHA), "review", stuck_probe()) == {"ok": True, "need": []}
    u = branched({**CHAIN, "review.md": review_art("accepted", five_full_page_passes())})
    assert check_gate(u, "review", ui_probe(["coscc/screens.py"])) == {"ok": True, "need": []}
    calls = []
    probe = stuck_probe()
    gh = probe.gh
    counted = with_attrs(probe, gh=lambda *a: (calls.append(" ".join(a)), gh(*a))[1])
    assert check_gate(asked(), "review", counted) == {"ok": True, "need": []}
    assert len(calls) == 1
    assert calls[0].startswith("pr checks ")


def test_a_second_pass_on_the_same_head_that_leaves_ship_closed_stops_with_the_gates_reasons():
    n = next_step(passed_on(SHA, SHA), stuck_probe())
    assert n["stage"] == ""
    assert n["blocked"] is True
    assert n["action"].startswith(
        "needs a person — review rounds 1 and 2 both passed on aaaaaaa and ship is still closed: "
    )
    assert "not an ancestor of the reviewed commit" in n["action"]


def test_the_same_stop_in_the_ship_refused_branch():
    u = branched(
        {
            **CHAIN,
            "review.md": review_art("accepted", passes(SHA, SHA)),
            "ship.md": ship_art(ship_draft(2)),
        }
    )
    n = next_step(u, stuck_probe())
    assert n["stage"] == ""
    assert re.search(
        r"^needs a person — review rounds 1 and 2 both passed on aaaaaaa and ship is still "
        r"closed: .*not an ancestor of the reviewed commit",
        n["action"],
    )


def test_two_passes_on_different_heads_do_not_stop():
    u = passed_on(SHA, REB)
    # Every git answer not named is `ok('')`, which reads as the same head; so the comparison
    # between the two rounds is named here, both ways a head can differ.
    moved = stuck_probe({f"diff --name-only {SHA}..{REB}": ok("coscc/runner.py")}, REB)
    assert next_step(u, moved)["stage"] == "review"
    rewritten = stuck_probe({f"merge-base --is-ancestor {SHA} {REB}": NO}, REB)
    assert next_step(u, rewritten)["stage"] == "review"
    # And the second of them is the retry: the review gate says so.
    assert check_gate(u, "review", moved)["retry"]["n"] == 2


def test_a_round_whose_reviewed_commit_is_not_here_stops_and_says_so():
    u = passed_on(SHA, REB)
    gone_git = {
        f"cat-file -e {SHA}^{{commit}}": {"code": 1, "out": "", "err": "fatal: not a valid object"}
    }
    gone = next_step(u, stuck_probe(gone_git, REB))
    assert gone["stage"] == ""
    assert re.search(
        rf"^needs a person — the reviewed commit {SHA} of review round 1 is not in this repository",
        gone["action"],
    )
    bad_diff = {
        f"diff --name-only {SHA}..{REB}": {"code": 128, "out": "", "err": "fatal: bad object"}
    }
    broken = next_step(u, stuck_probe(bad_diff, REB))
    assert broken["stage"] == ""
    assert re.search(r"^needs a person — git could not diff .*fatal: bad object", broken["action"])


def test_where_git_cannot_compare_the_two_rounds_the_stop_does_not_say_they_share_a_head():
    u = passed_on(SHA, REB)
    gone = next_step(u, stuck_probe({f"cat-file -e {SHA}^{{commit}}": NO}, REB))
    bad_diff = {
        f"diff --name-only {SHA}..{REB}": {"code": 128, "out": "", "err": "fatal: bad object"}
    }
    broken = next_step(u, stuck_probe(bad_diff, REB))
    for n in [gone, broken]:
        assert "both passed" not in n["action"]
        assert re.search(
            r"review rounds 1 and 2 passed on aaaaaaa and ddddddd, not known to be one head, "
            r"and ship is still closed: ",
            n["action"],
        )


def test_a_pull_request_behind_the_reviewed_commit_gets_one_retry_then_stops():
    # BEHIND is the reviewed commit's parent: the round was taken on a commit not pushed yet.
    behind = "6" * 40
    probe = green_probe(
        None,
        {
            f"merge-base --is-ancestor {SHA} {behind}": NO,
            f"merge-base --is-ancestor {behind} {SHA}": ok(),
        },
        {"state": "OPEN", "headRefOid": behind},
    )
    once = passed_on(SHA)
    assert next_step(once, probe)["stage"] == "review"
    assert "is not on the head of #7" in "; ".join(
        check_gate(once, "review", probe)["retry"]["need"]
    )
    twice = next_step(passed_on(SHA, SHA), probe)
    assert twice["stage"] == ""
    assert re.search(
        r"^needs a person — review rounds 1 and 2 both passed on aaaaaaa and ship is still "
        r"closed: .*is not on the head of #7",
        twice["action"],
    )
    # A head that is not an ancestor, a rebase, is a new head: another round, as before.
    rebased = green_probe(
        None,
        {
            f"merge-base --is-ancestor {SHA} {REB}": NO,
            f"merge-base --is-ancestor {REB} {SHA}": NO,
        },
        {"state": "OPEN", "headRefOid": REB},
    )
    assert next_step(passed_on(SHA, SHA), rebased)["stage"] == "review"


def test_a_commit_outside_the_units_files_on_the_head_or_a_rejected_review_is_the_way_out():
    # HEAD2 follows SHA, so it is not an ancestor of it; the default `ok()` would say it is.
    git_ = {
        f"diff --name-only {SHA}..{HEAD2}": ok("coscc/runner.py"),
        f"merge-base --is-ancestor {HEAD2} {SHA}": NO,
    }
    assert next_step(passed_on(SHA, SHA), stuck_probe(git_, HEAD2))["stage"] == "review"
    rejected = branched({**CHAIN, "review.md": review_art("rejected", passes(SHA, SHA))})
    r = next_step(rejected, stuck_probe())
    assert r == next_action(rejected)
    assert "needs a person" not in r["action"]


def test_the_one_retry_does_not_read_the_review_round_limit():
    u = passed_on(SHA, SHA)

    def at(limit):
        return next_step(u, stuck_probe(), limit)

    assert at(1) == at(10)
    assert at(1) == next_step(u, stuck_probe())
    assert at(1)["stage"] == ""


# --- a pull request merged before ship.md was written ------------------------------------------

MERGE = "9" * 40
MERGED_AT = "2026-09-20T13:33:07Z"


def merged_view(**over):
    return {
        "state": "MERGED",
        "headRefOid": SHA,
        "mergeCommit": {"oid": MERGE},
        "mergedAt": MERGED_AT,
        **over,
    }


def merged_probe(view=None, git_=None, checks=None, calls=None):
    """A pull request GitHub reports merged as `MERGE`, which is here and on origin/main unless
    `git_` says otherwise. Every call lands in `calls`."""
    probe = green_probe(checks, git_, view or merged_view())
    return recorded(probe, [] if calls is None else calls)


GONE = {
    "rev-parse --verify --quiet refs/heads/feat/x": NO,
    "rev-parse --verify --quiet refs/remotes/origin/feat/x": NO,
}


def test_a_merged_pull_request_opens_ship_to_record_it_when_its_branch_is_gone():
    calls = []
    g = check_gate(passed_once(), "ship", merged_probe(git_=GONE, calls=calls))
    assert g == {
        "ok": True,
        "need": [],
        "merged": {"number": 7, "commit": MERGE, "at": MERGED_AT, "head": SHA},
    }
    # One reading of the pull request, the merge commit asked for twice, and nothing else.
    assert calls == [
        "gh pr view 7 --json state,headRefOid,mergeCommit,mergedAt,title",
        f"cat-file -e {MERGE}^{{commit}}",
        f"merge-base --is-ancestor {MERGE} refs/remotes/origin/main",
    ]


def test_next_offers_ship_for_a_merged_pull_request_when_ship_md_is_missing_stale_or_refused():
    action = f"ship — #7 was merged as {MERGE} at {MERGED_AT}: record it in ship.md; do not merge"
    stale = {**art("accepted"), "stale": {"stage": "review", "date": "2026-09-26"}}
    refused = ship_art(ship_draft(1, "failed to delete local branch fix/x: cannot switch to main"))
    for ship in [None, stale, refused]:
        extra = {"ship.md": ship} if ship else {}
        u = branched({**CHAIN, "review.md": review_art("accepted", round_(1, "pass")), **extra})
        for git_ in [{}, GONE]:
            n = next_step(u, merged_probe(git_=git_))
            assert n == {"blocked": True, "action": action, "stage": "ship"}
            assert "MERGED, not open" not in n["action"]


def test_an_accepted_ship_md_is_finished_for_a_merged_pull_request_without_asking_gh():
    u = branched(
        {
            **CHAIN,
            "review.md": review_art("accepted", round_(1, "pass")),
            "ship.md": art("accepted"),
        }
    )
    calls = []
    for probe in [merged_probe(calls=calls), None]:
        assert next_step(u, probe) == {"blocked": False, "action": "finished", "stage": ""}
    assert calls == []


# --- a unit stuck at the review limit is given a round more ------------------------------------

MORE = "\n### More rounds\nDecided by: owner. Date: 2026-09-27. Via: product.\nRounds: 1\n"


def stuck_rounds(n):
    return "\n".join(round_(i + 1, "changes-requested", ["- F1 [open] a"]) for i in range(n))


STUCK = f"{REVIEW_HEAD}{stuck_rounds(3)}"


def with_more(review, *blocks):
    return f"{review}\n## Answers\n{''.join(blocks)}"


def stuck_files(review):
    return {
        "intent.md": "# I\nAuthor: t. Type: feat. Status: accepted.\n",
        "spec.md": "# S\nStatus: accepted.\n",
        "plan.md": "# P\nStatus: accepted.\n",
        "impl.md": impl_text(""),
        "pr.md": "# PR: feat(0001): x\nPR: https://github.com/o/r/pull/7. Status: accepted.\n",
        "review.md": review,
    }


def fresh(tmp_path: Path) -> Path:
    return tmp_path / str(len(list(tmp_path.iterdir())))


def tree_of(tmp_path, files, entry):
    d = fresh(tmp_path) / "qroot" / ".cos" / "0001_q"
    d.mkdir(parents=True)
    for f, text in files.items():
        (d / f).write_text(text)
    return read(d, "0001_q", entry)


def stuck(tmp_path, review, extra=None):
    files = {**stuck_files(review), **(extra or {})}
    statuses = {f: "accepted" for f in files} | {"review.md": "changes-requested"}
    return tree_of(tmp_path, files, known(statuses))


def tree_after_round_two(tmp_path, review, claims=("F2", "F3"), answers=None):
    """A unit right after its second review round, impl's record claiming `claims`."""
    files = {
        "intent.md": "# I\nAuthor: t. Type: fix. Status: accepted.\n",
        "spec.md": "# S\nStatus: accepted.\n",
        "plan.md": "# P\nStatus: accepted.\n",
        "impl.md": impl_text(),
        "pr.md": "# PR: fix(0001): x\nPR: https://github.com/o/r/pull/7. Status: accepted.\n",
        "review.md": review,
    }
    statuses = {f: "accepted" for f in files} | {"review.md": "changes-requested"}
    entry = known(statuses, type="fix", records=claim_record(*claims), answers=answers)
    return tree_of(tmp_path, files, entry)


def test_a_more_rounds_block_lifts_the_review_limit_of_its_unit_alone(tmp_path):
    none = stuck(tmp_path, STUCK)
    need = check_gate(none, "review", green_probe(), 3)["need"][0]
    assert "needs a person — review used 3 of 3 rounds" in need
    assert "needs a person" in next_action(none, 3)["action"]
    given = stuck(tmp_path, with_more(STUCK, MORE))
    assert given["artifacts"]["review.md"]["roundsGranted"] == 1
    assert given["problems"] == []
    assert check_gate(given, "review", green_probe(), 3)["ok"] is True
    assert "needs a person" not in next_action(given, 3)["action"]
    assert "3 of 4 rounds used" in next_action(given, 3)["action"]
    assert "needs a person" not in next_step(given, green_probe(), 3)["action"]


def test_a_unit_that_used_its_granted_round_needs_a_person_again_at_the_new_limit(tmp_path):
    used = f"{REVIEW_HEAD}{stuck_rounds(4)}"
    again = stuck(tmp_path, with_more(used, MORE))
    need = check_gate(again, "review", green_probe(), 3)["need"][0]
    assert "needs a person — review used 4 of 4 rounds" in need
    assert "review used 4 of 4 rounds" in next_action(again, 3)["action"]
    assert more_rounds(again, 3) is True
    # Two blocks add up.
    assert review_limit(stuck(tmp_path, with_more(used, MORE, MORE)), 3) == 5


def test_a_more_rounds_block_with_no_decided_by_or_no_rounds_line_is_not_counted(tmp_path):
    decided = "Decided by: owner. Date: 2026-09-27. Via: product.\n"
    bad = [
        ("\n### More rounds\nRounds: 1\n", "Decided by"),
        (f"\n### More rounds\n{decided}Rounds: 0\n", "Rounds"),
        (f"\n### More rounds\n{decided}Rounds: x\n", "Rounds"),
    ]
    for block, line in bad:
        u = stuck(tmp_path, with_more(STUCK, block))
        assert "roundsGranted" not in u["artifacts"]["review.md"], block
        assert u["problems"] == [
            f"review.md: more rounds block 1 has no valid {line} line — it is not counted"
        ], block
        need = check_gate(u, "review", green_probe(), 3)["need"][0]
        assert re.search(r"needs a person — review used 3 of 3", need)
    # A bad block is numbered among the more rounds blocks only, and does not void a good one.
    mixed = parse_more_rounds(with_more(STUCK, f_block("F1"), MORE, bad[1][0]))
    assert mixed == {
        "granted": 1,
        "problems": ["more rounds block 2 has no valid Rounds line — it is not counted"],
    }


def test_a_more_rounds_block_is_never_an_answer_and_never_the_tail_of_a_finding_answer():
    # It is not a round: the review stops at the answers.
    assert len(parse_review(with_more(STUCK, MORE))["rounds"]) == 3


# --- a red check no rerun can fix just stops ---------------------------------------------------

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
            "        # `github.head_ref` is the branch the pull request comes from. The check is the",
            "        # same one that runs locally, from the same file, so CI and `coscc.loop` cannot",
            "        # disagree about what a valid name is.",
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
STOP_97 = (
    "needs a person — CI is red on #7: branch-name — branch-name checks the branch name, and no "
    f'rerun or impl can fix it: "{HEAD_97}" is not a work branch: {REASON_97}'
)


def red_line(names):
    return f"CI is red on #7: {names} — back to impl: fix on the branch and push"


def head_probe(probe, head=HEAD_97, workflows=None, calls=None):
    """Any probe, with `gh pr view --json headRefName` answering `head` (`None`: gh fails) and the
    workflows `workflows`. Every `gh` call lands in `calls`."""
    calls = [] if calls is None else calls
    workflows = [PR_YML] if workflows is None else workflows
    inner = probe.gh

    def gh(*a):
        calls.append(" ".join(["gh", *a]))
        if a[-1] != "headRefName":
            return inner(*a)
        if head is None:
            return {"code": 1, "out": "", "err": "HTTP 502: Bad Gateway"}
        return ok(json.dumps({"headRefName": head}))

    return with_attrs(probe, gh=gh, workflows=lambda: workflows)


def test_a_branch_name_red_while_tests_pend_next_stops_and_needs_a_person():
    pr = {**PR, "pr": {"url": "https://github.com/o/r/pull/97", "number": 97}}
    u = branched({**CHAIN, "pr.md": pr})
    assert branch_problem(HEAD_97) == REASON_97
    n = next_step(u, head_probe(green_probe(RED_97)))
    assert n["stage"] == ""
    assert n["blocked"] is True
    assert n["action"].startswith("needs a person — "), n["action"]
    for part in ["#97", "branch-name", REASON_97]:
        assert part in n["action"], part
    assert n["action"] == STOP_97.replace("#7", "#97")
    # `tests` still running is not waited on, and nothing sends it to impl.
    assert not re.search(r"has not finished|back to impl", n["action"])


def test_a_red_branch_name_check_on_a_valid_head_goes_to_impl_saying_why_cannot_be_told():
    probe = head_probe(green_probe([{"name": "branch-name", "bucket": "fail"}]), head="feat/x")
    assert next_step(branched(CHAIN), probe) == {
        "blocked": True,
        "action": (
            f'{red_line("branch-name")} — branch-name checks the branch name, yet "feat/x" '
            "passes check-branch here, so why it failed cannot be told from here"
        ),
        "stage": "impl",
    }


def test_a_bad_head_no_red_check_runs_check_branch_for_or_a_head_gh_cannot_read_goes_to_impl():
    u = branched(CHAIN)

    def clause(names):
        return (
            f'{red_line(names)} — "{HEAD_97}" is not a work branch: {REASON_97}, but no red '
            "check runs coscc.loop check-branch in .github/workflows/, so whether that is why "
            "cannot be told from here"
        )

    other = {
        "path": ".github/workflows/x.yml",
        "text": "jobs:\n  branch-name:\n    steps:\n      - run: npm test\n",
    }
    # The red check is another one, or no workflow names the red one.
    for checks, workflows, names in [
        (
            [{"name": "tests", "bucket": "fail"}, {"name": "branch-name", "bucket": "pass"}],
            [PR_YML],
            "tests",
        ),
        ([{"name": "branch-name", "bucket": "fail"}], [], "branch-name"),
        ([{"name": "branch-name", "bucket": "cancel"}], [other], "branch-name"),
    ]:
        probe = head_probe(green_probe(checks), workflows=workflows)
        assert next_step(u, probe) == {"blocked": True, "action": clause(names), "stage": "impl"}
    # gh cannot say which branch, so the checks go to impl as they did before.
    c = next_step(u, head_probe(green_probe(RED_97), head=None))
    assert c == {
        "blocked": True,
        "action": (
            f"{red_line('branch-name')} — cannot read the branch of #7 (HTTP 502: Bad Gateway), "
            "so whether its name is why cannot be told from here"
        ),
        "stage": "impl",
    }
    # An answer with no `headRefName` in it is no branch either.
    plain = with_attrs(head_probe(green_probe(RED_97)), gh=green_probe(RED_97).gh)
    assert re.search(
        r'cannot read the branch of #7 \(\{"state": "OPEN"', next_step(u, plain)["action"]
    )


def incomplete_round(n):
    return (
        f"## Round {n}\n\nReviewed: {SHA}. Verdict: incomplete.\n\n### Reviewed so far\n\n- a.py\n\n"
        "### Findings\n\n- F1 [open] a.py:3 — high — x\n\n### What was not reviewed\n\n- b.py\n"
    )


def review_of_rounds(status, rounds):
    text = f"# Review: x\nStatus: {status}.\n\n{chr(10).join(rounds)}"
    return branched({**CHAIN, "review.md": review_art(status, text)})


def cr(n):
    return round_(n, "changes-requested", ["- F1 [open] x"])


def test_a_merge_base_that_names_no_commit_is_an_error_never_a_clean_rebase():
    u = passed_once()
    for base, said in [
        (
            lambda c: ok(""),
            r"its patch could not be compared with the reviewed one: git merge-base printed no commit",
        ),
        (lambda c: ok("not a sha\n"), r"git merge-base printed no commit"),
        (
            lambda c: {"code": 1, "out": "", "err": "fatal: no merge base"},
            r"could not be compared .*fatal: no merge base",
        ),
    ]:
        g = check_gate(u, "ship", rebased_probe(base=base))
        assert g["ok"] is False
        assert re.search(
            r"rewritten after the pass \(a rebase does this\): review its new head in another round",
            g["need"][0],
        )
        assert re.search(said, g["need"][0])
        assert next_step(u, rebased_probe(base=base))["stage"] == "review"
    # Two empty patches are equal, so an empty one from a failing diff must not be read as one.
    failing = rebased_probe()
    inner = failing.git

    def git2(*a):
        if a[0] == "diff" and a[1] == "--no-color":
            return {"code": 128, "out": "", "err": "fatal: bad object"}
        return inner(*a)

    failing.git = git2
    assert re.search(
        r"could not be compared .*fatal: bad object", check_gate(u, "ship", failing)["need"][0]
    )


def test_red_pending_or_none_on_a_clean_rebase_on_a_real_repository_never_offers_review(tmp_path):
    r = rebase_repo(tmp_path)
    r.on_main(far_from_the_hunk)
    head = r.rebase()
    u = passed_at(r.R)
    for checks, stage in [
        ([{"name": "tests", "bucket": "fail"}], "impl"),
        ([{"name": "tests", "bucket": "pending"}], ""),
        ([], ""),
    ]:
        assert check_gate(u, "ship", r.probe(head, checks))["ok"] is False
        assert next_step(u, r.probe(head, checks))["stage"] == stage


def test_a_merged_pull_request_opens_ship_to_record_it_while_its_branch_is_still_here():
    u = passed_once()
    assert check_gate(u, "ship", merged_probe()) == {
        "ok": True,
        "need": [],
        "merged": {"number": 7, "commit": MERGE, "at": MERGED_AT, "head": SHA},
    }
    # Nothing that speaks of a merge still to come closes it: red CI, a head behind origin/main,
    # code after the pass, a UI unit's screenshots. The head it merged is carried for ship.md.
    calls = []
    late = merged_probe(
        view=merged_view(headRefOid=HEAD2),
        checks=[{"name": "tests", "bucket": "fail"}],
        git_={
            f"merge-base --is-ancestor {TRUNK} {HEAD2}": NO,
            f"diff --name-only {SHA}..{HEAD2}": ok("src/a.py"),
            f"diff --name-only origin/main...{HEAD2}": ok("coscc/screens.py"),
        },
        calls=calls,
    )
    g = check_gate(u, "ship", with_attrs(late, ui=lambda: UI))
    assert g["ok"] is True
    assert g["merged"]["head"] == HEAD2
    assert "head" not in g
    asked_for = ("gh pr checks", "rev-parse", "diff")
    assert [c for c in calls if c.startswith(asked_for)] == []


def test_a_merge_commit_that_cannot_be_read_is_not_here_or_not_on_trunk_closes_ship():
    u = passed_once()
    cannot = "cannot read the merge commit of #7: gh gave mergeCommit"
    for probe, said in [
        (
            merged_probe(view=merged_view(mergeCommit=None)),
            rf"^{cannot} none and mergedAt 2026-09-20T13:33:07Z — fetch, then ask again$",
        ),
        (
            merged_probe(view=merged_view(mergeCommit={"oid": "abc"})),
            rf"^{cannot} abc and",
        ),
        (
            merged_probe(view=merged_view(mergedAt="")),
            rf"^{cannot} {MERGE} and mergedAt none — fetch, then ask again$",
        ),
        (
            merged_probe(git_={**GONE, f"cat-file -e {MERGE}^{{commit}}": NO}),
            rf"^the merge commit {MERGE} of #7 is not in this repository — fetch, then ask again$",
        ),
        (
            merged_probe(git_={f"merge-base --is-ancestor {MERGE} {TRUNK}": NO}),
            rf"^the merge commit {MERGE} of #7 is not on origin/main here — fetch, then ask again$",
        ),
    ]:
        g = check_gate(u, "ship", probe)
        assert [g["ok"], len(g["need"])] == [False, 1]
        assert re.search(said, g["need"][0])
        assert "MERGED, not open" not in g["need"][0]
        n = next_step(u, probe)
        assert [n["stage"], n["blocked"]] == ["", True]
        assert re.search(said, n["action"])


def test_a_pull_request_closed_without_merging_closes_ship_with_the_old_sentence():
    u = passed_once()
    for git_ in [{}, GONE]:
        view = {"state": "CLOSED", "headRefOid": SHA, "mergeCommit": None, "mergedAt": None}
        probe = merged_probe(view=view, git_=git_)
        said = "#7 is CLOSED, not open — there is nothing to merge"
        assert check_gate(u, "ship", probe) == {"ok": False, "need": [said]}
        assert next_step(u, probe) == {"blocked": True, "action": said, "stage": ""}


def test_every_branch_of_next_that_reads_ci_stops_on_the_same_line_and_the_gates_close_on_it(
    tmp_path,
):
    def probe():
        return head_probe(green_probe(RED_97))

    def rebased():
        return head_probe(rebased_probe(checks=RED_97))

    stale = {
        **review_art("accepted", round_(1, "pass")),
        "stale": {"stage": "impl", "date": "2026-09-26"},
    }
    passed_review = {**CHAIN, "review.md": review_art("accepted", round_(1, "pass"))}
    rows = [answer("review.md", f, "Bao", "ran it", "2026-09-24") for f in ("F2", "F3")]
    answered = (
        f"{REVIEW_HEAD}{ROUND1}\n{ROUND2}\n{round3()}\n## Answers\n{f_block('F2')}{f_block('F3')}"
    )
    claimed = f"{REVIEW_HEAD}{ROUND1}\n{ROUND2}"
    cases = [
        ("pr done, no review", branched(CHAIN), probe()),
        ("a stale review", branched({**CHAIN, "review.md": stale}), probe()),
        ("person-answered", tree_after_round_two(tmp_path, answered, answers=rows), probe()),
        ("every open finding claimed", tree_after_round_two(tmp_path, claimed), probe()),
        (
            "review-incomplete",
            review_of_rounds("draft", [cr(1), incomplete_round(2)]),
            probe(),
        ),
        ("changes-requested with a fix", asked(), head_probe(moved_to(["src/a.py"], RED_97))),
        ("ship after a clean rebase", branched(passed_review), rebased()),
        (
            "ship-refused after a clean rebase",
            branched({**passed_review, "ship.md": ship_art(ship_draft(1))}),
            rebased(),
        ),
    ]
    for label, u, p in cases:
        assert next_step(u, p) == {"blocked": True, "action": STOP_97, "stage": ""}, label
    assert check_gate(branched(CHAIN), "review", probe()) == {"ok": False, "need": [STOP_97]}
    assert check_gate(branched(passed_review), "ship", rebased()) == {
        "ok": False,
        "need": [STOP_97],
    }
