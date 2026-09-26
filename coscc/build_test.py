"""Tests for the fingerprint. None of these run a real build.

`spec.md` C3 is the thing being defended: a stale bundle must not read as current. The
states are kept apart on purpose — "no build" and "a build nobody fingerprinted" are
different problems with different fixes, and collapsing them would hide one.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from coscc import build
from coscc.config import Config


def _fake_source_tree(root: Path, page: str = "page v1", rx: str = "cfg v1") -> None:
    (root / "coscc").mkdir(parents=True, exist_ok=True)
    for source in build._SOURCES:
        (root / source).write_text("presentation v1")
    (root / "coscc" / "coscc.py").write_text(page)
    (root / "rxconfig.py").write_text(rx)


class WhatCountsAsCurrent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.built = Path(self.tmp.name) / "built"
        self.built.mkdir(parents=True)
        (self.built / "index.html").write_text("<html></html>")
        _fake_source_tree(self.root)
        self.config = Config(host="127.0.0.1", port=8790)
        self.addCleanup(self.tmp.cleanup)

    def test_a_fresh_fingerprint_reads_as_current(self):
        build.write_marker(self.built, self.config, root=self.root)
        state, msg = build.check(self.config, self.built, root=self.root)
        self.assertEqual(state, build.OK)
        self.assertEqual(msg, "")

    def test_editing_the_page_makes_it_stale_and_names_the_file(self):
        build.write_marker(self.built, self.config, root=self.root)
        _fake_source_tree(self.root, page="page v2")
        state, msg = build.check(self.config, self.built, root=self.root)
        self.assertEqual(state, build.STALE)
        self.assertIn("coscc/coscc.py", msg)
        self.assertIn("coscc-build", msg)

    def test_editing_rxconfig_makes_it_stale(self):
        build.write_marker(self.built, self.config, root=self.root)
        _fake_source_tree(self.root, rx="cfg v2")
        self.assertEqual(
            build.check(self.config, self.built, root=self.root)[0], build.STALE
        )

    def test_editing_any_presentation_module_makes_it_stale(self):
        for source in build._SOURCES:
            with self.subTest(source=source):
                build.write_marker(self.built, self.config, root=self.root)
                path = self.root / source
                path.write_text(path.read_text() + "\nchanged")
                state, message = build.check(self.config, self.built, root=self.root)
                self.assertEqual(state, build.STALE)
                self.assertIn(source, message)

    def test_a_different_port_is_stale_and_says_both_ports(self):
        """The 2026-09-21 failure, as a unit test.

        In a checkout the bundle hardcodes the backend address and stays that way, so a
        build aimed at another port renders a page that never connects. The message has
        to name both numbers or the reader cannot tell which one to change. A packaged
        install never gets here -- `coscc/frontend.py` rewrites the address instead.
        """
        build.write_marker(self.built, self.config, root=self.root)
        state, msg = build.check(
            Config(host="127.0.0.1", port=8792), self.built, root=self.root
        )
        self.assertEqual(state, build.STALE)
        self.assertIn("8790", msg)
        self.assertIn("8792", msg)

    def test_a_different_reflex_version_is_stale(self):
        build.write_marker(self.built, self.config, root=self.root)
        data = build.read_marker(self.built)
        data["reflex"] = "0.0.0-not-this-one"
        build.marker_path(self.built).write_text(json.dumps(data))
        state, msg = build.check(self.config, self.built, root=self.root)
        self.assertEqual(state, build.STALE)
        self.assertIn("reflex", msg)


class StatesKeptApart(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.built = Path(self.tmp.name) / "built"
        self.built.mkdir(parents=True)
        _fake_source_tree(self.root)
        self.config = Config()
        self.addCleanup(self.tmp.cleanup)

    def test_no_output_at_all_is_unbuilt(self):
        state, msg = build.check(self.config, self.built, root=self.root)
        self.assertEqual(state, build.UNBUILT)
        self.assertIn("coscc-build", msg)

    def test_output_without_a_fingerprint_is_missing_not_unbuilt(self):
        # Someone ran `reflex export` by hand. There is a page, but nothing says what it
        # was made from, so it cannot be trusted — and that is a different fix from
        # "there is no page".
        (self.built / "index.html").write_text("<html></html>")
        state, msg = build.check(self.config, self.built, root=self.root)
        self.assertEqual(state, build.MISSING)
        self.assertIn("coscc-build", msg)

    def test_a_corrupt_fingerprint_reads_as_missing_rather_than_crashing(self):
        (self.built / "index.html").write_text("<html></html>")
        build.marker_path(self.built).write_text("{not json")
        self.assertEqual(
            build.check(self.config, self.built, root=self.root)[0], build.MISSING
        )


class WhatTheFingerprintCovers(unittest.TestCase):
    def test_it_hashes_the_page_and_the_reflex_config(self):
        # If this list grows, the docstring in build.py has to say so — a fingerprint
        # that silently covers less than it claims is worse than none.
        self.assertEqual(
            set(build._SOURCES), {
                "coscc/coscc.py", "coscc/ui.py", "coscc/studio.py",
                "coscc/screens.py", "coscc/state.py", "rxconfig.py",
            }
        )

    def test_a_missing_source_file_is_recorded_not_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            digest = build.source_digest(Path(d))
        self.assertEqual(set(digest.values()), {"missing"})

    def test_every_source_it_lists_is_a_file_in_this_checkout(self):
        """A path left behind by a rename hashes as `missing` for ever, and a missing file
        never changes, so the fingerprint would call a stale bundle current."""
        repo = Path(__file__).resolve().parent.parent
        self.assertEqual([s for s in build._SOURCES if not (repo / s).is_file()], [])


if __name__ == "__main__":
    unittest.main()
