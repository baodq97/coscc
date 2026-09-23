"""`coscc/models.py`: the order a row's model is resolved in, and what is reported."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from coscc import models

REPO = Path(__file__).resolve().parent.parent
STAGES = ["idea", "intent", "spec", "plan", "impl", "pr", "review", "ship"]


class TheOrderIsOverrideThenDefaultThenCosModel(unittest.TestCase):
    def test_override_wins(self):
        self.assertEqual(
            models.resolve("impl", {"impl": "o"}, {"impl": "d"}, "e"), ("o", "override")
        )

    def test_default_wins_over_cos_model(self):
        self.assertEqual(models.resolve("impl", {}, {"impl": "d"}, "e"), ("d", "default"))

    def test_cos_model_only_where_neither_answers(self):
        defaults, _ = models.load_defaults()
        t = models.table(STAGES, {}, defaults, "x", 1)
        by = {r["name"]: (r["model"], r["source"]) for r in t["rows"]}
        self.assertEqual(by["chat"], ("x", "COS_MODEL"))
        for stage in STAGES:
            self.assertEqual(by[stage][1], "default", stage)

    def test_nothing_at_all_is_none(self):
        self.assertEqual(models.resolve("chat", {}, {}, None), (None, "none"))


class TheTable(unittest.TestCase):
    def test_chat_comes_after_the_stages_in_their_order(self):
        t = models.table(STAGES, {}, {}, None, 1)
        self.assertEqual([r["name"] for r in t["rows"]], STAGES + ["chat"])
        self.assertTrue(all(r["agents"] == 1 for r in t["rows"]))

    def test_an_unknown_key_is_reported_not_shown(self):
        t = models.table(STAGES, {"bogus": "x"}, {"old-stage": "y"}, None, 1)
        self.assertNotIn("bogus", [r["name"] for r in t["rows"]])
        self.assertEqual(len(t["problems"]), 2)
        self.assertIn("bogus", t["problems"][0])
        self.assertIn("old-stage", t["problems"][1])


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
        self.assertEqual(set(defaults), set(names))

    def test_the_answered_split(self):
        # `intent.md ## Answers, câu 2` (0004), giữ nguyên qua `0031` — chỉ đổi id sang
        # bản `[1m]` (`0031 intent.md ## Answers, câu 2`).
        defaults, _ = models.load_defaults()
        for stage in ("idea", "intent", "spec", "plan", "review"):
            self.assertEqual(defaults[stage], "claude-opus-5-5[1m]", stage)
        for stage in ("impl", "pr", "ship"):
            self.assertEqual(defaults[stage], "claude-sonnet-5[1m]", stage)
        self.assertNotIn("chat", defaults)

    def test_every_default_is_the_1m_variant(self):
        # `0031 intent.md`: mọi stage phải báo `contextWindow` 1000000, không riêng gì
        # bản nào — mọi id mặc định kết thúc bằng `[1m]`.
        defaults, problems = models.load_defaults()
        self.assertEqual(problems, [])
        self.assertTrue(defaults, "load_defaults() trả về rỗng")
        for stage, model in defaults.items():
            self.assertTrue(model.endswith("[1m]"), (stage, model))


if __name__ == "__main__":
    unittest.main()
