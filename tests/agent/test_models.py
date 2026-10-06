"""`coscc/agent/models.py`: the order a row's model and effort are resolved in, and what is reported."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from coscc.agent import models
from coscc.github import prmachine

REPO = Path(__file__).resolve().parents[2]
STAGES = ["idea", "intent", "spec", "spike", "plan", "impl", "review"]
# `agents.json`'s keys: the stages that run a session, then Gebo.
AGENTS = STAGES + ["integrate"]


def row(model=None, effort=None):
    return {"model": model, "effort": effort}


def table(keys=AGENTS, model=None, effort=None, defaults=None, env=None, turns=None, budget=None):
    over = {
        "model": model or {},
        "effort": effort or {},
        "turns": turns or {},
        "budget": budget or {},
    }
    return models.agent_config(keys, over, defaults or {}, env)


class TheOrderIsOverrideThenDefaultThenCosModel(unittest.TestCase):
    def test_override_wins(self):
        self.assertEqual(
            models.resolve("impl", None, {"impl": "o"}, {}, {"impl": row("d")}, "e")[:2],
            ("o", "override"),
        )

    def test_default_wins_over_cos_model(self):
        self.assertEqual(
            models.resolve("impl", None, {}, {}, {"impl": row("d")}, "e")[:2], ("d", "default")
        )

    def test_cos_model_only_where_neither_answers(self):
        defaults, _ = models.load_defaults()
        t = table(defaults=defaults, env="x")
        by = {r["key"]: (r["model"], r["model_source"]) for r in t["rows"]}
        self.assertEqual(by["chat"], ("x", "COS_MODEL"))
        for stage in STAGES:
            self.assertEqual(by[stage][1], "default", stage)

    def test_nothing_at_all_is_none(self):
        self.assertEqual(
            models.resolve("chat", None, {}, {}, {}, None), (None, "none", None, "none")
        )


class TheFiveStepsOfTheModelChoice(unittest.TestCase):
    """Novel override, novel default, base override, base default, then `COS_MODEL` for the model
    and nothing for the effort. Each component on its own."""

    DEFAULTS = {"impl": row("base-d", "medium"), "impl:novel": row("novel-d", "high")}

    def test_each_step_in_turn(self):
        d = self.DEFAULTS
        full_m = {"impl:novel": "novel-o", "impl": "base-o"}
        full_e = {"impl:novel": "max", "impl": "low"}
        r = models.resolve
        self.assertEqual(
            r("impl", "novel", full_m, full_e, d, "env"), ("novel-o", "override", "max", "override")
        )
        self.assertEqual(
            r("impl", "novel", {"impl": "base-o"}, {"impl": "low"}, d, "env"),
            ("novel-d", "default", "high", "default"),
        )
        self.assertEqual(
            r("impl", "novel", {"impl": "base-o"}, {"impl": "low"}, {"impl": d["impl"]}, "env"),
            ("base-o", "override", "low", "override"),
        )
        self.assertEqual(
            r("impl", "novel", {}, {}, {"impl": d["impl"]}, "env"),
            ("base-d", "default", "medium", "default"),
        )
        self.assertEqual(r("impl", "novel", {}, {}, {}, "env"), ("env", "COS_MODEL", None, "none"))

    def test_the_variant_is_read_only_for_novel(self):
        for label in (None, "routine"):
            self.assertEqual(
                models.resolve("impl", label, {"impl:novel": "x"}, {}, self.DEFAULTS, None)[:3],
                ("base-d", "default", "medium"),
                label,
            )


class TheTrialTier(unittest.TestCase):
    """Override, `COS_MODEL`, then the trial, then `models.json`; the effort as it was."""

    DEFAULTS = {"impl": row("base-d", "medium"), "impl:novel": row("novel-d", "high")}

    def test_without_a_trial_model_resolve_is_what_it_was(self):
        for label in (None, "routine", "novel"):
            for overrides in ({}, {"impl": "m"}, {"impl:novel": "n"}):
                for defaults in ({}, self.DEFAULTS):
                    with self.subTest(label=label, overrides=overrides, defaults=defaults):
                        args = ("impl", label, overrides, {"impl": "low"}, defaults, "env")
                        self.assertEqual(
                            models.resolve(*args), models.resolve(*args, trial_model=None)
                        )

    def test_the_trial_model_sits_between_cos_model_and_default(self):
        plain = models.resolve("impl", "routine", {}, {}, self.DEFAULTS, None)
        tried = models.resolve("impl", "routine", {}, {}, self.DEFAULTS, None, trial_model="t")
        self.assertEqual(tried[:2], ("t", "trial"))
        # Both arms run the default's effort, so the model is the only variable.
        self.assertEqual(tried[2:], plain[2:])
        overridden = models.resolve(
            "impl", "routine", {"impl": "o"}, {}, self.DEFAULTS, None, trial_model="t"
        )
        self.assertEqual(overridden[:2], ("o", "override"))
        env = models.resolve("impl", "routine", {}, {}, self.DEFAULTS, "env", trial_model="t")
        self.assertEqual(env[:2], ("env", "COS_MODEL"))


class TheShippedDefaultsMatchTheLoop(unittest.TestCase):
    """Spec Concerns 2. A stage added to the loop without a default here would quietly run
    on `COS_MODEL`; a key here for a stage that is gone would change nothing."""

    def test_the_keys_are_exactly_the_stages_the_loop_names(self):
        out = subprocess.run(
            [sys.executable, "-m", "coscc.loop", "--state", "-", "status", "--json"],
            # The stage table needs no unit, so an empty snapshot.
            input='{"workspace": "", "units": {}}',
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        )
        names = [s["name"] for s in json.loads(out.stdout)["stages"]]
        defaults, problems = models.load_defaults()
        self.assertEqual(problems, [])
        base = {k for k in defaults if not k.endswith(models.NOVEL_SUFFIX)}
        # `estimate` is a row of its own, not a stage the loop names. `pr` and `ship` run no
        # session and have none.
        self.assertEqual(base, (set(names) - set(prmachine.STAGES)) | {models.ESTIMATE})
        # The only variants shipped are these two.
        self.assertEqual(set(defaults) - base, {"impl:novel", "review:novel"})
        self.assertEqual(table(names, defaults=defaults)["problems"], [])

    def test_every_default_is_the_1m_variant(self):
        # Mọi stage phải báo `contextWindow` 1000000, không riêng gì bản nào — mọi id mặc định kết
        # thúc bằng `[1m]`.
        defaults, problems = models.load_defaults()
        self.assertEqual(problems, [])
        self.assertTrue(defaults, "load_defaults() trả về rỗng")
        for stage, entry in defaults.items():
            self.assertTrue(entry["model"].endswith("[1m]"), (stage, entry))


class TheCeilingsResolveInOnePlace(unittest.TestCase):
    """Override, else the grant's own, the `SUBMIT_TURNS` floor after either."""

    def test_default_and_override_each_say_so(self):
        self.assertEqual(
            models.ceilings("spec", None, {}, {}),
            {
                "max_turns": 40,
                "max_turns_source": "default",
                "max_budget_usd": 4.0,
                "max_budget_source": "default",
            },
        )
        found = models.ceilings("spec", None, {"spec": 30}, {"spec": 1.5})
        self.assertEqual((found["max_turns"], found["max_turns_source"]), (30, "override"))
        self.assertEqual((found["max_budget_usd"], found["max_budget_source"]), (1.5, "override"))

    def test_a_grant_with_no_budget_is_none(self):
        found = models.ceilings("idea", None, {}, {})
        self.assertEqual((found["max_budget_usd"], found["max_budget_source"]), (None, "none"))
        # `Grant()`'s one turn, raised to the floor of a stage that submits.
        self.assertEqual((found["max_turns"], found["max_turns_source"]), (4, "default"))

    def test_the_floor_holds_under_an_override(self):
        found = models.ceilings("plan", None, {"plan": 1}, {})
        self.assertEqual((found["max_turns"], found["max_turns_source"]), (4, "override"))

    def test_impl_novel_replaces_novel_ceilings_only_when_overridden(self):
        self.assertEqual(models.ceilings("impl", "novel", {}, {})["max_turns"], 250)
        self.assertEqual(models.ceilings("impl", "novel", {}, {})["max_budget_usd"], 16.0)
        found = models.ceilings("impl", "novel", {"impl:novel": 300}, {"impl:novel": 20.0})
        self.assertEqual((found["max_turns"], found["max_budget_usd"]), (300, 20.0))
        # The base row's override is not the `novel` run's.
        self.assertEqual(models.ceilings("impl", "novel", {"impl": 10}, {})["max_turns"], 250)
        self.assertEqual(models.ceilings("impl", None, {"impl": 10}, {})["max_turns"], 10)


class TheBoundsOfAnOverride(unittest.TestCase):
    """Out of bounds is refused, and a stored one is skipped and named."""

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

    def test_a_stored_value_out_of_bounds_is_a_problem(self):
        rows = {
            "turns:spec": json.dumps(30),
            "turns:plan": json.dumps(900),
            "turns:impl": "{",
            "turns:review": json.dumps("30"),
        }
        found, problems = models.overrides_from(rows, models.TURNS_PREFIX)
        self.assertEqual(found, {"spec": 30})
        self.assertEqual(len(problems), 3)
        found, problems = models.overrides_from(
            {"budget:spec": json.dumps(2.5), "budget:plan": json.dumps(99)}, models.BUDGET_PREFIX
        )
        self.assertEqual((found, len(problems)), ({"spec": 2.5}, 1))

    def test_a_key_no_row_takes_is_a_problem(self):
        t = table(turns={"review:novel": 10, "chat": 10, "deploy": 10}, budget={"estimate": 1.0})
        self.assertEqual(len(t["problems"]), 4, t["problems"])


LOOP = ["idea", "intent", "spec", "plan", "impl", "pr", "review", "ship"]


def _plan(impl: str = "routine", *files: str) -> dict:
    """A plan record as `UnitMeta.plan` returns it."""
    return {"impl": impl, "files": list(files or ("coscc/units/board.py",)), "steps": [],
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

    def test_a_ceiling_never_moves_the_label(self):
        """A run that hit a ceiling pauses and a raise goes on in it: no dearer row takes over."""
        self.assertEqual(models.label_of("impl", LOOP, _plan())[2], "declared")


if __name__ == "__main__":
    unittest.main()
