"""`0044`. Jera's pure half: the store, the questions, the prompt and the filter."""

from __future__ import annotations

import asyncio
import unittest

from coscc.agent import precedent
from coscc.agent import submit
from coscc.agent.policy import SUBMIT_TURNS, grant_for
from coscc.agent.submit_test import submits as _submits


def _unit(name: str, answers=(), questions=(), why: str = "next-stage") -> dict:
    return {"name": name, "answers": list(answers), "questions": list(questions), "why": why}


def _answer(artifact: str, n: int, by: str = "owner", via: str = "product", text: str = "yes") -> dict:
    return {"artifact": artifact, "n": n, "question": f"q{n}?", "by": by, "date": "2026-09-01", "via": via,
            "text": text}


Q = [{"unit": "0002_b", "artifact": "spec.md", "n": 1, "text": "a?"},
     {"unit": "0002_b", "artifact": "spec.md", "n": 2, "text": "b?"}]
# `0137`: the store's ids, each with who decided it; both the person's, so the older cases
# below keep the meaning they had before R8.
IDS = {"pref:1": "originator", "0001_a/spec.md#Câu 1": "originator"}


def _reply(*items: dict) -> dict:
    """The object Jera hands back through `submit` (`0136` R8)."""
    return {"verdicts": list(items)}


def _json_of(items: list) -> str:
    import json

    return json.dumps(items, ensure_ascii=False)


def _ok(n: int = 1, **over) -> dict:
    return {"artifact": "spec.md", "n": n, "verdict": "answer", "category": "other", "text": "Có.",
            "reason": "", "cites": ["0001_a/spec.md#Câu 1"], **over}


class TheStore(unittest.TestCase):
    def test_each_paragraph_of_the_preferences_is_one_entry(self):
        got = precedent.entries([], "Một.\ncòn một.\n\n  \n\nHai.\n\n", [])
        self.assertEqual([{k: e[k] for k in ("id", "text")} for e in got],
                         [{"id": "pref:1", "text": "Một.\ncòn một."}, {"id": "pref:2", "text": "Hai."}])

    def test_answers_in_force_are_entries_by_unit_artifact_and_number(self):
        got = precedent.entries([_unit("0001_a", [_answer("spec.md", 3, by="Leif (CoS agent)")])], "", [])
        self.assertEqual([e["id"] for e in got], ["0001_a/spec.md#Câu 3"])
        self.assertIn("q3?", got[0]["text"])
        self.assertIn("Leif (CoS agent)", got[0]["text"], "C3: Leif's answers stay in")

    def test_jeras_own_answers_are_not_precedent(self):
        units = [_unit("0001_a", [_answer("spec.md", 1, by=" jera "), _answer("spec.md", 2, via="precedent"),
                                  _answer("spec.md", 3)])]
        self.assertEqual([e["id"] for e in precedent.entries(units, "", [])], ["0001_a/spec.md#Câu 3"])

    def test_the_asked_unit_never_cites_itself(self):
        units = [_unit("0001_a", [_answer("spec.md", 1)]), _unit("0002_b", [_answer("intent.md", 1)])]
        self.assertEqual([e["id"] for e in precedent.entries(units, "", {"0002_b"})], ["0001_a/spec.md#Câu 1"])


class TheQuestions(unittest.TestCase):
    def test_only_unanswered_and_never_review(self):
        u = _unit("0002_b", questions=[
            {"artifact": "spec.md", "n": 1, "text": "a", "answered": False},
            {"artifact": "spec.md", "n": 2, "text": "b", "answered": True},
            {"artifact": "review.md", "n": 1, "text": "c", "answered": False},
        ])
        self.assertEqual(precedent.asked(u), [{"unit": "0002_b", "artifact": "spec.md", "n": 1, "text": "a"}])

    def test_none_on_a_finished_or_closed_unit(self):
        """`0136` R11: by `why`, the rule of `decide` that answered, not by `next`'s words."""
        q = [{"artifact": "spec.md", "n": 1, "text": "a", "answered": False}]
        for why in ("finished", "rejected"):
            self.assertEqual(precedent.asked({**_unit("0002_b", questions=q, why=why), "next": "anything"}), [], why)
        self.assertEqual(len(precedent.asked({**_unit("0002_b", questions=q), "next": "finished"})), 1)


class ThePrompt(unittest.TestCase):
    def test_it_names_every_category_the_store_and_no_path(self):
        p = precedent.build_prompt(Q, [{"id": "pref:1", "text": "Luôn rẻ."}])
        for c in precedent.CATEGORIES:
            self.assertIn(c, p)
        self.assertIn("### pref:1", p)
        self.assertIn("Luôn rẻ.", p)
        self.assertIn("Best practice", p)
        self.assertIn("Vietnamese", p)
        self.assertNotIn("/home/", p)


class TheFilter(unittest.TestCase):
    def verdict(self, *items, ids=IDS) -> list[dict]:
        got = precedent.verdicts(_reply(*items), Q, ids)
        self.assertIsNone(got["failed"])
        return got["verdicts"]

    def test_no_object_fails_whole(self):
        for none in (None, {}, {"verdicts": "no list"}):
            got = precedent.verdicts(none, Q, IDS)
            self.assertEqual(got["failed"], precedent.NO_OBJECT)
            self.assertEqual(got["verdicts"], [])

    def test_a_cited_answer_stays_an_answer(self):
        v = self.verdict(_ok(1), _ok(2))
        self.assertEqual([x["verdict"] for x in v], ["answer", "answer"])
        self.assertEqual(v[0]["cites"], ["0001_a/spec.md#Câu 1"])

    def test_a_question_left_out_needs_a_person(self):
        v = self.verdict(_ok(1))
        self.assertEqual((v[1]["verdict"], v[1]["reason"].startswith(precedent.NOT_ANSWERED)), ("needs-person", True))

    def test_a_malformed_verdict_needs_a_person(self):
        v = self.verdict(_ok(1, verdict="maybe"), _ok(2, cites="0001_a/spec.md#Câu 1"))
        self.assertEqual([x["verdict"] for x in v], ["needs-person", "needs-person"])
        self.assertTrue(all(x["reason"].startswith(precedent.NOT_ANSWERED) for x in v))

    def test_review_and_findings_are_ignored_not_written(self):
        got = precedent.verdicts(_reply(_ok(1, artifact="review.md"), _ok("F1", artifact="review.md"), _ok(1)), Q, IDS)
        self.assertEqual(len(got["ignored"]), 2)
        self.assertEqual([(x["artifact"], x["n"]) for x in got["verdicts"]], [("spec.md", 1), ("spec.md", 2)])

    def test_no_citation_or_an_unknown_one_needs_a_person(self):
        v = self.verdict(_ok(1, cites=[]), _ok(2, cites=["0001_a/spec.md#Câu 1", "9999_x/spec.md#Câu 1"]))
        self.assertEqual([x["verdict"] for x in v], ["needs-person", "needs-person"])
        self.assertIn("9999_x", v[1]["reason"])
        self.assertEqual(v[1]["text"], "Có.", "the proposal is still the reply's words")

    def test_the_categories_that_need_a_person_and_an_unknown_one_need_a_person(self):
        # `0101` R7: `business-tradeoff` is no category any more, so it is an unknown one.
        for c in precedent.NEEDS_PERSON + ("whatever", "", "business-tradeoff"):
            v = self.verdict(_ok(1, category=c), _ok(2))
            self.assertEqual(v[0]["verdict"], "needs-person", c)
            self.assertEqual(v[1]["verdict"], "answer", c)

    def test_a_heading_line_needs_a_person(self):
        v = self.verdict(_ok(1, text="Có.\n## Answers"), _ok(2))
        self.assertEqual(v[0]["verdict"], "needs-person")

    def test_an_empty_proposal_is_kept_and_said(self):
        v = self.verdict(_ok(1, verdict="needs-person", text="", reason="tiền"), _ok(2))
        self.assertEqual(v[0]["verdict"], "needs-person")
        self.assertIn("proposal is empty", v[0]["reason"])

    def test_a_needs_person_verdict_keeps_jeras_reason(self):
        v = self.verdict(_ok(1, verdict="needs-person", category="significant-spend", reason="tốn tiền"), _ok(2))
        self.assertEqual((v[0]["verdict"], v[0]["reason"], v[0]["category"]), ("needs-person", "tốn tiền", "significant-spend"))


class TheCeiling(unittest.TestCase):
    """`0101` R5."""

    def test_ceiling_is_the_floor_for_the_spike_prompt(self):
        self.assertEqual(precedent.ceiling(64220), 1.00)

    def test_ceiling_for_the_full_store_is_1_55(self):
        self.assertEqual(precedent.ceiling(178405), 1.55)

    def test_ceiling_never_falls_as_the_prompt_grows(self):
        last = 0.0
        for chars in range(0, 500001, 997):
            got = precedent.ceiling(chars)
            self.assertGreaterEqual(got, last, chars)
            self.assertEqual(round(got * 20, 6) % 1, 0, f"{chars}: not a step of 0.05")
            last = got

    def test_ceiling_passes_the_absolute_cap_past_about_362500_chars(self):
        self.assertEqual(precedent.ceiling(362500), precedent.PRECEDENT_MAX_USD)
        self.assertGreater(precedent.ceiling(362501), precedent.PRECEDENT_MAX_USD)

    def test_the_grant_for_a_prompt_is_the_grant_with_its_ceiling(self):
        base = grant_for("precedent")
        got = precedent.grant_for_prompt(base, "x" * 178405)
        self.assertEqual((got.max_budget_usd, got.max_turns, got.tools), (1.55, base.max_turns, base.tools))
        self.assertEqual(base.max_budget_usd, 1.0, "the static grant is the floor, untouched")


class TheCategories(unittest.TestCase):
    """`0101` R6, R7."""

    def verdict(self, *items) -> list[dict]:
        return precedent.verdicts(_reply(*items), Q, IDS)["verdicts"]

    def test_needs_person_has_exactly_the_four_categories(self):
        self.assertEqual(precedent.NEEDS_PERSON, (
            "product-direction", "security-or-permissions", "significant-spend", "external-action",
        ))
        self.assertEqual(precedent.CATEGORIES, precedent.NEEDS_PERSON + ("other",))

    def test_an_answer_in_one_of_the_four_categories_needs_a_person(self):
        for c in precedent.NEEDS_PERSON:
            for cites in (["0001_a/spec.md#Câu 1"], [precedent.PRACTICE]):
                v = self.verdict(_ok(1, category=c, cites=cites), _ok(2))
                self.assertEqual((v[0]["verdict"], v[1]["verdict"]), ("needs-person", "answer"), (c, cites))

    def test_practice_answers_a_question_of_category_other(self):
        v = self.verdict(_ok(1, cites=["practice"], text="Theo thông lệ: dùng UTC."), _ok(2))
        self.assertEqual((v[0]["verdict"], v[0]["cites"]), ("answer", ["practice"]))
        self.assertTrue(precedent.block_text(v[0]).endswith("Tiền lệ: practice"))

    def test_practice_on_any_other_category_needs_a_person(self):
        for c in ("whatever", ""):
            v = self.verdict(_ok(1, category=c, cites=["practice"]), _ok(2))
            self.assertEqual(v[0]["verdict"], "needs-person", c)
        v = self.verdict(_ok(1, category="significant-spend", cites=["practice"]), _ok(2))
        self.assertIn("practice", v[0]["reason"])


class TheRules(unittest.TestCase):
    """`0101` R8."""

    def test_prompt_carries_the_rules_it_is_given(self):
        default = precedent.build_prompt(Q, [])
        self.assertIn("## Rules\n\n" + precedent.DEFAULT_RULES, default)
        mine = precedent.build_prompt(Q, [], rules="Always ask me first.")
        self.assertIn("## Rules\n\nAlways ask me first.", mine)
        self.assertNotIn(precedent.DEFAULT_RULES, mine)
        self.assertIn("`practice`", mine, "how to cite a best practice is not the rules' to remove")

    def test_the_default_rules_name_no_one(self):
        for name in ("Leif", "CoS", "coscc", "github.com", "/home/"):
            self.assertNotIn(name, precedent.DEFAULT_RULES)


class TheBlock(unittest.TestCase):
    def test_cites_read_back_what_block_text_wrote(self):
        v = {"text": "Có.\nhai dòng", "cites": ["pref:1", "0001_a/spec.md#Câu 1"]}
        text = precedent.block_text(v)
        self.assertTrue(text.endswith("Tiền lệ: pref:1; 0001_a/spec.md#Câu 1"))
        self.assertEqual(precedent.cites_of(text), v["cites"])
        self.assertEqual(precedent.cites_of("no line"), [])
        self.assertEqual(precedent.words_of(text), v["text"])
        self.assertEqual(precedent.words_of("no line"), "no line")

    def test_jera_is_matched_however_it_is_typed(self):
        self.assertTrue(all(precedent.is_jera(n) for n in ("Jera", " jera ", "JERA")))
        self.assertFalse(precedent.is_jera("Jerald"))


def _decision(n: int, kind: str = "delegation", agent: str = "Leif", workspace: str = "",
              from_day: str = "2026-09-01", until_day: str = "", withdrawn: str = "", text: str = "d") -> dict:
    return {"id": n, "kind": kind, "text": text, "source": "chat 2026-09-01", "workspace": workspace,
            "agent": agent, "covers": "naming", "from_day": from_day, "until_day": until_day,
            "withdrawn": withdrawn}


class WhoDecided(unittest.TestCase):
    """`0137` R1, R2."""

    WS = "proj-abc"

    def who(self, by: str, text: str = "Có.", date: str = "2026-09-10", decisions=(), mine=(), agents=("Kenaz",)):
        return precedent.decided_by({"by": by, "date": date, "text": text}, self.WS, list(decisions), mine, agents)

    def test_decided_by_takes_each_branch_from_a_table_of_cases(self):
        line = "Có.\n\nTheo ủy quyền: D1"
        good = _decision(1)
        cases = [
            # (by, text, decisions, mine, expected, why)
            ("owner", "Có.", [], [], "originator", "owner"),
            ("  OWNER ", "Có.", [], [], "originator", "owner, case and spaces aside"),
            ("Phong", "Có.", [], ["phong"], "originator", "a name marked mine"),
            ("Phong", "Có.", [], [], "inferred", "a name nobody marked"),
            ("Leif (CoS), thay người khởi xướng", "Có.", [], [], "inferred", "Leif"),
            ("Leifson", "Có.", [], ["leifson"], "originator", "a person whose name only opens like Leif's"),
            ("Kenaz", "Có.", [], ["kenaz"], "inferred", "an agent's name marked mine is still an agent's"),
            ("Leif", "Có.", [], ["leif"], "inferred", "Leif marked mine is still Leif"),
            ("Leif (CoS)", "Có.\n\nTheo ủy quyền: D9", [good], [], "inferred", "a delegation that does not exist"),
            ("Leif", line, [_decision(1, kind="decision")], [], "inferred", "a decision, not a delegation"),
            ("Leif", line, [_decision(1, until_day="2026-09-05")], [], "inferred", "expired before Date:"),
            ("Leif", line, [_decision(1, withdrawn="2026-09-10")], [], "inferred", "withdrawn on Date:"),
            ("Leif", line, [_decision(1, from_day="2026-09-11")], [], "inferred", "entered after Date:"),
            ("Jera", line, [good], [], "inferred", "the wrong agent"),
            ("Leif", line, [_decision(1, workspace="other-123")], [], "inferred", "the wrong workspace"),
            ("owner", line, [good], [], "originator", "a person citing a delegation falls to branch 2"),
            ("Leif (CoS)", "Theo ủy quyền: D1\n\nCó.", [good], [], "inferred", "not the last line"),
            ("Leif (CoS)", line, [good], [], "delegated", "a delegation in force"),
            ("leif", line + "\n\n", [_decision(1, workspace=self.WS, withdrawn="2026-09-20")], [], "delegated",
             "withdrawn after Date: keeps the label"),
        ]
        for by, text, decisions, mine, expected, why in cases:
            self.assertEqual(self.who(by, text, decisions=decisions, mine=mine), expected, why)

    def test_an_agent_name_needs_a_word_boundary(self):
        self.assertTrue(precedent.is_agent_name("Leif (CoS agent)", []))
        self.assertTrue(precedent.is_agent_name("kenaz", ["Kenaz"]))
        self.assertFalse(precedent.is_agent_name("Leifson", []))
        self.assertFalse(precedent.is_agent_name("", []))


class TheLabelledStore(unittest.TestCase):
    """`0137` R3, R4."""

    WS = "proj-abc"

    def store(self, units=(), prefs="", decisions=(), mine=(), today="2026-09-28"):
        return precedent.entries(list(units), prefs, [], workspace=self.WS, decisions=list(decisions), mine=mine,
                                 agents=["Kenaz"], today=today)

    def test_every_store_entry_has_who_source_scope_and_term(self):
        units = [_unit("0001_a", [_answer("spec.md", 1), _answer("spec.md", 2, by="Leif (CoS)"),
                                  _answer("intent.md", 1, by="Leif", text="x\n\nTheo ủy quyền: D2")])]
        got = self.store(units, "Một.\n\nHai.", [_decision(1, kind="decision"), _decision(2)])
        self.assertEqual({e["who"] for e in got}, {"originator", "delegated", "inferred"})
        for e in got:
            for field in ("who", "source", "scope", "term"):
                self.assertTrue(e.get(field), (e["id"], field))
        by_id = {e["id"]: e for e in got}
        self.assertEqual(by_id["0001_a/spec.md#Câu 1"]["source"], "0001_a/spec.md ## Answers, câu 1, 2026-09-01")
        self.assertEqual((by_id["pref:1"]["source"], by_id["pref:1"]["scope"]),
                         ("Settings: Decision preferences", "every workspace"))
        self.assertEqual((by_id["D1"]["who"], by_id["D1"]["term"]), ("originator", "until withdrawn"))
        self.assertTrue(by_id["D2"]["text"].startswith("Delegation to Leif for: naming.\n"))

    def test_a_decision_enters_the_store_only_in_force_and_in_scope(self):
        ds = [_decision(1, kind="decision"), _decision(2, kind="decision", workspace=self.WS, until_day="2026-12-31"),
              _decision(3, kind="decision", workspace="other-1"), _decision(4, kind="decision", until_day="2026-09-27"),
              _decision(5, kind="decision", withdrawn="2026-09-28"), _decision(6, kind="decision", from_day="2026-09-29")]
        got = {e["id"]: e for e in self.store(decisions=ds)}
        self.assertEqual(sorted(got), ["D1", "D2"])
        self.assertEqual((got["D2"]["scope"], got["D2"]["term"]), ("this workspace", "until 2026-12-31"))
        self.assertEqual(self.store(decisions=ds, today=""), [], "no day, nothing in force")

    def test_leifs_answers_stay_in_the_store_as_inferred(self):
        got = self.store([_unit("0001_a", [_answer("spec.md", 3, by="Leif (CoS agent), thay người khởi xướng")])])
        self.assertEqual([(e["id"], e["who"]) for e in got], [("0001_a/spec.md#Câu 3", "inferred")])
        self.assertIn("Leif (CoS agent)", got[0]["text"])


class TheWeighing(unittest.TestCase):
    """`0137` R7, R8."""

    WHO = {"pref:1": "originator", "0001_a/spec.md#Câu 1": "inferred", "0001_a/spec.md#Câu 2": "inferred",
           "0001_a/spec.md#Câu 3": "delegated"}

    def verdict(self, *items) -> list[dict]:
        return precedent.verdicts(_reply(*items), Q, self.WHO)["verdicts"]

    def test_an_answer_citing_only_inferences_needs_a_person(self):
        v = self.verdict(_ok(1, cites=["0001_a/spec.md#Câu 1", "0001_a/spec.md#Câu 2"]), _ok(2))
        self.assertEqual((v[0]["verdict"], v[0]["reason"]), ("needs-person", precedent.ONLY_INFERRED))
        self.assertEqual(v[0]["text"], "Có.", "the proposal is still the reply's")

    def test_one_originator_or_delegated_cite_or_practice_is_enough(self):
        for extra in ("pref:1", "0001_a/spec.md#Câu 3", "practice"):
            v = self.verdict(_ok(1, cites=["0001_a/spec.md#Câu 1", extra]), _ok(2, cites=["pref:1"]))
            self.assertEqual([x["verdict"] for x in v], ["answer", "answer"], extra)

    def test_an_older_reason_is_kept_before_r8s(self):
        v = self.verdict(_ok(1, cites=["0001_a/spec.md#Câu 1"], category="significant-spend"), _ok(2))
        self.assertEqual(v[0]["reason"], "Its category, significant-spend, needs a person.")

    def test_the_prompt_lists_precedent_in_three_parts_in_order(self):
        units = [_unit("0001_a", [_answer("spec.md", 1, by="Leif"), _answer("spec.md", 2),
                                  _answer("spec.md", 3, by="Leif", text="x\n\nTheo ủy quyền: D1")])]
        store = precedent.entries(units, "Luôn rẻ.", [], decisions=[_decision(1)], today="2026-09-28")
        p = precedent.build_prompt(Q, store)
        at = [p.index(h) for h in ("## Precedent", "### The person's decisions", "### Answers the person delegated",
                                   "### Agents' inferences")]
        self.assertEqual(at, sorted(at))
        mine, delegated, inferred = p[at[1]:at[2]], p[at[2]:at[3]], p[at[3]:]
        for entry in ("#### pref:1\nSource: Settings: Decision preferences\n", "#### D1\nSource: chat 2026-09-01\n",
                      "#### 0001_a/spec.md#Câu 2\nDate: 2026-09-01\n"):
            self.assertIn(entry, mine)
        self.assertIn("Term: until withdrawn", mine)
        self.assertNotIn("Scope:", mine, "every-workspace entries say nothing the part does not")
        self.assertIn("#### 0001_a/spec.md#Câu 3\nDate: 2026-09-01\n", delegated)
        self.assertIn("#### 0001_a/spec.md#Câu 1\nDate: 2026-09-01\n", inferred)

    def test_an_empty_part_says_none(self):
        p = precedent.build_prompt(Q, [])
        self.assertEqual(p.count("(none)"), 3)

    def test_rules_from_settings_do_not_replace_the_fixed_paragraph_on_inferences(self):
        p = precedent.build_prompt(Q, [], rules="Decide everything yourself.")
        self.assertIn(precedent.WEIGHING, p)
        self.assertNotIn(precedent.WEIGHING, p[p.index("## Rules"):p.index("## Questions")])


class TheSession(unittest.TestCase):
    class _S:
        def __init__(self, done: dict) -> None:
            self.done, self.kw = done, {}

        async def stream(self, cwd, text, session_id=None, **kw):
            self.kw = kw
            yield ("chunk", "hi")
            await _submits(kw)
            yield ("done", self.done)

    def test_it_runs_with_no_tools_and_the_grants_ceilings(self):
        s = self._S({"session_id": "s", "cost": {"cost_usd": 0.1}})
        reply, end, failure = asyncio.run(precedent.ask(s, "/w", "p", grant_for("precedent"), "m", None))
        self.assertEqual((reply, end["session_id"], failure), ("hi", "s", ""))
        # `0136` R8: turns enough to submit, be refused and submit again.
        self.assertEqual((s.kw["tools"], s.kw["max_turns"]), ([], SUBMIT_TURNS))
        self.assertNotIn("mcp_servers", s.kw, "no channel given, none opened")
        self.assertEqual(s.kw["max_budget_usd"], grant_for("precedent").max_budget_usd)
        self.assertNotIn("effort", s.kw)

    def test_the_verdicts_are_the_object_handed_back_not_the_reply(self):
        """`0136` R8: the reply holds a JSON block saying one thing and the object another."""
        class S(self._S):
            async def stream(self, cwd, text, session_id=None, **kw):
                self.kw = kw
                yield ("chunk", "```json\n" + _json_of([_ok(1), _ok(2)]) + "\n```\n")
                await _submits(kw, verdicts=[_ok(1, verdict="needs-person", reason="r")])
                yield ("done", self.done)

        s = S({"session_id": "s"})
        channel = submit.Collector("precedent")
        reply, _, failure = asyncio.run(precedent.ask(s, "/w", "p", grant_for("precedent"), None, None,
                                                      channel=channel))
        self.assertEqual(failure, "")
        got = precedent.verdicts(channel.object(), Q, IDS)["verdicts"]
        self.assertEqual([v["verdict"] for v in got], ["needs-person", "needs-person"])
        self.assertTrue(s.kw["can_use_tool"])

    def test_a_ceiling_is_a_failure(self):
        s = self._S({"session_id": "s", "terminal_reason": "error_max_budget_usd"})
        _, _, failure = asyncio.run(precedent.ask(s, "/w", "p", grant_for("precedent"), None, None))
        self.assertIn("ceiling", failure)


if __name__ == "__main__":
    unittest.main()
