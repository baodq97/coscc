"""Tests for `coscc/service/update.py`'s words, split from `tests/service/test_service.py`."""

from __future__ import annotations

import unittest


class TheUpdateWords(unittest.TestCase):
    def test_the_update_words_name_no_variable_and_offer_only_usable_buttons(self):
        import re

        from coscc.service.update import update_words

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
