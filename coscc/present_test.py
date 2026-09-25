"""`0082` plan step 3: the one time format, the short sha, and the label tables."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from coscc import backlog, present, service

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


class When(unittest.TestCase):
    def test_an_iso_time_minutes_ago_reads_relative(self):
        self.assertEqual(present.when("2026-09-25T11:55:00+00:00", NOW), "5 min ago")

    def test_an_iso_time_without_a_zone_is_utc(self):
        self.assertEqual(present.when("2026-09-25T10:00:00", NOW), "2 h ago")

    def test_epoch_milliseconds_as_an_int_and_as_thirteen_digits(self):
        ms = int((NOW - timedelta(minutes=5)).timestamp() * 1000)
        self.assertEqual(present.when(ms, NOW), "5 min ago")
        self.assertEqual(present.when(str(ms), NOW), "5 min ago")
        self.assertEqual(len(str(ms)), 13)

    def test_three_days_ago_reads_as_a_short_local_date(self):
        text = present.when("2026-09-22T12:00:00+00:00", NOW)
        local = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc).astimezone()
        self.assertEqual(text, f"{local.strftime('%b')} {local.day}, {local.strftime('%H:%M')}")
        self.assertNotIn("T", text)
        self.assertNotIn("2026", text)

    def test_nothing_reads_as_nothing(self):
        self.assertEqual(present.when("", NOW), "")
        self.assertEqual(present.when(None, NOW), "")

    def test_a_string_that_is_no_time_is_left_as_it_is(self):
        self.assertEqual(present.when("yesterday-ish", NOW), "yesterday-ish")


class ShortSha(unittest.TestCase):
    def test_forty_characters_become_seven(self):
        self.assertEqual(present.short_sha("a" * 40), "aaaaaaa")

    def test_empty_stays_empty(self):
        self.assertEqual(present.short_sha(""), "")
        self.assertEqual(present.short_sha(None), "")


class Labels(unittest.TestCase):
    def test_every_stored_relation_has_an_english_label(self):
        self.assertEqual(set(present.RELATION_LABEL), set(backlog.RELATIONS))
        self.assertLessEqual(set(present.RELATION_LABEL_IN), set(backlog.RELATIONS))

    def test_every_stored_result_has_an_english_label(self):
        self.assertEqual(set(present.RESULT_LABEL), set(service.OUTCOME_RESULTS))

    def test_every_outcome_kind_has_an_english_label(self):
        self.assertEqual(set(present.OUTCOME_LABEL), {"met", "missed", "unmeasurable", "due", "pending"})

    def test_no_label_carries_a_vietnamese_letter(self):
        for table in (present.RELATION_LABEL, present.RELATION_LABEL_IN, present.RESULT_LABEL,
                      present.OUTCOME_LABEL):
            for label in table.values():
                self.assertTrue(label.isascii(), label)


if __name__ == "__main__":
    unittest.main()
