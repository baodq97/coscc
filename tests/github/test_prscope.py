"""`pr.md ## Scope of the diff` against the pull request's own counts."""

from __future__ import annotations

import asyncio
import json
import unittest

from coscc.github import prscope

URL = "https://github.com/o/r/pull/7"
SCOPE = {"files": 2, "additions": 10, "deletions": 3, "paths": ["a.py", "b/c.md"]}
GOT = {
    "changedFiles": 2,
    "additions": 10,
    "deletions": 3,
    "files": [{"path": "b/c.md"}, {"path": "a.py"}],
}
GITHUB = {"files": 2, "additions": 10, "deletions": 3}


def run(coro):
    return asyncio.run(coro)


class FakeGh:
    """Answers the one read with `out`, or exits, or raises; records every call."""

    def __init__(self, out=GOT, code=0, err="", raise_=None):
        self.out = out if isinstance(out, str) else json.dumps(out)
        self.code, self.err, self.raise_ = code, err, raise_
        self.calls: list[tuple[list[str], str | None]] = []

    async def __call__(self, argv, cwd, stdin):
        self.calls.append((list(argv), stdin))
        if self.raise_ is not None:
            raise self.raise_
        return self.code, self.out, self.err


class Compare(unittest.TestCase):
    def test_three_counts_and_every_path_equal_is_match(self):
        self.assertEqual(prscope.compare(SCOPE, GOT), {"github": GITHUB, "verdict": "match"})

    def test_each_count_that_differs_is_named(self):
        for field, key in (
            ("files", "changedFiles"),
            ("additions", "additions"),
            ("deletions", "deletions"),
        ):
            with self.subTest(field=field):
                got = {**GOT, key: GOT[key] + 5}
                out = prscope.compare(SCOPE, got)
                self.assertEqual(out["verdict"], "mismatch")
                self.assertEqual(out["differ"], [field])
                self.assertEqual((out["only_in_pr_md"], out["only_on_github"]), ([], []))
                self.assertEqual(out["github"][field], GOT[key] + 5)
        out = prscope.compare({**SCOPE, "files": 0, "deletions": 0}, GOT)
        self.assertEqual(out["differ"], ["files", "deletions"])

    def test_a_path_on_one_side_only_is_mismatch_and_listed(self):
        scope = {**SCOPE, "paths": ["a.py", "stale/only-here.py"]}
        got = {**GOT, "files": [{"path": "a.py"}, {"path": "b/c.md"}, {"path": "z.py"}]}
        out = prscope.compare(scope, got)
        self.assertEqual(out["verdict"], "mismatch")
        self.assertEqual(out["differ"], [])
        self.assertEqual(out["only_in_pr_md"], ["stale/only-here.py"])
        self.assertEqual(out["only_on_github"], ["b/c.md", "z.py"])


class Read(unittest.TestCase):
    def test_gh_failing_timing_out_or_missing_is_unread_with_its_words(self):
        cases = (
            (FakeGh(code=1, err="HTTP 404: Not Found\n"), "HTTP 404: Not Found"),
            (FakeGh(raise_=asyncio.TimeoutError()), "timed out"),
            (FakeGh(raise_=FileNotFoundError()), "gh is not installed"),
            (FakeGh(out="not json"), "did not return JSON"),
        )
        for gh, said in cases:
            with self.subTest(said=said):
                out = run(prscope.read(URL, SCOPE, "/tmp", run=gh))
                self.assertEqual(out["verdict"], "unread")
                self.assertIn(said, out["detail"])

    def test_the_one_command_is_a_read(self):
        gh = FakeGh()
        self.assertEqual(
            run(prscope.read(URL, SCOPE, "/tmp", run=gh)), {"github": GITHUB, "verdict": "match"}
        )
        self.assertEqual(
            gh.calls,
            [(["pr", "view", URL, "--json", "changedFiles,additions,deletions,files"], None)],
        )
        for url in ("", "--repo=x/y", "https://github.com/o/r/issues/7", f"{URL} --web"):
            with self.subTest(url=url):
                gh = FakeGh()
                out = run(prscope.read(url, SCOPE, "/tmp", run=gh))
                self.assertEqual((out["verdict"], gh.calls), ("unread", []))


if __name__ == "__main__":
    unittest.main()
