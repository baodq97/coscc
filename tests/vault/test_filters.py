"""The filter: every form a value may take, and nothing that is only a part of it."""

from __future__ import annotations

import base64
import itertools
import json
import unittest
from urllib.parse import quote, quote_plus

from coscc.vault.filters import Hit, mask, scan

# Its base64 has `+` and `/` in the standard alphabet and `-` and `_` in the URL-safe one.
BASE = bytes([0xFB, 0xEF, 0xBE, 0xFF, 0xFE, 0x3E, 0x3F]) + b"secret-material"


def masked_once(test: unittest.TestCase, value: bytes, text: bytes, label: str) -> None:
    out, counts = mask({"ws:k": value}, text)
    test.assertEqual(counts, {"ws:k": 1}, label)
    test.assertIn(b"[secret:ws:k]", out, label)
    test.assertTrue(out.startswith(b"<<") and out.endswith(b">>"), label)


class AValueIsMaskedInEveryFormItMayTake(unittest.TestCase):
    def test_raw(self):
        masked_once(self, b"tok-123456", b"<< the tok-123456 here >>", "raw")
        out, counts = mask({"ws:k": b"tok-123456"}, b"tok-123456 and tok-123456")
        self.assertEqual((out, counts), (b"[secret:ws:k] and [secret:ws:k]", {"ws:k": 2}))

    def test_base64_standard_and_urlsafe_padded_or_not_at_each_of_three_offsets(self):
        cases = itertools.product((base64.b64encode, base64.urlsafe_b64encode), (0, 1, 2), range(3))
        for encode, lead, extra in cases:
            value = BASE + b"x" * extra
            stream = encode(b"Q" * lead + value)
            inside = encode(b"Q" * lead + value + b"more-bytes-after")
            for label, text in (
                ("padded", stream),
                ("unpadded", stream.rstrip(b"=")),
                ("inside", inside),
            ):
                with self.subTest(encoder=encode.__name__, lead=lead, extra=extra, form=label):
                    masked_once(self, value, b"<<" + text + b">>", label)

    def test_hex_lower_and_upper(self):
        value = b"\x01\xabpass\xff"
        masked_once(self, value, b"<<" + value.hex().encode() + b">>", "lower")
        masked_once(self, value, b"<<" + value.hex().upper().encode() + b">>", "upper")

    def test_url_encoding_with_percent_20_and_with_plus(self):
        value = b"pass word&x=1/2"
        for label, text in (
            ("percent", quote(value, safe="")),
            ("percent keeping slash", quote(value)),
            ("plus", quote_plus(value)),
        ):
            with self.subTest(form=label):
                masked_once(self, value, b"<<" + text.encode() + b">>", label)
        self.assertIn("%20", quote(value))
        self.assertIn("+", quote_plus(value))

    def test_json_escaped_with_ensure_ascii_on_and_off(self):
        value = 'pässwörd "q" \\ \n\t'.encode()
        for ascii_only in (True, False):
            with self.subTest(ensure_ascii=ascii_only):
                text = json.dumps(value.decode(), ensure_ascii=ascii_only)[1:-1]
                self.assertEqual("\\u00e4" in text, ascii_only)
                masked_once(self, value, b"<<" + text.encode() + b">>", "json")


class OnlyAWholeValueIsCaught(unittest.TestCase):
    VALUE = b"abcdefgh12345678"

    def test_a_part_of_a_value_in_any_form_is_left_alone(self):
        half = self.VALUE[:8]
        text = b"<< " + b" ".join(
            [
                half,
                half.hex().encode(),
                base64.b64encode(half),
                quote_plus(half).encode(),
                self.VALUE[8:],
                self.VALUE[:-1],
            ]
        )
        out, counts = mask({"ws:k": self.VALUE}, text)
        self.assertEqual((out, counts), (text, {}))

    def test_a_value_that_differs_by_a_byte_is_left_alone(self):
        text = b"abcdefgh12345679 abcdefgh1234567"
        self.assertEqual(mask({"ws:k": self.VALUE}, text), (text, {}))


class TheLongestPatternGoesFirstAndOnePassIsMade(unittest.TestCase):
    def test_a_value_inside_another_is_not_masked_twice(self):
        values = {"ws:short": b"token-abc", "ws:long": b"token-abc-and-more"}
        out, counts = mask(values, b"x token-abc-and-more y")
        self.assertEqual((out, counts), (b"x [secret:ws:long] y", {"ws:long": 1}))

    def test_a_marker_is_never_masked_again(self):
        out, counts = mask({"ws:secret": b"secret"}, b"a secret b")
        self.assertEqual((out, counts), (b"a [secret:ws:secret] b", {"ws:secret": 1}))


class ScanningNamesWhereAndInWhichForm(unittest.TestCase):
    VALUES = {"ws:a": b"first-value-1", "ws:b": b"second-value-2"}

    def test_a_hit_names_the_secret_the_source_and_the_form(self):
        sources = [
            ("clean", b"nothing here"),
            ("raw", b"x first-value-1 y"),
            ("hexed", b"x " + b"second-value-2".hex().encode()),
        ]
        self.assertEqual(
            scan(self.VALUES, sources),
            [Hit("ws:a", "raw", "raw"), Hit("ws:b", "hexed", "hex")],
        )

    def test_a_part_of_a_value_is_no_hit(self):
        self.assertEqual(scan(self.VALUES, [("s", b"first-value second-value")]), [])


if __name__ == "__main__":
    unittest.main()
