"""Tests for where a workspace's units live, and for starting one.

`python -m coscc.loop` is run for real here rather than stubbed. What is under test is an agreement
with the loop — the shape it prints, the path it prints it relative to, what it says
about a bad slug — and a stub agrees with whatever it was told. `tests/units/test_board.py:1-7`
makes the same argument for the same loop.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from tests.units.test_meta import snapshot_of
from coscc import units
from coscc.units import BadUnit, CannotCreate

WS = "/tmp/a-workspace"


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = self._tmp.name


class OneFunctionAnswersWhereUnitsLive(Fixture):
    """Three modules used to work this out for themselves."""

    def test_the_store_is_under_the_data_root_and_not_in_the_workspace(self):
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

    def test_a_unit_name_from_a_request_cannot_walk_out_of_the_store(self):
        for bad in ("../../etc", "0001", "0001_Bad_Slug", "", "0001_x/../../y"):
            with self.assertRaises(BadUnit, msg=bad):
                units.unit_dir(WS, bad, self.data)


class StartingAUnit(Fixture):
    def test_the_number_comes_from_the_loop_and_counts_up(self):
        first = units.create(WS, "a-first-problem", "", self.data)
        second = units.create(WS, "a-second-problem", "", self.data)
        self.assertEqual(first["unit"], "0001_a-first-problem")
        self.assertEqual(second["unit"], "0002_a-second-problem")

    def test_the_directory_it_names_is_the_directory_it_makes(self):
        made = units.create(WS, "a-problem", "", self.data)
        self.assertTrue(Path(made["path"]).is_dir())
        self.assertEqual(Path(made["path"]).parent, units.cos_dir(WS, self.data))

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
    def _host(self, *names: str) -> Path:
        host = Path(self.data) / "host"
        for n in names:
            (host / ".cos" / n).mkdir(parents=True)
        return host

    def test_nothing_is_written_into_the_host(self):
        host = self._host("0001_a", "0014_n")
        before = sorted(p.name for p in (host / ".cos").iterdir())
        units.create(host, "fresh", "", self.data, reserve_from=[host])
        self.assertEqual(sorted(p.name for p in (host / ".cos").iterdir()), before)

    def test_the_number_is_whatever_the_loop_prints(self):
        """The number is whatever the loop prints: if Python worked it out itself, it would not be
        the fake loop's."""
        from unittest import mock

        fake = Path(self.data) / "fake_loop.py"
        seen = Path(self.data) / "argv.json"
        fake.write_text(
            "import json, sys\n"
            f"open({str(seen)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
            "print('.cos/0042_fixed')\n",
            encoding="utf-8",
        )
        host = self._host("0014_n")
        with mock.patch(
            "coscc.loop.run.argv", lambda args: [sys.executable, "-P", str(fake), *args]
        ):
            made = units.create(host, "anything", "", self.data, reserve_from=[host])
        self.assertEqual(made["unit"], "0042_fixed")

        import json

        argv = json.loads(seen.read_text(encoding="utf-8"))
        at = argv.index("--reserve-from")
        self.assertEqual(argv[at + 1], str(host.resolve()))
        # Before the command, so an older loop refuses instead of ignoring it (plan Risk 3).
        self.assertLess(at, argv.index("new-path"))


class TheBriefBecomesTheIdea(Fixture):
    """`plan.md` `## OQ1, settled before planning`."""

    def test_it_is_written_as_idea_md_with_a_status_the_loop_accepts(self):
        made = units.create(WS, "a-problem", "Nút Run không nói gì khi hỏng.", self.data)
        text = (Path(made["path"]) / "idea.md").read_text(encoding="utf-8")
        self.assertIn("Nút Run không nói gì khi hỏng.", text)
        self.assertIn("Status: accepted.", text)

    def test_the_loop_reads_it_back_as_an_accepted_idea(self):
        # The only check that matters: the loop has to agree it is a readable artifact,
        # because a file it calls broken blocks the unit rather than helping it.
        made = units.create(WS, "a-problem", "some words", self.data)
        store = units.root(WS, self.data)
        printed = units._cos(store, "--state", "-", "status", stdin=json.dumps(snapshot_of(store)))
        self.assertIn(made["unit"], printed)
        row = next(line for line in printed.splitlines() if made["unit"] in line)
        self.assertEqual(row.split("|")[2].strip(), "A", row)

    def test_no_brief_writes_no_file_rather_than_an_empty_one(self):
        made = units.create(WS, "a-problem", "   ", self.data)
        self.assertFalse((Path(made["path"]) / "idea.md").exists())
        self.assertFalse(made["brief"])


class TheBranchNameComesFromTheIntent(Fixture):
    def test_it_refuses_before_the_intent_stage_has_run_and_says_which_file_is_missing(self):
        # The loop's `unit-branch` says `No such work unit` here, which is true of the file
        # it reads and false of the unit. Measured 2026-09-22.
        made = units.create(WS, "a-problem", "", self.data)
        with self.assertRaises(CannotCreate) as caught:
            units.branch_name(WS, made["unit"], self.data, snapshot_of(units.root(WS, self.data)))
        message = str(caught.exception)
        self.assertIn("intent.md", message)
        self.assertNotIn("No such work unit", message)

    def test_it_reads_the_type_the_intent_declares(self):
        made = units.create(WS, "a-problem", "", self.data)
        (Path(made["path"]) / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: fix. Status: accepted.\n", encoding="utf-8"
        )
        self.assertEqual(
            units.branch_name(WS, made["unit"], self.data, snapshot_of(units.root(WS, self.data))),
            "fix/a-problem",
        )

    def test_a_unit_that_is_not_there_is_refused_before_any_command_runs(self):
        with self.assertRaises(CannotCreate) as caught:
            units.branch_name(WS, "0099_nothing", self.data, snapshot_of(units.root(WS, self.data)))
        self.assertIn("0099_nothing", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
