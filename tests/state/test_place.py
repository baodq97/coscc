"""The address, read and written, with nothing else in the way."""

from __future__ import annotations

import itertools
import unittest
from urllib.parse import urlsplit

from coscc.state import place
from coscc.state.place import Place


def _read(address: str) -> Place:
    parts = urlsplit(address)
    return place.read(parts.path, parts.query)


class AnAddressReadsBackAsThePlaceItWasWrittenFrom(unittest.TestCase):
    """Every screen, every tab, with and without `ws` and a unit."""

    def _every_place(self):
        for screen, ws in itertools.product(place.SCREENS, ("", "coscc")):
            yield Place(screen, ws)
        for ws, unit, tab in itertools.product(("", "coscc"), ("", "0016_x"), place.TABS):
            yield Place("unit", ws, unit, tab)

    def test_round_trip(self):
        count = 0
        for p in self._every_place():
            with self.subTest(place=p):
                self.assertEqual(_read(place.href(p)), p)
                count += 1
        self.assertEqual(count, 16 + 20)

    def test_no_address_ends_in_a_slash_but_the_root(self):
        for p in self._every_place():
            written = place.href(p)
            path = written.partition("?")[0]
            with self.subTest(href=written):
                if path != "/":
                    self.assertFalse(path.endswith("/"))

    def test_the_root_has_one_spelling(self):
        self.assertEqual(place.href(Place("overview")), "/")
        self.assertEqual(place.href(Place("overview", "a")), "/?ws=a")
        self.assertEqual(_read("/"), Place("overview"))

    def test_query_order_is_fixed(self):
        self.assertEqual(
            place.href(Place("unit", "a", "0016_x", "questions")),
            "/unit?ws=a&id=0016_x&tab=questions",
        )
        self.assertEqual(place.href(Place("unit", "a", "0016_x")), "/unit?ws=a&id=0016_x")


class AFeaturePageIsNamedInItsAddress(unittest.TestCase):
    def test_the_name_reads_back(self):
        p = Place("feature", "coscc", feature="vault")
        self.assertEqual(place.href(p), "/feature?ws=coscc&name=vault")
        self.assertEqual(_read(place.href(p)), p)
        self.assertEqual(_read("/feature/?name=vault"), Place("feature", feature="vault"))


class BothSpellingsAreOnePlace(unittest.TestCase):
    """A direct GET lands on `/board/`, `rx.redirect` on `/board`."""

    def test_trailing_slash(self):
        self.assertEqual(_read("/board"), _read("/board/"))
        self.assertEqual(_read("/board?ws=a"), _read("/board/?ws=a"))
        self.assertEqual(
            _read("/unit/?ws=a&id=0016_x&tab=timeline"), Place("unit", "a", "0016_x", "timeline")
        )


class WhatIsReadIsWhatIsWritten(unittest.TestCase):
    """`read` corrects nothing: `arrive` decides what an odd place becomes."""

    def test_a_bogus_tab_is_kept_word_for_word(self):
        self.assertEqual(_read("/unit?ws=a&id=x&tab=bogus").tab, "bogus")

    def test_no_tab_is_overview(self):
        self.assertEqual(_read("/unit?ws=a&id=x").tab, "overview")

    def test_id_and_tab_belong_to_unit_alone(self):
        self.assertEqual(_read("/board?id=x&tab=questions"), Place("board"))

    def test_unit_without_id_is_read_as_such(self):
        self.assertEqual(_read("/unit?ws=a"), Place("unit", "a"))

    def test_place_reads_and_writes_idea_with_ws_and_id(self):
        p = Place("idea", "proj", idea="0001_one-feature")
        self.assertEqual(place.href(p), "/idea?ws=proj&id=0001_one-feature")
        self.assertEqual(_read(place.href(p)), p)
        self.assertEqual(
            _read("/idea/?ws=proj&id=0001_x&tab=questions"), Place("idea", "proj", idea="0001_x")
        )
        self.assertNotIn("idea", place.SCREENS)

    def test_a_name_with_a_space_or_ampersand_survives(self):
        for name in ("my project", "a&b", "x=y", "ü"):
            with self.subTest(name=name):
                self.assertEqual(_read(place.href(Place("board", name))).ws, name)
                self.assertEqual(_read(place.href(Place("unit", name, "0001_a"))).ws, name)
