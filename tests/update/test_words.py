"""Tests for `coscc/update/updater.py`'s words."""

from __future__ import annotations

import unittest


class TheUpdateWords(unittest.TestCase):
    def test_the_update_words_name_no_variable_and_offer_only_usable_buttons(self):
        import re

        from coscc.update.updater import update_words

        base = {"shape": "service", "state": "idle", "version": "0.12.0", "commit": "a" * 40}
        states = {
            "unconfigured": (
                {
                    "release": {"state": "up-to-date"},
                    "local": {"state": "unconfigured", "reason": "COS_UPDATE_LOCAL_FROM chưa đặt"},
                },
                set(),
            ),
            "no working folder": (
                {
                    "release": {"state": "up-to-date"},
                    "local": {"state": "unconfigured", "reason": "chưa có working folder"},
                },
                set(),
            ),
            "nothing newer": (
                {"release": {"state": "up-to-date"}, "local": {"state": "idle", "workspace": "p"}},
                {"build-local"},
            ),
            "newer ready": (
                {
                    "release": {"state": "ready", "version": "0.13.0"},
                    "local": {"state": "idle", "workspace": "p"},
                },
                {"apply-release", "build-local"},
            ),
        }
        vietnamese = re.compile(
            r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", re.I
        )
        for name, (status, usable) in states.items():
            with self.subTest(name):
                words = update_words({**base, **status})
                self.assertTrue(words["line"])
                self.assertNotIn("COS_", words["line"])
                self.assertIsNone(vietnamese.search(words["line"]))
                self.assertEqual(set(words["actions"]), usable)


class WhatThePanelSays(unittest.TestCase):
    def test_the_updates_section_shows_one_apply_per_channel(self):
        from coscc.update.updater import update_words

        words = update_words(
            {
                "shape": "service",
                "state": "idle",
                "release": {"state": "ready", "version": "0.13.0"},
                "local": {"state": "ready", "version": "0.12.0+gabc"},
            }
        )
        self.assertEqual(
            [a for a in words["actions"] if a.startswith(("apply-", "now-"))],
            ["apply-release", "apply-local"],
        )

    def test_pending_says_what_it_waits_for_in_one_sentence(self):
        from coscc.update.updater import update_words
        from coscc.update import updater

        words = update_words(
            {"shape": "service", "state": "pending", "release": {"state": "ready"}, "local": {}}
        )
        self.assertEqual(
            words["line"], "An update waits for an integration or a screenshot retake to finish."
        )
        self.assertEqual(words["line"], updater.WAITING_WARNING)
        self.assertEqual(words["line"].count("."), 1)
        self.assertNotIn("apply-release", words["actions"])
        self.assertIn("cancel", words["actions"])
        self.assertEqual(
            update_words({"shape": "service", "state": "applying", "release": {}, "local": {}})[
                "line"
            ],
            "Updating now.",
        )
