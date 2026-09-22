"""Tests for the object store. The two properties `spec.md` R11 asks for, and the names."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cos_baodo.objects import Missing, Objects


class NamesComeFromContents(unittest.TestCase):
    def test_the_digest_is_sha256_of_the_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            digest = Objects(d).put(b"hello")
            self.assertEqual(digest, hashlib.sha256(b"hello").hexdigest())

    def test_the_path_is_built_from_the_digest(self):
        with tempfile.TemporaryDirectory() as d:
            store = Objects(d)
            digest = store.digest(b"hello")
            self.assertEqual(store.path_of(digest), Path(d).resolve() / digest[:2] / digest)

    def test_anything_that_is_not_a_digest_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            store = Objects(d)
            for bad in ("", "../escape", "/etc/passwd", "zz" * 32, "abc", "A" * 64 + "B"):
                with self.assertRaises(ValueError, msg=bad):
                    store.path_of(bad)

    def test_a_bad_digest_is_not_present_rather_than_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(Objects(d).has("../escape"))


class Immutability(unittest.TestCase):
    def test_the_same_bytes_twice_is_one_object(self):
        with tempfile.TemporaryDirectory() as d:
            store = Objects(d)
            first = store.put(b"same")
            second = store.put(b"same")
            self.assertEqual(first, second)
            files = [p for p in Path(d).rglob("*") if p.is_file()]
            self.assertEqual(len(files), 1)

    def test_different_bytes_are_different_objects(self):
        with tempfile.TemporaryDirectory() as d:
            store = Objects(d)
            self.assertNotEqual(store.put(b"a"), store.put(b"b"))

    def test_rewriting_does_not_touch_the_existing_file(self):
        with tempfile.TemporaryDirectory() as d:
            store = Objects(d)
            digest = store.put(b"same")
            before = store.path_of(digest).stat().st_mtime_ns
            store.put(b"same")
            self.assertEqual(store.path_of(digest).stat().st_mtime_ns, before)


class WritingIsAtomic(unittest.TestCase):
    def test_a_failure_mid_write_leaves_no_object_and_no_temp_file(self):
        """`spec.md` R11: an object that exists is complete.

        The failure is injected at `os.replace`, which is the last step — everything
        before it has already happened, so this is the moment a leftover would be worst.
        """
        with tempfile.TemporaryDirectory() as d:
            store = Objects(d)
            with mock.patch("cos_baodo.objects.os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    store.put(b"never lands")
            self.assertFalse(store.has(store.digest(b"never lands")))
            self.assertEqual([p for p in Path(d).rglob("*") if p.is_file()], [])

    def test_a_stored_object_is_not_world_readable(self):
        with tempfile.TemporaryDirectory() as d:
            store = Objects(d)
            digest = store.put(b"private")
            self.assertEqual(stat.S_IMODE(store.path_of(digest).stat().st_mode), 0o600)


class ReadingBack(unittest.TestCase):
    def test_bytes_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            store = Objects(d)
            payload = b"\x00\x01\x02 binary too"
            self.assertEqual(store.get(store.put(payload)), payload)

    def test_text_round_trips_including_non_ascii(self):
        with tempfile.TemporaryDirectory() as d:
            store = Objects(d)
            text = "một reply dài, có dấu\nvà nhiều dòng"
            self.assertEqual(store.get_text(store.put_text(text)), text)

    def test_a_missing_object_raises_without_a_default(self):
        with tempfile.TemporaryDirectory() as d:
            store = Objects(d)
            with self.assertRaises(Missing):
                store.get(store.digest(b"never stored"))

    def test_a_missing_object_gives_the_default_when_one_is_offered(self):
        """A timeline renders these. A gap belongs on the page, a traceback does not."""
        with tempfile.TemporaryDirectory() as d:
            store = Objects(d)
            self.assertEqual(
                store.get_text(store.digest(b"gone"), default="(no longer stored)"),
                "(no longer stored)",
            )


if __name__ == "__main__":
    unittest.main()
