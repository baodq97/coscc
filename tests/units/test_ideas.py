"""Tests for an idea several units share and for `Ideas`, what the app asks of them.

The loop's `new-idea` is run for real, for the reason `tests/units/test_units.py:1-7` gives."""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import unittest
from pathlib import Path

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
    def test_create_idea_writes_with_an_empty_units_section(self):
        made = ideas.create_idea(WS, "one-feature", "  backend adds, frontend calls  ", self.data)
        self.assertEqual(made["id"], "0001_one-feature")
        text = ideas.read_text(made["path"])
        self.assertEqual(
            text,
            "# Idea: one feature\nAuthor: the originator. Status: accepted.\n\n"
            "## In their own words\n\nbackend adds, frontend calls\n\n## Units\n\n",
        )
        self.assertEqual(ideas.read_units(text), [])
        self.assertEqual(ideas.brief_of(text), "backend adds, frontend calls")
        self.assertEqual(ideas.create_idea(WS, "two", "b", self.data)["id"], "0002_two")

    def test_an_idea_leaves_the_unit_numbering_alone(self):
        ideas.create_idea(WS, "one", "b", self.data)
        self.assertEqual(units.create(WS, "first", "", self.data)["unit"], "0001_first")

    def test_a_brief_with_headings_of_its_own_is_kept_whole(self):
        """A brief pasted from a markdown file ran only to its first `## `."""
        brief = (
            "Backend adds an endpoint.\n\n## Why\n\nThe frontend needs it.\n\n## Not this\n\nAuth."
        )
        path = ideas.create_idea(WS, "one", brief, self.data)["path"]
        ideas.append_unit(path, "api", "0001_backend")
        text = ideas.read_text(path)
        self.assertEqual(ideas.brief_of(text), brief)
        self.assertEqual(ideas.read_units(text), [{"ref": "api/0001_backend", "depends_on": []}])

    def test_a_brief_holding_the_units_heading_is_refused_before_a_number_is_taken(self):
        with self.assertRaises(CannotCreate) as caught:
            ideas.create_idea(WS, "one", "words\n## Units\n- api/0001_x.", self.data)
        self.assertIn("## Units", str(caught.exception))
        self.assertFalse((units.cos_dir(WS, self.data) / "ideas").exists())

    def test_an_empty_brief_is_refused_before_a_number_is_taken(self):
        with self.assertRaises(CannotCreate):
            ideas.create_idea(WS, "one", "   ", self.data)
        self.assertFalse((units.cos_dir(WS, self.data) / "ideas").exists())


class AppendingAUnit(Fixture):
    def test_append_unit_only_appends(self):
        path = ideas.create_idea(WS, "one", "words", self.data)["path"]
        before = ideas.read_text(path)
        ideas.append_unit(path, "api", "0001_backend")
        ideas.append_unit(path, "proj", "0006_frontend", "api/0001_backend")
        after = ideas.read_text(path)
        self.assertTrue(after.startswith(before), after)
        self.assertEqual(
            after[len(before) :],
            "- api/0001_backend.\n- proj/0006_frontend. Depends on: api/0001_backend.\n",
        )
        self.assertEqual(
            ideas.read_units(after),
            [
                {"ref": "api/0001_backend", "depends_on": []},
                {"ref": "proj/0006_frontend", "depends_on": ["api/0001_backend"]},
            ],
        )

    def test_a_file_without_a_final_newline_gets_one_before_the_line(self):
        path = ideas.create_idea(WS, "one", "words", self.data)["path"]
        with open(path, "a", encoding="utf-8") as f:
            f.write("- api/0001_a.")
        ideas.append_unit(path, "api", "0002_b")
        self.assertTrue(ideas.read_text(path).endswith("- api/0001_a.\n- api/0002_b.\n"))


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

    def test_a_unit_whose_repo_differs_from_its_workspace_gets_a_problem(self):
        made = self.run_(self.core.answers.create_unit(self.proj, "x"))
        (Path(made["path"]) / "intent.md").write_text(
            "# I\nType: feat. Status: accepted.\nRepo: api.\n", encoding="utf-8"
        )
        [u] = self.run_(self.core.board(self.proj))["units"]
        self.assertIn("Repo: api is not this workspace, proj.", u["problems"])


class AUnitOpenedFromAnIdea(ServiceFixture):
    def setUp(self):
        super().setUp()
        self.idea = self.core.ideas.create_idea(
            self.proj, "one-feature", "backend adds, frontend calls"
        )
        self.back = self.run_(
            self.core.answers.create_unit(self.api, "backend", idea=self.idea["ref"])
        )
        self.front = self.run_(
            self.core.answers.create_unit(
                self.proj, "frontend", idea=self.idea["ref"], depends_on=f"api/{self.back['unit']}"
            )
        )

    def test_the_intent_note_carries_the_idea_text_the_siblings_and_the_three_header_lines(self):
        note = self.core.ideas.idea_note(self.proj, self.front["unit"])
        self.assertIn("backend adds, frontend calls", note)
        self.assertIn(f"- api/{self.back['unit']} (Repo: api)", note)
        self.assertIn(
            f"    Idea: proj/ideas/0001_one-feature.md.\n    Repo: proj.\n    Depends on: api/{self.back['unit']}.\n",
            note,
        )
        self.assertNotIn(
            "Depends on",
            self.core.ideas.idea_note(self.api, self.back["unit"]).split("must carry")[1],
        )

    def test_a_brief_opened_unit_has_no_note_and_no_siblings(self):
        plain = self.run_(self.core.answers.create_unit(self.proj, "plain", "words"))
        self.assertEqual(self.core.ideas.idea_note(self.proj, plain["unit"]), "")
        self.assertEqual(self.run_(self.core.ideas.siblings(self.proj, plain["unit"])), ((), ""))

    def test_the_impl_prompt_names_each_sibling_and_its_head(self):
        head = subprocess.run(
            ["git", "-C", self.api, "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
        paths, note = self.run_(self.core.ideas.siblings(self.proj, self.front["unit"]))
        self.assertEqual(paths, (str(Path(self.api).resolve()),))
        self.assertIn(f"- api: {Path(self.api).resolve()} (HEAD {head[:12]} on main)", note)
        self.assertNotIn("- proj:", note)

    def test_a_brief_with_an_idea_is_refused_and_nothing_is_made(self):
        before = self.run_(board_reader.read(self.core.ws.units_root(self.api)))["count"]
        with self.assertRaises(Invalid):
            self.run_(
                self.core.answers.create_unit(self.api, "again", "a copy", idea=self.idea["ref"])
            )
        self.assertEqual(
            self.run_(board_reader.read(self.core.ws.units_root(self.api)))["count"], before
        )

    def test_the_idea_page_lists_each_child_with_repo_stage_and_waits_for(self):
        page = self.run_(self.core.ideas.idea(self.proj, "0001_one-feature"))
        self.assertEqual(page["title"], "one feature")
        self.assertEqual(
            [(r["unit"], r["repo"], r["missing"]) for r in page["units"]],
            [
                (self.back["unit"], "api", False),
                (self.front["unit"], "proj", False),
            ],
        )
        self.assertEqual(page["workspaces"], ["proj", "api"])

    def test_a_workspace_that_is_gone_is_a_missing_row(self):
        core = self.make(self.proj)
        page = self.run_(core.ideas.idea(self.proj, "0001_one-feature"))
        self.assertEqual(
            [(r["repo"], r["missing"], r["state"]) for r in page["units"]][0],
            ("api", True, "missing"),
        )
