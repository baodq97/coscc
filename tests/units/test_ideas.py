"""Tests for an idea several units share and for `Ideas`, what the app asks of them.

The loop's `new-idea` is run for real, for the reason `tests/units/test_units.py:1-7` gives."""

from __future__ import annotations

import asyncio
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc import units
from coscc.bus import Bus
from coscc.config import Config
from coscc.http.app import Core
from coscc.units import CannotCreate, Invalid, ideas
from coscc.units import board as _board
from tests.units.test_meta import WithSnapshot

board_reader = WithSnapshot(_board)

WS = "/tmp/a-workspace"


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = self._tmp.name


class CreatingAnIdea(Fixture):
    def test_create_idea_writes_the_brief_and_no_units_section(self):
        made = ideas.create_idea(WS, "one-feature", "  backend adds, frontend calls  ", self.data)
        self.assertEqual(made["id"], "0001_one-feature")
        text = ideas.read_text(made["path"])
        self.assertEqual(
            text,
            "# Idea: one feature\nAuthor: the originator. Status: accepted.\n\n"
            "## In their own words\n\nbackend adds, frontend calls\n",
        )
        self.assertEqual(ideas.create_idea(WS, "two", "b", self.data)["id"], "0002_two")

    def test_an_idea_leaves_the_unit_numbering_alone(self):
        ideas.create_idea(WS, "one", "b", self.data)
        self.assertEqual(units.create(WS, "first", "", self.data)["unit"], "0001_first")

    def test_a_brief_with_headings_of_its_own_is_kept_whole(self):
        brief = "Backend adds an endpoint.\n\n## Units\n\nThe frontend needs it."
        path = ideas.create_idea(WS, "one", brief, self.data)["path"]
        self.assertTrue(ideas.read_text(path).endswith(f"## In their own words\n\n{brief}\n"))

    def test_an_empty_brief_is_refused_before_a_number_is_taken(self):
        with self.assertRaises(CannotCreate):
            ideas.create_idea(WS, "one", "   ", self.data)
        self.assertFalse((units.cos_dir(WS, self.data) / "ideas").exists())


class References(unittest.TestCase):
    def test_the_grammar(self):
        self.assertEqual(ideas.parse_idea_ref("proj/ideas/0001_one.md"), ("proj", "0001_one"))
        self.assertIsNone(ideas.parse_idea_ref("ideas/0001_one.md"))
        self.assertIsNone(ideas.parse_idea_ref("../ideas/0001_one.md"))
        self.assertIsNone(ideas.parse_idea_ref("proj/ideas/0001_one"))
        self.assertEqual(ideas.parse_unit_ref("api/0001_x"), ("api", "0001_x"))
        self.assertIsNone(ideas.parse_unit_ref("0001_x"))
        self.assertIsNone(ideas.parse_unit_ref("a/b/0001_x"))
        with self.assertRaises(CannotCreate):
            ideas.idea_path(WS, "../x")


if __name__ == "__main__":
    unittest.main()


def _repo(path: Path) -> str:
    path.mkdir(parents=True)
    for args in (
        ["init", "-q", "-b", "main"],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "0"],
    ):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    return str(path)


class ServiceFixture(unittest.TestCase):
    """Two env workspaces, `proj` and `api`, each a git repository."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.proj = _repo(self.root / "a" / "proj")
        self.api = _repo(self.root / "b" / "api")
        self.core = self.make(self.proj, self.api)

    def make(self, *workspaces: str) -> Core:
        class NoSessions:
            """Nothing here starts a session."""

            bus = Bus()

        return Core(
            Config(
                workspaces=workspaces, working_dir=str(self.root), data_dir=str(self.root / "data")
            ),
            NoSessions(),
        )

    def run_(self, coro):
        return asyncio.run(coro)


class PeersAreEveryWorkspaceByName(ServiceFixture):
    def test_argv_carries_one_peer_per_workspace(self):
        self.assertEqual(
            self.core.ws.peers(),
            [
                ("proj", self.core.ws.units_root(self.proj)),
                ("api", self.core.ws.units_root(self.api)),
            ],
        )

    def test_two_workspaces_with_one_name_are_left_out_and_reported(self):
        other = _repo(self.root / "c" / "proj")
        core = self.make(self.proj, self.api, other)
        peers, problems = core.ws.peer_table()
        self.assertEqual([n for n, _ in peers], ["api"])
        self.assertEqual(
            problems, ["Two workspaces are named proj, so neither is linked by that name."]
        )


class AUnitOpenedFromAnIdeaIsARow(ServiceFixture):
    def setUp(self):
        super().setUp()
        self.idea = self.core.ideas.create_idea(
            self.proj, "one-feature", "backend adds, frontend calls"
        )
        self.text = ideas.read_text(
            ideas.idea_path(self.proj, "0001_one-feature", self.root / "data")
        )
        self.back = self.run_(
            self.core.answers.create_unit(self.api, "backend", idea=self.idea["ref"])
        )
        self.front = self.run_(
            self.core.answers.create_unit(
                self.proj, "frontend", idea=self.idea["ref"], depends_on=f"api/{self.back['unit']}"
            )
        )

    def test_the_idea_file_is_not_written_and_the_rows_list_its_units(self):
        path = ideas.idea_path(self.proj, "0001_one-feature", self.root / "data")
        self.assertEqual(ideas.read_text(path), self.text)
        self.assertEqual(
            sorted(self.core.ws.unit_meta().idea_units(self.idea["ref"])),
            sorted(
                [
                    (self.core.ws.key(self.api), self.back["unit"], []),
                    (self.core.ws.key(self.proj), self.front["unit"], [f"api/{self.back['unit']}"]),
                ]
            ),
        )

    def test_the_intent_note_carries_the_idea_text_and_the_other_units_and_no_header(self):
        note = self.core.ideas.idea_note(self.proj, self.front["unit"])
        self.assertIn("backend adds, frontend calls", note)
        self.assertIn(f"- api/{self.back['unit']}\n", note)
        self.assertNotIn(f"proj/{self.front['unit']}", note)
        self.assertNotIn("must carry", note)
        self.assertNotIn("Repo:", note)
        self.assertIn(
            f"- proj/{self.front['unit']}",
            self.core.ideas.idea_note(self.api, self.back["unit"]),
        )

    def test_a_brief_opened_unit_has_no_note_and_no_siblings(self):
        plain = self.run_(self.core.answers.create_unit(self.proj, "plain", "words"))
        self.assertEqual(self.core.ideas.idea_note(self.proj, plain["unit"]), "")
        self.assertEqual(self.run_(self.core.ideas.siblings(self.proj, plain["unit"])), "")

    def test_the_impl_prompt_names_each_sibling_and_its_head(self):
        head = subprocess.run(
            ["git", "-C", self.api, "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
        note = self.run_(self.core.ideas.siblings(self.proj, self.front["unit"]))
        self.assertIn(f"- api: {Path(self.api).resolve()} (HEAD {head[:12]} on main)", note)
        self.assertNotIn("- proj:", note)

    def test_a_dependency_outside_the_idea_is_refused_and_nothing_is_made(self):
        before = self.run_(board_reader.read(self.core.ws.units_root(self.api)))["count"]
        with self.assertRaises(Invalid):
            self.run_(
                self.core.answers.create_unit(
                    self.api, "again", idea=self.idea["ref"], depends_on="proj/0099_elsewhere"
                )
            )
        self.assertEqual(
            self.run_(board_reader.read(self.core.ws.units_root(self.api)))["count"], before
        )

    def test_the_link_is_written_with_the_units_row_or_not_at_all(self):
        with (
            mock.patch(
                "coscc.units.meta.UnitMeta.add_unit", side_effect=sqlite3.OperationalError("disk")
            ),
            self.assertRaisesRegex(Invalid, "idea link was not kept"),
        ):
            self.run_(self.core.answers.create_unit(self.api, "again", idea=self.idea["ref"]))
        self.assertEqual(
            sorted(u for _, u, _ in self.core.ws.unit_meta().idea_units(self.idea["ref"])),
            sorted([self.back["unit"], self.front["unit"]]),
        )

    def test_a_brief_with_an_idea_is_refused_and_nothing_is_made(self):
        before = self.run_(board_reader.read(self.core.ws.units_root(self.api)))["count"]
        with self.assertRaises(Invalid):
            self.run_(
                self.core.answers.create_unit(self.api, "again", "a copy", idea=self.idea["ref"])
            )
        self.assertEqual(
            self.run_(board_reader.read(self.core.ws.units_root(self.api)))["count"], before
        )
