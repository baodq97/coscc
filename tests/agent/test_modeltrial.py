"""`coscc/agent/modeltrial.py`: the arm a unit is in, and which step asks for its model."""

from __future__ import annotations

import hashlib
import unittest

from coscc.agent import modeltrial

OPUS_ARM, SONNET_ARM = "opus-5-5", "sonnet-5-5"
MODELS = {OPUS_ARM: "claude-opus-5-5[1m]", SONNET_ARM: "claude-sonnet-5-5[1m]"}


class TheArms(unittest.TestCase):
    def test_the_row_with_a_trial_names_two_arms_and_the_others_none(self):
        self.assertEqual(modeltrial.arms("impl"), MODELS)
        for stage in ("review", "spec", "integrate", "leif", "nobody"):
            self.assertEqual(modeltrial.arms(stage), {}, stage)

    def test_the_arm_is_the_parity_of_the_first_sha256_byte(self):
        for n in range(200):
            name = f"{n:04d}_unit-{n}"
            with self.subTest(name=name):
                even = hashlib.sha256(name.encode("utf-8")).digest()[0] % 2 == 0
                self.assertEqual(modeltrial.arm(name, "impl"), OPUS_ARM if even else SONNET_ARM)

    def test_a_stage_with_no_trial_has_no_arm(self):
        self.assertEqual(modeltrial.arm("0001_unit", "spec"), "")


class TheModel(unittest.TestCase):
    def test_only_a_routine_impl_asks_for_its_arms_model(self):
        for stage in ("impl", "review", "spec", "integrate"):
            for label in ("routine", "novel", None):
                for arm in (OPUS_ARM, SONNET_ARM):
                    with self.subTest(stage=stage, label=label, arm=arm):
                        wanted = MODELS[arm] if (stage, label) == ("impl", "routine") else None
                        self.assertEqual(modeltrial.model_for(stage, label, arm), wanted)
                        self.assertEqual(
                            modeltrial.applies(stage, label), (stage, label) == ("impl", "routine")
                        )

    def test_the_arms_name_models_the_rows_run(self):
        self.assertEqual(modeltrial.arm_name("claude-opus-5-5[1m]"), OPUS_ARM)


if __name__ == "__main__":
    unittest.main()
