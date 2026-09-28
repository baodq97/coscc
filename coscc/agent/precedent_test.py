"""`0044`. Jera's pure half: the store, the questions, the prompt and the filter."""

from __future__ import annotations

import asyncio
import json
import unittest

from coscc.agent import precedent
from coscc.agent.policy import grant_for


def _unit(name: str, answers=(), questions=(), next_: str = "write-plan") -> dict:
    return {"name": name, "answers": list(answers), "questions": list(questions), "next": next_}


def _answer(artifact: str, n: int, by: str = "owner", via: str = "product", text: str = "yes") -> dict:
    return {"artifact": artifact, "n": n, "question": f"q{n}?", "by": by, "date": "2026-09-01", "via": via,
            "text": text}


Q = [{"unit": "0002_b", "artifact": "spec.md", "n": 1, "text": "a?"},
     {"unit": "0002_b", "artifact": "spec.md", "n": 2, "text": "b?"}]
IDS = {"pref:1", "0001_a/spec.md#Câu 1"}


def _reply(*items: dict) -> str:
    return "thinking\n```json\n" + json.dumps(list(items), ensure_ascii=False) + "\n```\n"


def _ok(n: int = 1, **over) -> dict:
    return {"artifact": "spec.md", "n": n, "verdict": "answer", "category": "other", "text": "Có.",
            "reason": "", "cites": ["0001_a/spec.md#Câu 1"], **over}


class TheStore(unittest.TestCase):
    def test_each_paragraph_of_the_preferences_is_one_entry(self):
        got = precedent.entries([], "Một.\ncòn một.\n\n  \n\nHai.\n\n", [])
        self.assertEqual(got, [{"id": "pref:1", "text": "Một.\ncòn một."}, {"id": "pref:2", "text": "Hai."}])

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
        q = [{"artifact": "spec.md", "n": 1, "text": "a", "answered": False}]
        for nxt in ("finished", "closed — rejected"):
            self.assertEqual(precedent.asked(_unit("0002_b", questions=q, next_=nxt)), [], nxt)


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

    def test_a_reply_with_no_json_fails_whole(self):
        got = precedent.verdicts("no json here", Q, IDS)
        self.assertTrue(got["failed"])
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


class TheSession(unittest.TestCase):
    class _S:
        def __init__(self, done: dict) -> None:
            self.done, self.kw = done, {}

        async def stream(self, cwd, text, session_id=None, **kw):
            self.kw = kw
            yield ("chunk", "hi")
            yield ("done", self.done)

    def test_it_runs_with_no_tools_and_the_grants_ceilings(self):
        s = self._S({"session_id": "s", "cost": {"cost_usd": 0.1}})
        reply, end, failure = asyncio.run(precedent.ask(s, "/w", "p", grant_for("precedent"), "m", None))
        self.assertEqual((reply, end["session_id"], failure), ("hi", "s", ""))
        self.assertEqual((s.kw["tools"], s.kw["max_turns"]), ([], 1))
        self.assertEqual(s.kw["max_budget_usd"], grant_for("precedent").max_budget_usd)
        self.assertNotIn("effort", s.kw)

    def test_a_ceiling_is_a_failure(self):
        s = self._S({"session_id": "s", "terminal_reason": "error_max_budget_usd"})
        _, _, failure = asyncio.run(precedent.ask(s, "/w", "p", grant_for("precedent"), None, None))
        self.assertIn("ceiling", failure)


if __name__ == "__main__":
    unittest.main()
