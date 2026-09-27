"""`coscc/efforttrial.py`: the arm a unit is in, and which step gets the trial's effort."""

from __future__ import annotations

import unittest

from coscc import efforttrial, models


class TheArm(unittest.TestCase):
    def test_the_same_name_is_always_in_the_same_arm(self):
        name = "0123_no-one-knows-if-each-stage-runs-at-the-right-effort"
        self.assertEqual(efforttrial.arm(name), efforttrial.arm(name))
        self.assertIn(efforttrial.arm(name), (efforttrial.TRIAL_ARM, efforttrial.CONTROL_ARM))

    def test_about_half_of_a_thousand_names_are_in_trial(self):
        # Spec R2's threshold, chosen by the spec rather than measured. The names are fixed.
        names = [f"{n:04d}_unit-{n}" for n in range(1000)]
        share = sum(1 for n in names if efforttrial.arm(n) == efforttrial.TRIAL_ARM) / len(names)
        self.assertGreaterEqual(share, 0.45)
        self.assertLessEqual(share, 0.55)


class TheEffort(unittest.TestCase):
    def test_only_a_routine_impl_of_a_trial_unit_gets_the_trial_effort(self):
        for stage in ("impl", "review", "pr", "spec", "integrate"):
            for label in ("routine", "novel", None):
                for arm in (efforttrial.TRIAL_ARM, efforttrial.CONTROL_ARM):
                    with self.subTest(stage=stage, label=label, arm=arm):
                        wanted = (
                            "high" if (stage, label, arm) == ("impl", "routine", efforttrial.TRIAL_ARM) else None
                        )
                        self.assertEqual(efforttrial.effort_for(stage, label, arm), wanted)

    def test_the_effort_is_one_the_cli_takes_and_not_max(self):
        self.assertIn(efforttrial.EFFORT, models.EFFORTS)
        self.assertNotEqual(efforttrial.EFFORT, models.OVERRIDE_ONLY)


if __name__ == "__main__":
    unittest.main()
