"""`0055` R3, R5, R6: the title and body of `pr.md` put onto its pull request."""

from __future__ import annotations

import asyncio
import json
import unittest

from coscc import prsync

URL = "https://github.com/o/r/pull/7"
TITLE = "the pr body is taken from pr.md"
BODY = "## Where\n\nchecks pending.\n"


def run(coro):
    return asyncio.run(coro)


class FakeGh:
    """Holds one pull request's title and body in memory; records every argv and stdin."""

    def __init__(self, title="temporary", body="temporary", fail=None, raise_=None):
        self.title, self.body = title, body
        self.calls: list[tuple[list[str], str | None]] = []
        self.fail = fail  # which subcommand exits 1
        self.raise_ = raise_

    async def __call__(self, argv, cwd, stdin):
        self.calls.append((list(argv), stdin))
        if self.raise_ is not None:
            raise self.raise_
        if self.fail and argv[:2] == ["pr", self.fail]:
            return 1, "", "HTTP 403: nope"
        if argv[:2] == ["pr", "view"]:
            return 0, json.dumps({"title": self.title, "body": self.body}), ""
        if argv[:2] == ["pr", "edit"]:
            for a in argv:
                if a.startswith("--title="):
                    self.title = a[len("--title="):]
            self.body = stdin
            return 0, URL + "\n", ""
        return 2, "", "unexpected"

    def edits(self):
        return [c for c in self.calls if c[0][:2] == ["pr", "edit"]]


class ItPutsPrMdOnThePullRequest(unittest.TestCase):
    def test_a_temporary_description_is_replaced_with_title_and_body(self):
        gh = FakeGh()
        got = run(prsync.sync(URL, TITLE, BODY, "/tmp", run=gh))
        self.assertEqual(got.state, "updated")
        [(argv, stdin)] = gh.edits()
        self.assertEqual(argv, ["pr", "edit", URL, f"--title={TITLE}", "--body-file", "-"])
        self.assertEqual(stdin, BODY)
        self.assertEqual((gh.title, gh.body), (TITLE, BODY))

    def test_already_there_writes_nothing(self):
        gh = FakeGh(title=TITLE, body=BODY)
        self.assertEqual(run(prsync.sync(URL, TITLE, BODY, "/tmp", run=gh)).state, "already")
        self.assertEqual(gh.edits(), [])

    def test_line_endings_and_trailing_whitespace_are_not_a_difference(self):
        gh = FakeGh(title=TITLE + "  ", body=BODY.replace("\n", "\r\n").rstrip())
        self.assertEqual(run(prsync.sync(URL, TITLE, BODY, "/tmp", run=gh)).state, "already")
        self.assertEqual(gh.edits(), [])

    def test_no_title_leaves_the_title_alone(self):
        gh = FakeGh(title="something else", body=BODY)
        self.assertEqual(run(prsync.sync(URL, None, BODY, "/tmp", run=gh)).state, "already")
        gh = FakeGh(title="something else")
        self.assertEqual(run(prsync.sync(URL, None, BODY, "/tmp", run=gh)).state, "updated")
        [(argv, _)] = gh.edits()
        self.assertFalse(any(a.startswith("--title") for a in argv), argv)
        self.assertEqual(gh.title, "something else")

    def test_a_title_starting_with_a_dash_stays_inside_its_argv(self):
        gh = FakeGh()
        run(prsync.sync(URL, "--base=evil", BODY, "/tmp", run=gh))
        [(argv, _)] = gh.edits()
        self.assertIn("--title=--base=evil", argv)
        self.assertNotIn("--base=evil", argv)


class ItNeverRaisesAndSaysWhy(unittest.TestCase):
    def test_view_failing_is_failed_with_ghs_words_and_no_edit(self):
        gh = FakeGh(fail="view")
        got = run(prsync.sync(URL, TITLE, BODY, "/tmp", run=gh))
        self.assertEqual((got.state, got.reason), ("failed", "HTTP 403: nope"))
        self.assertEqual(gh.edits(), [])

    def test_edit_failing_is_failed(self):
        got = run(prsync.sync(URL, TITLE, BODY, "/tmp", run=FakeGh(fail="edit")))
        self.assertEqual((got.state, got.reason), ("failed", "HTTP 403: nope"))

    def test_a_timeout_is_failed_and_says_so(self):
        got = run(prsync.sync(URL, TITLE, BODY, "/tmp", run=FakeGh(raise_=asyncio.TimeoutError())))
        self.assertEqual(got.state, "failed")
        self.assertIn("timed out", got.reason)

    def test_no_gh_is_failed(self):
        got = run(prsync.sync(URL, TITLE, BODY, "/tmp", run=FakeGh(raise_=FileNotFoundError())))
        self.assertEqual(got.state, "failed")
        self.assertIn("not installed", got.reason)

    def test_view_that_is_not_json_is_failed(self):
        async def junk(argv, cwd, stdin):
            return 0, "not json", ""

        self.assertEqual(run(prsync.sync(URL, TITLE, BODY, "/tmp", run=junk)).state, "failed")

    def test_a_url_that_is_not_a_pull_request_calls_nothing(self):
        gh = FakeGh()
        for url in ("", "-R o/r", "https://x/pull/7"):
            self.assertEqual(run(prsync.sync(url, TITLE, BODY, "/tmp", run=gh)).state, "failed")
        self.assertEqual(gh.calls, [])


class OnlyTwoCommandsAndTheirWords(unittest.TestCase):
    """R6: `pr view` and `pr edit`, and no word outside this set but one `--title=`."""

    WORDS = {"pr", "view", "edit", "--json", "title,body", "--body-file", "-", URL}

    def test_every_argv_holds_only_the_named_words(self):
        for gh in (FakeGh(), FakeGh(title=TITLE, body=BODY), FakeGh(fail="edit")):
            run(prsync.sync(URL, TITLE, BODY, "/tmp", run=gh))
            self.assertTrue(gh.calls)
            for argv, _ in gh.calls:
                extra = [a for a in argv if a not in self.WORDS]
                self.assertIn(extra, ([], [f"--title={TITLE}"]), argv)


if __name__ == "__main__":
    unittest.main()
