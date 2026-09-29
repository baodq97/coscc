"""`coscc/knowledge/efforttrial.py`: the arm a unit was in, which the old rows are read by."""

from __future__ import annotations

import unittest

from coscc.knowledge import efforttrial


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


if __name__ == "__main__":
    unittest.main()
