"""`0035` plan step 4: the pure half of `coscc/integrate.py`, one table per function."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc import integrate as ig

HEAD = "a" * 40
NEW = "b" * 40
MAIN = "c" * 40


def row(mergeable="MERGEABLE", head=HEAD):
    return {"number": 7, "headRefOid": head, "headRefName": "feat/x", "mergeable": mergeable}


class ClassifyGivesAllFiveStates(unittest.TestCase):
    def test_the_five(self):
        rec = {"head_after": HEAD}
        cases = [
            (("gh: not logged in", 0, MAIN, None, None), "unknown"),
            ((row(), "rev-list failed", MAIN, None, None), "unknown"),
            ((row("CONFLICTING"), 3, MAIN, None, None), "conflicting"),
            ((row(), 0, MAIN, rec, [{"name": "tests", "bucket": "fail"}]), "red-after-integration"),
            ((row(), 2, MAIN, None, None), "behind"),
            ((row("UNKNOWN"), 2, MAIN, None, None), "behind"),
            ((row(), 0, MAIN, None, None), "current"),
            ((row(), 0, MAIN, rec, [{"name": "tests", "bucket": "pass"}]), "current"),
            ((row(), 0, MAIN, rec, "gh checks failed"), "unknown"),
        ]
        seen = set()
        for args, want in cases:
            with self.subTest(args=args):
                got = ig.classify(*args)
                self.assertEqual(got["state"], want)
                seen.add(got["state"])
        self.assertEqual(seen, set(ig.STATES))

    def test_an_error_carries_the_words(self):
        self.assertEqual(ig.classify("gh: HTTP 401", 0, MAIN, None)["reason"], "gh: HTTP 401")

    def test_checks_are_asked_only_on_the_integrations_own_head(self):
        self.assertFalse(ig.needs_checks(row(), None))
        self.assertFalse(ig.needs_checks(row(head=NEW), {"head_after": HEAD}))
        self.assertFalse(ig.needs_checks(row(), {"head_after": ""}))
        self.assertTrue(ig.needs_checks(row(), {"head_after": HEAD}))


class RefusalNamesTheFirstConditionMissing(unittest.TestCase):
    OK = dict(in_window=True, busy="", clean=True, branch_ok=True,
              local_head=HEAD, pr_head=HEAD, state="behind")

    def test_each_condition(self):
        cases = [
            ({"in_window": False}, "not between pr and ship"),
            ({"busy": "0001_a is busy: a spec step is running since 2026-09-24T01:02:03+00:00"},
             "a spec step is running"),
            ({"clean": False}, "uncommitted"),
            ({"branch_ok": False}, "not on the unit's branch"),
            ({"local_head": NEW}, "not the pull request's head"),
            ({"state": "current"}, "nothing to integrate"),
            ({"state": "unknown"}, "nothing to integrate"),
        ]
        for change, want in cases:
            with self.subTest(change=change):
                self.assertIn(want, ig.refusal(**{**self.OK, **change}))

    def test_all_hold(self):
        for state in ig.BUTTON_STATES:
            self.assertEqual(ig.refusal(**{**self.OK, "state": state}), "")

    def test_the_order_is_the_specs(self):
        self.assertIn("not between", ig.refusal(**{**self.OK, "in_window": False, "busy": "0001_a is busy"}))

    def test_current_says_what_it_was_compared_with(self):
        """`0052` R4: the sentence names the ref and how it got there, and keeps its start."""
        origin = ig.origin_note(MAIN, {"outcome": "fetched", "attempts": 1, "age": 0.4})
        said = ig.refusal(**{**self.OK, "state": "current", "origin": origin})
        self.assertEqual(
            said, "the unit is current against origin/main ccccccc (fetched), which has nothing to integrate")
        self.assertTrue(said.startswith("the unit is current"))

    def test_without_an_origin_the_old_sentence_stands(self):
        self.assertEqual(ig.refusal(**{**self.OK, "state": "current"}),
                         "the unit is current, which has nothing to integrate")
        # Only `current` carries the ref: `unknown` keeps its sentence.
        self.assertEqual(ig.refusal(**{**self.OK, "state": "unknown", "origin": "origin/main x"}),
                         "the unit is unknown, which has nothing to integrate")


class OriginNote(unittest.TestCase):
    """`0052` R4: the four ways a press got its `origin/main`."""

    def test_each_outcome(self):
        cases = [
            ({"outcome": "fetched", "attempts": 1, "age": 0.2}, "origin/main ccccccc (fetched)"),
            ({"outcome": "joined", "attempts": 1, "age": 1.5}, "origin/main ccccccc (joined a running fetch)"),
            ({"outcome": "reused", "attempts": 0, "age": 12.3}, "origin/main ccccccc (reused 12.3s ago)"),
            ({"outcome": "failed", "detail": "fatal: could not read"},
             "origin/main ccccccc (fetch failed: fatal: could not read)"),
            (None, "origin/main ccccccc"),
        ]
        for fetch, want in cases:
            with self.subTest(fetch=fetch):
                self.assertEqual(ig.origin_note(MAIN, fetch), want)
        self.assertEqual(ig.origin_note("", None), "origin/main unread")


class Warnings(unittest.TestCase):
    def test_each_line_only_when_true(self):
        self.assertEqual(ig.warnings([], "accepted", False, "W"), [])
        self.assertIn("ship gate closes", ig.warnings([{"verdict": "pass"}], "accepted", False, "W")[0])
        self.assertIn("offers review", ig.warnings([{"verdict": "changes-requested"}], "changes-requested", False, "W")[0])
        self.assertEqual(ig.warnings([], "", True, "W"), ["W"])

    def test_a_press_that_may_fall_to_gebo_says_so(self):
        """`0052`: a `behind` or `current` press may open Gebo; the page says so, with the grant."""
        said = ig.warnings([], "", True, "W", fallback=True)
        self.assertEqual(len(said), 2)
        self.assertIn("If GitHub refuses the rebase, the app opens Gebo, a paid agent session", said[0])
        self.assertIn("pressing Integrate agrees to that session", said[0])
        self.assertEqual(said[1], "W")

    def test_a_passed_unit_is_told_what_integrating_costs(self):
        # `0061` R11.1: the four things the warning must say.
        said = ig.warnings([{"verdict": "pass"}], "accepted", False, "W")[0]
        self.assertIn("rewrites the reviewed commit", said)
        self.assertIn("another review round is needed — it does not count toward COS_REVIEW_ROUNDS, but it is another paid session", said)
        self.assertIn("Run ship first", said)
        self.assertIn("only when GitHub reports a conflict or refuses the merge because the branch is behind main", said)


class Related(unittest.TestCase):
    UNITS = [{"name": "0030_a", "pr": {"number": 41}}, {"name": "0035_x", "pr": {"number": 50}},
             {"name": "0040_z", "pr": None}]

    def test_both_groups(self):
        commits = [
            {"sha": "1" * 40, "subject": "feat: a (#41)", "files": ["coscc/runner.py", "README.md"]},
            {"sha": "2" * 40, "subject": "fix: b (#99)", "files": ["coscc/runner.py"]},
            {"sha": "3" * 40, "subject": "docs: c (#42)", "files": ["docs/x.md"]},
            {"sha": "4" * 40, "subject": "no number", "files": ["coscc/runner.py"]},
        ]
        others = [
            {"unit": "0036_b", "files": ["coscc/runner.py"]},
            {"unit": "0037_c", "files": ["other.py"]},
            {"unit": "0038_d", "files": None},
            {"unit": "0035_x", "files": ["coscc/runner.py"]},
        ]
        rel = ig.related(commits, ["coscc/runner.py"], self.UNITS, others, "0035_x")
        self.assertEqual([(m["subject"], m["unit"]) for m in rel["merged"]],
                         [("feat: a (#41)", "0030_a"), ("fix: b (#99)", None), ("no number", None)])
        self.assertEqual(rel["open"], [{"unit": "0036_b", "files": ["coscc/runner.py"]},
                                       {"unit": "0038_d", "files": None}])
        self.assertEqual(ig.related_units(rel), ["0030_a", "0036_b", "0038_d"])

    def test_read_paths_are_the_own_folder_and_three_files_each(self):
        rel = {"merged": [{"unit": "0030_a"}], "open": []}
        paths = ig.read_paths(Path("/u"), "0035_x", rel)
        self.assertEqual(paths, ("/u/0035_x", "/u/0030_a/intent.md", "/u/0030_a/spec.md", "/u/0030_a/plan.md"))


class NeedsPersonAndOutcome(unittest.TestCase):
    def test_parse(self):
        reply = "Tried.\n- [needs-person] A keeps x, B drops x\n[needs-person] second\nnot [needs-person] this"
        self.assertEqual(ig.parse_needs_person(reply), ["A keeps x, B drops x", "second"])

    def test_the_outcome_is_read_from_git_not_the_reply(self):
        self.assertEqual(ig.outcome_of_session(HEAD, NEW, ""), "pushed")
        self.assertEqual(ig.outcome_of_session(HEAD, HEAD, "I pushed it."), "failed")
        self.assertEqual(ig.outcome_of_session(HEAD, HEAD, "[needs-person] x"), "needs-person")


class Record(unittest.TestCase):
    def test_shape(self):
        rec = ig.record(workspace="w", unit="0035_x", pr=7, mode="mechanical", head_before=HEAD,
                        head_after=NEW, origin_sha=MAIN, outcome="pushed")
        self.assertEqual(rec["kind"], "integration")
        self.assertEqual(rec["stage"], "integrate")
        self.assertEqual(rec["head_after"], NEW)

    def test_head_after_is_empty_unless_pushed(self):
        rec = ig.record(workspace="w", unit="u", pr=7, mode="agent", head_before=HEAD,
                        head_after=NEW, origin_sha=MAIN, outcome="needs-person")
        self.assertEqual(rec["head_after"], "")

    def test_an_unknown_outcome_is_refused(self):
        with self.assertRaises(ValueError):
            ig.record(workspace="w", unit="u", pr=7, mode="agent", head_before=HEAD,
                      head_after="", origin_sha=MAIN, outcome="ok")

    def test_fetch_merge_state_and_update_branch(self):
        """`0052` R5: the three keys are always there, and carry only what they name."""
        rec = ig.record(workspace="w", unit="u", pr=7, mode="mechanical", head_before=HEAD,
                        head_after="", origin_sha=MAIN, outcome="refused")
        self.assertEqual((rec["fetch"], rec["merge_state"], rec["update_branch"]), (None, "", None))
        rec = ig.record(workspace="w", unit="u", pr=7, mode="agent", head_before=HEAD, head_after="",
                        origin_sha=MAIN, outcome="failed",
                        fetch={"outcome": "fetched", "attempts": 1, "age": 0.3}, merge_state="BEHIND",
                        update_branch={"code": 1, "said": "gh: refused"})
        self.assertEqual(rec["fetch"], {"outcome": "fetched", "age": 0.3})
        self.assertEqual(rec["merge_state"], "BEHIND")
        self.assertEqual(rec["update_branch"], {"code": 1, "said": "gh: refused"})
        rec = ig.record(workspace="w", unit="u", pr=7, mode="mechanical", head_before=HEAD, head_after="",
                        origin_sha=MAIN, outcome="refused", fetch={"outcome": "failed", "detail": "no remote"})
        self.assertEqual(rec["fetch"], {"outcome": "failed", "detail": "no remote"})

    def test_an_old_record_without_them_still_describes(self):
        old = ig.record(workspace="w", unit="u", pr=7, mode="mechanical", head_before=HEAD,
                        head_after=NEW, origin_sha=MAIN, outcome="pushed")
        for k in ("fetch", "merge_state", "update_branch"):
            del old[k]
        text = ig.describe_for_review(old)
        self.assertIn("the app, mechanically", text)
        self.assertIn(NEW, text)

    def test_the_review_section_says_whose_word_it_is(self):
        text = ig.describe_for_review({"mode": "mechanical", "head_after": NEW})
        self.assertIn("An integration since the last round", text)
        self.assertIn("not by a person", text)
        self.assertIn("an agent session", ig.describe_for_review({"mode": "agent"}))


class ThePrompt(unittest.TestCase):
    def test_it_carries_the_lease_the_lists_and_the_artifacts(self):
        rel = {"merged": [{"sha": "1" * 40, "subject": "s (#41)", "unit": "0030_a", "files": ["f"]}],
               "open": [{"unit": "0036_b", "files": None}]}
        text = ig.build_prompt(skill="RULES", unit="0035_x", branch="feat/x", pr=7, state="conflicting",
                               reason="r", head_before=HEAD, origin_sha=MAIN, rel=rel,
                               units_root=Path("/u"),
                               own_paths={"intent.md": Path("/u/0035_x/intent.md")})
        for want in ("RULES", f"--force-with-lease=feat/x:{HEAD}", MAIN, "0030_a", "no local commit",
                     "/u/0030_a/plan.md", "- /u/0035_x/intent.md"):
            self.assertIn(want, text)
        self.assertNotIn("The mechanical rebase was refused", text)

    def test_every_own_artifact_it_names_can_be_read(self):
        """`0094` R15: Gebo is handed its unit's artifacts by path, so its grant must read them."""
        from coscc.policy import decide, grant_for

        with tempfile.TemporaryDirectory() as d:
            units_root = Path(d) / "units"
            own = units_root / "0035_x"
            own.mkdir(parents=True)
            tree = Path(d) / "tree"
            tree.mkdir()
            paths = {}
            for name in ("intent.md", "spec.md", "plan.md", "impl.md"):
                (own / name).write_text("Status: accepted.\nBODY-" + name, encoding="utf-8")
                paths[name] = own.resolve() / name
            rel = {"merged": [], "open": []}
            text = ig.build_prompt(skill="RULES", unit="0035_x", branch="feat/x", pr=7, state="conflicting",
                                   reason="r", head_before=HEAD, origin_sha=MAIN, rel=rel,
                                   units_root=units_root, own_paths=paths)
            self.assertNotIn("BODY-", text)
            named = [line[2:] for line in text.split("# This unit's own artifacts\n")[1].splitlines()
                     if line.startswith("- ")]
            self.assertEqual(named, [str(p) for p in paths.values()])
            for p in named:
                self.assertEqual(
                    decide(grant_for("integrate"), "Read", {"file_path": p}, str(tree), None,
                           read_also=ig.read_paths(units_root, "0035_x", rel)),
                    "", p)

    def test_a_refused_update_carries_its_code_and_words(self):
        """`0052`: the exit code and gh's words reach Gebo, and so does what they cannot say."""
        text = ig.build_prompt(skill="RULES", unit="0035_x", branch="feat/x", pr=7, state="behind",
                               reason="r", head_before=HEAD, origin_sha=MAIN,
                               rel={"merged": [], "open": []}, units_root=Path("/u"), own_paths={},
                               refused_update={"code": 1, "said": "gh: merge conflict"})
        for want in ("# The mechanical rebase was refused", "gh pr update-branch 7 --rebase",
                     "exited 1", "gh: merge conflict", "a login, the network, a permission"):
            self.assertIn(want, text)


class MergeState(unittest.TestCase):
    """`0052` R5: observed only, so every failure is `""` and nothing raises."""

    def ask(self, gh):
        with mock.patch.object(ig, "_gh", gh):
            return asyncio.run(ig.merge_state("/t", 7))

    def test_the_value(self):
        seen = {}

        async def gh(argv, cwd):
            seen["argv"] = argv
            return 0, json.dumps({"mergeStateStatus": "BEHIND"}), ""

        self.assertEqual(self.ask(gh), "BEHIND")
        self.assertEqual(seen["argv"], ["pr", "view", "7", "--json", "mergeStateStatus"])

    def test_gh_failing_is_empty(self):
        async def refused(argv, cwd):
            return 1, "", "gh: unknown field mergeStateStatus"

        async def timed_out(argv, cwd):
            raise ig.IntegrateError("gh pr view did not answer within 30s")

        self.assertEqual(self.ask(refused), "")
        self.assertEqual(self.ask(timed_out), "")

    def test_broken_json_is_empty(self):
        async def gh(argv, cwd):
            return 0, "not json", ""

        async def a_list(argv, cwd):
            return 0, "[]", ""

        self.assertEqual(self.ask(gh), "")
        self.assertEqual(self.ask(a_list), "")


class TheReviewPromptCarriesTheIntegration(unittest.TestCase):
    """R10, plan step 8: `build_prompt` places the note for `review` only."""

    def test_review_only(self):
        import tempfile

        from coscc.runner import build_prompt
        from coscc.runner_test import STAGES, UNIT, make_unit

        note = ig.describe_for_review({"mode": "mechanical", "head_after": NEW})
        with tempfile.TemporaryDirectory() as d:
            make_unit(Path(d), intent_md="Status: accepted.\nI")
            unit_dir = Path(d) / ".cos" / UNIT
            prompt, included = build_prompt(d, unit_dir, UNIT, "review", STAGES, "review.md", integration_note=note)
            self.assertIn("An integration since the last round", prompt)
            self.assertIn("integration", included)
            prompt, included = build_prompt(d, unit_dir, UNIT, "impl", STAGES, "impl.md", integration_note=note)
            self.assertNotIn("An integration since the last round", prompt)


class TheLatestIntegrationSinceTheLastRound(unittest.TestCase):
    """R10: a `pushed` integration counts only when written after the last `review` done."""

    def test_order_decides(self):
        import tempfile

        from coscc.journal import Journal
        from coscc.service import integration_since_review

        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            pushed = ig.record(workspace="w", unit="u", pr=7, mode="agent", head_before=HEAD,
                               head_after=NEW, origin_sha=MAIN, outcome="pushed")
            self.assertIsNone(integration_since_review(j, "w", "u"))
            j.append(pushed)
            self.assertEqual(integration_since_review(j, "w", "u")["head_after"], NEW)
            j.finished("w", "u", "review", "done")
            self.assertIsNone(integration_since_review(j, "w", "u"), "older than the last round")
            j.append({**pushed, "outcome": "refused"})
            self.assertIsNone(integration_since_review(j, "w", "u"), "only a push counts")
            j.finished("w", "u", "review", "failed")
            j.append(pushed)
            self.assertIsNotNone(integration_since_review(j, "w", "u"))


class GeboRunsUnderItsGrantAndLease(unittest.TestCase):
    """Plan step 7, with a stand-in `stream`: what the session is handed, and what it yields."""

    def test_the_session_gets_the_gate_and_the_ceilings(self):
        import asyncio

        from coscc.policy import grant_for

        seen: dict = {}

        class FakeSessions:
            async def stream(self, cwd, prompt, session_id, **kw):
                seen.update(kw, cwd=cwd)
                gate = kw["can_use_tool"]
                seen["push_ok"] = await gate("Bash", {"command": f"git push --force-with-lease=feat/x:{HEAD} origin feat/x"}, None)
                seen["push_bad"] = await gate("Bash", {"command": "git push --force origin feat/x"}, None)
                yield ("chunk", "[needs-person] A vs B")
                yield ("done", {"session_id": "s", "cost": {"usd": 0.1}})

        async def go():
            out = []
            async for item in ig.run_gebo(FakeSessions(), tree="/t", workspace="/w", prompt="p",
                                          grant=grant_for("integrate"), read_also=(), lease=("feat/x", HEAD),
                                          model=None):
                out.append(item)
            return out

        out = asyncio.run(go())
        self.assertEqual(seen["max_turns"], 120)
        self.assertEqual(seen["cwd"], "/t")
        self.assertEqual(type(seen["push_ok"]).__name__, "PermissionResultAllow")
        self.assertEqual(type(seen["push_bad"]).__name__, "PermissionResultDeny")
        end = out[-1][1]
        self.assertEqual(end["reply"], "[needs-person] A vs B")
        self.assertEqual(end["denials"], 1)
        self.assertEqual(ig.outcome_of_session(HEAD, HEAD, end["reply"]), "needs-person")


def fake_gh(bindir: Path, stdout: str = "", code: int = 0) -> Path:
    """A `gh` first on `PATH` that prints `stdout`, exits `code`, and logs its argv."""
    bindir.mkdir(parents=True, exist_ok=True)
    log = bindir / "gh.log"
    script = bindir / "gh"
    script.write_text(
        "#!/bin/sh\n"
        f"echo \"$@\" >> '{log}'\n"
        f"cat <<'EOF'\n{stdout}\nEOF\n"
        f"[ {code} -eq 0 ] || echo 'gh: no auth' >&2\n"
        f"exit {code}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return log


def on_path(bindir: Path):
    return mock.patch.dict(os.environ, {"PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"})


FOUND = json.dumps([{"url": "https://github.com/o/r/pull/7", "number": 7,
                     "mergeable": "MERGEABLE", "headRefOid": HEAD}])


class ThePullRequestIsLookedUpBeforePr(unittest.TestCase):
    """`0041` R2: three shapes, and a lookup that fails never raises."""

    def lookup(self, stdout="", code=0, branch="fix/x"):
        with tempfile.TemporaryDirectory() as d:
            log = fake_gh(Path(d) / "bin", stdout, code)
            with on_path(Path(d) / "bin"):
                rec = asyncio.run(ig.pr_for_branch(d, branch))
            argv = log.read_text(encoding="utf-8") if log.exists() else ""
        return rec, argv

    def test_found(self):
        rec, argv = self.lookup(FOUND)
        self.assertEqual(rec, {"state": "found", "url": "https://github.com/o/r/pull/7",
                               "number": 7, "mergeable": "MERGEABLE", "head": HEAD})
        self.assertIn("pr list --head fix/x --state open", argv)

    def test_none(self):
        rec, _ = self.lookup("[]")
        self.assertEqual(rec, {"state": "none", "branch": "fix/x"})

    def test_gh_failing_is_unknown_with_its_words(self):
        rec, _ = self.lookup("", code=1)
        self.assertEqual(rec["state"], "unknown")
        self.assertIn("no auth", rec["reason"])

    def test_broken_json_is_unknown(self):
        rec, _ = self.lookup("not json")
        self.assertEqual(rec["state"], "unknown")
        self.assertIn("JSON", rec["reason"])

    def test_no_branch_asks_nothing(self):
        rec, argv = self.lookup(FOUND, branch="")
        self.assertEqual(rec["state"], "unknown")
        self.assertEqual(argv, "")


class ThePullRequestBlock(unittest.TestCase):
    def test_found_says_reuse_and_not_create(self):
        text = ig.describe_pr_lookup({"state": "found", "url": "https://x/pull/7", "number": 7,
                                      "mergeable": "MERGEABLE", "head": HEAD})
        self.assertTrue(text.startswith("# The pull request, already looked up"))
        self.assertIn("https://x/pull/7", text)
        self.assertIn("Do not run `gh pr create` again", text)
        self.assertNotIn("Integrate", text)
        self.assertIn(ig.PR_SYNC_NOTE, text)

    def test_conflicting_says_stop_and_names_integrate(self):
        text = ig.describe_pr_lookup({"state": "found", "url": "u", "number": 7,
                                      "mergeable": "CONFLICTING", "head": HEAD})
        self.assertIn("Do not rebase", text)
        self.assertIn("*Integrate*", text)
        self.assertIn("`Status: accepted`", text)
        self.assertIn(ig.PR_SYNC_NOTE, text)

    def test_none_names_the_branch(self):
        text = ig.describe_pr_lookup({"state": "none", "branch": "fix/x"})
        self.assertIn("`fix/x`", text)
        self.assertIn(ig.PR_SYNC_NOTE, text)

    def test_0055_the_sync_note_says_the_app_does_it(self):
        self.assertIn("the app puts pr.md's title and body onto the pull request", ig.PR_SYNC_NOTE)
        self.assertIn("do not run `gh pr edit`", ig.PR_SYNC_NOTE)
        # R7 names `found` and `none`; `unknown` is left as it was.
        self.assertNotIn(ig.PR_SYNC_NOTE, ig.describe_pr_lookup({"state": "unknown", "reason": "x"}))

    def test_unknown_says_why_and_asks_once(self):
        text = ig.describe_pr_lookup({"state": "unknown", "reason": "gh: no auth"})
        self.assertIn("could not ask", text)
        self.assertIn("gh: no auth", text)
        self.assertIn("gh pr view", text)


if __name__ == "__main__":
    unittest.main()
