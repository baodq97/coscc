"""The shell carries the page kit once, before every feature script, and the slots features fill."""

from __future__ import annotations

import json
import unittest

from coscc import features, screens
from coscc.plugin import KIT_JS


class TheShellCarriesTheKit(unittest.TestCase):
    def test_the_kit_comes_once_before_every_feature_script(self):
        shell = json.dumps(screens.index().render(), ensure_ascii=False, default=str)
        marker = "window.coscc = {"
        self.assertEqual(shell.count(marker), 1)
        self.assertIn(marker, KIT_JS)
        kit_at = shell.index(marker)
        for f in features.FEATURES:
            for js in f.scripts:
                self.assertLess(kit_at, shell.index(js.strip().splitlines()[1].strip()))

    def test_both_slots_are_in_the_page(self):
        shell = json.dumps(screens.index().render(), ensure_ascii=False, default=str)
        for slot in ("slot-topbar", "slot-unit"):
            self.assertIn(slot, shell)
