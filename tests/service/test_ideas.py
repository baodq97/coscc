"""Tests for `Ideas` in `coscc/service/ideas.py` and the peers `Board` passes."""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import unittest
from pathlib import Path

from coscc.bus import Bus
from coscc.config import Config
from coscc.service import Service
from coscc.service.common import Invalid
from coscc.units import board as _board
from tests.units.test_meta import WithSnapshot

board_reader = WithSnapshot(_board)


def _repo(path: Path) -> str:
    path.mkdir(parents=True)
    for args in (
        ["init", "-q", "-b", "main"],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "0"],
    ):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    return str(path)


class Fixture(unittest.TestCase):
    """Two env workspaces, `proj` and `api`, each a git repository."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.proj = _repo(self.root / "a" / "proj")
        self.api = _repo(self.root / "b" / "api")
        self.service = self.make(self.proj, self.api)

    def make(self, *workspaces: str) -> Service:
        class NoSessions:
            """Nothing here starts a session."""

            bus = Bus()

        return Service(
            Config(
                workspaces=workspaces, working_dir=str(self.root), data_dir=str(self.root / "data")
            ),
            NoSessions(),
        )

    def run_(self, coro):
        return asyncio.run(coro)


class PeersAreEveryWorkspaceByName(Fixture):
    def test_argv_carries_one_peer_per_workspace(self):
        self.assertEqual(
            self.service.ws.peers(),
            [
                ("proj", self.service.ws.units_root(self.proj)),
                ("api", self.service.ws.units_root(self.api)),
            ],
        )

    def test_two_workspaces_with_one_name_are_left_out_and_reported(self):
        other = _repo(self.root / "c" / "proj")
        service = self.make(self.proj, self.api, other)
        peers, problems = service.ws.peer_table()
        self.assertEqual([n for n, _ in peers], ["api"])
        self.assertEqual(
            problems, ["Two workspaces are named proj, so neither is linked by that name."]
        )
        self.assertEqual(self.run_(service.board(self.api))["peer_problems"], problems)
        self.assertNotIn("peer_problems", self.run_(self.service.board(self.api)))

    def test_a_unit_whose_repo_differs_from_its_workspace_gets_a_problem(self):
        made = self.run_(self.service.answers.create_unit(self.proj, "x"))
        (Path(made["path"]) / "intent.md").write_text(
            "# I\nType: feat. Status: accepted.\nRepo: api.\n", encoding="utf-8"
        )
        [u] = self.run_(self.service.board(self.proj))["units"]
        self.assertIn("Repo: api is not this workspace, proj.", u["problems"])


class AUnitOpenedFromAnIdea(Fixture):
    def setUp(self):
        super().setUp()
        self.idea = self.service.ideas.create_idea(
            self.proj, "one-feature", "backend adds, frontend calls"
        )
        self.back = self.run_(
            self.service.answers.create_unit(self.api, "backend", idea=self.idea["ref"])
        )
        self.front = self.run_(
            self.service.answers.create_unit(
                self.proj, "frontend", idea=self.idea["ref"], depends_on=f"api/{self.back['unit']}"
            )
        )

    def test_the_intent_note_carries_the_idea_text_the_siblings_and_the_three_header_lines(self):
        note = self.service.ideas.idea_note(self.proj, self.front["unit"])
        self.assertIn("backend adds, frontend calls", note)
        self.assertIn(f"- api/{self.back['unit']} (Repo: api)", note)
        self.assertIn(
            f"    Idea: proj/ideas/0001_one-feature.md.\n    Repo: proj.\n    Depends on: api/{self.back['unit']}.\n",
            note,
        )
        self.assertNotIn(
            "Depends on",
            self.service.ideas.idea_note(self.api, self.back["unit"]).split("must carry")[1],
        )

    def test_a_brief_opened_unit_has_no_note_and_no_siblings(self):
        plain = self.run_(self.service.answers.create_unit(self.proj, "plain", "words"))
        self.assertEqual(self.service.ideas.idea_note(self.proj, plain["unit"]), "")
        self.assertEqual(self.run_(self.service.ideas.siblings(self.proj, plain["unit"])), ((), ""))

    def test_the_impl_prompt_names_each_sibling_and_its_head(self):
        head = subprocess.run(
            ["git", "-C", self.api, "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
        paths, note = self.run_(self.service.ideas.siblings(self.proj, self.front["unit"]))
        self.assertEqual(paths, (str(Path(self.api).resolve()),))
        self.assertIn(f"- api: {Path(self.api).resolve()} (HEAD {head[:12]} on main)", note)
        self.assertNotIn("- proj:", note)

    def test_a_brief_with_an_idea_is_refused_and_nothing_is_made(self):
        before = self.run_(board_reader.read(self.service.ws.units_root(self.api)))["count"]
        with self.assertRaises(Invalid):
            self.run_(
                self.service.answers.create_unit(self.api, "again", "a copy", idea=self.idea["ref"])
            )
        self.assertEqual(
            self.run_(board_reader.read(self.service.ws.units_root(self.api)))["count"], before
        )

    def test_the_idea_page_lists_each_child_with_repo_stage_and_waits_for(self):
        page = self.run_(self.service.ideas.idea(self.proj, "0001_one-feature"))
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
        service = self.make(self.proj)
        page = self.run_(service.ideas.idea(self.proj, "0001_one-feature"))
        self.assertEqual(
            [(r["repo"], r["missing"], r["state"]) for r in page["units"]][0],
            ("api", True, "missing"),
        )


if __name__ == "__main__":
    unittest.main()
