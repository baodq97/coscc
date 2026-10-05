from __future__ import annotations

import unittest

from coscc.features.codegraph import Row, Step, report

DAY = "2026-05-10T12:00:00"


def row(
    run: str, unit: str, arm: str, *, stage: str = "impl", at: str = DAY, error: str = ""
) -> Row:
    return Row(run, "ws", unit, stage, arm, "abc", 1000, 5, error, at)  # type: ignore[arg-type]


def step(run: str, unit: str, *, read: int | None = 1000, cost: float = 1.0) -> Step:
    return Step(run, unit, cost, read, None if read is None else 100)


class Trial:
    """Units of one arm, each with one impl run of two steps."""

    def __init__(self) -> None:
        self.rows: list[Row] = []
        self.steps: list[Step] = []
        self.rounds: dict[str, int] = {}

    def arm(self, arm: str, units: int, *, read: int, cost: float = 1.0, rounds: int = 1) -> Trial:
        for n in range(units):
            unit = f"{arm}{n}"
            self.rows.append(row(f"run-{unit}", unit, arm))
            self.steps += [step(f"run-{unit}", unit, read=read, cost=cost) for _ in range(2)]
            self.rounds[unit] = rounds
        return self

    def report(self, window: tuple[str | None, str | None] = (None, None)):
        return report(self.rows, self.steps, self.rounds, window)


class A_clear_win(unittest.TestCase):
    def test_passes_when_read_drops_by_forty_percent_and_nothing_rises(self) -> None:
        out = Trial().arm("on", 5, read=600).arm("off", 6, read=1000).report()
        self.assertEqual(out["verdict"], "pass")
        self.assertEqual(out["missed"], [])
        self.assertEqual(out["arms"]["on"]["read_mean"], 600)
        self.assertEqual(out["arms"]["off"]["read_mean"], 1000)
        self.assertEqual(out["arms"]["on"]["units"], 5)
        self.assertEqual(out["arms"]["on"]["steps"], 10)
        self.assertEqual(out["arms"]["on"]["served_mean"], 100)

    def test_a_drop_of_exactly_thirty_percent_passes(self) -> None:
        out = Trial().arm("on", 5, read=700).arm("off", 5, read=1000).report()
        self.assertEqual(out["verdict"], "pass")

    def test_reports_the_window_it_was_asked_for(self) -> None:
        out = Trial().arm("on", 5, read=600).arm("off", 5, read=1000).report(("2026-01-01", None))
        self.assertEqual(out["window"], {"since": "2026-01-01", "until": None})


class A_target_not_met(unittest.TestCase):
    def test_fails_naming_the_arm_with_four_units(self) -> None:
        out = Trial().arm("on", 5, read=600).arm("off", 4, read=1000).report()
        self.assertEqual(out["verdict"], "fail")
        self.assertEqual(out["missed"], ["the off arm has 4 units, under 5"])

    def test_fails_when_read_drops_only_twenty_percent(self) -> None:
        out = Trial().arm("on", 5, read=800).arm("off", 5, read=1000).report()
        self.assertEqual(out["verdict"], "fail")
        self.assertEqual(len(out["missed"]), 1)
        self.assertIn("read per impl step", out["missed"][0])

    def test_fails_when_cost_per_step_rises(self) -> None:
        out = Trial().arm("on", 5, read=600, cost=1.5).arm("off", 5, read=1000).report()
        self.assertEqual(out["verdict"], "fail")
        self.assertEqual(len(out["missed"]), 1)
        self.assertIn("cost", out["missed"][0])

    def test_fails_when_review_rounds_rise(self) -> None:
        out = Trial().arm("on", 5, read=600, rounds=2).arm("off", 5, read=1000).report()
        self.assertEqual(out["verdict"], "fail")
        self.assertEqual(len(out["missed"]), 1)
        self.assertIn("review rounds", out["missed"][0])

    def test_names_every_condition_that_is_unmet(self) -> None:
        out = Trial().arm("on", 3, read=900, cost=2.0, rounds=3).arm("off", 5, read=1000).report()
        self.assertEqual(len(out["missed"]), 4)

    def test_no_shipped_unit_leaves_the_rounds_comparison_unmet(self) -> None:
        trial = Trial().arm("on", 5, read=600).arm("off", 5, read=1000)
        trial.rounds.clear()
        out = trial.report()
        self.assertEqual(out["verdict"], "fail")
        self.assertIsNone(out["arms"]["on"]["changes_requested_mean"])
        self.assertIn("review rounds", out["missed"][0])

    def test_means_cost_to_four_decimals(self) -> None:
        trial = Trial().arm("on", 5, read=600, cost=1.0 / 3).arm("off", 5, read=1000)
        self.assertEqual(trial.report()["arms"]["on"]["cost_mean"], 0.3333)


class A_step_that_cannot_be_counted(unittest.TestCase):
    def test_an_on_step_with_an_error_is_excluded_and_counted_as_such(self) -> None:
        trial = Trial().arm("on", 5, read=600).arm("off", 5, read=1000)
        trial.rows[0] = row("run-on0", "on0", "on", error="map failed")
        out = trial.report()
        self.assertEqual(out["arms"]["on"]["excluded"], 2)
        self.assertEqual(out["arms"]["on"]["steps"], 8)
        self.assertEqual(out["arms"]["on"]["units"], 4)
        self.assertEqual(out["arms"]["off"]["excluded"], 0)
        self.assertEqual(out["verdict"], "fail")

    def test_a_purged_step_is_excluded_and_counted_as_such(self) -> None:
        trial = Trial().arm("on", 5, read=600).arm("off", 5, read=1000)
        trial.steps.append(step("run-off0", "off0", read=None))
        out = trial.report()
        self.assertEqual(out["arms"]["off"]["excluded"], 1)
        self.assertEqual(out["arms"]["off"]["steps"], 10)
        self.assertEqual(out["arms"]["off"]["read_mean"], 1000)

    def test_a_step_with_no_row_is_ignored(self) -> None:
        trial = Trial().arm("on", 5, read=600).arm("off", 5, read=1000)
        trial.steps.append(step("run-stray", "stray", read=99999))
        out = trial.report()
        self.assertEqual(out["arms"]["on"]["excluded"] + out["arms"]["off"]["excluded"], 0)
        self.assertEqual(out["arms"]["on"]["steps"] + out["arms"]["off"]["steps"], 20)


class A_unit_that_ran_in_both_arms(unittest.TestCase):
    def test_leaves_both_arms_and_is_counted(self) -> None:
        trial = Trial().arm("on", 5, read=600).arm("off", 5, read=1000)
        trial.rows.append(row("run-a", "mix", "on"))
        trial.rows.append(row("run-b", "mix", "off"))
        trial.steps += [step("run-a", "mix", read=1), step("run-b", "mix", read=1)]
        out = trial.report()
        self.assertEqual(out["excluded_units"], 1)
        self.assertEqual(out["arms"]["on"]["units"], 5)
        self.assertEqual(out["arms"]["off"]["units"], 5)
        self.assertEqual(out["arms"]["on"]["excluded"], 1)
        self.assertEqual(out["arms"]["off"]["excluded"], 1)
        self.assertEqual(out["arms"]["on"]["read_mean"], 600)
        self.assertEqual(out["verdict"], "pass")

    def test_a_review_row_in_the_other_arm_is_enough_to_exclude_the_unit(self) -> None:
        trial = Trial().arm("on", 5, read=600).arm("off", 5, read=1000)
        trial.rows.append(row("review-1", "on0", "off", stage="review"))
        out = trial.report()
        self.assertEqual(out["excluded_units"], 1)
        self.assertEqual(out["arms"]["on"]["units"], 4)
        self.assertEqual(out["arms"]["on"]["excluded"], 2)

    def test_a_review_row_of_the_same_arm_changes_nothing(self) -> None:
        trial = Trial().arm("on", 5, read=600).arm("off", 5, read=1000)
        trial.rows.append(row("review-1", "on0", "on", stage="review"))
        trial.steps.append(step("review-1", "on0", read=5000))
        out = trial.report()
        self.assertEqual(out["excluded_units"], 0)
        self.assertEqual(out["arms"]["on"]["steps"], 10)
        self.assertEqual(out["arms"]["on"]["read_mean"], 600)

    def test_a_both_arm_unit_split_across_the_window_is_not_excluded(self) -> None:
        trial = Trial().arm("on", 5, read=600).arm("off", 5, read=1000)
        trial.rows.append(row("old", "on0", "off", at="2026-01-01T00:00:00"))
        out = trial.report(("2026-05-01", None))
        self.assertEqual(out["excluded_units"], 0)
        self.assertEqual(out["arms"]["on"]["units"], 5)


class A_window(unittest.TestCase):
    def trial(self) -> Trial:
        trial = Trial().arm("on", 5, read=600).arm("off", 5, read=1000)
        trial.rows = [
            Row(
                r.run,
                r.workspace,
                r.unit,
                r.stage,
                r.arm,
                r.sha,
                r.map_chars,
                r.wait_ms,
                r.error,
                "2026-05-10",
            )
            for r in trial.rows
        ]
        return trial

    def test_since_is_inclusive(self) -> None:
        self.assertEqual(self.trial().report(("2026-05-10", None))["arms"]["on"]["units"], 5)

    def test_until_is_exclusive(self) -> None:
        self.assertEqual(self.trial().report((None, "2026-05-10"))["arms"]["on"]["units"], 0)
        self.assertEqual(self.trial().report((None, "2026-05-11"))["arms"]["on"]["units"], 5)

    def test_rows_outside_the_window_are_ignored(self) -> None:
        trial = self.trial()
        trial.rows.append(row("run-late", "late", "on", at="2026-06-01"))
        trial.steps.append(step("run-late", "late", read=1))
        out = trial.report(("2026-05-01", "2026-06-01"))
        self.assertEqual(out["arms"]["on"]["units"], 5)
        self.assertEqual(out["arms"]["on"]["excluded"], 0)

    def test_a_window_with_only_on_rows_fails_naming_the_off_arm(self) -> None:
        trial = Trial().arm("on", 6, read=600)
        out = trial.report()
        self.assertEqual(out["verdict"], "fail")
        self.assertEqual(out["arms"]["on"]["units"], 6)
        self.assertEqual(out["arms"]["off"]["units"], 0)
        self.assertIsNone(out["arms"]["off"]["read_mean"])
        self.assertIn("the off arm has 0 units, under 5", out["missed"])


if __name__ == "__main__":
    unittest.main()
