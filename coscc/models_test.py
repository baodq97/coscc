"""`coscc/models.py`: the order a row's model and effort are resolved in, and what is reported."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from coscc import models

REPO = Path(__file__).resolve().parent.parent
STAGES = ["idea", "intent", "spec", "spike", "plan", "impl", "pr", "review", "ship"]


def row(model=None, effort=None):
    return {"model": model, "effort": effort}


class TheOrderIsOverrideThenDefaultThenCosModel(unittest.TestCase):
    def test_override_wins(self):
        self.assertEqual(
            models.resolve("impl", None, {"impl": "o"}, {}, {"impl": row("d")}, "e")[:2], ("o", "override")
        )

    def test_default_wins_over_cos_model(self):
        self.assertEqual(models.resolve("impl", None, {}, {}, {"impl": row("d")}, "e")[:2], ("d", "default"))

    def test_cos_model_only_where_neither_answers(self):
        defaults, _ = models.load_defaults()
        t = models.table(STAGES, {}, {}, defaults, "x", 1)
        by = {r["name"]: (r["model"], r["source"]) for r in t["rows"]}
        self.assertEqual(by["chat"], ("x", "COS_MODEL"))
        for stage in STAGES:
            self.assertEqual(by[stage][1], "default", stage)

    def test_nothing_at_all_is_none(self):
        self.assertEqual(models.resolve("chat", None, {}, {}, {}, None), (None, "none", None, "none"))


class TheFiveStepsOfR6(unittest.TestCase):
    """`0033` spec R6: novel override, novel default, base override, base default, then
    `COS_MODEL` for the model and nothing for the effort. Each component on its own."""

    DEFAULTS = {"impl": row("base-d", "medium"), "impl:novel": row("novel-d", "high")}

    def test_each_step_in_turn(self):
        d = self.DEFAULTS
        full_m = {"impl:novel": "novel-o", "impl": "base-o"}
        full_e = {"impl:novel": "max", "impl": "low"}
        r = models.resolve
        self.assertEqual(r("impl", "novel", full_m, full_e, d, "env"), ("novel-o", "override", "max", "override"))
        self.assertEqual(r("impl", "novel", {"impl": "base-o"}, {"impl": "low"}, d, "env"),
                         ("novel-d", "default", "high", "default"))
        self.assertEqual(r("impl", "novel", {"impl": "base-o"}, {"impl": "low"}, {"impl": d["impl"]}, "env"),
                         ("base-o", "override", "low", "override"))
        self.assertEqual(r("impl", "novel", {}, {}, {"impl": d["impl"]}, "env"),
                         ("base-d", "default", "medium", "default"))
        self.assertEqual(r("impl", "novel", {}, {}, {}, "env"), ("env", "COS_MODEL", None, "none"))

    def test_the_components_are_looked_up_separately(self):
        # The novel row names a model and no effort: the effort falls through to the base.
        d = {"impl": row("base-d", "medium"), "impl:novel": row("novel-d")}
        self.assertEqual(models.resolve("impl", "novel", {}, {}, d, None),
                         ("novel-d", "default", "medium", "default"))

    def test_the_variant_is_read_only_for_novel(self):
        for label in (None, "routine"):
            self.assertEqual(models.resolve("impl", label, {"impl:novel": "x"}, {}, self.DEFAULTS, None)[:3],
                             ("base-d", "default", "medium"), label)


class TheTable(unittest.TestCase):
    def test_chat_comes_after_the_stages_in_their_order(self):
        t = models.table(STAGES, {}, {}, {}, None, 1)
        expected = ["idea", "intent", "spec", "plan", "impl", "impl:novel", "pr", "pr:novel",
                    "review", "review:novel", "ship", "ship:novel", "chat"]
        self.assertEqual([r["name"] for r in t["rows"]], expected)
        self.assertTrue(all(r["agents"] == 1 for r in t["rows"]))

    def test_an_unknown_key_is_reported_not_shown(self):
        t = models.table(STAGES, {"bogus": "x"}, {}, {"old-stage": row("y")}, None, 1)
        self.assertNotIn("bogus", [r["name"] for r in t["rows"]])
        self.assertEqual(len(t["problems"]), 2)
        self.assertIn("bogus", t["problems"][0])
        self.assertIn("old-stage", t["problems"][1])

    def test_a_novel_key_before_plan_is_reported(self):
        # R9: a stage order that puts `impl` before `plan` has no `impl:novel` row.
        order = ["idea", "impl", "plan", "review"]
        t = models.table(order, {}, {"impl:novel": "high"}, {"impl:novel": row("x")}, None, 1)
        self.assertNotIn("impl:novel", [r["name"] for r in t["rows"]])
        self.assertIn("review:novel", [r["name"] for r in t["rows"]])
        self.assertEqual(len(t["problems"]), 2)
        self.assertTrue(all("impl:novel" in p for p in t["problems"]))

    def test_chat_has_no_effort(self):
        t = models.table(STAGES, {}, {"chat": "high"}, {}, None, 1)
        chat = [r for r in t["rows"] if r["name"] == "chat"][0]
        self.assertEqual((chat["effort"], chat["effort_source"]), ("", "none"))
        self.assertEqual(len(t["problems"]), 1)


class BrokenRowsAreSkippedWithAReason(unittest.TestCase):
    def test_empty_non_string_and_broken_json(self):
        rows = {
            "model:impl": json.dumps("fine"),
            "model:plan": "{",
            "model:spec": json.dumps(3),
            "model:pr": json.dumps("  "),
        }
        found, problems = models.overrides_from(rows)
        self.assertEqual(found, {"impl": "fine"})
        self.assertEqual(len(problems), 3)
        for key in ("model:plan", "model:spec", "model:pr"):
            self.assertTrue(any(p.startswith(key) for p in problems), key)

    def test_a_broken_defaults_file_is_empty_with_a_reason(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "models.json"
            p.write_text("{", encoding="utf-8")
            self.assertEqual(models.load_defaults(p)[0], {})
            self.assertEqual(len(models.load_defaults(p)[1]), 1)
            self.assertEqual(len(models.load_defaults(Path(d) / "gone.json")[1]), 1)

    def test_effort_overrides_take_only_the_five_words_and_max_is_one(self):
        # R7: `max` is allowed from an override — a person chose it.
        rows = {"effort:impl": json.dumps("max"), "effort:pr": json.dumps("turbo"), "model:impl": json.dumps("m")}
        found, problems = models.overrides_from(rows, models.EFFORT_PREFIX)
        self.assertEqual(found, {"impl": "max"})
        self.assertEqual(len(problems), 1)
        self.assertTrue(problems[0].startswith("effort:pr"))


class TheShapeOfModelsJson(unittest.TestCase):
    def _load(self, body):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "models.json"
            p.write_text(json.dumps({"models": body}), encoding="utf-8")
            return models.load_defaults(p)

    def test_the_old_shape_is_a_model_with_no_effort(self):
        self.assertEqual(self._load({"impl": "m"}), ({"impl": row("m")}, []))

    def test_max_in_the_defaults_is_dropped_and_named(self):
        # R7: only an override may be `max`.
        found, problems = self._load({"impl": {"model": "m", "effort": "max"}})
        self.assertEqual(found, {"impl": row("m")})
        self.assertEqual(len(problems), 1)
        self.assertIn("max", problems[0])

    def test_an_unknown_effort_is_dropped_and_named(self):
        found, problems = self._load({"impl": {"model": "m", "effort": "turbo"}})
        self.assertEqual(found, {"impl": row("m")})
        self.assertEqual(len(problems), 1)


class TheShippedDefaultsMatchTheScript(unittest.TestCase):
    """Spec Concerns 2. A stage added to `cos.mjs` without a default here would quietly run
    on `COS_MODEL`; a key here for a stage that is gone would change nothing."""

    def test_the_keys_are_exactly_the_stages_cos_mjs_names(self):
        out = subprocess.run(
            ["node", str(REPO / ".claude" / "scripts" / "cos.mjs"), "status", "--json"],
            cwd=REPO, capture_output=True, text=True, check=True,
        )
        names = [s["name"] for s in json.loads(out.stdout)["stages"]]
        defaults, problems = models.load_defaults()
        self.assertEqual(problems, [])
        base = {k for k in defaults if not k.endswith(models.NOVEL_SUFFIX)}
        self.assertEqual(base, set(names))
        # `0033` spec R8: the only variants shipped are these two.
        self.assertEqual(set(defaults) - base, {"impl:novel", "review:novel"})
        self.assertEqual(models.table(names, {}, {}, defaults, None, 1)["problems"], [])

    def test_the_answered_split(self):
        # `0033` spec R8, from `idea.md:23`: a starting point, not a conclusion. The ids are
        # the `[1m]` ones `0031` shipped.
        defaults, _ = models.load_defaults()
        opus, sonnet = "claude-opus-5-5[1m]", "claude-sonnet-5[1m]"
        expected = {
            "idea": row(opus, "medium"), "intent": row(opus, "medium"),
            "spec": row(opus, "high"), "plan": row(opus, "high"),
            "impl": row(sonnet, "medium"), "impl:novel": row(opus, "high"),
            "review": row(opus, "high"), "review:novel": row(opus, "xhigh"),
            "pr": row(sonnet, "low"), "ship": row(sonnet, "low"),
        }
        self.assertEqual(defaults, expected)
        self.assertNotIn("chat", defaults)

    def test_every_default_is_the_1m_variant(self):
        # `0031 intent.md`: mọi stage phải báo `contextWindow` 1000000, không riêng gì
        # bản nào — mọi id mặc định kết thúc bằng `[1m]`.
        defaults, problems = models.load_defaults()
        self.assertEqual(problems, [])
        self.assertTrue(defaults, "load_defaults() trả về rỗng")
        for stage, entry in defaults.items():
            self.assertTrue(entry["model"].endswith("[1m]"), (stage, entry))


if __name__ == "__main__":
    unittest.main()
