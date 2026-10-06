"""Tests for `coscc/units/mentions.py`: the units a unit names, and what of them a step is told."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coscc import units
from coscc.agent import policy
from coscc.units import mentions

WS = "/tmp/a-workspace"


class Store(unittest.TestCase):
    """The app's store of one workspace: this unit, three others, and a tree to work in."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = tmp.name
        self.tree = Path(tmp.name) / "tree"
        self.tree.mkdir()
        self.me = self.unit("0086_me", "idea.md")
        self.by_idea = self.unit("0082_by-idea", "idea.md", "intent.md", "spec.md")
        self.by_answer = self.unit("0074_by-answer", "idea.md", "intent.md")
        self.unnamed = self.unit("0050_unnamed", "idea.md", "intent.md")

    def unit(self, name: str, *files: str) -> Path:
        d = units.unit_dir(WS, name, self.data)
        d.mkdir(parents=True)
        for f in files:
            (d / f).write_text(f"# {f}\n", encoding="utf-8")
        return d

    def found(self, idea="", answers=(), meta=None, own="", workspaces=()):
        return mentions.for_step(
            WS, "0086_me", idea, list(answers), meta, own, list(workspaces), self.data
        )


class AStepIsToldTheTwoFilesOfTheUnitsItsUnitNames(Store):
    """One unit named by `idea.md`, one by an answer, one by none."""

    def setUp(self):
        super().setUp()
        answers = [("spec.md", "## Answers\n\n### Câu 1\nAnswered by: x.\n\nGiống 0074.")]
        self.note = self.found("Như ca 0082 hôm trước.", answers)

    def test_nothing_of_the_three_is_written(self):
        grant = policy.Grant(cwd=str(self.tree), write=(str(self.tree), str(self.me)))
        for d in (self.by_idea, self.by_answer, self.unnamed):
            for f in ("idea.md", "intent.md", "spec.md"):
                said = policy.critical(grant, "Write", {"file_path": str(d / f)}, None)
                self.assertIn(policy.WRITES, said)

    def test_the_note_names_each_unit_its_source_and_its_files(self):
        self.assertIn(
            f"- 0082_by-idea (idea.md): {self.by_idea / 'idea.md'}, {self.by_idea / 'intent.md'}",
            self.note,
        )
        self.assertIn(
            f"- 0074_by-answer (## Answers of spec.md): {self.by_answer / 'idea.md'}", self.note
        )
        self.assertNotIn("0050", self.note)
        self.assertNotIn("refused", self.note)


class ANumberCountsOnlyAsAUnitDirectory(Store):
    def test_a_dates_year_is_not_a_unit(self):
        self.unit("2026_a-year", "idea.md")
        self.assertEqual(
            mentions.mentioned("0086_me", self.names(), "Trước 2026-10-12.", [], None), []
        )

    def test_a_number_inside_a_longer_one_is_not_a_unit(self):
        self.assertEqual(
            mentions.mentioned("0086_me", self.names(), "đo 100822 và 00740 lần", [], None), []
        )

    def test_a_number_with_no_directory_or_its_own_is_not_a_unit(self):
        self.assertEqual(mentions.mentioned("0086_me", self.names(), "0099 và 0086", [], None), [])

    def test_a_unit_named_twice_is_listed_once_with_its_first_source(self):
        found = mentions.mentioned(
            "0086_me", self.names(), "0082", [("plan.md", "0082 và 0074_by-answer")], None
        )
        self.assertEqual(
            found,
            [
                {"unit": "0082_by-idea", "source": "idea.md", "ws": ""},
                {"unit": "0074_by-answer", "source": "## Answers of plan.md", "ws": ""},
            ],
        )

    def test_a_path_in_the_text_builds_no_path(self):
        note = self.found("../../0050_unnamed/intent.md 0050x")
        # The number picks the one directory that carries it; the text around it builds nothing.
        self.assertIn(f"{self.unnamed / 'idea.md'}, {self.unnamed / 'intent.md'}", note)
        self.assertNotIn("..", note)

    def names(self) -> list[str]:
        return [p.name for p in units.cos_dir(WS, self.data).iterdir()]


class ALinkNamesAUnit(Store):
    def test_depends_on_and_the_backlog_name_a_unit_of_this_workspace(self):
        meta = {
            "links": {
                "dependsOn": ["proj/0082_by-idea"],
                "backlog": [{"ref": "0074_by-answer", "source": "backlog"}],
            }
        }
        note = self.found(meta=meta, own="proj")
        self.assertIn(str(self.by_idea / "intent.md"), note)
        self.assertIn(str(self.by_answer / "intent.md"), note)
        self.assertIn("- 0082_by-idea (Depends on:)", note)
        self.assertIn("- 0074_by-answer (backlog)", note)

    def test_a_unit_of_another_workspace_reads_from_that_workspaces_store(self):
        other = "/tmp/another-workspace"
        d = units.unit_dir(other, "0003_api", self.data)
        d.mkdir(parents=True)
        (d / "intent.md").write_text("x", encoding="utf-8")
        meta = {"links": {"dependsOn": ["api/0003_api", "gone/0001_x"]}}
        note = self.found(meta=meta, own="proj", workspaces=[{"name": "api", "path": other}])
        self.assertIn(f"- api/0003_api (Depends on:): {d / 'intent.md'}", note)
        self.assertIn("- gone/0001_x (Depends on:): missing, not readable", note)

    def test_a_named_unit_without_either_file_names_no_path(self):
        self.unit("0060_bare")
        note = self.found(idea="0060")
        self.assertIn("- 0060_bare (idea.md): no idea.md or intent.md yet", note)


class NothingNamedAddsNothing(Store):
    def test_no_note(self):
        self.assertEqual(self.found(idea="nothing here"), "")


if __name__ == "__main__":
    unittest.main()
