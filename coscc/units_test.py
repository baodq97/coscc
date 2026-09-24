"""Tests for where a workspace's units live, and for starting one.

`cos.mjs` is run for real here rather than stubbed. What is under test is an agreement
with that script — the shape it prints, the path it prints it relative to, what it says
about a bad slug — and a stub agrees with whatever it was told. `coscc/board_test.py:1-7`
makes the same argument for the same script.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coscc import units
from coscc.units import BadUnit, CannotCreate

WS = "/tmp/a-workspace"


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = self._tmp.name


class OneFunctionAnswersWhereUnitsLive(Fixture):
    """R3. Three modules used to work this out for themselves."""

    def test_the_store_is_under_the_data_root_and_not_in_the_workspace(self):
        # R2, structurally: there is no arrangement of arguments that puts a unit inside
        # the workspace, because the workspace is only ever hashed into a name.
        store = units.root(WS, self.data)
        self.assertTrue(str(store).startswith(self.data), store)
        self.assertNotIn("a-workspace/", str(store).replace(self.data, ""))

    def test_moving_the_data_root_moves_every_path_with_it(self):
        with tempfile.TemporaryDirectory() as other:
            self.assertNotEqual(units.root(WS, self.data), units.root(WS, other))
            self.assertTrue(str(units.unit_dir(WS, "0001_x", other)).startswith(other))

    def test_two_workspaces_with_the_same_basename_do_not_collide(self):
        self.assertNotEqual(
            units.root("/tmp/one/coscc", self.data), units.root("/tmp/two/coscc", self.data)
        )

    def test_the_identity_is_the_resolved_path_the_journal_already_uses(self):
        # `spec.md` open question 4: the same convention, not a second one.
        self.assertEqual(units.key("/tmp/../tmp/a-workspace"), str(Path(WS).resolve()))
        self.assertEqual(units.root("/tmp/../tmp/a-workspace", self.data), units.root(WS, self.data))

    def test_the_slot_name_carries_the_basename_so_a_person_can_read_it(self):
        self.assertTrue(units.slot(WS).startswith("a-workspace-"), units.slot(WS))

    def test_a_unit_name_from_a_request_cannot_walk_out_of_the_store(self):
        for bad in ("../../etc", "0001", "0001_Bad_Slug", "", "0001_x/../../y"):
            with self.assertRaises(BadUnit, msg=bad):
                units.unit_dir(WS, bad, self.data)


class StartingAUnit(Fixture):
    def test_the_number_comes_from_cos_mjs_and_counts_up(self):
        first = units.create(WS, "a-first-problem", "", self.data)
        second = units.create(WS, "a-second-problem", "", self.data)
        self.assertEqual(first["unit"], "0001_a-first-problem")
        self.assertEqual(second["unit"], "0002_a-second-problem")

    def test_the_directory_it_names_is_the_directory_it_makes(self):
        made = units.create(WS, "a-problem", "", self.data)
        self.assertTrue(Path(made["path"]).is_dir())
        self.assertEqual(
            Path(made["path"]).parent, units.cos_dir(WS, self.data)
        )

    def test_a_bad_slug_is_refused_in_the_scripts_own_words(self):
        # `intent.md` constraint 4: no second validator. The wording has to be its own or
        # there are two answers to what a slug is.
        with self.assertRaises(CannotCreate) as caught:
            units.create(WS, "Bad_Slug", "", self.data)
        self.assertIn("Bad_Slug", str(caught.exception))
        self.assertIn("slug", str(caught.exception).lower())

    def test_making_the_same_unit_twice_is_not_possible(self):
        # `new-path` allocates the next number, so a second call with the same slug gets a
        # different number rather than colliding. The point is that nothing is overwritten.
        first = units.create(WS, "same-slug", "", self.data)
        second = units.create(WS, "same-slug", "", self.data)
        self.assertNotEqual(first["unit"], second["unit"])

    def test_two_workspaces_number_their_units_independently(self):
        units.create("/tmp/one", "a-problem", "", self.data)
        other = units.create("/tmp/two", "a-problem", "", self.data)
        self.assertEqual(other["unit"], "0001_a-problem")


class NumbersTakenInTheHostRepositoryCount(Fixture):
    """`0001_product-describes-a-state-it-is-not-in` R10 and R11."""

    def _host(self, *names: str) -> Path:
        host = Path(self.data) / "host"
        for n in names:
            (host / ".cos" / n).mkdir(parents=True)
        return host

    def test_a_host_with_fourteen_units_makes_the_next_one_fifteen(self):
        host = self._host(*[f"{i:04d}_u{i}" for i in range(1, 15)])
        made = units.create(host, "fresh", "", self.data, reserve_from=[host])
        self.assertEqual(made["unit"], "0015_fresh")

    def test_nothing_is_written_into_the_host(self):
        host = self._host("0001_a", "0014_n")
        before = sorted(p.name for p in (host / ".cos").iterdir())
        units.create(host, "fresh", "", self.data, reserve_from=[host])
        self.assertEqual(sorted(p.name for p in (host / ".cos").iterdir()), before)

    def test_without_reserve_the_old_numbering_is_unchanged(self):
        host = self._host("0014_n")
        self.assertEqual(units.create(host, "fresh", "", self.data)["unit"], "0001_fresh")

    def test_the_number_is_whatever_cos_mjs_prints(self):
        """R11. If Python worked the number out itself, this would not be `0042`."""
        from unittest import mock

        fake = Path(self.data) / "fake-cos.mjs"
        seen = Path(self.data) / "argv.json"
        fake.write_text(
            "import { writeFileSync } from 'node:fs'\n"
            f"writeFileSync({str(seen)!r}, JSON.stringify(process.argv.slice(2)))\n"
            "console.log('.cos/0042_fixed')\n",
            encoding="utf-8",
        )
        host = self._host("0014_n")
        with mock.patch("coscc.harness.script", return_value=fake):
            made = units.create(host, "anything", "", self.data, reserve_from=[host])
        self.assertEqual(made["unit"], "0042_fixed")

        import json

        argv = json.loads(seen.read_text(encoding="utf-8"))
        at = argv.index("--reserve-from")
        self.assertEqual(argv[at + 1], str(host.resolve()))
        # Before the command, so an older script refuses instead of ignoring it (plan Risk 3).
        self.assertLess(at, argv.index("new-path"))


class TheHostUnitCount(Fixture):
    def test_it_counts_directories_named_like_units(self):
        host = Path(self.data) / "host"
        for n in ("0001_a", "0002_b", "0003_Not_A_Valid_Slug"):
            (host / ".cos" / n).mkdir(parents=True)
        (host / ".cos" / "RENAMES.md").write_text("x", encoding="utf-8")
        (host / ".cos" / "notes").mkdir()
        self.assertEqual(units.host_unit_count(host), 3)

    def test_no_cos_directory_is_zero(self):
        self.assertEqual(units.host_unit_count(self.data), 0)
        self.assertEqual(units.host_unit_count("/nonexistent-host-for-a-test"), 0)

    def test_it_is_counted_again_on_every_call(self):
        host = Path(self.data) / "host"
        (host / ".cos" / "0001_a").mkdir(parents=True)
        self.assertEqual(units.host_unit_count(host), 1)
        (host / ".cos" / "0002_b").mkdir()
        self.assertEqual(units.host_unit_count(host), 2)


class TheBriefBecomesTheIdea(Fixture):
    """`plan.md` `## OQ1, settled before planning`, and since
    `0003_one-idea-is-trapped-inside-one-unit` an idea of its own rather than an `idea.md`
    inside the unit (R1, R8). Rewritten there, not removed (`spec.md` C4)."""

    def _idea_file(self, made) -> Path:
        return units.cos_dir(WS, self.data) / "ideas" / f"{made['idea']}.md"

    def test_it_is_written_as_an_idea_file_with_a_status_the_loop_accepts(self):
        made = units.create(WS, "a-problem", "Nút Run không nói gì khi hỏng.", self.data)
        self.assertEqual(made["idea"], "0001_a-problem")
        text = self._idea_file(made).read_text(encoding="utf-8")
        self.assertIn("Nút Run không nói gì khi hỏng.", text)
        self.assertIn("Status: accepted.", text)
        self.assertTrue(text.endswith(f"## Units\n\n- {made['unit']}\n"), text)
        self.assertFalse((Path(made["path"]) / "idea.md").exists())

    def test_the_loop_reads_it_back_as_an_accepted_idea_and_a_pre_intent_unit(self):
        # The only check that matters: `cos.mjs` has to agree, because a file it calls
        # broken blocks the unit rather than helping it. Read from `status --json`: the
        # text table's `idea` column is `—` for a unit with no `idea.md`.
        import json

        made = units.create(WS, "a-problem", "some words", self.data)
        data = json.loads(units._cos(units.root(WS, self.data), "status", "--json"))
        [idea] = data["ideas"]
        [unit] = data["units"]
        self.assertEqual((idea["name"], idea["status"], idea["units"], idea["problems"]),
                         (made["idea"], "accepted", [made["unit"]], []))
        self.assertEqual((unit["phase"], unit["problems"], unit["source"]), ("pre-intent", [], made["idea"]))

    def test_no_brief_writes_no_file_rather_than_an_empty_one(self):
        made = units.create(WS, "a-problem", "   ", self.data)
        self.assertFalse((Path(made["path"]) / "idea.md").exists())
        self.assertFalse(made["brief"])
        self.assertEqual(made["idea"], "")
        self.assertFalse((units.cos_dir(WS, self.data) / "ideas").exists())


class OneIdeaOpensSeveralUnits(Fixture):
    """`0003_one-idea-is-trapped-inside-one-unit` R1, R9."""

    def test_a_second_unit_is_opened_from_the_idea_and_listed_under_it(self):
        first = units.create(WS, "first-half", "một quan sát", self.data)
        second = units.create(WS, "second-half", "", self.data, idea=first["idea"])
        self.assertEqual(second["idea"], first["idea"])
        text = (units.cos_dir(WS, self.data) / "ideas" / f"{first['idea']}.md").read_text(encoding="utf-8")
        self.assertTrue(text.endswith(f"- {first['unit']}\n- {second['unit']}\n"), text)
        self.assertEqual(text.count("## Units"), 1)

    def test_a_brief_and_an_idea_together_are_refused(self):
        first = units.create(WS, "a", "words", self.data)
        with self.assertRaises(BadUnit):
            units.create(WS, "b", "more words", self.data, idea=first["idea"])
        self.assertEqual(len([p for p in units.cos_dir(WS, self.data).iterdir() if p.name != "ideas"]), 1)

    def test_an_idea_that_does_not_exist_is_refused_before_anything_is_made(self):
        for bad in ("0009_nothing", "../0001_a", "x"):
            with self.subTest(bad), self.assertRaises(CannotCreate):
                units.create(WS, "b", "", self.data, idea=bad)
        self.assertEqual(list(units.cos_dir(WS, self.data).iterdir()), [])

    def test_appending_after_an_answers_section_opens_a_new_units_section(self):
        first = units.create(WS, "a", "words", self.data)
        path = units.cos_dir(WS, self.data) / "ideas" / f"{first['idea']}.md"
        before = path.read_text(encoding="utf-8") + "\n## Answers\n\n### Câu 1\n\nx\n"
        path.write_text(before, encoding="utf-8")
        second = units.create(WS, "b", "", self.data, idea=first["idea"])
        after = path.read_text(encoding="utf-8")
        self.assertTrue(after.startswith(before))
        self.assertEqual(after[len(before):], f"\n## Units\n\n- {second['unit']}\n")

    def test_the_brief_lives_in_one_file_only(self):
        mark = "MARK-ONE-FILE-7d"
        first = units.create(WS, "a", f"words {mark}", self.data)
        units.create(WS, "b", "", self.data, idea=first["idea"])
        carrying = [
            p for p in units.cos_dir(WS, self.data).rglob("*")
            if p.is_file() and mark in p.read_text(encoding="utf-8")
        ]
        self.assertEqual(len(carrying), 1, carrying)

    def test_a_failed_write_leaves_no_unit_directory(self):
        from unittest import mock

        first = units.create(WS, "a", "words", self.data)
        with mock.patch.object(units, "_append_unit", side_effect=OSError("disk full")):
            with self.assertRaises(CannotCreate):
                units.create(WS, "b", "", self.data, idea=first["idea"])
        self.assertEqual(sorted(p.name for p in units.cos_dir(WS, self.data).iterdir()), [first["unit"], "ideas"])


class TheBranchNameComesFromTheIntent(Fixture):
    def test_it_refuses_before_the_intent_stage_has_run_and_says_which_file_is_missing(self):
        # `cos.mjs unit-branch` says `No such work unit` here, which is true of the file
        # it reads and false of the unit. Measured 2026-09-22.
        made = units.create(WS, "a-problem", "", self.data)
        with self.assertRaises(CannotCreate) as caught:
            units.branch_name(WS, made["unit"], self.data)
        message = str(caught.exception)
        self.assertIn("intent.md", message)
        self.assertNotIn("No such work unit", message)

    def test_it_reads_the_type_the_intent_declares(self):
        made = units.create(WS, "a-problem", "", self.data)
        (Path(made["path"]) / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: fix. Status: accepted.\n", encoding="utf-8"
        )
        self.assertEqual(units.branch_name(WS, made["unit"], self.data), "fix/a-problem")

    def test_a_unit_that_is_not_there_is_refused_before_any_command_runs(self):
        with self.assertRaises(CannotCreate) as caught:
            units.branch_name(WS, "0099_nothing", self.data)
        self.assertIn("0099_nothing", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
