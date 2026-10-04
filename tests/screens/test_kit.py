"""The shell carries the page kit once, before every feature script, the slots features fill and
a sidebar entry for each feature's page."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from coscc import features, screens
from coscc.plugin import KIT_JS
from coscc.kernel import Feature, Page


class TheShellCarriesTheKit(unittest.TestCase):
    def test_the_kit_comes_once_before_every_feature_script(self):
        shell = json.dumps(screens.index().render(), ensure_ascii=False, default=str)
        marker = "window.coscc = {"
        self.assertEqual(shell.count(marker), 1)
        self.assertIn(marker, KIT_JS)
        kit_at = shell.index(marker)
        for f in features.FEATURES:
            for js in f.scripts:
                # The page holds each script as a string literal, and the dump escapes it again.
                twice = json.dumps(
                    json.dumps(js.strip(), ensure_ascii=False)[1:-1], ensure_ascii=False
                )
                self.assertLess(kit_at, shell.index(twice[1:-1]), f"{f.name}'s script")

    def test_both_slots_are_in_the_page(self):
        shell = json.dumps(screens.index().render(), ensure_ascii=False, default=str)
        for slot in ("slot-topbar", "slot-unit"):
            self.assertIn(slot, shell)


class AFeaturesPageIsInTheFrame(unittest.TestCase):
    def test_a_page_gets_a_sidebar_entry_on_both_navigations_and_a_frame(self):
        page = Page("Planted", "key-round", "/planted")
        planted = Feature("planted", routes=lambda ctx: (), page=page)
        with mock.patch.object(features, "FEATURES", (*features.FEATURES, planted)):
            shell = json.dumps(screens.index().render(), ensure_ascii=False, default=str)
        self.assertIn("nav-feature-planted", shell)
        self.assertIn("mobile-nav-feature-planted", shell)
        self.assertIn("feature-frame", shell)

    def test_a_feature_without_a_page_gets_no_entry(self):
        shell = json.dumps(screens.index().render(), ensure_ascii=False, default=str)
        for f in features.FEATURES:
            if f.page is None:
                self.assertNotIn(f"nav-feature-{f.name}", shell)
