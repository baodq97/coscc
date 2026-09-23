"""`0021` D1 and D2: the body a round becomes, and posting it exactly once."""

from __future__ import annotations

import asyncio
import json
import re
import unittest

from coscc import prcomment

UNIT = "0015_review-cannot-stop-a-merge"
URL = "https://github.com/o/r/pull/33"
ROUND = (
    "## Round 1\n\nReviewed: abcdef1. Verdict: changes-requested.\n\n"
    "### Findings\n\n"
    "- F1 [open] [high] the pr policy lets `gh api` through\n"
    "- F2 [open] [high] the ship gate reads the remote branch without a fetch\n"
    "- F3 [open] [low] a *typo*\n\n"
    "### What was not reviewed\n\nnothing"
)
FINDINGS = [line for line in ROUND.splitlines() if line.startswith("- F")]
FIRST = re.compile(
    r"^\*\*coscc review, round (\d+) of (\S+)\.\*\* Written by an agent session, not a person\. "
    r"This comment is not an approval\.$"
)


def run(coro):
    return asyncio.run(coro)


class FakeGh:
    """Records every argv; holds the pull request's comments in memory."""

    def __init__(self, comments=None, fail=None, raise_=None):
        self.comments = list(comments or [])
        self.calls: list[list[str]] = []
        self.fail = fail  # which subcommand exits 1
        self.raise_ = raise_

    async def __call__(self, argv, cwd, stdin):
        self.calls.append(list(argv))
        if self.raise_ is not None:
            raise self.raise_
        if self.fail and argv[:2] == ["pr", self.fail]:
            return 1, "", "HTTP 403: nope"
        if argv[:2] == ["pr", "view"]:
            return 0, json.dumps({"comments": self.comments}), ""
        if argv[:2] == ["pr", "comment"]:
            url = f"{URL}#issuecomment-{len(self.comments) + 1}"
            self.comments.append({"body": stdin, "url": url})
            return 0, url + "\n", ""
        return 2, "", "unexpected"

    def posts(self):
        return [c for c in self.calls if c[:2] == ["pr", "comment"]]


class TheBodySaysWhereItCameFromAndCarriesTheWholeRound(unittest.TestCase):
    def test_the_first_line_names_round_unit_agent_and_not_an_approval(self):
        first = prcomment.body(UNIT, 1, "changes-requested", ROUND).splitlines()[0]
        m = FIRST.match(first)
        self.assertIsNotNone(m, first)
        self.assertEqual(m.groups(), ("1", UNIT))

    def test_every_finding_is_in_the_body_verbatim(self):
        b = prcomment.body(UNIT, 1, "changes-requested", ROUND)
        self.assertEqual(len(FINDINGS), 3)
        for f in FINDINGS:
            self.assertIn(f, b)
        self.assertIn("Verdict: changes-requested", b)

    def test_the_same_round_gives_the_same_body(self):
        self.assertEqual(
            prcomment.body(UNIT, 1, "pass", ROUND), prcomment.body(UNIT, 1, "pass", ROUND)
        )

    def test_the_last_non_empty_line_is_the_marker(self):
        b = prcomment.body(UNIT, 2, None, ROUND)
        last = [line for line in b.splitlines() if line.strip()][-1]
        self.assertEqual(last, f"<!-- coscc-review unit={UNIT} round=2 -->")
        self.assertIn("Verdict: unreadable", b)


class PostingHappensOnceAndNeverAsAReview(unittest.TestCase):
    def test_a_first_post_comments_exactly_once_and_returns_its_url(self):
        gh = FakeGh()
        r = run(prcomment.post(UNIT, 1, "changes-requested", ROUND, URL, "/tmp", run=gh))
        self.assertEqual(r.state, "posted")
        self.assertEqual(r.url, f"{URL}#issuecomment-1")
        self.assertEqual(len(gh.posts()), 1)
        self.assertEqual(gh.posts()[0], ["pr", "comment", URL, "--body-file", "-"])

    def test_a_round_already_marked_is_not_posted_again(self):
        gh = FakeGh()
        run(prcomment.post(UNIT, 1, "changes-requested", ROUND, URL, "/tmp", run=gh))
        r = run(prcomment.post(UNIT, 1, "changes-requested", ROUND, URL, "/tmp", run=gh))
        self.assertEqual((r.state, r.url), ("already", f"{URL}#issuecomment-1"))
        self.assertEqual(len(gh.posts()), 1)

    def test_a_marker_quoted_mid_comment_does_not_count(self):
        quoted = {"body": f"see {prcomment.marker(UNIT, 1)} above\nand more", "url": "x"}
        gh = FakeGh(comments=[quoted])
        r = run(prcomment.post(UNIT, 1, "pass", ROUND, URL, "/tmp", run=gh))
        self.assertEqual(r.state, "posted")

    def test_another_round_of_the_same_unit_is_its_own_comment(self):
        gh = FakeGh()
        run(prcomment.post(UNIT, 1, "changes-requested", ROUND, URL, "/tmp", run=gh))
        r = run(prcomment.post(UNIT, 2, "pass", ROUND, URL, "/tmp", run=gh))
        self.assertEqual(r.state, "posted")
        self.assertEqual(len(gh.posts()), 2)

    def test_every_failure_is_a_result_with_a_reason_never_a_raise(self):
        cases = {
            "no gh": FakeGh(raise_=FileNotFoundError("gh")),
            "timeout": FakeGh(raise_=asyncio.TimeoutError()),
            "view exits 1": FakeGh(fail="view"),
            "comment exits 1": FakeGh(fail="comment"),
        }
        for name, gh in cases.items():
            with self.subTest(name):
                r = run(prcomment.post(UNIT, 1, "pass", ROUND, URL, "/tmp", run=gh))
                self.assertEqual(r.state, "failed")
                self.assertTrue(r.reason)

    def test_unreadable_json_is_a_failure(self):
        async def gh(argv, cwd, stdin):
            return 0, "not json", ""

        r = run(prcomment.post(UNIT, 1, "pass", ROUND, URL, "/tmp", run=gh))
        self.assertEqual(r.state, "failed")
        self.assertIn("JSON", r.reason)

    def test_no_url_or_a_bad_one_never_reaches_gh(self):
        for url in (None, "", "--repo=evil/x", "https://github.com/o/r/pull/7 --x", "https://github.com/o/r/issues/7"):
            with self.subTest(url):
                gh = FakeGh()
                r = run(prcomment.post(UNIT, 1, "pass", ROUND, url, "/tmp", run=gh))
                self.assertEqual(r.state, "failed")
                self.assertTrue(r.reason)
                self.assertEqual(gh.calls, [])
        r = run(prcomment.post(UNIT, 1, "pass", ROUND, None, "/tmp", run=FakeGh()))
        self.assertEqual(r.reason, "pr.md names no pull request")

    def test_no_path_ever_calls_gh_pr_review(self):
        for gh in (FakeGh(), FakeGh(fail="comment"), FakeGh(fail="view")):
            run(prcomment.post(UNIT, 1, "pass", ROUND, URL, "/tmp", run=gh))
            run(prcomment.post(UNIT, 1, "pass", ROUND, URL, "/tmp", run=gh))
            for argv in gh.calls:
                self.assertNotIn("review", argv[:2])

    def test_the_module_has_no_gh_pr_review_in_it(self):
        from pathlib import Path

        source = Path(prcomment.__file__).read_text(encoding="utf-8")
        code = source.split('"""', 2)[2]  # past the docstring, which names what it refuses
        self.assertNotIn('"review"', code)


if __name__ == "__main__":
    unittest.main()
