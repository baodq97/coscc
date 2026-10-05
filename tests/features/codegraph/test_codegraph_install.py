"""Tests for `install` and `installed` in `coscc/features/codegraph/install.py`.

No network and no real library: a home is a temp dir, the Node binary a shell script that prints a
version, and npm a function that either builds that tree or fails."""

from __future__ import annotations

import json
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from coscc.features.codegraph.graph import (
    BRIDGE_JS,
    PACKAGE_JSON,
    PACKAGE_LOCK,
    VERSION,
    binary_path,
    install,
    installed,
)


def tree(
    home: Path,
    *,
    node: str | None = "#!/bin/sh\necho v24.16.0\n",
    version: str = VERSION,
    lock: str = PACKAGE_LOCK,
) -> None:
    """What a good `npm ci` leaves in `home`, with `node` as the binary's text (None: no binary)."""
    pkg = home / "node_modules" / "@colbymchenry" / "codegraph"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "package.json").write_text(json.dumps({"version": version}))
    (home / "package-lock.json").write_text(lock)
    if node is not None:
        binary = binary_path(home)
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text(node)
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)


def reporting(version: str) -> str:
    return f"#!/bin/sh\necho {version}\n"


class Home(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name) / "codegraph"


class AnInstallThatIsThere(Home):
    def test_a_node_in_range_gives_its_path(self):
        for version in ("v22.16.0", "v24.16.0", "v24.20.0"):
            with self.subTest(version=version):
                tree(self.home, node=reporting(version))
                self.assertEqual(installed(self.home), binary_path(self.home))

    def test_a_node_out_of_range_gives_a_reason_naming_the_version(self):
        for version in ("v22.15.0", "v25.0.0", "v18.20.1"):
            with self.subTest(version=version):
                tree(self.home, node=reporting(version))
                reason = installed(self.home)
                self.assertIsInstance(reason, str)
                self.assertIn(version[1:], reason)

    def test_output_that_is_no_version_gives_a_reason(self):
        tree(self.home, node=reporting("hello"))
        self.assertIsInstance(installed(self.home), str)

    def test_a_binary_that_exits_non_zero_gives_a_reason(self):
        tree(self.home, node="#!/bin/sh\necho v24.16.0\nexit 3\n")
        self.assertIsInstance(installed(self.home), str)

    def test_a_binary_that_cannot_be_executed_gives_a_reason(self):
        tree(self.home)
        binary_path(self.home).chmod(0o644)
        self.assertIsInstance(installed(self.home), str)

    def test_a_binary_that_hangs_gives_a_reason(self):
        tree(self.home)

        def slow(argv, **kw):
            raise subprocess.TimeoutExpired(argv, kw["timeout"])

        self.assertIsInstance(installed(self.home, run=slow), str)

    def test_a_missing_binary_gives_a_reason(self):
        tree(self.home, node=None)
        self.assertIsInstance(installed(self.home), str)

    def test_a_library_of_another_version_gives_a_reason(self):
        tree(self.home, version="1.6.0")
        self.assertIn(VERSION, str(installed(self.home)))

    def test_a_lockfile_that_differs_gives_a_reason(self):
        tree(self.home, lock=PACKAGE_LOCK.replace("1.6.1", "1.6.2"))
        self.assertIsInstance(installed(self.home), str)

    def test_nothing_installed_gives_a_reason_and_creates_nothing(self):
        self.assertIsInstance(installed(self.home), str)
        self.assertFalse(self.home.exists())

    def test_a_reason_is_one_sentence_without_a_path(self):
        tree(self.home, node=reporting("v25.0.0"))
        reason = installed(self.home)
        self.assertIsInstance(reason, str)
        self.assertNotIn(str(self.home), reason)
        self.assertEqual(reason.count("\n"), 0)

    def test_node_on_the_path_is_never_used(self):
        tree(self.home, node=None)
        calls: list[list[str]] = []

        def run(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "v24.16.0\n", "")

        self.assertIsInstance(installed(self.home, run=run), str)
        self.assertEqual(calls, [])


class AnInstall(Home):
    def test_without_npm_it_gives_a_reason_and_writes_and_runs_nothing(self):
        ran: list[list[str]] = []

        def run(argv, **kw):
            ran.append(argv)
            raise AssertionError("nothing may run")

        reason = install(self.home, which=lambda name: None, run=run)
        self.assertEqual(reason, "npm is not installed, so codegraph cannot be installed.")
        self.assertFalse(self.home.exists())
        self.assertEqual(ran, [])

    def test_it_writes_the_manifest_lockfile_and_bridge_and_runs_npm_ci(self):
        seen: dict[str, object] = {}

        def run(argv, **kw):
            if argv[0] == "npm":
                seen.update(argv=argv, kw=kw, files=sorted(p.name for p in self.home.iterdir()))
                tree(self.home)
                return subprocess.CompletedProcess(argv, 0, "", "")
            return subprocess.run(argv, **kw)

        got = install(self.home, which=lambda name: "/usr/bin/npm", run=run)
        self.assertEqual(got, binary_path(self.home))
        self.assertEqual(
            seen["argv"],
            [
                "npm",
                "ci",
                "--prefix",
                str(self.home),
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
            ],
        )
        kw = seen["kw"]
        assert isinstance(kw, dict)
        self.assertNotIn("shell", kw)
        self.assertTrue(kw["capture_output"])
        self.assertEqual(seen["files"], ["bridge.mjs", "package-lock.json", "package.json"])
        self.assertEqual((self.home / "package.json").read_text(), PACKAGE_JSON)
        self.assertEqual((self.home / "package-lock.json").read_text(), PACKAGE_LOCK)
        self.assertEqual((self.home / "bridge.mjs").read_text(), BRIDGE_JS)

    def test_the_manifest_pins_one_dependency(self):
        self.assertEqual(
            json.loads(PACKAGE_JSON),
            {
                "name": "coscc-codegraph",
                "private": True,
                "dependencies": {"@colbymchenry/codegraph": VERSION},
            },
        )

    def test_the_lockfile_pins_the_same_version(self):
        lock = json.loads(PACKAGE_LOCK)
        self.assertEqual(lock["lockfileVersion"], 3)
        self.assertEqual(
            lock["packages"]["node_modules/@colbymchenry/codegraph"]["version"], VERSION
        )

    def test_a_failing_npm_gives_a_reason_ending_with_the_tail_of_its_output(self):
        noise = "progress " * 200
        output = f"{noise}\nnpm error 404 not found\nnpm error the end"

        def run(argv, **kw):
            return subprocess.CompletedProcess(argv, 1, "", output)

        reason = install(self.home, which=lambda name: "/usr/bin/npm", run=run)
        self.assertIsInstance(reason, str)
        self.assertTrue(reason.endswith("npm error the end"))
        self.assertIn("npm error 404", reason)
        self.assertNotIn(noise, reason)

    def test_an_npm_that_cannot_start_or_hangs_gives_a_reason(self):
        for failure in (OSError("no exec"), subprocess.TimeoutExpired(["npm"], 600)):
            with self.subTest(failure=type(failure).__name__):

                def run(argv, **kw):
                    raise failure

                reason = install(self.home, which=lambda name: "/usr/bin/npm", run=run)
                self.assertIsInstance(reason, str)

    def test_an_install_that_does_not_pass_the_check_gives_its_reason(self):
        def run(argv, **kw):
            if argv[0] == "npm":
                tree(self.home, node=reporting("v25.1.0"))
                return subprocess.CompletedProcess(argv, 0, "", "")
            return subprocess.run(argv, **kw)

        reason = install(self.home, which=lambda name: "/usr/bin/npm", run=run)
        self.assertIsInstance(reason, str)
        self.assertIn("25.1.0", reason)


if __name__ == "__main__":
    unittest.main()
