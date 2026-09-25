"""`0074`. The backlog fold, the checks and the proposal parser, each against the spec's own
"Kiểm được" line. No file, no database, no session: `backlog.py` has none to give them."""

from __future__ import annotations

import json
import unittest

from coscc import backlog as b


def _unit(name, stages=("idea",), nxt="write-intent", hold=None, number=None):
    return {
        "name": name,
        "number": number if number is not None else int(name[:4]),
        "stages": [{"stage": s, "status": "accepted"} for s in stages]
        + [{"stage": "plan", "status": "not started"}],
        "next": nxt,
        "hold": hold,
    }


def _est(unit, value, effort, by="Leif", basis="bớt can thiệp tay", **kw):
    return {"kind": "estimate-value", "unit": unit, "value": value, "effort": effort, "basis": basis,
            "by": by, "effort_source": "person", "at": kw.pop("at", "t"), **kw}


def _rel(unit, other, rtype, op="add", by="Leif"):
    return {"kind": "relation", "unit": unit, "other": other, "type": rtype, "op": op, "reason": "r", "by": by}


class TheBacklog(unittest.TestCase):
    def test_r1_only_the_idea_only_unit_and_the_paused_one(self):
        units = [
            _unit("0001_done", ("idea", "intent"), nxt="finished"),
            _unit("0002_dropped", ("idea", "intent"), nxt="dropped — x", hold={"state": "dropped"}),
            _unit("0003_idea_only"),
            _unit("0004_paused", ("idea", "intent"), nxt="paused — x", hold={"state": "paused"}),
            _unit("0005_rejected", nxt="closed — idea rejected"),
            {"name": "0006_nothing", "stages": [{"stage": "idea", "status": "not started"}], "next": "write-idea"},
        ]
        self.assertEqual(b.fold(units, [], {})["backlog"], ["0003_idea_only", "0004_paused"])


class Effort(unittest.TestCase):
    # Six finished units, costs and turns chosen so the terciles are easy to read by hand:
    # costs 1,2,3,4,5,6 → t1 = v[1] = 2, t2 = v[3] = 4. Turns 10..60 → t1 = 20, t2 = 40.
    FOUND = {f"000{i}_u": {"cost_usd": float(i), "turns": 10 * i} for i in range(1, 7)}

    def test_r4_the_band_matches_a_table_worked_by_hand(self):
        table = [
            (["0001_u"], "S"),              # $1, 10 turns
            (["0002_u"], "S"),              # $2 ≤ t1, 20 ≤ t1
            (["0003_u"], "M"),              # $3, 30
            (["0005_u"], "L"),              # $5 > t2
            (["0001_u", "0003_u"], "S"),    # median $2 → S, 20 → S
            (["0002_u", "0006_u"], "M"),    # median $4 → M, 40 → M
            (["0001_u", "0006_u"], "M"),    # median $3.5 → M, 35 → M
        ]
        for similar, want in table:
            got = b.effort_from(similar, self.FOUND)
            self.assertEqual((got["effort"], got["effort_source"]), (want, "measured"), similar)
            for s in similar:
                self.assertIn(s, got["effort_basis"])

    def test_r4_the_heavier_of_the_two_bands_wins(self):
        found = dict(self.FOUND)
        found["0001_u"] = {"cost_usd": 1.0, "turns": 100}
        self.assertEqual(b.effort_from(["0001_u"], found)["effort"], "L")

    def test_r5_five_measured_units_is_a_guess(self):
        five = dict(list(self.FOUND.items())[:5])
        got = b.effort_from(["0001_u"], five)
        self.assertEqual((got["effort"], got["effort_source"]), (None, "guess"))

    def test_r5_no_similar_unit_is_a_guess(self):
        self.assertEqual(b.effort_from([], self.FOUND)["effort_source"], "guess")
        self.assertEqual(b.effort_from(["9999_unknown"], self.FOUND)["effort_source"], "guess")

    def test_measured_counts_only_finished_units_that_reported_a_cost(self):
        units = [_unit("0001_a", nxt="finished"), _unit("0002_b", nxt="finished"), _unit("0003_c")]
        timelines = {
            "0001_a": [{"reported": True, "cost": {"cost_usd": 1.5, "turns": 7}}],
            "0002_b": [{"reported": False, "cost": {}}],
            "0003_c": [{"reported": True, "cost": {"cost_usd": 9.0, "turns": 1}}],
        }
        self.assertEqual(b.measured(timelines, units), {"0001_a": {"cost_usd": 1.5, "turns": 7}})


class Estimates(unittest.TestCase):
    def test_r2_out_of_range_is_refused(self):
        for value, effort, basis in ((0, "S", "x"), (6, "S", "x"), (True, "S", "x"), ("3", "S", "x"),
                                     (3, "XL", "x"), (3, "S", ""), (3, "S", "x" * 501)):
            self.assertTrue(b.check_estimate(value, effort, basis, "Leif", agent=False), (value, effort))
        self.assertEqual(b.check_estimate(3, "S", "x" * 500, "Leif", agent=False), "")

    def test_r7_a_person_cannot_type_an_agent_name(self):
        self.assertIn("agent:", b.check_estimate(3, "S", "x", "agent:abc", agent=False))

    def test_r3_an_agent_basis_must_name_a_goal(self):
        self.assertIn("names none", b.check_estimate(3, "S", "vì nó hay", "agent:s", agent=True))
        self.assertEqual(b.check_estimate(3, "S", "Bớt chi phí mỗi lượt", "agent:s", agent=True), "")

    def test_r6_agent_then_person_then_agent_keeps_the_person(self):
        records = [
            _est("0003_x", 2, "L", by="agent:s1", at="1"),
            _est("0003_x", 4, "S", by="Leif", basis="tôi thấy vậy", at="2"),
            _est("0003_x", 1, "M", by="agent:s2", at="3"),
        ]
        out = b.fold([_unit("0003_x")], records, {})
        entry = out["order"][0]
        self.assertEqual((entry["estimate"]["value"], entry["estimate"]["by"]), (4, "Leif"))
        self.assertEqual(entry["estimate"]["basis"], "tôi thấy vậy")
        self.assertEqual(entry["agent_differs"]["by"], "agent:s2")
        self.assertEqual(len(out["history"]["0003_x"]), 3)


class Relations(unittest.TestCase):
    NAMES = ["0001_a", "0002_b", "0003_c"]

    def check(self, unit, other, rtype, active, op="add"):
        return b.check_relation(unit, other, rtype, op, "why", "Leif", self.NAMES, active)

    def test_r8_every_refusal(self):
        self.assertIn("itself", self.check("0001_a", "0001_a", "liên quan", []))
        self.assertIn("no such", self.check("0001_a", "9999_z", "liên quan", []))
        self.assertIn("type", self.check("0001_a", "0002_b", "chặn", []))
        self.assertIn("already", self.check("0002_b", "0001_a", "trùng", [_rel("0001_a", "0002_b", "trùng")]))
        self.assertIn("not in effect", self.check("0001_a", "0002_b", "trùng", [], op="remove"))
        self.assertIn("replaces", self.check("0002_b", "0001_a", "thay thế", [_rel("0001_a", "0002_b", "thay thế")]))
        self.assertEqual(self.check("0001_a", "0002_b", "thay thế", []), "")

    def test_answer_1_a_dependency_cycle_is_refused(self):
        two = [_rel("0001_a", "0002_b", "phụ thuộc")]
        self.assertIn("cycle", self.check("0002_b", "0001_a", "phụ thuộc", two))
        three = two + [_rel("0002_b", "0003_c", "phụ thuộc")]
        self.assertIn("cycle", self.check("0003_c", "0001_a", "phụ thuộc", three))
        self.assertEqual(self.check("0001_a", "0003_c", "phụ thuộc", three), "")

    def test_the_last_record_of_a_pair_decides(self):
        records = [_rel("0001_a", "0002_b", "trùng"), _rel("0002_b", "0001_a", "trùng", op="remove")]
        self.assertEqual(b.relations_of(records), [])


class TheOrder(unittest.TestCase):
    def test_r11_value_then_effort_then_number(self):
        units = [_unit(n) for n in ("0001_a", "0002_b", "0003_c", "0004_d")]
        records = [_est("0001_a", 3, "M"), _est("0002_b", 5, "L"), _est("0003_c", 3, "S"), _est("0004_d", 3, "S")]
        out = b.fold(units, records, {})
        self.assertEqual([e["unit"] for e in out["order"]], ["0002_b", "0003_c", "0004_d", "0001_a"])

    def test_answer_1_a_value_5_that_depends_on_a_value_1_comes_after_it(self):
        units = [_unit(n) for n in ("0001_a", "0002_b", "0003_c")]
        records = [_est("0001_a", 5, "S"), _est("0002_b", 1, "L"), _est("0003_c", 3, "M"),
                   _rel("0001_a", "0002_b", "phụ thuộc")]
        out = b.fold(units, records, {})
        self.assertEqual([e["unit"] for e in out["order"]], ["0003_c", "0002_b", "0001_a"])

    def test_a_dependency_on_a_finished_unit_is_met_and_on_a_dropped_one_warns(self):
        units = [_unit("0001_a"), _unit("0002_done", nxt="finished"),
                 _unit("0003_gone", nxt="dropped — x", hold={"state": "dropped"})]
        records = [_est("0001_a", 3, "M"), _rel("0001_a", "0002_done", "phụ thuộc"),
                   _rel("0001_a", "0003_gone", "phụ thuộc")]
        out = b.fold(units, records, {})
        self.assertEqual([e["unit"] for e in out["order"]], ["0001_a"])
        self.assertEqual(len(out["warnings"]), 1)
        self.assertIn("0003_gone", out["warnings"][0]["text"])

    def test_a_cycle_written_into_the_log_by_hand_ends_last_and_never_loops(self):
        units = [_unit(n) for n in ("0001_a", "0002_b", "0003_c")]
        records = [_est("0001_a", 5, "S"), _est("0002_b", 5, "S"), _est("0003_c", 1, "L"),
                   _rel("0001_a", "0002_b", "phụ thuộc"), _rel("0002_b", "0001_a", "phụ thuộc")]
        out = b.fold(units, records, {})
        self.assertEqual([e["unit"] for e in out["order"]], ["0003_c", "0001_a", "0002_b"])
        self.assertTrue(any("cycle" in w["text"] for w in out["warnings"]))


class TheShortlist(unittest.TestCase):
    BACKLOG = [f"000{i}_u" for i in range(1, 10)]
    ESTS = {n: {} for n in BACKLOG[:8]}

    def test_r10_every_refusal(self):
        self.assertIn("empty", b.check_shortlist([], self.BACKLOG, self.ESTS))
        self.assertIn("at most", b.check_shortlist(self.BACKLOG[:8], self.BACKLOG, self.ESTS))
        self.assertIn("twice", b.check_shortlist(["0001_u", "0001_u"], self.BACKLOG, self.ESTS))
        self.assertIn("not in the backlog", b.check_shortlist(["9999_z"], self.BACKLOG, self.ESTS))
        self.assertIn("no estimate", b.check_shortlist(["0009_u"], self.BACKLOG, self.ESTS))
        self.assertEqual(b.check_shortlist(self.BACKLOG[:7], self.BACKLOG, self.ESTS), "")

    def test_answer_2_a_shortlist_that_drifts_everywhere_is_taken_and_marked(self):
        units = [_unit(n) for n in ("0001_a", "0002_b", "0003_c")]
        records = [_est("0001_a", 5, "S"), _est("0002_b", 3, "S"), _est("0003_c", 1, "S")]
        backlog = [u["name"] for u in units]
        ests = b.estimates_of(records)
        order = ["0003_c", "0001_a", "0002_b"]
        self.assertEqual(b.check_shortlist(order, backlog, ests), "")
        records.append({"kind": "shortlist", "units": order, "by": "Leif", "reason": "r", "at": "t"})
        out = b.fold(units, records, {})
        self.assertEqual([(e["unit"], e["drift"], e["computed"]) for e in out["shortlist"]],
                         [("0003_c", True, 3), ("0001_a", True, 1), ("0002_b", True, 2)])
        self.assertTrue(out["undiscriminating"])

    def test_r9_replaced_and_duplicate_units_carry_a_warning_and_stay(self):
        units = [_unit(n) for n in ("0001_a", "0002_b", "0003_c")]
        records = [_est(n, 3, "S") for n in ("0001_a", "0002_b", "0003_c")] + [
            _rel("0003_c", "0001_a", "thay thế"), _rel("0001_a", "0002_b", "trùng"),
            {"kind": "shortlist", "units": ["0001_a", "0002_b"], "by": "L", "reason": "r"},
        ]
        out = b.fold(units, records, {})
        self.assertEqual([e["unit"] for e in out["shortlist"]], ["0001_a", "0002_b"])
        self.assertTrue(any("replaced by" in w for w in out["shortlist"][0]["warnings"]))
        self.assertTrue(any("duplicates" in w for w in out["shortlist"][1]["warnings"]))
        self.assertEqual(out["per_unit"]["0001_a"]["rank"], 1)

    def test_r14_stamp(self):
        self.assertEqual(b.stamp([], "0001_a"), {"rank": None, "of": None, "record": None})
        records = [{"kind": "shortlist", "units": ["0002_b"], "at": "1"},
                   {"kind": "shortlist", "units": ["0002_b", "0001_a"], "at": "2"}]
        self.assertEqual(b.stamp(records, "0001_a"), {"rank": 2, "of": 2, "record": {"at": "2", "n": 2}})
        self.assertEqual(b.stamp(records, "0009_z")["rank"], None)


class Garbage(unittest.TestCase):
    def test_fold_never_raises_on_hand_edited_records(self):
        units = [_unit("0001_a"), _unit("0002_b")]
        records = [
            {"kind": "estimate-value"},
            {"kind": "estimate-value", "unit": "0001_a", "value": "five", "effort": "S", "basis": "x", "by": "L"},
            {"kind": "estimate-value", "unit": None, "value": 3, "effort": "S", "basis": "x", "by": "L"},
            {"kind": "relation", "unit": "0001_a", "other": None, "type": "trùng", "op": "add"},
            {"kind": "relation", "unit": ["x"], "other": "0002_b", "type": "phụ thuộc", "op": "add"},
            {"kind": "relation", "unit": "0001_a", "other": ["0002_b"], "type": "trùng", "op": "add"},
            {"kind": "relation", "unit": "0001_a", "other": {"n": 1}, "type": "trùng", "op": "add"},
            {"kind": "estimate-value", "unit": "0002_b", "value": 0, "effort": "S", "basis": "x",
             "by": "L", "similar": 7},
            {"kind": "estimate-value", "unit": "0002_b", "value": 0, "effort": "S", "basis": "x",
             "by": "L", "similar": True},
            {"kind": "shortlist", "units": "0001_a"},
            {"kind": "shortlist", "units": [1, 2]},
            {"kind": "shortlist", "units": ["9999_ghost"]},
            _est("0001_a", 3, "S"),
        ]
        out = b.fold(units, records, {})
        self.assertGreaterEqual(len(out["problems"]), 5)
        self.assertEqual(out["shortlist"][0]["unit"], "9999_ghost")
        self.assertFalse(out["shortlist"][0]["in_backlog"])
        self.assertEqual([e["unit"] for e in out["order"]], ["0001_a"])
        self.assertEqual(out["unestimated"], ["0002_b"])


class TheProposal(unittest.TestCase):
    BACKLOG = ["0001_a", "0002_b"]
    STORE = BACKLOG + ["0003_done"]

    def parse(self, reply, found=None, active=None):
        return b.parse_proposal(reply, self.BACKLOG, self.STORE, found or {}, "sess", active or [])

    def test_r3_a_basis_missing_all_three_goals_drops_only_that_unit(self):
        reply = "```json\n" + json.dumps({"units": [
            {"unit": "0001_a", "value": 4, "effort": "S", "similar": [], "basis": "bớt can thiệp tay: x"},
            {"unit": "0002_b", "value": 2, "effort": "M", "similar": [], "basis": "hay"},
        ]}) + "\n```"
        out = self.parse(reply)
        self.assertIsNone(out["failed"])
        self.assertEqual([r["unit"] for r in out["records"]], ["0001_a"])
        self.assertEqual(out["records"][0]["by"], "agent:sess")
        self.assertEqual(out["records"][0]["effort_source"], "guess")
        self.assertEqual(out["rejected"][0]["unit"], "0002_b")

    def test_not_json_writes_nothing(self):
        out = self.parse("I think 0001 is worth 4.")
        self.assertEqual(out["records"], [])
        self.assertIn("not JSON", out["failed"])

    def test_two_relations_in_one_reply_cannot_close_a_cycle(self):
        reply = json.dumps({"units": [
            {"unit": "0001_a", "value": 4, "effort": "S", "basis": "bớt chi phí",
             "relations": [{"type": "phụ thuộc", "other": "0002_b", "reason": "x"}]},
            {"unit": "0002_b", "value": 4, "effort": "S", "basis": "bớt chi phí",
             "relations": [{"type": "phụ thuộc", "other": "0001_a", "reason": "x"}]},
        ]})
        out = self.parse(reply)
        self.assertEqual(sum(1 for r in out["records"] if r["kind"] == "relation"), 1)
        self.assertTrue(any("cycle" in r["reason"] for r in out["rejected"]))

    def test_a_measured_effort_replaces_the_agents_word(self):
        found = {f"000{i}_f": {"cost_usd": float(i), "turns": 10 * i} for i in range(1, 7)}
        reply = json.dumps({"units": [
            {"unit": "0001_a", "value": 4, "effort": "S", "similar": ["0006_f"], "basis": "bớt chi phí"}]})
        rec = self.parse(reply, found)["records"][0]
        self.assertEqual((rec["effort"], rec["effort_source"]), ("L", "measured"))

    def test_the_prompt_carries_the_goals_and_the_units(self):
        text = b.build_prompt([{"unit": "0001_a", "idea": "words", "problem": "", "outcome": ""}],
                              [{"unit": "0003_done", "title": "Intent: t", "cost_usd": 1.2, "turns": 9}])
        for goal in b.VALUE_GOALS:
            self.assertIn(goal, text)
        self.assertIn("0001_a", text)
        self.assertIn("$1.20, 9 turns", text)


if __name__ == "__main__":
    unittest.main()
