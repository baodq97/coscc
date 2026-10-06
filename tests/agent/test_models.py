"""`coscc/agent/models.py`: the order a row's model and effort are resolved in, and what is reported."""

from __future__ import annotations

import unittest

from coscc.agent import models, pack, policy
from tests.agent.edit import set_part

OPUS = "claude-opus-5-5[1m]"
SONNET = "claude-sonnet-5-5[1m]"


class TheOrderIsOverrideThenDefaultThenCosModel(unittest.TestCase):
    def test_the_rows_own_model_says_default(self):
        self.assertEqual(
            models.resolve("impl", None, "e"), (SONNET, "default", "medium", "default")
        )

    def test_an_owner_model_says_override_and_wins_over_cos_model(self):
        pack.write("impl", "model", {"id": "o", "effort": "medium"})
        found = models.resolve("impl", None, "e")
        self.assertEqual(found[:2], ("o", "override"))
        # The effort the owner left alone is still the built-in's.
        self.assertEqual(found[2:], ("medium", "default"))

    def test_cos_model_only_where_the_row_has_none(self):
        self.assertEqual(models.resolve("leif", None, "x"), ("x", "COS_MODEL", None, "none"))

    def test_nothing_at_all_is_none(self):
        self.assertEqual(models.resolve("leif", None, None), (None, "none", None, "none"))

    def test_a_row_the_pack_lacks_takes_its_own(self):
        self.assertEqual(
            models.resolve("nobody", None, "e", own={"id": "m", "effort": "low"}),
            ("m", "default", "low", "default"),
        )


class TheNovelVariant(unittest.TestCase):
    def test_novel_reads_the_variant_and_routine_the_base(self):
        self.assertEqual(
            models.resolve("impl", "novel", None), (OPUS, "default", "high", "default")
        )
        for label in (None, "routine"):
            self.assertEqual(models.resolve("impl", label, None)[:3], (SONNET, "default", "medium"))

    def test_a_novel_override_is_the_variants_only(self):
        set_part("impl", "variants.novel.model.id", "n")
        self.assertEqual(models.resolve("impl", "novel", None)[:2], ("n", "override"))
        self.assertEqual(models.resolve("impl", None, None)[:2], (SONNET, "default"))

    def test_a_base_override_does_not_reach_the_novel_step(self):
        set_part("impl", "model.id", "b")
        self.assertEqual(models.resolve("impl", None, None)[:2], ("b", "override"))
        self.assertEqual(models.resolve("impl", "novel", None)[:2], (OPUS, "default"))


class TheTrialTier(unittest.TestCase):
    """The owner's model, `COS_MODEL`, then the trial, then the row's own; the effort as it was."""

    def test_the_trial_model_sits_between_cos_model_and_default(self):
        plain = models.resolve("impl", "routine", None)
        tried = models.resolve("impl", "routine", None, trial_model="t")
        self.assertEqual(tried[:2], ("t", "trial"))
        # Both arms run the default's effort, so the model is the only variable.
        self.assertEqual(tried[2:], plain[2:])
        env = models.resolve("impl", "routine", "env", trial_model="t")
        self.assertEqual(env[:2], ("env", "COS_MODEL"))

    def test_an_owner_model_beats_the_trial(self):
        set_part("impl", "model.id", "o")
        self.assertEqual(
            models.resolve("impl", "routine", None, trial_model="t")[:2], ("o", "override")
        )


class TheShippedRows(unittest.TestCase):
    def test_every_shipped_model_is_the_1m_variant(self):
        # Every stage must report `contextWindow` 1000000: each shipped id ends in `[1m]`.
        found = 0
        for key, row in pack.rows().items():
            if key in ("scout", "worker"):  # helpers name a model alias, not a session's id
                continue
            ids = [(row.get("model") or {}).get("id")]
            for variant in (row.get("variants") or {}).values():
                ids.append((variant.get("model") or {}).get("id"))
            for model in filter(None, ids):
                found += 1
                self.assertTrue(model.endswith("[1m]"), (key, model))
        self.assertTrue(found)


class TheCeilingsResolveInOnePlace(unittest.TestCase):
    """The owner's, else the row's own, the `SUBMIT_TURNS` floor after either."""

    def test_default_and_override_each_say_so(self):
        self.assertEqual(
            models.ceilings("spec", None),
            {
                "max_turns": 40,
                "max_turns_source": "default",
                "max_budget_usd": 4.0,
                "max_budget_source": "default",
            },
        )
        set_part("spec", "ceilings.turns", 30)
        set_part("spec", "ceilings.usd", 1.5)
        found = models.ceilings("spec", None)
        self.assertEqual((found["max_turns"], found["max_turns_source"]), (30, "override"))
        self.assertEqual((found["max_budget_usd"], found["max_budget_source"]), (1.5, "override"))

    def test_a_row_with_no_budget_is_none(self):
        found = models.ceilings("idea", None)
        self.assertEqual((found["max_budget_usd"], found["max_budget_source"]), (None, "none"))
        # The row's one turn, raised to the floor of a stage that submits.
        self.assertEqual(
            (found["max_turns"], found["max_turns_source"]), (policy.SUBMIT_TURNS, "default")
        )

    def test_the_floor_holds_under_an_override(self):
        set_part("plan", "ceilings.turns", 1)
        found = models.ceilings("plan", None)
        self.assertEqual(
            (found["max_turns"], found["max_turns_source"]), (policy.SUBMIT_TURNS, "override")
        )

    def test_impl_novel_has_its_own_ceilings_and_an_override_of_them(self):
        found = models.ceilings("impl", "novel")
        self.assertEqual((found["max_turns"], found["max_budget_usd"]), (250, 16.0))
        pack.write("impl", "ceilings", {"turns": 120, "usd": 12.0})
        # The base row's raise is not the `novel` run's.
        self.assertEqual(models.ceilings("impl", "novel")["max_turns"], 250)
        self.assertEqual(models.ceilings("impl", None)["max_budget_usd"], 12.0)
        set_part("impl", "variants.novel.ceilings.turns", 300)
        set_part("impl", "variants.novel.ceilings.usd", 20.0)
        found = models.ceilings("impl", "novel")
        self.assertEqual((found["max_turns"], found["max_budget_usd"]), (300, 20.0))
        self.assertEqual(found["max_turns_source"], "override")

    def test_putting_none_back_restores_the_builtin(self):
        set_part("spec", "ceilings.turns", 30)
        old, new = set_part("spec", "ceilings.turns", None)
        self.assertEqual((old, new), (30, 40))
        self.assertEqual(models.ceilings("spec", None)["max_turns_source"], "default")


class TheConfigRowIsWhatThePageShows(unittest.TestCase):
    def test_it_is_what_a_run_gets_under_its_label(self):
        found = models.config_row("impl", None, "novel")
        self.assertEqual((found["model"], found["effort"]), (OPUS, "high"))
        self.assertEqual(found["ceilings"], models.ceilings("impl", "novel"))
        set_part("impl", "model.effort", "high")
        self.assertEqual(models.config_row("impl", None)["effort_source"], "override")

    def test_a_row_without_a_model_takes_cos_model(self):
        found = models.config_row("leif", "x")
        self.assertEqual((found["model"], found["model_source"]), ("x", "COS_MODEL"))


class TheBoundsOfAnOverride(unittest.TestCase):
    """Out of bounds is refused."""

    def test_each_bound(self):
        good = [
            ("model", "  m  ", "m"),
            ("model", "x" * 100, "x" * 100),
            ("effort", "max", "max"),
            ("turns", 1, 1),
            ("turns", 500, 500),
            ("turns", "30", 30),
            ("budget", 0.1, 0.1),
            ("budget", 50, 50.0),
            ("budget", "1.234", 1.23),
        ]
        for field, value, stored in good:
            self.assertEqual(models.check(field, value), (stored, ""), (field, value))
        bad = [
            ("model", ""),
            ("model", "   "),
            ("model", "x" * 101),
            ("model", 3),
            ("effort", "turbo"),
            ("turns", 0),
            ("turns", 501),
            ("turns", 2.5),
            ("turns", True),
            ("budget", 0.09),
            ("budget", 50.01),
            ("budget", float("nan")),
            ("budget", "lots"),
            ("colour", "red"),
        ]
        for field, value in bad:
            found, reason = models.check(field, value)
            self.assertIsNone(found, (field, value))
            self.assertTrue(reason, (field, value))


LOOP = "coscc-sdlc/full"
SHORT = "coscc-sdlc/short"


def _plan(variant: str = "routine", *files: str) -> dict:
    """A plan record as `UnitMeta.plan` returns it."""
    return {"variant": variant, "files": list(files or ("coscc/units/board.py",)), "steps": [],
            "rests_on": []}  # fmt: skip


class TheLabelIsThePlansRecord(unittest.TestCase):
    def test_the_security_surface_is_these_four(self):
        self.assertEqual(
            models.SECURITY_SURFACE,
            (
                "coscc/agent/policy.py",
                "coscc/agent/sessions.py",
                "coscc/loop/rules.py",
                ".claude/settings.json",
            ),
        )

    def test_before_or_at_plan_there_is_no_label(self):
        for stage in ("idea", "intent", "spec", "plan"):
            self.assertEqual(models.label_of(stage, LOOP, _plan()), (None, None, None))

    def test_a_file_of_the_security_surface_forces_novel(self):
        for path in models.SECURITY_SURFACE:
            self.assertEqual(
                models.label_of("impl", LOOP, _plan("routine", path)),
                ("routine", "novel", "forced"),
                path,
            )

    def test_no_record_runs_as_novel(self):
        self.assertEqual(models.label_of("impl", LOOP, None), ("missing", "novel", "missing"))

    def test_declared(self):
        self.assertEqual(
            models.label_of("impl", LOOP, _plan("routine")),
            ("routine", "routine", "declared"),
        )
        self.assertEqual(
            models.label_of("review", LOOP, _plan("novel")), ("novel", "novel", "declared")
        )

    def test_with_no_plan_in_its_process_a_row_with_variants_runs_novel_and_others_have_none(self):
        for stage in ("impl", "review"):
            self.assertEqual(models.label_of(stage, SHORT, None), ("missing", "novel", "missing"))
        for stage in ("intent", "pr", "ship"):
            self.assertEqual(models.label_of(stage, SHORT, None), (None, None, None), stage)
        self.assertEqual(models.label_of("spec", SHORT, _plan()), (None, None, None))

    def test_a_ceiling_never_moves_the_label(self):
        """A run that hit a ceiling pauses and a raise goes on in it: no dearer row takes over."""
        self.assertEqual(models.label_of("impl", LOOP, _plan())[2], "declared")


if __name__ == "__main__":
    unittest.main()
