"""`0138` step 2. The transcript reader, on lines shaped like the ones `spike.md ## U2` printed."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from coscc.agent import transcript


def prompt(uid, text="Run these commands"):
    return {"type": "user", "uuid": uid, "message": {"role": "user", "content": text}}


def call(uid, mid, tid, command, text=None):
    blocks = ([{"type": "text", "text": text}] if text else []) + [
        {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": command}}]
    return {"type": "assistant", "uuid": uid, "message": {"id": mid, "role": "assistant", "content": blocks}}


def said(uid, mid, text):
    return {"type": "assistant", "uuid": uid, "message": {"id": mid, "content": [{"type": "text", "text": text}]}}


def result(uid, tid, text):
    return {"type": "user", "uuid": uid, "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": text}]}}


REFUSED = "The user doesn't want to proceed with this tool use."


class Reading(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "s.jsonl"

    def write(self, before, after=(), tail=""):
        body = "".join(json.dumps(x) + "\n" for x in before)
        self.path.write_text(body, encoding="utf-8")
        edge = transcript.boundary(self.path)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("".join(json.dumps(x) + "\n" for x in after) + tail)
        return edge

    def test_the_project_directory_replaces_every_non_alphanumeric_character(self):
        got = transcript.path_for("/home/bd/.cos/w_1/0138_x", "sid", root=Path("/r"))
        self.assertEqual(got, Path("/r/-home-bd--cos-w-1-0138-x/sid.jsonl"))

    def test_an_interrupted_bash_call_cuts_to_the_last_real_result(self):
        edge = self.write(
            [prompt("p"), call("a1", "m1", "t1", "echo one"), result("r1", "t1", "one"),
             call("a2", "m2", "t2", "echo two"), result("r2", "t2", "two"),
             call("a3", "m3", "t3", "sh ./u3loop.sh")],
            [result("fake", "t3", REFUSED), prompt("int", "[Request interrupted by user for tool use]")],
        )
        got = transcript.cut(self.path, edge)
        self.assertEqual(got["safe_uuid"], "r2")
        self.assertEqual(got["dropped"], [{"name": "Bash", "input": "sh ./u3loop.sh"}])

    def test_a_parallel_turn_half_answered_is_dropped_whole(self):
        edge = self.write(
            [prompt("p"), call("a0", "m0", "t0", "echo zero"), result("r0", "t0", "zero"),
             call("a1", "m1", "t1", "sleep 1; echo p1"), call("a2", "m1", "t2", "sh ./u3loop.sh; echo p2"),
             result("r1", "t1", "p1"), call("a3", "m1", "t3", "sleep 1; echo p3")],
            [result("f2", "t2", "Exit code 137")],
        )
        got = transcript.cut(self.path, edge)
        self.assertEqual(got["safe_uuid"], "r0")
        self.assertEqual([d["input"] for d in got["dropped"]],
                         ["sleep 1; echo p1", "sh ./u3loop.sh; echo p2", "sleep 1; echo p3"])

    def test_a_turn_with_no_tool_cuts_to_its_prompt(self):
        edge = self.write([prompt("p")], [said("x", "m", "half")])
        self.assertEqual(transcript.cut(self.path, edge)["safe_uuid"], "p")

    def test_api_calls_are_counted_once_per_message_id(self):
        edge = self.write([
            prompt("p"), call("a1", "m1", "t1", "one", text="first"), call("a2", "m1", "t2", "two"),
            result("r1", "t1", "1"), result("r2", "t2", "2"), call("a3", "m2", "t3", "three"),
        ])
        self.assertEqual(transcript.cut(self.path, edge)["api_calls"], 2)

    def test_a_partial_last_line_is_not_inside_the_boundary(self):
        edge = self.write([prompt("p")], tail='{"type": "assistant", "uu')
        self.assertEqual(edge, 1)
        self.assertEqual(transcript.boundary(self.path), 1)
        self.assertEqual(transcript.cut(self.path, edge)["safe_uuid"], "p")

    def test_a_broken_line_before_the_boundary_is_unreadable(self):
        self.path.write_text(json.dumps(prompt("p")) + "\n{not json\n", encoding="utf-8")
        with self.assertRaises(transcript.Unreadable):
            transcript.cut(self.path, transcript.boundary(self.path))

    def test_cost_state_after_the_boundary_is_what_was_spent(self):
        edge = self.write(
            [prompt("p"), {"type": "cost-state", "totalCostUSD": 0.01}],
            [{"type": "cost-state", "totalCostUSD": 0.0126}],
        )
        self.assertEqual(transcript.spent_after(self.path, edge), 0.0126)

    def test_no_cost_state_after_the_boundary_is_cost_unknown(self):
        edge = self.write([prompt("p"), {"type": "cost-state", "totalCostUSD": 0.01}])
        self.assertIsNone(transcript.spent_after(self.path, edge))

    def test_pieces_before_the_safe_point_split_at_tool_calls(self):
        edge = self.write([
            prompt("p"), call("a1", "m1", "t1", "ls", text="Looking."), result("r1", "t1", "x"),
            said("s2", "m2", "# Impl: t\nStatus: draft."), prompt("q", "go on"),
            said("s3", "m3", "after the safe point"),
        ])
        got = transcript.cut(self.path, edge)
        self.assertEqual(got["safe_uuid"], "q")
        self.assertEqual(got["pieces"], ["Looking.", "# Impl: t\nStatus: draft."])


if __name__ == "__main__":
    unittest.main()
