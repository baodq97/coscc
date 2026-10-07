"""The skills hub: a new skill lands only in the owner's layer and only under its own name, a run
with no stage prompt gets its row's skills, and a `start` naming a skill counts as one use."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from coscc.agent import helpers, pack, skills

NOW = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)


class _Root(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        patcher = mock.patch.object(pack, "ROOT", self.d.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = Path(self.d.name)
        self.owner = pack.owner_dir()

    def files(self) -> set[str]:
        return {str(p.relative_to(self.root)) for p in self.root.rglob("*")}


class NewSkill(_Root):
    def test_a_new_skill_is_written_into_the_owners_layer_and_read_back(self):
        path = skills.new("note-taking", "Write down what you found.")
        self.assertEqual(path, self.owner / "skills" / "note-taking" / "SKILL.md")
        self.assertEqual(pack.skill("note-taking"), "Write down what you found.\n")
        self.assertTrue((self.owner / pack.MANIFEST).is_file())

    def test_a_name_that_is_no_plain_word_is_refused_and_nothing_is_written(self):
        for bad in ("../x", "a/b", "A", "x" * 41, ".hidden", "-x", "1x", "", "a b", "a\\b", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                skills.new(bad, "text")
        self.assertEqual(self.files(), set())

    def test_the_longest_name_is_written(self):
        skills.new("x" * 40, "text")
        self.assertIsNotNone(pack.skill_path("x" * 40))

    def test_a_name_any_pack_has_is_refused(self):
        with self.assertRaisesRegex(ValueError, "exists"):
            skills.new("write-intent", "my rules")
        skills.new("mine", "one")
        with self.assertRaisesRegex(ValueError, "exists"):
            skills.new("mine", "two")
        self.assertEqual(pack.skill("mine"), "one\n")
        self.assertNotIn("my rules", pack.skill("write-intent"))

    def test_text_past_the_cap_or_not_text_is_refused_and_the_cap_itself_is_written(self):
        for bad in (
            "x" * (skills.MAX_BYTES + 1),
            "é" * (skills.MAX_BYTES // 2 + 1),
            "",
            "  \n",
            "a\x00b",
            3,
        ):
            with self.subTest(n=len(str(bad))), self.assertRaises(ValueError):
                skills.new("big", bad)
        self.assertEqual(self.files(), set())
        skills.new("big", "x" * (skills.MAX_BYTES - 1) + "\n")
        self.assertEqual(len(pack.skill("big")), skills.MAX_BYTES)

    def test_a_link_on_the_way_is_refused_and_nothing_lands_where_it_points(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        for link in (self.owner, self.owner / "skills", self.owner / "skills" / "evil"):
            with self.subTest(link=link.name):
                link.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(outside.name, link)
                with self.assertRaisesRegex(ValueError, "link"):
                    skills.new("evil", "text")
                self.assertEqual(list(Path(outside.name).rglob("*")), [])
                link.unlink()

    def test_a_folder_left_without_a_skill_is_not_written_into(self):
        (self.owner / "skills" / "left").mkdir(parents=True)
        (self.owner / "skills" / "left" / "notes.txt").write_text("x")
        with self.assertRaisesRegex(ValueError, "folder exists"):
            skills.new("left", "text")
        self.assertIsNone(pack.skill_path("left"))


class WhatARunGets(_Root):
    def _name_on(self, key: str, *names: str) -> None:
        path = self.owner / "agents" / f"{key}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(pack.render({"skills": list(names)}, ""), encoding="utf-8")

    def test_a_row_with_no_skill_gets_its_body_unchanged(self):
        self.assertEqual(skills.system("scout"), str(pack.row("scout")[pack.BODY]))

    def test_a_helper_and_an_agent_get_the_skills_their_row_names_after_the_body(self):
        skills.new("note-taking", "NOTE RULES")
        self._name_on("scout", "note-taking")
        body = str(pack.row("scout")[pack.BODY]).rstrip()
        self.assertEqual(skills.system("scout"), f"{body}\n\nNOTE RULES")
        self.assertEqual(
            helpers.definitions(("scout",))["scout"]["prompt"], f"{body}\n\nNOTE RULES"
        )

    def test_a_stage_gets_the_same_text_a_helper_does(self):
        self.assertEqual(skills.text(["write-idea"]), pack.skill("write-idea"))


class Catalog(_Root):
    def test_every_skill_once_with_its_pack_agents_and_uses(self):
        skills.new("note-taking", "---\nname: note-taking\ndescription: Take notes.\n---\nRULES")
        (self.owner / "skills" / "write-idea").mkdir(parents=True)
        (self.owner / "skills" / "write-idea" / "SKILL.md").write_text("my idea rules\n")
        records = [
            {"kind": "start", "at": "2026-10-06T00:00:00+00:00", "skills": ["note-taking@ab"]},
            {
                "kind": "start",
                "at": "2026-10-07T00:00:00+00:00",
                "skills": ["note-taking@cd", "write-idea@ef"],
            },
            {"kind": "start", "at": "2026-08-01T00:00:00+00:00", "skills": ["note-taking@ab"]},
            {"kind": "end", "at": "2026-10-07T00:00:00+00:00", "skills": ["note-taking@ab"]},
        ]
        found = {s["name"]: s for s in skills.catalog(records, NOW)}
        self.assertEqual(sorted(found), sorted({*found} | {"note-taking", "write-intent"}))
        mine, idea = found["note-taking"], found["write-idea"]
        self.assertEqual((mine["pack"], mine["own"], mine["edited"]), ("local", True, False))
        self.assertEqual((mine["uses_30d"], mine["last_used"]), (2, "2026-10-07T00:00:00+00:00"))
        self.assertEqual(mine["description"], "Take notes.")
        self.assertEqual((mine["builtin"], idea["builtin"]), (False, True))
        self.assertEqual(mine["agents"], [])
        self.assertEqual(
            (idea["pack"], idea["own"], idea["edited"]), (pack.manifest()["name"], False, True)
        )
        self.assertEqual((idea["agents"], idea["uses_30d"]), (["idea"], 1))
        self.assertEqual(idea["text"], "my idea rules\n")
        self.assertEqual(idea["description"], "my idea rules")
        self.assertEqual(found["write-intent"]["uses_30d"], 0)


if __name__ == "__main__":
    unittest.main()
