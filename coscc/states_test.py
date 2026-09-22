"""Tests for the state set, and for the claim that it is configuration.

Two of these carry most of the weight and they pull in opposite directions.

`TheDefaultIsTheSetInUseToday` reads `.claude/scripts/cos.mjs` **by running it** and
compares its stage table to `coscc/states.json` field by field. A hand-written expectation
would keep passing after the two drift apart, which is the failure `coscc/board_test.py`
already argues against for the same reason.

`ADifferentStateSetLoadsWithNoPythonChange` is `spec.md` R6's second half. It builds a set
that shares no stage name, no artifact name and no status with the default, and asks it
every question the log asks. If any of those answers were computed from a literal in Python
rather than from the file, that test is where it shows.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from coscc import states
from coscc.states import BadMachine

REPO = Path(__file__).resolve().parent.parent

# Nothing here is a name the default uses. That is the whole design of the fixture: a
# module that answers correctly for this set cannot be answering from memory.
OTHER = {
    "name": "two-step",
    "absent": "nowhere",
    "settled": ["closed"],
    "stages": [
        {"name": "ticket", "artifact": "ticket.txt", "statuses": ["open", "closed"]},
        {"name": "wrap", "artifact": "wrap.txt", "optional": True, "statuses": ["open", "closed", "void"]},
    ],
}


def write(raw: dict, into: Path) -> Path:
    into.write_text(json.dumps(raw), encoding="utf-8")
    return into


class TheDefaultIsTheSetInUseToday(unittest.TestCase):
    """`spec.md` R6, first half: eight stages and the statuses `cos.mjs` enforces."""

    def test_it_matches_the_stage_table_cos_mjs_prints(self):
        # Run rather than parsed. `cos.mjs` is JavaScript and its table is a literal in
        # the source; asking the program is the only reading that cannot go stale.
        out = subprocess.run(
            ["node", str(REPO / ".claude" / "scripts" / "cos.mjs"), "status", "--json"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
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

    def test_settled_means_what_cos_mjs_means_by_it(self):
        # `.claude/scripts/cos.mjs:123`. Counted on this, so a disagreement here moves
        # 0013's whole number.
        machine = states.default()
        for state in ("accepted", "skipped", "done"):
            self.assertTrue(machine.is_settled(state), state)
        for state in ("draft", "rejected", machine.absent):
            self.assertFalse(machine.is_settled(state), state)

    def test_the_definition_lives_inside_the_package_so_a_wheel_can_carry_it(self):
        # `coscc/harness.py` had to learn this lesson at the cost of a unit. A default
        # sitting outside `coscc/` is a default a wheel does not ship.
        self.assertTrue(states.DEFAULT_PATH.is_file(), states.DEFAULT_PATH)
        self.assertEqual(states.DEFAULT_PATH.parent.name, "coscc")


class ADifferentStateSetLoadsWithNoPythonChange(unittest.TestCase):
    """`spec.md` R6, second half. Not one name below is shared with the default."""

    def test_every_question_the_log_asks_is_answered_from_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            machine = states.load(write(OTHER, Path(tmp) / "other.json"))

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
        with tempfile.TemporaryDirectory() as tmp:
            machine = states.load(write(OTHER, Path(tmp) / "other.json"))
        reason = machine.refuse("ticket.txt", "accepted")
        self.assertIn("two-step", reason)
        self.assertIn("open, closed", reason)
        self.assertNotIn("intent.md", reason)


class ADefinitionThatWouldLieIsRefused(unittest.TestCase):
    def _bad(self, raw: dict) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BadMachine) as caught:
                states.load(write(raw, Path(tmp) / "bad.json"))
        return str(caught.exception)

    def test_settled_naming_a_status_no_stage_carries(self):
        # The one that would pass silently and report zero events. `0013`'s count is
        # "edited while settled", so a settled set that matches nothing reads as good news.
        raw = {**OTHER, "settled": ["cloesd"]}
        self.assertIn("cloesd", self._bad(raw))

    def test_a_stage_that_can_be_written_back_into_nothing(self):
        raw = {**OTHER, "stages": [{"name": "t", "artifact": "t.txt", "statuses": ["open", "nowhere"]}]}
        self.assertIn("nowhere", self._bad(raw))

    def test_two_stages_writing_the_same_artifact(self):
        raw = {
            **OTHER,
            "stages": [
                {"name": "a", "artifact": "same.txt", "statuses": ["open"]},
                {"name": "b", "artifact": "same.txt", "statuses": ["open"]},
            ],
            "settled": [],
        }
        self.assertIn("same.txt", self._bad(raw))

    def test_no_stages_at_all(self):
        self.assertIn("stages", self._bad({**OTHER, "stages": []}))

    def test_no_absent_state(self):
        raw = dict(OTHER)
        raw.pop("absent")
        self.assertIn("absent", self._bad(raw))

    def test_a_file_that_is_not_there_names_the_path_it_looked_for(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.json"
            with self.assertRaises(BadMachine) as caught:
                states.load(missing)
        self.assertIn(str(missing), str(caught.exception))


if __name__ == "__main__":
    unittest.main()
