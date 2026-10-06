"""The pure half of `coscc/github/integrate.py`, one table per function."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.github import integrate as ig
from tests.runner.test_step import asks
from tests.units.test_submit import submits as _submits

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

    def test_checks_are_asked_only_on_the_integrations_own_head(self):
        self.assertFalse(ig.needs_checks(row(), None))
        self.assertFalse(ig.needs_checks(row(head=NEW), {"head_after": HEAD}))
        self.assertFalse(ig.needs_checks(row(), {"head_after": ""}))
        self.assertTrue(ig.needs_checks(row(), {"head_after": HEAD}))


class RefusalNamesTheFirstConditionMissing(unittest.TestCase):
    OK = dict(
        in_window=True,
        busy="",
        clean=True,
        branch_ok=True,
        local_head=HEAD,
        pr_head=HEAD,
        state="behind",
    )

    def test_each_condition(self):
        # Only a state with nothing to integrate carries `NOTHING`; every other refusal is one a
        # person looks at, and carries no code.
        cases = [
            ({"in_window": False}, "not between pr and ship", ""),
            (
                {"busy": "0001_a is busy: a spec step is running since 2026-09-24T01:02:03+00:00"},
                "a spec step is running",
                "",
            ),
            ({"clean": None}, "no worktree", ""),
            ({"clean": False}, "uncommitted", ""),
            ({"branch_ok": False}, "not on the unit's branch", ""),
            ({"pr_head": ""}, "could not be read", ""),
            ({"local_head": NEW}, "not the pull request's head", ""),
            ({"state": "current"}, "nothing to integrate", ig.NOTHING),
            ({"state": "unknown"}, "nothing to integrate", ig.NOTHING),
        ]
        for change, want, code in cases:
            with self.subTest(change=change):
                said, got = ig.refusal(**{**self.OK, **change})
                self.assertIn(want, said)
                self.assertEqual(got, code)
        self.assertEqual(ig.NOTHING, "nothing-to-integrate")

    def test_all_hold(self):
        for state in ig.BUTTON_STATES:
            self.assertEqual(ig.refusal(**{**self.OK, "state": state}), ("", ""))

    def test_the_order_is_the_specs(self):
        self.assertIn(
            "not between",
            ig.refusal(**{**self.OK, "in_window": False, "busy": "0001_a is busy"})[0],
        )

    def test_current_says_what_it_was_compared_with(self):
        """The sentence names the ref and how it got there, and keeps its start."""
        origin = ig.origin_note(MAIN, {"outcome": "fetched", "attempts": 1, "age": 0.4})
        said, _ = ig.refusal(**{**self.OK, "state": "current", "origin": origin})
        self.assertEqual(
            said,
            "the unit is current against origin/main ccccccc (fetched), which has nothing to integrate",
        )
        self.assertTrue(said.startswith("the unit is current"))

    def test_without_an_origin_the_old_sentence_stands(self):
        self.assertEqual(
            ig.refusal(**{**self.OK, "state": "current"})[0],
            "the unit is current, which has nothing to integrate",
        )
        # Only `current` carries the ref: `unknown` keeps its sentence.
        self.assertEqual(
            ig.refusal(**{**self.OK, "state": "unknown", "origin": "origin/main x"})[0],
            "the unit is unknown, which has nothing to integrate",
        )

    def test_ahead_or_diverged_is_the_completion_road_in_every_state(self):
        for how in ig.COMPLETION:
            for state in ig.STATES:
                with self.subTest(relation=how, state=state):
                    self.assertEqual(
                        ig.refusal(
                            **{**self.OK, "local_head": NEW, "relation": how, "state": state}
                        ),
                        ("", ""),
                    )

    def test_an_unread_relation_is_refused_with_gits_words(self):
        said, code = ig.refusal(
            **{**self.OK, "local_head": NEW, "relation_said": "bad object bbbbbbb"}
        )
        self.assertEqual(
            said,
            "the local head (bbbbbbb) is not the pull request's head (aaaaaaa): bad object bbbbbbb",
        )
        self.assertEqual(code, "")
        # `behind` that could not follow is refused too.
        self.assertIn(
            "not the pull request's head",
            ig.refusal(**{**self.OK, "local_head": NEW, "relation": "behind"})[0],
        )


class Relation(unittest.TestCase):
    """The four answers two `is_ancestor` calls give, and none when git gave none."""

    def test_each(self):
        cases = [
            ((HEAD, HEAD, None, None), "same"),
            ((HEAD, NEW, True, False), "behind"),
            ((NEW, HEAD, False, True), "ahead"),
            ((NEW, HEAD, False, False), "diverged"),
            ((NEW, HEAD, True, True), "behind"),
            ((NEW, HEAD, None, True), ""),
            ((NEW, HEAD, False, None), ""),
        ]
        for args, want in cases:
            with self.subTest(args=args):
                self.assertEqual(ig.relation(*args), want)

    def test_diverged_is_only_a_local_head_on_a_newer_base(self):
        self.assertEqual(ig.relation(NEW, HEAD, False, False, newer=True), "diverged")
        self.assertEqual(ig.relation(NEW, HEAD, False, False, newer=False), "stale")
        self.assertEqual(ig.relation(NEW, HEAD, False, False, newer=None), "")
        # Read only when the heads diverge.
        self.assertEqual(ig.relation(NEW, HEAD, False, True, newer=False), "ahead")
        self.assertEqual(ig.relation(HEAD, NEW, True, False, newer=False), "behind")

    def test_newer_base(self):
        cases = [
            ((NEW, MAIN, True), True),
            ((MAIN, MAIN, True), False),
            ((MAIN, NEW, False), False),
            ((NEW, MAIN, None), None),
            (("", MAIN, True), None),
        ]
        for args, want in cases:
            with self.subTest(args=args):
                self.assertIs(ig.newer_base(*args), want)

    def test_stale_is_refused_and_says_why(self):
        ok = RefusalNamesTheFirstConditionMissing.OK
        for state in ig.STATES:
            with self.subTest(state=state):
                said = ig.refusal(**{**ok, "local_head": NEW, "relation": "stale", "state": state})
                self.assertEqual(
                    said,
                    (
                        f"the local head (bbbbbbb) is not the pull request's head (aaaaaaa): {ig.STALE}",
                        "",
                    ),
                )


class CutIntegration(unittest.TestCase):
    """A `start` of `integrate` that nothing closed, in this unit's run log."""

    U = "0096_x"

    def start(self, stage="integrate", at="2026-09-26T13:12:35+00:00", head=HEAD):
        return {"kind": "start", "unit": self.U, "stage": stage, "at": at, "head": head}

    def test_each(self):
        end = {"kind": "end", "unit": self.U, "stage": "integrate"}
        integration = {"kind": "integration", "unit": self.U}
        cases = [
            ([self.start()], False, {"at": "2026-09-26T13:12:35+00:00", "head": HEAD}),
            ([self.start(), end], False, None),
            ([self.start(), integration], False, None),
            ([self.start()], True, None),
            ([self.start(stage="impl")], False, None),
            (
                [self.start(), end, self.start(at="2026-09-26T14:00:00+00:00", head=NEW)],
                False,
                {"at": "2026-09-26T14:00:00+00:00", "head": NEW},
            ),
            ([], False, None),
        ]
        for records, running, want in cases:
            with self.subTest(records=records, running=running):
                self.assertEqual(ig.cut_integration(records, self.U, running), want)

    def test_another_units_records_are_not_read(self):
        other = {"kind": "end", "unit": "0097_y", "stage": "integrate"}
        self.assertIsNotNone(ig.cut_integration([self.start(), other], self.U, False))


class OriginNote(unittest.TestCase):
    """The four ways a press got its `origin/main`."""

    def test_each_outcome(self):
        cases = [
            ({"outcome": "fetched", "attempts": 1, "age": 0.2}, "origin/main ccccccc (fetched)"),
            (
                {"outcome": "joined", "attempts": 1, "age": 1.5},
                "origin/main ccccccc (joined a running fetch)",
            ),
            (
                {"outcome": "reused", "attempts": 0, "age": 12.3},
                "origin/main ccccccc (reused 12.3s ago)",
            ),
            (
                {"outcome": "failed", "detail": "fatal: could not read"},
                "origin/main ccccccc (fetch failed: fatal: could not read)",
            ),
            (None, "origin/main ccccccc"),
        ]
        for fetch, want in cases:
            with self.subTest(fetch=fetch):
                self.assertEqual(ig.origin_note(MAIN, fetch), want)
        self.assertEqual(ig.origin_note("", None), "origin/main unread")


class NeedsPersonAndOutcome(unittest.TestCase):
    def test_what_needs_a_person_is_the_object_gebo_handed_back(self):
        """One line per item, the commit first when it names one."""
        obj = {
            "needs_person": [
                {"commit": "", "why": "A keeps x, B drops x"},
                {"commit": "abc1234", "why": "second"},
                {"commit": "", "why": " "},
            ]
        }
        self.assertEqual(ig.needs_person_of(obj), ["A keeps x, B drops x", "abc1234: second"])
        self.assertEqual(ig.needs_person_of(None), [])
        self.assertEqual(ig.needs_person_of({"needs_person": []}), [])

    def test_the_outcome_is_read_from_git_then_the_object(self):
        """The order: the head moved, else the object's `needs_person`, else failed."""
        self.assertEqual(ig.outcome_of_session(HEAD, NEW, ["x"]), "pushed")
        self.assertEqual(ig.outcome_of_session(HEAD, NEW, []), "pushed")
        self.assertEqual(ig.outcome_of_session(HEAD, HEAD, []), "failed")
        self.assertEqual(ig.outcome_of_session(HEAD, HEAD, ["x"]), "needs-person")


class Record(unittest.TestCase):
    def test_the_code_is_always_written_and_empty_without_one(self):
        kw = dict(
            workspace="w",
            unit="u",
            pr=7,
            mode="mechanical",
            head_before=HEAD,
            head_after="",
            origin_sha=MAIN,
            outcome="refused",
        )
        self.assertEqual(ig.record(**kw)["code"], "")
        self.assertEqual(ig.record(**kw, code=ig.NOTHING)["code"], "nothing-to-integrate")

    def test_an_unknown_outcome_is_refused(self):
        with self.assertRaises(ValueError):
            ig.record(
                workspace="w",
                unit="u",
                pr=7,
                mode="agent",
                head_before=HEAD,
                head_after="",
                origin_sha=MAIN,
                outcome="ok",
            )

    def test_fetch_merge_state_and_update_branch(self):
        """The three keys are always there, and carry only what they name."""
        rec = ig.record(
            workspace="w",
            unit="u",
            pr=7,
            mode="mechanical",
            head_before=HEAD,
            head_after="",
            origin_sha=MAIN,
            outcome="refused",
        )
        self.assertEqual((rec["fetch"], rec["merge_state"], rec["update_branch"]), (None, "", None))
        rec = ig.record(
            workspace="w",
            unit="u",
            pr=7,
            mode="agent",
            head_before=HEAD,
            head_after="",
            origin_sha=MAIN,
            outcome="failed",
            fetch={"outcome": "fetched", "attempts": 1, "age": 0.3},
            merge_state="BEHIND",
            update_branch={"code": 1, "said": "gh: refused"},
        )
        self.assertEqual(rec["fetch"], {"outcome": "fetched", "age": 0.3})
        self.assertEqual(rec["merge_state"], "BEHIND")
        self.assertEqual(rec["update_branch"], {"code": 1, "said": "gh: refused"})
        rec = ig.record(
            workspace="w",
            unit="u",
            pr=7,
            mode="mechanical",
            head_before=HEAD,
            head_after="",
            origin_sha=MAIN,
            outcome="refused",
            fetch={"outcome": "failed", "detail": "no remote"},
        )
        self.assertEqual(rec["fetch"], {"outcome": "failed", "detail": "no remote"})

    def test_started_by_is_person_unless_named(self):
        """Every integration record says who started it, and only two values."""
        rec = ig.record(
            workspace="w",
            unit="u",
            pr=7,
            mode="mechanical",
            head_before=HEAD,
            head_after="",
            origin_sha=MAIN,
            outcome="refused",
        )
        self.assertEqual(rec["started_by"], "person")
        rec = ig.record(
            workspace="w",
            unit="u",
            pr=7,
            mode="mechanical",
            head_before=HEAD,
            head_after="",
            origin_sha=MAIN,
            outcome="refused",
            started_by="autopilot",
        )
        self.assertEqual(rec["started_by"], "autopilot")
        with self.assertRaises(ValueError):
            ig.record(
                workspace="w",
                unit="u",
                pr=7,
                mode="mechanical",
                head_before=HEAD,
                head_after="",
                origin_sha=MAIN,
                outcome="refused",
                started_by="cron",
            )

    def test_an_old_record_without_them_still_describes(self):
        old = ig.record(
            workspace="w",
            unit="u",
            pr=7,
            mode="mechanical",
            head_before=HEAD,
            head_after=NEW,
            origin_sha=MAIN,
            outcome="pushed",
        )
        for k in ("fetch", "merge_state", "update_branch"):
            del old[k]
        text = ig.describe_for_review(old)
        self.assertIn("the app, mechanically", text)
        self.assertIn(NEW, text)

    def test_a_completion_says_it_pushed_unpushed_commits_and_whose_word_it_is(self):
        rec = ig.record(
            workspace="w",
            unit="u",
            pr=7,
            mode="agent",
            head_before=HEAD,
            head_after=NEW,
            origin_sha=MAIN,
            outcome="pushed",
            completion={"relation": "ahead", "local_head": NEW, "cut": None},
        )
        text = ig.describe_for_review(rec)
        for want in (
            "An integration since the last round",
            "local commits",
            "never been pushed",
            "not a person",
            "no one's approval",
            NEW,
        ):
            self.assertIn(want, text)
        self.assertNotIn("rebased onto", text)
        # `behind` pushed nothing of its own: the old sentence stands.
        rec["completion"]["relation"] = "behind"
        self.assertIn("rebased onto", ig.describe_for_review(rec))


class MergeState(unittest.TestCase):
    """Observed only, so every failure is `""` and nothing raises."""

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


class TheLatestIntegrationSinceTheLastRound(unittest.TestCase):
    """A `pushed` integration counts only when written after the last `review` done."""

    def test_order_decides(self):

        from coscc.store.journal import Journal
        from coscc.github.integration import integration_since_review

        with tempfile.TemporaryDirectory() as d:
            j = Journal(d, d)
            pushed = ig.record(
                workspace="w",
                unit="u",
                pr=7,
                mode="agent",
                head_before=HEAD,
                head_after=NEW,
                origin_sha=MAIN,
                outcome="pushed",
            )
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

        from coscc.agent.policy import grant_for

        seen: dict = {}

        class FakeSessions:
            async def stream(self, cwd, prompt, session_id, **kw):
                seen.update(kw, cwd=cwd)
                gate = kw["gate"]
                seen["push_ok"] = await asks(
                    gate,
                    "Bash",
                    {"command": f"git push --force-with-lease=feat/x:{HEAD} origin feat/x"},
                )
                seen["push_bad"] = await asks(
                    gate, "Bash", {"command": "git push --force origin feat/x"}
                )
                seen["submit"] = await asks(gate, "mcp__cos__submit", {})
                yield ("chunk", "[needs-person] C vs D")
                await _submits(kw, needs_person=[{"commit": "", "why": "A vs B"}])
                yield ("done", {"session_id": "s", "cost": {"usd": 0.1}})

        from coscc.units import submit

        collector = submit.Collector("integrate")

        async def go():
            out = []
            async for item in ig.run_gebo(
                FakeSessions(),
                tree="/t",
                workspace="/w",
                prompt="p",
                grant=grant_for("integrate"),
                lease=("feat/x", HEAD),
                model=None,
                channel=collector,
            ):
                out.append(item)
            return out

        out = asyncio.run(go())
        self.assertEqual(seen["max_turns"], 120)
        self.assertEqual(seen["cwd"], "/t")
        self.assertEqual(seen["push_ok"], "")
        self.assertIn("lease", seen["push_bad"])
        self.assertEqual(seen["submit"], "")
        end = out[-1][1]
        self.assertEqual(end["reply"], "[needs-person] C vs D")
        self.assertEqual(end["denials"], 1)
        # The object's words, never the reply's.
        self.assertEqual(ig.needs_person_of(collector.object()), ["A vs B"])
        self.assertEqual(
            ig.outcome_of_session(HEAD, HEAD, ig.needs_person_of(collector.object())),
            "needs-person",
        )

    def test_gebo_counts_the_background_runs_it_was_refused(self):
        from coscc.agent.policy import grant_for

        class FakeSessions:
            async def stream(self, cwd, prompt, session_id, **kw):
                await asks(kw["gate"], "Bash", {"command": "npm test &"})
                await asks(kw["gate"], "Bash", {"command": "git push --force origin feat/x"})
                await _submits(kw)
                yield ("done", {"session_id": "s", "cost": {}})

        async def go():
            return [
                item
                async for item in ig.run_gebo(
                    FakeSessions(),
                    tree="/t",
                    workspace="/w",
                    prompt="p",
                    grant=grant_for("integrate"),
                    lease=("feat/x", HEAD),
                    model=None,
                )
            ]

        end = asyncio.run(go())[-1][1]
        self.assertEqual((end["denials"], end["background"]), (2, 1))

    def test_gebo_is_told_once(self):
        """Gebo's session ends with its turn, as a board step's does."""
        from coscc.runner.prompt import SESSION_ENDS_ADVICE, SESSION_ENDS_HEADING

        prompt = ig.build_prompt(
            skill="rules",
            unit="0130_x",
            branch="feat/x",
            pr=7,
            state="conflicting",
            reason="r",
            head_before=HEAD,
            origin_sha=MAIN,
            rel={},
            units_root=Path("/u"),
            own_paths={},
        )
        self.assertEqual(prompt.count(SESSION_ENDS_HEADING), 1)
        self.assertIn(SESSION_ENDS_ADVICE, prompt)


if __name__ == "__main__":
    unittest.main()


class Warnings(unittest.TestCase):
    def test_a_press_that_may_fall_to_gebo_says_so(self):
        """A `behind` or `current` press may open Gebo; the page says so, with the grant."""
        said = ig.warnings([], "", True, "W", fallback=True, name="Gebo")
        self.assertEqual(len(said), 2)
        self.assertIn(
            "If GitHub refuses the rebase, the app opens Gebo, a paid agent session, to rebase",
            said[0],
        )
        self.assertIn("pressing Integrate agrees to that session", said[0])
        self.assertEqual(said[1], "W")
        # The name is the table's, handed in; with none the sentence still reads.
        self.assertIn(
            "the app opens a paid agent session to rebase",
            ig.warnings([], "", True, "W", fallback=True)[0],
        )

    def test_changes_asked_and_a_clean_rebase_spends_no_round(self):
        # A patch left unchanged goes back to impl; only a changed one costs a round.
        said = ig.warnings([{"verdict": "changes-requested"}], "changes-requested", False, "W")[0]
        self.assertIn("If integrating leaves the unit's patch unchanged", said)
        self.assertIn("the loop's next still offers impl, and no review round is spent", said)
        self.assertIn(
            "If it changes the patch, next offers review once CI is green, and that round counts toward COS_REVIEW_ROUNDS",
            said,
        )
        self.assertNotIn("offers review, not impl", said)


class ThePrompt(unittest.TestCase):
    def test_a_refused_update_carries_its_code_and_words(self):
        """The exit code and gh's words reach Gebo, and so does what they cannot say."""
        text = ig.build_prompt(
            skill="RULES",
            unit="0035_x",
            branch="feat/x",
            pr=7,
            state="behind",
            reason="r",
            head_before=HEAD,
            origin_sha=MAIN,
            rel={"merged": [], "open": []},
            units_root=Path("/u"),
            own_paths={},
            refused_update={"code": 1, "said": "gh: merge conflict"},
        )
        for want in (
            "# The mechanical rebase was refused",
            "gh pr update-branch 7 --rebase",
            "exited 1",
            "gh: merge conflict",
            "a login, the network, a permission",
        ):
            self.assertIn(want, text)

    def completion_prompt(self, completion):
        return ig.build_prompt(
            skill="RULES",
            unit="0035_x",
            branch="feat/x",
            pr=7,
            state="current",
            reason="",
            head_before=HEAD,
            origin_sha=MAIN,
            rel={"merged": [], "open": []},
            units_root=Path("/u"),
            own_paths={},
            completion=completion,
        )
