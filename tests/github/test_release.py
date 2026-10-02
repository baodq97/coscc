"""The pure decisions of cutting a release, and the version files it edits."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from coscc.github import release
from coscc.loop import run

UNITS = [
    {"name": "0001_a", "type": "feat", "pr": {"number": 11}},
    {"name": "0002_b", "type": "fix", "pr": {"number": 12}},
    {"name": "0003_c", "type": "docs", "pr": None},
]


class Tags(unittest.TestCase):
    def test_candidates_are_release_shaped_and_highest_first(self):
        got = release.candidates(["v0.9.0", "v0.10.0", "v0.10.0-rc.1", "x", "v0.2.10", "v1.0"])
        self.assertEqual(got, ["v0.10.0", "v0.9.0", "v0.2.10"])

    def test_branch_names_round_trip(self):
        self.assertEqual(release.branch_name("0.11.0"), "chore/release-0-11-0")
        self.assertEqual(release.version_of_branch("chore/release-0-11-0"), "0.11.0")
        self.assertEqual(release.version_of_branch("feat/x"), "")


class Matching(unittest.TestCase):
    def test_a_commit_is_a_units_when_its_pr_number_is_the_stores(self):
        got = release.match_commits(
            [
                {"sha": "a" * 40, "subject": "feat: one (#11)"},
                {"sha": "b" * 40, "subject": "fix: two (#12)"},
                {"sha": "c" * 40, "subject": "build(deps): bump (#99)"},
                {"sha": "d" * 40, "subject": "no number"},
            ],
            UNITS,
        )
        self.assertEqual(
            [(u["name"], u["type"], u["pr"]) for u in got["units"]],
            [("0001_a", "feat", 11), ("0002_b", "fix", 12)],
        )
        self.assertEqual([c["sha"][0] for c in got["unmatched"]], ["c", "d"])


class Proposing(unittest.TestCase):
    def test_a_feat_unit_is_a_minor(self):
        self.assertEqual(release.propose("v0.1.0", [{"type": "feat"}], []), ("0.2.0", ""))

    def test_anything_else_is_a_patch(self):
        self.assertEqual(
            release.propose("v0.1.3", [{"type": "fix"}], [{"subject": "build: x"}]), ("0.1.4", "")
        )

    def test_an_unmatched_feat_commit_is_a_minor(self):
        for subject in ("feat: x", "feat(ui): x", "feat!: x", "feat(ui)!: x"):
            with self.subTest(subject=subject):
                self.assertEqual(
                    release.propose("v0.1.3", [], [{"subject": subject}]), ("0.2.0", "")
                )
        self.assertEqual(release.propose("v0.1.3", [], [{"subject": "feature: x"}]), ("0.1.4", ""))

    def test_nothing_new_proposes_nothing(self):
        self.assertEqual(release.propose("v0.1.0", [], []), ("", "nothing new since v0.1.0"))


class Refusing(unittest.TestCase):
    def test_version_problems_in_order(self):
        self.assertIn("not a release tag", release.version_problem("x", 1, "bad", "v0.1.0", True))
        self.assertIn(
            "prerelease", release.version_problem("0.2.0-rc.1", 0, "prerelease", "v0.1.0", True)
        )
        self.assertIn("not greater", release.version_problem("0.1.0", 0, "release", "v0.1.0", True))
        self.assertIn(
            "already on the remote", release.version_problem("0.3.0", 0, "release", "v0.1.0", True)
        )
        self.assertEqual(release.version_problem("0.3.0", 0, "release", "v0.1.0", False), "")

    def test_refusals_in_order(self):
        base = dict(
            active=True,
            phase="prepare",
            open_release_pr={"number": 5},
            unreadable="gh: offline",
            check_version=(1, "off"),
            version_problem_="too low",
            state="nothing",
        )
        expected = [
            "already running",
            "already open: #5",
            "the release could not be read: gh: offline",
            "check-version on origin/main failed: off",
            "too low",
            "has no Prepare button",
        ]
        keys = (
            "active",
            "open_release_pr",
            "unreadable",
            "check_version",
            "version_problem_",
            "state",
        )
        for key, said in zip(keys, expected):
            with self.subTest(first=key):
                self.assertIn(said, release.refusal(**base))
            base[key] = {
                "active": False,
                "open_release_pr": None,
                "unreadable": "",
                "check_version": (0, "0.1.0"),
                "version_problem_": "",
                "state": "ready",
            }[key]
        self.assertEqual(release.refusal(**base), "")

    def test_an_unreadable_workspace_is_not_blamed_on_check_version(self):
        # `gh`'s words used to arrive as "check-version on origin/main failed: …".
        said = release.refusal(
            active=False,
            phase="prepare",
            open_release_pr=None,
            check_version=(1, ""),
            version_problem_="",
            state="unknown",
            unreadable="gh: could not resolve api.github.com",
        )
        self.assertEqual(
            said, "the release could not be read: gh: could not resolve api.github.com"
        )

    def test_an_open_release_pr_does_not_refuse_the_second_press(self):
        self.assertEqual(
            release.refusal(
                active=False,
                phase="publish",
                open_release_pr={"number": 5},
                check_version=(0, "0.1.0"),
                version_problem_="",
                state="pr-open",
            ),
            "",
        )

    def test_checks_red_pending_or_none_close_the_button(self):
        self.assertIn("no required checks", release.checks_problem([]))
        self.assertIn("failed: t", release.checks_problem([{"name": "t", "bucket": "fail"}]))
        self.assertIn(
            "still running: t", release.checks_problem([{"name": "t", "bucket": "pending"}])
        )
        self.assertEqual(
            release.checks_problem(
                [{"name": "t", "bucket": "pass"}, {"name": "s", "bucket": "skipping"}]
            ),
            "",
        )

    def test_publish_needs_the_apps_head_and_no_tag(self):
        green = [{"name": "t", "bucket": "pass"}]
        self.assertIn(
            "not opened by the app", release.publish_problem(green, "a" * 40, "", False, "0.2.0")
        )
        self.assertIn(
            "not the commit the app pushed",
            release.publish_problem(green, "a" * 40, "b" * 40, False, "0.2.0"),
        )
        self.assertIn(
            "already exists", release.publish_problem(green, "a" * 40, "a" * 40, True, "0.2.0")
        )
        self.assertEqual(release.publish_problem(green, "a" * 40, "a" * 40, False, "0.2.0"), "")


class Classifying(unittest.TestCase):
    def facts(self, **kw):
        base = dict(
            last_tag="v0.1.0",
            units=[],
            unmatched=[],
            open_pr=None,
            main_version="0.1.0",
            tags=["v0.1.0"],
            last_record=None,
        )
        return release.classify(**{**base, **kw})

    def test_each_state(self):
        self.assertEqual(self.facts(last_tag="")["state"], "nothing")
        self.assertEqual(self.facts()["state"], "nothing")
        ready = self.facts(units=[{"type": "feat"}])
        self.assertEqual((ready["state"], ready["version"]), ("ready", "0.2.0"))
        opened = self.facts(open_pr={"number": 3, "headRefName": "chore/release-0-2-0"})
        self.assertEqual((opened["state"], opened["version"]), ("pr-open", "0.2.0"))
        merged = self.facts(main_version="0.2.0")
        self.assertEqual((merged["state"], merged["version"]), ("merged-untagged", "0.2.0"))
        tagged = self.facts(last_record={"outcome": "tagged", "version": "0.1.0"})
        self.assertEqual(tagged["state"], "tagged")


class Diffing(unittest.TestCase):
    DIFF = (
        "diff --git a/package.json b/package.json\n--- a/package.json\n+++ b/package.json\n"
        '@@ -3 +3 @@\n-  "version": "0.1.0",\n+  "version": "0.2.0",\n'
        "diff --git a/uv.lock b/uv.lock\n--- a/uv.lock\n+++ b/uv.lock\n"
        '@@ -9 +9 @@\n-version = "0.1.0"\n+version = "0.2.0"\n'
    )

    def test_only_version_lines_pass(self):
        self.assertEqual(release.extra_diff(self.DIFF, "0.1.0", "0.2.0"), [])

    def test_anything_else_is_named(self):
        more = self.DIFF + (
            'diff --git a/uv.lock b/uv.lock\n@@ -20 +20 @@\n-version = "1.0.0"\n+version = "1.1.0"\n'
            "diff --git a/README.md b/README.md\n@@ -1 +1 @@\n-a\n+b\n"
        )
        got = release.extra_diff(more, "0.1.0", "0.2.0")
        self.assertTrue(any("1.0.0" in g for g in got), got)
        self.assertTrue(any(g.startswith("README.md") for g in got), got)


class Recording(unittest.TestCase):
    def test_the_fields_of(self):
        rec = release.record(
            workspace="/w", phase="prepare", version="0.2.0", outcome="opened", pr=3
        )
        for key in (
            "workspace",
            "phase",
            "version",
            "proposed",
            "last_tag",
            "units",
            "commits",
            "pr",
            "head",
            "merge_sha",
            "outcome",
            "detail",
        ):
            self.assertIn(key, rec)
        self.assertEqual((rec["kind"], rec["unit"], rec["stage"]), ("release", "", "release"))

    def test_an_unknown_outcome_or_phase_is_refused(self):
        with self.assertRaises(ValueError):
            release.record(workspace="/w", phase="prepare", version="0.2.0", outcome="pushed")
        with self.assertRaises(ValueError):
            release.record(workspace="/w", phase="later", version="0.2.0", outcome="opened")

    def test_opened_head_is_the_last_for_the_version(self):
        rows = [
            {"kind": "release", "outcome": "opened", "version": "0.2.0", "head": "a", "pr": 1},
            {"kind": "release", "outcome": "opened", "version": "0.3.0", "head": "b", "pr": 2},
            {"kind": "release", "outcome": "opened", "version": "0.2.0", "head": "c", "pr": 3},
        ]
        self.assertEqual(release.opened_head(rows, "0.2.0"), ("c", 3))


PYPROJECT = (
    '[project]\nname = "fixture"\nversion = "0.1.0"\nrequires-python = ">=3.11"\ndependencies = []\n\n'
    '[tool.x]\nversion = "0.1.0"\n'
)
PACKAGE = '{\n  "name": "fixture",\n  "version": "0.1.0",\n  "private": true\n}\n'
LOCK = (
    '{\n  "name": "fixture",\n  "version": "0.1.0",\n  "lockfileVersion": 3,\n  "requires": true,\n'
    '  "packages": {\n    "": {\n      "name": "fixture",\n      "version": "0.1.0"\n    }\n  }\n}\n'
)


class EditingVersions(unittest.TestCase):
    def test_only_the_project_lines_move(self):
        py = release.set_version_text("pyproject.toml", PYPROJECT, "0.1.0", "0.2.0")
        self.assertIn('version = "0.2.0"\nrequires', py)
        self.assertIn('[tool.x]\nversion = "0.1.0"', py)
        lock = json.loads(release.set_version_text("package-lock.json", LOCK, "0.1.0", "0.2.0"))
        self.assertEqual((lock["version"], lock["packages"][""]["version"]), ("0.2.0", "0.2.0"))
        self.assertEqual(
            release.set_version_text("package.json", PACKAGE, "0.1.0", "0.2.0"),
            PACKAGE.replace("0.1.0", "0.2.0"),
        )

    def test_a_missing_line_is_an_error(self):
        with self.assertRaises(release.ReleaseError):
            release.set_version_text("pyproject.toml", PYPROJECT, "9.9.9", "0.2.0")
        with self.assertRaises(release.ReleaseError):
            release.set_version_text("package-lock.json", PACKAGE, "0.1.0", "0.2.0")

    @unittest.skipUnless(shutil.which("uv"), "uv is not on PATH")
    def test_set_versions_runs_uv_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            (tree / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
            (tree / "package.json").write_text(PACKAGE, encoding="utf-8")
            (tree / "package-lock.json").write_text(LOCK, encoding="utf-8")
            subprocess.run(["uv", "lock", "-q"], cwd=tree, check=True, capture_output=True)
            asyncio.run(release.set_versions(tree, "0.1.0", "0.2.0"))
            lock = (tree / "uv.lock").read_text(encoding="utf-8")
            self.assertIn('name = "fixture"\nversion = "0.2.0"', lock)


class AskingTheLoop(unittest.TestCase):
    """`release.cos` runs this app's `python -m coscc.loop` with the checkout as cwd (R8)."""

    def checkout(self, tmp: str, version: str) -> Path:
        tree = Path(tmp)
        (tree / "pyproject.toml").write_text(
            f'[project]\nname = "fixture"\nversion = "{version}"\n', encoding="utf-8"
        )
        (tree / "package.json").write_text(
            json.dumps({"name": "fixture", "version": version}), encoding="utf-8"
        )
        (tree / "uv.lock").write_text(
            f'version = 1\n\n[[package]]\nname = "fixture"\nversion = "{version}"\n',
            encoding="utf-8",
        )
        (tree / "package-lock.json").write_text(
            json.dumps(
                {
                    "name": "fixture",
                    "version": version,
                    "lockfileVersion": 3,
                    "packages": {"": {"name": "fixture", "version": version}},
                }
            ),
            encoding="utf-8",
        )
        return tree

    def test_a_checkout_outside_this_tree_answers_with_its_own_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = self.checkout(tmp, "9.9.9")
            by_hand = run.ask_sync(["check-version"], cwd=tree)
            self.assertEqual((by_hand.code, by_hand.out.strip()), (0, "9.9.9"), by_hand.err)
            self.assertEqual(asyncio.run(release.cos(tree, "check-version")), (0, "9.9.9"))

    def test_the_checkouts_disagreement_is_its_own_words_and_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = self.checkout(tmp, "9.9.9")
            (tree / "package.json").write_text(
                json.dumps({"name": "fixture", "version": "9.9.8"}), encoding="utf-8"
            )
            code, said = asyncio.run(release.cos(tree, "check-version"))
            self.assertNotEqual(code, 0)
            self.assertIn("9.9.8", said)

    def test_a_checkout_without_a_loop_script_is_asked_all_the_same(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = self.checkout(tmp, "9.9.9")
            self.assertFalse((tree / ".claude").exists())
            self.assertEqual(asyncio.run(release.cos(tree, "check-tag", "v9.9.9")), (0, "release"))


if __name__ == "__main__":
    unittest.main()
