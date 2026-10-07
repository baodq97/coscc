"""Tests for the state set, and for the claim that it comes from data.

Two of these carry most of the weight and they pull in opposite directions.

`TheDefaultIsTheSetInUseToday` reads the loop's stage table **by running it** and
compares its stage table to the set the pack's processes make, field by field. A hand-written expectation
would keep passing after the two drift apart, which is the failure `tests/units/test_board.py`
already argues against for the same reason.

It builds a set that shares no stage name, no artifact name and no status with the default, and asks
it every question the log asks. If any of those answers were computed from a literal in Python
rather than from the file, that test is where it shows."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests.units.test_meta import loop
from coscc.agent import pack
from coscc.units import states

REPO = Path(__file__).resolve().parents[2]

# Nothing here is a name the default uses. That is the whole design of the fixture: a
# module that answers correctly for this set cannot be answering from memory.
OTHER = {
    "name": "two-step",
    "absent": "nowhere",
    "settled": ["closed"],
    "stages": [
        {"name": "ticket", "artifact": "ticket.txt", "statuses": ["open", "closed"]},
        {
            "name": "wrap",
            "artifact": "wrap.txt",
            "optional": True,
            "statuses": ["open", "closed", "void"],
        },
    ],
}


class TheDefaultIsTheSetInUseToday(unittest.TestCase):
    def test_it_matches_the_stage_table_the_loop_prints(self):
        # Run rather than parsed. The loop's table is a literal in its source; asking the
        # program is the only reading that cannot go stale.
        out = loop(
            "--state",
            "-",
            "status",
            "--json",
            # The stage table needs no unit, so an empty snapshot.
            input='{"workspace": "", "units": {}}',
        ).stdout
        theirs = json.loads(out)["stages"]
        machine = states.default()

        self.assertEqual([s["name"] for s in theirs], list(machine.stage_names))
        self.assertEqual([s["file"] for s in theirs], list(machine.artifacts))
        for stage in theirs:
            mine = machine.stage(stage["name"])
            self.assertIsNotNone(mine, stage["name"])
            self.assertEqual(list(mine.statuses), stage["statuses"], stage["name"])
            self.assertEqual(mine.optional, bool(stage.get("optional")), stage["name"])

    def test_settled_means_what_the_loop_means_by_it(self):
        # `coscc/loop/model.py` `settled`. Counted on this, so a disagreement here moves
        # 0013's whole number.
        machine = states.default()
        for state in ("accepted", "skipped"):
            self.assertTrue(machine.is_settled(state), state)
        for state in ("draft", "rejected", "done", machine.absent):
            self.assertFalse(machine.is_settled(state), state)

    def test_the_definition_lives_inside_the_package_so_a_wheel_can_carry_it(self):
        # `coscc/agent/harness.py` had to learn this lesson at the cost of a unit. A default
        # sitting outside `coscc/` is a default a wheel does not ship.
        path = pack.BUILTIN / pack.PROCESS_FILE
        self.assertTrue(path.is_file(), path)
        package = Path(states.__file__).resolve().parents[1]
        self.assertTrue(path.is_relative_to(package / "packs"), path)
        self.assertEqual(package.name, "coscc")

    def test_a_status_follows_from_what_the_state_is(self):
        machine = states.default()
        self.assertEqual(machine.stage("review").statuses[1], "changes-requested")
        self.assertIn("skipped", machine.stage("spec").statuses)
        self.assertNotIn("skipped", machine.stage("plan").statuses)
        self.assertTrue(machine.stage("idea").optional)


class ADifferentStateSetIsAnsweredFromItsData(unittest.TestCase):
    """Not one name below is shared with the default."""

    def test_every_question_the_log_asks_is_answered_from_the_file(self):
        machine = states.Machine.of(OTHER)

        self.assertEqual(machine.name, "two-step")
        self.assertEqual(machine.stage_names, ("ticket", "wrap"))
        self.assertEqual(machine.artifacts, ("ticket.txt", "wrap.txt"))
        self.assertEqual(machine.absent, "nowhere")

        self.assertTrue(machine.is_settled("closed"))
        self.assertFalse(machine.is_settled("open"))
        # The default's settled words carry no meaning here, and that is the check.
        for stale in ("accepted", "done", "skipped"):
            self.assertFalse(machine.is_settled(stale), stale)

        self.assertTrue(machine.allows("ticket.txt", "open"))
        self.assertTrue(machine.allows("wrap.txt", "void"))
        self.assertTrue(machine.allows("ticket.txt", "nowhere"))
        self.assertFalse(machine.allows("ticket.txt", "void"))
        self.assertFalse(machine.allows("intent.md", "draft"))
        self.assertTrue(machine.stage("wrap").optional)
        self.assertFalse(machine.stage("ticket").optional)

    def test_a_refusal_names_this_set_rather_than_the_default(self):
        machine = states.Machine.of(OTHER)
        reason = machine.refuse("ticket.txt", "accepted")
        self.assertIn("two-step", reason)
        self.assertIn("open, closed", reason)
        self.assertNotIn("intent.md", reason)


class AStatusIsKeyedByTheUnitsProcess(unittest.TestCase):
    def test_a_skip_in_one_process_is_not_a_skip_in_another(self):
        machine = states.Machine.of(
            {
                "name": "m",
                "absent": "none",
                "settled": ["accepted", "skipped"],
                "stages": [
                    {"name": "a", "artifact": "a.md", "statuses": ["draft", "accepted", "skipped"]}
                ],
                "by_process": {
                    "p/skips": {"a.md": ["draft", "accepted", "skipped"]},
                    "p/keeps": {"a.md": ["draft", "accepted"]},
                },
            }
        )
        self.assertIsNone(machine.refuse("a.md", "skipped", "p/skips"))
        self.assertIn("cannot be 'skipped'", machine.refuse("a.md", "skipped", "p/keeps") or "")
        # No process named (or an artifact the process lacks): the union, as before.
        self.assertIsNone(machine.refuse("a.md", "skipped"))

    def test_the_built_in_review_may_ask_for_changes_in_both_processes(self):
        machine = states.default()
        for ref in pack.processes():
            self.assertTrue(machine.allows("review.md", "changes-requested", ref), ref)


class ThePackagedFilesAreCoherent(unittest.TestCase):
    """A mistake in either file would pass silently and report nothing, so the files are held to
    what the code that reads them assumes."""

    def test_settled_names_only_statuses_a_stage_carries_and_absent_is_none_of_them(self):
        machine = states.default()
        every = {s for stage in machine.stages for s in stage.statuses}
        self.assertLessEqual(machine.settled, every)
        self.assertNotIn(machine.absent, every)

    def test_every_state_of_every_process_is_a_stage_of_the_set(self):
        for ref, process in pack.processes().items():
            for name in process["states"]:
                self.assertIsNotNone(states.default().stage(name), f"{ref}.{name}")


if __name__ == "__main__":
    unittest.main()
